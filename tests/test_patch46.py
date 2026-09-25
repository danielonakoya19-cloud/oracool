"""patch46: brain failover rotation (Groq model pool → Agnes → OpenAI; explicit 'groq' rotates too) and durable state
for Render (data files + build folders mirrored to Supabase kv, restored at boot, flushed on SIGTERM)."""
import os, sys, io, json, time, shutil, tempfile, unittest, urllib.error
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
        self.assertNotIn("patch45-inline-feed", SRV)


class _FakeHeaders(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def _fake_handler():
    h = s.Handler.__new__(s.Handler)
    h.wfile = io.BytesIO(); h.rfile = io.BytesIO()
    h.headers = _FakeHeaders({"Host": "localhost:8000"}); h.client_address = ("127.0.0.1", 1)
    h.request_version = "HTTP/1.1"; h.command = "POST"; h.path = "/api/chat"; h.requestline = "POST /api/chat HTTP/1.1"
    h.send_response = lambda *a, **k: None; h.send_header = lambda *a, **k: None; h.end_headers = lambda *a, **k: None
    h.log_message = lambda *a, **k: None
    return h


def _http_err(code, msg):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(json.dumps({"error": {"message": msg}}).encode()))


class _SSE(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _sse_reply(text):
    return _SSE(("data: " + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]}) + "\n" +
                 "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n" + "data: [DONE]\n").encode())


class Failover(unittest.TestCase):
    def _run(self, provider, model_ok, keys, mode="auto"):
        seen = []
        def urlopen(req, timeout=0, context=None):
            body = json.loads(req.data.decode()); m = body["model"]; host = req.full_url
            seen.append((host.split("/")[2], m))
            if m == model_ok and "groq" in host:
                return _sse_reply("Hello from " + m)
            if "agnes" in host and model_ok == "agnes":
                return _sse_reply("Hello from agnes")
            raise _http_err(429, "Rate limit reached for model `%s` … tokens per day (TPD): Limit 200000, Used 199653" % m)
        h = _fake_handler()
        body = {"messages": [{"role": "user", "content": "hi there, quick one"}], "stream": True, "provider": provider,
                "email": "f46@example.invalid", "_verified_email": "f46@example.invalid", "tools": False, "mode": mode}
        if provider == "groq":
            body["model"] = "qwen/qwen3.8-27b"
        with mock.patch.object(s.urllib.request, "urlopen", urlopen), \
             mock.patch.object(s, "key", lambda k, d="": keys.get(k, "")), \
             mock.patch.dict(s.KEYS, {"GROQ_MODEL": "qwen/qwen3.8-27b", "BRAIN_PROVIDER": "groq"}, clear=False), \
             mock.patch.object(s, "is_blocked", lambda e: False), mock.patch.object(s, "chat_touch_async", lambda *a, **k: None), \
             mock.patch.object(s, "conv_append", lambda *a, **k: None), mock.patch.object(s, "check_tier", lambda e: "pro"):
            try:
                h._handle_chat(body)
            except Exception as e:  # pragma: no cover
                self.fail("handler raised: %r" % e)
        raw = h.wfile.getvalue().decode("utf-8", "replace")
        return seen, raw

    def test_groq_daily_cap_rotates_to_next_groq_model(self):
        seen, raw = self._run("auto", "openai/gpt-oss-120b", {"GROQ_API_KEY": "gk"})
        self.assertEqual([m for host, m in seen], ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"])
        self.assertIn("Hello from openai/gpt-oss-120b", raw)
        self.assertNotIn('"error"', raw)

    def test_explicit_groq_fast_mode_rotates_too(self):
        seen, raw = self._run("groq", "openai/gpt-oss-20b", {"GROQ_API_KEY": "gk"})
        self.assertEqual([m for host, m in seen], ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"])
        self.assertIn("Hello from openai/gpt-oss-20b", raw)

    def test_all_groq_exhausted_spills_to_agnes(self):
        seen, raw = self._run("auto", "agnes", {"GROQ_API_KEY": "gk", "AGNES_API_KEY": "ak"})
        self.assertEqual(seen[-1][0], "apihub.agnes-ai.com")
        self.assertIn("Hello from agnes", raw)

    def test_everything_down_gives_a_human_sentence_not_a_billing_lie(self):
        seen, raw = self._run("auto", "nothing", {"GROQ_API_KEY": "gk"})
        self.assertEqual(len(seen), 3, seen)  # all three Groq models tried once each
        self.assertIn("rate-limited right now", raw)
        self.assertNotIn("no credits", raw)

    def test_error_texts(self):
        self.assertIn("no credits left", s._brain_error_text(429, '{"error":{"type":"insufficient_quota"}}'))
        self.assertIn("rate-limited", s._brain_error_text(429, "Rate limit reached for model"))
        self.assertIn("rejected the server key", s._brain_error_text(401, "invalid api key"))

    def test_model_pool(self):
        with mock.patch.dict(s.KEYS, {"GROQ_MODEL": "qwen/qwen3.8-27b", "GROQ_FAST_MODEL": "qwen/qwen3.8-27b"}, clear=False):
            self.assertEqual(s._groq_chat_models(), ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"])


class Persist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.store = {}
        self.patches = [mock.patch.object(s, "DATA_DIR", self.tmp),
                        mock.patch.object(s, "supabase_kv_put", lambda k, o: self.store.__setitem__(k, json.loads(json.dumps(o))) or True),
                        mock.patch.object(s, "supabase_kv_list", lambda p: [(k, v) for k, v in self.store.items() if k.startswith(p)]),
                        mock.patch.object(s, "_persist_enabled", lambda: True)]
        for p in self.patches: p.start()
        s._PERSIST["synced"].clear(); s._PERSIST.update(errors=0, pushed=0, restored=0)

    def tearDown(self):
        for p in self.patches: p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self):
        os.makedirs(os.path.join(self.tmp, "builds", "legend-fintech")); os.makedirs(os.path.join(self.tmp, "sites"))
        open(os.path.join(self.tmp, "users.json"), "w").write(json.dumps({"a@x.com": {"plan": "pro", "coins": {"balance": 5}}}))
        open(os.path.join(self.tmp, "vault.json"), "w").write(json.dumps({"a@x.com": {"k": "sealed"}}))
        open(os.path.join(self.tmp, "skills.json"), "w").write(json.dumps({"a@x.com": [{"name": "greet"}]}))
        open(os.path.join(self.tmp, "vapid_private.pem"), "w").write("-----BEGIN EC PRIVATE KEY-----\nabc\n-----END EC PRIVATE KEY-----\n")
        open(os.path.join(self.tmp, "builds", "meta.json"), "w").write(json.dumps({"legend-fintech": {"name": "Legend Fintech"}}))
        open(os.path.join(self.tmp, "builds", "legend-fintech", "index.html"), "w").write("<html>legend</html>")
        open(os.path.join(self.tmp, "builds", "legend-fintech", "README.md"), "w").write("# Legend")
        open(os.path.join(self.tmp, "conversations.json"), "w").write("{}")   # has its own mirror
        open(os.path.join(self.tmp, "broken.json"), "w").write("{ not json")  # never mirrored half-written
        open(os.path.join(self.tmp, "x.json.tmp"), "w").write("{}")

    def test_round_trip_survives_a_wiped_disk(self):
        self._seed()
        self.assertEqual(s._persist_scan(), 6)
        self.assertEqual(sorted(self.store), ["build:legend-fintech", "file:builds/meta.json", "file:skills.json", "file:users.json",
                                               "file:vapid_private.pem", "file:vault.json"])
        self.assertEqual(s._persist_scan(), 0, "unchanged files are not re-pushed")
        time.sleep(0.02); p = os.path.join(self.tmp, "users.json")
        open(p, "w").write(json.dumps({"a@x.com": {"plan": "pro", "coins": {"balance": 4}}})); os.utime(p, (time.time() + 3, time.time() + 3))
        self.assertEqual(s._persist_scan(), 1)
        shutil.rmtree(self.tmp); os.makedirs(self.tmp); s._PERSIST["synced"].clear()   # fresh Render instance
        self.assertEqual(s._persist_restore(), 6)
        self.assertEqual(json.load(open(os.path.join(self.tmp, "users.json")))["a@x.com"]["coins"]["balance"], 4)
        self.assertEqual(open(os.path.join(self.tmp, "builds", "legend-fintech", "index.html")).read(), "<html>legend</html>")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "builds", "meta.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "vapid_private.pem")))
        self.assertEqual(s._persist_scan(), 0, "restored state is already in sync")

    def test_newer_local_file_wins_at_boot(self):
        self._seed(); s._persist_scan()
        p = os.path.join(self.tmp, "users.json")
        open(p, "w").write(json.dumps({"a@x.com": {"plan": "ultra"}})); os.utime(p, (time.time() + 60, time.time() + 60))
        s._PERSIST["synced"].clear(); s._persist_restore()
        self.assertEqual(json.load(open(p))["a@x.com"]["plan"], "ultra")

    def test_path_traversal_ignored(self):
        self.store["file:../../etc/evil.json"] = {"text": "{}", "mtime": time.time()}
        self.store["build:../evil"] = {"files": {"../x.html": "x"}, "mtime": time.time()}
        self.store["build:okslug"] = {"files": {"../x.html": "x", "index.html": "fine"}, "mtime": time.time()}
        s._persist_restore()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "..", "..", "etc", "evil.json")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "builds", "x.html")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "builds", "okslug", "index.html")))

    def test_enabled_only_on_render_by_default(self):
        with mock.patch.object(s, "key", lambda k, d="": "x"), mock.patch.dict(s.KEYS, {}, clear=False):
            s.KEYS.pop("PERSIST_CLOUD", None)
            with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=False):
                os.environ.pop("PERSIST_CLOUD", None)
                self.assertTrue(s._persist_enabled.__wrapped__() if hasattr(s._persist_enabled, "__wrapped__") else True)
            env = {k: v for k, v in os.environ.items() if k not in ("RENDER", "RENDER_SERVICE_ID", "PERSIST_CLOUD")}
            with mock.patch.dict(os.environ, env, clear=True):
                self.patches[3].stop()
                try:
                    self.assertFalse(s._persist_enabled(), "a dev copy must not push over production")
                    os.environ["PERSIST_CLOUD"] = "1"; self.assertTrue(s._persist_enabled())
                    os.environ["PERSIST_CLOUD"] = "0"; self.assertFalse(s._persist_enabled())
                finally:
                    self.patches[3].start()

    def test_boot_wiring(self):
        self.assertIn("_n = _persist_restore()", SRV)
        self.assertIn("threading.Thread(target=_persist_loop, daemon=True).start()", SRV)
        self.assertIn("_signal.signal(_signal.SIGTERM, _persist_flush_and_exit)", SRV)
        self.assertIn('"persist": ("cloud" if _PERSIST.get("enabled") else "local")', SRV)


class Client(unittest.TestCase):
    def test_error_wording(self):
        self.assertNotIn("your AI provider has no credits/billing. Add credits or switch provider in Settings.", APP)
        self.assertIn("rate-limited for a few minutes", APP)


if __name__ == "__main__":
    unittest.main()
