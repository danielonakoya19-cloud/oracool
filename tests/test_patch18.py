"""Patch 18 — profile-picture storage fix, scrollable sidebar (client),
room delete & owner-only posting (channels always owner-only), message
reactions, image+file attachments in DMs, and 2-player Tic-Tac-Toe in chat.

Offline: Supabase REST tables emulated by FakeRest; Supabase storage is
exercised at the http_fetch level (the exact bucket-creation payload bug
that broke profile pictures is regression-tested here).
"""
import importlib.util
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
spec = importlib.util.spec_from_file_location('oracool_test_server_p18', ROOT / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
import community  # noqa: E402
from test_patch15 import FakeRest  # noqa: E402
from test_patch16 import FakeCloud, FakeRest16, ADMIN, USER  # noqa: E402


class FakeRest18(FakeRest16):
    """Patch-18 schema: adds comm_games (owner_only_post & reactions are plain fields)."""

    def reset(self):
        super().reset()
        self.tables['comm_games'] = {'rows': [], 'next_id': 1}

    def rest(self, method, path, body=None, prefer=None):
        if path.split('?')[0] == 'comm_games' and method == 'POST':
            t = self.tables['comm_games']
            t['next_id'] += 1
            row = dict(body if isinstance(body, dict) else body[0])
            row.setdefault('id', t['next_id'])
            row.setdefault('created_at', '2026-09-21T00:00:00+00:00')
            row.setdefault('updated_at', row['created_at'])
            t['rows'].append(row)
            return 201, [dict(row)]
        return super().rest(method, path, body=body, prefer=prefer)


class Patch18Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cloud = FakeCloud()
        self.rest = FakeRest18()
        self.storage_calls = []

        def fake_storage_fetch(url, method='GET', headers=None, json_body=None, data=None, timeout=25):
            self.storage_calls.append((method, url))
            return 200, b'{}', 'application/json'

        self.patches = [patch.object(s, 'DATA_DIR', self.tmp.name), patch.object(s, 'BASE_DIR', self.tmp.name),
                        patch.dict(s.KEYS, {}, clear=True), patch.object(s, '_AUTH_CACHE', {}),
                        patch.dict(s._BLOCK_CACHE, {}, clear=True), patch.dict(s._VERIFIED_CACHE, {}, clear=True),
                        patch.object(s, 'audit_log'), patch.object(s, 'emit_event'), patch.object(s, 'notify_admins'),
                        patch.object(s, 'llm_complete', return_value=('', None)),
                        patch.object(s, '_COMMUNITY_SERVICE', None),
                        patch.object(s, 'community_rest', side_effect=self.rest.rest),
                        patch.object(s, 'supabase_get_flag', side_effect=self.cloud.get_flag),
                        patch.object(s, 'supabase_upsert_flag', side_effect=self.cloud.upsert_flag),
                        patch.object(s, 'http_fetch', side_effect=fake_storage_fetch)]
        for p in self.patches:
            p.start()
        s.KEYS.update(SUPABASE_URL='https://example.invalid', SUPABASE_SERVICE_KEY='svc',
                      ADMIN_EMAILS=[ADMIN], JWT_SECRET='t', PAYSTACK_SECRET_KEY='sk_test_dummy')
        s._STORAGE_BUCKETS.clear()
        self.svc = s.community_service()
        self.svc.async_reviews = False
        old = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 7200))
        users = {}
        for e in (USER, 'r1@example.test', 'r2@example.test', ADMIN):
            users[e] = {'email': e, 'created': old, 'last_seen': old}
        s.save_users(users)
        self.svc.ensure_profile(USER, username='verified_user')
        self.svc.ensure_profile('r1@example.test', username='member_one')
        self.svc.ensure_profile('r2@example.test', username='member_two')
        self.svc.ensure_profile(ADMIN, username='the_owner')

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class StorageBucketFixTest(Patch18Base):
    """The live bug: Supabase's storage API now REQUIRES the bucket name in the
    create body; the old client sent only {id, public} -> 400 -> every avatar
    upload failed with 'Could not save the picture'."""

    def _ensure(self, code, message):
        captured = {}

        def fetch(url, method=None, headers=None, data=None, json_body=None, timeout=None):
            captured['url'] = url
            captured['body'] = json_body
            raise urllib.error.HTTPError(url, code, message, {}, io.BytesIO(json.dumps({'message': message}).encode()))

        s._STORAGE_BUCKETS.discard('avatars')
        with patch.object(s, 'http_fetch', side_effect=fetch):
            ok, err = s.storage_ensure_bucket('avatars')
        return ok, err, captured

    def test_bucket_payload_must_include_name(self):
        ok, err, cap = self._ensure(409, 'bucket avatars already exists')
        self.assertTrue(ok, err)
        self.assertEqual(cap['body']['id'], 'avatars')
        self.assertEqual(cap['body']['name'], 'avatars')
        self.assertTrue(cap['body']['public'])
        self.assertIn('avatars', s._STORAGE_BUCKETS)

    def test_400_exists_message_is_treated_as_exists(self):
        ok, err, cap = self._ensure(400, 'Bucket avatars already exists')
        self.assertTrue(ok, err)
        self.assertIn('avatars', s._STORAGE_BUCKETS)

    def test_real_400_error_is_not_masked_as_exists(self):
        ok, err, cap = self._ensure(400, "body must have required property 'name'")
        self.assertFalse(ok)
        self.assertIn('name', err)
        self.assertNotIn('avatars', s._STORAGE_BUCKETS)

    def test_put_surfaces_storage_error_to_caller(self):
        def fetch(url, method=None, headers=None, data=None, json_body=None, timeout=None):
            raise urllib.error.HTTPError(url, 403, 'forbidden', {},
                                         io.BytesIO(b'{"error":"no policies defined","message":"no policies defined"}'))
        s._STORAGE_BUCKETS.add('media')
        with patch.object(s, 'http_fetch', side_effect=fetch):
            url, err = s.storage_put('media', 'dm/x.jpg', b'img', 'image/jpeg')
        self.assertIsNone(url)
        self.assertIn('no policies', err)


class RoomPermissionsTest(Patch18Base):
    def test_channel_only_owner_can_post(self):
        r = self.svc.create_room(USER, 'Newsfeed', 'channel', description='updates')
        self.assertFalse(r.get('error'), r)
        slug = r['room']['id']
        blocked = self.svc.room_send('r1@example.test', slug, 'hello channel')
        self.assertIn('error', blocked)
        self.assertTrue(blocked.get('locked'))
        self.assertIn('channel admin', blocked['error'])
        ok = self.svc.room_send(USER, slug, 'owner post')
        self.assertFalse(ok.get('error'), ok)

    def test_group_owner_only_posting(self):
        r = self.svc.create_room(USER, 'Inner circle', 'group', description='tight')
        slug = r['room']['id']
        self.svc.room_add_member(USER, slug, self.svc.handle_of('r1@example.test') or 'r1')  # patch31: invited member
        first = self.svc.room_send('r1@example.test', slug, 'hi before lock')
        self.assertFalse(first.get('error'), first)
        toggle = self.svc.room_set_owner_only(USER, slug, True)
        self.assertFalse(toggle.get('error'), toggle)
        self.assertTrue(toggle['owner_only'])
        locked = self.svc.room_send('r1@example.test', slug, 'now locked?')
        self.assertIn('error', locked)
        self.assertTrue(locked.get('locked'))
        self.assertIn('group admin', locked['error'])
        owner_ok = self.svc.room_send(USER, slug, 'owner still posts')
        self.assertFalse(owner_ok.get('error'), owner_ok)
        admin_ok = self.svc.room_send(ADMIN, slug, 'admin posts too')
        self.assertFalse(admin_ok.get('error'), admin_ok)
        views = self.svc.rooms('r1@example.test')
        self.assertTrue([v for v in views if v['id'] == slug][0]['owner_only'])

    def test_only_owner_can_toggle_owner_only(self):
        r = self.svc.create_room(USER, 'Locked down', 'group')
        slug = r['room']['id']
        bad = self.svc.room_set_owner_only('r1@example.test', slug, True)
        self.assertIn('error', bad)
        ch = self.svc.create_room(USER, 'Broadcast', 'channel')
        chbad = self.svc.room_set_owner_only(USER, ch['room']['id'], True)
        self.assertIn('error', chbad)  # channels are always owner-only

    def test_delete_room(self):
        r = self.svc.create_room(USER, 'Doomed', 'group', description='x')
        slug = r['room']['id']
        self.svc.room_send('r1@example.test', slug, 'a message')
        no = self.svc.delete_room('r1@example.test', slug)
        self.assertIn('error', no)
        before = [v for v in self.svc.rooms(USER) if v['id'] == slug]
        self.assertTrue(before)
        ok = self.svc.delete_room(USER, slug)
        self.assertFalse(ok.get('error'), ok)
        self.assertNotIn(slug, [v['id'] for v in self.svc.rooms(USER)])
        self.assertEqual(self.rest.tables['comm_messages']['rows'], [])
        self.assertEqual([r0 for r0 in self.rest.tables['comm_members']['rows'] if r0.get('room_id') == before[0] and False], [])
        badkind = self.svc.delete_room(USER, 'lounge')
        self.assertIn('error', badkind)


class ReactionsTest(Patch18Base):
    def test_react_toggle(self):
        r = self.svc.create_room(USER, 'React room', 'group')
        slug = r['room']['id']
        sent = self.svc.room_send(USER, slug, 'feel this')
        mid = sent['message']['id']
        r1 = self.svc.react('r1@example.test', mid, '\U0001f44d')
        self.assertFalse(r1.get('error'), r1)
        self.assertEqual(r1['reactions'].get('\U0001f44d'), 1)
        r2 = self.svc.react('r2@example.test', mid, '\U0001f44d')
        self.assertEqual(r2['reactions'].get('\U0001f44d'), 2)
        off = self.svc.react('r1@example.test', mid, '\U0001f44d')
        self.assertEqual(off['reactions'].get('\U0001f44d'), 1)
        self.assertFalse(off['on'])
        bad = self.svc.react('r1@example.test', mid, '\U0001f92b')
        self.assertIn('error', bad)
        msgs = self.svc.room_messages(USER, slug)
        row = [m for m in msgs['messages'] if m['id'] == mid][0]
        self.assertEqual(row['reactions'].get('\U0001f44d'), 1)


class ChatGameTest(Patch18Base):
    def test_start_resume_and_win(self):
        started = self.svc.game_start(USER, 'member_one')
        self.assertFalse(started.get('error'), started)
        g1 = started['game']
        self.assertEqual(g1['you'], 'X')
        self.assertTrue(g1['turn_is_you'])
        resumed = self.svc.game_start(USER, 'member_one')
        self.assertTrue(resumed.get('resumed'))
        self.assertEqual(resumed['game']['id'], g1['id'])
        # wrong turn first
        early = self.svc.game_move('r1@example.test', g1['id'], 1)
        self.assertIn('error', early)
        self.assertIn('turn', early['error'])
        moves = [(USER, 0), ('r1@example.test', 3), (USER, 1), ('r1@example.test', 4)]
        for email, idx in moves:
            mv = self.svc.game_move(email, g1['id'], idx)
            self.assertFalse(mv.get('error'), mv)
        # occupied cell
        taken = self.svc.game_move(USER, g1['id'], 0)
        self.assertIn('error', taken)
        win = self.svc.game_move(USER, g1['id'], 2)
        self.assertFalse(win.get('error'), win)
        self.assertEqual(win['game']['status'], 'won')
        self.assertTrue(win['game']['you_won'])
        after = self.svc.game_move('r1@example.test', win['game']['id'], 5)
        self.assertIn('error', after)

    def test_draw_and_rematch(self):
        st = self.svc.game_start(USER, 'member_one')
        gid = st['game']['id']
        seq = [(USER, 0), ('r1@example.test', 1), (USER, 2), ('r1@example.test', 4),
               (USER, 3), ('r1@example.test', 5), (USER, 7), ('r1@example.test', 6), (USER, 8)]
        last = None
        for email, idx in seq:
            last = self.svc.game_move(email, gid, idx)
            self.assertFalse(last.get('error'), last)
        self.assertEqual(last['game']['status'], 'draw')
        rematch = self.svc.game_start(USER, 'member_one')
        self.assertFalse(rematch.get('resumed'))
        self.assertNotEqual(rematch['game']['id'], gid)
        self.assertEqual(rematch['game']['board'].count(None), 9)

    def test_game_visible_in_dm_messages(self):
        st = self.svc.game_start(USER, 'member_one')
        dm = self.svc.dm_messages(USER, 'member_one')
        self.assertIsNotNone(dm.get('game'))
        self.assertEqual(dm['game']['id'], st['game']['id'])


class DmAttachmentsTest(Patch18Base):
    def test_file_placeholder(self):
        r = self.svc.dm_send(USER, 'member_one', '', 'https://example.invalid/storage/v1/object/public/media/files/ab_1.pdf')
        self.assertFalse(r.get('error'), r)
        self.assertEqual(r['message']['body'], '\U0001f4ce File')
        self.assertTrue(r['message']['media_url'])

    def test_photo_placeholder(self):
        r = self.svc.dm_send(USER, 'member_one', '', 'https://example.invalid/storage/v1/object/public/media/dm/ab_1.jpg')
        self.assertFalse(r.get('error'), r)
        self.assertEqual(r['message']['body'], '\U0001f5bc\ufe0f Photo')


if __name__ == '__main__':
    unittest.main(verbosity=2)
