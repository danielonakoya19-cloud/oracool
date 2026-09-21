"""Patch 15 — community chat, OraCool numbers & usernames, groups/channels,
friends-by-number, member reports and the AI moderator (Supabase-table backed).

Offline: the Supabase REST tables are emulated by FakeRest (a mini PostgREST),
and the model is mocked. Verifies that
  * signup takes a unique username and issues a unique 10-digit OraCool number,
    reserved / taken / malformed usernames are refused,
  * members are shown by username + number only — no e-mail and no IP address
    ever leaves the server,
  * rooms/DMs work with rate limits and unread counts,
  * members can create groups and channels (public and private),
  * a member can find another member by OraCool number and add them as a friend,
  * a report needs a written reason; self/admin reports are refused,
  * the moderator opens a case only at THREE distinct credible reporters (young
    accounts and serial false reporters do not count),
  * only the moderator suspends — and only on an evidenced 'block' verdict with
    confidence >= 0.75; low confidence / warn / dismiss / model failure / no
    evidence never suspend,
  * dismissals lower reporter credibility; admins can confirm escalations and
    overturn suspensions,
  * while the tables do not exist yet the app degrades to 'setup_required',
  * suspended members are locked out of the community like everything else.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('oracool_test_server_p15', ROOT / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
import community  # noqa: E402


def _post(port, path, body):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def _as_str(v):
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    return str(v)


class FakeRest:
    """Mini PostgREST emulator for the Patch-15 community tables."""

    def __init__(self):
        self.tables = {}
        self.reset()

    def reset(self):
        self.tables = {
            'comm_profiles': {'rows': [], 'next_number': 2000000000},
            'comm_rooms': {'rows': [], 'next_id': 1},
            'comm_members': {'rows': []},
            'comm_messages': {'rows': [], 'next_id': 1},
            'comm_reports': {'rows': [], 'next_id': 1},
            'comm_cases': {'rows': [], 'next_id': 1},
            'comm_contacts': {'rows': []},
            'comm_read_state': {'rows': []},
        }
        for slug, rid in (('lounge', 'rm-lounge'), ('markets', 'rm-markets'), ('help', 'rm-help')):
            self.tables['comm_rooms']['rows'].append(
                {'id': rid, 'slug': slug, 'name': slug.title(), 'kind': 'room', 'description': '',
                 'is_public': True, 'owner_email': None, 'created_at': '2026-01-01T00:00:00+00:00'})

    # -- matching ------------------------------------------------------------
    @staticmethod
    def _match(row, conds):
        for k, v in conds.items():
            val = row.get(k)
            if v.startswith('in.('):
                if _as_str(val) not in v[4:-1].split(','):
                    return False
            elif v == 'is.null':
                if val not in (None, ''):
                    return False
            elif v.startswith('eq.'):
                if _as_str(val) != v[3:]:
                    return False
            elif v.startswith('gt.'):
                try:
                    if not (float(val or 0) > float(v[3:])):
                        return False
                except (TypeError, ValueError):
                    return False
            else:
                return False
        return True

    @staticmethod
    def _skey(key):
        def k(r):
            v = r.get(key)
            try:
                return (0, float(v))
            except (TypeError, ValueError):
                return (1, str(v or ''))
        return k

    def _conflict_key(self, table, cols, row):
        return tuple(row.get(c) for c in cols)

    # -- REST ----------------------------------------------------------------
    def rest(self, method, path, body=None, prefer=None):
        table, _, query = path.partition('?')
        t = self.tables.get(table)
        if t is None:
            return 400, {'code': 'PGRST205', 'message': 'Could not find the table \u0027%s\u0027 in the schema cache' % table}
        parts = [p for p in query.split('&') if p]
        conds, order, limit = {}, None, 1000
        on_conflict = None
        for p in parts:
            k, _, v = p.partition('=')
            if k == 'order':
                order = v
            elif k == 'limit':
                limit = int(v)
            elif k == 'on_conflict':
                on_conflict = v
            elif k not in ('select',):
                conds[k] = v
        rows = t['rows']
        sel = [r for r in rows if self._match(r, conds)]
        if order:
            key, _, direction = order.partition('.')
            sel = sorted(sel, key=self._skey(key), reverse=(direction == 'desc'))
        sel = sel[:limit]

        if method == 'GET':
            return 200, [dict(r) for r in sel]

        if method == 'POST':
            new_rows = body if isinstance(body, list) else [body]
            out = []
            for nr in new_rows:
                nr = dict(nr)
                if table == 'comm_profiles':
                    ts = '2026-09-20T00:00:00+00:00'
                    nr.setdefault('created_at', ts); nr.setdefault('updated_at', ts)
                    nr.setdefault('bio', ''); nr.setdefault('display_name', '')
                    nr.setdefault('credibility', 'normal')
                    if nr.get('oracool_number') in (None, ''):
                        t['next_number'] += 1
                        nr['oracool_number'] = t['next_number']
                    if table == 'comm_profiles':
                        if any(_as_str(r.get('username')).lower() == _as_str(nr.get('username')).lower() for r in rows):
                            return 409, {'code': '23505', 'message': 'duplicate key value violates unique constraint "comm_profiles_username_uq"'}
                        if any(r.get('email') == nr.get('email') for r in rows):
                            return 409, {'code': '23505', 'message': 'duplicate key value violates unique constraint "comm_profiles_pkey"'}
                if table == 'comm_rooms':
                    if 'id' not in nr or not nr.get('id'):
                        t['next_id'] += 1
                        nr['id'] = 'uuid-%04d' % t['next_id']
                    nr.setdefault('created_at', '2026-09-20T00:00:00+00:00')
                    nr.setdefault('is_public', True)
                    if any(r.get('slug') == nr.get('slug') for r in rows):
                        if not on_conflict:
                            return 409, {'code': '23505', 'message': 'duplicate key value violates unique constraint "comm_rooms_slug_key"'}
                if table in ('comm_messages', 'comm_reports', 'comm_cases'):
                    nr['id'] = t['next_id']; t['next_id'] += 1
                    nr.setdefault('created_at', '2026-09-20T00:00:00+00:00')
                if table in ('comm_members',):
                    nr.setdefault('role', 'member'); nr.setdefault('joined_at', '2026-09-20T00:00:00+00:00')
                if table == 'comm_contacts':
                    nr.setdefault('added_at', '2026-09-20T00:00:00+00:00')
                if table == 'comm_read_state':
                    nr.setdefault('last_read_id', 0); nr.setdefault('updated_at', '2026-09-20T00:00:00+00:00')

                if on_conflict:
                    cols = on_conflict.split(',')
                    hit = next((r for r in rows if all(_as_str(r.get(c)) == _as_str(nr.get(c)) for c in cols)), None)
                    if hit is not None:
                        if prefer and 'merge-duplicates' in prefer:
                            hit.update(nr)
                        return 201, [dict(hit)] if (prefer and 'return=representation' in (prefer or '')) else []
                rows.append(nr)
                out.append(nr)
            return 201, [dict(r) for r in out]

        if method == 'PATCH':
            updated = []
            for r in rows:
                if self._match(r, conds):
                    r.update(body or {})
                    updated.append(r)
            return 200, [dict(r) for r in updated]

        if method == 'DELETE':
            keep = [r for r in rows if not self._match(r, conds)]
            t['rows'] = keep
            return 204, {}
        return 405, {'message': 'bad method'}


def _not_ready_rest(method, path, body=None, prefer=None):
    return 400, {'code': 'PGRST205', 'message': 'Could not find the table \u0027public.comm_messages\u0027 in the schema cache'}


BLOCK_VERDICT = json.dumps({"verdict": "block", "confidence": 0.92, "category": "fraud_or_scam",
                            "summary": "The member repeatedly solicits payments for a fake investment scheme.",
                            "evidence": ["send 50k and I double it in 2 hours"]})
ADMIN = 'owner@example.test'


def _no_ip_keys(obj, path='$'):
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ('ip', 'client_ip', 'remote_addr', 'ip_address', 'address'):
                bad.append(path + '.' + str(k))
            bad += _no_ip_keys(v, path + '.' + str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += _no_ip_keys(v, path + '[%d]' % i)
    return bad


class FakeCloud:
    def __init__(self):
        self.flags = {}

    def get_flag(self, email):
        e = (email or '').lower()
        return dict(self.flags[e]) if e in self.flags else None

    def upsert_flag(self, rec, block_fields=True):
        email = rec['email'].lower()
        row = self.flags.setdefault(email, {'email': email, 'blocked': False})
        row['last_seen'] = rec.get('last_seen', '')
        if block_fields:
            for f in ('blocked', 'block_reason', 'blocked_by', 'blocked_at'):
                row[f] = rec.get(f)
        return True


class Patch15Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cloud = FakeCloud()
        self.rest = FakeRest()
        self.llm_calls = []
        self.llm_reply = (BLOCK_VERDICT, None)

        def fake_llm(messages, temperature=0.4, max_tokens=800):
            self.llm_calls.append(messages)
            return self.llm_reply

        self.patches = [patch.object(s, 'DATA_DIR', self.tmp.name), patch.object(s, 'BASE_DIR', self.tmp.name),
                        patch.dict(s.KEYS, {}, clear=True), patch.object(s, '_AUTH_CACHE', {}),
                        patch.dict(s._BLOCK_CACHE, {}, clear=True), patch.object(s, 'audit_log'),
                        patch.object(s, 'emit_event'), patch.object(s, 'notify_admins'),
                        patch.object(s, 'llm_complete', side_effect=fake_llm),
                        patch.object(s, '_COMMUNITY_SERVICE', None),
                        patch.object(s, 'community_rest', side_effect=self.rest.rest),
                        patch.object(s, 'supabase_get_flag', side_effect=self.cloud.get_flag),
                        patch.object(s, 'supabase_upsert_flag', side_effect=self.cloud.upsert_flag)]
        for p in self.patches:
            p.start()
        s.KEYS.update(SUPABASE_URL='https://example.invalid', SUPABASE_SERVICE_KEY='svc',
                      ADMIN_EMAILS=[ADMIN], JWT_SECRET='t')
        self.svc = s.community_service()
        self.svc.async_reviews = False
        self.svc.last_post.clear()
        old = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 7200))
        users = {}
        for e in ('scammer@example.test', 'r1@example.test', 'r2@example.test', 'r3@example.test',
                  'r4@example.test', ADMIN):
            users[e] = {'email': e, 'created': old, 'last_seen': old}
        s.save_users(users)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def serve(self):
        server = s.ThreadingHTTPServer(('127.0.0.1', 0), s.Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
        return server, t

    def _talk(self, email, room, text):
        self.svc.last_post.pop(email, None)
        r = self.svc.room_send(email, room, text)
        self.assertTrue(r.get('ok'), r)
        return r['message']['id']

    def _three_reports(self, reported_username, reporters=('r1@example.test', 'r2@example.test', 'r3@example.test'), mid=None):
        out = None
        for r in reporters:
            out = self.svc.report(r, reported_username, 'He asked me to send money and promised to double it.', [mid] if mid else None)
            self.assertTrue(out.get('ok'), out)
        return out

    # ---- identities: username + OraCool number --------------------------------
    def test_username_and_number_are_unique_per_member(self):
        a = self.svc.ensure_profile('r1@example.test', username='daniel_o', display_name='Daniel')
        b = self.svc.ensure_profile('r2@example.test', username='ami_k', display_name='Ami')
        for row in (a, b):
            self.assertGreaterEqual(row['oracool_number'], 2000000000)
            self.assertLessEqual(row['oracool_number'], 2999999999)
        self.assertNotEqual(a['oracool_number'], b['oracool_number'])
        # duplicate usernames can never be granted
        with self.assertRaises(community.UsernameTaken):
            self.svc.ensure_profile('r3@example.test', username='daniel_o')
        # auto username for a member who signed up without one
        c = self.svc.ensure_profile('r4@example.test')
        self.assertTrue(c['username'].startswith('ora_'))
        # lookup by number & username
        row = self.svc.profile_by_number(str(a['oracool_number']))
        self.assertEqual(row['email'], 'r1@example.test')
        self.assertEqual(self.svc.profile_by_username('Daniel_O')['email'], 'r1@example.test')
        self.assertIsNone(self.svc.profile_by_number('999999'))
        # reserved + malformed refused via update_profile
        self.assertIn('error', self.svc.update_profile('r2@example.test', 'ab'))
        self.assertIn('reserved', self.svc.update_profile('r2@example.test', 'admin')['error'])
        self.assertTrue(self.svc.update_profile('r2@example.test', 'crypto_king')['ok'])
        self.assertEqual(self.svc.handle_of('r2@example.test'), 'crypto_king')
        self.assertIn('taken', self.svc.update_profile('r4@example.test', 'crypto_king')['error'])
        self.assertEqual(self.svc.email_of('@Crypto_King'), 'r2@example.test')

    def test_signup_takes_username_and_issues_number(self):
        calls = []

        def fake_fetch(url, method='GET', headers=None, json_body=None, data=None, timeout=25):
            if method == 'POST' and '/auth/v1/admin/users' in url:
                calls.append(json_body)
                return 200, json.dumps({'id': 'uid-1', 'user': {'id': 'uid-1', 'email': json_body['email']}}).encode(), 'application/json'
            return 200, b'{}', 'application/json'

        with patch.object(s, 'http_fetch', side_effect=fake_fetch), \
             patch.object(s, 'supabase_auth', return_value={'status': 200, 'data': {'access_token': 'tok'}}), \
             patch.object(s, 'check_subscription', return_value=''):
            self.assertIn('reserved', s.auth_signup('newu@example.test', 'Sup3r$ecret9', 'New', 'admin')['error'])
            self.assertIn('error', s.auth_signup('newu@example.test', 'Sup3r$ecret9', 'New', 'x'))
            # duplicate: the username is already taken by r1
            self.svc.ensure_profile('r1@example.test', username='daniel_o')
            r = s.auth_signup('newu@example.test', 'Sup3r$ecret9', 'New', 'daniel_o')
            self.assertIn('already taken', r['error']); self.assertEqual(calls, [])
            r = s.auth_signup('newu@example.test', 'Sup3r$ecret9', 'New', 'fresh_user')
            self.assertEqual(r['status'], 200)
            self.assertEqual(r['community']['username'], 'fresh_user')
            self.assertGreaterEqual(r['community']['number'], 2000000000)
            self.assertEqual(calls[-1]['user_metadata']['username'], 'fresh_user')
            self.assertEqual(self.svc._prof('newu@example.test')['username'], 'fresh_user')

    # ---- privacy ---------------------------------------------------------------
    def test_usernames_and_numbers_only_never_emails_or_ips(self):
        self.svc.ensure_profile('scammer@example.test', username='scamface')
        mid = self._talk('scammer@example.test', 'lounge', 'hello everyone')
        h1 = self.svc.handle_of('r1@example.test') or self.svc.ensure_profile('r1@example.test', username='r1')['username']
        self.svc.dm_send('scammer@example.test', h1, 'psst')
        blobs = [self.svc.me('r1@example.test'), self.svc.room_messages('r1@example.test', 'lounge'),
                 self.svc.people('r1@example.test'), self.svc.dm_threads('r1@example.test'),
                 self.svc.dm_messages('r1@example.test', 'scamface'), self.svc.friends('r1@example.test'),
                 {'rooms': self.svc.rooms('r1@example.test')}]
        for b in blobs:
            self.assertNotIn('@example.test', json.dumps(b), b)
            self.assertEqual(_no_ip_keys(b), [], json.dumps(b)[:200])
        m = self.svc.room_messages('r1@example.test', 'lounge')['messages'][0]
        self.assertEqual(m['username'], 'scamface')
        self.assertGreaterEqual(m['number'], 2000000000)

    # ---- rooms / DMs -----------------------------------------------------------
    def test_rooms_and_dms_with_rate_limit_and_unread(self):
        mid = self._talk('r1@example.test', 'markets', 'BTC looks strong today')
        self.assertIn('too quickly', self.svc.room_send('r1@example.test', 'markets', 'again')['error'])
        self.assertIn('error', self.svc.room_send('r1@example.test', 'nope', 'x'))
        page = self.svc.room_messages('r2@example.test', 'markets')
        self.assertEqual([m['id'] for m in page['messages']], [mid]); self.assertFalse(page['messages'][0]['mine'])
        self.assertEqual(self.svc.room_messages('r2@example.test', 'markets', after_id=mid)['messages'], [])
        h2 = self.svc.ensure_profile('r2@example.test', username='r2')['username']
        self.svc.last_post.clear()
        self.assertTrue(self.svc.dm_send('r1@example.test', h2, 'hi there')['ok'])
        th = self.svc.dm_threads('r2@example.test')['threads']
        self.assertEqual(th[0]['unread'], 1); self.assertEqual(self.svc.me('r2@example.test')['unread_dm'], 1)
        msgs = self.svc.dm_messages('r2@example.test', self.svc.handle_of('r1@example.test') or 'r1')['messages']
        self.assertEqual(msgs[0]['body'], 'hi there'); self.assertEqual(self.svc.me('r2@example.test')['unread_dm'], 0)
        self.assertIn('error', self.svc.dm_send('r1@example.test', self.svc.handle_of('r1@example.test') or 'r1', 'me'))
        self.assertIn('error', self.svc.dm_send('r1@example.test', 'OraCool Moderator', 'hello?'))

    # ---- groups & channels ------------------------------------------------------
    def test_members_can_create_groups_and_channels(self):
        self.svc.ensure_profile('r1@example.test', username='rr1')
        r = self.svc.create_room('r1@example.test', 'Crypto Watchers', 'group', 'watching charts', True)
        self.assertTrue(r['ok'], r); self.assertEqual(r['room']['id'], 'crypto-watchers')
        self.svc.ensure_profile('r2@example.test', username='rr2')
        rooms2 = self.svc.rooms('r2@example.test')
        self.assertIn('crypto-watchers', [x['id'] for x in rooms2])
        # anyone can join a public group and chat in it
        self.svc.last_post.clear()
        self.assertTrue(self.svc.room_send('r2@example.test', 'crypto-watchers', 'gm all')['ok'])
        self.assertTrue(self.svc.room_send('r1@example.test', 'crypto-watchers', 'gm back')['ok'])
        seen = self.svc.room_messages('r1@example.test', 'crypto-watchers')['messages']
        self.assertEqual([m['username'] for m in seen], ['rr2', 'rr1'])
        # duplicate names get a suffix
        self.svc.ensure_profile('r3@example.test', username='rr3')
        r2 = self.svc.create_room('r3@example.test', 'Crypto Watchers', 'group', '', True)
        self.assertEqual(r2['room']['id'], 'crypto-watchers-2')
        # channels
        ch = self.svc.create_room('r3@example.test', 'News channel', 'channel', 'macro news', True)
        self.assertTrue(ch['ok']); self.assertEqual(ch['room']['kind'], 'channel')
        # private group: invisible + unjoinable for outsiders
        pr = self.svc.create_room('r1@example.test', 'Secret Crew', 'group', '', False)
        self.assertTrue(pr['ok'])
        self.assertNotIn(pr['room']['id'], [x['id'] for x in self.svc.rooms('r2@example.test')])
        self.assertIn('private', self.svc.join_room('r2@example.test', pr['room']['id'])['error'])
        self.svc.last_post.clear()
        self.assertIn('private', self.svc.room_send('r2@example.test', pr['room']['id'], 'what is this?')['error'])
        self.assertTrue(self.svc.join_room('r1@example.test', pr['room']['id'])['ok'])
        self.svc.last_post.clear()
        self.assertTrue(self.svc.room_send('r1@example.test', pr['room']['id'], 'hello crew')['ok'])
        # kind must be group or channel; names too short are refused
        self.assertIn('error', self.svc.create_room('r1@example.test', 'x', 'room'))
        self.assertIn('error', self.svc.create_room('r1@example.test', 'x', 'group'))

    # ---- friends by OraCool number ---------------------------------------------
    def test_lookup_and_add_friend_by_oracool_number(self):
        p1 = self.svc.ensure_profile('r1@example.test', username='daniel_o')
        p2 = self.svc.ensure_profile('r2@example.test', username='ami_k')
        n1, n2 = p1['oracool_number'], p2['oracool_number']
        found = self.svc.lookup('r2@example.test', n1)
        self.assertEqual(found['profile']['username'], 'daniel_o')
        self.assertNotIn('email', json.dumps(found))
        self.assertFalse(found['is_friend'])
        self.assertIn('No member', self.svc.lookup('r2@example.test', '12345')['error'])
        r = self.svc.add_friend('r2@example.test', n1)
        self.assertTrue(r['ok']); self.assertEqual(r['profile']['username'], 'daniel_o')
        fr = self.svc.friends('r2@example.test')['friends']
        self.assertEqual([f['username'] for f in fr], ['daniel_o'])
        self.assertTrue(self.svc.lookup('r2@example.test', n1)['is_friend'])
        # own number / unknown number
        self.assertIn('own number', self.svc.add_friend('r2@example.test', n2)['error'])
        self.assertIn('No member', self.svc.add_friend('r2@example.test', '2999999999')['error'])
        # idempotent
        self.assertTrue(self.svc.add_friend('r2@example.test', n1)['ok'])
        self.assertEqual(len(self.svc.friends('r2@example.test')['friends']), 1)

    # ---- reports: rules and the three-reporter threshold ------------------------
    def test_report_rules(self):
        self.svc.ensure_profile('scammer@example.test', username='scamface')
        self.svc.ensure_profile(ADMIN, username='owner')
        h = self.svc.handle_of('scammer@example.test')
        self.assertIn('error', self.svc.report('scammer@example.test', h, 'reporting myself for fun', None))
        self.assertIn('error', self.svc.report('r1@example.test', self.svc.handle_of(ADMIN), 'admin is mean to me', None))
        self.assertIn('error', self.svc.report('r1@example.test', h, 'bad', None))
        r = self.svc.report('r1@example.test', h, 'He is running an obvious investment scam.', None)
        self.assertEqual((r['reports'], r['review_started']), (1, False))
        r = self.svc.report('r1@example.test', h, 'Updated: he keeps asking for money.', None)
        self.assertEqual(r['reports'], 1)
        r = self.svc.report('r2@example.test', h, 'Same here, asked me to pay for a fake signal group.', None)
        self.assertEqual((r['reports'], r['review_started']), (2, False))
        self.assertEqual(self.llm_calls, []); self.assertFalse(s.is_blocked('scammer@example.test'))

    def test_third_credible_report_triggers_ai_review_which_suspends_on_evidence(self):
        self.svc.ensure_profile('scammer@example.test', username='scamface')
        mid = self._talk('scammer@example.test', 'lounge', 'send 50k and I double it in 2 hours, DM me')
        out = self._three_reports('scamface', mid=mid)
        self.assertTrue(out['review_started']); self.assertEqual(len(self.llm_calls), 1)
        dossier = json.loads(self.llm_calls[0][1]['content'].split('\n', 1)[1])
        self.assertNotIn('@example.test', json.dumps(dossier))
        self.assertEqual(dossier['reported_member_room_messages'][0]['text'], 'send 50k and I double it in 2 hours, DM me')
        self.assertEqual(dossier['reports'][0]['quoted_messages'], ['send 50k and I double it in 2 hours, DM me'])
        self.assertTrue(s.is_blocked('scammer@example.test'))
        row = self.cloud.flags['scammer@example.test']
        self.assertEqual(row['blocked_by'], 'oracool-ai-moderator'); self.assertIn('fraud or scam', row['block_reason'])
        case = self.svc.admin_overview()['cases'][0]
        self.assertEqual((case['status'], case['verdict'], case['category']), ('blocked', 'block', 'fraud_or_scam'))
        th = self.svc.dm_threads('r1@example.test')['threads']
        self.assertTrue(th and th[0]['mod'] and 'suspended' in th[0]['preview'])
        # a 4th report afterwards starts from zero and does not re-open during the cool-down
        r = self.svc.report('r4@example.test', 'scamface', 'Also scammed me last week, please look.', None)
        self.assertEqual((r['reports'], r['review_started']), (1, False)); self.assertEqual(len(self.llm_calls), 1)

    def test_low_confidence_verdict_escalates_to_admin_instead_of_blocking(self):
        self.llm_reply = (json.dumps({"verdict": "block", "confidence": 0.5, "category": "fraud_or_scam",
                                      "summary": "Possibly a scam.", "evidence": []}), None)
        self.svc.ensure_profile('scammer@example.test', username='scamface')
        self._talk('scammer@example.test', 'lounge', 'great returns guaranteed')
        self._three_reports('scamface')
        self.assertFalse(s.is_blocked('scammer@example.test'))
        case = self.svc.admin_overview()['cases'][0]
        self.assertEqual((case['status'], case['action']), ('pending_admin', 'escalated'))
        self.assertTrue(self.svc.admin_confirm(case['id'], ADMIN)['ok'])
        self.assertTrue(s.is_blocked('scammer@example.test'))
        self.assertEqual(self.cloud.flags['scammer@example.test']['blocked_by'], ADMIN)
        self.assertTrue(self.svc.admin_overturn(case['id'], ADMIN)['ok'])
        self.assertFalse(s.is_blocked('scammer@example.test'))

    def test_warn_and_dismiss_never_block_and_dismiss_costs_reporter_credibility(self):
        self.svc.ensure_profile('scammer@example.test', username='scamface')
        self._talk('scammer@example.test', 'lounge', 'your trading strategy is garbage lol')
        self.llm_reply = (json.dumps({"verdict": "warn", "confidence": 0.8, "category": "none",
                                      "summary": "Rude but not illegal.", "evidence": []}), None)
        self._three_reports('scamface')
        self.assertFalse(s.is_blocked('scammer@example.test'))
        self.assertEqual(self.svc.admin_overview()['cases'][0]['status'], 'warned')
        warned = self.svc.dm_threads('scammer@example.test')['threads']
        self.assertTrue(warned and warned[0]['mod'] and 'Warning' in warned[0]['preview'])
        self.llm_reply = (json.dumps({"verdict": "dismiss", "confidence": 0.9, "category": "none",
                                      "summary": "Nothing wrong.", "evidence": []}), None)
        targets = ['t1@example.test', 't2@example.test', 't3@example.test']
        users = s.load_users()
        for t in targets:
            users[t] = {'email': t, 'created': '2024-01-01 00:00:00'}
        s.save_users(users)
        for i, t in enumerate(targets):
            self.svc.ensure_profile(t, username='target%d' % (i + 1))
            self._talk(t, 'help', 'how do I change my username?')
            self._three_reports('target%d' % (i + 1))
            self.assertFalse(s.is_blocked(t))
        reports = self.rest.tables['comm_reports']['rows']
        unfounded = [r for r in reports if r['reporter_email'] == 'r1@example.test' and r['status'] == 'unfounded']
        self.assertEqual(len(unfounded), 3)
        self.svc.ensure_profile('t4@example.test', username='target4')
        r = self.svc.report('r1@example.test', 'target4', 'This person is definitely a criminal, trust me.', None)
        self.assertFalse(r['counts']); self.assertEqual(r['reports'], 0); self.assertIn('unfounded', r['note'])

    def test_new_accounts_do_not_count_toward_the_threshold(self):
        users = s.load_users()
        users['baby@example.test'] = {'email': 'baby@example.test', 'created': time.strftime('%Y-%m-%d %H:%M:%S')}
        s.save_users(users)
        self.svc.ensure_profile('scammer@example.test', username='scamface')
        self._talk('scammer@example.test', 'lounge', 'send 50k and I double it')
        self.svc.report('r1@example.test', 'scamface', 'Scam offer, asked me to send money.', None)
        self.svc.report('r2@example.test', 'scamface', 'Scam offer, asked me to send money.', None)
        r = self.svc.report('baby@example.test', 'scamface', 'Scam offer, asked me to send money.', None)
        self.assertFalse(r['counts']); self.assertEqual(r['reports'], 2); self.assertFalse(r['review_started'])
        self.assertEqual(self.llm_calls, []); self.assertFalse(s.is_blocked('scammer@example.test'))

    def test_model_failure_or_no_evidence_never_suspends(self):
        self.svc.ensure_profile('scammer@example.test', username='scamface')
        self._three_reports('scamface')
        self.assertEqual(self.llm_calls, []); self.assertFalse(s.is_blocked('scammer@example.test'))
        c = self.svc.admin_overview()['cases'][0]
        self.assertEqual((c['status'], c['action']), ('pending_admin', 'insufficient_evidence'))
        # model down → pending admin, nobody blocked
        self.rest.reset(); self.svc.store._ready = (0, False)
        self.llm_reply = (None, 'AI provider error 503')
        self.svc.ensure_profile('scammer@example.test', username='scamface')
        self._talk('scammer@example.test', 'lounge', 'send 50k and I double it')
        self._three_reports('scamface')
        self.assertEqual(len(self.llm_calls), 1); self.assertFalse(s.is_blocked('scammer@example.test'))
        self.assertEqual(self.svc.admin_overview()['cases'][0]['status'], 'pending_admin')
        # garbage from the model is treated the same way
        self.rest.reset(); self.svc.store._ready = (0, False)
        self.llm_reply = ('I think you should block him!!', None)
        self.svc.ensure_profile('scammer@example.test', username='scamface')
        self._talk('scammer@example.test', 'lounge', 'send 50k and I double it')
        self._three_reports('scamface')
        self.assertFalse(s.is_blocked('scammer@example.test'))

    def test_advisory_review_never_suspends_but_returns_verdict(self):
        self.svc.ensure_profile('scammer@example.test', username='scamface')
        self._talk('scammer@example.test', 'lounge', 'send 50k and I double it')
        out = self.svc.admin_advisory_review('scammer@example.test')
        self.assertEqual(out.get('status'), 'advisory')
        self.assertEqual(out.get('verdict'), 'block')
        self.assertFalse(s.is_blocked('scammer@example.test'))

    def test_parse_verdict(self):
        pv = community.Service.parse_verdict
        self.assertEqual(pv('```json\n' + BLOCK_VERDICT + '\n```')['category'], 'fraud_or_scam')
        self.assertIsNone(pv('no json here')); self.assertIsNone(pv(json.dumps({'verdict': 'execute'})))
        v = pv(json.dumps({'verdict': 'block', 'confidence': '7', 'category': 'being annoying', 'summary': 'x'}))
        self.assertEqual((v['confidence'], v['category']), (1.0, 'none'))

    # ---- setup-required degradation ---------------------------------------------
    def test_setup_required_until_the_tables_exist(self):
        with patch.object(s, 'community_rest', side_effect=_not_ready_rest), \
             patch.object(s, '_COMMUNITY_SERVICE', None):
            self.assertTrue(s.community_route('me', {'email': 'r1@example.test'}).get('setup_required'))
            self.assertTrue(s.community_route('rooms/create', {'email': 'r1@example.test', 'name': 'x'}).get('setup_required'))
            with self.assertRaises(community.CommunitySetup):
                s.community_service().admin_overview()
            self.assertTrue(s.community_service().setup_status()['ready'] is False)

    # ---- chat prompt brief --------------------------------------------------------
    def test_ai_prompt_brief_contains_the_number(self):
        self.svc.ensure_profile('r1@example.test', username='daniel_o')
        brief = self.svc.brief_for('r1@example.test')
        self.assertIn('OraCool number', brief)
        self.assertIn(str(self.svc.number_of('r1@example.test')), brief)
        self.assertIn('daniel_o', brief)
        self.assertEqual(self.svc.brief_for('nobody@example.test'), '')

    # ---- HTTP: identity + lockout ---------------------------------------------
    def test_http_requires_sign_in_and_locks_out_suspended_members(self):
        server, t = self.serve()
        try:
            port = server.server_port
            code, body = _post(port, '/api/community/me', {})
            self.assertEqual(code, 401); self.assertTrue(body.get('auth_required'))
            tok = s.sign_jwt({'sub': 'r1@example.test', 'tier': 'pro', 'exp': time.time() + 600}, 't')
            code, body = _post(port, '/api/community/me', {'token': tok})
            self.assertEqual(code, 200)
            self.assertTrue(body['profile']['username'].startswith('ora_'))
            self.assertGreaterEqual(body['profile']['number'], 2000000000)
            code, body = _post(port, '/api/community/room/send', {'token': tok, 'room': 'lounge', 'body': 'hello from http'})
            self.assertEqual(code, 200); self.assertTrue(body['ok'])
            s.block_user('r1@example.test', True, 'test', ADMIN)
            for path in ('/api/community/me', '/api/community/room/send', '/api/community/dm/send', '/api/community/report'):
                code, body = _post(port, path, {'token': tok, 'room': 'lounge', 'body': 'x'})
                self.assertEqual(code, 403, path); self.assertTrue(body.get('suspended'), path)
            code, body = _post(port, '/api/admin/moderation',
                               {'token': s.sign_jwt({'sub': 'r2@example.test', 'tier': 'pro', 'exp': time.time() + 600}, 't')})
            self.assertEqual(code, 403)
            atok = s.sign_jwt({'sub': ADMIN, 'admin': True, 'tier': 'enterprise', 'exp': time.time() + 600}, 't')
            code, body = _post(port, '/api/admin/moderation', {'token': atok})
            self.assertEqual(code, 200); self.assertEqual(body['stats']['threshold'], 3)
            with urllib.request.urlopen('http://127.0.0.1:%d/api/health' % port, timeout=3) as r:
                self.assertEqual(json.loads(r.read())['build'], 'patch20-no-twilio')
        finally:
            server.shutdown(); server.server_close(); t.join()


if __name__ == '__main__':
    unittest.main()
