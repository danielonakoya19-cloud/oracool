"""patch86 — dark/light theme toggle across the interface: a switch on the app header
and the landing nav flips html[data-theme] between the dark holographic liquid glass
(default) and the LIGHT CRYSTAL variant; choice persists in localStorage('oc_theme')
and applies flash-free before first paint on every page."""
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


class TestPatch86Marker(unittest.TestCase):
    def test_marker_is_patch86(self):
        self.assertEqual(SRV.count('"patch89b-call-buttons"'), 2)


class TestPatch86Toggle(unittest.TestCase):
    def test_toggle_on_app_and_landing(self):
        self.assertIn('id="themeToggle"', APP)
        self.assertIn('id="themeToggle"', LAND)

    def test_theme_persists_in_localstorage(self):
        for page in (APP, LAND, PRIV, OAUTH):
            self.assertIn("localStorage.getItem('oc_theme')", page)
            self.assertIn("localStorage.setItem('oc_theme'", page)

    def test_init_runs_before_first_paint(self):
        # data-theme is applied by an inline script before the first body element renders
        for page in (APP, LAND, PRIV, OAUTH):
            self.assertIn("document.documentElement.setAttribute('data-theme'", page)
            self.assertLess(page.index("localStorage.getItem('oc_theme')"), page.index('id="holoBg"'))

    def test_toggle_flips_and_rebinds(self):
        self.assertIn("getElementById('themeToggle')", APP)
        self.assertIn("addEventListener('click'", APP)

    def test_every_page_knows_both_themes(self):
        for page in (APP, LAND, PRIV, OAUTH):
            self.assertIn('html[data-theme="light"]', holo_layer(page))


class TestPatch86LightCrystal(unittest.TestCase):
    def test_light_tokens(self):
        css = holo_layer(APP)
        self.assertIn('html[data-theme="light"]{', css)
        self.assertIn("--holo-glass:rgba(255,255,255,.58)", css)
        self.assertIn("--holo-deep:#eef2fb", css)

    def test_light_surfaces(self):
        css = holo_layer(APP)
        for sel in ('html[data-theme="light"] .sidebar', 'html[data-theme="light"] .bubble.ai',
                    'html[data-theme="light"] #boot', 'html[data-theme="light"] .bootlines',
                    'html[data-theme="light"] .gpt-input.big'):
            self.assertIn(sel, css)

    def test_light_landing_surfaces(self):
        css = holo_layer(LAND)
        for sel in ('html[data-theme="light"] .nav', 'html[data-theme="light"] .cta',
                    'html[data-theme="light"] .cap .icon'):
            self.assertIn(sel, css)

    def test_light_mini_surfaces(self):
        for page in (PRIV, OAUTH):
            css = holo_layer(page)
            self.assertIn('html[data-theme="light"] main', css)
            self.assertIn('html[data-theme="light"] h1', css)

    def test_toggle_button_styled_both_modes(self):
        css = holo_layer(APP)
        self.assertIn('.theme-toggle{', css)
        self.assertIn('html[data-theme="light"] .theme-toggle', css)

    def test_light_mode_keeps_gradient_text_readable(self):
        css = holo_layer(APP)
        self.assertIn('#0369a1', css)  # darkened rainbow stops for light bg


if __name__ == "__main__":
    unittest.main()
