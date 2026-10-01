"""patch56: fake Kali terminal removed; hosted Nmap/SQLMap/Wireshark/John/Hydra stay on Intel cards."""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch58-media-honesty"'), 2)
        self.assertNotIn("KALI_PS1", SRV)
        self.assertNotIn("kali㉿oracool", SRV)
        self.assertNotIn("drawSecConsole", APP)
        self.assertNotIn("kali@oracool: ~", APP)
        self.assertNotIn("{id:'console',label:'Terminal'", APP)


class ToolsStay(unittest.TestCase):
    def test_intel_cards_hidden(self):
        self.assertNotIn("{id:'secscan'", APP)
        self.assertNotIn("Nmap / Port Scan", APP)

    def test_chat_still_has_nmap(self):
        out = s.auto_tools("do u have nmap", tier="enterprise", email="e@example.com")
        blob = " ".join(str(x.get("label")) + str(x.get("result")) for x in out)
        self.assertIn("have_nmap", blob)


if __name__ == "__main__":
    unittest.main()
