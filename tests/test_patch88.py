"""patch88 — (1) image-30 bug: morning briefings/movers now actually RUN the live tools
(weather falls back to the timezone city, 'movers' gets a live basket tool) and internal
patch ids are scrubbed from model-facing prompts; (2) landing FAQ cards = liquid glass +
content refreshed (Private chats); (3) the OraCool logo mark = liquid glass (both pages)."""
import re
import unittest
from pathlib import Path

SRV = Path("server.py").read_text(encoding="utf-8")
APP = Path("index.html").read_text(encoding="utf-8")
LAND = Path("landing.html").read_text(encoding="utf-8")


class TestPatch88Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch89b-call-buttons"'), 2)


class TestPatch88BriefingTools(unittest.TestCase):
    def test_market_movers_tool_exists(self):
        self.assertIn("def market_movers():", SRV)
        i = SRV.index("def market_movers():")
        self.assertIn("_resolve_market", SRV[i:i + 1200])
        self.assertIn("_signal_series", SRV[i:i + 1200])
        self.assertIn("USD/NGN", SRV[i:i + 1200])

    def test_movers_triggers_on_briefing_words(self):
        i = SRV.index("def auto_tools(")
        seg = SRV[i:i + 40000]
        self.assertIn('morning brief|briefing|movers?', seg)
        self.assertIn('{"tool": "movers"', seg)

    def test_weather_falls_back_to_tz_city(self):
        i = SRV.index("def auto_tools(")
        seg = SRV[i:i + 40000]
        self.assertIn('or "Lagos"', seg)
        self.assertIn('split("/")[-1]', seg)

    def test_auto_tools_receives_tz(self):
        self.assertIn('force_build=False, tz="")', SRV)
        self.assertEqual(SRV.count('tz=str(body.get("tz") or "")'), 2)

    def test_no_internal_patch_ids_in_prompts(self):
        i = APP.index("TRADE SIGNALS:")
        self.assertNotIn("patch80", APP[i:i + 200])
        j = APP.index("MARKETS & FORECASTS:")
        self.assertNotIn("patch79", APP[j:j + 200])
        self.assertIn("market-movers basket automatically", APP)

    def test_no_patch_ids_leak_in_prompt_templates(self):
        # the two model-facing prompt paragraphs carry no patchNN tags
        for anchor in ("TRADE SIGNALS:", "MARKETS & FORECASTS:"):
            i = APP.index(anchor)
            seg = APP[i:i + 2600]
            self.assertIsNone(re.search(r"patch\d+", seg), anchor)


class TestPatch88FaqAndLogo(unittest.TestCase):
    def test_faq_cards_liquid_glass(self):
        i = LAND.index("patch88: FAQ cards")
        self.assertIn("backdrop-filter:blur(22px)", LAND[i:i + 1200])
        self.assertIn('html[data-theme="light"] .faqs details', LAND)

    def test_faq_content_current(self):
        self.assertIn("Private chat", LAND)
        self.assertIn("never saved anywhere", LAND)

    def test_landing_logo_liquid_glass(self):
        i = LAND.rindex(".brand i{")
        self.assertIn("conic-gradient", LAND[i:i + 500])
        self.assertIn("logoGlow", LAND[i:i + 800])

    def test_app_orb_logo_liquid_glass(self):
        i = APP.index("orb logo = liquid glass")
        self.assertIn("conic-gradient", APP[i:i + 1200])
        self.assertIn("@keyframes logoGlow", APP)
        self.assertIn('html[data-theme="light"] .orbcore', APP)


if __name__ == "__main__":
    unittest.main()
