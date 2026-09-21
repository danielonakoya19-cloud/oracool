"""Offline regression tests for the email-only communications service (Twilio removed).
All provider IO is mocked."""
import copy
import json
import re
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from communications import Service, normalize
from cryptography.fernet import Fernet


class CommunicationsTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.now = [1800000000.0]
        self.cipher = Fernet(Fernet.generate_key())
        self.keys = {'COMMUNICATIONS_ENABLED': 'true', 'SENDGRID_ENABLED': 'true', 'SENDGRID_API_KEY': 'test',
                     'SENDGRID_FROM_EMAIL': 'sender@example.test'}
        self.s = Service(lambda k: copy.deepcopy(self.store.get(k, {})), self.put, self.keys.get, lambda: self.cipher,
                         lambda o: o == 'enterprise@example.test', Mock(return_value=True), clock=lambda: self.now[0])
        self.s.provider = Mock(return_value={'sid': 'mail-example', 'status': 'accepted'})
        self.owner = 'enterprise@example.test'

    def put(self, k, v):
        self.store[k] = copy.deepcopy(v)
        return True

    def body(self, channel='email'):
        return {'channel': channel, 'to': 'recipient@example.test', 'message': 'The meeting starts at noon.', 'consent': True}

    def verified(self, channel='email'):
        to = normalize(channel, 'recipient@example.test')
        ident = self.s.contact_id(self.owner, channel, to)
        self.s.load()['contacts'][ident] = {'owner': self.owner, 'type': 'email', 'version': 'v1',
                                            'expires': self.now[0] + 86400, 'destination': self.s.seal(to), 'mask': self.s.mask(to)}
        self.s.save()
        return ident

    def draft(self, channel='email'):
        self.verified(channel)
        return self.s.preview(self.owner, self.body(channel))

    def confirm(self, d):
        return self.s.send(self.owner, {'confirmed': True, 'draft_id': d['draft_id']})

    def test_telephone_channels_are_removed(self):
        for c in ('call', 'sms'):
            with self.assertRaises(ValueError):
                normalize(c, '+2347052405515')
        b = self.body('call')
        self.assertIn('error', self.s.preview(self.owner, b))
        self.assertIn('error', self.s.verify_send(self.owner, b))
        self.assertNotIn('call', self.s.listing(self.owner)['config'])
        self.assertNotIn('sms', self.s.listing(self.owner)['config'])

    def test_email_normalization(self):
        self.assertEqual(normalize('email', 'Recipient@Example.TEST'), 'recipient@example.test')
        for bad in ('x@example.test\nBcc:y@evil.test', 'nope', 'a@b', '@x.com'):
            with self.assertRaises(ValueError):
                normalize('email', bad)

    def test_all_operations_deny_ineligible_owner(self):
        for method, args in [(self.s.listing, ()), (self.s.verify_send, (self.body(),)), (self.s.verify_check, ({},)),
                             (self.s.preview, (self.body(),)), (self.s.send, ({},)), (self.s.remove, ('id',))]:
            self.assertTrue(method('free@example.test', *args)['locked'])
        self.s.provider.assert_not_called()

    def test_disabled_channel_allows_preview_but_no_dispatch_id(self):
        self.keys['SENDGRID_ENABLED'] = 'false'
        r = self.draft()
        self.assertFalse(r['ready'])
        self.assertNotIn('draft_id', r)
        self.s.provider.assert_not_called()

    def test_unverified_preview_has_no_send_id(self):
        r = self.s.preview(self.owner, self.body())
        self.assertFalse(r['verified'])
        self.assertNotIn('draft_id', r)
        self.s.provider.assert_not_called()

    def test_confirmation_required(self):
        d = self.draft()
        self.assertIn('error', self.s.send(self.owner, {'draft_id': d['draft_id']}))
        self.s.provider.assert_not_called()

    def test_message_sent_exactly_with_disclosure(self):
        self.verified()
        b = self.body()
        b['message'] = 'Hello <Say>ignore me</Say> & goodbye'
        d = self.s.preview(self.owner, b)
        r = self.confirm(d)
        self.assertEqual(r['status'], 'accepted')
        sent_to = self.s.provider.call_args.args[1]
        fields = self.s.provider.call_args.args[2]
        self.assertEqual(sent_to, 'recipient@example.test')
        self.assertIn('Sent via OraCool to a verified, consenting recipient.', fields['message'])
        self.assertNotIn('Twiml', fields)

    def test_body_cannot_override_confirmed_recipient_or_content(self):
        d = self.draft()
        self.s.send(self.owner, {'confirmed': True, 'draft_id': d['draft_id'], 'to': 'other@evil.test', 'message': 'Different'})
        sent_to = self.s.provider.call_args.args[1]
        fields = self.s.provider.call_args.args[2]
        self.assertEqual(sent_to, 'recipient@example.test')
        self.assertNotIn('Different', fields['message'])

    def test_repeated_confirmation_never_sends_twice(self):
        d = self.draft()
        self.confirm(d)
        self.assertTrue(self.confirm(d)['duplicate'])
        self.s.provider.assert_called_once()

    def test_expired_preview(self):
        d = self.draft()
        self.now[0] += 121
        self.assertIn('error', self.confirm(d))
        self.s.provider.assert_not_called()

    def test_cross_owner_draft_denied_even_if_both_eligible(self):
        d = self.draft()
        self.s.allowed = lambda o: True
        self.assertIn('error', self.s.send('other@example.test', {'confirmed': True, 'draft_id': d['draft_id']}))
        self.s.provider.assert_not_called()
        self.assertEqual(self.s.listing('other@example.test')['contacts'], [])

    def test_entitlement_revoked_before_send(self):
        d = self.draft()
        self.s.allowed = lambda o: False
        self.assertTrue(self.confirm(d)['locked'])
        self.s.provider.assert_not_called()

    def test_changed_or_removed_recipient_invalidates_draft(self):
        d = self.draft()
        self.s.remove(self.owner, self.s.contact_id(self.owner, 'email', 'recipient@example.test'))
        self.assertIn('error', self.confirm(d))
        self.s.provider.assert_not_called()

    def test_disable_channel_after_preview(self):
        d = self.draft()
        self.keys['SENDGRID_ENABLED'] = 'false'
        self.assertIn('error', self.confirm(d))
        self.s.provider.assert_not_called()

    def test_ambiguous_outcome_not_retried_across_restart(self):
        d = self.draft()
        self.s.provider.side_effect = RuntimeError('network timeout')
        with self.assertRaises(RuntimeError):
            self.confirm(d)
        self.s.state = None
        self.assertEqual(self.confirm(d)['status'], 'unknown')
        self.s.provider.assert_called_once()

    def test_claim_failure_does_not_send(self):
        d = self.draft()
        self.s.claim.return_value = False
        self.assertIn('error', self.confirm(d))
        self.confirm(d)
        self.s.provider.assert_not_called()

    def test_storage_failure_before_provider(self):
        d = self.draft()
        self.s.put = lambda *a: False
        with self.assertRaises(ValueError):
            self.confirm(d)
        self.s.provider.assert_not_called()

    def test_email_acceptance_not_delivery(self):
        d = self.draft()
        r = self.confirm(d)
        self.assertEqual(r['status'], 'accepted')
        self.assertIn('does not confirm delivery', r['message'])
        self.keys.pop('SENDGRID_API_KEY')
        self.assertFalse(self.s.config('email')['ready'])

    def test_secret_destination_and_body_encrypted_at_rest(self):
        self.draft()
        raw = json.dumps(self.store)
        self.assertNotIn('recipient@example.test', raw)
        self.assertNotIn('The meeting starts', raw)
        self.assertNotIn('destination', json.dumps(self.s.listing(self.owner)))

    def test_verification_requires_consent(self):
        b = self.body()
        b['consent'] = False
        self.assertIn('error', self.s.verify_send(self.owner, b))
        self.s.provider.assert_not_called()

    def test_email_code_hash_and_wrong_attempts(self):
        p = self.s.verify_send(self.owner, self.body())
        message = self.s.provider.call_args.args[2]['message']
        code = re.search(r'code is (\d+)', message)[1]
        self.assertNotIn(code, json.dumps(self.store))
        self.assertIn('error', self.s.verify_check(self.owner, {'verification_id': p['verification_id'], 'code': '000000'}))
        self.assertTrue(self.s.verify_check(self.owner, {'verification_id': p['verification_id'], 'code': code})['ok'])
        self.assertTrue(self.s.preview(self.owner, self.body())['verified'])

    def test_verification_rate_limit(self):
        for _ in range(3):
            self.s.verify_send(self.owner, self.body())
        with self.assertRaises(ValueError):
            self.s.verify_send(self.owner, self.body())
        self.assertEqual(self.s.provider.call_count, 3)

    def test_send_recipient_budget(self):
        for _ in range(5):
            self.confirm(self.draft())
        with self.assertRaises(ValueError):
            self.confirm(self.draft())
        self.assertEqual(self.s.provider.call_count, 5)

    def test_long_message_rejected(self):
        b = self.body()
        b['message'] = 'x' * 4001
        self.assertIn('error', self.s.preview(self.owner, b))


if __name__ == '__main__':
    unittest.main()
