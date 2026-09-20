"""iPhone / Safari (WebKit) check.
1. Real WebKit engine with iPhone emulation: landing page, /app boot to the sign-in gate, no page errors,
   the signed-in console renders, the BLOCKED screen renders.
2. Boot failsafe: when the main script cannot parse (what an old iOS does on unsupported syntax),
   the user sees a readable message instead of a black screen (checked in WebKit and Chromium).
3. Static guard: the inline scripts contain no syntax that iOS 15 Safari cannot parse
   (regex lookbehind was the cause of the black screen on iPhones running iOS < 16.4).
"""
import base64, json, re, subprocess, sys, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8000'
html = open('/home/user/oracool/index.html', encoding='utf-8').read()

# ---- 3. static guard -------------------------------------------------------
scripts = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
assert len(scripts) >= 2, 'expected failsafe + main inline scripts'
for i, js in enumerate(scripts):
    assert '(?<=' not in js and '(?<!' not in js, f'script {i}: regex lookbehind breaks Safari < 16.4'
    assert re.search(r'/\\p\{', js) is None, f'script {i}: literal unicode property escape — build it with new RegExp so old engines skip it'
    assert '.at(-' not in js and 'structuredClone' not in js and 'Object.hasOwn(' not in js, f'script {i}: API missing before iOS 15.4'
open('/tmp/_failsafe.js', 'w').write(scripts[0]); open('/tmp/_main.js', 'w').write(scripts[1])
node = subprocess.run(['node', '-e', """
const acorn=require('/tmp/esb/node_modules/acorn'); const fs=require('fs');
acorn.parse(fs.readFileSync('/tmp/_failsafe.js','utf8'),{ecmaVersion:5});
acorn.parse(fs.readFileSync('/tmp/_main.js','utf8'),{ecmaVersion:2020});
console.log('ok');"""], capture_output=True, text=True)
assert node.stdout.strip() == 'ok', node.stderr[:500]
print('Static: failsafe is ES5, main script is ES2020 (iOS 13.4+ syntax), no lookbehind PASS')

cfg = json.load(urllib.request.urlopen(BASE + '/api/config'))
token = 'e30.' + base64.urlsafe_b64encode(json.dumps({'exp': int(time.time()) + 3600}).encode()).decode().rstrip('=') + '.test'

def api_ok(route, blocked=False):
    path = route.request.url.split('8000')[-1]
    if path == '/api/config':
        route.fulfill(status=200, content_type='application/json', body=json.dumps(cfg)); return
    if path == '/api/auth/me':
        route.fulfill(status=200, content_type='application/json', body=json.dumps({'status': 200, 'data': {'email': 'iphone@example.test'}, 'admin': False, 'blocked': blocked})); return
    if path == '/api/block/status':
        route.fulfill(status=200, content_type='application/json', body=json.dumps({'blocked': blocked, 'reason': 'test', 'fine': {'usd': 20, 'ngn': 31000, 'currency': 'NGN'}, 'paystack': True, 'crypto': True})); return
    if blocked:
        route.fulfill(status=403, content_type='application/json', body=json.dumps({'error': 'suspended', 'suspended': True, 'fine_usd': 20})); return
    d = {'sessions': []} if path == '/api/chat/sessions' else {}
    route.fulfill(status=200, content_type='application/json', body=json.dumps(d))

with sync_playwright() as p:
    iphone = p.devices['iPhone 13']
    for engine_name in ('webkit', 'chromium'):
        engine = getattr(p, engine_name)
        browser = engine.launch(headless=True, args=[] if engine_name == 'webkit' else ['--no-sandbox'])
        ctx = browser.new_context(**iphone, service_workers='block')  # the app registers /sw.js; WebKit routes cannot see SW-mediated requests
        errors = []
        # -- landing page
        page = ctx.new_page(); page.on('pageerror', lambda e: errors.append('landing: ' + str(e)))
        page.goto(BASE + '/'); page.wait_for_load_state('load'); page.wait_for_timeout(500)
        assert page.evaluate("document.body.innerText.length") > 200, 'landing page rendered nothing'
        page.close()
        # -- /app signed out → sign-in gate visible (no black screen)
        page = ctx.new_page(); page.on('pageerror', lambda e: errors.append('gate: ' + str(e)))
        page.route('**/api/**', lambda r: api_ok(r))
        page.goto(BASE + '/app')
        page.wait_for_selector('#app:not(.hidden)', timeout=20000)
        page.wait_for_timeout(800)
        gate_visible = page.evaluate("(()=>{const g=document.querySelector('#authGate'); if(!g) return false; const r=g.getBoundingClientRect(); return getComputedStyle(g).display!=='none' && r.height>100;})()")
        assert gate_visible, 'sign-in gate not visible on iPhone'
        assert not page.evaluate("!!document.getElementById('bootFail')"), 'failsafe fired although the app booted'
        if engine_name == 'webkit':
            page.screenshot(path='/home/user/oracool-iphone-gate.png')
        page.close()
        # -- /app signed in → console renders; suspended → BLOCKED screen
        for blocked in (False, True):
            page = ctx.new_page(); page.on('pageerror', lambda e: errors.append(f'app blocked={blocked}: ' + str(e)))
            session = {'user': {'email': 'iphone@example.test'}, 'access_token': token, 'refresh_token': 't', 'admin': False, 'blocked': blocked}
            page.add_init_script(f"localStorage.setItem('oracool_session',{json.dumps(json.dumps(session))});localStorage.setItem('oracool',JSON.stringify({{lockMins:0,ttsOn:false}}));")
            page.route('**/api/**', lambda route, request, b=blocked: api_ok(route, b))
            page.goto(BASE + '/app'); page.wait_for_selector('#app:not(.hidden)', timeout=20000); page.wait_for_timeout(1000)
            if blocked:
                page.wait_for_selector('#blockedScreen.open', timeout=15000)
                assert page.inner_text('#blockedTitle').strip() == 'BLOCKED'
                if engine_name == 'webkit': page.screenshot(path='/home/user/oracool-iphone-blocked.png')
            else:
                assert page.locator('#input').is_visible(), 'chat input not visible'
                assert not page.locator('#input').is_disabled()
                if engine_name == 'webkit': page.screenshot(path='/home/user/oracool-iphone-console.png')
            page.close()
        # -- failsafe: main script fails to parse (as on an old iOS) → readable message, not a black screen
        page = ctx.new_page()
        def broken(route):
            r = route.fetch(); body = r.text()
            body = body.replace("const $ = (s, el=document) => el.querySelector(s);", "const $ = (s, el=document) =>> el.querySelector(s);", 1)
            route.fulfill(status=200, content_type='text/html; charset=utf-8', body=body)
        page.route(BASE + '/app', broken)
        page.route('**/api/**', lambda r: api_ok(r))
        page.goto(BASE + '/app')
        page.wait_for_selector('#bootFail', timeout=8000)
        txt = page.inner_text('#bootFail')
        assert 'could not start' in txt and 'ORA-COOL' in txt, txt
        assert 'iOS 15.0' in txt, txt   # the iPhone 13 descriptor reports iOS 15 → version-specific advice
        if engine_name == 'webkit': page.screenshot(path='/home/user/oracool-iphone-failsafe.png')
        page.close()
        ctx.close(); browser.close()
        assert not errors, errors
        print(f'{engine_name} (iPhone 13 emulation): landing, sign-in gate, console, BLOCKED screen, failsafe message PASS')
print('ALL PASS')
