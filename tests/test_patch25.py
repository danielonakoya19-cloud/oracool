"""Patch 25 — speed & reliability + Arena-style builder:
1. DM sends render instantly (optimistic UI) — no more frozen 'loading'
2. Calls get TURN relay + honest timeouts (no more stuck 'Connecting…')
3. Voice notes are transcoded to AAC/m4a so they play on every browser (Safari included)
4. Collapsible sidebar (slides in / out, remembered)
5. Auto-camera (opens + captures by itself, 3-second countdown — no card)
6. Arena-style app builder: live in-chat preview, download, deploy guide,
   one-click push to the user's own GitHub (repo create + GitHub Pages)

Run: python3 -m pytest tests/test_patch25.py -v
"""
import base64
import os
import re
import subprocess
import sys
import zipfile

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

def test_build_marker_patch25():
    assert read("server.py").count('"build": "patch30-studio"') == 2


# ---------------------------------------------------------------- voice notes

def test_voice_note_transcoded_to_m4a(monkeypatch, tmp_path):
    import imageio_ffmpeg
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    webm = tmp_path / "vn.webm"
    subprocess.run([exe, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:a", "libopus", str(webm)], check=True, capture_output=True, timeout=60)
    data = webm.read_bytes()
    b64 = base64.b64encode(data).decode()
    data_url = "data:audio/webm;base64," + b64

    stored = {}
    def fake_storage_put(bucket, rel, data, mime="application/octet-stream"):
        stored["rel"] = rel
        stored["data"] = data
        stored["mime"] = mime
        return ("https://storage.test/public/buckets/media/objects/" + rel, None)
    monkeypatch.setattr(server, "storage_put", fake_storage_put)

    r = server.upload_community_media("voice.test25@example.com", data_url)
    assert r.get("ok"), r
    assert stored["rel"].endswith(".m4a"), stored["rel"]
    assert stored["mime"] == "audio/mp4"
    # the stored bytes are a real mp4 (ftyp box) and decodable
    head = stored["data"][:12]
    assert b"ftyp" in head
    out = tmp_path / "out.m4a"
    out.write_bytes(stored["data"])
    probe = subprocess.run([exe, "-i", str(out)], capture_output=True, timeout=30)
    assert "audio" in probe.stderr.decode().lower()


def test_media_html_audio_fallback():
    html = read("index.html")
    assert 'onerror="this.outerHTML' in html
    assert "cmessaudio" in html


# ---------------------------------------------------------------- builder

def test_build_site_with_mocked_llm(monkeypatch):
    # patch27: the builder speaks the raw-HTML marker protocol now (no JSON)
    html = ("<html><head><title>Demo</title></head>"
            "<body><h1>Demo site</h1><script>console.log('hi')</script>"
            + "<!--" + "pad pad pad pad pad pad " * 40 + "-->"
            + "</body></html>")
    monkeypatch.setattr(
        server, "_llm_text",
        lambda s, u, max_tokens=16000, extra_msgs=None:
        ("TEMPLATE: Mock Minimal\nTITLE: Demo\nBEGIN index.html\n" + html + "\nEND", "mock", "stop"))
    # clean slate
    meta = server._builds_load()
    meta.pop("demo-site", None)
    server._builds_save(meta)
    r = server.build_site("builder.test25@example.com", "Demo Site", "a tiny demo site")
    assert r.get("ok"), r
    assert r["url"] == "/builds/demo-site/"
    # real project workspace: the self-contained page + an auto README
    assert "index.html" in r["files"] and "README.md" in r["files"]
    assert r.get("template") == "Mock Minimal"
    p = os.path.join(server._BUILDS_DIR, "demo-site", "index.html")
    assert os.path.exists(p)
    assert "Demo site" in open(p, encoding="utf-8").read()
    rd = open(os.path.join(server._BUILDS_DIR, "demo-site", "README.md"), encoding="utf-8").read()
    assert "OraCool AI" in rd

def test_build_slug_and_traversal(monkeypatch):
    monkeypatch.setattr(server, "_llm_json", lambda s, u, max_tokens=8000: (
        {"files": [{"path": "../../evil.html", "content": "x"},
                   {"path": "index.html", "content": "<title>ok</title>"}]}, "mock"))
    r = server.build_site("trav.test25@example.com", "Trav Site", "x")
    assert r.get("ok"), r
    # the traversal path must NOT have escaped the build dir
    evil = os.path.join(server._BUILDS_DIR, "..", "evil.html")
    assert not os.path.exists(os.path.normpath(evil))

def _build_fixture(slug="demo-site", owner="zip.test"):
    import os as _os
    dest = _os.path.join(server._BUILDS_DIR, slug)
    _os.makedirs(dest, exist_ok=True)
    with open(_os.path.join(dest, "index.html"), "w") as f:
        f.write("<title>fixture</title>")
    meta = server._builds_load()
    meta[slug] = {"owner": owner, "name": slug, "brief": "", "provider": "mock",
                  "files": ["index.html"], "t": server._now()}
    server._builds_save(meta)

def test_build_zip():
    _build_fixture()
    z = server.build_zip("demo-site")
    assert z
    zi = zipfile.ZipFile(__import__("io").BytesIO(z))
    names = zi.namelist()
    assert any(n.endswith("index.html") for n in names)

def test_build_list_scoped():
    _build_fixture()
    r = server.build_list("zip.test")
    assert r.get("ok")
    assert any(b.get("slug") == "demo-site" for b in r["builds"])


# ---------------------------------------------------------------- github (hermetic)

def _fake_gh(routes):
    def fake_http_fetch(url, method="GET", headers=None, json_body=None, data=None, timeout=25):
        for prefix, (code, payload) in routes.items():
            if url.endswith(prefix) or prefix in url:
                import json as _json
                return code, _json.dumps(payload).encode(), "application/json"
        return 404, b'{"message":"Not Found"}', "application/json"
    return fake_http_fetch

def test_github_connect_and_push(monkeypatch, tmp_path):
    # build a fixture site
    _build_fixture()
    pat = "ghp_" + "x" * 34
    calls = {}
    state = {"repo_created": False}
    def fake_http_fetch(url, method="GET", headers=None, json_body=None, data=None, timeout=25):
        import json as _json
        if url.endswith("/user") and method == "GET":
            return 200, _json.dumps({"login": "testdev"}).encode(), "application/json"
        if url.endswith("/repos/testdev/demo-site") and method == "GET":
            if state["repo_created"]:
                return 200, _json.dumps({"full_name": "testdev/demo-site",
                                         "html_url": "https://github.com/testdev/demo-site"}).encode(), "application/json"
            return 404, _json.dumps({"message": "Not Found"}).encode(), "application/json"
        if url.endswith("/user/repos") and method == "POST":
            calls["create"] = json_body
            state["repo_created"] = True
            return 201, _json.dumps({"full_name": "testdev/demo-site",
                                     "html_url": "https://github.com/testdev/demo-site"}).encode(), "application/json"
        if "/contents/" in url and method == "PUT":
            calls.setdefault("pushes", []).append(url.rsplit("/contents/", 1)[1])
            return 200, _json.dumps({"content": {"path": "f"}}).encode(), "application/json"
        if url.endswith("/pages") and method == "PUT":
            calls["pages"] = json_body
            return 200, _json.dumps({"html_url": "https://testdev.github.io/demo-site/"}).encode(), "application/json"
        return 404, b'{"message":"Not Found"}', "application/json"
    monkeypatch.setattr(server, "http_fetch", fake_http_fetch)

    em = "gh.test25@example.com"
    r = server.github_connect(em, pat)
    assert r.get("ok") and r.get("login") == "testdev", r
    # token is sealed in the vault, never returned
    v = server.vault_get(em, "github_pat")
    assert v.get("value") == pat

    s = server.github_status(em)
    assert s.get("connected") is True and s.get("login") == "testdev"

    p = server.github_push_build(em, "demo-site", "testdev/demo-site")
    assert p.get("ok"), p
    assert p["repo"] == "testdev/demo-site"
    assert "index.html" in p["pushed"]
    assert "pages" in p
    assert p.get("pages_url") == "https://testdev.github.io/demo-site/"
    assert calls.get("create"), "repo should be auto-created when missing"
    assert calls.get("pages"), "GitHub Pages should be enabled"

def test_github_bad_token(monkeypatch):
    def fake_http_fetch(url, method="GET", headers=None, json_body=None, data=None, timeout=25):
        return 401, b'{"message":"Bad credentials"}', "application/json"
    monkeypatch.setattr(server, "http_fetch", fake_http_fetch)
    r = server.github_connect("badgh.test@example.com", "ghp_" + "y" * 34)
    assert "error" in r

def test_github_requires_token():
    r = server.github_repos("nobody.gh25@example.com")
    assert "error" in r and "GitHub" in r["error"]


# ---------------------------------------------------------------- calls

def test_client_has_turn_and_timeouts():
    html = read("index.html")
    assert "turn:openrelay.metered.ca:443" in html
    assert "callWatchdog" in html
    assert "No answer — their app may be closed" in html
    assert "Could not establish a media connection" in html
    assert "CALL.ringing.call_id===c.call_id" in html


# ---------------------------------------------------------------- dm speed

def test_optimistic_dm_send():
    html = read("index.html")
    assert "optimistic bubble" in html
    assert "⏳" in html
    assert "optId.remove()" in html


# ---------------------------------------------------------------- sidebar

def test_collapsible_sidebar():
    html = read("index.html")
    css = read("console-layout.css")
    assert 'id="sbCollapse"' in html
    assert "sbToggleInit" in html
    assert html.count('class="slbl"') >= 15
    assert "oracool_sb_collapsed" in html
    assert "#app.sb-collapsed .sidebar{width:64px" in css
    assert "#app.sb-collapsed .side-item .slbl{display:none}" in css


# ---------------------------------------------------------------- auto camera

def test_auto_camera_no_card():
    html = read("index.html")
    assert "function cameraAuto()" in html
    assert "facingMode:'user'" in html
    assert "capturing automatically" in html
    # the instant intent path calls cameraAuto, not cameraCard
    seg = html[html.index("function runDeviceCommand"):html.index("function runDeviceCommand") + 2500]
    assert "cameraAuto()" in seg
    assert "3,2,1" in html or "for(const n of [3,2,1])" in html
    # server tool still fires the card fallback via __tools
    assert "x.tool==='camera'" in html


def test_prompt_camera_auto():
    src = read("server.py")
    assert "CAMERA (AUTO)" in src
    assert "3-second countdown" in src


# ---------------------------------------------------------------- builder wiring

def test_build_tool_in_auto_tools(monkeypatch):
    monkeypatch.setattr(server, "build_site", lambda em, name, prompt:
                        {"ok": True, "slug": "t", "url": "/builds/t/", "name": "T",
                         "files": ["index.html"], "provider": "mock"})
    out = server.auto_tools("build me a website called Demo Page for a bakery",
                            tier="free", email="builder.ui25@example.com")
    assert any(x["tool"] == "build" for x in out), out

def test_github_tools_in_auto_tools(monkeypatch):
    monkeypatch.setattr(server, "github_repos", lambda em: {"ok": True, "repos": []})
    out = server.auto_tools("show my github repos", tier="ultra", email="gh.ui25@example.com")
    assert any(x["tool"] == "github_repos" for x in out)

def test_tool_summary_carries_build_url():
    src = read("server.py")
    assert 'ts["url"] = tr["url"]' in src
    assert 'if t.get("tool") == "github_push":' in src

def test_builds_route_and_zip():
    src = read("server.py")
    assert 'elif path.startswith("/builds/"):' in src
    assert "download.zip" in src
    assert '"/api/builds"' in src and '"/api/github"' in src
    assert '"/api/sites"' in src and '"/api/coins"' in src  # patch27: publish + wallet are authed too

def test_protected_routes():
    src = read("server.py")
    m = re.search(r'protected = path\.startswith\(([^)]*)\)', src)
    assert m
    assert '"/api/builds"' in m.group(1)
    assert '"/api/github"' in m.group(1)

def test_prompt_builder_and_github():
    src = read("server.py")
    assert "APP BUILDER (Arena-style)" in src
    assert "GITHUB: the user can connect their own GitHub account" in src
    assert "AES-encrypted" in src


# ---------------------------------------------------------------- live (network)

@pytest.mark.skipif(not (server.key("GROQ_API_KEY") or server.key("OPENAI_API_KEY")
                         or server.key("AGNES_API_KEY")), reason="no LLM key locally")
def test_live_build_site():
    r = server.build_site("live.test25@example.com", "Live Test Site",
                          "a one-page landing page for a futuristic AI assistant, dark theme")
    if r.get("error"):
        pytest.skip("live LLM build unavailable: " + r["error"][:120])
    assert r.get("ok")
    p = os.path.join(server._BUILDS_DIR, r["slug"], "index.html")
    assert os.path.exists(p)
    assert len(open(p).read()) > 500

# ---------------------------------------------------------------- no-stale-app caching
def test_app_assets_not_stale_cached():
    import threading, urllib.request
    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        def hdrs(path):
            with urllib.request.urlopen(base + path, timeout=10) as r:
                return r.headers.get("Cache-Control", "")
        assert "no-cache" in hdrs("/app"), "app HTML must revalidate"
        assert "no-cache" in hdrs("/console-layout.css"), "css must revalidate"
        assert "no-cache" in hdrs("/communications.js"), "js must revalidate"
        assert "no-cache" in hdrs("/manifest.json"), "manifest must revalidate"
    finally:
        srv.shutdown()

def test_sw_cache_bumped_and_purged():
    sw = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sw.js")).read()
    assert "oracool-v4" in sw
    assert "caches.delete" in sw, "activate must purge old cache versions"
