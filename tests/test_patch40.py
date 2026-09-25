"""patch40: live build steps, licensed image collection, voice watchdog, camera video clips."""
import os, sys, json, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
VOICE = open(os.path.join(ROOT, "voice-reminders.js"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch46-durable"'), 2)
        self.assertNotIn("patch39-arena", SRV)
        self.assertIn('/voice-reminders.js?v=40', APP)


class BuildProgress(unittest.TestCase):
    def test_steps_lifecycle(self):
        s._bp_reset("Who@X.com", "Test Site")
        s._bp_step("who@x.com", "Reading the brief", "12 words")
        s._bp_step("who@x.com", "Writing index.html", "builder")
        p = s.build_progress("WHO@x.com")
        self.assertTrue(p["active"]); self.assertFalse(p["done"])
        self.assertEqual([x["title"] for x in p["steps"]], ["Reading the brief", "Writing index.html"])
        self.assertFalse(p["steps"][0]["running"]); self.assertTrue(p["steps"][1]["running"])
        self.assertIsInstance(p["steps"][0]["ms"], int)
        s._bp_done("who@x.com", True, "/builds/test-site/")
        p = s.build_progress("who@x.com")
        self.assertTrue(p["done"]); self.assertTrue(p["ok_build"]); self.assertEqual(p["result"], "/builds/test-site/")
        self.assertFalse(any(x["running"] for x in p["steps"]))
        self.assertIsInstance(p["total_ms"], int)

    def test_unknown_email_is_inactive(self):
        p = s.build_progress("nobody-here@x.com")
        self.assertTrue(p["ok"]); self.assertFalse(p["active"]); self.assertEqual(p["steps"], [])

    def test_route_and_build_site_wiring(self):
        self.assertIn('elif path == "/api/builds/progress":', SRV)
        self.assertIn('build_progress(body.get("email"))', SRV)
        # progress is emitted from inside build_site so the client can show Arena-style running steps
        self.assertIn('_bp_step(email, "Reading the brief"', SRV)
        self.assertIn('_bp_step(email, "Collecting licensed photos"', SRV)
        self.assertIn('_bp_step(email, "Building " + (name or "the site")', SRV)
        self.assertIn('_bp_step(email, "Preview ready"', SRV)
        self.assertNotIn("gpt-oss-120b\")", SRV.split("def build_site(")[1].split("def build_edit(")[0].split("_bp_step")[0] + "")  # no engine names in user-visible step text


class ImageSearch(unittest.TestCase):
    def _fetch(self, url, **kw):
        if "openverse" in url:
            body = {"results": [
                {"url": "https://live.staticflickr.com/1/a_b.jpg", "thumbnail": "https://t/1", "title": "Hotel pool",
                 "creator": "Ada", "license": "by", "license_version": "2.0", "width": 1200, "height": 800, "source": "flickr"},
                {"url": "https://live.staticflickr.com/1/small.jpg", "thumbnail": "https://t/2", "title": "tiny",
                 "creator": "B", "license": "by", "width": 300, "height": 200, "source": "flickr"},
                {"url": "https://live.staticflickr.com/1/a_b.jpg", "thumbnail": "https://t/1", "title": "dup",
                 "creator": "Ada", "license": "by", "width": 1200, "height": 800, "source": "flickr"},
            ]}
            return 200, json.dumps(body).encode(), {}
        if "wikimedia" in url or "commons" in url:
            body = {"query": {"pages": {"1": {"title": "File:Lobby.jpg", "imageinfo": [{
                "url": "https://upload.wikimedia.org/x/Lobby.jpg", "thumburl": "https://upload.wikimedia.org/x/1400px-Lobby.jpg",
                "mime": "image/jpeg", "width": 4000, "height": 3000,
                "extmetadata": {"Artist": {"value": "<a href='#'>Chidi</a>"}, "LicenseShortName": {"value": "CC BY-SA 4.0"}}}]}}}}
            return 200, json.dumps(body).encode(), {}
        return 500, b"", {}

    def test_openverse_filters_small_and_duplicates(self):
        with mock.patch.object(s, "http_fetch", side_effect=self._fetch), mock.patch.object(s, "key", lambda k, d="": ""):
            out = s.image_search("hotel pool", 6)
        urls = [x["url"] for x in out]
        self.assertIn("https://live.staticflickr.com/1/a_b.jpg", urls)
        self.assertNotIn("https://live.staticflickr.com/1/small.jpg", urls)
        self.assertEqual(urls.count("https://live.staticflickr.com/1/a_b.jpg"), 1)
        first = out[0]
        for k in ("url", "thumb", "title", "author", "license", "source", "w", "h"):
            self.assertIn(k, first)
        self.assertEqual(first["license"], "CC BY")

    def test_wikimedia_fallback_when_openverse_fails(self):
        def fetch(url, **kw):
            if "openverse" in url:
                raise RuntimeError("down")
            return self._fetch(url, **kw)
        with mock.patch.object(s, "http_fetch", side_effect=fetch), mock.patch.object(s, "key", lambda k, d="": ""):
            out = s.image_search("hotel lobby", 4)
        self.assertTrue(out)
        self.assertEqual(out[0]["source"], "wikimedia")
        self.assertEqual(out[0]["url"], "https://upload.wikimedia.org/x/1400px-Lobby.jpg")
        self.assertEqual(out[0]["author"], "Chidi")

    def test_never_raises(self):
        with mock.patch.object(s, "http_fetch", side_effect=RuntimeError("offline")), mock.patch.object(s, "key", lambda k, d="": ""):
            self.assertEqual(s.image_search("anything", 3), [])
            self.assertEqual(s.image_search("", 3), [])

    def test_build_library_uses_industry_queries_and_caps(self):
        calls = []
        def fake(q, n=6):
            calls.append(q)
            return [{"url": "https://img/%s/%d.jpg" % (q.replace(" ", "_"), i), "thumb": "", "title": q, "author": "A",
                     "license": "CC BY", "source": "flickr", "w": 1200, "h": 800} for i in range(n)]
        with mock.patch.object(s, "image_search", side_effect=fake):
            bp = s._brief_blueprint("a boutique hotel in Abeokuta with pool and restaurant")
            lib = s._build_image_library("a boutique hotel in Abeokuta with pool and restaurant", bp)
        self.assertTrue(1 <= len(lib) <= 9)
        self.assertTrue(any("hotel" in c for c in calls), calls)

    def test_chat_tool_and_route(self):
        with mock.patch.object(s, "image_search", lambda q, n=6: [{"url": "https://img/1.jpg", "thumb": "", "title": "Lagos skyline",
                                                                   "author": "Ada", "license": "CC BY", "source": "flickr", "w": 1200, "h": 800}]):
            runs = s.auto_tools("find me 3 images of lagos skyline", tier="free", email="a@x.com")
        self.assertTrue(runs and runs[0]["tool"] == "images", runs)
        self.assertIn("https://img/1.jpg", runs[0]["result"])
        self.assertIn('path in ("/api/builds/images", "/api/images/search")', SRV)
        # generation requests are not hijacked by the collector
        with mock.patch.object(s, "image_search", lambda q, n=6: [{"url": "x"}]):
            runs = s.auto_tools("generate an image of a lion", tier="free", email="a@x.com")
        self.assertFalse(any(r["tool"] == "images" for r in runs))

    def test_builder_prompt_gets_image_library(self):
        self.assertIn("IMAGE LIBRARY", SRV)
        self.assertIn("_build_image_library(prompt, _bpt)", SRV)
        self.assertIn('loading=\\"lazy\\"', SRV)


class Client(unittest.TestCase):
    def test_steps_card(self):
        self.assertIn("function stepsCard(anchor, opts)", APP)
        self.assertIn("function wantsBuild(q)", APP)
        self.assertIn("post('/api/builds/progress',{})", APP)
        self.assertIn("_steps=stepsCard(b)", APP)
        self.assertIn("_steps.finish()", APP)
        self.assertIn("' · '+steps.length+' steps'", APP)

    def test_video_recording(self):
        self.assertIn("function videoAuto(seconds)", APP)
        self.assertIn("new MediaRecorder(stream", APP)
        self.assertIn("video/webm;codecs=vp8,opus", APP)
        self.assertIn("Download clip", APP)
        # routing: video/camera commands are handled before the generic app launcher
        i_video = APP.index("const _vm=t.match(")
        i_smart = APP.index("[/^open\\s+(?!(?:my|the)?\\s*(?:camera|webcam|video|mic|microphone|screen)\\b)")
        self.assertLess(i_video, i_smart)
        self.assertIn("if(/\\bopen\\s+(?:my\\s+|the\\s+)?(?:camera|webcam|front camera|selfie camera)\\b/.test(t)", APP)

    def test_video_regex_matches_user_phrases(self):
        import re
        rx = re.compile(r"\b(?:record|take|shoot|make|capture|film|start)\b.{0,30}?\b(?:video|clip|recording|reel|footage)\b|\b(?:video|clip)\s+of\s+(?:me|myself|my face)\b|\bfilm me\b|\brecord me\b")
        for ok in ["open my camera and record clip", "take a video of me", "record a 15 second clip", "film me", "make a short video"]:
            self.assertTrue(rx.search(ok), ok)
        for no in ["open whatsapp", "take a photo of me", "what is a video codec"]:
            self.assertFalse(rx.search(no), no)
        smart = re.compile(r"^open\s+(?!(?:my|the)?\s*(?:camera|webcam|video|mic|microphone|screen)\b)([a-z0-9 .+-]{2,30}?)\s*(?:app|application)?\s*(?:for me)?[.!?]?$")
        self.assertFalse(smart.match("open my camera"))
        self.assertFalse(smart.match("open camera"))
        self.assertTrue(smart.match("open whatsapp"))
        self.assertTrue(smart.match("open my notes app"))

    def test_speech_watchdogs(self):
        # text-mode speak(): sentence chunks + never-started / never-ended recovery + resume ticker
        self.assertIn("window.__speakEpoch", APP)
        self.assertIn("const wdStart=setTimeout(", APP)
        self.assertIn("const wdEnd=setTimeout(", APP)
        self.assertIn("speechSynthesis.paused) speechSynthesis.resume()", APP)
        self.assertIn("tap <b>🔊 Speak</b>", APP)
        # conversation mode pump(): same recovery, activeSpeech can never stay stuck
        self.assertIn("const wdStart=setTimeout(()=>{if(started||finished)return;", VOICE)
        self.assertIn("const wdEnd=setTimeout(()=>{if(finished)return;", VOICE)
        self.assertIn("speechQueue.unshift(text)", VOICE)
        self.assertIn("if(clean.length<=220){speechQueue.push(clean);}", VOICE)


if __name__ == "__main__":
    unittest.main()


class EarlyStream(unittest.TestCase):
    """The site builder can take 1–2 minutes; the browser gives up after 45s without headers.
    patch40 opens the event-stream first and pings while slow tools run."""
    def test_slow_tool_detector(self):
        for t in ["build me a website for a bakery called Sweet Crumbs", "generate an image of a lion",
                  "make a video of a sunset", "create a landing page for my gym"]:
            self.assertTrue(s._looks_slow_tool(t), t)
        for t in ["what is 2+2", "how do I build a website", "open whatsapp", "hi"]:
            self.assertFalse(s._looks_slow_tool(t), t)

    def test_wiring(self):
        self.assertIn("def _sse_begin(self):", SRV)
        self.assertIn('_slow = stream and (_mode == "build" or _looks_slow_tool(last_user))', SRV)
        self.assertIn('self.wfile.write(b": ping\\n\\n")', SRV)
        self.assertIn('if not getattr(self, "_sse_on", False):\n            self._sse_on = True\n            self.send_response(200)', SRV)
        # once the stream is open, JSON errors are delivered as SSE frames instead of a second HTTP response
        self.assertIn('if getattr(self, "_sse_on", False):\n            # the stream is already open', SRV)

    def test_send_json_after_sse_emits_frames(self):
        import io
        class H:  # minimal stand-in for the handler
            _sse_on = True
            wfile = io.BytesIO()
        h = H()
        s.Handler._send_json(h, {"error": "No AI key"}, 400)
        out = h.wfile.getvalue().decode()
        self.assertIn('data: {"error": "No AI key"}', out)
        self.assertTrue(out.rstrip().endswith("data: [DONE]"))

    def test_site_name_extraction(self):
        cases = {"build a website for a bakery called Sweet Crumbs with a menu and order form": "Sweet Crumbs",
                 "create a site named Lagos Fresh Foods": "Lagos Fresh Foods",
                 'build a hotel website called "Becfom Hotel" in Abeokuta': "Becfom Hotel",
                 "make a portfolio called Dan's Studio, dark theme": "Dan's Studio"}
        for t, exp in cases.items():
            m = s._NAME_RX.search(t)
            self.assertEqual(m.group(1).strip(" ."), exp, t)
