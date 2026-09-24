"""patch33 — Alumni & industry partners band on the landing page (TAIEF, Cisco, Code.org, Simplilearn)."""
import os, re, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAND = open(os.path.join(ROOT, "landing.html"), encoding="utf-8").read()
SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()


class AlumniBandTests(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch33-alumni"'), 2)

    def test_section_once_with_nav_anchor(self):
        self.assertEqual(LAND.count('id="partners"'), 1)
        self.assertIn('<a href="#partners">Alumni</a>', LAND)

    def test_all_four_partners_present(self):
        for name in ("TAIEF", "Cisco Industry", "Code.org", "Simplilearn"):
            self.assertIn("<h3>%s</h3>" % name, LAND)
        self.assertIn("Think Advancement Initiative Empowerment Foundation", LAND)

    def test_logos_are_inline_svg_not_external(self):
        seg = LAND[LAND.index('id="partners"'):LAND.index('id="pricing"')]
        self.assertEqual(seg.count("<svg"), 4)
        self.assertNotIn("<img", seg)          # nothing that can 404 or leak a hotlink
        self.assertNotIn("http", seg.lower().replace("https?://x", ""))  # fully self-contained

    def test_markup_balance(self):
        self.assertEqual(LAND.count("<section"), LAND.count("</section>"))
        self.assertEqual(LAND.count("<svg"), LAND.count("</svg>"))
        self.assertEqual(LAND.count("<div"), LAND.count("</div>"))

    def test_no_fake_official_claim(self):
        # honest framing: alumni/partners band, not an endorsement scam
        seg = LAND[LAND.index('id="partners"'):LAND.index('id="pricing"')]
        for banned in ("official partner of", "endorsed by", "certified by Cisco", "sponsored by"):
            self.assertNotIn(banned, seg.lower())


if __name__ == "__main__":
    unittest.main()
