"""patch76: free landing — the public landing page drops all pricing (static
cards + the injected feature matrix) and the creator's personal info, gains a
member-reviews section, and the AI never quotes plan prices during the free
launch."""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
LAND = open(os.path.join(ROOT, "landing.html"), encoding="utf-8").read()
MARK = "patch89b-call-buttons"


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count(MARK), 2)


class Landing(unittest.TestCase):
    def test_no_prices_anywhere(self):
        for gone in ("$29", "$49", "$149", "$500", "PER 30 DAYS", "Choose Pro",
                     "Choose Enterprise", "price-grid", "plan-note", "Explore plans"):
            self.assertNotIn(gone, LAND)

    def test_no_personal_info(self):
        for gone in ("Daniel Onakoya Adebayo", "danielonakoya19@gmail.com",
                     "Lagos, Nigeria", "creator-card", "#creator"):
            self.assertNotIn(gone, LAND)

    def test_reviews_section_present(self):
        self.assertIn('id="reviews"', LAND)
        self.assertIn("Member reviews", LAND)
        for must in ("Adaeze O.", "Tunde A.", "Emeka N.", "★★★★★", "build a website for my tailoring business"):
            self.assertIn(must, LAND)

    def test_nav_and_footer_point_to_reviews(self):
        self.assertIn('<a href="#reviews">Reviews</a>', LAND)
        self.assertNotIn('href="#pricing"', LAND)


class ServerInjection(unittest.TestCase):
    def test_matrix_and_creator_blank(self):
        self.assertIn('_lh.replace("<!--FEATURE_MATRIX-->", "").replace("<!--CREATOR_CARD-->", "")', SRV)
        self.assertNotIn("feature_matrix_html()).replace", SRV)


class AIFreePricing(unittest.TestCase):
    def test_never_quotes_prices_in_free_mode(self):
        with __import__("unittest").mock.patch.object(s, "free_mode", lambda: True):
            head = s.identity_prompt_head("fan@example.com", "how much is the pro plan?")
        self.assertIn("FREE LAUNCH", head)
        self.assertNotIn("$29", head)

    def test_app_strips_creator_line(self):
        self.assertNotIn("About the creator", APP)
        self.assertIn("creator personal info removed from public surfaces", APP)


if __name__ == "__main__":
    unittest.main()
