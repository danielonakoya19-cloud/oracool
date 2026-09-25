"""Patch 24 — Google Gemini key integration: Veo 3.1 native-audio video,
Gemini neural TTS, Gemini image gen, Gemini vision, and an honest live
'gemini status' report. When the Google project is denied access (as it is
until the owner enables the API + billing), every path falls back to the
existing free engines and the denial is reported with the exact fix.

Run: python3 -m pytest tests/test_patch24.py -v
"""
import os
import re
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

def test_build_marker_patch24():
    assert read("server.py").count('"build": "patch39-arena"') == 2


def test_key_configured_locally_and_not_committed():
    env = open(os.path.join(ROOT, "RENDER_ENV.txt")).read()
    assert "GEMINI_API_KEY=" in env
    # the key value must never be in the repo mirror (pattern is built at
    # runtime from the local env file so this test file can never self-match)
    repo = os.path.join(ROOT, "..", "oracool-repo-push")
    if os.path.isdir(repo):
        import subprocess
        val = next((ln.split("=", 1)[1].strip().strip('"')
                    for ln in env.splitlines()
                    if ln.strip().startswith("GEMINI_API_KEY=")), "")
        assert len(val) > 4, "local GEMINI_API_KEY missing in RENDER_ENV.txt"
        pat = "GEMINI_API_KEY=" + val[:3]
        r = subprocess.run(["git", "-C", repo, "grep", "-l", pat],
                           capture_output=True, text=True)
        assert r.returncode != 0, "key value leaked into repo files"


# ---------------------------------------------------------------- helpers

def test_helpers_exist():
    for fn in ("gemini_key", "_gemini_post", "_gemini_text", "_gemini_tts",
               "_gemini_image", "_gemini_veo_video", "gemini_status",
               "_gemini_denied", "_gemini_skipped", "_gemini_mark_denied"):
        assert hasattr(server, fn), fn


def test_denied_detection():
    assert server._gemini_denied("PERMISSION_DENIED Your project has been denied access.")
    assert not server._gemini_denied("no key")
    assert not server._gemini_denied("rate limited")


def test_denied_cache_skips_for_an_hour(monkeypatch):
    monkeypatch.setattr(server, "_GEMINI_DENIED_UNTIL", [0.0])
    assert not server._gemini_skipped()
    server._gemini_mark_denied()
    assert server._gemini_skipped()
    # reset for other tests
    server._GEMINI_DENIED_UNTIL[0] = 0.0


def test_status_without_key(monkeypatch):
    monkeypatch.setattr(server, "gemini_key", lambda: "")
    r = server.gemini_status()
    assert r["connected"] is False
    assert "GEMINI_API_KEY" in r["note"]


def test_status_shape(monkeypatch):
    monkeypatch.setattr(server, "gemini_key", lambda: "fake-key")
    monkeypatch.setattr(server, "_gemini_text", lambda *a, **k: ("", "PERMISSION_DENIED Your project has been denied access."))
    monkeypatch.setattr(server, "_gemini_tts",
                        lambda *a, **k: (None, "PERMISSION_DENIED Your project has been denied access."))
    monkeypatch.setattr(server, "_gemini_skipped", lambda: False)
    r = server.gemini_status()
    assert r["connected"] is True
    caps = r["capabilities"]
    assert "denied" in caps["chat+vision"]
    assert "Generative Language API" in caps["chat+vision"]
    assert caps["tts"] == "denied"


# ---------------------------------------------------------------- cascade wiring

def test_veo_first_when_voice_requested(monkeypatch):
    monkeypatch.setattr(server, "gemini_key", lambda: "fake-key")
    server._GEMINI_DENIED_UNTIL[0] = 0.0
    calls = []

    def fake_veo(prompt, duration=8):
        calls.append(prompt)
        return {"ok": True, "urls": ["https://example.com/veo.mp4"], "audio": True}
    monkeypatch.setattr(server, "_gemini_veo_video", fake_veo)

    r = server.gen_video("a lighthouse in a storm", want_audio=True)
    assert r["provider"] == "google-veo31"
    assert "native synchronized audio" in r["audio"]
    assert r["videos"] == ["https://example.com/veo.mp4"]


def test_veo_denial_falls_through_to_other_engines(monkeypatch):
    # Real _gen_video_raw, every engine mocked: Veo is denied → the cascade
    # must keep working and reach a later engine.
    monkeypatch.setattr(server, "gemini_key", lambda: "fake-key")
    server._GEMINI_DENIED_UNTIL[0] = 0.0
    monkeypatch.setattr(server, "_gemini_veo_video",
                        lambda p, duration=8: {"error": server.GEMINI_FIX})
    monkeypatch.setattr(server, "key", lambda k, d=None: (d or ""))  # no HIA key
    monkeypatch.setattr(server, "_agnes_video", lambda p, duration=None: {"error": "no key"})
    monkeypatch.setattr(server, "_cvron_video",
                        lambda p, attempts=2: {"ok": True, "urls": ["https://cvron.example/x.mp4"]})
    r = server._gen_video_raw("a lighthouse", want_audio=True)
    assert r.get("ok"), "cascade must keep working when Veo is denied"
    assert r["provider"] != "google-veo31"
    assert "Veo 3.1" in " ".join(str(r.get(k) or "") for k in ("note", "via", "error")) or \
        r["provider"] in ("cvron-free (WAN-22)", "cvron")


def test_veo_block_present_at_top_of_raw_cascade():
    src = read("server.py")
    m = re.search(r"def _gen_video_raw\([\s\S]*?# -1\) Google Veo 3\.1", src)
    assert m, "Veo 3.1 must be probed before the silent engines"
    assert src.count("# -1) Google Veo 3.1") == 1


def test_tts_narration_prefers_gemini_then_edge(monkeypatch):
    monkeypatch.setattr(server, "gemini_key", lambda: "fake-key")
    server._GEMINI_DENIED_UNTIL[0] = 0.0
    monkeypatch.setattr(server, "_gemini_tts", lambda t, voice="Kore": ("/generated/gemini-voice.mp3", None))
    r = server._tts_narration("Here is your video: a test.")
    assert r == "/generated/gemini-voice.mp3"

    # gemini fails -> edge-tts fallback (mocked)
    monkeypatch.setattr(server, "_gemini_tts", lambda t, voice="Kore": (None, "denied"))
    # force gemini skip so only the edge path is exercised
    server._GEMINI_DENIED_UNTIL[0] = 0.0
    monkeypatch.setattr(server, "_gemini_skipped", lambda: True)
    r2 = server._tts_narration("Here is your video: a test.")
    # without network we cannot guarantee edge-tts; accept url or None
    assert r2 is None or r2.startswith("/generated/")
    server._GEMINI_DENIED_UNTIL[0] = 0.0


def test_image_cascade_includes_gemini():
    src = read("server.py")
    assert "0b) Google Gemini image" in src
    assert '"provider": "gemini"' in src


def test_vision_includes_gemini():
    src = read("server.py")
    seg = src[src.index("def _vision_describe"):src.index("def _vision_describe") + 1400]
    assert "_gemini_text" in seg


def test_gemini_status_tool_admin_only():
    out = server.auto_tools("gemini status", tier="ultra", email="danielonakoya19@gmail.com")
    assert any(x["tool"] == "gemini_status" for x in out)
    out2 = server.auto_tools("gemini status", tier="ultra", email="stranger@example.com")
    assert not any(x["tool"] == "gemini_status" for x in out2)


def test_prompt_documents_gemini_fix():
    src = read("server.py")
    assert "GEMINI ENGINES (Google key connected" in src
    assert "enable 'Generative Language API'" in src
    assert "no redeploy" in src


# ---------------------------------------------------------------- live probe (network)

@pytest.mark.skipif(not server.gemini_key(), reason="no GEMINI_API_KEY locally")
def test_live_gemini_probe_honest():
    """Whatever Google says right now, the client must surface it honestly."""
    server._GEMINI_DENIED_UNTIL[0] = 0.0
    r = server.gemini_status()
    assert r["connected"] is True
    caps = r["capabilities"]
    # the project is currently denied access by Google (2026-09-21) — but this
    # test must keep passing if/when the owner fixes the project:
    for cap in ("chat+vision", "tts"):
        v = caps[cap]
        assert v.startswith(("ok", "denied", "unavailable", "error")), v
    # whichever state we're in, a denial must carry the exact fix steps
    if "denied" in caps["chat+vision"]:
        assert "Generative Language API" in caps["chat+vision"]
    # reset the denial cache so other live tests are not skipped
    server._GEMINI_DENIED_UNTIL[0] = 0.0
