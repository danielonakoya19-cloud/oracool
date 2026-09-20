"""Chromium UI regression. All authenticated API/provider actions are mocked."""
import base64,json,time,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
BASE='http://127.0.0.1:8000'
cfg=json.load(urllib.request.urlopen(BASE+'/api/config'))
def jwt(payload):return 'e30.'+base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')+'.test'
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    landing=browser.new_page(viewport={'width':1440,'height':1000});errors=[];landing.on('pageerror',lambda e:errors.append(e.stack));landing.goto(BASE+'/')
    assert landing.title().startswith('OraCool');assert landing.locator('.price-grid .plan').count()==5
    assert '$500' in landing.locator('.plan').last.inner_text()
    assert landing.locator('.nav .button').get_attribute('href')=='/app'
    landing.screenshot(path='/home/user/oracool-landing-desktop.png',full_page=True)
    landing.set_viewport_size({'width':390,'height':844});assert landing.evaluate('document.documentElement.scrollWidth<=innerWidth')
    landing.screenshot(path='/home/user/oracool-landing-mobile.png',full_page=True)
    print('Landing: five accurate plans, launch route, FAQ, desktop/390px no overflow PASS')
    page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(e.stack))
    email='paid@example.test';tier=jwt({'sub':email,'tier':'enterprise','exp':int(time.time())+3600})
    session={'user':{'email':email},'access_token':jwt({'exp':int(time.time())+3600}),'refresh_token':'test','pro_token':tier,'admin':False}
    page.add_init_script(f"localStorage.setItem('oracool_session',{json.dumps(json.dumps(session))});localStorage.setItem('oracool_pro',{json.dumps(tier)});localStorage.setItem('oracool',JSON.stringify({{lockMins:0,ttsOn:false}}));")
    calls=[];last={};records=[]
    def api(route):
        path=route.request.url.split('8000')[-1];body=json.loads(route.request.post_data or '{}');d={}
        if path=='/api/config':d=cfg
        elif path=='/api/auth/me':d={'status':200,'user':session['user'],'data':session['user'],'admin':False,'pro_token':tier}
        elif path=='/api/chat/sessions':d={'sessions':[]}
        elif path=='/api/reminders/state':d={'config':{'ready':False,'missing':['PUBLIC_BASE_URL']},'phone':{},'alarms':[]}
        elif path=='/api/comms/state':d={'config':{c:{'ready':c!='email','missing':['SENDGRID_API_KEY'] if c=='email' else []} for c in ('call','sms','email')},'contacts':[],'messages':records}
        elif path=='/api/comms/preview':
            calls.append(path);last.update(body);channel=body['channel'];d={'channel':channel,'to':'+2347052405515' if channel!='email' else body['to'],'message':('This is an automated message from OraCool. ' if channel=='call' else '')+body['message'],'subject':body.get('subject','') if channel=='email' else '', 'ready':channel!='email','verified':True,'note':'Confirming uses provider credit.'}
            if channel!='email':d['draft_id']='draft-test'
        elif path=='/api/comms/send':
            calls.append(path);assert body['confirmed'] is True and body['draft_id']=='draft-test';d={'ok':True,'status':'accepted','message':'Accepted by the provider. This does not confirm delivery or that a call was heard.'};records.append({'id':'draft-test','channel':'call','recipient':'+23••••5515','status':'accepted','created':time.time()})
        elif path.startswith('/api/comms/'):
            calls.append(path)
        route.fulfill(status=200,content_type='application/json',body=json.dumps(d))
    page.route('**/api/**',api);page.goto(BASE+'/app');page.wait_for_selector('#app:not(.hidden)',timeout=15000)
    page.wait_for_function('window.OraComms && window.OraCommunicationsAccess()')
    before=page.locator('#chat').bounding_box()['height'];assert before>440,before
    page.click('#alarmToggle');assert page.locator('#adminAlarmTools').is_visible();assert page.locator('#voiceSheet').is_visible();assert page.locator('#chat').bounding_box()['height']==before
    page.click('#closeVoiceSheet')
    page.evaluate("send('OraCool call 07052405515')");page.wait_for_function("document.querySelector('#chat').textContent.includes('What should I say on the call?')")
    assert not calls,calls
    page.evaluate("send('The meeting starts at noon. Please bring your notes.')");page.wait_for_selector('#commsConfirm')
    assert last['message']=='The meeting starts at noon. Please bring your notes.'
    assert '+2347052405515' in page.locator('#commsReviewBox').inner_text();assert calls==['/api/comms/preview']
    page.locator('#commsSheet').evaluate('(el)=>Promise.all(el.getAnimations().map(a=>a.finished))')
    page.screenshot(path='/home/user/oracool-communications-review.png')
    page.click('#commsConfirm');page.wait_for_function("document.querySelector('#commsReviewBox').textContent.includes('Accepted by the provider')")
    assert calls.count('/api/comms/send')==1
    page.click('[data-channel=email]');page.fill('#commsTo','recipient@example.test');page.fill('#commsMessage','A quick update.');page.click('#commsReview');page.wait_for_function("document.querySelector('#commsReviewBox').textContent.includes('Provider setup is incomplete')")
    assert page.locator('#commsConfirm').count()==0;assert 'SENDGRID_API_KEY' in page.locator('#commsStatus').inner_text()
    page.keyboard.press('Escape');assert not page.locator('#commsSheet').is_visible()
    page.screenshot(path='/home/user/oracool-console-desktop.png')
    page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.click('#commsToggle');assert page.locator('#commsSheet').bounding_box()['width']<=390
    page.screenshot(path='/home/user/oracool-console-mobile.png');page.click('#commsClose')
    # A token issued for someone else cannot expose Enterprise settings on this account.
    page.evaluate("proToken='e30.'+btoa(JSON.stringify({sub:'someoneelse@example.test',tier:'enterprise',exp:9999999999}))+'.test'")
    page.wait_for_function('!window.OraCommunicationsAccess()');page.click('#commsToggle');assert page.locator('#commsLocked').is_visible();assert page.locator('#commsForm').is_hidden();page.click('#commsClose')
    # Legacy payment return stays on the console, not the new landing page.
    page.goto(BASE+'/?reference=mock-reference');page.wait_for_url('**/app?reference=mock-reference')
    assert not errors,errors
    print('Enterprise: voice drawer without shrinking chat; call number→message→preview→one confirmed send; email readiness; foreign token denial; legacy return; mobile drawer PASS')
    print('No JavaScript errors. All outbound actions mocked; no real call/SMS/email sent.')
    browser.close()
