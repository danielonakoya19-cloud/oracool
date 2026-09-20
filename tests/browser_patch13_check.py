"""Patch 13 browser check: suspended account sees the holographic BLOCKED screen,
cannot send anything, can start the fine payment, and the screen clears when the
status endpoint reports reinstatement. All /api/** calls are intercepted.
"""
import base64, json, time, urllib.request
from playwright.sync_api import sync_playwright

cfg = json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/config'))
token = 'e30.' + base64.urlsafe_b64encode(json.dumps({'exp': int(time.time()) + 3600}).encode()).decode().rstrip('=') + '.test'

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1280, 'height': 860})
    errors, calls, state = [], [], {'blocked': True}
    page.on('pageerror', lambda e: errors.append(str(e)))
    session = {'user': {'email': 'blocked@example.test'}, 'access_token': token, 'refresh_token': 'test', 'admin': False, 'blocked': False}
    page.add_init_script(f"localStorage.setItem('oracool_session',{json.dumps(json.dumps(session))});localStorage.setItem('oracool',JSON.stringify({{lockMins:0,ttsOn:false}}));")

    def api(route):
        path = route.request.url.split('8000')[-1]
        try:
            body = json.loads(route.request.post_data or '{}')
        except Exception:
            body = {}
        calls.append((path, body))
        if path == '/api/config':
            route.fulfill(status=200, content_type='application/json', body=json.dumps(cfg)); return
        if path == '/api/auth/me':
            # the server reports the suspension on the very next identity check
            route.fulfill(status=200, content_type='application/json', body=json.dumps({'status': 200, 'data': session['user'], 'admin': False, 'blocked': state['blocked']})); return
        if path == '/api/block/status':
            route.fulfill(status=200, content_type='application/json', body=json.dumps({'blocked': state['blocked'], 'reason': 'Misuse of the AI', 'blocked_at': '2026-09-20 15:00:00', 'fine': {'usd': 20, 'ngn': 31000, 'currency': 'NGN'}, 'paystack': True, 'crypto': True})); return
        if path == '/api/block/fine/paystack':
            route.fulfill(status=200, content_type='application/json', body=json.dumps({'authorization_url': 'http://127.0.0.1:8000/app?fine_redirect=1', 'reference': 'FINE-1'})); return
        if path.startswith('/api/block/'):
            route.fulfill(status=200, content_type='application/json', body='{}'); return
        if state['blocked']:
            route.fulfill(status=403, content_type='application/json', body=json.dumps({'error': 'suspended', 'suspended': True, 'blocked': True, 'fine_usd': 20})); return
        d = {'sessions': []} if path == '/api/chat/sessions' else {}
        route.fulfill(status=200, content_type='application/json', body=json.dumps(d))

    page.route('**/api/**', api)
    page.goto('http://127.0.0.1:8000/app')
    page.wait_for_selector('#app:not(.hidden)', timeout=15000)
    # any suspended response flips the screen on (the boot-time /api/chat/sessions call returns 403 suspended)
    page.wait_for_selector('#blockedScreen.open', timeout=15000)
    assert page.inner_text('#blockedTitle').strip() == 'BLOCKED'
    box = page.locator('#blockedTitle').bounding_box()
    assert box and box['height'] > 120, box                               # big text
    assert 'Misuse of the AI' in page.inner_text('#blockedReason')
    assert '$20' in page.inner_text('#blockedFine')
    assert page.locator('#input').is_disabled()
    # sending is refused client-side and nothing reaches /api/chat
    before = len([c for c in calls if c[0] == '/api/chat'])
    page.evaluate("send('hello?')")
    page.wait_for_timeout(600)
    assert len([c for c in calls if c[0] == '/api/chat']) == before
    # the screen sits above everything else
    z = page.evaluate("getComputedStyle(document.querySelector('#blockedScreen')).zIndex")
    assert int(z) >= 9999, z
    page.screenshot(path='/home/user/oracool-blocked-screen.png')
    # pay button asks the server for a fine checkout for THIS account
    page.click('#blockedPayCard')
    page.wait_for_function("location.search.includes('fine_redirect')", timeout=10000)
    fine_calls = [b for (pth, b) in calls if pth == '/api/block/fine/paystack']
    assert fine_calls and fine_calls[0].get('email') == 'blocked@example.test', fine_calls
    # reinstatement: status flips → screen clears on the manual re-check
    page.goto('http://127.0.0.1:8000/app')
    page.wait_for_selector('#blockedScreen.open', timeout=15000)
    state['blocked'] = False
    page.click('#blockedRecheck')
    page.wait_for_function("!document.querySelector('#blockedScreen').classList.contains('open')", timeout=10000)
    assert not page.locator('#input').is_disabled()
    assert not errors, errors
    print('Suspended user: holographic BLOCKED screen, input locked, no chat requests, fine checkout for own account, auto-clear after reinstatement PASS')
    browser.close()
