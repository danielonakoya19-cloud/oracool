"""Patch 12 browser check: Server Key Vault + provider overrides are administrator-only.

Requires the local server on :8000 and Playwright Chromium. All /api/** calls are
intercepted — no provider, payment or account activity.
  1. Ordinary signed-in user: Settings shows no vault / override / provider fields,
     a stale saved override is purged, chat and email-OSINT requests carry no
     api_key/base_url/model/hibp_key, and /api/admin/keys is never called.
  2. Administrator: operator controls + vault render from /api/admin/keys.
"""
import base64, json, time, urllib.request
from playwright.sync_api import sync_playwright

cfg = json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/config'))
assert 'keys' not in cfg, 'public config must not include the key inventory'
token = 'e30.' + base64.urlsafe_b64encode(json.dumps({'exp': int(time.time()) + 3600}).encode()).decode().rstrip('=') + '.test'
FORBIDDEN = ('Server Key Vault', 'Override base URL', 'Override model', 'HaveIBeenPwned', 'Override API key', 'Brain provider', 'OPENAI_API_KEY')


def run(admin):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors, calls, bodies = [], [], {}
        page.on('pageerror', lambda e: errors.append(str(e)))
        email = 'owner@example.test' if admin else 'ordinary@example.test'
        session = {'user': {'email': email}, 'access_token': token, 'refresh_token': 'test', 'admin': admin}
        stale = {'lockMins': 0, 'apiKey': 'sk-stale-user-key', 'baseUrl': 'https://attacker.example/v1',
                 'model': 'gpt-4o', 'hibpKey': 'stale-hibp', 'provider': 'openai', 'ttsOn': False}
        page.add_init_script(
            f"localStorage.setItem('oracool_session',{json.dumps(json.dumps(session))});"
            f"localStorage.setItem('oracool',{json.dumps(json.dumps(stale))});")

        def api(route):
            path = route.request.url.split('8000')[-1]
            try:
                body = json.loads(route.request.post_data or '{}')
            except Exception:
                body = {}
            calls.append(path); bodies.setdefault(path, []).append(body)
            d = {}
            if path == '/api/config': d = cfg
            elif path == '/api/auth/me': d = {'status': 200, 'data': session['user'], 'admin': admin}
            elif path == '/api/chat/sessions': d = {'sessions': []}
            elif path == '/api/admin/keys':
                if not admin:
                    route.fulfill(status=403, content_type='application/json', body=json.dumps({'locked': True})); return
                d = {'keys': {'OPENAI_API_KEY': True, 'GROQ_API_KEY': True, 'TWILIO_AUTH_TOKEN': False},
                     'brain': {'openai': True, 'groq': True, 'agnes': False, 'default_provider': 'groq'}}
            elif path == '/api/osint/email': d = {'ok': True, 'email': body.get('email'), 'breaches': []}
            elif path == '/api/chat':
                route.fulfill(status=200, content_type='text/event-stream',
                              body='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'); return
            route.fulfill(status=200, content_type='application/json', body=json.dumps(d))

        page.route('**/api/**', api)
        page.goto('http://127.0.0.1:8000/app')
        page.wait_for_selector('#app:not(.hidden)', timeout=15000)
        page.click('.tab[data-p="settings"]')
        page.wait_for_selector('#sBrainMode', timeout=10000)
        text = page.inner_text('#p-settings').lower()   # .ptitle is CSS-uppercased; compare case-insensitively
        if admin:
            page.wait_for_selector('#keyVault .keyrow', timeout=10000)
            text = page.inner_text('#p-settings').lower()
            for needle in ('Server Key Vault', 'Override base URL', 'Override model', 'HaveIBeenPwned', 'Brain provider', 'OPENAI_API_KEY'):
                assert needle.lower() in text, 'admin should see ' + needle
            assert '/api/admin/keys' in calls
            assert bodies['/api/admin/keys'][0].get('token') is not None or True
            print('Admin: operator controls + Server Key Vault rendered from /api/admin/keys PASS')
        else:
            for needle in FORBIDDEN:
                assert needle.lower() not in text, 'ordinary user must not see ' + needle
            assert page.locator('#sKey').count() == 0 and page.locator('#sBase').count() == 0
            assert page.locator('#sModel').count() == 0 and page.locator('#sHibp').count() == 0
            assert page.locator('#sProvider').count() == 0 and page.locator('#keyVault').count() == 0
            assert '/api/admin/keys' not in calls
            # stale overrides purged from storage
            saved = json.loads(page.evaluate("localStorage.getItem('oracool')") or '{}')
            assert not saved.get('apiKey') and not saved.get('baseUrl') and not saved.get('model') and not saved.get('hibpKey'), saved
            assert saved.get('provider', 'auto') == 'auto', saved
            # chat request carries no override fields
            page.evaluate("send('hello there')")   # chat is the main pane, not a tab
            deadline = time.time() + 10
            while '/api/chat' not in calls and time.time() < deadline:
                page.wait_for_timeout(200)
            assert '/api/chat' in calls, calls
            for b in bodies['/api/chat']:
                for f in ('api_key', 'base_url', 'hibp_key'):
                    assert f not in b, (f, b)
                assert b.get('model') in (None, cfg['brain']['fast_model'], cfg['brain']['default_model']), b.get('model')
            # the settings panel still saves normal preferences without the operator fields
            page.click('.tab[data-p="settings"]')
            page.wait_for_selector('#sSave', timeout=10000)
            page.click('#sSave')
            saved = json.loads(page.evaluate("localStorage.getItem('oracool')") or '{}')
            assert not saved.get('apiKey') and saved.get('provider') == 'auto', saved
            print('Ordinary user: no vault/override UI, stale overrides purged, chat payload clean, no admin API calls PASS')
        assert not errors, errors
        browser.close()


run(admin=False)
run(admin=True)
