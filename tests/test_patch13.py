"""Patch 13 — durable suspensions, hard lockout, $20 reinstatement fine.

Offline: Supabase / Paystack / ATLOS are mocked. Verifies that
  * blocks are written durably first (fail-closed) and activity touches never clobber them,
  * the durable row is authoritative for is_blocked (survives a wiped local store),
  * a suspended account gets 403 {suspended} on chat AND ordinary routes, but can
    still read its status and start a fine payment,
  * a paid fine (Paystack webhook/verify or crypto) lifts the block, never grants a plan,
    is idempotent, and a normal plan purchase does NOT lift a block.
"""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('oracool_test_server_p13', ROOT / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


def _post(port, path, body):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


class FakeCloud:
    """In-memory stand-in for the Supabase user_flags row + kv store."""
    def __init__(self):
        self.flags = {}
        self.kv = {}
        self.fail_writes = False

    def get_flag(self, email):
        return dict(self.flags.get((email or '').lower())) if (email or '').lower() in self.flags else None

    def upsert_flag(self, rec, block_fields=True):
        if self.fail_writes:
            return False
        email = rec['email'].lower()
        row = self.flags.setdefault(email, {'email': email, 'blocked': False})
        row['last_seen'] = rec.get('last_seen', '')
        if block_fields:
            for f in ('blocked', 'block_reason', 'blocked_by', 'blocked_at'):
                row[f] = rec.get(f)
        return True


class Patch13Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cloud = FakeCloud()
        self.patches = [patch.object(s, 'DATA_DIR', self.tmp.name), patch.object(s, 'BASE_DIR', self.tmp.name),
                        patch.dict(s.KEYS, {}, clear=True), patch.object(s, '_AUTH_CACHE', {}),
                        patch.dict(s._BLOCK_CACHE, {}, clear=True), patch.object(s, 'audit_log'),
                        patch.object(s, 'emit_event'), patch.object(s, 'notify_admins'),
                        patch.object(s, 'supabase_get_flag', side_effect=self.cloud.get_flag),
                        patch.object(s, 'supabase_upsert_flag', side_effect=self.cloud.upsert_flag),
                        patch.object(s, 'supabase_kv_get', side_effect=lambda k: self.cloud.kv.get(k)),
                        patch.object(s, 'supabase_kv_put', side_effect=lambda k, v: self.cloud.kv.__setitem__(k, json.loads(json.dumps(v))) or True)]
        for p in self.patches:
            p.start()
        s.KEYS.update(SUPABASE_URL='https://example.invalid', SUPABASE_SERVICE_KEY='svc', ADMIN_EMAILS=['owner@example.test'])
        self.user = 'blocked@example.test'

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def serve(self):
        server = s.ThreadingHTTPServer(('127.0.0.1', 0), s.Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
        return server, t

    # ---- durability -------------------------------------------------------
    def test_block_is_written_durably_and_survives_local_wipe(self):
        r = s.block_user(self.user, True, 'Misuse of the AI', 'owner@example.test')
        self.assertTrue(r['ok']); self.assertTrue(self.cloud.flags[self.user]['blocked'])
        # simulate a redeploy: local users.json gone, cache cold
        s.save_users({}); s._BLOCK_CACHE.clear()
        self.assertTrue(s.is_blocked(self.user))
        self.assertEqual(s.check_tier(self.user), 'free')

    def test_activity_touch_never_clears_a_block(self):
        s.block_user(self.user, True, 'spam', 'owner@example.test')
        s.save_users({}); s._BLOCK_CACHE.clear()
        with patch.object(s, 'supabase_get_flag', return_value=None):   # lookup fails during the touch
            s.touch_user(self.user, last_ip='1.2.3.4')
        self.assertTrue(self.cloud.flags[self.user]['blocked'])            # cloud row untouched
        s._BLOCK_CACHE.clear()
        self.assertTrue(s.is_blocked(self.user))                          # and the truth wins again

    def test_block_fails_closed_when_durable_write_fails(self):
        self.cloud.fail_writes = True
        r = s.block_user(self.user, True, 'x', 'owner@example.test')
        self.assertIn('error', r)
        self.assertFalse(s.is_blocked(self.user))
        self.assertNotIn(self.user, self.cloud.flags)

    def test_admins_cannot_be_blocked(self):
        self.assertIn('error', s.block_user('owner@example.test', True, 'oops', 'owner@example.test'))
        self.assertFalse(s.is_blocked('owner@example.test'))

    def test_cloud_row_is_authoritative_over_stale_local_record(self):
        users = s.load_users(); users[self.user] = {'email': self.user, 'blocked': False}; s.save_users(users)
        self.cloud.flags[self.user] = {'email': self.user, 'blocked': True, 'block_reason': 'abuse'}
        self.assertTrue(s.is_blocked(self.user))
        self.assertTrue(s.load_users()[self.user]['blocked'])              # local mirror repaired
        # cached for a minute: a flipped cloud row is not re-read immediately, block_user updates the cache directly
        self.cloud.flags[self.user]['blocked'] = False
        self.assertTrue(s.is_blocked(self.user))
        s.block_user(self.user, False, '', 'owner@example.test')
        self.assertFalse(s.is_blocked(self.user))

    # ---- lockout ----------------------------------------------------------
    def test_suspended_account_is_locked_out_everywhere_but_can_see_status_and_pay(self):
        s.block_user(self.user, True, 'Misuse of the AI', 'owner@example.test')
        server, t = self.serve()
        try:
            port = server.server_port
            with patch.object(s, 'request_identity', return_value=self.user):
                for path in ('/api/chat', '/api/osint/email', '/api/chat/sessions', '/api/media/image', '/api/pay/crypto/invoice', '/api/paystack/initialize'):
                    code, body = _post(port, path, {'messages': [{'role': 'user', 'content': 'hi'}], 'email': self.user, 'plan': 'pro'})
                    if path == '/api/paystack/initialize':
                        continue   # payment confirmation routes stay reachable; plan purchase itself does not unblock (tested below)
                    self.assertEqual(code, 403, path); self.assertTrue(body.get('suspended'), path); self.assertIsNone(body.get('fine_usd'))
                code, st = _post(port, '/api/block/status', {})
                self.assertEqual(code, 200); self.assertTrue(st['blocked']); self.assertEqual(st['reason'], 'Misuse of the AI')
                self.assertTrue(st['fine'].get('cancelled'))   # Patch 16: fines are no longer collected
                code, body = _post(port, '/api/block/fine/paystack', {'callback_url': 'https://app.example/app'})
                self.assertEqual(code, 410); self.assertIn('no longer collected', body['error'])
            # a modified client that strips its token but still names the email is refused too
            with patch.object(s, 'request_identity', return_value=''):
                code, body = _post(port, '/api/osint/email', {'email': self.user})
                self.assertEqual(code, 403); self.assertTrue(body['suspended'])
            # a non-suspended account owes no fine either — the route is retired
            with patch.object(s, 'request_identity', return_value='fine@example.test'):
                code, body = _post(port, '/api/block/fine/paystack', {})
                self.assertEqual(code, 410)
                code, st = _post(port, '/api/block/status', {})
                self.assertFalse(st['blocked'])
        finally:
            server.shutdown(); server.server_close(); t.join()

    # ---- the fine ---------------------------------------------------------
    def test_paystack_fine_lifts_block_without_granting_a_plan(self):
        s.block_user(self.user, True, 'x', 'owner@example.test')
        data = {'reference': 'FINE-abc', 'amount': 3100000, 'currency': 'NGN', 'channel': 'card', 'status': 'success',
                'paid_at': '2026-09-20T15:00:00Z', 'customer': {'email': 'receipt@example.test'},
                'metadata': {'product': 'OraCool AI', 'plan': 'fine', 'purpose': 'fine', 'account': self.user}}
        with patch.object(s, 'save_subscriber') as save_sub, patch.object(s, 'supabase_store_subscriber') as store_sub:
            rec = s.record_paystack_success(data)
        self.assertTrue(rec['fine']); self.assertTrue(rec['unblocked'])
        save_sub.assert_not_called(); store_sub.assert_not_called()          # never recorded as a subscription
        self.assertFalse(s.is_blocked(self.user))
        self.assertEqual(s.check_tier(self.user), 'free')
        self.assertEqual(len(s.fines_for(self.user)), 1)
        # idempotent: the webhook and the return-page verify may both fire
        with patch.object(s, 'block_user', wraps=s.block_user) as bu:
            rec2 = s.record_paystack_success(data)
        self.assertTrue(rec2['unblocked']); bu.assert_not_called()
        self.assertEqual(len(s.fines_for(self.user)), 1)
        # verify-on-return returns no tier token for a fine
        with patch.object(s, 'http_fetch', return_value=(200, json.dumps({'status': True, 'data': data}).encode(), '')):
            s.KEYS['PAYSTACK_SECRET_KEY'] = 'sk_test_x'
            v = s.paystack_verify('FINE-abc')
        self.assertTrue(v['fine']); self.assertNotIn('token', v)

    def test_plan_purchase_does_not_lift_a_block(self):
        s.block_user(self.user, True, 'x', 'owner@example.test')
        data = {'reference': 'PLAN-1', 'amount': 7500000, 'currency': 'NGN', 'channel': 'card', 'status': 'success',
                'paid_at': '2026-09-20T15:00:00Z', 'customer': {'email': self.user}, 'metadata': {'plan': 'pro'}}
        with patch.object(s, 'supabase_store_subscriber'):
            s.record_paystack_success(data)
        self.assertTrue(s.is_blocked(self.user))
        self.assertEqual(s.check_tier(self.user), 'free')      # suspended accounts are free until reinstated

    def test_crypto_fine_invoice_and_grant(self):
        s.block_user(self.user, True, 'x', 'owner@example.test')
        s.KEYS['CRYPTO_WALLET_EVM'] = '0x' + '1' * 40
        with patch.object(s, 'atlos_api', return_value={'Id': 'inv1', 'PaymentLink': 'https://atlos.example/pay'}), patch.object(s, 'crypto_qr_svg_b64', return_value='qr'):
            inv = s.crypto_invoice(self.user, 'fine', 'https://app.example')
        self.assertTrue(inv['ok']); self.assertEqual(inv['amount_usd'], 20); self.assertTrue(inv['ref'].endswith('-fine'))
        with patch.object(s, 'admin_set_pro') as grant:
            r = s.crypto_grant(inv['ref'], 'atlos')
        grant.assert_not_called()
        self.assertTrue(r['unblocked']); self.assertFalse(s.is_blocked(self.user)); self.assertEqual(s.check_tier(self.user), 'free')

    def test_pending_reinstatement_is_retried_from_status(self):
        s.block_user(self.user, True, 'x', 'owner@example.test')
        self.cloud.fail_writes = True                                   # storage hiccup exactly when the fine lands
        r = s.fine_paid(self.user, 'FINE-late', 20, 'USD', 'crypto', 'atlos')
        self.assertTrue(r['ok']); self.assertFalse(r['unblocked']); self.assertTrue(s.is_blocked(self.user))
        self.cloud.fail_writes = False
        st = s.block_status(self.user)                                  # the BLOCKED screen polls this
        self.assertFalse(st['blocked']); self.assertEqual(st['fines_paid'], 1)

    def test_revenue_lists_fines_separately(self):
        s.block_user(self.user, True, 'x', 'owner@example.test')
        s.fine_paid(self.user, 'FINE-r', 31000, 'NGN', 'card', 'paystack')
        summary = s.fines_summary()
        self.assertEqual(summary['count'], 1); self.assertEqual(summary['usd'], 20)

    def test_health_build_marker(self):
        server, t = self.serve()
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/api/health' % server.server_port, timeout=3) as r:
                self.assertEqual(json.loads(r.read())['build'], 'patch16-community-calls-badge')
        finally:
            server.shutdown(); server.server_close(); t.join()


if __name__ == '__main__':
    unittest.main()
