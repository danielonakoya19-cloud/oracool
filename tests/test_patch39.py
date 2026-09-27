"""patch39 — creator privacy lock (no creator PII anywhere the model or public can see it,
outside the creator's own session) + Arena-style chat upgrades (follow-up chips, ask blocks,
message actions, code copy, clean layout)."""
import importlib.util, sys, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('oracool_test_server_p39', ROOT / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
SRV = open(ROOT / 'server.py', encoding='utf-8').read()
APP = open(ROOT / 'index.html', encoding='utf-8').read()
APPS = open(ROOT / 'apps.js', encoding='utf-8').read()
LAND = open(ROOT / 'landing.html', encoding='utf-8').read()

NAME = "Daniel " + "Onakoya"  # split so this test file itself never carries the literal


class CreatorPrivacy(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch48-people"'), 2)

    def test_client_source_has_no_creator_identity(self):
        # anyone can read index.html — the creator's name/birthday must not be in it
        self.assertNotIn("Onakoya", APP); self.assertNotIn("19 June 2009", APP); self.assertNotIn("born 19", APP)
        self.assertNotIn("Onakoya", LAND); self.assertNotIn("Onakoya", APPS)
        self.assertNotIn("isCreator", APP)
        self.assertNotIn("thinkglobal1000", APP)
        self.assertIn("CREATOR PRIVACY (absolute)", APP)
        self.assertIn("built by the OraCool team", APP)
        # the admin-board rule no longer lists admin addresses
        self.assertIn("this session is NOT an administrator", APP)

    def test_identity_lock_names_the_public_creator(self):
        # patch48: the creator chose to be public; the model/provider stays private
        self.assertIn("Daniel Onakoya Adebayo", s._IDENTITY_LOCK)
        self.assertIn("never name, hint at or speculate about the underlying model", s._IDENTITY_LOCK)

    def test_pii_scrub_keeps_birthday_private_only(self):
        with mock.patch.object(s, "admin_emails", lambda: ["creator@x.com", "other@x.com"]):
            t = "I'm OraCool AI, created by " + NAME + " Adebayo. Email danielonakoya19@gmail.com, born 19 June 2009."
            out = s._pii_scrub(t, "visitor@x.com")
            self.assertIn("Onakoya", out); self.assertNotIn("2009", out)  # name public, birthday never
            self.assertEqual(s._pii_scrub(t, "creator@x.com"), t)

    def test_prompt_messages_are_sanitized(self):
        with mock.patch.object(s, "admin_emails", lambda: ["creator@x.com"]):
            msgs = [{"role": "system", "content": "CREATOR: owner is " + NAME.upper() + " ADEBAYO, born 19 June 2009, alt thinkglobal1000@gmail.com."},
                    {"role": "assistant", "content": "I was created by " + NAME + "."},
                    {"role": "user", "content": "who is Onakoya?"}]
            out = s._pii_scrub_messages(msgs, "visitor@x.com")
            self.assertNotIn("2009", out[0]["content"]); self.assertNotIn("thinkglobal1000", out[0]["content"])
            self.assertIn("Onakoya", out[1]["content"])  # the public name stays
            self.assertEqual(out[2]["content"], "who is Onakoya?")  # user text is never rewritten
            self.assertIs(s._pii_scrub_messages(msgs, "creator@x.com"), msgs)


class ArenaStyleUX(unittest.TestCase):
    def test_client_features_present(self):
        self.assertIn("function _splitFollowups(text)", APP)
        self.assertIn("function attachActions(bubble, text)", APP)
        self.assertIn("function regenerateLast()", APP)
        self.assertIn("attachActions(b, acc);", APP)
        self.assertIn("OraApps.onAsk=(t)=>", APP)
        self.assertIn('id="sClean"', APP); self.assertIn("layout:'clean'", APP); self.assertIn("body.clean .bubble.ai{", APP)
        self.assertIn("FOLLOW-UPS: <next step 1> | <next step 2> | <next step 3>", APP)
        self.assertIn('{"app":"ask","question"', APP)
        self.assertIn('<script src="/apps.js?v=39"></script>', APP)
        self.assertIn("function _noApp(t){ return _noAppRaw(_splitFollowups(String(t||'')).text); }", APP)

    def test_apps_ask_block(self):
        self.assertIn("function vizAsk(d)", APPS); self.assertIn("function wireAsk(host)", APPS)
        self.assertIn("ask: 1, question: 1, choice: 1", APPS); self.assertIn("onAsk: null", APPS)


if __name__ == "__main__":
    unittest.main()
