"""Patch 20 — Twilio removed permanently, creator-identity privacy, password show/hide,
username placeholder, Google/GitHub OAuth wiring. Hermetic: no network."""
import base64
import importlib
import json
import os
import unittest
from pathlib import Path
import urllib.request
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CREATOR = "danielonakoya19@gmail.com"
OTHER_ADMIN = "thinkglobal1000@gmail.com"


class TwilioRemovedTests(unittest.TestCase):
    def _read(self, name):
        return (ROOT / name).read_text(errors="ignore")

    def test_twilio_gone_from_every_shipped_file(self):
        for name in ("server.py", "communications.py", "index.html", "voice-reminders.js",
                     "communications.js", "landing.html", "privacy.html", "RENDER_ENV.txt"):
            p = ROOT / name
            self.assertTrue(p.exists(), name + " missing")
            content = p.read_text(errors="ignore").lower().replace("patch20-no-twilio", "")
            self.assertNotIn("twilio", content, "Twilio still referenced in " + name)

    def test_reminders_module_and_phone_docs_gone(self):
        self.assertFalse((ROOT / "reminders.py").exists(), "reminders.py must be deleted")
        self.assertFalse((ROOT / "PHONE-CALLS-SETUP.md").exists(), "PHONE-CALLS-SETUP.md must be deleted")
        try:
            importlib.import_module("reminders")
            self.fail("reminders module must not import")
        except Exception:
            pass

    def test_phone_channels_rejected_by_communications(self):
        from communications import normalize
        for c in ("call", "sms"):
            with self.assertRaises(ValueError):
                normalize(c, "+2347052405515")


class CreatorIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server as s
        cls.s = s
        cls._old = dict(s.KEYS)
        s.KEYS["ADMIN_EMAILS"] = [CREATOR, OTHER_ADMIN]

    @classmethod
    def tearDownClass(cls):
        cls.s.KEYS.clear()
        cls.s.KEYS.update(cls._old)

    def test_creator_session_is_told_creator_identity(self):
        head = self.s.identity_prompt_head(CREATOR)
        self.assertIn("DANIEL ONAKOYA ADEBAYO", head)
        self.assertIn("CREATOR", head)

    def test_other_admin_ai_never_sees_creator_identity(self):
        head = self.s.identity_prompt_head(OTHER_ADMIN)
        for needle in ("danielonakoya19", "DANIEL ONAKOYA", "19 June 2009"):
            self.assertNotIn(needle, head)
        self.assertIn("confidential", head)
        self.assertIn("private", head)

    def test_random_user_never_sees_creator_identity(self):
        head = self.s.identity_prompt_head("random@example.com")
        for needle in ("danielonakoya19", "DANIEL ONAKOYA"):
            self.assertNotIn(needle, head)

    def test_admin_board_masks_creator_for_other_admins(self):
        s = self.s
        with mock.patch.object(s, "load_users", return_value={CREATOR: {"created": "2026-01-01"},
                                                              OTHER_ADMIN: {"created": "2026-02-02"}}), \
             mock.patch.object(s, "_load_accounts", return_value={}), \
             mock.patch.object(s, "load_subscribers", return_value=[]), \
             mock.patch.object(s, "supabase_all_flags", return_value={}), \
             mock.patch.object(s, "supabase_auth_users", return_value=[]):
            masked = s.admin_users_payload(OTHER_ADMIN)
            joined = json.dumps(masked, ensure_ascii=False).lower()
            self.assertNotIn(CREATOR, joined, "creator email leaked to other admin")
            self.assertIn("••••• (creator account)", json.dumps(masked, ensure_ascii=False))
            creator_rows = [u for u in masked["users"] if "creator" in u["email"]]
            self.assertEqual(len(creator_rows), 1)
            self.assertEqual(creator_rows[0]["plan"], "enterprise")
            visible = s.admin_users_payload(CREATOR)
            self.assertIn(CREATOR, json.dumps(visible), "creator must see own board unmasked")
            self.assertEqual(sorted(visible["admins"]), sorted([CREATOR, OTHER_ADMIN]))

    def test_identity_never_claimable(self):
        head = self.s.identity_prompt_head(CREATOR)
        self.assertIn("No other account ever receives this disclosure", head)
        head2 = self.s.identity_prompt_head(OTHER_ADMIN)
        self.assertIn("never granted to an account by typing or claiming it", head2)


class ClientChangesTests(unittest.TestCase):
    def _index(self):
        return (ROOT / "index.html").read_text(errors="ignore")

    def test_password_eye_toggles_present(self):
        html = self._index()
        for inp, eye in (("gPass", "gPassEye"), ("lkPass", "lkPassEye"), ("aPass", "aPassEye")):
            self.assertIn('id="%s"' % inp, html, inp + " input missing")
            self.assertIn('id="%s"' % eye, html, eye + " toggle missing")
        self.assertIn("Show or hide password", html)

    def test_username_placeholder_is_not_a_real_person(self):
        html = self._index()
        self.assertNotIn("daniel_o", html)
        self.assertIn('placeholder="e.g. nova_7x"', html)

    def test_no_reminder_ui_left_in_client(self):
        for name in ("index.html", "voice-reminders.js", "communications.js"):
            content = (ROOT / name).read_text(errors="ignore")
            self.assertNotIn("/api/reminders", content, name)
            self.assertNotIn("OraReminders", content, name)
            self.assertNotIn("twilio", content.lower(), name)


class OAuthWiringTests(unittest.TestCase):
    def test_authorize_url_points_at_supabase_with_app_return(self):
        import server as s
        s.KEYS.update({"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_ANON_KEY": "anon"})
        try:
            with mock.patch.object(s, "oauth_providers", return_value={"google": True, "github": False, "checked": True}):
                r = s.oauth_url("google", "https://oracool-ai.onrender.com/oauth")
                self.assertNotIn("error", r)
                self.assertTrue(r["url"].startswith("https://example.supabase.co/auth/v1/authorize?provider=google"))
                self.assertIn("oracool-ai.onrender.com/oauth", r["url"])
                bad = s.oauth_url("facebook", "")
                self.assertIn("error", bad)
        finally:
            s.KEYS.pop("SUPABASE_URL", None)
            s.KEYS.pop("SUPABASE_ANON_KEY", None)


class HealthMarkerTests(unittest.TestCase):
    def test_build_marker(self):
        # the marker lives in the health endpoint; assert via source.
        # The exact marker moves with each patch (current: patch21-wan22,
        # pinned in tests/test_patch21.py) — the Twilio removal itself is
        # pinned by the other tests in this file.
        src = (ROOT / "server.py").read_text(errors="ignore")
        self.assertIn('"build": "patch', src)
        self.assertNotIn("twilio", src.lower().replace("patch20-no-twilio", ""))


if __name__ == "__main__":
    unittest.main()
