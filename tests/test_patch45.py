"""patch45: the live build feed rides inside the chat event-stream (__steps frames) — no polling, no identity mismatch;
descriptive build names for anonymous briefs."""
import os, sys, io, json, time, threading, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch46-durable"'), 2)
        self.assertNotIn("patch44-vision", SRV)


class _FakeHeaders(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def _fake_handler():
    h = s.Handler.__new__(s.Handler)
    h.wfile = io.BytesIO()
    h.rfile = io.BytesIO()
    h.headers = _FakeHeaders({"Host": "localhost:8000"})
    h.client_address = ("127.0.0.1", 1)
    h.request_version = "HTTP/1.1"
    h.command = "POST"
    h.path = "/api/chat"
    h.requestline = "POST /api/chat HTTP/1.1"
    h.send_response = lambda *a, **k: None
    h.send_header = lambda *a, **k: None
    h.end_headers = lambda *a, **k: None
    h.log_message = lambda *a, **k: None
    return h


def _frames(raw):
    out = []
    for chunk in raw.decode("utf-8", "replace").split("\n\n"):
        for line in chunk.split("\n"):
            if line.startswith("data:"):
                try:
                    out.append(json.loads(line[5:].strip()))
                except Exception:
                    pass
    return out


class InlineFeed(unittest.TestCase):
    def test_chat_stream_carries_the_build_feed(self):
        email = "feed45@example.invalid"

        def fake_tools(text, tier="free", ha_url=None, ha_token=None, email=None, crypto_site="", history=None, force_build=False):
            s._bp_reset(email, "Fashion Designer")
            s._bp_step(email, "Reading the brief", text[:40]); time.sleep(0.9)
            s._bp_step(email, "Building Fashion Designer", "thinking…", kind="build"); time.sleep(0.9)
            s._bp_step(email, "Writing the hero section", "6.1 KB written"); time.sleep(0.9)
            s._bp_step(email, "Preview ready", "/builds/fashion-designer/")
            s._bp_done(email, True, "/builds/fashion-designer/")
            return [{"tool": "build", "label": "build · Fashion Designer", "result": "{\"ok\": true}"}]

        h = _fake_handler()
        body = {"messages": [{"role": "user", "content": "create a website for a fashion designer"}], "stream": True,
                "provider": "auto", "email": email, "_verified_email": email, "tools": True}
        with mock.patch.object(s, "auto_tools", fake_tools), \
             mock.patch.object(s, "check_tier", lambda e: "pro"), \
             mock.patch.object(s, "is_blocked", lambda e: False), \
             mock.patch.object(s, "chat_touch_async", lambda *a, **k: None), \
             mock.patch.object(s.Handler, "_resolve_provider", lambda self, b: (None, None, None, "none")):
            try:
                h._handle_chat(body)
            except Exception:
                pass  # the reply-model leg is not under test; the feed frames are already on the wire
        fr = _frames(h.wfile.getvalue())
        self.assertTrue(any(f.get("__progress") == "build" for f in fr), "announces the build")
        steps = [f["__steps"] for f in fr if f.get("__steps")]
        self.assertGreaterEqual(len(steps), 3, "several live frames while the tool ran")
        titles = [x["title"] for x in steps[-1]["steps"]]
        self.assertEqual(titles, ["Reading the brief", "Building Fashion Designer", "Writing the hero section", "Preview ready"])
        self.assertTrue(steps[-1]["done"], "the final frame is the finished feed")
        self.assertEqual(steps[-1]["name"], "Fashion Designer")
        # each frame adds information (no duplicate spam)
        sigs = [(len(p["steps"]), p["steps"][-1]["detail"], p["done"]) for p in steps]
        self.assertEqual(len(sigs), len(set(sigs)))

    def test_old_finished_record_is_not_replayed(self):
        email = "stale45@example.invalid"
        s._bp_reset(email, "Old"); s._bp_step(email, "Preview ready", "/builds/old/"); s._bp_done(email, True, "/builds/old/")
        s._BUILD_PROGRESS[email]["started"] -= 600  # finished ten minutes ago

        def quiet_tools(*a, **k):
            time.sleep(1.2)
            return []

        h = _fake_handler()
        body = {"messages": [{"role": "user", "content": "create a website for a fashion designer"}], "stream": True,
                "provider": "auto", "email": email, "_verified_email": email, "tools": True}
        with mock.patch.object(s, "auto_tools", quiet_tools), mock.patch.object(s, "check_tier", lambda e: "pro"), \
             mock.patch.object(s, "is_blocked", lambda e: False), mock.patch.object(s, "chat_touch_async", lambda *a, **k: None), \
             mock.patch.object(s.Handler, "_resolve_provider", lambda self, b: (None, None, None, "none")):
            try:
                h._handle_chat(body)
            except Exception:
                pass
        self.assertFalse(any(f.get("__steps") for f in _frames(h.wfile.getvalue())))


class Names(unittest.TestCase):
    def test_descriptive_names_for_anonymous_briefs(self):
        cases = {"create a website for a fashion designer": "Fashion Designer",
                 "make a landing page for a wedding photographer based in Abuja": "Wedding Photographer",
                 "create an online store for handmade candles": "Handmade Candles",
                 "build a website for Becfom Hotel": "Becfom Hotel"}
        for text, want in cases.items():
            calls = []
            with mock.patch.object(s, "build_site", lambda e, n, b: calls.append(n) or {"ok": True, "url": "/x/", "slug": "x"}):
                s.auto_tools(text, tier="pro", email="n@x.com")
            self.assertEqual(calls, [want], text)


class Client(unittest.TestCase):
    def test_push_mode(self):
        for needle in ("if(j.__steps){ try{ if(!_steps) _steps=stepsCard(b,{push:true}); _steps.push(j.__steps); }catch(e){} }",
                       "const push=(p)=>{", "return {stop, push, finish:", "polling=!opts.push"):
            self.assertIn(needle, APP, needle)


if __name__ == "__main__":
    unittest.main()
