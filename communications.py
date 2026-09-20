"""Single-recipient, consent-based outbound communications. No bulk sends or retries.
One active server; encrypted destinations/messages; durable dispatch reservations.
Provider acceptance does not prove delivery or that a telephone message was heard.
"""
import base64
import copy
import hashlib
import hmac
import html
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import wraps
from reminders import E164, twilio_post


def normalize(channel, destination, country='international'):
    text = str(destination or '').strip()
    if channel == 'email':
        if len(text)>254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', text):
            raise ValueError('Enter a valid email address.')
        return text.lower()
    if channel not in ('call','sms'):
        raise ValueError('Choose call, SMS or email.')
    text = re.sub(r'[\s().-]', '', text)
    if country == 'NG' and re.fullmatch(r'0[789]\d{9}', text):
        text = '+234'+text[1:]
    if not E164.fullmatch(text):
        raise ValueError('Use international format (+234…), or choose Nigeria for a Nigerian local number.')
    return text


def eligible(fn):
    @wraps(fn)
    def wrapped(self, owner, *args, **kwargs):
        if not self.allowed(owner):
            return {'error':'Communications require Enterprise or administrator access.', 'plan':'enterprise','locked':True}
        return fn(self, owner, *args, **kwargs)
    return wrapped


class Service:
    def __init__(self, get, put, key, cipher, allowed, claim, clock=time.time):
        self.get,self.put,self.key,self.cipher,self.allowed,self.claim,self.clock=get,put,key,cipher,allowed,claim,clock
        self.lock=threading.RLock();self.state=None

    def config(self, channel):
        required = ['SENDGRID_API_KEY','SENDGRID_FROM_EMAIL','SENDGRID_ENABLED'] if channel=='email' else ['TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_FROM_NUMBER','TWILIO_VERIFY_SERVICE_SID','PUBLIC_BASE_URL','COMMUNICATIONS_ENABLED']
        missing=[k for k in required if (str(self.key(k) or '').lower() not in ('true','yes','1') if k.endswith('_ENABLED') else not self.key(k))]
        public=str(self.key('PUBLIC_BASE_URL') or '').rstrip('/')
        if channel!='email' and public:
            p=urllib.parse.urlsplit(public)
            if p.scheme!='https' or not p.hostname or p.path or p.query or p.fragment or p.username:
                missing.append('PUBLIC_BASE_URL must be an HTTPS origin')
        return {'ready':not missing,'missing':missing,'note':'Requires an authorized sender, recipient consent and available provider credit. Single-recipient transactional messages only.'}

    def load(self):
        if self.state is None:
            d=self.get('communications')
            if not isinstance(d,dict):raise ValueError('Durable communications storage unavailable. Nothing has been sent.')
            self.state=d
        for name in ('contacts','pending','drafts','limits','optouts'):self.state.setdefault(name,{})
        return self.state

    def save(self):
        if not self.put('communications',self.state):
            self.state=None
            raise ValueError('Storage confirmation failed. Refresh status before retrying; no automatic retry will occur.')

    def seal(self,text):return self.cipher().encrypt(text.encode()).decode()
    def unseal(self,text):return self.cipher().decrypt(text.encode()).decode()
    def digest(self,text):return hashlib.sha256(text.encode()).hexdigest()
    def contact_id(self,owner,channel,to):return self.digest(owner+'|'+('email' if channel=='email' else 'phone')+'|'+to)
    def mask(self,to):
        if '@' in to:
            a,b=to.split('@',1);return a[:1]+'•••@'+b
        return to[:3]+'••••••'+to[-4:]

    def rate(self,name,limit,window=86400):
        d=self.load();now=self.clock()
        d['limits']={k:[t for t in ts if t>now-86400] for k,ts in d['limits'].items() if any(t>now-86400 for t in ts)}
        ts=[t for t in d['limits'].get(name,[]) if t>now-window]
        if len(ts)>=limit:raise ValueError('Communications limit reached. Try again after the limit resets.')
        d['limits'][name]=ts+[now]

    def provider(self,channel,to,fields):
        if channel=='email':
            data={'personalizations':[{'to':[{'email':to}]}], 'from':{'email':self.key('SENDGRID_FROM_EMAIL'),'name':'OraCool'},'subject':fields['subject'],'content':[{'type':'text/plain','value':fields['message']}]}
            req=urllib.request.Request('https://api.sendgrid.com/v3/mail/send',data=json.dumps(data).encode(),headers={'Authorization':'Bearer '+str(self.key('SENDGRID_API_KEY')),'Content-Type':'application/json'},method='POST')
        else:
            sid=str(self.key('TWILIO_ACCOUNT_SID'))
            if channel in ('verify','check'):
                path='/v2/Services/'+str(self.key('TWILIO_VERIFY_SERVICE_SID'))+('/Verifications' if channel=='verify' else '/VerificationCheck')
                url='https://verify.twilio.com'+path
            else:
                url='https://api.twilio.com/2010-04-01/Accounts/'+sid+('/Calls.json' if channel=='call' else '/Messages.json')
            req=None
        try:
            if channel=='email':
                with urllib.request.urlopen(req,timeout=20) as r:
                    return {'sid':r.headers.get('X-Message-Id',''),'status':'accepted'}
            # Twilio: API key first (if configured), Auth Token fallback on 401.
            data,_=twilio_post(self.key,url,fields)
            return data
        except urllib.error.HTTPError as e:
            if e.code>=500:raise RuntimeError('Provider outcome unknown. Check its console; do not resend automatically.') from None
            raise ValueError('Provider rejected the request (HTTP '+str(e.code)+'). Check sender authorization, verified recipients, opt-outs and credit.') from None
        except ValueError:
            raise
        except Exception:
            raise RuntimeError('Provider outcome unknown. Check its console; do not resend automatically.') from None

    @eligible
    def listing(self,owner):
        with self.lock:
            d=self.load()
            rows=[{'id':i,'channel':v['channel'],'recipient':v['mask'],'status':v['status'],'created':v['created']} for i,v in d['drafts'].items() if v['owner']==owner and v['status']!='draft']
            contacts=[{'id':i,'mask':v['mask'],'type':v['type'],'expires':v['expires']} for i,v in d['contacts'].items() if v['owner']==owner and v['expires']>self.clock()]
            return {'config':{c:self.config(c) for c in ('call','sms','email')},'contacts':contacts,'messages':sorted(rows,key=lambda v:v['created'],reverse=True)[:30],'plan':'enterprise'}

    @eligible
    def verify_send(self,owner,b,ip=''):
        channel=b.get('channel');to=normalize(channel,b.get('to'),b.get('country'))
        cfg=self.config(channel)
        if not cfg['ready']:return {'error':'This channel is not enabled by the operator yet.','config':cfg}
        if b.get('consent') is not True:return {'error':'Confirm that the recipient controls this destination and has agreed to verification and the messages you request.'}
        ident=self.contact_id(owner,channel,to)
        with self.lock:
            d=self.load()
            if self.digest(to) in d['optouts']:return {'error':'This number opted out. It must send START to the sender before verification can resume.'}
            self.rate('verify:owner:'+self.digest(owner),3,3600)
            self.rate('verify:to:'+self.digest(to),3,3600)
            self.rate('verify:ip:'+self.digest(ip),5,3600)
            self.rate('verify:global',30)
            if len([v for v in d['contacts'].values() if v['owner']==owner and v['expires']>self.clock()])>=20 and ident not in d['contacts']:
                return {'error':'Maximum 20 verified recipients per account. Remove an old recipient first.'}
            code=str(secrets.randbelow(900000)+100000)
            row={'owner':owner,'destination':self.seal(to),'type':'email' if channel=='email' else 'phone','mask':self.mask(to),'created':self.clock(),'attempts':0,'code_hash':self.digest(ident+'|'+code) if channel=='email' else ''}
            d['pending'][ident]=row;self.save()
            if channel=='email':
                self.provider('email',to,{'subject':'OraCool recipient verification','message':'An OraCool user asked to verify this address for messages you consent to receive. Your code is '+code+'. It expires in ten minutes. Only provide it to the person whose message you expect. If you did not agree to this, ignore this email; no message can be sent without verification.'})
            else:
                r=self.provider('verify',to,{'To':to,'Channel':'sms'})
                if not str(r.get('sid','')).startswith('VE'):raise ValueError('Verification was not accepted by the provider.')
            return {'ok':True,'verification_id':ident,'mask':row['mask'],'message':'Verification requested. Enter the recipient’s code in the drawer, never in chat.'}

    @eligible
    def verify_check(self,owner,b):
        ident=str(b.get('verification_id') or '');code=str(b.get('code') or '')
        if not re.fullmatch(r'\d{4,8}',code):return {'error':'Enter the numeric verification code.'}
        with self.lock:
            d=self.load();p=d['pending'].get(ident)
            if not p or p['owner']!=owner or p['created']<self.clock()-600 or p['attempts']>=5:return {'error':'Verification expired or unavailable.'}
            p['attempts']+=1;self.save();to=self.unseal(p['destination'])
            if p['type']=='email':ok=hmac.compare_digest(p['code_hash'],self.digest(ident+'|'+code))
            else:
                r=self.provider('check',to,{'To':to,'Code':code});ok=r.get('status')=='approved' and str(r.get('to') or '')==to
            if not ok:return {'error':'Code not accepted.'}
            d['contacts'][ident]={'owner':owner,'destination':p['destination'],'mask':p['mask'],'type':p['type'],'version':secrets.token_urlsafe(16),'expires':self.clock()+30*86400}
            del d['pending'][ident];self.save();return {'ok':True,'mask':p['mask']}

    @eligible
    def remove(self,owner,ident):
        with self.lock:
            d=self.load();c=d['contacts'].get(ident)
            if not c or c['owner']!=owner:return {'error':'Recipient not found.'}
            del d['contacts'][ident];self.save();return {'ok':True}

    @eligible
    def preview(self,owner,b):
        channel=b.get('channel');to=normalize(channel,b.get('to'),b.get('country'))
        message=str(b.get('message') or '').strip();subject=str(b.get('subject') or 'A message from OraCool').strip()
        maxlen=600 if channel=='call' else 1200 if channel=='sms' else 4000
        if not message or len(message)>maxlen:return {'error':'Enter a message between 1 and '+str(maxlen)+' characters.'}
        if len(subject)>160 or '\n' in subject or '\r' in subject:return {'error':'Use a one-line subject of at most 160 characters.'}
        delivered=('This is an automated message from OraCool. '+message) if channel=='call' else ('OraCool: '+message+'\nReply STOP to opt out.') if channel=='sms' else message+'\n\nSent via OraCool to a verified, consenting recipient.'
        cfg=self.config(channel)
        with self.lock:
            d=self.load();ident=self.contact_id(owner,channel,to);c=d['contacts'].get(ident)
            verified=bool(c and c['expires']>self.clock() and self.digest(to) not in d['optouts'])
            # Preview works before setup; no send ID is issued until ready and verified.
            result={'channel':channel,'to':to,'subject':subject if channel=='email' else '', 'message':delivered,'ready':cfg['ready'],'verified':verified,'config':cfg,'expires_in':120,'note':'Review the exact destination and content. Confirmation may incur provider charges. A call reads this message; it is not an interactive AI phone conversation.'}
            if not cfg['ready'] or not verified:return result
            self.rate('preview:'+self.digest(owner),30,3600)
            d['drafts']={k:v for k,v in d['drafts'].items() if v['created']>self.clock()-(600 if v['status']=='draft' else 30*86400)}
            draft_id=secrets.token_urlsafe(24)
            d['drafts'][draft_id]={'owner':owner,'channel':channel,'contact':ident,'version':c['version'],'destination':self.seal(to),'content':self.seal(json.dumps({'message':delivered,'subject':subject})),'mask':self.mask(to),'status':'draft','created':self.clock()}
            self.save();return {**result,'draft_id':draft_id}

    @eligible
    def send(self,owner,b):
        if b.get('confirmed') is not True:return {'error':'Review and explicitly confirm the message before sending.'}
        ident=str(b.get('draft_id') or '')
        with self.lock:
            d=self.load();r=d['drafts'].get(ident)
            if not r or r['owner']!=owner:return {'error':'Draft not found.'}
            if r['status']!='draft':return {'ok':True,'id':ident,'status':r['status'],'duplicate':True,'message':'Already attempted. This request will not send again.'}
            if r['created']<self.clock()-120:return {'error':'Preview expired. Review again before sending.'}
            cfg=self.config(r['channel']);c=d['contacts'].get(r['contact'])
            if not cfg['ready']:return {'error':'Channel disabled. Nothing was sent.'}
            to=self.unseal(r['destination'])
            if not c or c['owner']!=owner or c['version']!=r['version'] or c['expires']<=self.clock() or self.digest(to) in d['optouts']:return {'error':'Recipient verification changed or was removed. Nothing was sent.'}
            self.rate('send:owner:'+self.digest(owner),10)
            self.rate('send:recipient:'+self.digest(to),5)
            self.rate('send:global',100)
            content=json.loads(self.unseal(r['content']))
            # Persist reservation and claim globally before touching the provider.
            r['status']='dispatching';self.save()
            if not self.claim('comms_'+ident):
                r['status']='unknown';self.save();return {'error':'Dispatch reservation could not be confirmed. No retry; check status/provider console.'}
            base=str(self.key('PUBLIC_BASE_URL') or '').rstrip('/')+'/api/comms/callback/'+ident
            fields={'To':to,'From':self.key('TWILIO_FROM_NUMBER'),'StatusCallback':base}
            if r['channel']=='call':fields.update(Twiml='<Response><Say>'+html.escape(content['message'])+'</Say><Hangup/></Response>',Timeout='30',TimeLimit='90',StatusCallbackEvent=['initiated','ringing','answered','completed'])
            elif r['channel']=='sms':fields['Body']=content['message']
            else:fields=content
            try:
                out=self.provider(r['channel'],to,fields)
                sid=str(out.get('sid') or '')
                if r['channel']!='email' and not sid.startswith('CA' if r['channel']=='call' else 'SM'):raise RuntimeError('Provider receipt missing; check the provider console before any further attempt.')
                r.update(status='accepted',provider_id=sid,attempted=self.clock())
            except ValueError:
                r['status']='rejected';self.save();raise
            except Exception:
                r['status']='unknown';self.save();raise
            self.save();return {'ok':True,'id':ident,'status':'accepted','message':'Accepted by the provider. This does not confirm delivery or that a call was heard.'}

    def signed(self,path,params,signature):
        token=str(self.key('TWILIO_AUTH_TOKEN') or '');base=str(self.key('PUBLIC_BASE_URL') or '').rstrip('/')
        if not token or not base or params.get('AccountSid')!=self.key('TWILIO_ACCOUNT_SID'):return False
        payload=base+path+''.join(k+str(params[k]) for k in sorted(params))
        wanted=base64.b64encode(hmac.new(token.encode(),payload.encode(),hashlib.sha1).digest()).decode()
        return hmac.compare_digest(wanted,str(signature))

    def callback(self,ident,params,signature):
        if not self.signed('/api/comms/callback/'+ident,params,signature):return False
        with self.lock:
            d=self.load();r=d['drafts'].get(ident)
            if not r or r['channel']=='email' or r['status']=='draft':return False
            sid=params.get('CallSid' if r['channel']=='call' else 'MessageSid','')
            if not sid.startswith('CA' if r['channel']=='call' else 'SM') or (r.get('provider_id') and sid!=r['provider_id']):return False
            status=params.get('CallStatus' if r['channel']=='call' else 'MessageStatus','')
            allowed={'queued','initiated','ringing','in-progress','completed','busy','failed','no-answer','canceled'} if r['channel']=='call' else {'queued','sending','sent','delivered','failed','undelivered'}
            if status not in allowed:return False
            if r['status'] in {'completed','busy','failed','no-answer','canceled','delivered','undelivered'}:return True
            rank={'accepted':0,'unknown':0,'dispatching':0,'queued':1,'initiated':2,'sending':2,'ringing':3,'sent':3,'in-progress':4}
            if rank.get(status,9)<rank.get(r['status'],0):return True
            r.update(status=status,provider_id=sid);self.save();return True

    def inbound(self,params,signature):
        if not self.signed('/api/comms/inbound',params,signature):return False
        if params.get('To')!=self.key('TWILIO_FROM_NUMBER'):return False
        sender=str(params.get('From') or '')
        if not E164.fullmatch(sender):return False
        command=str(params.get('Body') or '').strip().upper()
        if command not in ('STOP','STOPALL','UNSUBSCRIBE','CANCEL','END','QUIT','START','UNSTOP'):return True
        with self.lock:
            d=self.load();ident=self.digest(sender)
            if command in ('START','UNSTOP'):d['optouts'].pop(ident,None)
            else:
                d['optouts'][ident]=self.clock()
                d['contacts']={k:v for k,v in d['contacts'].items() if v['type']!='phone' or self.digest(self.unseal(v['destination']))!=ident}
            self.save();return True
