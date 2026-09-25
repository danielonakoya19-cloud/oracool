"""patch35 — identity lock: the AI is always OraCool AI (never the fallback provider's
persona), provider error frames surface in the UI, and jammed streams time out loudly."""
import importlib.util, json, sys, tempfile, unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('oracool_test_server_p35', ROOT / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
SRV = open(ROOT / 'server.py', encoding='utf-8').read()
APP = open(ROOT / 'index.html', encoding='utf-8').read()


class ScrubTests(unittest.TestCase):
    def test_claims_are_rewritten(self):
        for leak in ("I'm Agnes, developed by Sapiens AI.",
                     "I am Qwen, a large language model trained by Alibaba.",
                     "Hi! I'm ChatGPT.",
                     "this is Claude, created by Anthropic.",
                     "I am Grok"):
            out = s._identity_scrub(leak)
            self.assertIn("OraCool", out, leak)
            self.assertNotIn("Agnes, developed", out)
            self.assertNotIn("Sapiens", out)
            self.assertNotIn("Qwen", out)
            self.assertNotIn("trained by Alibaba", out)
            self.assertNotIn("..", out)                       # no doubled punctuation

    def test_legit_text_untouched(self):
        for ok in ("ChatGPT pricing changed in 2026.",
                   "I am going to fetch the news now.",
                   "A llama is a South American camelid.",
                   "You can call me whenever you need help."):
            self.assertEqual(s._identity_scrub(ok), ok)

    def test_trailing_brand_fragment_removed(self):
        out = s._identity_scrub("Sure thing. I'm Agnes — developed by Sapiens AI. What next?")
        self.assertNotIn("Sapiens", out)
        self.assertIn("OraCool", out)


class LockWiringTests(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch39-arena"'), 2)

    def test_lock_prepended_to_every_provider_call(self):
        self.assertIn('messages = [{"role": "system", "content": _IDENTITY_LOCK}] + messages', SRV)
        # and it sits right before payload construction, i.e. after all other injections
        i_payload = SRV.index('url = base_url + "/chat/completions"\n        max_tokens')
        i_lock = SRV.index('_IDENTITY_LOCK}] + messages')
        self.assertTrue(0 < i_lock - i_payload < 200 or -200 < i_lock - i_payload < 200)
        self.assertLess(abs(i_lock - i_payload), 200)

    def test_finish_and_stream_scrubbed(self):
        self.assertIn("clean = _pii_scrub(_identity_scrub(_strip_agent_markup(text)), conv_em)", SRV)
        self.assertIn("_final_txt = _pii_scrub(_identity_scrub(_final_txt), chat_email)", SRV)

    def test_client_wiring(self):
        for k in ("if(j.error){ _streamErr=new Error",
                  "if(_streamErr) throw _streamErr;",
                  "No answer from the brain in 45s",
                  "_wd=setInterval(()=>{ if(Date.now()-lastByte>120000)",
                  "⏱ The provider timed out with no answer",
                  "IDENTITY: you are always OraCool AI",
                  "if(j.error){ addErr('⚠️ '"):
            self.assertIn(k, APP)


class FakeW:
    def __init__(self): self.buf = b""
    def write(self, b): self.buf += bytes(b)
    def flush(self): pass


class LiveHandlerTests(unittest.TestCase):
    def test_lock_reaches_payload_and_leak_is_scrubbed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        captured = {}
        def fake_fetch(url, method="POST", headers=None, json_body=None, timeout=None):
            captured["body"] = json_body
            return 200, json.dumps({"choices": [{"message": {"content": "I'm Agnes, developed by Sapiens AI."},
                                                 "finish_reason": "stop"}]}), ""
        h = object.__new__(s.Handler)
        h.wfile = FakeW(); h.sent = []
        h._send_json = lambda obj, status=200: h.sent.append(obj)
        h.send_response = lambda *a: None
        h.send_header = lambda *a, **k: None
        h.end_headers = lambda: None
        h.client_address = ("127.0.0.1", 5)
        class _H(dict):
            def get(self, k, d=None): return "127.0.0.1:8000" if k == "Host" else d
        h.headers = _H()
        with ExitStack() as st:
            st.enter_context(mock.patch.object(s, "DATA_DIR", tmp.name))
            st.enter_context(mock.patch.object(s, "BASE_DIR", tmp.name))
            st.enter_context(mock.patch.dict(s.KEYS, {"GROQ_API_KEY": "k", "BRAIN_PROVIDER": "groq"}, clear=True))
            st.enter_context(mock.patch.object(s, "audit_log"))
            st.enter_context(mock.patch.object(s, "emit_event"))
            st.enter_context(mock.patch.object(s, "notify_admins"))
            st.enter_context(mock.patch.object(s, "touch_user"))
            st.enter_context(mock.patch.object(s, "auto_tools", return_value=[]))
            st.enter_context(mock.patch.object(s, "http_fetch", side_effect=fake_fetch))
            s.Handler._handle_chat(h, {"messages": [{"role": "user", "content": "who are you?"}],
                                       "stream": False, "email": "probe@t.com"})
        sent_msgs = captured["body"]["messages"]
        self.assertTrue(str(sent_msgs[0]["content"]).startswith("PLATFORM IDENTITY LOCK"))
        self.assertIn("OraCool AI", h.sent[0]["content"])
        self.assertNotIn("Agnes", h.sent[0]["content"])
        self.assertNotIn("Sapiens", h.sent[0]["content"])


if __name__ == "__main__":
    unittest.main()
