"""Patch 22 — install button / PWA on phones and every device.
Hermetic: static file serving + markup assertions, one ephemeral server."""
import json
import struct
import sys
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server as s  # noqa: E402


class InstallPwaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = s.ThreadingHTTPServer(("127.0.0.1", 0), s.Handler)
        t = threading.Thread(target=cls.server.serve_forever, daemon=True)
        t.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path))
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")

    # ---- manifest + icon + service worker --------------------------------
    def test_manifest_is_valid_pwa(self):
        st, raw, ct = self.get("/manifest.json")
        self.assertEqual(st, 200)
        self.assertIn("json", ct)
        m = json.loads(raw)
        self.assertEqual(m["name"], "OraCool AI")
        self.assertEqual(m["start_url"], "/app")
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m.get("id"), "/app")
        sizes = [i.get("sizes", "") for i in m.get("icons", [])]
        self.assertTrue(any("512" in sz for sz in sizes), "need a 512 icon for installability")

    def test_icon_is_real_512_png(self):
        st, raw, ct = self.get("/icon.png")
        self.assertEqual(st, 200)
        self.assertTrue(ct.startswith("image/"))
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", raw[16:24])
        self.assertEqual((w, h), (512, 512))

    def test_service_worker_served(self):
        st, raw, ct = self.get("/sw.js")
        self.assertEqual(st, 200)
        self.assertIn("javascript", ct)
        self.assertIn("fetch", raw.decode())

    # ---- app install button (phones + desktop) ----------------------------
    def test_app_has_install_button_everywhere(self):
        src = (ROOT / "index.html").read_text(errors="ignore")
        # header install button exists
        self.assertIn('id="installBtn"', src)
        # it must NOT be force-hidden (Patch 22 removed the mobile hide)
        self.assertNotIn("#installBtn{display:none !important}", src)
        # native prompt path
        self.assertIn("beforeinstallprompt", src)
        # per-device instructions path (iOS has no beforeinstallprompt)
        self.assertIn("Add to Home Screen", src)   # iOS instructions
        self.assertIn("Install app", src)          # Android instructions
        self.assertIn("openInstallHelp", src)
        # pre-signup (gate) affordance so phones can install before login
        self.assertIn('id="gInstall"', src)
        # PWA basics
        self.assertIn('rel="manifest"', src)
        self.assertIn("apple-touch-icon", src)
        self.assertIn("serviceWorker.register('/sw.js')", src)

    # ---- landing page install button --------------------------------------
    def test_landing_has_install_button(self):
        src = (ROOT / "landing.html").read_text(errors="ignore")
        self.assertIn('id="landInstall"', src)
        self.assertIn("beforeinstallprompt", src)
        self.assertIn("Add to Home Screen", src)
        self.assertIn("Install app", src)

    # ---- health marker ------------------------------------------------------
    def test_health_marker(self):
        st, raw, _ = self.get("/api/health")
        self.assertEqual(st, 200)
        expect = __import__("re").search(
            r'"build": "(patch[^"]+)"', (ROOT / "server.py").read_text(errors="ignore")).group(1)
        self.assertEqual(json.loads(raw)["build"], expect)
        self.assertEqual(json.loads(raw)["build"], "patch42-feed")


if __name__ == "__main__":
    unittest.main()
