"""patch77: the boot screen is a holographic world map — a real Earth land
grid on canvas with ground stations, relay arcs, graticule and radar sweep,
replacing the plain orb."""
import base64, os, re, sys, unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count("patch103-wa-content"), 2)


class HoloBoot(unittest.TestCase):
    def test_canvas_and_module_present(self):
        self.assertIn('<canvas id="bootMap"', APP)
        self.assertIn("var HOLO=", APP)
        self.assertIn("HOLO.start()", APP)
        self.assertIn("HOLO.stop()", APP)

    def test_real_earth_grid_encoded(self):
        m = re.search(r"var B64='([A-Za-z0-9+/=]+)'", APP)
        self.assertIsNotNone(m)
        raw = base64.b64decode(m.group(1))
        self.assertEqual(len(raw), 72 * 23)                      # 180x72 grid, 23 bytes/row
        ones = sum(bin(b).count("1") for b in raw)
        pct = 100.0 * ones / (180 * 72)
        self.assertGreater(pct, 25)                              # real landmass share (~29%)
        self.assertLess(pct, 34)  # grid spans 84N-60S, so above Earth's 29% average

    def test_ground_stations_and_home(self):
        self.assertIn("['LAGOS',6.5,3.4,1,10,11]", APP)
        self.assertIn("['ABUJA',9.1,7.4,1,9,-9]", APP)
        for city in ("LONDON", "NEW YORK", "SYDNEY", "TOKYO", "MUMBAI"):
            self.assertIn(city, APP)
        self.assertEqual(APP.count("var ARCS="), 1)

    def test_technical_boot_lines(self):
        self.assertIn("Holographic earth projection", APP)
        self.assertIn("Ground stations linked: 10/10", APP)
        self.assertIn("ALL SYSTEMS ONLINE.", APP)

    def test_old_orb_boot_hidden_and_failsafe_kept(self):
        self.assertIn("#bootReactor{display:none;}", APP)
        self.assertIn("bootFail", APP)                           # failsafe intact
        self.assertIn("__oraBooted", APP)

    def test_free_launch_unchanged(self):
        with unittest.mock.patch.object(s, "free_mode", lambda: True):
            self.assertIn("FREE LAUNCH", s.identity_prompt_head("a@b.c", "price?"))


if __name__ == "__main__":
    unittest.main()
