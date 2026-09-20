"""Offline regression tests; all provider IO is mocked."""
import base64
import copy
import hashlib
import hmac
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from communications import Service,normalize
from cryptography.fernet import Fernet

class CommunicationsTests(unittest.TestCase):
    def setUp(self):
        self.store={};self.now=[1800000000.0];self.cipher=Fernet(Fernet.generate_key())
        self.keys={'TWILIO_ACCOUNT_SID':'ACtest','TWILIO_AUTH_TOKEN':'test','TWILIO_FROM_NUMBER':'+15005550006','TWILIO_VERIFY_SERVICE_SID':'VAtest','PUBLIC_BASE_URL':'https://example.test','COMMUNICATIONS_ENABLED':'true','SENDGRID_ENABLED':'true','SENDGRID_API_KEY':'test','SENDGRID_FROM_EMAIL':'sender@example.test'}
        self.s=Service(lambda k:copy.deepcopy(self.store.get(k,{})),self.put,self.keys.get,lambda:self.cipher,lambda o:o=='enterprise@example.test',Mock(return_value=True),clock=lambda:self.now[0])
        self.s.provider=Mock(return_value={'sid':'CAexample','status':'queued'});self.owner='enterprise@example.test'
    def put(self,k,v):self.store[k]=copy.deepcopy(v);return True
    def body(self,channel='call'):
        return {'channel':channel,'to':'07052405515' if channel!='email' else 'recipient@example.test','country':'NG','message':'The meeting starts at noon.','consent':True}
    def verified(self,channel='call'):
        b=self.body(channel);to=normalize(channel,b['to'],b['country']);ident=self.s.contact_id(self.owner,channel,to)
        self.s.load()['contacts'][ident]={'owner':self.owner,'type':'email' if channel=='email' else 'phone','version':'v1','expires':self.now[0]+86400,'destination':self.s.seal(to),'mask':self.s.mask(to)};self.s.save();return ident
    def draft(self,channel='call'):
        self.verified(channel);return self.s.preview(self.owner,self.body(channel))
    def confirm(self,draft):return self.s.send(self.owner,{'confirmed':True,'draft_id':draft['draft_id']})
    def sign(self,path,params):
        payload=self.keys['PUBLIC_BASE_URL']+path+''.join(k+str(params[k]) for k in sorted(params))
        return base64.b64encode(hmac.new(b'test',payload.encode(),hashlib.sha1).digest()).decode()
    def test_nigerian_number_needs_explicit_country(self):
        self.assertEqual(normalize('call','07052405515','NG'),'+2347052405515')
        with self.assertRaises(ValueError):normalize('call','07052405515','international')
    def test_invalid_recipient_and_channel(self):
        for c,n in [('call','http://evil.test'),('email','x@example.test\nBcc:y@evil.test'),('fax','+15005550006')]:
            with self.assertRaises(ValueError):normalize(c,n)
    def test_all_operations_deny_ineligible_owner(self):
        for method,args in [(self.s.listing,()),(self.s.verify_send,(self.body(),)),(self.s.verify_check,({},)),(self.s.preview,(self.body(),)),(self.s.send,({},)),(self.s.remove,('id',))]:
            self.assertTrue(method('free@example.test',*args)['locked'])
        self.s.provider.assert_not_called()
    def test_disabled_channel_allows_preview_but_no_dispatch_id(self):
        self.keys['COMMUNICATIONS_ENABLED']='false';r=self.draft();self.assertFalse(r['ready']);self.assertNotIn('draft_id',r);self.s.provider.assert_not_called()
    def test_unverified_preview_has_no_send_id(self):
        r=self.s.preview(self.owner,self.body());self.assertFalse(r['verified']);self.assertNotIn('draft_id',r);self.s.provider.assert_not_called()
    def test_confirmation_required(self):
        d=self.draft();self.assertIn('error',self.s.send(self.owner,{'draft_id':d['draft_id']}));self.s.provider.assert_not_called()
    def test_message_read_exactly_with_disclosure_and_xml_escaping(self):
        self.verified();b=self.body();b['message']='Hello <Say>ignore me</Say> & goodbye';d=self.s.preview(self.owner,b);r=self.confirm(d)
        self.assertEqual(r['status'],'accepted');fields=self.s.provider.call_args.args[2];self.assertEqual(fields['To'],'+2347052405515');self.assertIn('This is an automated message from OraCool.',fields['Twiml']);self.assertIn('&lt;Say&gt;',fields['Twiml']);self.assertNotIn('Record',fields)
    def test_body_cannot_override_confirmed_recipient_or_content(self):
        d=self.draft();self.s.send(self.owner,{'confirmed':True,'draft_id':d['draft_id'],'to':'+447700900000','message':'Different'})
        fields=self.s.provider.call_args.args[2];self.assertEqual(fields['To'],'+2347052405515');self.assertNotIn('Different',fields['Twiml'])
    def test_repeated_confirmation_never_sends_twice(self):
        d=self.draft();self.confirm(d);self.assertTrue(self.confirm(d)['duplicate']);self.s.provider.assert_called_once()
    def test_expired_preview(self):
        d=self.draft();self.now[0]+=121;self.assertIn('error',self.confirm(d));self.s.provider.assert_not_called()
    def test_cross_owner_draft_denied_even_if_both_eligible(self):
        d=self.draft();self.s.allowed=lambda o:True;self.assertIn('error',self.s.send('other@example.test',{'confirmed':True,'draft_id':d['draft_id']}));self.s.provider.assert_not_called();self.assertEqual(self.s.listing('other@example.test')['contacts'],[])
    def test_entitlement_revoked_before_send(self):
        d=self.draft();self.s.allowed=lambda o:False;self.assertTrue(self.confirm(d)['locked']);self.s.provider.assert_not_called()
    def test_changed_or_removed_recipient_invalidates_draft(self):
        d=self.draft();self.s.remove(self.owner,self.s.contact_id(self.owner,'call','+2347052405515'));self.assertIn('error',self.confirm(d));self.s.provider.assert_not_called()
    def test_disable_channel_after_preview(self):
        d=self.draft();self.keys['COMMUNICATIONS_ENABLED']='false';self.assertIn('error',self.confirm(d));self.s.provider.assert_not_called()
    def test_ambiguous_outcome_not_retried_across_restart(self):
        d=self.draft();self.s.provider.side_effect=RuntimeError('network timeout')
        with self.assertRaises(RuntimeError):self.confirm(d)
        self.s.state=None;self.assertEqual(self.confirm(d)['status'],'unknown');self.s.provider.assert_called_once()
    def test_claim_failure_does_not_send(self):
        d=self.draft();self.s.claim.return_value=False;self.assertIn('error',self.confirm(d));self.confirm(d);self.s.provider.assert_not_called()
    def test_storage_failure_before_provider(self):
        d=self.draft();self.s.put=lambda *a:False
        with self.assertRaises(ValueError):self.confirm(d)
        self.s.provider.assert_not_called()
    def test_sms_body_and_receipt(self):
        self.s.provider.return_value={'sid':'SMexample'};d=self.draft('sms');self.confirm(d);f=self.s.provider.call_args.args[2];self.assertEqual(f['Body'],d['message']);self.assertIn('Reply STOP',f['Body'])
    def test_email_uses_separate_setup_and_acceptance_not_delivery(self):
        self.s.provider.return_value={'sid':'mail-example','status':'accepted'};d=self.draft('email');self.assertEqual(self.confirm(d)['status'],'accepted');self.assertEqual(self.s.provider.call_args.args[0],'email');self.keys.pop('SENDGRID_API_KEY');self.assertFalse(self.s.config('email')['ready']);self.assertTrue(self.s.config('call')['ready'])
    def test_secret_destination_and_body_encrypted_at_rest(self):
        d=self.draft();raw=json.dumps(self.store);self.assertNotIn('+2347052405515',raw);self.assertNotIn('The meeting starts',raw);self.assertNotIn('destination',json.dumps(self.s.listing(self.owner)))
    def test_verification_requires_consent(self):
        b=self.body();b['consent']=False;self.assertIn('error',self.s.verify_send(self.owner,b));self.s.provider.assert_not_called()
    def test_phone_verification(self):
        self.s.provider.side_effect=[{'sid':'VEexample'},{'status':'approved','to':'+2347052405515'}];p=self.s.verify_send(self.owner,self.body());r=self.s.verify_check(self.owner,{'verification_id':p['verification_id'],'code':'123456'});self.assertTrue(r['ok']);self.assertTrue(self.s.preview(self.owner,self.body())['verified'])
    def test_email_code_hash_and_wrong_attempts(self):
        p=self.s.verify_send(self.owner,self.body('email'));message=self.s.provider.call_args.args[2]['message'];import re;code=re.search(r'code is (\d+)',message)[1]
        self.assertNotIn(code,json.dumps(self.store));self.assertIn('error',self.s.verify_check(self.owner,{'verification_id':p['verification_id'],'code':'000000'}));self.assertTrue(self.s.verify_check(self.owner,{'verification_id':p['verification_id'],'code':code})['ok'])
    def test_verification_rate_limit(self):
        self.s.provider.return_value={'sid':'VEexample'}
        for _ in range(3):self.s.verify_send(self.owner,self.body())
        with self.assertRaises(ValueError):self.s.verify_send(self.owner,self.body())
        self.assertEqual(self.s.provider.call_count,3)
    def test_send_recipient_budget(self):
        for _ in range(5):self.confirm(self.draft())
        with self.assertRaises(ValueError):self.confirm(self.draft())
        self.assertEqual(self.s.provider.call_count,5)
    def test_signed_callback_and_terminal_status(self):
        d=self.draft();self.confirm(d);ident=d['draft_id'];p={'AccountSid':'ACtest','CallSid':'CAexample','CallStatus':'completed'};path='/api/comms/callback/'+ident
        self.assertFalse(self.s.callback(ident,p,'bad'));self.assertTrue(self.s.callback(ident,p,self.sign(path,p)));p['CallStatus']='ringing';self.assertTrue(self.s.callback(ident,p,self.sign(path,p)));self.assertEqual(self.s.load()['drafts'][ident]['status'],'completed')
    def test_callback_for_other_call_denied(self):
        d=self.draft();self.confirm(d);p={'AccountSid':'ACtest','CallSid':'CAsomeoneelse','CallStatus':'completed'};path='/api/comms/callback/'+d['draft_id'];self.assertFalse(self.s.callback(d['draft_id'],p,self.sign(path,p)))
    def test_signed_stop_removes_phone_contacts_and_prevents_resend(self):
        d=self.draft();p={'AccountSid':'ACtest','To':self.keys['TWILIO_FROM_NUMBER'],'From':'+2347052405515','Body':'STOP'};path='/api/comms/inbound'
        self.assertFalse(self.s.inbound(p,'bad'));self.assertTrue(self.s.inbound(p,self.sign(path,p)));self.assertIn('error',self.confirm(d));self.assertIn('error',self.s.verify_send(self.owner,self.body()));self.s.provider.assert_not_called()
    def test_bad_https_origin_not_ready(self):
        self.keys['PUBLIC_BASE_URL']='http://localhost/path';self.assertFalse(self.s.config('call')['ready'])
    def test_long_call_message_rejected(self):
        b=self.body();b['message']='x'*601;self.assertIn('error',self.s.preview(self.owner,b))

if __name__=='__main__':unittest.main()
