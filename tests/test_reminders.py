import base64
import copy
from datetime import datetime,timezone
import hashlib
import hmac
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reminders import Service,parse_schedule
from cryptography.fernet import Fernet

class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.now=[datetime(2026,9,18,12,tzinfo=timezone.utc).timestamp()]
        self.store={}
        self.keys={'TWILIO_ACCOUNT_SID':'ACtest','TWILIO_AUTH_TOKEN':'test-secret','TWILIO_FROM_NUMBER':'+15005550006','TWILIO_VERIFY_SERVICE_SID':'VAtest','PUBLIC_BASE_URL':'https://example.test','REMINDER_CALLS_ENABLED':'true','REMINDERS_ALWAYS_ON':'true'}
        self.cipher=Fernet(Fernet.generate_key())
        self.s=Service(lambda k:copy.deepcopy(self.store.get(k,{})),self.put,self.keys.get,lambda:self.cipher,clock=lambda:self.now[0],allowed=lambda owner:True)
        self.s.provider=Mock(return_value={'sid':'CAtest','status':'queued'})
    def put(self,k,d):self.store[k]=copy.deepcopy(d);return True
    def verified(self,owner='owner@example.test'):
        d=self.s.load();d['phones'][owner]={'phone':self.cipher.encrypt(b'+15005550006').decode(),'version':'v1','verified_at':self.now[0],'mask':'+15••••0006','consent':True};self.s.save()
    def body(self):return {'due_at':self.now[0]+1200,'timezone':'Africa/Lagos','request_id':'request-00000001','confirmed':True,'kind':'timer'}
    def test_nonadmin_service_methods_denied(self):
        self.s.allowed=lambda owner:False
        for method,args in ((self.s.listing,()),(self.s.send_code,('+15005550006',True)),(self.s.check_code,('123456',)),(self.s.create,(self.body(),)),(self.s.cancel,('abc',)),(self.s.disconnect,())):
            self.assertTrue(method('ordinary@example.test',*args)['locked'])
        self.s.provider.assert_not_called()
    def test_role_removal_cancels_future_alarm_while_provider_disabled(self):
        self.verified();self.s.create('owner@example.test',self.body())
        self.keys['REMINDER_CALLS_ENABLED']='false';self.s.allowed=lambda owner:False;self.s.tick()
        self.assertEqual(next(iter(self.store['reminders']['alarms'].values()))['status'],'cancelled')
        self.s.provider.assert_not_called()
    def test_twenty_minute_timer(self):
        for t in ('set a timer for 20 minutes','alert me at the count down of twenty min'):
            r=parse_schedule(t,'Africa/Lagos',self.now[0]);self.assertEqual(r['due_at'],self.now[0]+1200)
    def test_next_six_am_in_lagos(self):
        r=parse_schedule('oracool call me when it is 6am','Africa/Lagos',self.now[0]);self.assertTrue(r['local_time'].startswith('2026-09-19T06:00:00+01:00'))
    def test_ambiguous_clock_requests_clarification(self):self.assertIn('error',parse_schedule('wake me at 6','Africa/Lagos',self.now[0]))
    def test_invalid_timezone(self):self.assertIn('error',parse_schedule('timer for 20 minutes','Bad/Zone',self.now[0]))
    def test_no_recurring_claim(self):self.assertIn('error',parse_schedule('wake me at 6am every day','Africa/Lagos',self.now[0]))
    def test_short_phone_timer_rejected(self):self.assertIn('error',parse_schedule('timer for 5 seconds','Africa/Lagos',self.now[0]))
    def test_dst_ambiguity_rejected(self):
        now=datetime(2026,11,1,4,tzinfo=timezone.utc).timestamp();self.assertIn('error',parse_schedule('wake me at 1:30am','America/New_York',now))
    def test_compound_duration_not_silently_shortened(self):
        self.assertIn('error',parse_schedule('timer for 1 hour and 30 minutes','Africa/Lagos',self.now[0]))
    def test_expired_today_not_silently_tomorrow(self):
        self.assertIn('error',parse_schedule('wake me today at 6am','Africa/Lagos',self.now[0]))
    def test_atomic_claim_denial_prevents_duplicate_dispatch(self):
        self.verified();self.s.create('owner@example.test',self.body());self.s.claim=lambda ident:False;self.now[0]+=1200;self.s.tick();self.s.provider.assert_not_called()
    def test_blocked_account_not_called(self):
        self.verified();self.s.create('owner@example.test',self.body());self.s.allowed=lambda owner:False;self.now[0]+=1200;self.s.tick();self.s.provider.assert_not_called()
    def test_disabled_provider_does_not_schedule(self):
        self.keys.pop('TWILIO_AUTH_TOKEN');self.assertIn('error',self.s.create('owner@example.test',self.body()));self.s.provider.assert_not_called()
    def test_unverified_number_rejected(self):self.assertIn('error',self.s.create('owner@example.test',self.body()))
    def test_confirmation_required(self):
        self.verified();b=self.body();b['confirmed']=False;self.assertIn('error',self.s.create('owner@example.test',b))
    def test_idempotent_creation(self):
        self.verified();a=self.s.create('owner@example.test',self.body());b=self.s.create('owner@example.test',self.body());self.assertEqual(a['alarm']['id'],b['alarm']['id']);self.assertTrue(b['duplicate'])
    def test_account_isolation(self):
        self.verified();a=self.s.create('owner@example.test',self.body())['alarm'];self.assertEqual(self.s.listing('other@example.test')['alarms'],[]);self.assertIn('error',self.s.cancel('other@example.test',a['id']))
    def test_dispatch_only_verified_number_once(self):
        self.verified();b=self.body();b['to']='+441234567890';self.s.create('owner@example.test',b);self.now[0]+=1200;self.s.tick();self.s.tick()
        self.s.provider.assert_called_once();self.assertEqual(self.s.provider.call_args.args[1]['To'],'+15005550006');self.assertIn('<Say>',self.s.provider.call_args.args[1]['Twiml'])
    def test_missed_alarm_not_called_hours_late(self):
        self.verified();self.s.create('owner@example.test',self.body());self.now[0]+=7200;self.s.tick();self.s.provider.assert_not_called();self.assertEqual(self.s.listing('owner@example.test')['alarms'][0]['status'],'missed')
    def test_network_ambiguity_not_retried(self):
        self.verified();self.s.create('owner@example.test',self.body());self.s.provider.side_effect=RuntimeError('timeout');self.now[0]+=1200;self.s.tick();self.s.tick();self.s.provider.assert_called_once();self.assertEqual(self.s.listing('owner@example.test')['alarms'][0]['status'],'unknown')
    def test_storage_failure_prevents_call(self):
        self.verified();self.s.create('owner@example.test',self.body());self.now[0]+=1200;self.s.put=lambda *a:False
        with self.assertRaises(ValueError):self.s.tick()
        self.s.provider.assert_not_called()
    def test_restart_recovers_pending(self):
        self.verified();self.s.create('owner@example.test',self.body());self.s.state=None;self.now[0]+=1200;self.s.tick();self.s.provider.assert_called_once()
    def test_disconnect_cancels_pending(self):
        self.verified();self.s.create('owner@example.test',self.body());self.s.disconnect('owner@example.test');self.now[0]+=1200;self.s.tick();self.s.provider.assert_not_called()
    def test_phone_not_in_public_state(self):
        self.verified();self.assertNotIn('+15005550006',str(self.s.listing('owner@example.test')))
    def test_verify_needs_explicit_consent(self):
        self.assertIn('error',self.s.send_code('owner@example.test','+15005550006',False));self.s.provider.assert_not_called()
    def test_successful_sms_verification(self):
        self.s.provider.side_effect=[{'sid':'VEtest','status':'pending'},{'status':'approved','to':'+15005550006'}]
        self.assertTrue(self.s.send_code('owner@example.test','+15005550006',True)['ok']);self.assertTrue(self.s.check_code('owner@example.test','123456')['ok']);self.assertTrue(self.s.listing('owner@example.test')['phone']['verified'])
    def test_verification_attempt_budget(self):
        self.s.provider.return_value={'sid':'VEtest'}
        for _ in range(3):self.s.send_code('owner@example.test','+15005550006',True)
        with self.assertRaises(ValueError):self.s.send_code('owner@example.test','+15005550006',True)
        self.assertEqual(self.s.provider.call_count,3)
    def test_signed_callback_and_out_of_order_events(self):
        self.verified();a=self.s.create('owner@example.test',self.body())['alarm'];self.now[0]+=1200;self.s.tick()
        fields={'AccountSid':'ACtest','CallSid':'CAtest','CallStatus':'completed','SequenceNumber':'3'}
        url=self.keys['PUBLIC_BASE_URL']+'/api/reminders/callback/'+a['id']
        data=url+''.join(k+fields[k] for k in sorted(fields));sig=base64.b64encode(hmac.new(b'test-secret',data.encode(),hashlib.sha1).digest()).decode()
        self.assertFalse(self.s.callback(a['id'],fields,'forged'));self.assertTrue(self.s.callback(a['id'],fields,sig));self.assertEqual(self.s.listing('owner@example.test')['alarms'][0]['status'],'completed')
    def test_limits_daily_calls(self):
        self.verified()
        for i in range(5):
            b=self.body();b['request_id']='request-0000000'+str(i);self.assertTrue(self.s.create('owner@example.test',b)['ok'])
        b=self.body();b['request_id']='request-00000009';self.assertIn('error',self.s.create('owner@example.test',b))

if __name__=='__main__':unittest.main()
