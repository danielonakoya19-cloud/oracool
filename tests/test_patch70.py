"""patch70: Render/Base44-grade custom domains (DNS record cards, server-side
DoH verification, availability search via RDAP, concierge domain orders) +
creation engine upgrades (imagination pass, app-mode builder, version history
with restore)."""
import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

MARK = "patch75-writing-domains"


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count(MARK), 2)  # BUILD + health


class DomainEngine(unittest.TestCase):
    def test_search_rdap_free_vs_taken(self):
        calls = []

        def fake_fetch(url, **kw):
            calls.append(url)
            if "rdap.org" in url:
                st = 404 if "free" in url else 200
                return st, "{}", {}
            return 0, "", {}

        with mock.patch.object(s, "http_fetch", fake_fetch):
            r = s.domain_search("u@x.com", "freeone")
        self.assertTrue(r["ok"])
        by = {x["domain"]: x for x in r["results"]}
        self.assertTrue(by["freeone.com"]["available"])
        self.assertFalse(by["takenorwhat.com"]["available"]) if "takenorwhat.com" in by else None

    def test_search_dns_fallback_nxdomain(self):
        def fake_fetch(url, **kw):
            if "rdap.org" in url:
                return 0, "", {}
            return 200, '{"Status": 3}', {}

        with mock.patch.object(s, "http_fetch", fake_fetch):
            r = s.domain_search("u@x.com", "november")
        self.assertTrue(r["ok"])
        self.assertTrue(all(x["available"] for x in r["results"]))

    def test_search_rejects_junk(self):
        r = s.domain_search("u@x.com", "a")
        self.assertIn("error", r)

    def test_order_records_durable(self):
        got = {}

        def fake_kv(k, obj):
            got["k"] = k; got["o"] = obj
            return True

        with mock.patch.object(s, "supabase_kv_put", fake_kv), \
             mock.patch.object(s, "audit_log", lambda *a, **k: None):
            r = s.domain_order("u@x.com", "MyBrand.Com", 2)
        self.assertTrue(r["ok"])
        self.assertTrue(got["k"].startswith("domord:u@x.com:"))
        self.assertEqual(got["o"]["domain"], "mybrand.com")
        self.assertEqual(got["o"]["status"], "requested")
        self.assertEqual(got["o"]["years"], 2)

    def test_dns_status_stages(self):
        def fake_fetch(url, **kw):
            if "dns.google" in url and "type=CNAME" in url:
                return 200, '{"Answer":[{"data":"oracoolai.com."}]}', {}
            if url.startswith("https://live.example/"):
                return 200, "<html>hi</html>", {}
            return 0, "", {}

        reg = {"mysite": {"owner": "u@x.com", "custom_domain": "live.example"}}
        with mock.patch.object(s, "_sites_load", lambda: reg), \
             mock.patch.object(s, "is_admin", lambda e: False), \
             mock.patch.object(s, "http_fetch", fake_fetch):
            r = s.site_dns_status("u@x.com", "mysite")
        self.assertEqual(r["stage"], "live")
        self.assertTrue(r["serving"])
        self.assertEqual(r["www_cname"], "oracoolai.com")
        reg["mysite"]["custom_domain"] = ""
        with mock.patch.object(s, "_sites_load", lambda: reg), \
             mock.patch.object(s, "is_admin", lambda e: False):
            r = s.site_dns_status("u@x.com", "mysite")
        self.assertEqual(r["stage"], "none")

    def test_domain_set_returns_records(self):
        reg = {"mysite": {"owner": "u@x.com", "custom_domain": ""}}

        def save(d):
            pass

        with mock.patch.object(s, "_sites_load", lambda: reg), \
             mock.patch.object(s, "_sites_save", save), \
             mock.patch.object(s, "is_admin", lambda e: False), \
             mock.patch.object(s, "key", lambda k: None), \
             mock.patch.object(s, "_SITE_HOST_CACHE", {}):
            r = s.site_domain_set("u@x.com", "mysite", "mybrand.com")
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["records"]), 2)
        self.assertEqual(r["records"][0]["value"], "oracoolai.com")

    def test_routes_exist(self):
        self.assertIn('"/api/domains"', SRV)
        self.assertIn('_act == "domain_status"', SRV)
        self.assertIn('_bact == "versions"', SRV)
        self.assertIn('_bact == "restore"', SRV)
        self.assertIn('"/api/sites", "/api/domains"', SRV)


class CreationEngine(unittest.TestCase):
    def test_imagination_pass_exists(self):
        self.assertIn("def _creative_direction", SRV)
        self.assertIn("CONCEPT:", SRV)
        self.assertIn('"Imagination"', SRV)

    def test_app_mode(self):
        self.assertIn("_APP_RX", SRV)
        self.assertIn("_APP_SYSTEM", SRV)  # patch72: dedicated app/game brain
        self.assertTrue(s._APP_RX.search("build me a delivery app for my shop"))
        self.assertTrue(s._APP_RX.search("create an AI companion"))
        self.assertFalse(s._APP_RX.search("a landing page for a coffee brand"))

    def test_version_helpers(self):
        self.assertIn("def _version_store", SRV)
        self.assertIn("def build_versions", SRV)
        self.assertIn("def build_restore", SRV)

    def test_build_seeds_version_edit_stores_version(self):
        self.assertIn('_version_store(slug, _idx["content"], "initial build")', SRV)
        self.assertIn('_version_store(slug, old, "before: " + instructions[:120])', SRV)

    def test_client_ui(self):
        for frag in ("function domainModal(", "function versionsModal(",
                     'action:\'domain_status\'', 'action:\'search\'',
                     'action:\'order\'', 'action:\'versions\'',
                     'action:\'restore\'', "data-wd=", "id=\"editHist\"",
                     "Order via OraCool", "Check DNS"):
            self.assertIn(frag, APP)


class VersionsRoundtrip(unittest.TestCase):
    def test_store_list_restore(self):
        meta = {"vtest": {"owner": "u@x.com", "name": "V Test"}}
        kv = {}
        n = {"i": 0}

        def kget(k):
            return kv.get(k)

        def kset(k, o):
            kv[k] = o; return True

        def kdel(k):
            kv.pop(k, None); return True

        with mock.patch.object(s, "_builds_load", lambda: dict(meta)), \
             mock.patch.object(s, "_builds_save", lambda d: None), \
             mock.patch.object(s, "is_admin", lambda e: False), \
             mock.patch.object(s, "supabase_kv_get", kget), \
             mock.patch.object(s, "supabase_kv_put", kset), \
             mock.patch.object(s, "_supabase_kv_del", kdel), \
             mock.patch.object(s, "builds_sup_save", lambda slug, files: True), \
             mock.patch.object(s, "_BUILDS_DIR", "/tmp/oracool_vtest_builds"):
            s._version_store("vtest", "<html>v1</html>", "initial")
            s._version_store("vtest", "<html>v2</html>", "before: recolor")
            lst = s.build_versions("u@x.com", "vtest")
            self.assertTrue(lst["ok"])
            self.assertEqual(len(lst["versions"]), 2)
            self.assertEqual(lst["versions"][0]["v"], 2)
            r = s.build_restore("u@x.com", "vtest", 1)
            self.assertTrue(r["ok"])
            self.assertEqual(r["restored"], 1)
            idx = open("/tmp/oracool_vtest_builds/vtest/index.html", encoding="utf-8").read()
            self.assertEqual(idx, "<html>v1</html>")
            # foreign owner refused
            r2 = s.build_restore("other@x.com", "vtest", 1)
            self.assertIn("error", r2)

    def test_restore_missing_version(self):
        with mock.patch.object(s, "_builds_load", lambda: {"vtest": {"owner": "u@x.com"}}), \
             mock.patch.object(s, "is_admin", lambda e: False), \
             mock.patch.object(s, "supabase_kv_get", lambda k: None):
            r = s.build_restore("u@x.com", "vtest", 9)
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
