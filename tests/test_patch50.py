"""patch50: Anthropic (Claude) wired in as a brain — Messages API adapter, stream translation, provider ladder,
admin vault box for the key."""
import os, sys, io, json, socket, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch51-security-osint"'), 2)
        self.assertNotIn("patch49-clock", SRV)


class Adapter(unittest.TestCase):
    def test_message_conversion(self):
        system, msgs = s._anthropic_messages([
            {"role": "system", "content": "LOCK"}, {"role": "system", "content": "NOW"},
            {"role": "assistant", "content": "earlier reply"}, {"role": "user", "content": "hi"},
            {"role": "user", "content": "again"}, {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}])
        self.assertEqual(system, "LOCK\n\nNOW")
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user", "assistant", "user"])
        self.assertEqual(msgs[0]["content"], "(continue)"); self.assertEqual(msgs[2]["content"], "hi\n\nagain"); self.assertEqual(msgs[3]["content"], "ok")

    def test_request_shape(self):
        req = s._anthropic_request("sk-ant-test", {"model": "claude-sonnet-4-5", "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "q"}], "max_tokens": 900, "temperature": 1.4})
        self.assertEqual(req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.get_header("X-api-key"), "sk-ant-test"); self.assertEqual(req.get_header("Anthropic-version"), "2023-06-01")
        body = json.loads(req.data.decode())
        self.assertEqual(body["system"], "S"); self.assertEqual(body["max_tokens"], 900); self.assertEqual(body["temperature"], 1.0); self.assertTrue(body["stream"])
        self.assertNotIn("reasoning_effort", body)
        self.assertIsNone(req.get_header("Anthropic-workspace-id"))
        with mock.patch.object(s, "key", lambda n, d="": "wrkspc_01abc" if n == "ANTHROPIC_WORKSPACE_ID" else ""):
            req2 = s._anthropic_request("sk-ant-test", {"messages": [{"role": "user", "content": "q"}]})
        self.assertEqual(req2.get_header("Anthropic-workspace-id"), "wrkspc_01abc")
        self.assertIn("ANTHROPIC_WORKSPACE_ID", s._brain_error_text(400, '{"error":{"message":"this request must include the anthropic-workspace-id header"}}'))

    def test_stream_translation(self):
        frames = [{"type": "message_start"}, {"type": "content_block_start"},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}},
                  {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"}]
        raw = io.BytesIO("".join("event: x\ndata: %s\n\n" % json.dumps(f) for f in frames).encode())
        out = [l.decode().strip() for l in s._AnthropicSSE(raw)]
        self.assertEqual(json.loads(out[0][5:])["choices"][0]["delta"]["content"], "Hello")
        self.assertEqual(json.loads(out[2][5:])["choices"][0]["finish_reason"], "stop")
        self.assertEqual(out[-1], "data: [DONE]")


class _H(dict):
    def get(self, k, d=None): return dict.get(self, k, d)


class _FakeResp(io.BytesIO):
    def close(self): pass


class Ladder(unittest.TestCase):
    def test_resolve_and_rotation(self):
        h = s.Handler.__new__(s.Handler); s._BRAIN_STATS.clear(); h.headers = _H({}); h.client_address = ("127.0.0.1", 1)
        with mock.patch.object(s, "key", lambda n, d="": {"ANTHROPIC_API_KEY": "sk-ant-x", "GROQ_API_KEY": "g"}.get(n, "")), \
             mock.patch.dict(s.KEYS, {"BRAIN_PROVIDER": "agnes"}, clear=False):
            k, base, model, prov = h._resolve_provider({"provider": "auto"})
            self.assertEqual(base, s.ANTHROPIC_BASE); self.assertEqual(model, "claude-sonnet-4-5")  # Claude leads auto when configured
            self.assertEqual(h._resolve_provider({"provider": "claude"})[1], s.ANTHROPIC_BASE)
            self.assertIn("groq", h._resolve_provider({"provider": "auto", "stream": False})[1])  # non-streaming callers stay on OpenAI-style brains
            nb = h._next_brain("auto", "https://api.groq.com/openai/v1", "qwen/qwen3.8-27b", {"qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"})
            self.assertEqual(nb[1], s.ANTHROPIC_BASE)
        self.assertEqual(s._brain_name(s.ANTHROPIC_BASE), "anthropic")

    def test_chat_streams_through_claude(self):
        s._BRAIN_STATS.clear(); seen = {}
        frames = [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "I am OraCool"}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " AI."}},
                  {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"}]
        def fake_urlopen(req, timeout=0, context=None):
            seen["url"] = req.full_url; seen["key"] = req.get_header("X-api-key"); seen["body"] = json.loads(req.data.decode())
            return _FakeResp("".join("data: %s\n\n" % json.dumps(f) for f in frames).encode())
        h = s.Handler.__new__(s.Handler); h.wfile = io.BytesIO(); h.rfile = io.BytesIO(); h.headers = _H({"Host": "localhost"}); h.client_address = ("127.0.0.1", 1)
        h.request_version = "HTTP/1.1"; h.command = "POST"; h.path = "/api/chat"; h.requestline = "POST /api/chat HTTP/1.1"
        h.send_response = h.send_header = h.end_headers = h.log_message = lambda *a, **k: None
        body = {"messages": [{"role": "user", "content": "who are you?"}], "stream": True, "provider": "anthropic",
                "email": "c50@example.invalid", "_verified_email": "c50@example.invalid", "tools": False, "tz": "Africa/Lagos"}
        with mock.patch.object(s, "key", lambda n, d="": {"ANTHROPIC_API_KEY": "sk-ant-x"}.get(n, "")), \
             mock.patch.object(s.urllib.request, "urlopen", fake_urlopen), mock.patch.object(s, "is_blocked", lambda e: False), \
             mock.patch.object(s, "check_tier", lambda e: "pro"), mock.patch.object(s, "chat_touch_async", lambda *a, **k: None), \
             mock.patch.object(s, "_community_brief_cached", lambda e: ""):
            h._handle_chat(body)
        out = h.wfile.getvalue().decode()
        self.assertIn("I am OraCool", out); self.assertIn(" AI.", out)
        self.assertEqual(seen["url"], "https://api.anthropic.com/v1/messages"); self.assertEqual(seen["key"], "sk-ant-x")
        self.assertIn("CURRENT DATE & TIME", seen["body"]["system"]); self.assertIn("OraCool AI", seen["body"]["system"])
        self.assertEqual(seen["body"]["messages"][-1]["role"], "user")
        self.assertGreater(s.brain_status()["anthropic"]["samples"], 0)


class NoCredits(unittest.TestCase):
    def test_billing_error_benches_claude_and_rotates(self):
        s._BRAIN_STATS.clear(); calls = []
        def fake_urlopen(req, timeout=0, context=None):
            calls.append(req.full_url)
            if "anthropic" in req.full_url:
                raise s.urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(
                    b'{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low to access the Anthropic API."}}'))
            return _FakeResp(b'data: {"choices":[{"delta":{"content":"groq says hi"}}]}\n\ndata: [DONE]\n\n')
        h = s.Handler.__new__(s.Handler); h.wfile = io.BytesIO(); h.rfile = io.BytesIO(); h.headers = _H({"Host": "localhost"}); h.client_address = ("127.0.0.1", 1)
        h.request_version = "HTTP/1.1"; h.command = "POST"; h.path = "/api/chat"; h.requestline = "POST /api/chat HTTP/1.1"
        h.send_response = h.send_header = h.end_headers = h.log_message = lambda *a, **k: None
        body = {"messages": [{"role": "user", "content": "hello"}], "stream": True, "provider": "auto", "email": "c50b@example.invalid",
                "_verified_email": "c50b@example.invalid", "tools": False}
        with mock.patch.object(s, "key", lambda n, d="": {"ANTHROPIC_API_KEY": "sk-ant-x", "GROQ_API_KEY": "gk"}.get(n, "")), \
             mock.patch.object(s.urllib.request, "urlopen", fake_urlopen), mock.patch.object(s, "is_blocked", lambda e: False), \
             mock.patch.object(s, "check_tier", lambda e: "pro"), mock.patch.object(s, "chat_touch_async", lambda *a, **k: None), \
             mock.patch.object(s, "_community_brief_cached", lambda e: ""), mock.patch.dict(s.KEYS, {"BRAIN_PROVIDER": ""}, clear=False):
            h._handle_chat(body)
        out = h.wfile.getvalue().decode()
        self.assertTrue(calls, out[:400])
        self.assertIn("anthropic.com", calls[0]); self.assertIn("groq", calls[1]); self.assertIn("groq says hi", out)
        self.assertGreater(s._BRAIN_STATS["anthropic"]["fail_until"], s.time.time() + 1000)  # benched, so the next chat skips the dead brain
        with mock.patch.object(s, "key", lambda n, d="": {"ANTHROPIC_API_KEY": "sk-ant-x", "GROQ_API_KEY": "gk"}.get(n, "")):
            self.assertEqual(s._brain_order(), ["groq", "anthropic"])
        self.assertIn("no credits", s._brain_error_text(400, "Your credit balance is too low"))


class AdminBox(unittest.TestCase):
    def test_vault_box(self):
        for needle in ('id="akSave"', 'id="akTest"', "post('/api/admin/vault',{set:{[nm]:v}", "real ones start with <b>sk-ant-</b>", 'provider:\'anthropic\''):
            self.assertIn(needle, APP, needle)
        self.assertIn('"anthropic": bool(key("ANTHROPIC_API_KEY"))', SRV)
        self.assertIn('"credit balance" in low', SRV)


if __name__ == "__main__":
    unittest.main()
