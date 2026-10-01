"""patch57: security tools live in chat only; admin user-base SQL + list header."""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
SQL = open(os.path.join(ROOT, "supabase_patch57_user_base.sql"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch58-media-honesty"'), 2)
        self.assertNotIn("Nmap / Port Scan", APP)
        self.assertIn("YOU HAVE Nmap", APP)
        self.assertIn("User base —", APP)
        self.assertIn("/api/admin/users/resync", APP)


class Sql(unittest.TestCase):
    def test_sql_copies_auth_users(self):
        self.assertIn("insert into public.user_flags", SQL)
        self.assertIn("from auth.users", SQL)
        self.assertIn("oracool_user_base", SQL)


class ChatStillHasNmap(unittest.TestCase):
    def test_do_you_have_nmap(self):
        out = s.auto_tools("do u have nmap", tier="enterprise", email="e@example.com")
        blob = " ".join(str(x.get("label")) + str(x.get("result")) for x in out)
        self.assertIn("have_nmap", blob)


if __name__ == "__main__":
    unittest.main()
