"""Offline regression tests. Provider calls are mocked; no real account or payment changes."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('oracool_test_server', ROOT/'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)

class Patch9Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patches=[patch.object(s,'DATA_DIR',self.tmp.name),patch.object(s,'BASE_DIR',self.tmp.name),
                      patch.dict(s.KEYS,{},clear=True),patch.object(s,'_AUTH_CACHE',{}),
                      patch.object(s,'_CONV_SYNC',{'cloud_ok':None,'error':''}),patch.object(s,'audit_log')]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def cloud(self):s.KEYS.update(SUPABASE_URL='https://example.invalid',SUPABASE_SERVICE_KEY='server-test')
    def test_environment_only_gateway_and_media(self):
        with patch.dict(os.environ,{'ATLOS_MERCHANT_ID':'test-merchant','ATLOS_API_SECRET':'test-secret',
                                    'AGNES_API_KEY':'test-media','CRYPTO_WALLET_EVM':'0x'+'1'*40},clear=True):
            s._load_keys()
        self.assertEqual(s.key('ATLOS_MERCHANT_ID'),'test-merchant')
        self.assertEqual(s.key('AGNES_API_KEY'),'test-media')
        self.assertEqual(s.crypto_wallet(),'0x'+'1'*40)
    def test_signup_no_verification_email(self):
        self.cloud()
        with patch.object(s,'is_admin',return_value=False),patch.object(s,'http_fetch',return_value=(201,b'{}',{})) as fetch,patch.object(s,'supabase_auth',return_value={'status':200,'data':{'access_token':'session','user':{'email':'a@example.com'}}}) as auth,patch.object(s,'touch_user'),patch.object(s,'is_blocked',return_value=False),patch.object(s,'check_subscription',return_value=None):
            r=s.auth_signup('a@example.com','TestPassword123')
        self.assertEqual(r['status'],200)
        self.assertNotIn('verify_sent',r)
        create_call = next(c for c in fetch.call_args_list if '/auth/v1/admin/users' in c.args[0])
        self.assertTrue(create_call.kwargs['json_body']['email_confirm'])
        self.assertIn('grant_type=password',auth.call_args.args[0])
    def test_admin_signup_is_reserved(self):
        with patch.object(s,'is_admin',return_value=True),patch.object(s,'http_fetch') as f:
            r=s.auth_signup('owner@example.com','TestPassword123')
        self.assertIn('reserved',r['error']);f.assert_not_called()
    def test_cloud_chat_restore_after_disk_loss(self):
        self.cloud()
        remote={'a@example.com':[{'id':'abc','title':'Old chat','messages':[{'role':'assistant','content':'Welcome back'}]}]}
        with patch.object(s,'supabase_kv_get',return_value=remote) as get:
            self.assertEqual(s.conv_list('a@example.com')[0]['id'],'abc')
            self.assertEqual(s.conv_get('a@example.com','abc')['messages'][0]['content'],'Welcome back')
            get.assert_called_once_with('conversations')
    def test_cloud_outage_cannot_overwrite_archive(self):
        self.cloud()
        with patch.object(s,'supabase_kv_get',return_value=None),patch.object(s,'supabase_kv_put') as put:
            with self.assertRaises(RuntimeError):s.conv_new('a@example.com')
            put.assert_not_called()
    def test_chat_roundtrip_isolated_by_account(self):
        c=s.conv_new('a@example.com');s.conv_append('a@example.com',c['id'],'user','My saved chat')
        self.assertEqual(s.conv_list('b@example.com'),[])
        self.assertEqual(s.conv_get('a@example.com',c['id'])['messages'][0]['content'],'My saved chat')
    def test_missing_gateway_wallet_fallback(self):
        s.KEYS['CRYPTO_WALLET_EVM']='0x'+'1'*40
        with patch.object(s,'atlos_api',return_value={'error':'not configured'}),patch.object(s,'crypto_qr_svg_b64',return_value='qr'):
            r=s.crypto_invoice('a@example.com','enterprise')
        self.assertTrue(r['ok']);self.assertEqual(r['amount_usd'],500)
        self.assertTrue(r['manual_review']);self.assertEqual(r['qr_svg_b64'],'qr')
        self.assertFalse(s._crypto_orders()[r['ref']]['granted'])
    def test_no_gateway_or_wallet_no_fake_invoice(self):
        with patch.object(s,'atlos_api',return_value={'error':'not configured'}):r=s.crypto_invoice('a@example.com')
        self.assertIn('error',r);self.assertNotIn('ok',r)
    def test_brand_encrypted_and_no_secret_in_public_response(self):
        s.KEYS['ENCRYPTION_KEY']='test-encryption-key-not-production'
        with patch.object(s,'is_admin',return_value=True),patch.object(s,'_brand_probe',return_value={'ok':True,'name':'Test Page'}):
            r=s.brand_connect('owner@example.com',{'kind':'facebook','target':'123','credential':'secret-test-token','owned':True})
        self.assertTrue(r['ok']);raw=Path(self.tmp.name,'brand_accounts.json').read_text()
        self.assertNotIn('secret-test-token',raw)
        self.assertNotIn('secret',json.dumps(s.brand_public()))
    def test_brand_nonadmin_blocked(self):
        with patch.object(s,'is_admin',return_value=False):
            self.assertIn('error',s.brand_connect('user@example.com',{}))
            self.assertIn('error',s.brand_inspect('user@example.com'))
            self.assertIn('error',s.brand_publish('user@example.com',{}))
    def test_brand_publish_requires_confirmation(self):
        with patch.object(s,'is_admin',return_value=True),patch.object(s,'http_fetch') as fetch:
            self.assertIn('error',s.brand_publish('owner@example.com',{'text':'Test'}))
            fetch.assert_not_called()
    def test_brand_cannot_store_without_encryption(self):
        with patch.object(s,'is_admin',return_value=True),patch.object(s,'_brand_probe') as probe:
            r=s.brand_connect('owner@example.com',{'owned':True,'credential':'placeholder'})
            self.assertIn('error',r);probe.assert_not_called()
    def test_identity_does_not_trust_claimed_email(self):
        class Handler:
            def _auth(self,b):return None
        self.assertEqual(s.request_identity(Handler(),{'email':'owner@example.com'}),'')
        with patch.object(s,'supabase_auth',return_value={'status':200,'data':{'email':'user@example.com'}}):
            self.assertEqual(s.request_identity(Handler(),{'email':'owner@example.com','access_token':'valid'}),'user@example.com')
    def test_agnes_video_schema_and_metadata(self):
        s.KEYS['AGNES_API_KEY']='test-media'
        responses=[(200,b'{"id":"video-test","status":"queued"}',{}),
                   (200,b'{"status":"completed","metadata":{"url":"https://example.com/video.mp4"}}',{})]
        with patch.object(s,'http_fetch',side_effect=responses) as fetch,patch.object(s.time,'sleep'):
            r=s._agnes_video('A calm ocean wave',5)
        self.assertTrue(r['ok'])
        body=fetch.call_args_list[0].kwargs['json_body']
        self.assertEqual(body['mode'],'text');self.assertEqual(body['seconds'],'5')
        self.assertIn('/agnesapi?',fetch.call_args_list[1].args[0])
        self.assertEqual(r['urls'],['https://example.com/video.mp4'])
    def test_touch_user_mirrors_email_and_preserves_remote_suspension(self):
        with patch.object(s,'supabase_get_flag',return_value={'email':'saved@example.com','blocked':True}),patch.object(s,'supabase_upsert_flag') as upsert:
            s.touch_user('Saved@Example.com',last_ip='192.0.2.1')
        rec=upsert.call_args.args[0]
        self.assertEqual(rec['email'],'saved@example.com')
        self.assertTrue(rec['blocked'])
        self.assertEqual(rec['last_ip'],'192.0.2.1')
    def test_subscriber_mirror_preserves_enterprise_and_amount(self):
        self.cloud()
        with patch.object(s,'http_fetch',return_value=(201,b'',{})) as fetch:
            self.assertTrue(s.supabase_store_subscriber({'email':'buyer@example.com','plan':'enterprise','tier':'enterprise','amount_usd':500,'amount_ngn':750000,'reference':'test-payment','channel':'test'}))
        body=fetch.call_args.kwargs['json_body']
        self.assertEqual(body['plan'],'enterprise');self.assertEqual(body['tier'],'enterprise')
        self.assertEqual(body['amount_usd'],500);self.assertEqual(body['amount_ngn'],750000)
    def test_user_flags_reject_missing_identity(self):
        self.cloud()
        with patch.object(s,'http_fetch') as fetch:
            self.assertFalse(s.supabase_upsert_flag({'blocked':True}))
            fetch.assert_not_called()
    def test_jobs_own_account_only(self):
        with patch.object(s,'_MEDIA_JOBS',{'j':{'email':'a@example.com','status':'done','kind':'video','result':{'ok':True}}}):
            self.assertIn('error',s.media_job_status('b@example.com','j'))
            self.assertEqual(s.media_job_status('a@example.com','j')['status'],'done')
    def test_reminder_routes_removed_permanently(self):
        """Phone-alarm (Twilio) routes are gone: every /api/reminders/* path 404s for everyone."""
        with patch.object(s,'request_identity',return_value='admin@example.test'),patch.object(s,'is_admin',return_value=True),patch.object(s,'is_blocked',return_value=False):
            server=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler)
            t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            try:
                for suffix in ('state','preview','create','cancel','disconnect','phone/send','phone/verify'):
                    req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/reminders/'+suffix,data=json.dumps({'email':'admin@example.test','admin':True,'confirmed':True}).encode(),headers={'Content-Type':'application/json'})
                    try:
                        urllib.request.urlopen(req,timeout=3)
                        self.fail(suffix + ' should not exist')
                    except urllib.error.HTTPError as e:
                        self.assertEqual(e.code, 404, suffix)
            finally:server.shutdown();server.server_close();t.join()
    def test_comms_all_endpoints_deny_forged_enterprise_and_other_identity(self):
        with patch.object(s,'request_identity',return_value='free@example.test'),patch.object(s,'is_admin',return_value=False),patch.object(s,'is_blocked',return_value=False),patch.object(s,'check_tier',return_value='free'),patch.object(s,'communication_service') as service:
            server=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            try:
                for suffix in ('state','preview','send','verify/send','verify/check','remove'):
                    body={'email':'admin@example.test','admin':True,'tier':'enterprise','token':s.make_tier_token('paid@example.test','enterprise'),'confirmed':True}
                    req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/comms/'+suffix,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                    with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req,timeout=3)
                    self.assertEqual(caught.exception.code,403)
                service.assert_not_called()
            finally:server.shutdown();server.server_close();t.join()
    def test_enterprise_can_access_comms_using_verified_identity(self):
        with patch.object(s,'request_identity',return_value='paid@example.test'),patch.object(s,'is_admin',return_value=False),patch.object(s,'is_blocked',return_value=False),patch.object(s,'check_tier',return_value='enterprise'),patch.object(s,'communication_service') as comms:
            comms.return_value.listing.return_value={'messages':[]}
            server=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            try:
                req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/comms/state',data=json.dumps({'email':'other@example.test'}).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=3) as r:self.assertEqual(r.status,200)
                comms.return_value.listing.assert_called_once_with('paid@example.test')
            finally:server.shutdown();server.server_close();t.join()
    def test_blocked_admin_and_enterprise_cannot_send(self):
        with patch.object(s,'is_blocked',return_value=True),patch.object(s,'is_admin',return_value=True),patch.object(s,'check_tier',return_value='enterprise'):
            self.assertFalse(s.communications_allowed('admin@example.test'))
    def test_cloud_entitlements_override_stale_local_tier(self):
        self.cloud()
        with patch.object(s,'is_blocked',return_value=False),patch.object(s,'is_admin',return_value=False),patch.object(s,'check_tier',return_value='enterprise'):
            for rows in ([],[{'email':'other@example.test','plan':'enterprise','expires_at':'2099-01-01'}],[{'email':'paid@example.test','plan':'enterprise','expires_at':'2001-01-01'}],[{'email':'paid@example.test','plan':'pro','expires_at':'2099-01-01'}]):
                with patch.object(s,'http_fetch',return_value=(200,json.dumps(rows).encode(),{})):self.assertFalse(s.communications_allowed('paid@example.test'))
            with patch.object(s,'http_fetch',return_value=(200,b'[{"email":"paid@example.test","plan":"enterprise","expires_at":"2099-01-01"}]',{})):self.assertTrue(s.communications_allowed('paid@example.test'))
            with patch.object(s,'http_fetch',side_effect=RuntimeError('offline')):self.assertFalse(s.communications_allowed('paid@example.test'))
    def test_admin_revoke_retires_durable_paid_rows(self):
        self.cloud()
        with patch.object(s,'http_fetch',return_value=(204,b'',{})) as fetch,patch.object(s,'touch_user'),patch.object(s,'emit_event'):
            self.assertTrue(s.admin_set_pro('paid@example.test','free')['revoked'])
            self.assertEqual(fetch.call_args.kwargs['method'],'PATCH');self.assertIn('expires_at',fetch.call_args.kwargs['json_body'])
    def test_admin_revoke_cloud_failure_makes_no_local_change(self):
        self.cloud();s.save_subscriber({'email':'paid@example.test','plan':'enterprise','expires_at':'2099-01-01'})
        with patch.object(s,'http_fetch',side_effect=RuntimeError('offline')):
            self.assertIn('error',s.admin_set_pro('paid@example.test','free'))
        self.assertEqual(s.load_subscribers()[0]['plan'],'enterprise')
    def test_http_history_rejects_forged_email(self):
        server=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/chat/sessions',data=json.dumps({'email':'owner@example.com'}).encode(),headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req,timeout=3)
            self.assertEqual(caught.exception.code,401)
        finally:server.shutdown();server.server_close();t.join()

if __name__=='__main__':unittest.main()
