"""patch47: adaptive brain — fastest healthy provider first, cool-downs after 429/timeouts, rotation on silent
brains; cached per-message Supabase lookups; friendly 429 handling on the client."""
import os, sys, io, json, time, socket, unittest, urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


def _keys(**have):
    def k(name, d=""):
        return have.get(name, "")
    return k


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch53-tools-ui-adminfix"'), 2)
        self.assertNotIn("patch46-durable", SRV)


class _H(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def _handler():
    h = s.Handler.__new__(s.Handler)
    h.wfile = io.BytesIO(); h.rfile = io.BytesIO(); h.headers = _H({"Host": "localhost"}); h.client_address = ("127.0.0.1", 1)
    h.request_version = "HTTP/1.1"; h.command = "POST"; h.path = "/api/chat"; h.requestline = "POST /api/chat HTTP/1.1"
    h.send_response = h.send_header = h.end_headers = h.log_message = lambda *a, **k: None
    return h


class _SSE(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def close(self): pass


def _sse(text):
    frames = "".join("data: " + json.dumps({"choices": [{"delta": {"content": w}}]}) + "\n\n" for w in text.split("|"))
    frames += "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\ndata: [DONE]\n\n"
    return _SSE(frames.encode())


class BrainHealth(unittest.TestCase):
    def setUp(self):
        s._BRAIN_STATS.clear()

    def test_preferred_wins_when_healthy_and_slow_brain_is_demoted(self):
        with mock.patch.object(s, "key", _keys(GROQ_API_KEY="g", AGNES_API_KEY="a")), \
             mock.patch.dict(s.KEYS, {"BRAIN_PROVIDER": "agnes"}, clear=False):
            self.assertEqual(s._brain_order()[0], "agnes")          # preference bonus
            s._brain_note("agnes", first_token_s=38.0)               # one 38-second first token
            self.assertEqual(s._brain_order()[0], "groq")           # …and Groq answers first from now on
            self.assertGreater(s.brain_status()["agnes"]["cooling_s"], 500)  # benched for ~10 minutes at once
            s._BRAIN_STATS["agnes"]["fail_until"] = 0
            for _ in range(6):
                s._brain_note("agnes", first_token_s=0.9)            # it recovers as it speeds up again
            self.assertEqual(s._brain_order()[0], "agnes")

    def test_failure_cooldown_and_retry_after(self):
        with mock.patch.object(s, "key", _keys(GROQ_API_KEY="g", AGNES_API_KEY="a", OPENAI_API_KEY="o")), \
             mock.patch.dict(s.KEYS, {"BRAIN_PROVIDER": "groq"}, clear=False):
            s._brain_note("groq", failed=True, cooldown=300)
            self.assertNotEqual(s._brain_order()[0], "groq")
            self.assertGreater(s.brain_status()["groq"]["cooling_s"], 250)
        self.assertEqual(s._retry_after_s({"Retry-After": "17"}), 17)
        self.assertEqual(s._retry_after_s({}, "Rate limit reached … Please try again in 2m13.5s."), 134)
        self.assertEqual(s._retry_after_s({}, "nothing useful"), 0)

    def test_resolve_provider_skips_cooling_brain(self):
        h = _handler()
        with mock.patch.object(s, "key", _keys(GROQ_API_KEY="g", AGNES_API_KEY="a")), \
             mock.patch.dict(s.KEYS, {"BRAIN_PROVIDER": "agnes"}, clear=False):
            self.assertIn("agnes-ai", h._resolve_provider({"provider": "auto"})[1])
            s._brain_note("agnes", failed=True, cooldown=120)
            self.assertIn("groq", h._resolve_provider({"provider": "auto"})[1])
            self.assertIn("agnes-ai", h._resolve_provider({"provider": "agnes"})[1])  # explicit choice still honoured


class RotationOnSilence(unittest.TestCase):
    def test_timeout_rotates_to_next_brain(self):
        s._BRAIN_STATS.clear()
        seen = []
        def fake_urlopen(req, timeout=0, context=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            seen.append((url, timeout))
            if "agnes-ai" in url:
                raise socket.timeout("timed out")
            return _sse("Hello| from| Groq")
        h = _handler()
        body = {"messages": [{"role": "user", "content": "say hello"}], "stream": True, "provider": "auto",
                "email": "r47@example.invalid", "_verified_email": "r47@example.invalid", "tools": False}
        with mock.patch.object(s, "key", _keys(GROQ_API_KEY="g", AGNES_API_KEY="a")), \
             mock.patch.dict(s.KEYS, {"BRAIN_PROVIDER": "agnes"}, clear=False), \
             mock.patch.object(s.urllib.request, "urlopen", fake_urlopen), \
             mock.patch.object(s, "is_blocked", lambda e: False), mock.patch.object(s, "check_tier", lambda e: "pro"), \
             mock.patch.object(s, "chat_touch_async", lambda *a, **k: None), mock.patch.object(s, "_community_brief_cached", lambda e: ""):
            h._handle_chat(body)
        out = h.wfile.getvalue().decode()
        self.assertIn("Hello", out); self.assertIn("Groq", out)
        self.assertTrue(seen and "agnes-ai" in seen[0][0] and any("groq" in u for u, _ in seen[1:]))
        self.assertLessEqual(seen[0][1], 15, "a silent brain is abandoned in 15s, not 180s")
        self.assertGreater(s.brain_status()["agnes"]["cooling_s"], 0)
        self.assertGreater(s.brain_status()["groq"]["samples"], 0)


class Caches(unittest.TestCase):
    def test_tier_cached_and_cleared_on_subscription(self):
        s.tier_cache_clear()
        calls = []
        with mock.patch.object(s, "_check_tier_live", lambda e: calls.append(e) or "pro"):
            for _ in range(5):
                self.assertEqual(s.check_tier("Cache47@Example.invalid"), "pro")
            self.assertEqual(len(calls), 1)
            s.tier_cache_clear("cache47@example.invalid")
            s.check_tier("cache47@example.invalid")
            self.assertEqual(len(calls), 2)
        self.assertIn('tier_cache_clear((rec or {}).get("email"))', SRV)

    def test_brief_cached(self):
        s._BRIEF_CACHE.clear()
        n = {"c": 0}
        class Svc:
            def brief_for(self, em):
                n["c"] += 1; return "Community identity: number 123"
        with mock.patch.object(s, "community_service", lambda: Svc()):
            for _ in range(4):
                self.assertIn("123", s._community_brief_cached("b47@example.invalid"))
        self.assertEqual(n["c"], 1)
        self.assertIn("_brief = _community_brief_cached(chat_email) if chat_email else \"\"", SRV)


class Client(unittest.TestCase):
    def test_busy_handling(self):
        for needle in ("const busy=(st)=>st===429||st===502||st===503||st===504;", "Retry-After", "busy(r.status)?friendly(r.status):('Bad response '+r.status)",
                       "busy — retrying…", "OraCool is busy for a moment (too many requests at once)"):
            self.assertIn(needle, APP, needle)


if __name__ == "__main__":
    unittest.main()
