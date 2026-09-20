"""Patch 16 — fines cancelled, verified badge (paid monthly, admin pre-verified),
profile pictures, voice notes, audio/video call signaling, reported & bannable
groups/channels, and continued IP/privacy guarantees.

Offline: Supabase REST tables emulated by FakeRest (from test_patch15),
Supabase storage and Paystack mocked at the http_fetch level.
"""
import importlib.util
import json
import base64
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
spec = importlib.util.spec_from_file_location('oracool_test_server_p16', ROOT / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
import community  # noqa: E402
from test_patch15 import FakeRest  # noqa: E402


def _post(port, path, body):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def _no_ip_keys(obj, path='$'):
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ('ip', 'client_ip', 'remote_addr', 'ip_address', 'address'):
                bad.append(path + '.' + str(k))
            bad += _no_ip_keys(v, path + '.' + str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += _no_ip_keys(v, path + '[%d]' % i)
    return bad


ADMIN = 'owner@example.test'
USER = 'verified@example.test'


class FakeRest16(FakeRest):
    """Patch-16 schema: adds comm_room_reports (Patch 15's fake predates it)."""

    def reset(self):
        super().reset()
        self.tables['comm_room_reports'] = {'rows': [], 'next_id': 1}


class FakeCloud:
    def __init__(self):
        self.flags = {}

    def get_flag(self, email):
        e = (email or '').lower()
        return dict(self.flags[e]) if e in self.flags else None

    def upsert_flag(self, rec, block_fields=True):
        email = rec['email'].lower()
        row = self.flags.setdefault(email, {'email': email, 'blocked': False})
        row['last_seen'] = rec.get('last_seen', '')
        if block_fields:
            for f in ('blocked', 'block_reason', 'blocked_by', 'blocked_at'):
                row[f] = rec.get(f)
        return True


class Patch16Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cloud = FakeCloud()
        self.rest = FakeRest16()
        self.storage_calls = []

        def fake_storage_fetch(url, method='GET', headers=None, json_body=None, data=None, timeout=25):
            self.storage_calls.append((method, url))
            return 200, b'{}', 'application/json'

        self.patches = [patch.object(s, 'DATA_DIR', self.tmp.name), patch.object(s, 'BASE_DIR', self.tmp.name),
                        patch.dict(s.KEYS, {}, clear=True), patch.object(s, '_AUTH_CACHE', {}),
                        patch.dict(s._BLOCK_CACHE, {}, clear=True), patch.dict(s._VERIFIED_CACHE, {}, clear=True),
                        patch.object(s, 'audit_log'), patch.object(s, 'emit_event'), patch.object(s, 'notify_admins'),
                        patch.object(s, 'llm_complete', return_value=('', None)),
                        patch.object(s, '_COMMUNITY_SERVICE', None),
                        patch.object(s, 'community_rest', side_effect=self.rest.rest),
                        patch.object(s, 'supabase_get_flag', side_effect=self.cloud.get_flag),
                        patch.object(s, 'supabase_upsert_flag', side_effect=self.cloud.upsert_flag)]
        for p in self.patches:
            p.start()
        s.KEYS.update(SUPABASE_URL='https://example.invalid', SUPABASE_SERVICE_KEY='svc',
                      ADMIN_EMAILS=[ADMIN], JWT_SECRET='t', PAYSTACK_SECRET_KEY='sk_test_dummy')
        s._STORAGE_BUCKETS.clear()
        self.svc = s.community_service()
        self.svc.async_reviews = False
        old = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 7200))
        users = {}
        for e in (USER, 'r1@example.test', 'r2@example.test', 'r3@example.test', ADMIN):
            users[e] = {'email': e, 'created': old, 'last_seen': old}
        s.save_users(users)
        self.svc.ensure_profile(USER, username='verified_user')
        self.svc.ensure_profile('r1@example.test', username='member_one')
        self.svc.ensure_profile('r2@example.test', username='member_two')
        self.svc.ensure_profile('r3@example.test', username='member_three')
        self.svc.ensure_profile(ADMIN, username='the_owner')

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def serve(self):
        server = s.ThreadingHTTPServer(('127.0.0.1', 0), s.Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
        return server, t

    # ---- verified badge ----------------------------------------------------------------
    def test_admin_is_verified_by_default_and_badge_extends_monthly(self):
        self.assertTrue(s.is_verified(ADMIN))
        self.assertFalse(s.is_verified(USER))
        # first month
        s.set_verified(USER, months=1)
        self.assertTrue(s.is_verified(USER))
        until1 = s.verified_status(USER)['verified_until']
        # paying again while active EXTENDS, does not reset
        time.sleep(0.01)
        s.set_verified(USER, months=1)
        until2 = s.verified_status(USER)['verified_until']
        self.assertGreater(until2, until1)
        self.assertTrue(s.verified_status(USER)['verified'])
        self.assertEqual(s.verified_status(USER)['price_usd'], 10)

    def test_verified_member_cannot_be_reported_or_blocked_by_members(self):
        s.set_verified(USER, months=1)
        r = self.svc.report('r1@example.test', 'verified_user', 'I think this person is up to something bad.', None)
        self.assertIn('Verified members', r['error'])
        r = s.block_user(USER, True, 'test', 'r1@example.test')
        self.assertIn('Verified members', r['error'])
        self.assertFalse(s.is_blocked(USER))
        # an administrator can still block a verified member
        r = s.block_user(USER, True, 'admin action', ADMIN)
        self.assertTrue(r.get('ok'), r)
        self.assertTrue(s.is_blocked(USER))
        s.block_user(USER, False, '', ADMIN)

    def test_badge_payment_flow_activates_the_badge(self):
        def fake_fetch(url, method='GET', headers=None, json_body=None, data=None, timeout=25):
            if 'transaction/initialize' in url:
                return 200, json.dumps({'status': True, 'data': {'authorization_url': 'https://pay.example',
                                                                 'reference': 'BADGE-REF-1', 'access_code': 'A1'}}).encode(), 'application/json'
            if 'transaction/verify' in url:
                return 200, json.dumps({'status': True, 'data': {
                    'status': 'success', 'customer': {'email': USER},
                    'metadata': {'purpose': 'verified', 'account': USER},
                    'reference': 'BADGE-REF-1', 'amount': 1550000, 'currency': 'NGN',
                    'channel': 'card', 'paid_at': '2026-09-20 18:00:00'}}).encode(), 'application/json'
            return 200, b'{}', 'application/json'

        with patch.object(s, 'http_fetch', side_effect=fake_fetch):
            init = s.paystack_initialize(USER, 'https://app.example/app', 'verified')
            self.assertIn('authorization_url', init)
            self.assertEqual(init['price_usd'], 10)
            res = s.record_paystack_success_from_reference('BADGE-REF-1')
            self.assertTrue(res['ok']); self.assertTrue(res['badge']); self.assertTrue(res['verified'])
        self.assertTrue(s.is_verified(USER))

    # ---- profile pictures -----------------------------------------------------------------
    def test_avatar_upload_and_validation(self):
        png = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 200).decode()
        with patch.object(s, 'http_fetch', side_effect=self._fake_storage):
            r = s.set_community_avatar(USER, 'data:image/png;base64,' + png)
        self.assertTrue(r.get('ok'), r)
        self.assertIn('/storage/v1/object/public/avatars/', r['avatar_url'])
        prof = self.svc._prof(USER)
        self.assertEqual(prof['avatar_url'], r['avatar_url'])
        # wrong type and oversize are refused
        with patch.object(s, 'http_fetch', side_effect=self._fake_storage):
            self.assertIn('error', s.set_community_avatar(USER, 'data:text/plain;base64,' + base64.b64encode(b'x' * 10).decode()))
            err = s.set_community_avatar(USER, 'data:image/png;base64,' + base64.b64encode(b'0' * (3 * 1024 * 1024)).decode())['error']
            self.assertIn('too large', err)

    def _fake_storage(self, url, method='GET', headers=None, json_body=None, data=None, timeout=25):
        return 200, b'{}', 'application/json'

    # ---- voice notes ------------------------------------------------------------------------
    def test_voice_note_dm(self):
        payload = base64.b64encode(b'\x01\x22\x37' * 4000).decode()
        with patch.object(s, 'http_fetch', side_effect=self._fake_storage):
            up = s.upload_community_media('r1@example.test', 'data:audio/webm;base64,' + payload)
        self.assertTrue(up.get('ok'), up)
        self.assertIn('/storage/v1/object/public/media/', up['media_url'])
        self.svc.last_post.clear()
        r = self.svc.dm_send('r1@example.test', 'member_two', '', media_url=up['media_url'])
        self.assertTrue(r.get('ok'), r)
        m = r['message']
        self.assertEqual((m['body'], m.get('media_url')[:20]), ('🎤 Voice note', up['media_url'][:20]))
        msgs = self.svc.dm_messages('r2@example.test', 'member_one')['messages']
        self.assertTrue(any(x.get('media_url') for x in msgs))
        # a client-supplied foreign URL is refused
        self.svc.last_post.clear()
        self.assertIn('uploaded first', self.svc.dm_send('r1@example.test', 'member_two', 'hi',
                                                         media_url='https://evil.example/x.webm')['error'])
        self.assertIn('Unsupported', s.upload_community_media(USER, 'data:text/plain;base64,abc')['error'])

    # ---- audio / video call signaling -----------------------------------------------------------
    def test_call_signaling_state_machine(self):
        a, b = 'r1@example.test', 'r2@example.test'
        cid = s.call_start(a, b, 'video')
        self.assertTrue(cid.startswith('call-'))
        s.call_set_sdp(cid, a, json.dumps({'type': 'offer', 'sdp': 'x'}))
        inc, ups = s.call_poll(b)
        self.assertIsNotNone(inc); self.assertEqual(inc['from'], a); self.assertEqual(inc['kind'], 'video')
        self.assertEqual(ups, [])
        s.call_set_sdp(cid, b, json.dumps({'type': 'answer', 'sdp': 'y'}))
        inc2, ups2 = s.call_poll(a)
        self.assertIsNone(inc2)
        self.assertTrue(any(u['event'] == 'answer' for u in ups2))
        s.call_add_ice(cid, b, {'candidate': 'c1'})
        s.call_add_ice(cid, b, {'candidate': 'c2'})
        _, ups3 = s.call_poll(a)
        ice = [u for u in ups3 if u['event'] == 'ice']
        self.assertEqual(len(ice[0]['candidates']), 2)
        _, ups4 = s.call_poll(a)
        self.assertFalse([u for u in ups4 if u['event'] == 'ice'])   # delivered once
        s.call_close(cid, b)
        _, ups5 = s.call_poll(a)
        self.assertTrue(any(u['event'] == 'closed' and u['by'] == b for u in ups5))
        # strangers see nothing
        inc6, ups6 = s.call_poll('r3@example.test')
        self.assertIsNone(inc6); self.assertEqual(ups6, [])

    # ---- reported groups & channels -----------------------------------------------------------
    def test_room_report_and_admin_ban(self):
        r = self.svc.create_room('r1@example.test', 'Dark Corner', 'group', 'suspicious', True)
        slug = r['room']['id']
        self.svc.last_post.clear()
        self.assertTrue(self.svc.room_send('r2@example.test', slug, 'hello')[ 'ok'])
        rr = self.svc.report_room('r2@example.test', slug, 'People are selling stolen data in here.')
        self.assertTrue(rr['ok']); self.assertEqual(rr['reports'], 1)
        rr = self.svc.report_room('r2@example.test', slug, 'Still selling stolen data.')
        self.assertEqual(rr['reports'], 1)   # one open report per member
        rows = self.svc.admin_overview()['rooms']
        hit = next(x for x in rows if x['slug'] == slug)
        self.assertEqual(hit['reports'], 1); self.assertFalse(hit['banned'])
        # ban it
        b = self.svc.admin_ban_room(slug, True, 'Illegal sales', ADMIN)
        self.assertTrue(b.get('ok'), b); self.assertTrue(b['room']['banned'])
        self.svc.last_post.clear()
        self.assertIn('banned', self.svc.room_send('r3@example.test', slug, 'hi there')['error'])
        self.assertIn('banned', self.svc.room_messages('r3@example.test', slug)['error'])
        # unban restores posting
        u = self.svc.admin_ban_room(slug, False, '', ADMIN)
        self.assertTrue(u.get('ok')); self.assertFalse(u['room']['banned'])
        self.svc.last_post.clear()
        self.assertTrue(self.svc.room_send('r3@example.test', slug, 'back again')['ok'])
        # DM threads cannot be reported — report the member instead
        dm = self.svc._ensure_dm_room('r2@example.test', 'r1@example.test')
        self.assertIn('cannot be reported', self.svc.report_room('r2@example.test', dm['slug'], 'bad stuff here')['error'])

    # ---- fines cancelled ----------------------------------------------------------------------------
    def test_no_fine_anywhere(self):
        server, t = self.serve()
        try:
            port = server.server_port
            tok = s.sign_jwt({'sub': 'r1@example.test', 'tier': 'free', 'exp': time.time() + 600}, 't')
            s.block_user('r1@example.test', True, 'Misuse', ADMIN)
            code, body = _post(port, '/api/chat', {'token': tok, 'messages': [{'role': 'user', 'content': 'hi'}]})
            self.assertEqual(code, 403); self.assertTrue(body.get('suspended')); self.assertIsNone(body.get('fine_usd'))
            code, st = _post(port, '/api/block/status', {'token': tok})
            self.assertEqual(code, 200); self.assertTrue(st['blocked']); self.assertTrue(st['fine'].get('cancelled'))
            code, body = _post(port, '/api/block/fine/paystack', {'token': tok, 'callback_url': 'https://x/app'})
            self.assertEqual(code, 410); self.assertIn('no longer collected', body['error'])
            # the account keeps its identity — same e-mail, community profile intact
            self.assertIsNotNone(self.svc._prof('r1@example.test'))
        finally:
            server.shutdown(); server.server_close(); t.join()

    # ---- privacy -----------------------------------------------------------------------------------
    def test_new_surfaces_never_leak_ips_or_emails(self):
        s.set_verified(USER, months=1)
        with patch.object(s, 'http_fetch', side_effect=self._fake_storage):
            s.set_community_avatar('r1@example.test', 'data:image/png;base64,' +
                                   base64.b64encode(b'\x89PNG' + b'0' * 100).decode())
        blobs = [self.svc.me('r2@example.test'), self.svc.dm_threads('r2@example.test'),
                 self.svc.people('r2@example.test'), {'rooms': self.svc.rooms('r2@example.test')},
                 s.verified_status(USER)]
        for b in blobs:
            self.assertNotIn('@example.test', json.dumps(b), b)
            self.assertEqual(_no_ip_keys(b), [], json.dumps(b)[:200])
        me = self.svc.me('r1@example.test')
        self.assertTrue(me['profile']['avatar'])
        self.assertTrue(me['profile']['verified'] is False)
        me2 = self.svc.me(USER)
        self.assertTrue(me2['profile']['verified'])

    # ---- AI brief mentions verified status ----------------------------------------------------------
    def test_ai_brief_mentions_verified(self):
        s.set_verified(USER, months=1)
        self.assertIn('VERIFIED', self.svc.brief_for(USER))
        self.assertNotIn('VERIFIED', self.svc.brief_for('r1@example.test'))


if __name__ == '__main__':
    unittest.main()
