"""patch53: security tools are visible in the Intel UI and Admin can repair a collapsed user list."""
import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch53-tools-ui-adminfix"'), 2)
        self.assertIn('/api/admin/users/resync', SRV)
        self.assertIn('admin_users_resync', SRV)


class IntelUI(unittest.TestCase):
    def test_security_tools_visible_in_intel_panel(self):
        for needle in ("Nmap / Port Scan", "SQLMap SQLi Audit", "Wireshark PCAP",
                       "John the Ripper Audit", "Hydra Login Defense",
                       "/api/security/nmap", "/api/security/sqlmap", "/api/security/pcap",
                       "/api/security/hash", "/api/security/login",
                       "id=\"secAuth\"", "Repair user list"):
            self.assertIn(needle, APP, needle)
        self.assertIn("Enterprise security toolbox appears in Intel", APP)


class AdminRepair(unittest.TestCase):
    def test_admin_payload_sources_and_resync(self):
        users = [{"email": "a@example.com", "created_at": "2026-09-01T00:00:00Z", "email_confirmed_at": "2026-09-01T00:00:00Z"},
                 {"email": "b@example.com", "created_at": "2026-09-02T00:00:00Z"}]
        with mock.patch.object(s, "key", lambda n, d="": {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "svc"}.get(n, "")), \
             mock.patch.object(s, "supabase_auth_users", lambda: users), \
             mock.patch.object(s, "supabase_all_flags", lambda: {}), \
             mock.patch.object(s, "load_users", lambda: {}), \
             mock.patch.object(s, "load_subscribers", lambda: []), \
             mock.patch.object(s, "_load_accounts", lambda: {}), \
             mock.patch.object(s, "is_admin", lambda e: False):
            s._SUPABASE_AUTH_USERS_LAST = {"ok": True, "count": 2, "error": ""}
            p = s.admin_users_payload("owner@example.com")
        self.assertEqual(p["stats"]["users"], 2)
        self.assertEqual(p["sources"]["auth_users"], 2)

    def test_resync_reports_missing_service_key(self):
        with mock.patch.object(s, "key", lambda n, d="": ""):
            r = s.admin_users_resync("admin@example.com")
        self.assertIn("SUPABASE_SERVICE_KEY", r["error"])


if __name__ == "__main__":
    unittest.main()
