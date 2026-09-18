# Manual browser regression: pip install playwright; playwright install --with-deps chromium; run local server first.
import json,base64,time,urllib.request
from playwright.sync_api import sync_playwright
cfg=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/config'))
token='e30.'+base64.urlsafe_b64encode(json.dumps({'exp':int(time.time())+3600}).encode()).decode().rstrip('=')+'.test'
sess={'user':{'email':'fallback@example.test'},'access_token':token,'refresh_token':'test'}
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,args=['--no-sandbox','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream'])
 c=b.new_context(permissions=['microphone']);page=c.new_page();errs=[];page.on('pageerror',lambda e:errs.append(str(e)));clips=[]
 page.add_init_script('''window.SpeechRecognition=undefined;window.webkitSpeechRecognition=undefined;window._spoken=[];
 Object.defineProperty(window,'speechSynthesis',{value:{getVoices(){return[]},cancel(){},speak(u){window._spoken.push(u.text);u.onstart?.();setTimeout(()=>u.onend?.(),30)}},configurable:true});
 '''+f"localStorage.setItem('oracool_session',{json.dumps(json.dumps(sess))});localStorage.setItem('oracool',JSON.stringify({{lockMins:0}}));")
 def api(route):
  path=route.request.url.split('8000')[-1];data={};body=json.loads(route.request.post_data or '{}')
  if path=='/api/config':data=cfg
  elif path=='/api/auth/me':data={'status':200,'data':sess['user']}
  elif path=='/api/chat/sessions':data={'sessions':[]}
  elif path=='/api/chat/session/new':data={'id':'test'}
  elif path=='/api/voice/transcribe':clips.append(body);data={'text':'Hello from the recorder fallback'}
  elif path=='/api/chat':
   route.fulfill(status=200,content_type='text/event-stream',body='data: '+json.dumps({'choices':[{'delta':{'content':'Recorder fallback is working.'}}]})+'\n\ndata: [DONE]\n\n');return
  route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
 page.route('**/api/**',api);page.goto('http://127.0.0.1:8000/');page.wait_for_selector('#app:not(.hidden)',timeout=15000);page.click('#hfStart')
 page.wait_for_function("window._spoken.some(t=>t.includes('Recorder fallback is working'))",timeout=25000)
 assert clips and len(clips[0]['audio'])>100
 page.click('#hfStop');assert not page.evaluate('OraVoice.enabled');assert not errs,errs
 print('Real browser MediaRecorder fallback captured a synthetic clip, used mocked transcription, spoke reply and stopped: PASS')
 b.close()
