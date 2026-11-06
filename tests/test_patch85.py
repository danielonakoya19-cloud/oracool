"""patch85 — HOLOGRAPHIC LIQUID GLASS across the whole interface (landing, chat app,
privacy, oauth): dark iridescent theme, liquid aurora backdrop, glass surfaces with
backdrop-blur, animated holographic borders/buttons, morphing liquid orb, motion guards.
Pinned so future patches never flatten the look."""
import re
import unittest
from pathlib import Path

SRV = Path("server.py").read_text(encoding="utf-8")
APP = Path("index.html").read_text(encoding="utf-8")
LAND = Path("landing.html").read_text(encoding="utf-8")
PRIV = Path("privacy.html").read_text(encoding="utf-8")
OAUTH = Path("oauth.html").read_text(encoding="utf-8")


def holo_layer(page):
    m = re.search(r'<style id="holoGlass">([\s\S]*?)</style>', page)
    assert m, "page missing holoGlass layer"
    return m.group(1)


class TestPatch85Marker(unittest.TestCase):
    def test_marker_is_patch85(self):
        self.assertEqual(SRV.count('"patch88-movers-faq-logo"'), 2)


class TestPatch85AllSurfaces(unittest.TestCase):
    def test_every_page_has_holo_layer_and_aurora(self):
        for name, page in (("index", APP), ("landing", LAND), ("privacy", PRIV), ("oauth", OAUTH)):
            self.assertIn('<style id="holoGlass">', page, name)
            self.assertIn('id="holoBg"', page, name)
            self.assertIn('class="blob b1"', page, name)

    def test_layer_wins_cascade_over_console_css(self):
        self.assertLess(APP.index("console-layout.css?v=69"), APP.index('<style id="holoGlass">'))

    def test_layer_balanced_css(self):
        for page in (APP, LAND, PRIV, OAUTH):
            css = holo_layer(page)
            self.assertEqual(css.count("{"), css.count("}"))

    def test_no_important_inside_keyframes(self):
        for page in (APP, LAND, PRIV, OAUTH):
            css = holo_layer(page)
            for m in re.finditer(r'@keyframes[^{]*\{([\s\S]*?)\n?\}', css):
                self.assertNotIn("!important", m.group(1))


class TestPatch85Look(unittest.TestCase):
    def test_liquid_aurora_animation(self):
        css = holo_layer(APP)
        self.assertIn("holoDrift1", css)
        self.assertIn("holoDrift2", css)
        self.assertIn("#holoBg", css)
        self.assertIn("filter:blur(72px) saturate(165%)", css)

    def test_glass_surfaces(self):
        css = holo_layer(APP)
        self.assertIn("backdrop-filter:blur(26px) saturate(180%) !important", css)
        self.assertIn("-webkit-backdrop-filter:blur(26px) saturate(180%) !important", css)
        self.assertIn("inset 0 1px 0 rgba(255,255,255,.14)", css)

    def test_holographic_buttons_and_text(self):
        css = holo_layer(APP)
        self.assertIn("holoSweep 8.5s", css)          # animated holo CTA sweep
        self.assertIn("holoShift 14s", css)           # animated iridescent borders
        self.assertIn("background-clip:text", css)    # holographic gradient text

    def test_landing_liquid_orb(self):
        css = holo_layer(LAND)
        self.assertIn("holoMorph", css)               # morphing liquid blob
        self.assertIn(".orb{", css)
        self.assertIn(".orb-halo", css)
        self.assertIn("conic-gradient", css)

    def test_reduced_motion_guard(self):
        for page in (APP, LAND):
            self.assertIn("prefers-reduced-motion", holo_layer(page))

    def test_ios15_floor_vh_before_dvh(self):
        # dvh may never appear without a vh fallback declaration before it
        for page in (APP, LAND, PRIV, OAUTH):
            css = holo_layer(page)
            for m in re.finditer(r'([^;{}]*dvh[^;{}]*)', css):
                block = m.group(1)
                self.assertIn("100vh", css[:m.start()][-160:] + block)

    def test_chat_surfaces_glassed(self):
        css = holo_layer(APP)
        for sel in (".bubble.ai", ".gpt-input.big", ".sidebar", ".settings-sheet", ".modemenu"):
            self.assertIn(sel, css)

    def test_sidebar_and_account_bar_glassed(self):
        """User screenshot (patch85b): sidebar + chats list + account dock were flat."""
        css = holo_layer(APP)
        self.assertIn("rgba(13,16,32,.42)", css)          # translucent sidebar, aurora shows
        self.assertIn(".sb-foot, .drawerfoot", css)        # account dock is glass
        self.assertIn("#sbUpgrade", css)                   # Upgrade button is holo CTA
        self.assertIn(".coinpill", css)                    # coins pill gold-iris glass
        self.assertIn(".sessgrp", css)                     # TODAY/YESTERDAY labels styled

    def test_boot_screen_glassed(self):
        """User screenshot (patch85b): boot sequence + uplink map were flat black."""
        css = holo_layer(APP)
        self.assertIn("#boot{", css)
        self.assertIn("#bootMap{", css)
        self.assertIn(".bootcap{", css)
        self.assertIn(".bootlines{", css)
        self.assertIn(".gatecard{", css)


if __name__ == "__main__":
    unittest.main()
