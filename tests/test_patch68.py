"""patch68: iOS 15.8+ compatibility + big readable chat history sidebar.
- all dvh/svh uses have vh fallbacks; -webkit-backdrop-filter; --app-h viewport fitter
- sidebar: Chats owns the space (flex 1 + min-height), Menu collapsible, big rows
"""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
CSS = open(os.path.join(ROOT, "console-layout.css"), encoding="utf-8").read()

MARK = "patch90-call-vision-fit"


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count(MARK), 2)  # BUILD + health


class IOS15Compat(unittest.TestCase):
    def test_dvh_uses_have_vh_fallback(self):
        import re
        for m in re.finditer(r"(\d+(?:\.\d+)?)dvh", APP):
            line = APP[max(0, m.start() - 220):m.end() + 60]
            self.assertTrue(
                re.search(r"\d+(?:\.\d+)?vh", line),
                f"dvh without vh fallback near: {m.group(0)}")

    def test_call_overlay_backdrop_prefix(self):
        self.assertIn("-webkit-backdrop-filter:blur(10px)", APP)

    def test_body_height_app_h(self):
        self.assertIn("height:var(--app-h, 100vh)", APP)
        self.assertIn("--app-h", APP)
        self.assertIn("setProperty('--app-h'", APP)

    def test_no_unsupported_syntax(self):
        import re
        self.assertIsNone(re.search(r"\?\?=|\|\|=|&&=", APP))
        self.assertIsNone(re.search(r"^\s*#[a-zA-Z_]+\s*=", APP, re.M))
        self.assertNotIn("static {", APP)


class BigHistorySidebar(unittest.TestCase):
    def test_chats_owns_space(self):
        self.assertIn("min-height:44vh; overflow-y:auto", APP)

    def test_menu_collapsible(self):
        self.assertIn('id="menuToggle"', APP)
        self.assertIn("menuCaret", APP)
        self.assertIn(".side-nav.collapsed{display:none;}", APP)

    def test_rows_big(self):
        self.assertIn("font-size:14.5px", APP)   # .sess .st
        self.assertIn("padding:9px 36px 9px 12px", APP)  # .sess

    def test_sidebar_wider(self):
        self.assertIn(".sidebar{width:288px;", CSS)
        self.assertIn("width:min(300px,88vw)", CSS)


if __name__ == "__main__":
    unittest.main()
