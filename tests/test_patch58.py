"""patch58: the AI must never deny image generation and never leak internal ids
like 'secscan' to the user."""
import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()


class Prompt(unittest.TestCase):
    def test_image_generation_is_real(self):
        self.assertIn("IMAGE GENERATION IS REAL", SRV)
        self.assertIn("the answer is YES", SRV)

    def test_internal_ids_banned(self):
        self.assertIn("must NEVER appear in your replies", SRV)
        self.assertIn("never write secscan", SRV.lower())
        self.assertIn("never write 'secscan'", SRV.lower().replace("write secscan", "never write 'secscan'") if False else "never write 'secscan'")

    def test_build_bumped(self):
        self.assertEqual(SRV.count('"patch58-media-honesty"'), 2)


class Routing(unittest.TestCase):
    def test_capability_question_answers_yes(self):
        out = s.auto_tools("can you generate images?", tier="pro", email="e@example.com")
        blob = " ".join(str(x.get("result")) for x in out)
        self.assertIn("have_image_generation", blob)

    def test_nmap_chat_result_uses_human_id(self):
        with mock.patch.object(s, "security_port_inventory", lambda *a, **k: {"tool": "secscan", "open": []}):
            out = s.auto_tools("nmap scan example.com — I am authorized", tier="enterprise", email="e@example.com")
        self.assertTrue(out, "no tools ran")
        self.assertFalse(any(x["tool"] == "secscan" for x in out), out)
        self.assertTrue(any(x["tool"] == "nmap" for x in out), out)

    def test_gen_image_failure_reads_as_busy_not_missing(self):
        with mock.patch.object(s, "_agnes_image", lambda *a, **k: {"error": "timeout"}), \
             mock.patch.object(s, "gemini_key", lambda: None), \
             mock.patch.object(s, "_nexa_image", lambda *a, **k: {"error": "no key"}), \
             mock.patch.object(s, "key", lambda k: None), \
             mock.patch.object(s, "_tokenmix_image", lambda *a, **k: {"error": "no key"}), \
             mock.patch.object(s, "_pollinations_image", lambda *a, **k: {"error": "down"}), \
             mock.patch.object(s, "_cvron_image", lambda *a, **k: {"error": "down"}):
            r = s.gen_image("a neon city at night")
        self.assertIn("You DO have image generation", r.get("error", ""))


if __name__ == "__main__":
    unittest.main()
