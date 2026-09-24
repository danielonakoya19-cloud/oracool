"""patch34 — junk-reply floor: when a tool ran but the model answers with nothing but
'.' (or '(ran a tool)'), the server nudges once and, failing that, surfaces the live
tool results so the user never sees a dead bubble."""
import importlib.util, json, os, sys, tempfile, unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('oracool_test_server_p34', ROOT / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)

SRV = open(ROOT / 'server.py', encoding='utf-8').read()

WEATHER = [{"tool": "weather", "label": "weather in Lagos", "result": json.dumps({"summary": "28C and sunny in Lagos"})}]


class MarkerTest(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch36-visuals"'), 2)


class JunkUnitTests(unittest.TestCase):
    def test_is_junk(self):
        for j in ("", ".", "..", "...", "…", " - ", "(ran a tool)", "*(ran a tool)*", ". . ."):
            self.assertTrue(s._reply_is_junk(j), repr(j))
        for ok in ("28C and sunny.", "OK", "It is raining", "(see above)"):
            self.assertFalse(s._reply_is_junk(ok), repr(ok))

    def test_digest(self):
        runs = WEATHER + [{"tool": "x", "label": "long", "result": "A" * 2000},
                          {"tool": "y", "label": "third", "result": "{}"},
                          {"tool": "z", "label": "fourth", "result": "must not appear"}]
        d = s._tool_digest(runs)
        self.assertIn("weather in Lagos: 28C and sunny in Lagos", d)
        self.assertEqual(len(d.splitlines()), 3)
        self.assertIn("A" * 360, d)
        self.assertNotIn("must not appear", d)


class FakeW:
    def __init__(self):
        self.buf = b""
    def write(self, b):
        self.buf += bytes(b)
    def flush(self):
        pass


def make_handler():
    h = object.__new__(s.Handler)
    h.wfile = FakeW()
    h.sent = []
    h._send_json = lambda obj, status=200: h.sent.append(obj)
    h.send_response = lambda *a: None
    h.send_header = lambda *a, **k: None
    h.end_headers = lambda: None
    h.client_address = ("127.0.0.1", 5)
    class _H(dict):
        def get(self, k, d=None):
            return "127.0.0.1:8000" if k == "Host" else d
    h.headers = _H()
    return h


class FloorLiveTests(unittest.TestCase):
    def _stack(self):
        """ExitStack pre-loaded with the common offline patches."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = ExitStack()
        self.addCleanup(st.close)
        st.enter_context(mock.patch.object(s, "DATA_DIR", tmp.name))
        st.enter_context(mock.patch.object(s, "BASE_DIR", tmp.name))
        st.enter_context(mock.patch.dict(s.KEYS, {"GROQ_API_KEY": "test-key", "BRAIN_PROVIDER": "groq"}, clear=True))
        st.enter_context(mock.patch.object(s, "audit_log"))
        st.enter_context(mock.patch.object(s, "emit_event"))
        st.enter_context(mock.patch.object(s, "notify_admins"))
        st.enter_context(mock.patch.object(s, "auto_tools", return_value=list(WEATHER)))
        st.enter_context(mock.patch.object(s, "touch_user"))  # bookkeeping thread — silent under mocked DATA_DIR
        return st

    def test_streaming_floor(self):
        calls = []
        class MR:
            def close(self): pass
            def __iter__(self):
                return iter([b'data: {"choices":[{"delta":{"content":"."},"finish_reason":"stop"}]}\n\n',
                             b'data: [DONE]\n\n'])
        def fake_urlopen(req, *a, **k):
            calls.append(json.loads(req.data.decode()))
            return MR()
        st = self._stack()
        st.enter_context(mock.patch.object(s.urllib.request, "urlopen", side_effect=fake_urlopen))
        with st:
            h = make_handler()
            s.Handler._handle_chat(h, {"messages": [{"role": "system", "content": "You are OraCool."},
                                                    {"role": "user", "content": "what is the weather in Lagos"}],
                                       "stream": True, "email": "junky@t.com"})
            out = h.wfile.buf.decode()
        self.assertEqual(len(calls), 2)                                    # first reply + ONE nudge retry
        self.assertIn("empty or only punctuation", json.dumps(calls[1]["messages"][-1]))
        self.assertIn("Here is what my live tools found for you", out)     # digest streamed to the client
        self.assertIn("28C and sunny in Lagos", out)

    def test_nonstreaming_floor(self):
        calls = []
        def fake_fetch(url, method="POST", headers=None, json_body=None, timeout=None):
            calls.append(json_body)
            return 200, json.dumps({"choices": [{"message": {"content": "."}, "finish_reason": "stop"}]}), ""
        st = self._stack()
        st.enter_context(mock.patch.object(s, "http_fetch", side_effect=fake_fetch))
        with st:
            h = make_handler()
            s.Handler._handle_chat(h, {"messages": [{"role": "system", "content": "You are OraCool."},
                                                     {"role": "user", "content": "what is the weather in Lagos"}],
                                       "stream": False, "email": "junky@t.com"})
        self.assertEqual(len(calls), 3)                                     # two nudges then the digest exit
        sent = h.sent[0]["content"]
        self.assertIn("28C and sunny in Lagos", sent)
        self.assertFalse(s._reply_is_junk(sent))

    def test_good_reply_is_left_alone(self):
        def fake_fetch(url, method="POST", headers=None, json_body=None, timeout=None):
            return 200, json.dumps({"choices": [{"message": {"content": "It is 28C and sunny in Lagos."},
                                                 "finish_reason": "stop"}]}), ""
        st = self._stack()
        st.enter_context(mock.patch.object(s, "http_fetch", side_effect=fake_fetch))
        with st:
            h = make_handler()
            s.Handler._handle_chat(h, {"messages": [{"role": "user", "content": "weather"}],
                                       "stream": False, "email": "junky@t.com"})
        self.assertEqual(h.sent[0]["content"], "It is 28C and sunny in Lagos.")
        self.assertNotIn("live tools found", h.sent[0]["content"])


if __name__ == "__main__":
    unittest.main()
