"""patch59: referral / advocate program — codes, coin rewards, milestones,
admin visibility, chat answers, durable SQL."""
import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
SQL = open(os.path.join(ROOT, "supabase_patch59_referrals.sql"), encoding="utf-8").read()

TEST_MAILS = {"advocate@example.com", "friend@example.com", "friend2@example.com", "friend3@example.com"}


def _local_only():
    return mock.patch.object(s, "supabase_all_flags", lambda: {}), \
           mock.patch.object(s, "supabase_get_flag", lambda e: None), \
           mock.patch.object(s, "supabase_upsert_flag", lambda r, block_fields=True: True), \
           mock.patch.object(s, "_referrals_log", lambda row: None), \
           mock.patch.object(s, "audit_log", lambda *a, **k: None), \
           mock.patch.object(s, "notify_admins", lambda *a, **k: None), \
           mock.patch.object(s, "check_tier", lambda e: "free")


def _cleanup():
    try:
        users = s.load_users()
        changed = False
        for em in list(users):
            if em in TEST_MAILS:
                del users[em]
                changed = True
        if changed:
            s.save_users(users)
    except Exception:
        pass


class Marker(unittest.TestCase):
    def test_build_bumped(self):
        self.assertEqual(SRV.count('"patch69-ios15-live"'), 2)

    def test_frontend_pieces(self):
        self.assertIn("oracool_ref", APP)
        self.assertIn("/api/referrals", APP)
        self.assertIn("Refer &amp; earn", APP)
        self.assertIn("st.advocates", APP)
        self.assertIn("ADVOCATE", APP)

    def test_sql(self):
        self.assertIn("add column if not exists ref_code", SQL)
        self.assertIn("create table if not exists public.referrals", SQL)
        self.assertIn("oracool_top_advocates", SQL)


class Engine(unittest.TestCase):
    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_code_create_and_stable(self):
        mocks = _local_only()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6]:
            c1 = s.referral_code("advocate@example.com")
            c2 = s.referral_code("advocate@example.com")
            self.assertTrue(s._REF_RX.match(c1), c1)
            self.assertEqual(c1, c2)
            self.assertIn("/app?ref=" + c1, s.referral_link("advocate@example.com"))

    def test_credit_pays_coins(self):
        mocks = _local_only()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6]:
            s.touch_user("advocate@example.com", ref_code="TESTCOD1")
            base = s.coins_state("advocate@example.com")["balance"]
            r = s.referral_credit("TESTCOD1", "friend@example.com")
            self.assertTrue(r["ok"])
            self.assertEqual(r["coins_awarded"], s.REF_REWARD_COINS)
            self.assertEqual(r["count"], 1)
            self.assertFalse(r["early_access"])
            st = s.coins_state("advocate@example.com")
            self.assertEqual(st["balance"], base + s.REF_REWARD_COINS)

    def test_milestone_three_grants_early_access(self):
        mocks = _local_only()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6]:
            s.touch_user("advocate@example.com", ref_code="TESTCOD2", ref_count=2, ref_coins_earned=500_000)
            r = s.referral_credit("TESTCOD2", "friend@example.com")
            self.assertEqual(r["count"], 3)
            self.assertTrue(r["early_access"])
            self.assertEqual(r["coins_awarded"], s.REF_REWARD_COINS + s.REF_MILESTONES[3])

    def test_self_referral_refused(self):
        mocks = _local_only()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6]:
            s.touch_user("advocate@example.com", ref_code="TESTCOD3")
            self.assertIsNone(s.referral_credit("TESTCOD3", "advocate@example.com"))
            self.assertIsNone(s.referral_credit("bad code!", "friend@example.com"))

    def test_status_shape(self):
        mocks = _local_only()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6]:
            st = s.referral_status("advocate@example.com")
            self.assertTrue(st["ok"])
            self.assertEqual(st["coins_per_referral"], 250_000)
            self.assertEqual(st["early_access_at"], 3)
            self.assertIn("/app?ref=", st["link"])

    def test_chat_answers_referral_ask(self):
        mocks = _local_only()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6]:
            out = s.auto_tools("how do I earn more coins? can I refer a friend?",
                               tier="free", email="advocate@example.com")
            self.assertTrue(any(x["tool"] == "referrals" for x in out), out)

    def test_signup_signature_takes_ref(self):
        import inspect
        self.assertIn("ref", inspect.signature(s.auth_signup).parameters)

    def test_admin_payload_reports_advocates(self):
        mocks = _local_only()
        with mock.patch.object(s, "supabase_auth_users", lambda: []):
            with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6]:
                s.touch_user("advocate@example.com", ref_code="TESTCOD4",
                             ref_count=4, ref_coins_earned=2_000_000, early_access=True)
                p = s.admin_users_payload("danielonakoya19@gmail.com")
                self.assertGreaterEqual(p["stats"]["advocates"], 1)
                row = next(u for u in p["users"] if u["email"] == "advocate@example.com")
                self.assertEqual(row["ref_count"], 4)
                self.assertTrue(row["early_access"])


if __name__ == "__main__":
    unittest.main()
