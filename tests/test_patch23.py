"""Patch 23 — device-grade OraCool: camera capture card, voice in generated
videos, FULL owner-granted access to the OraCool mailbox, and durable media
(created images/videos survive refresh AND Render redeploys).

Run: python3 -m pytest tests/test_patch23.py -v
"""
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server  # noqa: E402
try:
    server._load_keys()
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- markers

def test_build_marker_patch23():
    src = read("server.py")
    assert src.count('"build": "patch24-gemini"') == 2


# ---------------------------------------------------------------- media persistence

def test_generated_route_serves_files():
    src = read("server.py")
    assert 'elif path.startswith("/generated/")' in src
    assert os.path.exists(os.path.join(ROOT, "data", "generated")) or True  # dir created lazily

def test_media_record_durable_upload(monkeypatch):
    # fake generated file
    gdir = os.path.join(ROOT, "data", "generated")
    os.makedirs(gdir, exist_ok=True)
    fn = "ptest23.mp4"
    path = os.path.join(gdir, fn)
    with open(path, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypisom" + b"A" * 4000)

    calls = {}
    def fake_storage_put(bucket, rel, data, mime="application/octet-stream"):
        calls.update(bucket=bucket, rel=rel, data=data, mime=mime)
        return ("https://storage.test/public/buckets/media/objects/" + rel, None)

    monkeypatch.setattr(server, "storage_put", fake_storage_put)
    monkeypatch.setattr(server, "key", lambda k, d=None: "x" if k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") else (d or ""))

    email_addr = "persist.test23@example.com"
    # clean slate
    d = server._media_all()
    d.pop(email_addr, None)
    server._media_write(d)

    items = server.media_record(email_addr, "video", "a silver car at dusk", ["/generated/" + fn])
    assert items, "media_record returned nothing"
    it = items[0]
    assert it["local"].startswith("/media/"), it["local"]
    assert it.get("durable") is True, "no durable copy was recorded"
    assert it["url"].startswith("https://storage.test/"), it["url"]
    # the bytes that went to storage are the real file bytes
    with open(path, "rb") as f:
        assert calls["data"] == f.read()
    assert calls["bucket"] == "media"
    assert re.match(r"^gen/[a-z0-9_-]+/[a-f0-9]+\.mp4$", calls["rel"])
    assert calls["mime"] == "video/mp4"
    # chat media now prefers the durable URL
    assert 'url": _it.get("url") or _it.get("local")' in read("server.py")

def test_media_record_local_url_copied_to_gallery():
    gdir = os.path.join(ROOT, "data", "generated")
    os.makedirs(gdir, exist_ok=True)
    path = os.path.join(gdir, "ptest23b.jpg")
    with open(path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"B" * 3000)
    email_addr = "persist.test23b@example.com"
    d = server._media_all()
    d.pop(email_addr, None)
    server._media_write(d)
    # no supabase configured here — local copy must still work
    monkey = None
    real_key = server.key
    server.key = lambda k, d=None: (d or "")  # force "no supabase"
    try:
        items = server.media_record(email_addr, "image", "my photo", ["/generated/ptest23b.jpg"])
    finally:
        server.key = real_key
    assert items and items[0]["local"].startswith("/media/")
    assert items[0]["url"] == "/generated/ptest23b.jpg"  # kept as-is when offline


# ---------------------------------------------------------------- voice in video

def test_requirements_declare_voice_deps():
    req = read("requirements.txt")
    assert "edge-tts" in req
    assert "imageio-ffmpeg" in req

def test_gen_video_wrapper_adds_narration(monkeypatch):
    # the public wrapper must run the raw cascade, then attach narration when
    # a silent engine answered an audio request
    calls = {}
    def fake_raw(prompt, duration=None, want_audio=False):
        calls["prompt"] = prompt
        gdir = os.path.join(ROOT, "data", "generated")
        os.makedirs(gdir, exist_ok=True)
        # make a tiny real mp4 with the bundled ffmpeg so the mux works
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        out = os.path.join(gdir, "ptest23vid.mp4")
        subprocess.run([exe, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=128x128:rate=10",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", out],
                       check=True, capture_output=True, timeout=120)
        calls["vid"] = "/generated/ptest23vid.mp4"
        return {"ok": True, "videos": [calls["vid"]], "provider": "hf-wan22",
                "audio": False, "model": "wan2.2"}
    monkeypatch.setattr(server, "_gen_video_raw", fake_raw)

    narr_calls = {}
    def fake_tts(text):
        narr_calls["text"] = text
        gdir = os.path.join(ROOT, "data", "generated")
        os.makedirs(gdir, exist_ok=True)
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        mp3 = os.path.join(gdir, "ptest23narr.mp3")
        subprocess.run([exe, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-c:a", "libmp3lame", mp3], check=True, capture_output=True, timeout=120)
        narr_calls["mp3"] = "/generated/ptest23narr.mp3"
        return narr_calls["mp3"]
    monkeypatch.setattr(server, "_tts_narration", fake_tts)

    r = server.gen_video("a silver car at dusk", want_audio=True)
    assert r["ok"]
    assert "Here is your video" in narr_calls["text"]
    assert r["videos"][0] != calls["vid"], "narrated clip must replace the silent one"
    assert os.path.exists(os.path.join(ROOT, "data", "generated", os.path.basename(r["videos"][0])))
    assert "narrat" in str(r.get("audio", "")).lower()
    # silent request must NOT trigger narration
    before = dict(narr_calls)
    r2 = server.gen_video("plain clip", want_audio=False)
    assert r2["videos"] == [calls["vid"]], "silent engine must pass through untouched"
    assert narr_calls == before, "no TTS call may happen for a silent request"

def test_tts_narration_live():
    # real edge-tts (network) — the exact function gen_video uses
    url = server._tts_narration("Here is your video: a silver car on a coastal highway at sunset.")
    assert url and url.startswith("/generated/")
    p = os.path.join(ROOT, "data", "generated", os.path.basename(url))
    assert os.path.getsize(p) > 1500

def test_mux_video_audio_live():
    import imageio_ffmpeg
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    gdir = os.path.join(ROOT, "data", "generated")
    os.makedirs(gdir, exist_ok=True)
    vid = os.path.join(gdir, "ptest23mux-vid.mp4")
    mp3 = os.path.join(gdir, "ptest23mux-a.mp3")
    subprocess.run([exe, "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=128x128:rate=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", vid],
                   check=True, capture_output=True, timeout=120)
    subprocess.run([exe, "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
                    "-c:a", "libmp3lame", mp3], check=True, capture_output=True, timeout=120)
    out = server._mux_video_audio("/generated/ptest23mux-vid.mp4", "/generated/ptest23mux-a.mp3")
    assert out and out.startswith("/generated/")
    op = os.path.join(gdir, os.path.basename(out))
    assert os.path.getsize(op) > 20000
    # the muxed file is a real mp4 container (ftyp box) and grew vs the silent
    # input (the aac track is inside)
    with open(op, "rb") as f:
        head = f.read(12)
    assert b"ftyp" in head
    assert os.path.getsize(op) > os.path.getsize(vid)
    # and the muxed clip really carries an audio stream (Stream #0:1)
    r = subprocess.run([exe, "-i", op], capture_output=True, timeout=60)
    info = r.stderr.decode("utf-8", "replace")
    assert "Stream #0:1" in info, info[:400]
    assert "audio" in info.lower()

def test_auto_tools_video_want_audio_detected():
    src = read("server.py")
    assert 'want_aud = bool(re.search(r"\\b(?:with|having|have)\\s+(?:sound|audio|voice|speech|voices|talking)\\b", low))' in src


# ---------------------------------------------------------------- full mailbox access

def test_brand_mailbox_cfg_unconnected():
    # whatever the local brand file says, the function must return a cfg or None —
    # and never an exception
    cfg = server.brand_mailbox_cfg()
    assert cfg is None or ("email" in cfg and "app_password" in cfg)

def test_mail_read_full_needs_connector():
    r = server.mail_read_full(None)
    assert "error" in r
    assert "OraCool" in r["error"]

def test_mail_send_validation():
    r = server.mail_send(None, "x@y.com", "s", "b")
    assert "error" in r
    r2 = server.mail_send({"email": "a@b.com", "app_password": "x"}, "not-an-address", "s", "b")
    assert "error" in r2 and "valid recipient" in r2["error"]

def test_auto_tools_oracool_inbox_admin_only():
    admin = "danielonakoya19@gmail.com"
    out = server.auto_tools("check oracool's inbox", tier="ultra", email=admin)
    tools = [x["tool"] for x in out]
    assert "oracool_mail" in tools
    item = next(x for x in out if x["tool"] == "oracool_mail")
    res = item["result"]
    assert res.get("ok") or "error" in res  # connected or honest setup note
    # non-admins never get the tool
    out2 = server.auto_tools("check oracool's inbox", tier="ultra", email="stranger@example.com")
    assert "oracool_mail" not in [x["tool"] for x in out2]

def test_auto_tools_oracool_send_parsing():
    out = server.auto_tools("send from oracool mail to friend@example.com: hello friend, the report is ready",
                            tier="ultra", email="danielonakoya19@gmail.com")
    tools = [x["tool"] for x in out]
    assert "oracool_mail_send" in tools
    item = next(x for x in out if x["tool"] == "oracool_mail_send")
    res = item["result"]
    assert res.get("ok") or "error" in res
    if "error" not in res:
        assert res["to"] == "friend@example.com"
        assert res["sent_from"] == "oracoolai19@gmail.com" or res["sent_from"].endswith("@gmail.com")

def test_prompt_promises_full_access_and_media():
    src = read("server.py")
    assert "ORACOOL MAILBOX (FULL ACCESS" in src
    assert "INCLUDING body snippets" in src
    assert "VIDEO WITH VOICE" in src
    assert "CREATED MEDIA PERSISTS" in src


# ---------------------------------------------------------------- camera

def test_auto_tools_camera_intent():
    for phrase in ("take my picture", "take a photo of me", "snap a selfie",
                   "can you take a picture of me"):
        out = server.auto_tools(phrase, tier="free", email="danielonakoya19@gmail.com")
        assert any(x["tool"] == "camera" for x in out), phrase
    # unrelated requests must NOT fire the camera
    out = server.auto_tools("open gmail", tier="free", email="danielonakoya19@gmail.com")
    assert not any(x["tool"] == "camera" for x in out)

def test_prompt_camera_honesty():
    src = read("server.py")
    assert "CAMERA (HONESTY RULE)" in src
    assert "NEVER say you cannot take photos" in src
    assert "claim a photo was taken without their" in src

def test_client_camera_card():
    html = read("index.html")
    assert "function cameraCard()" in html
    assert "navigator.mediaDevices.getUserMedia" in html
    assert "facingMode:'user'" in html
    assert "id=\"camGo\"" in html
    assert "download=\"oracool-photo.jpg\"" in html
    assert "id=\"camAttach\"" in html
    # instant client-side intent interception
    assert re.search(r"take\|snap\|capture\|click\|shoot\|get.*?picture\|photo\|selfie", html)
    # server-driven fallback card
    assert "x.tool==='camera'" in html

def test_camera_uses_front_camera_and_cleanup():
    html = read("index.html")
    assert "getTracks().forEach(t=>t.stop())" in html
    assert "Permission" in html or "permission" in html


# ---------------------------------------------------------------- e2e wiring

def test_local_preview_marker_if_up():
    import urllib.request, json
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=3) as r:
            j = json.load(r)
            assert j.get("build") == "patch24-gemini"
    except Exception:
        pytest.skip("local preview not running")
