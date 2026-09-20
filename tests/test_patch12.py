"""Patch 12 — operator-only server key vault and provider overrides.

Offline: no provider, payment or account changes. Verifies that
  * the public /api/config no longer carries the per-secret inventory,
  * POST /api/admin/keys serves booleans to verified administrators only,
  * api_key / base_url / hibp_key / arbitrary model overrides are stripped
    from non-administrator requests before any provider resolution, while a
    server-configured model name (Turbo/Smart) is still honoured.
"""
import importlib.util
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
                self.assertEqual(json.loads(r.read())['build'], 'patch12-admin-only-vault')
            with urllib.request.urlopen('http://127.0.0.1:%d/api/config' % server.server_port, timeout=3) as r:
                self.assertNotIn('keys', json.loads(r.read()))
        finally:
            server.shutdown(); server.server_close(); t.join()


if __name__ == '__main__':
    unittest.main()
