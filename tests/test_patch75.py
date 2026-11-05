"""patch75: (1) the build bubble WRITES while building — client mirrors the live
step feed into the reply bubble (title + detail + dots) instead of silent dots;
(2) custom domains become truly Render-style — attaching registers the domain
on the Render service via API (cert auto-issues), Render-correct DNS records
(CNAME www -> oracoolai.com, A @ -> 216.24.57.1), and dns_status reports
hosting_registered."""
import json, os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
MARK = "patch80-trade-signals"


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count(MARK), 2)


class RenderHosting(unittest.TestCase):
    def test_register_success_and_409(self):
        for st in (201, 409):
            with mock.patch.object(s, "_render_cfg", lambda: ("k", "sid")), \
                 mock.patch.object(s, "http_fetch", lambda *a, **k: (st, "{}", {})):
                r = s._render_register_domain("mybrand.com")
            self.assertTrue(r["registered"])

    def test_register_without_keys_is_honest(self):
        with mock.patch.object(s, "_render_cfg", lambda: (None, None)):
            r = s._render_register_domain("mybrand.com")
        self.assertFalse(r["registered"])
        self.assertIn("queued", r["note"])

    def test_domain_state_lookup(self):
        rows = [{"custom_domain": {"name": "MyBrand.Com"}}]
        with mock.patch.object(s, "_render_cfg", lambda: ("k", "sid")), \
             mock.patch.object(s, "http_fetch", lambda *a, **k: (200, json.dumps(rows), {})):
            self.assertTrue(s._render_domain_state("mybrand.com"))
        with mock.patch.object(s, "_render_cfg", lambda: ("k", "sid")), \
             mock.patch.object(s, "http_fetch", lambda *a, **k: (200, "[]", {})):
            self.assertFalse(s._render_domain_state("mybrand.com"))

    def test_attach_returns_records_and_hosting(self):
        reg = {"mysite": {"owner": "u@x.com", "custom_domain": ""}}
        with mock.patch.object(s, "_sites_load", lambda: reg), \
             mock.patch.object(s, "_sites_save", lambda d: None), \
             mock.patch.object(s, "is_admin", lambda e: False), \
             mock.patch.object(s, "key", lambda k: None), \
             mock.patch.object(s, "_SITE_HOST_CACHE", {}), \
             mock.patch.object(s, "_render_register_domain", lambda d: {"registered": True, "note": "ok"}):
            r = s.site_domain_set("u@x.com", "mysite", "mybrand.com")
        self.assertTrue(r["ok"])
        self.assertTrue(r["hosting"]["registered"])
        recs = {x["name"]: x for x in r["records"]}
        self.assertEqual(recs["www"]["value"], "oracoolai.com")
        self.assertEqual(recs["@"]["value"], "216.24.57.1")

    def test_dns_status_includes_hosting(self):
        reg = {"mysite": {"owner": "u@x.com", "custom_domain": ""}}
        with mock.patch.object(s, "_sites_load", lambda: reg), \
             mock.patch.object(s, "is_admin", lambda e: False), \
             mock.patch.object(s, "_render_domain_state", lambda d: None):
            r = s.site_dns_status("u@x.com", "mysite")
        self.assertEqual(r["stage"], "none")
        self.assertIsNone(r["hosting_registered"])


class BubbleWriting(unittest.TestCase):
    def test_client_mirrors_steps_into_bubble(self):
        self.assertIn("Starting the build — imagining the concept", APP)
        self.assertIn("🏗 <b>'+escapeHtml(String(_cur.title).slice(0,60))+'</b>", APP)
        self.assertIn("the bubble WRITES while building", APP)

    def test_domain_ui_render_records(self):
        self.assertIn("'216.24.57.1'", APP)
        self.assertIn("Registered on the hosting layer", APP)


if __name__ == "__main__":
    unittest.main()
