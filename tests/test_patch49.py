"""patch49: plan gates honour the signed-in session (Enterprise/creator never told 'you are on Free'); the model gets
the real date & time in the user's timezone and is forbidden from inventing 'updates'."""
import os, sys, io, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


def _handler():
    h = s.Handler.__new__(s.Handler)
    h.headers = {}; h.wfile = io.BytesIO(); h.sent = []
    h._send_json = lambda obj, status=200: h.sent.append((status, obj))
    return h


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch56-no-fake-kali"'), 2)
        self.assertNotIn("patch48b-creator", SRV)


class TierGate(unittest.TestCase):
    def test_enterprise_session_passes_without_pro_jwt(self):
        h = _handler()
        s.tier_cache_clear()
        with mock.patch.object(s, "request_identity", lambda handler, body, require_supabase=False: "danielonakoya19@gmail.com"), \
             mock.patch.object(s, "is_blocked", lambda e: False), mock.patch.object(s, "key", lambda n, d="": ""), \
             mock.patch.object(s, "admin_emails", lambda: ["danielonakoya19@gmail.com"]):
            tier = h._require_tier({"access_token": "supabase-jwt", "phone": "07052405515"}, "starter")
        self.assertEqual(tier, "enterprise"); self.assertEqual(h.sent, [])
        s.tier_cache_clear()

    def test_free_session_still_locked_with_honest_wording(self):
        h = _handler()
        s.tier_cache_clear()
        with mock.patch.object(s, "request_identity", lambda handler, body, require_supabase=False: "free.user@example.invalid"), \
             mock.patch.object(s, "is_blocked", lambda e: False), mock.patch.object(s, "_check_tier_live", lambda e: "free"):
            tier = h._require_tier({"access_token": "supabase-jwt"}, "starter")
        self.assertIsNone(tier); self.assertEqual(h.sent[0][0], 402); self.assertIn("Starter", h.sent[0][1]["message"])
        s.tier_cache_clear()

    def test_admin_jwt_without_admin_claim_is_enterprise(self):
        h = _handler()
        with mock.patch.object(s.Handler, "_auth", lambda self, body: {"sub": "danielonakoya19@gmail.com", "tier": "pro"}), \
             mock.patch.object(s, "is_blocked", lambda e: False), mock.patch.object(s, "is_admin", lambda e: e == "danielonakoya19@gmail.com"):
            self.assertEqual(h._require_tier({"token": "x"}, "ultra"), "enterprise")


class Clock(unittest.TestCase):
    def test_time_context(self):
        line = s.time_context("Africa/Lagos")
        self.assertIn("Africa/Lagos", line); self.assertIn("UTC+01:00", line); self.assertIn("never invent news", line)
        self.assertIn("2026", line)  # server clock, not training data
        line2 = s.time_context("Not/AZone", "2026-09-27T20:29:38+01:00")
        self.assertIn("20:29", line2)
        self.assertIn("UTC now", s.time_context())

    def test_wired_into_chat_and_client(self):
        self.assertIn('identity_prompt_head(chat_email, _lu_txt) + "\\n\\n" + time_context(body.get("tz"), body.get("local_time"))', SRV)
        self.assertIn("tz:_tz, local_time:_lt", APP)
        self.assertIn("Intl.DateTimeFormat().resolvedOptions().timeZone", APP)
        self.assertIn("const _nowLine=", APP)


if __name__ == "__main__":
    unittest.main()
