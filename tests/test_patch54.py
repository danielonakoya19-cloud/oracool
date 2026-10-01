"""patch54: Nmap is named Nmap, chat never denies it, console refuses zphisher, plans lock Enterprise tools."""
import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
SQL = open(os.path.join(ROOT, "supabase_patch54_security_console.sql"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch56-no-fake-kali"'), 2)
        self.assertIn("/api/security/console", SRV)
        self.assertIn("security_console_exec", SRV)
        self.assertIn("have_nmap", SRV)
        self.assertIn("Never say you don't have nmap", SRV)


class CatalogAndChat(unittest.TestCase):
    def test_catalog_says_yes_to_nmap(self):
        c = s.security_catalog()
        self.assertTrue(c["have_nmap"])
        self.assertTrue(c["have_sqlmap"])
        names = [t["name"] for t in c["tool_list"]]
        self.assertIn("Nmap", names)
        self.assertIn("SQLMap", names)
        self.assertIn("Wireshark", names)
        self.assertIn("John the Ripper", names)
        self.assertIn("Hydra", names)

    def test_do_you_have_nmap_is_yes_not_a_failed_scan(self):
        out = s.auto_tools("do u have nmap", tier="enterprise", email="e@example.com")
        self.assertTrue(out, out)
        blob = " ".join(str(x.get("label")) + str(x.get("result")) for x in out)
        self.assertIn("Nmap", blob)
        self.assertNotIn("Authorization required", blob)
        self.assertNotIn("Tell me the public domain", blob)
        self.assertIn("have_nmap", blob)

    def test_explicit_nmap_runs(self):
        with mock.patch.object(s, "security_port_inventory",
                               lambda *a, **k: {"tool": "secscan", "name": "Nmap", "have_nmap": True, "open": []}):
            out = s.auto_tools("nmap 8.8.8.8", tier="enterprise", email="e@example.com")
        self.assertTrue(any(x["label"].startswith("Nmap") for x in out), out)

    def test_locked_on_pro(self):
        out = s.auto_tools("do u have nmap", tier="pro", email="p@example.com")
        self.assertTrue(any(x["tool"] == "locked" for x in out), out)


class Console(unittest.TestCase):
    def test_help_lists_nmap(self):
        r = s.security_console_exec("help")
        self.assertIn("nmap", r["output"].lower())
        self.assertTrue(r["have_nmap"])

    def test_refuses_zphisher(self):
        r = s.security_console_exec("git clone https://github.com/htr-tech/zphisher.git")
        self.assertTrue(r.get("refused"))
        self.assertTrue(r.get("have_nmap"))

    def test_refuses_apt_install(self):
        r = s.security_console_exec("apt install nmap")
        self.assertTrue(r.get("refused"))

    def test_refuses_hydra_wordlist(self):
        r = s.security_console_exec("hydra -l admin -P rockyou.txt https://example.com/login")
        self.assertTrue(r.get("refused"))

    def test_unknown_points_to_help(self):
        r = s.security_console_exec("msfconsole")
        self.assertTrue(r.get("refused") or "unknown" in (r.get("output") or "").lower())


class FrontendAndSql(unittest.TestCase):
    def test_console_card_and_prompt(self):
        for needle in ("Nmap / Port Scan", "YOU HAVE Nmap", "/api/security/nmap"):
            self.assertIn(needle, APP, needle)

    def test_price_list_locks_security_to_enterprise(self):
        self.assertIn("Nmap port scan (hosted TCP-connect inventory)", SRV)
        self.assertIn('("Nmap port scan (hosted TCP-connect inventory)", "❌", "❌", "❌", "❌", "✅")', SRV)
        self.assertIn("Nmap/SQLMap/Wireshark/John/Hydra locked until Enterprise", APP)

    def test_sql(self):
        self.assertIn("security_tools:patch54", SQL)
        self.assertIn("have_nmap", SQL)
        self.assertIn("zphisher", SQL)
        self.assertIn("/api/security/console", SQL)


if __name__ == "__main__":
    unittest.main()
