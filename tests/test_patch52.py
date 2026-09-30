"""patch52: SQLMap-style SQLi audit joins the Enterprise security toolbox, with Supabase run-log migration."""
import io, json, os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
SQL = open(os.path.join(ROOT, "supabase_patch52_security_tools.sql"), encoding="utf-8").read() if os.path.exists(os.path.join(ROOT, "supabase_patch52_security_tools.sql")) else ""


class _Resp(io.BytesIO):
    def __init__(self, body, status=200, url="https://93.184.216.34/item?id=1"):
        super().__init__(body.encode() if isinstance(body, str) else body)
        self.status = status; self._url = url; self.headers = {}
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def geturl(self): return self._url


class Marker(unittest.TestCase):
    def test_marker_routes_and_catalog(self):
        self.assertEqual(SRV.count('"patch52-sqlmap-osint"'), 2)
        for needle in ("/api/security/sqlmap", "security_sqlmap_audit", "SQLMap-style SQLi audit",
                       '"sqlmap": "SQLMap-style low-impact SQL injection indicator audit',
                       '"security_tools": ["secscan", "sqlmap", "pcap", "hashaudit", "login_audit"]'):
            self.assertIn(needle, SRV, needle)

    def test_supabase_sql_file_complete(self):
        self.assertIn("create table if not exists public.security_tool_runs", SQL)
        self.assertIn("sqlmap", SQL)
        self.assertIn("enable row level security", SQL.lower())
        self.assertIn("oracool_security_tool_catalog", SQL)


class SQLMapTool(unittest.TestCase):
    def test_requires_authorization(self):
        r = s.security_sqlmap_audit("https://93.184.216.34/item?id=1", authorized=False)
        self.assertEqual(r["tool"], "sqlmap")
        self.assertIn("Authorization required", r["error"])

    def test_detects_sql_error_indicator(self):
        calls = []
        def fake_urlopen(req, timeout=0, context=None):
            url = req.full_url
            calls.append(url)
            if "%27" in url or "%22" in url:
                return _Resp("You have an error in your SQL syntax near '\\''", status=500, url=url)
            return _Resp("normal product page", status=200, url=url)
        with mock.patch.object(s.urllib.request, "urlopen", fake_urlopen):
            r = s.security_sqlmap_audit("https://93.184.216.34/item?id=1", authorized=True)
        self.assertEqual(r["tool"], "sqlmap")
        self.assertEqual(r["risk"], "high")
        self.assertEqual(r["flagged_parameters"], ["id"])
        self.assertIn("No crawling", r["limits"])
        self.assertGreaterEqual(len(calls), 2)

    def test_auto_tools_enterprise_and_locked(self):
        with mock.patch.object(s, "security_sqlmap_audit", lambda *a, **k: {"tool": "sqlmap", "risk": "low"}):
            out = s.auto_tools("sqlmap audit https://example.com/item?id=1 — I am authorized", tier="enterprise", email="e@example.com")
        self.assertTrue(any(x["tool"] == "sqlmap" for x in out), out)
        out2 = s.auto_tools("run sqlmap on https://example.com/item?id=1", tier="pro", email="p@example.com")
        self.assertTrue(any(x["tool"] == "locked" and "SQLMap-style" in x["label"] for x in out2), out2)


if __name__ == "__main__":
    unittest.main()
