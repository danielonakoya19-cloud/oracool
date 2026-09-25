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
        self.assertEqual(SRV.count('"patch45-inline-feed"'), 2)

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

    def test_identity_lock_names_no_person(self):
        self.assertNotIn("Onakoya", s._IDENTITY_LOCK)
        self.assertIn("CREATOR PRIVACY (absolute)", s._IDENTITY_LOCK)
        self.assertIn("the OraCool team", s._IDENTITY_LOCK)

    def test_pii_scrub_outside_creator_session(self):
        with mock.patch.object(s, "admin_emails", lambda: ["creator@x.com", "other@x.com"]):
            t = "I'm OraCool AI, created by " + NAME + " Adebayo. Email danielonakoya19@gmail.com, born 19 June 2009."
            out = s._pii_scrub(t, "visitor@x.com")
            self.assertNotIn("Onakoya", out); self.assertNotIn("gmail", out); self.assertNotIn("2009", out)
            self.assertIn("created by the OraCool team.", out)
            # other admins are NOT the creator either
            self.assertNotIn("Onakoya", s._pii_scrub(t, "other@x.com"))
            # the creator's own session is untouched
            self.assertEqual(s._pii_scrub(t, "creator@x.com"), t)
            self.assertEqual(s._pii_scrub(t, "CREATOR@x.com"), t)

    def test_prompt_messages_are_sanitized(self):
        with mock.patch.object(s, "admin_emails", lambda: ["creator@x.com"]):
            msgs = [{"role": "system", "content": "CREATOR: owner is " + NAME.upper() + " ADEBAYO, email danielonakoya19@gmail.com."},
                    {"role": "assistant", "content": "I was created by " + NAME + "."},
                    {"role": "user", "content": "who is Onakoya?"}]
            out = s._pii_scrub_messages(msgs, "visitor@x.com")
            self.assertNotIn("ONAKOYA", out[0]["content"].upper()); self.assertNotIn("gmail", out[0]["content"])
            self.assertNotIn("Onakoya", out[1]["content"])
            self.assertEqual(out[2]["content"], "who is Onakoya?")  # user text is never rewritten
            self.assertIs(s._pii_scrub_messages(msgs, "creator@x.com"), msgs)
        self.assertIn("messages = _pii_scrub_messages(messages, chat_email)", SRV)
        self.assertIn("_pii_scrub(_identity_scrub(_final_txt), chat_email)", SRV)
        self.assertIn("_pii_scrub(_identity_scrub(_strip_agent_markup(text)), conv_em)", SRV)


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
