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
  let voiceLocked=false,abortReply=null,lastSpoken='';
  const normTxt=t=>String(t||'').toLowerCase().replace(/[^a-z0-9 ]+/g,' ').replace(/\s+/g,' ').trim();
  function isEcho(t){const n=normTxt(t);if(!n)return true;const s=normTxt(lastSpoken);if(!s)return false;const sw=new Set(s.split(' '));const tw=n.split(' ');let hit=0;for(const w of tw)if(sw.has(w))hit++;return tw.length>1&&hit/tw.length>=0.85;}
  function interruptTurn(){if(abortReply)abortReply();streaming=false;}
  const toolbar=document.createElement('div');
  toolbar.className='voicebar';
  toolbar.innerHTML=`<div class="row" style="flex-wrap:wrap;gap:7px">
    <button class="btn primary" id="hfStart">🎧 Start voice</button>
    <button class="btn ghost" id="hfStop" disabled>Stop / mute</button>
    <button class="btn ghost" id="hfInterrupt" disabled>Interrupt reply</button>
    <button class="btn ghost" id="alarmToggle">Voice settings</button>
    <span id="hfState" role="status" aria-live="polite">Microphone off</span>
    </div>
    <div class="hint">Start once, then speak after each reply—no repeated microphone taps. Listening pauses during replies to prevent echoes. Talk over the AI any time — like a real conversation, it stops, listens and answers what you just said. Browser/device support varies.</div>
    <details id="voiceAlarmPanel"><summary>Voice settings & privacy</summary>
      <div style="padding:10px;max-height:380px;overflow:auto">
        <p class="hint">Voice mode sends speech to your browser's recognition service, or short recordings to OraCool's configured transcription provider. OraCool does not save the audio clips. Provider retention terms apply. Stop / mute releases the microphone. A locked phone or hidden browser pauses voice mode.</p>
        <label>Language <select id="hfLanguage"><option value="en-NG">English (Nigeria)</option><option value="en-US">English (US)</option><option value="en-GB">English (UK)</option><option value="fr-FR">French</option><option value="es-ES">Spanish</option></select></label>
        <label style="display:block;margin:8px 0"><input type="checkbox" id="hfProactive"> Offer occasional spoken suggestions while conversation mode is on (quiet 10 PM–7 AM).</label>
      </div>
    </details>`;
  document.querySelector('.reactorhead').after(toolbar);
  const style=document.createElement('style');style.textContent='.voicebar{padding:8px 12px;border-bottom:1px solid var(--line);font-size:12px}.voicebar [hidden]{display:none!important}.voicebar details{margin-top:7px}.voicebar button{min-height:40px}.voicebar input,.voicebar select{max-width:100%}.voicebar input:not([type=checkbox]),.voicebar select,.voicebar textarea{background:#061723;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px;margin:4px 0}.voicebar input[type=checkbox]{accent-color:var(--cyan)}#hfState{font-size:11px;color:var(--cyan2)}.alarmitem{padding:8px 0;border-bottom:1px solid var(--line)}';document.head.appendChild(style);
  const sheet=document.createElement('dialog');sheet.id='voiceSheet';sheet.className='settings-sheet voicebar';
  sheet.setAttribute('aria-label','Voice settings');
  sheet.innerHTML='<div class="sheet-head"><div><span class="sheet-eyebrow">PERSONALIZE</span><h2>Voice settings</h2></div><button class="btn ghost" id="closeVoiceSheet" aria-label="Close voice settings">✕</button></div>';
  const details=toolbar.querySelector('#voiceAlarmPanel');sheet.appendChild(details);document.body.appendChild(sheet);
  toolbar.querySelector('.hint').remove();
  const voiceButton=document.createElement('button');voiceButton.className='btn ghost';voiceButton.id='commsToggle';voiceButton.textContent='↗ Communications';toolbar.querySelector('.row').insertBefore(voiceButton,toolbar.querySelector('#hfState'));
  window.OraSettings={open(){if(!sheet.open)sheet.showModal();details.open=true;},close(){sheet.close();details.open=false;}};
  document.querySelector('#closeVoiceSheet').onclick=()=>window.OraSettings.close();
  sheet.addEventListener('click',e=>{if(e.target===sheet&&e.clientX<sheet.getBoundingClientRect().left)window.OraSettings.close();});
  sheet.addEventListener('close',()=>{details.open=false;});
  const q=s=>document.querySelector(s);
  if(q('#alarmTimezone'))q('#alarmTimezone').value=Intl.DateTimeFormat().resolvedOptions().timeZone||'Africa/Lagos';
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
    if(!enabled||document.hidden||voiceLocked)return;
    if(capture)return; // a recognizer is already listening — barge-in is handled in its events
    if(!signedIn()){stop('Sign in to resume conversation');return;}
    state('Listening — speak naturally');listening=true;q('#micBtn').classList.add('listening');
    if(SR){
      const r=new SR();capture=r;r.lang=settings.voiceLanguage||'en-NG';r.continuous=true;r.interimResults=true;
      let finals='';
      r.onresult=e=>{
        if(capture!==r||!enabled)return;
        let interim='';
        for(let i=e.resultIndex;i<e.results.length;i++){
          if(e.results[i].isFinal)finals+=' '+e.results[i][0].transcript;else interim+=' '+e.results[i][0].transcript;
        }
        if(activeSpeech){ // user talking over the AI: stop speaking, take the floor
          const f=finals.trim();
          if(f&&!isEcho(f)&&(f.split(/\s+/).length>=2||f.length>6)){cancelSpeech();interruptTurn();accept(f);return;}
          if(normTxt(interim).split(' ').length>=4&&!isEcho(interim)){cancelSpeech();interruptTurn();}
          return;
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
        try{const r=await post('/api/voice/transcribe',{audio:data,mime:type});if(!enabled)return;if(r.error){stop(r.error);return;}if(r.text&&!isEcho(r.text))await accept(r.text);else resume();}
        catch(e){stop('Transcription unavailable — use text or retry');}
      };
      current.start();vadStarted=Date.now();vadLastSound=vadStarted;
      const samples=new Uint8Array(analyser.fftSize);
      vadInterval=setInterval(()=>{
        if(!analyser||current.state!=='recording')return;
        analyser.getByteTimeDomainData(samples);const rms=Math.sqrt(samples.reduce((n,v)=>n+((v-128)/128)**2,0)/samples.length);
        if(rms>0.018){heard=true;vadLastSound=Date.now();lastUserAt=Date.now();if(activeSpeech&&rms>0.045){cancelSpeech();interruptTurn();}}
        if((heard&&Date.now()-vadLastSound>1000)||Date.now()-vadStarted>12000){clearInterval(vadInterval);vadInterval=null;current.stop();}
      },100);
    }catch(e){releaseMedia();stop('Microphone permission or browser recording unavailable. Use text or retry.');}
  }
  function resume(delay=350){
    q('#hfInterrupt').disabled=!activeSpeech&&!streaming&&!busy;
    clearTimeout(restart);
    if(enabled&&!busy&&!speechQueue.length&&!streaming&&!document.hidden&&!voiceLocked)restart=setTimeout(listen,busy||activeSpeech?900:delay);
    else if(enabled)state(activeSpeech?'Speaking — just talk over me':busy||streaming?'Thinking…':'Conversation ready');
  }
  function pump(){
    if(activeSpeech||!speechQueue.length)return;
    if(!settings.ttsOn||!('speechSynthesis' in window)){speechQueue=[];resume();return;}
    activeSpeech=true;state('Speaking — just talk over me');
    const epoch=speechEpoch, text=speechQueue.shift();lastSpoken=text;
    const u=new SpeechSynthesisUtterance(text);u.lang=settings.voiceLanguage||'en-NG';u.rate=1.08;
    const chosen=voices.find(v=>v.name===settings.voiceName)||voices.find(v=>(v.lang||'').startsWith(u.lang.slice(0,2)));
    if(chosen)u.voice=chosen;
    let started=false,finished=false;
    u.onstart=()=>{started=true;if(epoch===speechEpoch){startWave();pauseWake();}};
    const finish=()=>{if(finished)return;finished=true;clearTimeout(wdStart);clearTimeout(wdEnd);clearInterval(keep);if(epoch!==speechEpoch)return;activeSpeech=false;if(speechQueue.length)pump();else{stopWave();if(!streaming)resumeWake();state(enabled?'Conversation ready':'Microphone off');resume();}};
    u.onend=finish;u.onerror=finish;
    // patch40: stuck-speech recovery — Chrome/Android sometimes never fires onstart/onend, which used to leave
    // activeSpeech=true forever: no more speech AND no more listening ("mute, won't respond"). Watchdogs + resume keep it alive.
    const wdStart=setTimeout(()=>{if(started||finished)return;try{speechSynthesis.cancel();}catch(e){}if(!u.__retried){u.__retried=true;finished=true;clearTimeout(wdEnd);clearInterval(keep);if(epoch===speechEpoch){activeSpeech=false;speechQueue.unshift(text);try{speechSynthesis.resume();}catch(e){}setTimeout(pump,200);}}else finish();},3500);
    const wdEnd=setTimeout(()=>{if(finished)return;try{speechSynthesis.cancel();}catch(e){}finish();},6000+text.length*95);
    const keep=setInterval(()=>{try{if(speechSynthesis.paused)speechSynthesis.resume();}catch(e){}},4000);
    try{speechSynthesis.speak(u);}catch(e){finish();}
  }
  function enqueue(text){const clean=speechClean(_cleanReply(text));if(!clean||!settings.ttsOn)return;
    if(clean.length<=220){speechQueue.push(clean);}else{let cur='';clean.split(/(?<=[.!?…])\s+|\n+/).forEach(p=>{p=p.trim();if(!p)return;if((cur+' '+p).length>200&&cur){speechQueue.push(cur);cur=p;}else cur=cur?cur+' '+p:p;});if(cur)speechQueue.push(cur);}
    pump();}
  window.OraVoice={
    get enabled(){return enabled;},
    pause:pauseCapture,resume,stop,setAbort(fn){abortReply=fn;},
    interrupt(){cancelSpeech();interruptTurn();resume();},
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
  q('#hfInterrupt').onclick=()=>{interruptTurn();cancelSpeech();state('Reply interrupted');resume();};
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

  function hasCommunications(){
    if(!signedIn()||session.blocked)return false;
    if(session.admin===true)return true;
    try{const p=JSON.parse(atob(proToken.split('.')[1].replace(/-/g,'+').replace(/_/g,'/')));return !p.trial&&p.tier==='enterprise'&&p.exp>Date.now()/1000&&String(p.sub||'').toLowerCase()===myEmail().toLowerCase();}catch(e){return false;}
  }
  window.OraCommunicationsAccess=hasCommunications;
})();
