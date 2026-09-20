"""Durable, consent-based one-shot phone alarms. One active scheduler instance only.
Twilio secrets stay server-side. Provider requests are not retried after ambiguity.
"""
import base64
import copy
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import hmac
import html
import json
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
TERMINAL = {"completed", "busy", "failed", "no-answer", "canceled", "cancelled", "missed", "unknown"}


def parse_schedule(text, zone, now=None):
    """Deterministic times only; never ask a model to invent a confirmed alarm."""
    now = time.time() if now is None else now
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return {"error": "Choose a valid timezone, such as Africa/Lagos."}
    t = str(text or "").strip().lower()
    if not re.search(r"\b(timer|alarm|remind|alert|wake|call me|count\s*down)\b", t):
        return {"error": "Try: set a timer for 20 minutes, or wake me at 6 AM."}
    if re.search(r"\b(every|daily|weekdays|weekly|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t):
        return {"error": "This version supports one-time alarms. Choose a specific next time, not a recurring schedule."}
    words = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,"eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,"seventeen":17,"eighteen":18,"nineteen":19,"twenty":20,"thirty":30,"forty":40,"fifty":50,"sixty":60}
    for a,n in list(words.items()):
        if n >= 20:
            for b,v in list(words.items()):
                if v < 10:
                    t = re.sub(r"\b"+a+r"[ -]+"+b+r"\b", str(n+v), t)
    for word,n in words.items():
        t = re.sub(r"\b"+word+r"\b",str(n),t)
    duration = re.search(r"\b(?:in|for|of)\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",t)
    if duration:
        if re.search(r"\b\d+(?:\.\d+)?\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", t[duration.end():]):
            return {"error":"Use a single duration, such as 90 minutes, so the countdown is unambiguous."}
        unit=duration[2]; factor=3600 if unit.startswith(('hour','hr')) else 60 if unit.startswith(('min',)) else 1
        delay=float(duration[1])*factor
        if not 60 <= delay <= 30*86400:
            return {"error":"Phone timers must be between one minute and 30 days. Carrier delivery is not exact to the second."}
        due=now+delay
        kind='timer'
    else:
        m=re.search(r"\b(?:at|when it is|when it's)\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b",t)
        if not m:
            return {"error":"Tell me the time clearly: ‘in 20 minutes’ or ‘at 6 AM’."}
        hour=int(m[1]);minute=int(m[2] or 0);suffix=(m[3] or '').replace('.','')
        if minute>59 or (suffix and not 1<=hour<=12) or (not suffix and (hour>23 or m[2] is None)):
            return {"error":"Use AM/PM (6 AM) or a 24-hour time (06:00)."}
        if suffix: hour=hour%12+(12 if suffix=='pm' else 0)
        local=datetime.fromtimestamp(now,tz)
        target=local.replace(hour=hour,minute=minute,second=0,microsecond=0)
        if 'today' in t and target.timestamp() <= now:return {'error':'That time has passed today. Ask for tomorrow or choose a later time.'}
        if 'tomorrow' in t or target.timestamp() <= now: target+=timedelta(days=1)
        # Reject ambiguous/nonexistent DST wall times instead of silently moving them.
        if target.replace(fold=0).utcoffset()!=target.replace(fold=1).utcoffset():
            return {"error":"That local time crosses a daylight-saving change. Choose another time or use a duration."}
        due=target.timestamp();kind='alarm'
        if due-now<60:return {"error":"Choose an alarm at least one minute from now."}
    return {"due_at":due,"timezone":zone,"local_time":datetime.fromtimestamp(due,tz).isoformat(),"kind":kind,
            "label":"Wake-up call" if re.search(r"\b(wake|sleep)\b",t) else "OraCool timer" if kind=='timer' else "OraCool alarm"}


def authorized_communications(method):
    @wraps(method)
    def guarded(self, owner, *args, **kwargs):
        if not self.allowed(owner):
            return {"error": "Phone calling and reminders require Enterprise or administrator access.", "plan": "enterprise", "locked": True}
        return method(self, owner, *args, **kwargs)
    return guarded


class Service:
    def __init__(self, get, put, key, cipher, notify=lambda *a:None, clock=time.time, claim=None, allowed=lambda owner:False):
        self.get,self.put,self.key,self.cipher,self.notify,self.clock=get,put,key,cipher,notify,clock
        self.lock=threading.RLock(); self.state=None
        self.claim=claim or (lambda ident:True);self.allowed=allowed
        self.last_tick=None;self.last_error=""

    def config(self):
        required=('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_FROM_NUMBER','TWILIO_VERIFY_SERVICE_SID','PUBLIC_BASE_URL')
        missing=[k for k in required if not self.key(k)]
        public=str(self.key('PUBLIC_BASE_URL') or '').rstrip('/')
        parsed=urllib.parse.urlsplit(public)
        if public and (parsed.scheme!='https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username):
            missing.append('PUBLIC_BASE_URL must be an HTTPS origin without a path')
        for k in ('REMINDER_CALLS_ENABLED','REMINDERS_ALWAYS_ON'):
            if str(self.key(k) or '').lower() not in ('true','1','yes'):missing.append(k)
        return {'ready':not missing,'missing':missing,'phone_required':True,'plan':'enterprise','last_tick':self.last_tick,'scheduler_error':self.last_error,
                'note':'Real calls and verification SMS incur provider charges. Requires one always-running server. Not for emergency or safety-critical alerts.'}

    def load(self):
        if self.state is None:
            d=self.get('reminders')
            if not isinstance(d,dict):raise ValueError('Reminder cloud storage unavailable. Check Supabase; no alarm has been scheduled.')
            self.state=d
        for name in ('phones','pending','alarms','limits'):self.state.setdefault(name,{})
        return self.state

    def save(self):
        if not self.put('reminders',self.state):
            self.state=None
            raise ValueError('Could not confirm durable storage. No new call will be dispatched. Refresh the alarm list before retrying.')

    def provider(self, path, fields):
        origin='https://verify.twilio.com' if path.startswith('/v2/') else 'https://api.twilio.com'
        auth=base64.b64encode((str(self.key('TWILIO_ACCOUNT_SID'))+':'+str(self.key('TWILIO_AUTH_TOKEN'))).encode()).decode()
        req=urllib.request.Request(origin+path,data=urllib.parse.urlencode(fields,doseq=True).encode(),method='POST',
                                   headers={'Authorization':'Basic '+auth,'Content-Type':'application/x-www-form-urlencoded'})
        try:
            with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
        except urllib.error.HTTPError as e:
            raise ValueError('Calling provider rejected the request (HTTP '+str(e.code)+'). Check account credit, permissions and number configuration.') from None
        except Exception:
            raise RuntimeError('Provider outcome is unknown after a network error. No automatic retry; check the provider console.') from None

    def mask(self,number):return number[:3]+'••••••'+number[-4:]

    def rate(self, bucket, limit, window):
        d=self.load(); now=self.clock()
        # Bound the rate-limit ledger; keys are account/phone hashes, never raw numbers.
        d['limits']={k:[v for v in vals if v>now-86400] for k,vals in d['limits'].items() if any(v>now-86400 for v in vals)}
        times=[x for x in d['limits'].get(bucket,[]) if x>now-window]
        if len(times)>=limit:raise ValueError('Verification limit reached. Please try again later.')
        d['limits'][bucket]=times+[now]

    @authorized_communications
    def send_code(self,owner,number,consent,ip=''):
        if not self.config()['ready']:return {'error':'Phone alarms are not configured by the operator yet.','config':self.config()}
        if consent is not True or not E164.fullmatch(number):return {'error':'Use your own number in international format (+234…) and agree to receive verification SMS and requested alarm calls.'}
        with self.lock:
            self.load()
            for kind,value,limit,window in [('owner',owner,3,3600),('number',number,3,3600),('ip',ip,5,3600),('global','all',30,86400)]:
                self.rate(kind+':'+hashlib.sha256(value.encode()).hexdigest(),limit,window)
            encrypted=self.cipher().encrypt(number.encode()).decode()
            self.save() # budget reservation before sending an SMS
            r=self.provider('/v2/Services/'+str(self.key('TWILIO_VERIFY_SERVICE_SID'))+'/Verifications',{'To':number,'Channel':'sms'})
            if not str(r.get('sid','')).startswith('VE'):return {'error':'Verification SMS was not accepted.'}
            self.state['pending'][owner]={'phone':encrypted,'sid':r['sid'],'created':self.clock(),'attempts':0}
            self.save()
            return {'ok':True,'mask':self.mask(number),'message':'Verification SMS requested. Enter its code; do not share it in chat.'}

    @authorized_communications
    def check_code(self,owner,code):
        if not re.fullmatch(r'\d{4,8}',code):return {'error':'Enter the numeric code from your SMS.'}
        with self.lock:
            d=self.load(); p=d['pending'].get(owner)
            if not p or self.clock()-p['created']>600 or p['attempts']>=5:return {'error':'Verification expired or attempts exhausted. Request a new code.'}
            p['attempts']+=1;self.save()
            r=self.provider('/v2/Services/'+str(self.key('TWILIO_VERIFY_SERVICE_SID'))+'/VerificationCheck',{'VerificationSid':p['sid'],'Code':code})
            number=self.cipher().decrypt(p['phone'].encode()).decode()
            if r.get('status')!='approved' or r.get('to')!=number:return {'error':'Incorrect or unapproved verification code.'}
            d['phones'][owner]={'phone':p['phone'],'mask':self.mask(number),'version':secrets.token_hex(8),'verified_at':self.clock(),'consent':True}
            d['pending'].pop(owner,None);self.save()
            return {'ok':True,'mask':self.mask(number)}

    @authorized_communications
    def listing(self,owner):
        with self.lock:
            d=self.load(); p=d['phones'].get(owner) or {}
            rows=[self.public(x) for x in d['alarms'].values() if x['owner']==owner]
            rows.sort(key=lambda x:x['due_at'],reverse=True)
            return {'config':self.config(),'phone':{'verified':bool(p.get('verified_at')),'mask':p.get('mask','')},'alarms':rows[:60]}

    def public(self,row):
        return {k:v for k,v in row.items() if k not in ('owner','request_id','phone_version','call_sid')}

    @authorized_communications
    def create(self,owner,body):
        if body.get('confirmed') is not True:return {'error':'Review the exact time, timezone and phone-call consent, then confirm.'}
        if not self.config()['ready']:return {'error':'Calling provider or always-on hosting is not configured. No alarm scheduled.'}
        with self.lock:
            d=self.load(); p=d['phones'].get(owner)
            if not p or not p.get('consent'):return {'error':'Verify your own phone in Voice & alarms first.'}
            req=str(body.get('request_id') or '')
            if not re.fullmatch(r'[a-zA-Z0-9-]{12,64}',req):return {'error':'Missing alarm request ID.'}
            for x in d['alarms'].values():
                if x['owner']==owner and x.get('request_id')==req:return {'ok':True,'alarm':self.public(x),'duplicate':True}
            try:
                due=float(body.get('due_at'));tz=ZoneInfo(str(body.get('timezone') or ''))
            except (TypeError,ValueError,ZoneInfoNotFoundError):return {'error':'Invalid time or timezone.'}
            if not self.clock()+30<=due<=self.clock()+30*86400:return {'error':'Alarm must be between 30 seconds and 30 days from now. Preview again.'}
            owned=[x for x in d['alarms'].values() if x['owner']==owner]
            if len([x for x in owned if x['status']=='scheduled'])>=10:return {'error':'At most 10 pending phone alarms per account.'}
            day=datetime.fromtimestamp(due,timezone.utc).date()
            matching=[x for x in d['alarms'].values() if datetime.fromtimestamp(x['due_at'],timezone.utc).date()==day and x['status'] not in ('cancelled','canceled')]
            if len(matching)>=100 or sum(x['owner']==owner for x in matching)>=5:return {'error':'Daily phone-call limit reached (5 per account, 100 platform-wide).'}
            ident=secrets.token_hex(12)
            d['alarms']={k:v for k,v in d['alarms'].items() if v['due_at']>self.clock()-90*86400 or v['status'] not in TERMINAL}
            row={'id':ident,'owner':owner,'due_at':due,'timezone':str(tz),'local_time':datetime.fromtimestamp(due,tz).isoformat(),
                 'kind':'alarm' if body.get('kind')=='alarm' else 'timer','label':str(body.get('label') or 'OraCool alarm')[:80],
                 'status':'scheduled','created_at':self.clock(),'request_id':req,'phone_version':p['version']}
            d['alarms'][ident]=row;self.save()
            return {'ok':True,'alarm':self.public(row),'message':'Phone alarm saved. Delivery depends on server, provider, carrier and your phone settings.'}

    @authorized_communications
    def cancel(self,owner,ident):
        with self.lock:
            row=self.load()['alarms'].get(ident)
            if not row or row['owner']!=owner:return {'error':'Alarm not found.'}
            if row['status']!='scheduled':return {'error':'The alarm is no longer pending; a dispatched telephone call cannot be recalled here.'}
            row['status']='cancelled';self.save();return {'ok':True}

    @authorized_communications
    def disconnect(self,owner):
        with self.lock:
            d=self.load();d['phones'].pop(owner,None);d['pending'].pop(owner,None)
            for x in d['alarms'].values():
                if x['owner']==owner and x['status']=='scheduled':x['status']='cancelled'
            self.save();return {'ok':True,'message':'Phone removed and pending calls cancelled. Already dispatched calls may still ring.'}

    def tick(self):
        with self.lock:
            d=self.load();now=self.clock()
            # Revoke queued work immediately after role removal, even while
            # provider delivery is disabled. Dispatched calls cannot be undone here.
            revoked=False
            for row in d['alarms'].values():
                if row['status']=='scheduled' and not self.allowed(row['owner']):
                    row['status']='cancelled';row['note']='Administrator access is required for phone reminders.';revoked=True
            if revoked:self.save()
            if not self.config()['ready']:return
            for row in list(d['alarms'].values()):
                if row['status']=='dispatching' and now-row.get('dispatch_at',now)>120:
                    row['status']='unknown';row['note']='Interrupted dispatch; check provider before scheduling again.';self.save()
                if row['status']!='scheduled' or row['due_at']>now:continue
                if now-row['due_at']>300:
                    row['status']='missed';row['note']='Server was too late. No unexpected catch-up call placed.';self.save();continue
                p=d['phones'].get(row['owner'])
                if not p or not p.get('consent') or p.get('version')!=row['phone_version'] or not self.allowed(row['owner']):
                    row['status']='cancelled';self.save();continue
                number=self.cipher().decrypt(p['phone'].encode()).decode()
                if not self.claim(row['id']):
                    row['status']='unknown';row['note']='Dispatch already claimed or claim storage unavailable. No duplicate call attempted.';self.save();continue
                row['status']='dispatching';row['dispatch_at']=now;self.save()
                local=datetime.fromtimestamp(row['due_at'],ZoneInfo(row['timezone']))
                greeting='Good morning.' if 4<=local.hour<12 else 'Hello.'
                # No arbitrary URLs, scripts or user text in TwiML; no call recording.
                words=greeting+' This is your requested OraCool '+('wake-up alarm.' if row['kind']=='alarm' else 'timer. Your countdown has finished.')
                words+=' Take a moment to get ready. Open OraCool if you would like help planning your day. Have a good day.'
                fields={'To':number,'From':self.key('TWILIO_FROM_NUMBER'),'Twiml':'<Response><Say>'+html.escape(words)+'</Say><Pause length="1"/><Say>Your OraCool reminder is complete. Goodbye.</Say><Hangup/></Response>',
                        'Timeout':'30','TimeLimit':'60','StatusCallback':str(self.key('PUBLIC_BASE_URL')).rstrip('/')+'/api/reminders/callback/'+row['id'],
                        'StatusCallbackEvent':['initiated','ringing','answered','completed'],'StatusCallbackMethod':'POST'}
                try:
                    r=self.provider('/2010-04-01/Accounts/'+str(self.key('TWILIO_ACCOUNT_SID'))+'/Calls.json',fields)
                    if not str(r.get('sid','')).startswith('CA'):raise RuntimeError('No confirmed call identifier.')
                    row['call_sid']=r['sid'];row['status']=str(r.get('status') or 'queued');row['note']='Call accepted by provider, not proof you heard it.'
                except ValueError as e:row['status']='failed';row['note']=str(e)
                except Exception:row['status']='unknown';row['note']='Call outcome unknown; not automatically retried.'
                self.save()

    def callback(self,ident,params,signature):
        url=str(self.key('PUBLIC_BASE_URL') or '').rstrip('/')+'/api/reminders/callback/'+ident
        payload=url+''.join(k+str(params[k]) for k in sorted(params))
        expected=base64.b64encode(hmac.new(str(self.key('TWILIO_AUTH_TOKEN') or '').encode(),payload.encode(),hashlib.sha1).digest()).decode()
        if not self.key('TWILIO_AUTH_TOKEN') or not hmac.compare_digest(expected,signature or ''):return False
        if params.get('AccountSid')!=self.key('TWILIO_ACCOUNT_SID'):return False
        with self.lock:
            row=self.load()['alarms'].get(ident)
            if not row:return False
            sid=params.get('CallSid','')
            if not sid.startswith('CA') or (row.get('call_sid') and sid!=row['call_sid']):return False
            status=params.get('CallStatus')
            if status not in {'queued','initiated','ringing','in-progress','completed','busy','failed','no-answer','canceled'}:return False
            if row['status'] in TERMINAL and row['status']!='unknown':return True
            try:seq=int(params.get('SequenceNumber','0'))
            except ValueError:return False
            if seq<row.get('callback_seq',-1):return True
            row.update(call_sid=sid,status=status,callback_seq=seq,callback_at=self.clock())
            self.save();return True

    def run(self):
        while True:
            try:
                self.tick();self.last_tick=self.clock();self.last_error=""
            except Exception:
                self.last_error="Scheduler/storage request failed; check provider configuration and Supabase connectivity."
                # Never log raw credentials, numbers or callback bodies.
            time.sleep(5)
