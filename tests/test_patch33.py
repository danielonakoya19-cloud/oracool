"""patch33 — Alumni & industry partners band on the landing page (TAIEF, Cisco, Code.org, Simplilearn)."""
import os, re, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAND = open(os.path.join(ROOT, "landing.html"), encoding="utf-8").read()
SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()


class AlumniBandTests(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch44-vision"'), 2)

    def test_section_once_with_nav_anchor(self):
        self.assertEqual(LAND.count('id="partners"'), 1)
        self.assertIn('<a href="#partners">Alumni</a>', LAND)

    def test_all_four_partners_present(self):
        for name in ("TAIEF", "Cisco Industry", "Code.org", "Simplilearn"):
            self.assertRegex(LAND, r"<h3[^>]*>\s*%s" % re.escape(name))
        self.assertIn("Think Advancement Initiative Empowerment Foundation", LAND)

    def test_logos_are_self_contained(self):
        seg = LAND[LAND.index('id="partners"'):LAND.index('id="pricing"')]
        # three drawn marks + the official TAIEF seal embedded as a data-URI image
        self.assertEqual(seg.count("<svg"), 3)
        imgs = re.findall(r'<img[^>]*src="([^"]*)"', seg)
        self.assertEqual(len(imgs), 1)
        self.assertTrue(imgs[0].startswith("data:image/webp;base64,"))   # no hotlink that can 404

    def test_markup_balance(self):
        self.assertEqual(LAND.count("<section"), LAND.count("</section>"))
        self.assertEqual(LAND.count("<svg"), LAND.count("</svg>"))
        self.assertEqual(LAND.count("<div"), LAND.count("</div>"))

    def test_taief_card_links_official_site(self):
        seg = LAND[LAND.index('id="taiefCard"'):LAND.index('id="taiefCard"') + 400]
        self.assertIn('href="https://taief.com.ng/"', seg)
        self.assertIn('target="_blank"', seg)
        self.assertIn('rel="noopener"', seg)      # tabnabbing guard

    def test_no_fake_official_claim(self):
        # honest framing: alumni/partners band, not an endorsement scam
        seg = LAND[LAND.index('id="partners"'):LAND.index('id="pricing"')]
        for banned in ("official partner of", "endorsed by", "certified by Cisco", "sponsored by"):
            self.assertNotIn(banned, seg.lower())


if __name__ == "__main__":
    unittest.main()
