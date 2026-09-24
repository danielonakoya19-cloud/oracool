"""Patch 21 — Wan 2.2 video generation (Hugging Face models) wired into the
video cascade. Hermetic: no network; providers are mocked at the HTTP layer."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class FakeResp:
    def __init__(self, data):
        self._data = data if isinstance(data, bytes) else data.encode()
        self.headers = {}

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSSE:
    """readline() iterator over SSE lines; EOF after the last one."""

    def __init__(self, lines):
        self._lines = [l.encode() for l in lines]

    def readline(self):
        return self._lines.pop(0) if self._lines else b""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class BuildMarkerTests(unittest.TestCase):
    def test_patch21_marker(self):
        src = (ROOT / "server.py").read_text(errors="ignore")
        self.assertIn('"build": "patch36-visuals"', src)


class CascadeOrderTests(unittest.TestCase):
    def setUp(self):
        self._keys = dict(server.KEYS)
        server.KEYS.pop("HIA_API_KEY", None)
        server.KEYS.pop("HF_TOKEN", None)

    def tearDown(self):
        server.KEYS.clear()
        server.KEYS.update(self._keys)

    def test_wan22_runs_after_agnes_before_hiapi(self):
        calls = []
        with mock.patch.object(server, "_agnes_video",
                               side_effect=lambda *a, **k: calls.append("agnes") or {"error": "queue full"}), \
             mock.patch.object(server, "_hf_wan22_video",
                               side_effect=lambda *a, **k: calls.append("wan22") or {"ok": True, "urls": ["https://x/v.mp4"], "model": "Wan-AI/Wan2.2-I2V-A14B", "via": "HF Space tmtanu"}), \
             mock.patch.object(server, "hiapi_submit",
                               side_effect=lambda *a, **k: calls.append("hiapi") or ("", "no key")):
            r = server.gen_video("a whale swimming under a full moon")
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("provider"), "hf-wan22")
        self.assertEqual(calls[:2], ["agnes", "wan22"])
        self.assertNotIn("hiapi", calls)

    def test_wan22_skipped_when_audio_requested(self):
        calls = []
        with mock.patch.object(server, "_agnes_video",
                               side_effect=lambda *a, **k: calls.append("agnes") or {"error": "queue full"}), \
             mock.patch.object(server, "_hf_wan22_video",
                               side_effect=lambda *a, **k: calls.append("wan22") or {"ok": True, "urls": ["u"], "model": "m", "via": "v"}), \
             mock.patch.object(server, "_cvron_video",
                               side_effect=lambda *a, **k: calls.append("cvron") or {"error": "down"}):
            r = server.gen_video("a whale singing", want_audio=True)
        self.assertIn("error", r)
        self.assertNotIn("wan22", calls)  # Wan 2.2 renders silent video

    def test_final_error_is_honest_no_topup_links(self):
        with mock.patch.object(server, "_agnes_video", return_value={"error": "queue full"}), \
             mock.patch.object(server, "_hf_wan22_video", return_value={"error": "Wan2.2: all spaces unhealthy"}), \
             mock.patch.object(server, "_cvron_video", return_value={"error": "cvron wan22: down"}):
            r = server.gen_video("a whale in the deep ocean")
        err = r.get("error", "")
        self.assertIn("Wan2.2", err)
        self.assertNotIn("top up at hiapi", err)
        self.assertNotIn("nexawapi", err)


class HfRouterTests(unittest.TestCase):
    def setUp(self):
        self._keys = dict(server.KEYS)
        server.KEYS.pop("HF_TOKEN", None)

    def tearDown(self):
        server.KEYS.clear()
        server.KEYS.update(self._keys)
        try:
            for f in os.listdir(server._gen_dir()):
                os.remove(os.path.join(server._gen_dir(), f))
        except Exception:
            pass

    def test_no_token_is_a_clean_skip(self):
        r = server._hf_router_video("Wan-AI/Wan2.2-T2V-A14B", "prompt here")
        self.assertEqual(r.get("error"), "no HF_TOKEN")

    def test_direct_video_bytes_saved(self):
        server.KEYS["HF_TOKEN"] = "hf_test"
        video = b"\x00\x01fake-mp4-bytes" * 500
        with mock.patch.object(server, "http_fetch",
                               return_value=(200, video, "video/mp4")):
            r = server._hf_router_video("Wan-AI/Wan2.2-T2V-A14B", "a calm lake at dawn")
        self.assertTrue(r.get("ok"), r)
        u = r["urls"][0]
        self.assertTrue(u.startswith("/generated/hf-"))
        self.assertTrue(os.path.exists(os.path.join(server._gen_dir(), os.path.basename(u))))

    def test_202_job_then_video(self):
        server.KEYS["HF_TOKEN"] = "hf_test"
        video = b"mp4data" * 4000
        calls = []

        def fake_fetch(url, method="GET", **kw):
            calls.append(url)
            if url.endswith("/v1/videos"):
                return (202, json.dumps({"id": "job-123"}).encode(), "application/json")
            return (200, video, "video/mp4")
        with mock.patch.object(server, "http_fetch", side_effect=fake_fetch), \
             mock.patch.object(server.time, "sleep", lambda s: None):
            r = server._hf_router_video("Wan-AI/Wan2.2-I2V-A14B", "kayaker on a river")
        self.assertTrue(r.get("ok"), r)
        self.assertIn("job-123", calls[-1])

    def test_failed_job_reports_error(self):
        server.KEYS["HF_TOKEN"] = "hf_test"

        def fake_fetch(url, method="GET", **kw):
            if url.endswith("/v1/videos"):
                return (202, json.dumps({"id": "job-9"}).encode(), "application/json")
            return (200, json.dumps({"status": "failed", "failure": "provider down"}).encode(),
                    "application/json")
        with mock.patch.object(server, "http_fetch", side_effect=fake_fetch), \
             mock.patch.object(server.time, "sleep", lambda s: None):
            r = server._hf_router_video("Wan-AI/Wan2.2-TI2V-5B", "a cat")
        self.assertIn("failed", str(r.get("error")))


class SpaceHealthTests(unittest.TestCase):
    def test_parses_monitoring_success_rate(self):
        payload = json.dumps({"functions": {"generate_video": {"success_rate": 0.62, "total_requests": 137}}})
        with mock.patch.object(server, "http_fetch", return_value=(200, payload.encode(), "application/json")):
            self.assertAlmostEqual(server._hf_space_health("https://s.hf.space"), 0.62)

    def test_low_request_count_means_unknown(self):
        payload = json.dumps({"functions": {"generate_video": {"success_rate": 0.9, "total_requests": 3}}})
        with mock.patch.object(server, "http_fetch", return_value=(200, payload.encode(), "application/json")):
            self.assertEqual(server._hf_space_health("https://s.hf.space"), 0.0)

    def test_unreachable_space_is_zero(self):
        import urllib.error
        with mock.patch.object(server, "http_fetch",
                               side_effect=urllib.error.URLError("down")):
            self.assertEqual(server._hf_space_health("https://s.hf.space"), 0.0)


class SpaceVideoTests(unittest.TestCase):
    BASE = "https://tmtanu-wan2-2-14b-i2v-480p-lightning-nsfw-diffusers.hf.space"

    def _fake_urlopen(self, kind="success"):
        sse = FakeSSE([
            "event: generating",
            "data: null",
            "event: complete",
            'data: {"data": [{"url": "/file=w22-out.mp4"}, {"url": "/file=w22-dl.mp4"}, 7]}\n',
        ]) if kind == "success" else FakeSSE(["event: error", "data: null"])

        def fake(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if url.endswith("/gradio_api/upload"):
                return FakeResp(json.dumps(["/tmp/gradio/frame.jpg"]))
            if url.endswith("/gradio_api/call/generate_video"):
                return FakeResp(json.dumps({"event_id": "e-1"}))
            if "/gradio_api/call/generate_video/e-1" in url:
                return sse
            return FakeResp(b"fake-image-bytes")
        return fake

    def test_success_returns_prefixed_url(self):
        with mock.patch.object(server.urllib.request, "urlopen",
                               side_effect=lambda *a, **k: self._fake_urlopen("success")(*a, **k)):
            r = server._hf_space_video(self.BASE, "waves on a shore", "https://img.example/f.jpg")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["urls"][0], self.BASE + "/file=w22-out.mp4")

    def test_space_job_failure_is_an_error(self):
        with mock.patch.object(server.urllib.request, "urlopen",
                               side_effect=lambda *a, **k: self._fake_urlopen("error")(*a, **k)):
            r = server._hf_space_video(self.BASE, "waves on a shore", "https://img.example/f.jpg")
        self.assertIn("error", r)


class Wan22FreePathTests(unittest.TestCase):
    def setUp(self):
        self._keys = dict(server.KEYS)
        server.KEYS.pop("HF_TOKEN", None)

    def tearDown(self):
        server.KEYS.clear()
        server.KEYS.update(self._keys)

    def test_unhealthy_space_is_skipped_with_reason(self):
        with mock.patch.object(server, "_hf_space_health", return_value=0.05), \
             mock.patch.object(server, "_cvron_image", return_value={"ok": True, "urls": ["https://i/x.jpg"]}), \
             mock.patch.object(server, "_hf_space_video", return_value={"ok": True, "urls": ["u"]}) as sv:
            r = server._hf_wan22_video("a forest")
        self.assertIn("error", r)
        self.assertIn("space unhealthy", r["error"])
        sv.assert_not_called()

    def test_healthy_space_frame_from_cvron(self):
        with mock.patch.object(server, "_hf_space_health", return_value=0.8), \
             mock.patch.object(server, "_cvron_image", return_value={"ok": True, "urls": ["https://i/x.jpg"]}), \
             mock.patch.object(server, "_hf_space_video", return_value={"ok": True, "urls": ["https://v/y.mp4"]}) as sv:
            r = server._hf_wan22_video("a forest at night")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["urls"], ["https://v/y.mp4"])
        self.assertIn("Wan2.2-I2V-A14B", r["model"])
        sv.assert_called_once()
        args = sv.call_args[0]
        self.assertEqual(args[2], "https://i/x.jpg")  # frame was passed through


if __name__ == "__main__":
    unittest.main()
