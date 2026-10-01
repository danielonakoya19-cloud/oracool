"""patch55 leftovers: console API still refuses installers; Kali chrome is gone as of patch56."""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch58-media-honesty"'), 2)
        self.assertIn("/api/security/console", SRV)


class Console(unittest.TestCase):
    def test_help_lists_nmap(self):
        r = s.security_console_exec("help")
        self.assertIn("nmap", r["output"].lower())
        self.assertTrue(r["have_nmap"])

    def test_still_refuses_installers(self):
        self.assertTrue(s.security_console_exec("sudo apt install zphisher").get("refused"))
