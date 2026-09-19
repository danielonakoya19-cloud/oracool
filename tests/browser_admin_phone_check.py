"""Manual browser check. Requires local server + Playwright Chromium; no real provider calls."""
import base64,json,time,urllib.request
from playwright.sync_api import sync_playwright
cfg=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/config'))
token='e30.'+base64.urlsafe_b64encode(json.dumps({'exp':int(time.time())+3600}).encode()).decode().rstrip('=')+'.test'
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    page=browser.new_page();errors=[];calls=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    session={'user':{'email':'ordinary@example.test'},'access_token':token,'refresh_token':'test','admin':False}
    page.add_init_script(f"localStorage.setItem('oracool_session',{json.dumps(json.dumps(session))});localStorage.setItem('oracool',JSON.stringify({{lockMins:0}}));")
    def api(route):
        path=route.request.url.split('8000')[-1];d={}
        if path=='/api/config':d=cfg
        elif path=='/api/auth/me':d={'status':200,'data':session['user'],'admin':False}
        elif path=='/api/chat/sessions':d={'sessions':[]}
        elif path.startswith('/api/reminders/'):
            calls.append(path);route.fulfill(status=403,content_type='application/json',body=json.dumps({'error':'Admin only','admin_only':True}));return
        route.fulfill(status=200,content_type='application/json',body=json.dumps(d))
    page.route('**/api/**',api);page.goto('http://127.0.0.1:8000/')
    page.wait_for_selector('#app:not(.hidden)',timeout=15000)
    page.click('#alarmToggle');assert page.locator('#adminAlarmTools').is_hidden()
    assert page.locator('#hfStart').is_visible()
    assert page.locator('#hfLanguage').is_visible()
    page.evaluate("send('set a timer for 20 minutes')")
    page.wait_for_function("document.querySelector('#chat').textContent.includes('restricted to administrators')")
    assert not calls,calls;assert not errors,errors
    print('Ordinary user: phone UI hidden, timer commands blocked without provider/API calls, normal voice controls retained PASS')
    browser.close()
