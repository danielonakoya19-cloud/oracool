"""patch87 — (1) AI-built sites get the holographic liquid-glass design bar + high-quality
graphics bar; (2) Private chat (never saved — user+assistant turns vanish); (3) community
chat fully liquid glass; (4) multi-account add/switch/remove; (5) liquid-glass type
everywhere. Both themes."""
import re
import unittest
from pathlib import Path

SRV = Path("server.py").read_text(encoding="utf-8")
APP = Path("index.html").read_text(encoding="utf-8")


def holo_layer(page):
    m = re.search(r'<style id="holoGlass">([\s\S]*?)</style>', page)
    assert m
    return m.group(1)


class TestPatch87Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch88-movers-faq-logo"'), 2)


class TestPatch87BuilderGlass(unittest.TestCase):
    def test_site_prompt_has_liquid_glass_bar(self):
        i = SRV.index("def build_site(")
        win = SRV[i:i + 20000]
        self.assertIn("LIQUID GLASS DESIGN BAR", win)
        self.assertIn("HIGH-QUALITY GRAPHICS BAR", win)
        self.assertIn("backdrop-filter blur(22px)", win)

    def test_app_prompt_has_glass_chrome(self):
        self.assertIn("UI chrome is holographic liquid glass", SRV)

    def test_edit_prompt_uses_house_look(self):
        i = SRV.index("_EDIT_SYS = (")
        self.assertIn("holographic liquid-glass design language", SRV[i:i + 800])


class TestPatch87PrivateChat(unittest.TestCase):
    def test_server_never_persists_private(self):
        i = SRV.index('_private = bool(body.get("private"))')
        self.assertIn('_conv_id = ""', SRV[i:i + 200])
        # every conv_append CALL site guards on conv_id (the def itself uses cid)
        calls = [m.start() for m in re.finditer(r"conv_append\(", SRV)]
        self.assertGreaterEqual(len(calls), 4)  # 3 calls + definition
        for c in calls:
            line = SRV[SRV.rfind("\n", 0, c):c]
            if "def" in line:
                continue
            self.assertIn("conv_id", line + SRV[c:c + 40])

    def test_client_private_pill_and_payload(self):
        self.assertIn('id="privBtn"', APP)
        self.assertIn("private:!!privMode", APP)
        self.assertEqual(APP.count("private:!!privMode"), 2)  # send + side-by-side
        self.assertIn("privMode ? '' : await ensureSession()", APP)

    def test_private_skips_session_entirely(self):
        self.assertEqual(APP.count("privMode ? '' : await ensureSession()"), 2)


class TestPatch87CommunityGlass(unittest.TestCase):
    def test_community_surfaces(self):
        css = holo_layer(APP)
        for sel in (".commmsgs, #commMsgs, #dmMsgs", ".wa-compose, .commcompose",
                    ".wa-row .wa-bub", ".commhead", ".wa-pop.open"):
            self.assertIn(sel, css)
        self.assertIn("border:1px solid rgba(255,255,255,.13) !important", css)

    def test_community_light_variant(self):
        css = holo_layer(APP)
        self.assertIn('html[data-theme="light"] #commMsgs', css)


class TestPatch87Accounts(unittest.TestCase):
    def test_account_registry(self):
        self.assertIn("oracool_accounts", APP)
        self.assertIn("function registerAccount()", APP)
        self.assertIn("function switchAccount(", APP)
        self.assertIn("function dropAccount(", APP)

    def test_account_sheet_ui(self):
        self.assertIn('id="acctAdd"', APP)
        self.assertIn('id="acctList"', APP)
        self.assertIn("showGate()", APP)

    def test_signout_keeps_other_accounts(self):
        i = APP.index("$('#acctSignout').onclick")
        self.assertIn("dropAccount(_em)", APP[i:i + 400])
        self.assertIn("switchAccount(rest[0])", APP[i:i + 400])


class TestPatch87LiquidType(unittest.TestCase):
    def test_gradient_titles_both_themes(self):
        css = holo_layer(APP)
        self.assertIn(".ptitle, .viewtitle, .sb-title", css)
        self.assertIn('html[data-theme="light"] .ptitle', css)

    def test_private_pill_styled(self):
        css = holo_layer(APP)
        self.assertIn(".privbtn.on", css)
        self.assertIn('html[data-theme="light"] .privbtn.on', css)


if __name__ == "__main__":
    unittest.main()
