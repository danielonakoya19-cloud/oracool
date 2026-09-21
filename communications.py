"""Single-recipient, consent-based outbound EMAIL communications (Enterprise).
No bulk sends, no retries, no telephone channels — call/SMS support was
removed permanently; email (SendGrid, operator-enabled) is the only channel.
"""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from functools import wraps


def normalize(channel, destination, country='international'):
    """Email only. Call/SMS channels no longer exist."""
    if channel in ('call', 'sms'):
        raise ValueError('Telephone calling and SMS are not available on OraCool.')
    if channel != 'email':
        raise ValueError('Email is the only supported channel.')
    text = str(destination or '').strip()
    if len(text) > 254 or not re.fullmatch(r'[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+', text):
        raise ValueError('Enter a valid email address.')
    return text.lower()


def eligible(fn):
    @wraps(fn)
    def wrapped(self, owner, *args, **kwargs):
        if not self.allowed(owner):
            return {'error': 'Communications require Enterprise or administrator access.', 'plan': 'enterprise', 'locked': True}
        return fn(self, owner, *args, **kwargs)
    return wrapped


class Service:
    def __init__(self, get, put, key, cipher, allowed, claim, clock=time.time):
        self.get, self.put, self.key, self.cipher, self.allowed, self.claim, self.clock = get, put, key, cipher, allowed, claim, clock
        self.lock = threading.RLock()
        self.state = None

    def config(self, channel='email'):
        if channel in ('call', 'sms'):
            return {'ready': False, 'missing': ['removed'], 'note': 'Telephone calling and SMS were removed from OraCool.'}
        required = ['SENDGRID_API_KEY', 'SENDGRID_FROM_EMAIL', 'SENDGRID_ENABLED']
        missing = [k for k in required if (str(self.key(k) or '').lower() not in ('true', 'yes', '1') if k.endswith('_ENABLED') else not self.key(k))]
        return {'ready': not missing, 'missing': missing,
                'note': 'Requires a verified sender (SendGrid, operator-configured), recipient consent and available provider credit. Single-recipient transactional messages only.'}

    def load(self):
        if self.state is None:
            d = self.get('communications')
            if not isinstance(d, dict):
                raise ValueError('Durable communications storage unavailable. Nothing has been sent.')
            self.state = d
        for name in ('contacts', 'pending', 'drafts', 'limits', 'optouts'):
            self.state.setdefault(name, {})
        return self.state

    def save(self):
        if not self.put('communications', self.state):
            self.state = None
            raise ValueError('Storage confirmation failed. Refresh status before retrying; no automatic retry will occur.')

    def seal(self, text):
        return self.cipher().encrypt(text.encode()).decode()

    def unseal(self, text):
        return self.cipher().decrypt(text.encode()).decode()

    def digest(self, text):
        return hashlib.sha256(text.encode()).hexdigest()

    def contact_id(self, owner, channel, to):
        return self.digest(owner + '|email|' + to)

    def mask(self, to):
        if '@' in to:
            a, b = to.split('@', 1)
            return a[:1] + '•••@' + b
        return to[:3] + '••••••' + to[-4:]

    def rate(self, name, limit, window=86400):
        d = self.load()
        now = self.clock()
        d['limits'] = {k: [t for t in ts if t > now - 86400] for k, ts in d['limits'].items() if any(t > now - 86400 for t in ts)}
        ts = [t for t in d['limits'].get(name, []) if t > now - window]
        if len(ts) >= limit:
            raise ValueError('Communications limit reached. Try again after the limit resets.')
        d['limits'][name] = ts + [now]

    def provider(self, channel, to, fields):
        if channel != 'email':
            raise ValueError('Email is the only supported channel.')
        data = {'personalizations': [{'to': [{'email': to}]}],
                'from': {'email': self.key('SENDGRID_FROM_EMAIL'), 'name': 'OraCool'},
                'subject': fields['subject'],
                'content': [{'type': 'text/plain', 'value': fields['message']}]}
        req = urllib.request.Request('https://api.sendgrid.com/v3/mail/send', data=json.dumps(data).encode(),
                                     headers={'Authorization': 'Bearer ' + str(self.key('SENDGRID_API_KEY')), 'Content-Type': 'application/json'},
                                     method='POST')
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return {'sid': r.headers.get('X-Message-Id', ''), 'status': 'accepted'}
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                raise RuntimeError('Provider outcome unknown. Check its console; do not resend automatically.') from None
            raise ValueError('Provider rejected the request (HTTP ' + str(e.code) + '). Check sender authorization and verified recipients.') from None
        except Exception:
            raise RuntimeError('Provider outcome unknown. Check its console; do not resend automatically.') from None

    @eligible
    def listing(self, owner):
        with self.lock:
            d = self.load()
            rows = [{'id': i, 'channel': v['channel'], 'recipient': v['mask'], 'status': v['status'], 'created': v['created']}
                    for i, v in d['drafts'].items() if v['owner'] == owner and v['status'] != 'draft']
            contacts = [{'id': i, 'mask': v['mask'], 'type': v['type'], 'expires': v['expires']}
                        for i, v in d['contacts'].items() if v['owner'] == owner and v['expires'] > self.clock()]
            return {'config': {'email': self.config('email')}, 'contacts': contacts,
                    'messages': sorted(rows, key=lambda v: v['created'], reverse=True)[:30], 'plan': 'enterprise'}

    @eligible
    def verify_send(self, owner, b, ip=''):
        channel = b.get('channel')
        try:
            to = normalize(channel, b.get('to'), b.get('country'))
        except ValueError as e:
            return {'error': str(e)}
        cfg = self.config(channel)
        if not cfg['ready']:
            return {'error': 'Email is not enabled by the operator yet.', 'config': cfg}
        if b.get('consent') is not True:
            return {'error': 'Confirm that the recipient controls this address and has agreed to verification and the messages you request.'}
        ident = self.contact_id(owner, channel, to)
        with self.lock:
            d = self.load()
            if self.digest(to) in d['optouts']:
                return {'error': 'This address opted out. It must confirm again before verification can resume.'}
            self.rate('verify:owner:' + self.digest(owner), 3, 3600)
            self.rate('verify:to:' + self.digest(to), 3, 3600)
            self.rate('verify:ip:' + self.digest(ip), 5, 3600)
            self.rate('verify:global', 30)
            if len([v for v in d['contacts'].values() if v['owner'] == owner and v['expires'] > self.clock()]) >= 20 and ident not in d['contacts']:
                return {'error': 'Maximum 20 verified recipients per account. Remove an old recipient first.'}
            code = str(secrets.randbelow(900000) + 100000)
            row = {'owner': owner, 'destination': self.seal(to), 'type': 'email', 'mask': self.mask(to),
                   'created': self.clock(), 'attempts': 0, 'code_hash': self.digest(ident + '|' + code)}
            d['pending'][ident] = row
            self.save()
            self.provider('email', to, {'subject': 'OraCool recipient verification',
                                        'message': 'An OraCool user asked to verify this address for messages you consent to receive. Your code is ' + code +
                                                   '. It expires in ten minutes. Only provide it to the person whose message you expect. '
                                                   'If you did not agree to this, ignore this email; no message can be sent without verification.'})
            return {'ok': True, 'verification_id': ident, 'mask': row['mask'],
                    'message': 'Verification requested. Enter the recipient’s code in the drawer, never in chat.'}

    @eligible
    def verify_check(self, owner, b):
        ident = str(b.get('verification_id') or '')
        code = str(b.get('code') or '')
        if not re.fullmatch(r'\d{4,8}', code):
            return {'error': 'Enter the numeric verification code.'}
        with self.lock:
            d = self.load()
            p = d['pending'].get(ident)
            if not p or p['owner'] != owner or p['created'] < self.clock() - 600 or p['attempts'] >= 5:
                return {'error': 'Verification expired or unavailable.'}
            p['attempts'] += 1
            self.save()
            ok = hmac.compare_digest(p['code_hash'], self.digest(ident + '|' + code))
            if not ok:
                return {'error': 'Code not accepted.'}
            d['contacts'][ident] = {'owner': owner, 'destination': p['destination'], 'mask': p['mask'], 'type': 'email',
                                    'version': secrets.token_urlsafe(16), 'expires': self.clock() + 30 * 86400}
            del d['pending'][ident]
            self.save()
            return {'ok': True, 'mask': p['mask']}

    @eligible
    def remove(self, owner, ident):
        with self.lock:
            d = self.load()
            c = d['contacts'].get(ident)
            if not c or c['owner'] != owner:
                return {'error': 'Recipient not found.'}
            del d['contacts'][ident]
            self.save()
            return {'ok': True}

    @eligible
    def preview(self, owner, b):
        channel = b.get('channel')
        try:
            to = normalize(channel, b.get('to'), b.get('country'))
        except ValueError as e:
            return {'error': str(e)}
        message = str(b.get('message') or '').strip()
        subject = str(b.get('subject') or 'A message from OraCool').strip()
        if not message or len(message) > 4000:
            return {'error': 'Enter a message between 1 and 4000 characters.'}
        if len(subject) > 160 or '\n' in subject or '\r' in subject:
            return {'error': 'Use a one-line subject of at most 160 characters.'}
        delivered = message + '\n\nSent via OraCool to a verified, consenting recipient.'
        cfg = self.config(channel)
        with self.lock:
            d = self.load()
            ident = self.contact_id(owner, channel, to)
            c = d['contacts'].get(ident)
            verified = bool(c and c['expires'] > self.clock() and self.digest(to) not in d['optouts'])
            # Preview works before setup; no send ID is issued until ready and verified.
            result = {'channel': channel, 'to': to, 'subject': subject, 'message': delivered, 'ready': cfg['ready'],
                      'verified': verified, 'config': cfg, 'expires_in': 120,
                      'note': 'Review the exact destination and content. Confirmation uses provider credit; acceptance is not delivery confirmation.'}
            if not cfg['ready'] or not verified:
                return result
            self.rate('preview:' + self.digest(owner), 30, 3600)
            d['drafts'] = {k: v for k, v in d['drafts'].items() if v['created'] > self.clock() - (600 if v['status'] == 'draft' else 30 * 86400)}
            draft_id = secrets.token_urlsafe(24)
            d['drafts'][draft_id] = {'owner': owner, 'channel': channel, 'contact': ident, 'version': c['version'],
                                     'destination': self.seal(to),
                                     'content': self.seal(json.dumps({'message': delivered, 'subject': subject})),
                                     'mask': self.mask(to), 'status': 'draft', 'created': self.clock()}
            self.save()
            return {**result, 'draft_id': draft_id}

    @eligible
    def send(self, owner, b):
        if b.get('confirmed') is not True:
            return {'error': 'Review and explicitly confirm the message before sending.'}
        ident = str(b.get('draft_id') or '')
        with self.lock:
            d = self.load()
            r = d['drafts'].get(ident)
            if not r or r['owner'] != owner:
                return {'error': 'Draft not found.'}
            if r['status'] != 'draft':
                return {'ok': True, 'id': ident, 'status': r['status'], 'duplicate': True, 'message': 'Already attempted. This request will not send again.'}
            if r['created'] < self.clock() - 120:
                return {'error': 'Preview expired. Review again before sending.'}
            cfg = self.config(r['channel'])
            c = d['contacts'].get(r['contact'])
            if not cfg['ready']:
                return {'error': 'Channel disabled. Nothing was sent.'}
            to = self.unseal(r['destination'])
            if not c or c['owner'] != owner or c['version'] != r['version'] or c['expires'] <= self.clock() or self.digest(to) in d['optouts']:
                return {'error': 'Recipient verification changed or was removed. Nothing was sent.'}
            self.rate('send:owner:' + self.digest(owner), 10)
            self.rate('send:recipient:' + self.digest(to), 5)
            self.rate('send:global', 100)
            content = json.loads(self.unseal(r['content']))
            # Persist reservation and claim globally before touching the provider.
            r['status'] = 'dispatching'
            self.save()
            if not self.claim('comms_' + ident):
                r['status'] = 'unknown'
                self.save()
                return {'error': 'Dispatch reservation could not be confirmed. No retry; check status/provider console.'}
            try:
                out = self.provider(r['channel'], to, content)
                r.update(status='accepted', provider_id=str(out.get('sid') or ''), attempted=self.clock())
            except ValueError:
                r['status'] = 'rejected'
                self.save()
                raise
            except Exception:
                r['status'] = 'unknown'
                self.save()
                raise
            self.save()
            return {'ok': True, 'id': ident, 'status': 'accepted',
                    'message': 'Accepted by the email provider. This does not confirm delivery.'}
