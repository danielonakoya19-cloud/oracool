"""patch44: image attachments the brain can actually see (vision ladder incl. Groq qwen3.8 multimodal, honest fallback,
thumbnails) + composer clears on device-command sends."""
import os, sys, json, base64, unittest, urllib.error, io
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")).decode()


def _http_err(code, msg):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(json.dumps({"error": {"message": msg}}).encode()))


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch44-vision"'), 2)
        self.assertNotIn("patch43-modes", SRV)


class VisionLadder(unittest.TestCase):
    def _keys(self, groq=True, gemini=False, openai=False):
        def k(name, d=""):
            return {"GROQ_API_KEY": "gk" if groq else "", "GEMINI_API_KEY": "gm" if gemini else "",
                    "OPENAI_API_KEY": "ok" if openai else ""}.get(name, "")
        return k

    def test_groq_ladder_skips_retired_models(self):
        calls = []
        def fetch(url, method="GET", headers=None, json_body=None, data=None, timeout=25):
            m = json_body["model"]; calls.append(m)
            if "llama-4" in m:
                raise _http_err(404, "The model `%s` does not exist or you do not have access to it." % m)
            return 200, json.dumps({"choices": [{"message": {"content": "A dark chat screenshot: the user typed 'open youtube' and ORA-COOL replied 'Opening YouTube now, sir.'"}}]}), {}
        with mock.patch.object(s, "key", self._keys()), mock.patch.object(s, "http_fetch", fetch), \
             mock.patch.dict(s.KEYS, {"GROQ_VISION_MODEL": "meta-llama/llama-4-scout-17b-16e-instruct"}, clear=False):
            out = s._vision_describe("shot.png", "image/png", PNG, purpose="attachment")
        self.assertIn("open youtube", out)
        self.assertEqual(calls[0], "meta-llama/llama-4-scout-17b-16e-instruct")  # configured model first…
        self.assertIn("qwen/qwen3.8-27b", calls)                                     # …then the multimodal fallback
        self.assertEqual(s._VISION_LAST["provider"], "qwen/qwen3.8-27b")

    def test_never_fabricates_and_records_error(self):
        def fetch(url, method="GET", headers=None, json_body=None, data=None, timeout=25):
            raise _http_err(404, "model_not_found")
        with mock.patch.object(s, "key", self._keys()), mock.patch.object(s, "http_fetch", fetch):
            out = s._vision_describe("shot.png", "image/png", PNG, purpose="attachment")
        self.assertEqual(out, "")
        self.assertIn("model_not_found", s._VISION_LAST["error"])

    def test_default_models(self):
        with mock.patch.dict(s.KEYS, {}, clear=False):
            s.KEYS.pop("GROQ_VISION_MODEL", None)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GROQ_VISION_MODEL", None)
                self.assertEqual(s._groq_vision_models()[0], "qwen/qwen3.8-27b")


class AnalyzeFile(unittest.TestCase):
    def test_attachment_fields(self):
        with mock.patch.object(s, "_vision_describe", lambda *a, **k: "A red bicycle leaning on a blue wall; sign reads 'OPEN'."), \
             mock.patch.object(s, "media_inspect", lambda **k: {"sha256": "ab" * 32, "format": "png", "width": 1, "height": 1}):
            r = s.analyze_file("bike.png", "image/png", PNG, purpose="attachment")
        self.assertTrue(r["vision"]); self.assertEqual(r["vision_error"], "")
        self.assertIn("red bicycle", r["text"]); self.assertIn("red bicycle", r["note"]); self.assertGreater(r["chars"], 10)

    def test_honest_when_vision_down(self):
        s._VISION_LAST.update(provider="", error="qwen/qwen3.8-27b: rate limited")
        with mock.patch.object(s, "_vision_describe", lambda *a, **k: ""), \
             mock.patch.object(s, "media_inspect", lambda **k: {"sha256": "ab" * 32, "format": "png", "width": 1, "height": 1}):
            r = s.analyze_file("bike.png", "image/png", PNG, purpose="attachment")
        self.assertFalse(r["vision"]); self.assertEqual(r["text"], "")
        self.assertIn("could not look inside", r["note"]); self.assertIn("rate limited", r["vision_error"])

    def test_attachment_prompt_transcribes_text(self):
        self.assertIn("attachment", s._VISION_PROMPTS)
        self.assertIn("visible text transcribed", s._VISION_PROMPTS["attachment"])


class Client(unittest.TestCase):
    def test_attach_flow(self):
        for needle in ("purpose:isImg?'attachment':''", "pendingFiles.push({name:f.name, type:'image', thumb:dataURL,",
                       "never say you cannot see images", "=== ${f.type==='image'?'IMAGE':'FILE'}: ${f.name}",
                       "files.some(f=>f.thumb)", "I could not look inside this picture right now"):
            self.assertIn(needle, APP, needle)
        self.assertEqual(APP.count("purpose:'attachment'"), 2)  # both camera "attach to your message" paths

    def test_composer_clears_before_early_returns(self):
        i_clear = APP.index("// patch44: clear the box before any early-return path")
        i_device = APP.index("const dc=runDeviceCommand(text);")
        self.assertLess(i_clear, i_device)


if __name__ == "__main__":
    unittest.main()
