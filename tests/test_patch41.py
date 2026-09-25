"""patch41: remote key vault, media-intent fixes (no "image of a video"), conversation-aware media briefs,
selfie/clip vision mode, ask-option hook fix, capture memory + editor, code runner."""
import os, sys, json, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
MD = open(os.path.join(ROOT, "md.js"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch44-vision"'), 2)
        self.assertNotIn("patch40-studio", SRV)
        self.assertIn('/md.js?v=41', APP)


class Vault(unittest.TestCase):
    def test_key_falls_back_to_vault_only_when_local_missing(self):
        with mock.patch.dict(s._VAULT, {"PEXELS_API_KEY": "vault-key", "GROQ_API_KEY": "vault-groq"}, clear=True), \
             mock.patch.dict(s.KEYS, {"GROQ_API_KEY": "local-groq", "PEXELS_API_KEY": ""}, clear=False), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PEXELS_API_KEY", None)
            self.assertEqual(s.key("PEXELS_API_KEY"), "vault-key")   # empty local → vault fills the gap
            self.assertEqual(s.key("GROQ_API_KEY"), "local-groq")    # local always wins

    def test_refresh_filters_junk(self):
        with mock.patch.object(s, "supabase_kv_get", lambda k: {"PEXELS_API_KEY": " abc ", "bad key": "x", "EMPTY": "", "N": 1}):
            s._VAULT_TS[0] = 0
            self.assertTrue(s._vault_refresh(force=True))
            self.assertEqual(s._VAULT.get("PEXELS_API_KEY"), "abc")
            self.assertNotIn("bad key", s._VAULT); self.assertNotIn("EMPTY", s._VAULT)
        s._VAULT.clear()

    def test_set_merges_and_deletes(self):
        store = {"server_vault": {"A_KEY": "1"}}
        with mock.patch.object(s, "supabase_kv_get", lambda k: dict(store.get(k) or {})), \
             mock.patch.object(s, "supabase_kv_put", lambda k, v: store.__setitem__(k, dict(v)) or True):
            keys = s.server_vault_set({"PEXELS_API_KEY": "p", "A_KEY": "", "lower": "no"})
        self.assertEqual(keys, ["PEXELS_API_KEY"]); self.assertEqual(store["server_vault"], {"PEXELS_API_KEY": "p"})
        s._VAULT.clear()

    def test_wiring(self):
        self.assertIn("_vault_refresh(force=True)\n        threading.Thread(target=_vault_loop, daemon=True).start()", SRV)
        self.assertIn('elif path == "/api/admin/vault":', SRV)
        self.assertIn('if not _ve or not is_admin(_ve):', SRV)
        self.assertIn('"PEXELS_API_KEY", "COMMUNICATIONS_ENABLED"', SRV)


class MediaIntents(unittest.TestCase):
    def setUp(self):
        self.pi = mock.patch.object(s, "gen_image", lambda p: {"images": ["img"], "prompt": p}); self.pi.start()
        self.pv = mock.patch.object(s, "gen_video", lambda p, **k: {"videos": ["vid"], "prompt": p, "audio": k.get("want_audio")}); self.pv.start()

    def tearDown(self):
        self.pi.stop(); self.pv.stop()

    def tools(self, text, tier="enterprise", history=None):
        return [(x["tool"], x["label"]) for x in s.auto_tools(text, tier=tier, email="a@x.com", history=history)]

    def test_bare_video_is_never_an_image_of_a_video(self):
        r = self.tools("generate a video")
        self.assertEqual(r, [("video", "video · what should it show?")])
        self.assertFalse(any(t == "image" for t, _ in r))

    def test_bare_image_asks_for_subject(self):
        self.assertEqual(self.tools("make me an image"), [("image", "image · what should it show?")])

    def test_subject_requests_still_generate(self):
        self.assertEqual(self.tools("generate a video of a lion running"), [("video", "video · a lion running")])
        self.assertEqual(self.tools("generate an image of a red bicycle"), [("image", "image · a red bicycle")])

    def test_pending_brief_turns_answer_into_generation(self):
        hist = [{"role": "user", "content": "generate a video"},
                {"role": "assistant", "content": "What should the video show?"},
                {"role": "user", "content": "Video with voice narration"},
                {"role": "assistant", "content": "Noted — and what should it show?"},
                {"role": "user", "content": "a lion walking through the savannah at sunset"}]
        self.assertEqual(s._pending_media_brief(hist, "a lion walking through the savannah at sunset"),
                         ("video", "a lion walking through the savannah at sunset", "Video with voice narration"))
        runs = s.auto_tools("a lion walking through the savannah at sunset", tier="enterprise", email="a@x.com", history=hist)
        self.assertEqual(runs[0]["tool"], "video")
        self.assertIn("savannah", runs[0]["result"]); self.assertIn('"audio": true', runs[0]["result"])
        # free tier: honest lock instead of silence
        runs = s.auto_tools("a lion walking through the savannah at sunset", tier="free", email="a@x.com", history=hist)
        self.assertEqual(runs[0]["tool"], "video"); self.assertIn("Professional", json.dumps(runs[0]["result"]))

    def test_pending_brief_ignores_unrelated_followups(self):
        hist = [{"role": "user", "content": "generate a video"}, {"role": "assistant", "content": "What should the video show?"}]
        self.assertIsNone(s._pending_media_brief(hist, "what is the weather in lagos?"))
        self.assertIsNone(s._pending_media_brief(hist, "open whatsapp"))
        self.assertIsNone(s._pending_media_brief([], "a lion"))
        self.assertIsNone(s._pending_media_brief([{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Hi, sir."}], "a lion"))

    def test_free_tier_locks_are_accurate(self):
        r = s.auto_tools("generate a video of a lion", tier="free", email="a@x.com")
        labels = [x["label"] for x in r]
        self.assertIn("video creation", labels); self.assertNotIn("image creation", labels)
        r = s.auto_tools("how do I edit a video on my phone", tier="free", email="a@x.com")
        self.assertFalse(any(x["tool"] == "locked" for x in r))

    def test_history_is_passed_from_chat(self):
        self.assertEqual(SRV.count("crypto_site=_site, history=messages,"), 2)


class VisionPurpose(unittest.TestCase):
    def test_selfie_and_clip_prompts(self):
        seen = {}
        def fake(prompt, mime, b64, max_tokens=420):
            seen["p"] = prompt; return "Even light, centred, calm expression."
        with mock.patch.object(s, "_vision_describe_prompt", fake):
            r = s.analyze_file("clip-frames.jpg", "image/jpeg", "aGVsbG8=", purpose="clip")
        self.assertEqual(r["purpose"], "clip"); self.assertIn("left to right", seen["p"]); self.assertIn("Even light", r["text"])
        with mock.patch.object(s, "_vision_describe_prompt", fake):
            r = s.analyze_file("selfie.jpg", "image/jpeg", "aGVsbG8=", purpose="selfie")
        self.assertIn("selfie", seen["p"]); self.assertNotIn("Fingerprints", r.get("note", ""))

    def test_route_passes_purpose(self):
        self.assertIn('purpose=str(body.get("purpose") or ""))', SRV)


class Client(unittest.TestCase):
    def test_ask_hook_attached_after_apps_js(self):
        i_apps = APP.index('<script src="/apps.js?v=')
        i_hook = APP.index('OraApps.onAsk=(t)=>{ try{ send(t); }catch(e){} }; }</script>')
        self.assertLess(i_apps, i_hook)

    def test_media_dedupe(self):
        self.assertIn("streamMedia=streamMedia.filter(m=>{ if(!m||!m.url||seen[m.url]) return false;", APP)
        self.assertIn("if(body.querySelector('img[src=\"'+m.url+'\"],video[src=\"'+m.url+'\"]')) return;", APP)
        self.assertIn("// patch41: the same image/video listed twice renders once", APP)

    def test_capture_memory_and_editor(self):
        for needle in ("let lastCapture=null;", "function captureContext()", "function captureReport(kind)", "function parseEditOps(t)",
                       "function videoEdit(ops)", "function photoEdit(ops)", "c.captureStream(30)", "createMediaElementSource(v)",
                       "captureAnalyze('video', sheet)", "captureAnalyze('photo', url)", "captureContext()+", "window.__sendOverride"):
            self.assertIn(needle, APP, needle)
        # edit rules run before the recording rules inside runDeviceCommand
        self.assertLess(APP.index("// patch41: edits to the photo/clip just captured"), APP.index("// patch40: video clips + \"open my camera\" never go to the app launcher"))

    def test_code_runner(self):
        for needle in ("function runnableKind(pre)", "function codeRun(pre, kind)", "function previewHtml(pre, code)", "function runJs(pre, code)",
                       "function runPy(pre, code)", "cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js", "sandbox','allow-scripts allow-forms allow-modals allow-popups'",
                       "f.setAttribute('sandbox','allow-scripts');", "🛠 Ask OraCool to fix", "CODE RUNNER (you can RUN code"):
            self.assertIn(needle, APP, needle)
        self.assertIn("data-info=", MD)  # ```python autorun


if __name__ == "__main__":
    unittest.main()
