/* OraCool opt-in voice turns and consent-based telephone alarms. No hidden recording. */
(() => {
  'use strict';
  class SentenceBuffer {
    constructor(){ this.position=0; }
    take(text, final=false){
      const parts=[];
      while(this.position<text.length){
        const rest=text.slice(this.position);
        const boundary=rest.match(/[.!?](?:\s+|$)|\n+/);
        let end=boundary?boundary.index+boundary[0].length:0;
        if(!end&&rest.length>180){const at=rest.lastIndexOf(' ',160);if(at>60)end=at;}
        if(!end){if(final)end=rest.length;else break;}
        // Avoid reading a partial fenced code block aloud.
        if(!final&&(text.slice(0,this.position+end).match(/```/g)||[]).length%2)break;
        parts.push(rest.slice(0,end));this.position+=end;
      }
      return parts;
    }
  }
  window.OraSentenceBuffer=SentenceBuffer;
  let enabled=false, capture=null, restart=null, errors=0, stream=null, recorder=null;
  let audioContext=null, analyser=null, vadInterval=null, vadStarted=0, vadLastSound=0;
  let speechQueue=[], activeSpeech=false, speechEpoch=0, streaming=false, oneShot=false;
  let submitted='', submittedAt=0, transcriptTimer=null, lastSuggestion=0;
  let voiceLocked=false,abortReply=null;
  const toolbar=document.createElement('div');
  toolbar.className='voicebar';
  toolbar.innerHTML=`<div class="row" style="flex-wrap:wrap;gap:7px">
    <button class="btn primary" id="hfStart">🎧 Start conversation</button>
    <button class="btn ghost" id="hfStop" disabled>Stop / mute</button>
    <button class="btn ghost" id="hfInterrupt" disabled>Interrupt reply</button>
    <button class="btn ghost" id="alarmToggle">Voice settings</button>
    <span id="hfState" role="status" aria-live="polite">Microphone off</span>
    </div>
    <div class="hint">Start once, then speak after each reply—no repeated microphone taps. Listening pauses during replies to prevent echoes. Keep this page visible. Browser/device support varies.</div>
    <details id="voiceAlarmPanel"><summary>Voice settings & privacy</summary>
      <div style="padding:10px;max-height:380px;overflow:auto">
        <p class="hint">Voice mode sends speech to your browser's recognition service, or short recordings to OraCool's configured transcription provider. OraCool does not save the audio clips. Provider retention terms apply. Stop / mute releases the microphone. A locked phone or hidden browser pauses voice mode.</p>
        <label>Language <select id="hfLanguage"><option value="en-NG">English (Nigeria)</option><option value="en-US">English (US)</option><option value="en-GB">English (UK)</option><option value="fr-FR">French</option><option value="es-ES">Spanish</option></select></label>
        <label style="display:block;margin:8px 0"><input type="checkbox" id="hfProactive"> Offer occasional spoken suggestions while conversation mode is on (quiet 10 PM–7 AM).</label>
        <section id="adminAlarmTools" hidden>
        <h4>Admin-only phone calls & reminders</h4>
        <div class="hint">Verify your own number. Only alarms you review and confirm will call it. SMS verification and calls use provider credit. Requires an always-running server and phone reception; Do Not Disturb/carrier filtering may silence calls. Not an emergency alarm service.</div>
        <div id="alarmStatus" class="result">Open this panel to check phone-call configuration.</div>
        <input id="alarmPhone" type="tel" autocomplete="tel" placeholder="Your own number, e.g. +234…">
        <label style="display:block;margin:6px 0"><input type="checkbox" id="alarmConsent"> I control this number and consent to a verification SMS and the alarm calls I explicitly schedule.</label>
        <div class="row"><button class="btn ghost" id="alarmSendCode">Send verification SMS</button><input id="alarmCode" inputmode="numeric" autocomplete="one-time-code" placeholder="SMS code" style="width:120px"><button class="btn ghost" id="alarmVerify">Verify number</button></div>
        <label style="display:block;margin-top:8px">Alarm timezone <input id="alarmTimezone" placeholder="Africa/Lagos"></label>
        <div class="row"><input id="alarmCommand" placeholder="Wake me at 6 AM / set a timer for 20 minutes" style="flex:1;min-width:180px"><button class="btn primary" id="alarmPreview">Review alarm</button></div>
        <div id="alarmConfirm"></div>
        <div class="row" style="margin-top:8px"><button class="btn ghost" id="alarmRefresh">Refresh alarms</button><button class="btn ghost" id="alarmDisconnect">Disconnect phone / cancel pending calls</button></div>
        <div id="alarmRows"></div>
        </section>
      </div>
    </details>`;
  document.querySelector('.reactorhead').after(toolbar);
  const style=document.createElement('style');style.textContent='.voicebar{padding:8px 12px;border-bottom:1px solid var(--line);font-size:12px}.voicebar [hidden]{display:none!important}.voicebar details{margin-top:7px}.voicebar button{min-height:40px}.voicebar input,.voicebar select{max-width:100%}.voicebar input:not([type=checkbox]),.voicebar select,.voicebar textarea{background:#061723;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px;margin:4px 0}.voicebar input[type=checkbox]{accent-color:var(--cyan)}#hfState{font-size:11px;color:var(--cyan2)}.alarmitem{padding:8px 0;border-bottom:1px solid var(--line)}';document.head.appendChild(style);
  const q=s=>document.querySelector(s);
  q('#alarmTimezone').value=Intl.DateTimeFormat().resolvedOptions().timeZone||'Africa/Lagos';
  q('#hfLanguage').value=settings.voiceLanguage||'en-NG';
  q('#hfProactive').checked=settings.proactiveConsent===true;
  q('#hfProactive').onchange=()=>{settings.proactiveConsent=q('#hfProactive').checked;settings.proactive=settings.proactiveConsent;saveSettings();applyProactive();lastSuggestion=Date.now();};
  q('#hfLanguage').onchange=()=>{settings.voiceLanguage=q('#hfLanguage').value;saveSettings();if(enabled){pauseCapture();resume();}};
  function state(text){q('#hfState').textContent=text;q('#hfStart').disabled=enabled;q('#hfStop').disabled=!enabled&&!activeSpeech;q('#hfInterrupt').disabled=!activeSpeech&&!streaming&&!busy;}
  function signedIn(){return !!(session&&session.access_token&&myEmail());}
  function releaseMedia(){
    if(vadInterval)clearInterval(vadInterval);vadInterval=null;
    if(recorder&&recorder.state!=='inactive'){recorder.onstop=null;try{recorder.stop();}catch(e){}}recorder=null;
    if(stream)stream.getTracks().forEach(t=>t.stop());stream=null;
    if(audioContext){audioContext.close().catch(()=>{});audioContext=null;}analyser=null;
  }
  function pauseCapture(){
    clearTimeout(restart);clearTimeout(transcriptTimer);restart=null;
    if(capture){const c=capture;capture=null;c.onend=null;c.onresult=null;try{c.abort();}catch(e){}}
    releaseMedia();listening=false;q('#micBtn').classList.remove('listening');
  }
  function cancelSpeech(){speechEpoch++;speechQueue=[];activeSpeech=false;try{speechSynthesis.cancel();}catch(e){}stopWave();}
  function stop(reason='Microphone off'){
    enabled=false;oneShot=false;streaming=false;pauseCapture();cancelSpeech();state(reason);
  }
  async function start(single=false){
    if(!signedIn()){addErr('Sign in before starting a voice conversation.');return;}
    if(!window.isSecureContext){addErr('Voice needs HTTPS or localhost. Open OraCool in its full secure browser tab.');return;}
    setWakeArmed(false);if(rec){rec.onresult=null;rec.onend=null;try{rec.abort();}catch(e){}}
    pauseCapture();cancelSpeech();enabled=true;oneShot=single;errors=0;voiceLocked=false;
    settings.ttsOn=true;settings.voiceLanguage=q('#hfLanguage').value;saveSettings();
    lastActAt=Date.now();lastSuggestion=Date.now();state('Starting microphone…');
    await listen();
  }
  async function accept(text){
    text=text.trim();if(!text||!enabled)return;
    if(/^(stop listening|stop conversation|mute yourself|stop voice mode)[.!]?$/i.test(text)){stop();return;}
    if(text===submitted&&Date.now()-submittedAt<1800)return;
    submitted=text;submittedAt=Date.now();lastActAt=Date.now();
    pauseCapture();state('Thinking…');q('#input').value=text;
    if(oneShot){enabled=false;oneShot=false;}
    try{await send(text);}finally{resume();}
  }
  async function listen(){
    if(!enabled||busy||activeSpeech||streaming||document.hidden||voiceLocked)return;
    if(!signedIn()){stop('Sign in to resume conversation');return;}
    state('Listening — speak naturally');listening=true;q('#micBtn').classList.add('listening');
    if(SR){
      const r=new SR();capture=r;r.lang=settings.voiceLanguage||'en-NG';r.continuous=true;r.interimResults=true;
      let finals='';
      r.onresult=e=>{
        if(capture!==r||!enabled||activeSpeech)return;
        let interim='';
        for(let i=e.resultIndex;i<e.results.length;i++){
          if(e.results[i].isFinal)finals+=' '+e.results[i][0].transcript;else interim+=' '+e.results[i][0].transcript;
        }
        q('#input').value=(finals+' '+interim).trim();
        clearTimeout(transcriptTimer);
        if(finals.trim()&&!interim.trim())transcriptTimer=setTimeout(()=>accept(finals),650);
      };
      r.onend=()=>{if(capture!==r)return;capture=null;listening=false;if(finals.trim())accept(finals);else resume(600);};
      r.onerror=e=>{
        if(capture!==r)return;
        if(['not-allowed','service-not-allowed','audio-capture'].includes(e.error)){stop('Microphone unavailable — check browser permissions');return;}
        if(e.error!=='no-speech'&&e.error!=='aborted')errors++;
        if(errors>=3){stop('Speech service unavailable — try a different browser or text');}
      };
      try{r.start();}catch(e){capture=null;stop('Could not start recognition. Try again in the full browser tab.');}
      return;
    }
    // MediaRecorder + silence detection fallback for browsers without Web Speech.
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){stop('This browser does not support voice input. Use text or a supported browser.');return;}
    try{
      const acquired=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      if(!enabled||document.hidden||busy){acquired.getTracks().forEach(t=>t.stop());return;}
      stream=acquired;audioContext=new (window.AudioContext||window.webkitAudioContext)();await audioContext.resume();
      const source=audioContext.createMediaStreamSource(stream);analyser=audioContext.createAnalyser();analyser.fftSize=1024;source.connect(analyser);
      const mime=['audio/webm;codecs=opus','audio/mp4','audio/ogg;codecs=opus'].find(x=>MediaRecorder.isTypeSupported(x));
      recorder=new MediaRecorder(stream,mime?{mimeType:mime}:{});
      const pieces=[];const current=recorder;let heard=false;
      current.ondataavailable=e=>{if(e.data.size)pieces.push(e.data);};
      current.onstop=async()=>{
        const type=current.mimeType||mime||'audio/webm';releaseMedia();listening=false;
        if(!enabled||!heard){resume(500);return;}
        state('Transcribing…');
        const blob=new Blob(pieces,{type});
        const data=await new Promise(resolve=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.readAsDataURL(blob);});
        try{const r=await post('/api/voice/transcribe',{audio:data,mime:type});if(!enabled)return;if(r.error){stop(r.error);return;}if(r.text)await accept(r.text);else resume();}
        catch(e){stop('Transcription unavailable — use text or retry');}
      };
      current.start();vadStarted=Date.now();vadLastSound=vadStarted;
      const samples=new Uint8Array(analyser.fftSize);
      vadInterval=setInterval(()=>{
        if(!analyser||current.state!=='recording')return;
        analyser.getByteTimeDomainData(samples);const rms=Math.sqrt(samples.reduce((n,v)=>n+((v-128)/128)**2,0)/samples.length);
        if(rms>0.018){heard=true;vadLastSound=Date.now();lastUserAt=Date.now();}
        if((heard&&Date.now()-vadLastSound>1000)||Date.now()-vadStarted>12000){clearInterval(vadInterval);vadInterval=null;current.stop();}
      },100);
    }catch(e){releaseMedia();stop('Microphone permission or browser recording unavailable. Use text or retry.');}
  }
  function resume(delay=350){
    q('#hfInterrupt').disabled=!activeSpeech&&!streaming&&!busy;
    clearTimeout(restart);
    if(enabled&&!busy&&!activeSpeech&&!speechQueue.length&&!streaming&&!document.hidden&&!voiceLocked)restart=setTimeout(listen,delay);
    else if(enabled)state(activeSpeech?'Speaking — Interrupt to respond':busy||streaming?'Thinking…':'Conversation paused');
  }
  function pump(){
    if(activeSpeech||!speechQueue.length)return;
    if(!settings.ttsOn||!('speechSynthesis' in window)){speechQueue=[];resume();return;}
    pauseCapture();activeSpeech=true;state('Speaking — Interrupt to respond');
    const epoch=speechEpoch, text=speechQueue.shift();
    const u=new SpeechSynthesisUtterance(text);u.lang=settings.voiceLanguage||'en-NG';u.rate=1.08;
    const chosen=voices.find(v=>v.name===settings.voiceName)||voices.find(v=>(v.lang||'').startsWith(u.lang.slice(0,2)));
    if(chosen)u.voice=chosen;
    u.onstart=()=>{if(epoch===speechEpoch){startWave();pauseWake();}};
    const finish=()=>{if(epoch!==speechEpoch)return;activeSpeech=false;if(speechQueue.length)pump();else{stopWave();if(!streaming)resumeWake();state(enabled?'Conversation ready':'Microphone off');resume();}};
    u.onend=finish;u.onerror=finish;
    speechSynthesis.speak(u);
  }
  function enqueue(text){const clean=speechClean(_cleanReply(text));if(clean&&settings.ttsOn){speechQueue.push(clean);pump();}}
  window.OraVoice={
    get enabled(){return enabled;},
    pause:pauseCapture,resume,stop,setAbort(fn){abortReply=fn;},
    say(text){cancelSpeech();enqueue(text);},
    newTurn(){
      pauseCapture();cancelSpeech();streaming=true;state('Thinking…');
      const epoch=speechEpoch,buffer=new SentenceBuffer();
      const acknowledgement=setTimeout(()=>{if(epoch===speechEpoch&&enabled&&buffer.position===0)enqueue("I am on it.");},1300);
      return {feed(text){clearTimeout(acknowledgement);if(epoch!==speechEpoch)return;buffer.take(text).forEach(enqueue);},
        finish(text){clearTimeout(acknowledgement);if(epoch!==speechEpoch)return;buffer.take(text,true).forEach(enqueue);streaming=false;if(!activeSpeech&&!speechQueue.length)resumeWake();resume();},
        cancel(){clearTimeout(acknowledgement);if(epoch===speechEpoch){streaming=false;cancelSpeech();resume();}}};
    }
  };
  q('#hfStart').onclick=()=>start();q('#hfStop').onclick=()=>stop();
  q('#hfInterrupt').onclick=()=>{if(abortReply)abortReply();cancelSpeech();streaming=false;state('Reply interrupted');resume();};
  q('#micBtn').onclick=()=>enabled?stop():start(true);
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&enabled)stop('Paused when app was hidden. Tap Start to resume.');});
  // Lock/logout must release the microphone; merely hiding the panel is not consent.
  setInterval(()=>{if(enabled&&(!signedIn()||q('#authGate')?.classList.contains('open')||q('#lockScreen')?.classList.contains('open')))stop('Microphone off — sign in and tap Start to resume');},1000);
  setInterval(()=>{
    const h=new Date().getHours();
    if(!enabled||!settings.proactiveConsent||busy||activeSpeech||streaming||document.hidden||h<7||h>=22)return;
    if(Date.now()-lastUserAt<90000||Date.now()-lastSuggestion<180000||q('#input').value.trim())return;
    lastSuggestion=Date.now();const text=proactivePick();addAI(text);window.OraVoice.say(text);
  },10000);

  let pending=null, alarmOwner='';
  function isAlarmAdmin(){return signedIn()&&session.admin===true;}
  function updateAlarmAccess(){
    const admin=isAlarmAdmin(), owner=admin?myEmail():'';
    q('#adminAlarmTools').hidden=!admin;
    q('#alarmToggle').textContent=admin?'☎ Voice & admin alarms':'Voice settings';
    q('#voiceAlarmPanel summary').textContent=admin?'Voice privacy, admin phone setup & scheduled alarms':'Voice settings & privacy';
    if(owner!==alarmOwner){
      pending=null;q('#alarmConfirm').innerHTML='';q('#alarmRows').innerHTML='';
      q('#alarmPhone').value='';q('#alarmCode').value='';q('#alarmConsent').checked=false;
      q('#alarmStatus').textContent=admin?'Open this panel to check your admin phone setup.':'';
      alarmOwner=owner;
    }
  }
  setInterval(updateAlarmAccess,500);
  updateAlarmAccess();
  function alarmMessage(message,error=false){q('#alarmStatus').className=error?'errbox':'okbox';q('#alarmStatus').textContent=message;}
  async function refreshAlarms(){
    if(!isAlarmAdmin()){updateAlarmAccess();return null;}
    const owner=myEmail();
    try{
      const r=await post('/api/reminders/state',{});
      if(!isAlarmAdmin()||myEmail()!==owner)return null;
      if(r.error){alarmMessage(r.error,true);return null;}
      const cfg=r.config||{},phone=r.phone||{};
      alarmMessage(!cfg.ready?'Phone calls are not enabled yet. Operator setup needed: '+(cfg.missing||[]).join(', '):phone.verified?'Verified number: '+phone.mask+'. Review and confirm each phone alarm.':'Calling provider configured. Verify your own phone number to continue.',!cfg.ready);
      q('#alarmSendCode').disabled=!cfg.ready;q('#alarmVerify').disabled=!cfg.ready;
      q('#alarmRows').innerHTML=(r.alarms||[]).map(a=>`<div class="alarmitem"><b>${escapeHtml(a.label)}</b> · ${escapeHtml(a.local_time)} (${escapeHtml(a.timezone)})<br>Status: <b>${escapeHtml(a.status)}</b>${a.note?' · '+escapeHtml(a.note):''}${a.status==='scheduled'?` <button class="btn ghost cancelAlarm" data-id="${escapeHtml(a.id)}">Cancel</button>`:''}</div>`).join('')||'<div class="hint">No phone alarms saved. Try “set a timer for 20 minutes”.</div>';
      q('#alarmRows').querySelectorAll('.cancelAlarm').forEach(b=>b.onclick=async()=>{const r=await post('/api/reminders/cancel',{id:b.dataset.id});if(r.error)alarmMessage(r.error,true);else refreshAlarms();});
      return r;
    }catch(e){alarmMessage('Cannot reach alarm storage. No new alarm has been scheduled.',true);return null;}
  }
  function reply(text){addAI(text);speak(text);saveChat();}
  async function prepare(text,chatReply=true){
    if(!isAlarmAdmin()){if(chatReply)reply('Phone calls and reminders are available only to administrators. Normal voice conversation is still available.');return;}
    const state=await refreshAlarms();
    if(!state?.config?.ready||!state?.phone?.verified){
      q('#voiceAlarmPanel').open=true;
      if(chatReply)reply('No alarm has been scheduled. Open Voice & alarms to configure calling and verify your own phone number first.');
      return;
    }
    const r=await post('/api/reminders/preview',{text,timezone:q('#alarmTimezone').value.trim()});
    if(r.error){alarmMessage(r.error,true);if(chatReply)reply(r.error);return;}
    pending={...r,request_id:crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+'-'+Math.random().toString(36).slice(2),expires:Date.now()+120000};
    const message='Call '+state.phone.mask+' at '+r.local_time+' in '+r.timezone+'? This is a real telephone call using provider credit. Say “confirm alarm” or “cancel alarm”.';
    q('#alarmConfirm').innerHTML='<div class="okbox">'+escapeHtml(message)+'</div><div class="row"><button class="btn primary" id="confirmAlarm">Confirm phone alarm</button><button class="btn ghost" id="declineAlarm">Cancel</button></div>';
    q('#confirmAlarm').onclick=()=>confirmAlarm();q('#declineAlarm').onclick=()=>{pending=null;q('#alarmConfirm').innerHTML='';reply('Alarm cancelled before scheduling.');};
    q('#voiceAlarmPanel').open=true;if(chatReply)reply(message);
  }
  let confirming=false;
  async function confirmAlarm(){
    if(!isAlarmAdmin()){pending=null;updateAlarmAccess();reply('Phone reminders require administrator access.');return;}
    if(confirming)return;
    if(!pending||pending.expires<Date.now()){pending=null;q('#alarmConfirm').innerHTML='';reply('That preview expired. Please ask for the timer or alarm again.');return;}
    confirming=true;
    try{
      const r=await post('/api/reminders/create',{...pending,confirmed:true});
      if(r.error){reply(r.error);return;}
      pending=null;q('#alarmConfirm').innerHTML='';reply('Phone alarm saved for '+r.alarm.local_time+' in '+r.alarm.timezone+'. When you answer, OraCool will greet you. Delivery depends on the server, phone provider and your phone settings.');await refreshAlarms();
    }catch(e){reply('The request outcome is uncertain. Refresh the alarm list before scheduling again.');}
    finally{confirming=false;}
  }
  window.OraReminders={async handle(text){
    if(!isAlarmAdmin()&&(/\b(?:timers?|alarms?|reminders?|wake me|call me|count\s*down|scheduled calls)\b/i.test(text)||/\b(?:remind|alert) me\b/i.test(text))){
      addUser(text);reply('Phone calling and reminders are restricted to administrators. You can still use hands-free voice conversation.');return true;
    }
    if(pending&&/^(?:yes|yes please|confirm(?: alarm)?|confirm phone alarm)[.!]?$/i.test(text.trim())){addUser(text);await confirmAlarm();return true;}
    if(pending&&/^(?:no|cancel(?: alarm)?|never mind)[.!]?$/i.test(text.trim())){pending=null;q('#alarmConfirm').innerHTML='';addUser(text);reply('No phone alarm scheduled.');return true;}
    if(/\b(?:my alarms|my timers|list alarms|show alarms|scheduled calls)\b/i.test(text)){addUser(text);q('#voiceAlarmPanel').open=true;await refreshAlarms();reply('Your phone alarms and their delivery status are in Voice & alarms.');return true;}
    if(/\b(?:set|start|create|remind|alert|wake|call me|count\s*down)\b/i.test(text)&&/\b(?:timer|alarm|minutes?|mins?|seconds?|hours?|wake|call me|at|when it is|when it's)\b/i.test(text)){
      addUser(text);await prepare(text);return true;
    }
    return false;
  }};
  q('#alarmToggle').onclick=()=>{q('#voiceAlarmPanel').open=!q('#voiceAlarmPanel').open;};
  q('#voiceAlarmPanel').addEventListener('toggle',()=>{if(q('#voiceAlarmPanel').open&&isAlarmAdmin())refreshAlarms();});
  q('#alarmRefresh').onclick=refreshAlarms;
  q('#alarmPreview').onclick=()=>prepare(q('#alarmCommand').value,true);
  q('#alarmSendCode').onclick=async()=>{
    q('#alarmSendCode').disabled=true;
    try{const r=await post('/api/reminders/phone/send',{number:q('#alarmPhone').value.trim(),consent:q('#alarmConsent').checked});alarmMessage(r.error||r.message,!!r.error);}
    catch(e){alarmMessage('SMS request could not be confirmed. Wait before retrying.',true);}finally{q('#alarmSendCode').disabled=false;}
  };
  q('#alarmVerify').onclick=async()=>{
    const code=q('#alarmCode').value.trim();q('#alarmCode').value='';
    try{const r=await post('/api/reminders/phone/verify',{code});if(r.error)alarmMessage(r.error,true);else{q('#alarmPhone').value='';await refreshAlarms();}}
    catch(e){alarmMessage('Verification unavailable. Retry shortly.',true);}
  };
  q('#alarmDisconnect').onclick=async()=>{if(!confirm('Remove your phone and cancel all pending calls?'))return;const r=await post('/api/reminders/disconnect',{});if(r.error)alarmMessage(r.error,true);else refreshAlarms();};
  setInterval(()=>{if(q('#voiceAlarmPanel').open&&isAlarmAdmin()&&!document.hidden)refreshAlarms();},20000);
})();
