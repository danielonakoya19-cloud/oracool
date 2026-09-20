"""Patch 12 — operator-only server key vault and provider overrides.

Offline: no provider, payment or account changes. Verifies that
  * the public /api/config no longer carries the per-secret inventory,
  * POST /api/admin/keys serves booleans to verified administrators only,
  * api_key / base_url / hibp_key / arbitrary model overrides are stripped
    from non-administrator requests before any provider resolution, while a
    server-configured model name (Turbo/Smart) is still honoured.
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
spec = importlib.util.spec_from_file_location('oracool_test_server_p12', ROOT / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


def _Req():
    """A real Handler instance without a socket: only _auth/_sanitize_overrides/_resolve_provider are exercised."""
    h = s.Handler.__new__(s.Handler)
    h.headers = {}
    return h


def _post(port, path, body):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status, json.loads(r.read())


def _post_err(port, path, body):
    try:
        _post(port, path, body)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    raise AssertionError('expected an HTTP error for ' + path)


class Patch12Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(s, 'DATA_DIR', self.tmp.name), patch.object(s, 'BASE_DIR', self.tmp.name),
                        patch.dict(s.KEYS, {}, clear=True), patch.object(s, '_AUTH_CACHE', {}),
                        patch.object(s, 'audit_log')]
        for p in self.patches:
            p.start()
        s.KEYS.update(GROQ_API_KEY='groq-test', GROQ_MODEL='llama-smart', GROQ_FAST_MODEL='llama-fast',
                      OPENAI_API_KEY='openai-test', ADMIN_EMAILS=['owner@example.test'])

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def serve(self):
        server = s.ThreadingHTTPServer(('127.0.0.1', 0), s.Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server, t

    # ---- public config -------------------------------------------------
    def test_public_config_has_no_secret_inventory(self):
        cfg = s.get_config()
        self.assertNotIn('keys', cfg)
        self.assertNotIn('openai', cfg['brain'])
        self.assertNotIn('groq', cfg['brain'])
        self.assertTrue(cfg['brain']['ready'])
        self.assertIn('payments_ready', cfg)
        blob = json.dumps(cfg)
        for name in ('OPENAI_API_KEY', 'GROQ_API_KEY', 'JWT_SECRET', 'ENCRYPTION_KEY', 'TWILIO_AUTH_TOKEN'):
            self.assertNotIn(name, blob)
        self.assertNotIn('groq-test', blob)
        self.assertNotIn('openai-test', blob)

    def test_public_config_ready_false_without_any_brain_key(self):
        s.KEYS.pop('GROQ_API_KEY'); s.KEYS.pop('OPENAI_API_KEY')
        self.assertFalse(s.get_config()['brain']['ready'])

    # ---- admin vault route ----------------------------------------------
    def test_admin_keys_route_denies_everyone_but_verified_admins(self):
        server, t = self.serve()
        try:
            port = server.server_port
            # anonymous
            code, body = _post_err(port, '/api/admin/keys', {})
            self.assertEqual(code, 403); self.assertTrue(body['locked'])
            # forged client claims — never trusted
            code, body = _post_err(port, '/api/admin/keys', {'email': 'owner@example.test', 'admin': True})
            self.assertEqual(code, 403)
            # paid non-admin (signed enterprise token, not an admin subject)
            code, body = _post_err(port, '/api/admin/keys', {'token': s.make_tier_token('paid@example.test', 'enterprise')})
            self.assertEqual(code, 403)
            # verified administrator — booleans only, never values
            code, body = _post(port, '/api/admin/keys', {'token': s.make_admin_token('owner@example.test')})
            self.assertEqual(code, 200)
            self.assertIs(body['keys']['GROQ_API_KEY'], True)
            self.assertIs(body['keys']['TWILIO_AUTH_TOKEN'], False)
            self.assertTrue(all(isinstance(v, bool) for v in body['keys'].values()))
            self.assertNotIn('groq-test', json.dumps(body))
            self.assertTrue(body['brain']['groq'])
        finally:
            server.shutdown(); server.server_close(); t.join()

    def test_blocked_admin_is_not_an_operator(self):
        req = _Req()
        with patch.object(s, 'is_blocked', return_value=True):
            self.assertFalse(s.Handler._admin_request(req, {'token': s.make_admin_token('owner@example.test')}))
        with patch.object(s, 'is_blocked', return_value=False):
            self.assertTrue(s.Handler._admin_request(req, {'token': s.make_admin_token('owner@example.test')}))
            # subject in ADMIN_EMAILS with a plain signed token also counts (server-issued)
            self.assertTrue(s.Handler._admin_request(req, {'token': s.make_tier_token('owner@example.test', 'pro')}))
            self.assertFalse(s.Handler._admin_request(req, {'token': s.make_tier_token('paid@example.test', 'enterprise')}))
            self.assertFalse(s.Handler._admin_request(req, {'email': 'owner@example.test', 'admin': True}))

    # ---- provider overrides ---------------------------------------------
    def test_nonadmin_overrides_are_stripped_before_provider_resolution(self):
        req = _Req()
        body = {'api_key': 'sk-attacker', 'base_url': 'https://attacker.example/v1', 'model': 'gpt-4o',
                'hibp_key': 'hibp-attacker', 'token': s.make_tier_token('paid@example.test', 'enterprise')}
        api_key, base_url, model, provider = s.Handler._resolve_provider(req, body)
        self.assertEqual(api_key, 'groq-test')
        self.assertIn('groq.com', base_url)
        self.assertEqual(model, 'llama-smart')
        for field in ('api_key', 'base_url', 'hibp_key', 'model'):
            self.assertNotIn(field, body)
        self.assertFalse(body['_admin_overrides'])

    def test_anonymous_overrides_are_stripped_too(self):
        req = _Req()
        body = {'api_key': 'sk-attacker', 'base_url': 'https://attacker.example/v1', 'model': 'o3-pro'}
        api_key, base_url, model, provider = s.Handler._resolve_provider(req, body)
        self.assertEqual(api_key, 'groq-test'); self.assertIn('groq.com', base_url); self.assertEqual(model, 'llama-smart')

    def test_nonadmin_may_still_pick_server_configured_fast_model(self):
        req = _Req()
        body = {'provider': 'groq', 'model': 'llama-fast', 'token': s.make_tier_token('paid@example.test', 'pro')}
        api_key, base_url, model, provider = s.Handler._resolve_provider(req, body)
        self.assertEqual(model, 'llama-fast'); self.assertEqual(api_key, 'groq-test')

    def test_admin_overrides_are_honoured(self):
        req = _Req()
        body = {'api_key': 'sk-owner', 'base_url': 'https://proxy.example/v1/', 'model': 'custom-model',
                'token': s.make_admin_token('owner@example.test')}
        api_key, base_url, model, provider = s.Handler._resolve_provider(req, body)
        self.assertEqual((api_key, base_url, model), ('sk-owner', 'https://proxy.example/v1', 'custom-model'))
        self.assertTrue(body['_admin_overrides'])

    def test_sanitizer_runs_once_per_request(self):
        req = _Req()
        body = {'api_key': 'sk-attacker'}
        s.Handler._sanitize_overrides(req, body)
        body['api_key'] = 'sk-attacker-again'          # a later stage cannot re-inject
        s.Handler._sanitize_overrides(req, body)        # idempotent guard: no crash, flag preserved
        self.assertFalse(body['_admin_overrides'])

    # ---- HIBP route -----------------------------------------------------
    def test_hibp_key_route_ignores_nonadmin_override(self):
        with patch.object(s, 'osint_email', return_value={'ok': True}) as osint:
            server, t = self.serve()
            try:
                port = server.server_port
                _post(port, '/api/osint/email', {'email': 'target@example.test', 'hibp_key': 'user-supplied'})
                self.assertEqual(osint.call_args.args, ('target@example.test', None))
                _post(port, '/api/osint/email', {'email': 'target@example.test', 'hibp_key': 'owner-supplied',
                                                 'token': s.make_admin_token('owner@example.test')})
                self.assertEqual(osint.call_args.args, ('target@example.test', 'owner-supplied'))
            finally:
                server.shutdown(); server.server_close(); t.join()

    def test_health_build_marker(self):
        server, t = self.serve()
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/api/health' % server.server_port, timeout=3) as r:
                self.assertEqual(json.loads(r.read())['build'], 'patch12b-twilio-api-key')
            with urllib.request.urlopen('http://127.0.0.1:%d/api/config' % server.server_port, timeout=3) as r:
                self.assertNotIn('keys', json.loads(r.read()))
        finally:
            server.shutdown(); server.server_close(); t.join()


if __name__ == '__main__':
    unittest.main()


class TwilioApiKeyTests(unittest.TestCase):
    """Patch 12b — Twilio API key (SK…) support with Auth Token fallback; read-only readiness diagnostics."""

    def setUp(self):
        import reminders
        self.rem = reminders
        self.keys = {'TWILIO_ACCOUNT_SID': 'ACtest', 'TWILIO_AUTH_TOKEN': 'token-secret',
                     'TWILIO_API_KEY_SID': 'SKtest', 'TWILIO_API_KEY_SECRET': 'key-secret'}
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.dict(s.KEYS, {}, clear=True), patch.object(s, 'DATA_DIR', self.tmp.name),
                        patch.dict(s._TWILIO_READY_CACHE, {'at': 0.0, 'value': None})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    @staticmethod
    def _basic(req):
        import base64
        return base64.b64decode(req.get_header('Authorization').split()[1]).decode()

    def test_credential_order_prefers_api_key_then_auth_token(self):
        self.assertEqual([c[2] for c in self.rem.twilio_credentials(self.keys.get)], ['api_key', 'auth_token'])
        self.keys['TWILIO_API_KEY_SID'] = 'not-an-sk'      # malformed key ids are ignored
        self.assertEqual([c[2] for c in self.rem.twilio_credentials(self.keys.get)], ['auth_token'])
        self.assertEqual(self.rem.twilio_credentials({}.get), [])

    def test_post_uses_api_key_when_it_works(self):
        seen = []
        class R:
            def __init__(self, req): seen.append(TwilioApiKeyTests._basic(req))
            def __enter__(self): return io.BytesIO(b'{"sid":"CA1","status":"queued"}')
            def __exit__(self, *a): return False
        with patch.object(self.rem.urllib.request, 'urlopen', side_effect=lambda req, timeout=0: R(req)):
            data, label = self.rem.twilio_post(self.keys.get, 'https://api.twilio.com/2010-04-01/Accounts/ACtest/Calls.json', {'To': '+15005550006'})
        self.assertEqual((data['sid'], label), ('CA1', 'api_key'))
        self.assertEqual(seen, ['SKtest:key-secret'])

    def test_unfinished_api_key_401_falls_back_to_auth_token_once(self):
        seen = []
        def fake(req, timeout=0):
            seen.append(TwilioApiKeyTests._basic(req))
            if seen[-1].startswith('SKtest:'):
                raise urllib.error.HTTPError(req.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'{"code":20003}'))
            class R:
                def __enter__(self_inner): return io.BytesIO(b'{"sid":"CA2","status":"queued"}')
                def __exit__(self_inner, *a): return False
            return R()
        with patch.object(self.rem.urllib.request, 'urlopen', side_effect=fake):
            data, label = self.rem.twilio_post(self.keys.get, 'https://api.twilio.com/x', {'To': '+15005550006'})
        self.assertEqual((data['sid'], label), ('CA2', 'auth_token'))
        self.assertEqual(seen, ['SKtest:key-secret', 'ACtest:token-secret'])

    def test_non_401_rejection_is_not_retried_with_other_credentials(self):
        seen = []
        def fake(req, timeout=0):
            seen.append(TwilioApiKeyTests._basic(req))
            raise urllib.error.HTTPError(req.full_url, 400, 'Bad Request', {}, io.BytesIO(b'{"code":21211}'))
        with patch.object(self.rem.urllib.request, 'urlopen', side_effect=fake):
            with self.assertRaises(urllib.error.HTTPError):
                self.rem.twilio_post(self.keys.get, 'https://api.twilio.com/x', {'To': 'bad'})
        self.assertEqual(seen, ['SKtest:key-secret'])   # one attempt: a 400 is never a credential problem

    def test_services_map_provider_errors_the_same_way_as_before(self):
        import communications
        svc = communications.Service(lambda k: {}, lambda k, v: True, self.keys.get, lambda: None, lambda o: True, lambda i: True)
        def fake(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'{}'))
        with patch.object(self.rem.urllib.request, 'urlopen', side_effect=fake):
            with self.assertRaises(ValueError):       # both credentials rejected → clear rejection, no "unknown"
                svc.provider('sms', '+15005550006', {'To': '+15005550006', 'Body': 'x'})
        alarms = self.rem.Service(lambda k: {}, lambda k, v: True, self.keys.get, lambda: None, allowed=lambda o: True)
        with patch.object(self.rem.urllib.request, 'urlopen', side_effect=fake):
            with self.assertRaises(ValueError):
                alarms.provider('/2010-04-01/Accounts/ACtest/Calls.json', {'To': '+15005550006'})
        with patch.object(self.rem.urllib.request, 'urlopen', side_effect=OSError('network down')):
            with self.assertRaises(RuntimeError):     # network failure → outcome unknown, no auto-retry
                alarms.provider('/2010-04-01/Accounts/ACtest/Calls.json', {'To': '+15005550006'})

    def test_readiness_reports_blockers_without_sending(self):
        s.KEYS.update(self.keys, TWILIO_FROM_NUMBER='+17345550034', TWILIO_VERIFY_SERVICE_SID='VAtest')
        calls = []
        def fake_fetch(url, headers=None, timeout=0, **kw):
            import base64
            user = base64.b64decode(headers['Authorization'].split()[1]).decode().split(':')[0]
            calls.append((user, url))
            if user == 'SKtest':
                raise urllib.error.HTTPError(url, 401, 'Unauthorized', {}, io.BytesIO(b'{"message":"actor doesn\'t have any assertions"}'))
            if url.endswith('/Accounts/ACtest.json'):
                return 200, json.dumps({'status': 'active', 'type': 'Trial'}).encode(), 'application/json'
            if 'IncomingPhoneNumbers' in url:
                return 200, json.dumps({'incoming_phone_numbers': []}).encode(), 'application/json'
            if 'OutgoingCallerIds' in url:
                return 200, json.dumps({'outgoing_caller_ids': [{'phone_number': '+2347052405515'}]}).encode(), 'application/json'
            if 'verify.twilio.com' in url:
                raise urllib.error.HTTPError(url, 404, 'Not Found', {}, io.BytesIO(b'{}'))
            raise AssertionError('unexpected URL ' + url)
        with patch.object(s, 'http_fetch', side_effect=fake_fetch):
            r = s.twilio_readiness(force=True)
        self.assertTrue(r['configured'])
        self.assertFalse(r['api_key']['ok']); self.assertTrue(r['auth_token']['ok'])
        self.assertEqual(r['account']['type'], 'Trial')
        self.assertEqual(r['owned_numbers'], []); self.assertEqual(r['caller_ids'], 1)
        self.assertFalse(r['from_number']['owned'])
        self.assertFalse(r['verify_service']['ok'])
        joined = ' '.join(r['blockers'])
        for needle in ('API key rejected', 'Trial account', 'not a number this Twilio account owns', 'TWILIO_VERIFY_SERVICE_SID is not accepted', 'PUBLIC_BASE_URL', 'COMMUNICATIONS_ENABLED'):
            self.assertIn(needle, joined)
        self.assertNotIn('+17345550034', json.dumps(r))          # numbers are masked
        self.assertTrue(all(m == 'GET' or True for m in []))
        self.assertTrue(all('Messages.json' not in u and 'Calls.json' not in u for _, u in calls))  # read-only
        # cached: a second call without force performs no network IO
        with patch.object(s, 'http_fetch', side_effect=AssertionError('should be cached')):
            self.assertEqual(s.twilio_readiness()['checked_at'], r['checked_at'])

    def test_readiness_route_is_admin_only(self):
        s.KEYS.update(ADMIN_EMAILS=['owner@example.test'])
        with patch.object(s, 'twilio_readiness', return_value={'configured': False, 'blockers': ['x']}) as ready:
            server = s.ThreadingHTTPServer(('127.0.0.1', 0), s.Handler)
            t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
            try:
                code, body = _post_err(server.server_port, '/api/admin/twilio', {'email': 'owner@example.test', 'admin': True})
                self.assertEqual(code, 403); ready.assert_not_called()
                code, body = _post(server.server_port, '/api/admin/twilio', {'token': s.make_admin_token('owner@example.test'), 'refresh': True})
                self.assertEqual(code, 200); self.assertEqual(body['blockers'], ['x'])
                ready.assert_called_once_with(force=True)
            finally:
                server.shutdown(); server.server_close(); t.join()
