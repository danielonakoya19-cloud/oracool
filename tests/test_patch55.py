"""patch55: the hosted console looks like a Kali terminal (visual only)."""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch55-kali-look"'), 2)
        self.assertIn("KALI_PS1", SRV)
        self.assertIn("kali㉿oracool", SRV)


class Look(unittest.TestCase):
    def test_window_chrome(self):
        for needle in ("kali@oracool: ~", "Kali GNU/Linux Rolling", "kalips", "kali-tb",
                       "{id:'console',label:'Terminal'"):
            self.assertIn(needle, APP, needle)

    def test_prompt_and_whoami(self):
        r = s.security_console_exec("whoami")
        self.assertIn("kali", r["output"].splitlines()[0])
        self.assertIn("kali㉿oracool", r["output"])
        r2 = s.security_console_exec("help")
        self.assertIn("nmap", r2["output"].lower())
        self.assertTrue(r2["have_nmap"])

    def test_still_refuses_installers(self):
        self.assertTrue(s.security_console_exec("sudo apt install zphisher").get("refused"))


if __name__ == "__main__":
    unittest.main()
