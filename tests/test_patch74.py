"""patch74: FREE LAUNCH — one master switch (GROWTH_FREE_MODE, default on) that
opens every plan feature (check_tier -> ultra), stops coins from blocking or
deducting, hides paywall UI, and puts Claude first in the builder ladders
(falling through to Groq on any error) for creation quality."""
import json, os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
MARK = "patch92-community-write"


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count(MARK), 2)


class FreeMode(unittest.TestCase):
    def test_default_on_and_env_off(self):
        with mock.patch.object(s, "key", lambda k: None):
            self.assertTrue(s.free_mode())
        with mock.patch.object(s, "key", lambda k: "0"):
            self.assertFalse(s.free_mode())
        with mock.patch.object(s, "key", lambda k: "false"):
            self.assertFalse(s.free_mode())

    def test_check_tier_ultra(self):
        with mock.patch.object(s, "key", lambda k: None):
            self.assertEqual(s.check_tier("anyone@example.com"), "ultra")
            self.assertEqual(s.check_tier(""), "ultra")

    def test_coins_never_block_or_deduct(self):
        with mock.patch.object(s, "key", lambda k: None), \
             mock.patch.object(s, "coins_state", lambda e: {"balance": 0, "unlimited": False}):
            self.assertIsNone(s.coins_gate("u@x.com", 10000))
            st = s.coins_charge("u@x.com", 10000)
            self.assertTrue(st.get("free_mode"))
            self.assertTrue(st.get("unlimited"))

    def test_payments_back_with_env(self):
        with mock.patch.object(s, "key", lambda k: "0"), \
             mock.patch.object(s, "coins_state", lambda e: {"balance": 5, "unlimited": False,
                                                            "one_time": True, "grant": 1000000,
                                                            "reset_in_days": 3}):
            g = s.coins_gate("u@x.com", 10000)
            self.assertIsInstance(g, dict)  # the paywall returns when the flag is off
            self.assertEqual(s.check_tier("u@x.com"), "free")

    def test_config_advertises(self):
        self.assertIn('"free_mode": free_mode()', SRV)
        self.assertEqual(SRV.count('"free_mode": free_mode()'), 2)  # config + health


class BuilderBrain(unittest.TestCase):
    def test_claude_first_in_both_ladders(self):
        # patch82: both ladders share _llm_attempts — anthropic leads there
        a = SRV.index("def _llm_attempts(")
        seg = SRV[a:a + 2200]
        self.assertIn('key("ANTHROPIC_API_KEY")', seg)
        self.assertLess(seg.index("ANTHROPIC_API_KEY"), seg.index("GROQ_API_KEY"))
        self.assertLess(seg.index("GEMINI_API_KEY"), seg.index("OPENAI_API_KEY"))
        self.assertEqual(SRV.count("attempts = _llm_attempts(effort)"), 2)

    def test_anthropic_translations_present(self):
        self.assertIn('req = _anthropic_request(k, req_body, stream=True)', SRV)
        self.assertIn("resp = _AnthropicSSE(resp)  # speaks OpenAI SSE format", SRV)
        self.assertIn('http_fetch(base.rstrip("/") + "/messages", method="POST", timeout=_BRAIN_TIMEOUT.get(prov, 240),', SRV)

    def test_app_build_effort_high(self):
        self.assertIn('effort=("high" if _is_app else "medium")', SRV)


class ClientFree(unittest.TestCase):
    def test_plans_free_banner(self):
        self.assertIn("Everything is FREE right now", APP)
        self.assertIn("free launch mode", APP)

    def test_upgrade_row_hidden_and_wallet_line(self):
        self.assertIn("/pro|ultra|enterprise|admin/.test(String(t).toLowerCase()) || !!(srv&&srv.free_mode)", APP)
        self.assertIn("Free launch:</b> builds and edits cost you nothing", APP)


if __name__ == "__main__":
    unittest.main()
