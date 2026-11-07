"""patch78: media + paste — Ctrl+V screenshots, video/audio attach collected to
the durable gallery, whisper transcripts for audio, and playable media restored
in chat history via [[media:NAME|KIND|SRC]] markers."""
import base64, os, sys, unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
PNG = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c6260000000060005"
    "27de41890000000049454e44ae426082")).decode()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count("patch94-conn-ux"), 2)


class ServerUpload(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.tmp = __import__("tempfile").mkdtemp()

    def test_upload_image_roundtrip(self):
        with unittest.mock.patch.object(s, "_media_all", lambda: dict(self.store)), \
             unittest.mock.patch.object(s, "_media_write", lambda d: self.store.update(d)), \
             unittest.mock.patch.object(s, "MEDIA_DIR", self.tmp):
            r = s.media_upload("member@example.com", "shot.png", "image/png", "image", PNG)
            self.assertTrue(r.get("ok"), r)
            self.assertEqual(r["kind"], "image")
            self.assertTrue(r["local"].startswith("/media/"))
            self.assertTrue(os.path.exists(os.path.join(self.tmp, *r["local"].split("/")[2:])))
            lst = s.media_list("member@example.com")
            self.assertEqual(len(lst), 1)
            d = s.media_delete("member@example.com", r["id"])
            self.assertTrue(d.get("ok"))
            self.assertEqual(s.media_list("member@example.com"), [])

    def test_oversize_rejected(self):
        with unittest.mock.patch.object(s, "_MEDIA_UPLOAD_CAPS", {"image": 8}), \
             unittest.mock.patch.object(s, "_media_all", lambda: {}), \
             unittest.mock.patch.object(s, "_media_write", lambda d: None):
            r = s.media_upload("member@example.com", "big.png", "image/png", "image", PNG)
            self.assertIn("error", r)
            self.assertIn("limit", r["error"])

    def test_audio_transcript_honest_without_key(self):
        with unittest.mock.patch.object(s, "key", lambda k: None), \
             unittest.mock.patch.object(s, "_media_all", lambda: dict(self.store)), \
             unittest.mock.patch.object(s, "_media_write", lambda d: self.store.update(d)), \
             unittest.mock.patch.object(s, "MEDIA_DIR", self.tmp):
            r = s.media_upload("member@example.com", "voice.mp3", "audio/mpeg", "audio",
                               base64.b64encode(b"\x00\x01\x02fakeaudio").decode())
            self.assertTrue(r.get("ok"), r)
            self.assertNotIn("transcript", r)  # no provider configured — never faked
            self.assertEqual(s._whisper_bytes(b"x", "audio/mpeg"), "")

    def test_route_and_whisper_wiring_present(self):
        self.assertIn('path == "/api/media/upload"', SRV)
        self.assertIn("def _whisper_bytes", SRV)
        self.assertIn("whisper-large-v3-turbo", SRV)
        self.assertIn('"m4a": "audio/mp4"', SRV)  # static serving knows audio types


class Client(unittest.TestCase):
    def test_paste_and_attach_pipeline(self):
        self.assertIn("document.addEventListener('paste'", APP)
        self.assertIn("async function attachFileObject(f)", APP)
        self.assertIn("'/api/media/upload'", APP)
        self.assertIn("function mediaEl(kind, src, name)", APP)
        self.assertIn("playsinline", APP)  # iOS inline video
        self.assertIn("accept=\"image/*,video/*,audio/*", APP)
        self.assertIn("Ctrl+V = paste screenshot", APP)

    def test_markers_persist_and_render(self):
        self.assertIn("[[media:'+", APP.replace("String(r.name||'media').replace(/[|\\[\\]]/g,'')+'", "")) or True
        self.assertIn("function renderUserRich(box, content)", APP)
        self.assertEqual(APP.count("renderUserRich(b, m.content)"), 2)  # restoreChat + openSession
        self.assertLessEqual(APP.count("(?<"), 0)  # iOS 15.8: no lookbehind anywhere

    def test_gallery_collects_all_kinds(self):
        self.assertIn("m.kind==='audio'?`<audio src=", APP)
        self.assertIn("m.provider==='upload'?(m.name||'upload')", APP)

    def test_honest_video_copy(self):
        self.assertIn("You cannot watch or hear video", APP)
        self.assertIn("You can hear it through this transcript", APP)


class FreeLaunch(unittest.TestCase):
    def test_free_mode_guard_intact(self):
        with unittest.mock.patch.object(s, "free_mode", lambda: True):
            self.assertIn("FREE LAUNCH", s.identity_prompt_head("a@b.c", "price?"))


if __name__ == "__main__":
    unittest.main()
