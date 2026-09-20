# Manual browser regression: pip install playwright; playwright install --with-deps chromium; run local server first.
import json,base64,time,urllib.request
from playwright.sync_api import sync_playwright
config=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/config'))
token='e30.'+base64.urlsafe_b64encode(json.dumps({'exp':int(time.time())+3600}).encode()).decode().rstrip('=')+'.test'
fixture={'user':{'email':'voice-test@example.test','id':'test'},'access_token':token,'refresh_token':'test','admin':True}
init='''
window._utterances=[];window._recStarts=0;
class FakeSR { start(){window._recStarts++;window._activeRec=this;if(this.onstart)this.onstart();} abort(){} stop(){if(this.onend)this.onend();} emit(t){const r=[{transcript:t}];r.isFinal=true;this.onresult?.({resultIndex:0,results:[r]});} }
window.SpeechRecognition=FakeSR;window.webkitSpeechRecognition=FakeSR;
Object.defineProperty(window,'speechSynthesis',{value:{getVoices(){return [];},cancel(){},speak(u){window._utterances.push(u.text);u.onstart?.();setTimeout(()=>u.onend?.(),30);}},configurable:true});
'''+f"localStorage.setItem('oracool_session',{json.dumps(json.dumps(fixture))});localStorage.setItem('oracool',JSON.stringify({{lockMins:0,lockAlways:false,proactiveConsent:false}}));"
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
 page=browser.new_page();page.add_init_script(init);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 alarms=[];created=[]
 def api(route):
  path=route.request.url.split('8000')[-1];body=json.loads(route.request.post_data or '{}')
  data={}
  if path=='/api/config':data=config
  elif path=='/api/auth/me':data={'status':200,'data':fixture['user'],'admin':True}
  elif path=='/api/chat/sessions':data={'sessions':[]}
  elif path=='/api/chat/session/new':data={'id':'test-session'}
  elif path=='/api/reminders/state':data={'config':{'ready':True,'missing':[]},'phone':{'verified':True,'mask':'+15••••0006'},'alarms':alarms}
  elif path=='/api/reminders/preview':data={'due_at':time.time()+1200,'timezone':'Africa/Lagos','local_time':'2026-09-18T20:20:00+01:00','kind':'timer','label':'OraCool timer'}
  elif path=='/api/reminders/create':
   created.append(body);alarm={'id':'abc',**body,'status':'scheduled'};alarms.append(alarm);data={'ok':True,'alarm':alarm}
  elif path=='/api/chat':
   frames=[{'choices':[{'delta':{'content':'Hello there. I am ready to help.'}}]}]
   route.fulfill(status=200,content_type='text/event-stream',body=''.join('data: '+json.dumps(f)+'\n\n' for f in frames)+'data: [DONE]\n\n');return
  route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
 page.route('**/api/**',api)
 page.goto('http://127.0.0.1:8000/app');page.wait_for_selector('#app:not(.hidden)',timeout=15000)
 page.wait_for_function('!!window.OraVoice && !!session',timeout=10000)
 print('Startup errors:',errors)
 page.click('#hfStart');page.wait_for_function('window._recStarts>0')
 before=page.evaluate('window._recStarts')
 page.evaluate("window._activeRec.emit('hello there')")
 page.wait_for_function("window._utterances.some(t=>t.includes('I am ready to help'))",timeout=10000)
 page.wait_for_function(f'window._recStarts>{before}',timeout=10000)
 print('Hands-free recognized turn, spoken response, automatic re-listen PASS')
 # Speech begins on a complete sentence without needing finish().
 page.evaluate("window._streamTest=OraVoice.newTurn();window._streamTest.feed('This first sentence is ready. The rest is still streaming')")
 page.wait_for_function("window._utterances.some(t=>t.includes('This first sentence is ready'))")
 page.evaluate("window._streamTest.finish('This first sentence is ready. The rest is still streaming.')")
 print('Sentence speech before full completion PASS')
 page.evaluate("send('set a timer for 20 minutes')")
 page.wait_for_selector('#confirmAlarm')
 assert not created,'Alarm created before confirmation'
 page.click('#confirmAlarm');page.wait_for_function("document.querySelector('#alarmRows').textContent.includes('scheduled')")
 assert len(created)==1 and created[0]['confirmed']
 print('Phone alarm preview, explicit confirmation, scheduled status PASS (provider mocked)')
 page.click('#closeVoiceSheet');page.click('#hfStop');page.wait_for_function('!OraVoice.enabled')
 assert 'Microphone off' in page.locator('#hfState').inner_text()
 print('Stop releases conversation mode PASS')
 page.set_viewport_size({'width':390,'height':844})
 assert page.locator('#hfStart').is_visible()
 assert page.locator('#alarmToggle').is_visible()
 page.screenshot(path='/home/user/voice-mobile-check.png',full_page=False)
 print('390px controls visible PASS')
 assert not errors,errors
 browser.close()
