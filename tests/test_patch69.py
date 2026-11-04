"""patch69: iOS 15 live-delivery hardening — service worker fresh-cache
generation + always-network page navigations, versioned assets, visible
build tag so any device can verify it runs the new code."""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
SW = open(os.path.join(ROOT, "sw.js"), encoding="utf-8").read()

MARK = "patch72-app-quality"


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count(MARK), 2)  # BUILD + health


class FreshDelivery(unittest.TestCase):
    def test_sw_cache_generation_bumped(self):
        self.assertIn("oracool-v5", SW)
        self.assertNotIn("oracool-v4", SW)

    def test_sw_pages_always_network(self):
        self.assertIn("e.request.mode === 'navigate'", SW)
        self.assertIn("fetch(e.request).catch(() => caches.match(e.request))", SW)

    def test_assets_versioned(self):
        self.assertIn("/communications.js?v=69", APP)
        self.assertIn("/console-layout.css?v=69", APP)
        self.assertIn("/md.js?v=42", APP)          # lookbehind fix shipped
        self.assertIn("/voice-reminders.js?v=41", APP)  # lookbehind fix shipped

    def test_no_regex_lookbehind_anywhere(self):
        import re
        for name in ("md.js", "apps.js", "voice-reminders.js",
                     "communications.js", "sw.js"):
            t = open(os.path.join(ROOT, name), encoding="utf-8").read()
            self.assertIsNone(re.search(r"\(\?<[=!]", t), name)
        self.assertIsNone(re.search(r"\(\?<[=!]", APP))

    def test_build_tag_visible(self):
        self.assertIn('id="buildTag"', APP)
        self.assertIn("OraCool build ", APP)


class IOS15StillClean(unittest.TestCase):
    def test_no_parse_time_risk_syntax(self):
        import re
        self.assertIsNone(re.search(r"\?\?=|\|\|=|&&=", APP))
        self.assertIsNone(re.search(r"static\s*\{", APP))
        self.assertIsNone(re.search(r"\(\?<[=!]|\(\?<[a-z]", APP))  # regex lookbehind

    def test_dvh_fallbacks_kept(self):
        import re
        for m in re.finditer(r"(\d+(?:\.\d+)?)dvh", APP):
            line = APP[max(0, m.start() - 220):m.end() + 60]
            self.assertTrue(re.search(r"\d+(?:\.\d+)?vh", line), m.group(0))


if __name__ == "__main__":
    unittest.main()
