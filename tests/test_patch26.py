# Patch 26 — big typing space, removed header bar, in-chat code workspace
import json
import os
import shutil

import pytest

import server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- server
def _serve(handler=None, port=None):
    import threading
    import urllib.request
    srv = server.ThreadingHTTPServer(("127.0.0.1", port or 0), server.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]

    def get(path):
        try:
            with urllib.request.urlopen(base + path, timeout=15) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    return srv, base, get


def test_manifest_route_lists_files_with_sizes(tmp_path):
    bdir = os.path.join(server._BUILDS_DIR, "p26test")
    os.makedirs(os.path.join(bdir, "assets"), exist_ok=True)
    try:
        open(os.path.join(bdir, "index.html"), "w").write("<html>hi</html>")
        open(os.path.join(bdir, "assets", "style.css"), "w").write("body{color:red}")
        open(os.path.join(bdir, "assets", "app.js"), "w").write("console.log(1)")
        open(os.path.join(bdir, "skip.zip"), "wb").write(b"zipzip")
        srv, base, get = _serve()
        try:
            st, body = get("/builds/p26test/manifest.json")
            assert st == 200
            j = json.loads(body)
            assert j["ok"] is True
            paths = [f["path"] for f in j["files"]]
            assert paths == ["assets/app.js", "assets/style.css", "index.html"]
            assert j["total"] == len("<html>hi</html>") + len("body{color:red}") + len("console.log(1)")
            assert all(f["size"] > 0 for f in j["files"])
            # individual file still served (code viewer fetches it)
            st2, b2 = get("/builds/p26test/assets/style.css")
            assert st2 == 200 and b2 == b"body{color:red}"
            # unknown manifest → 404
            st3, _ = get("/builds/nosuchbuild/manifest.json")
            assert st3 == 404
        finally:
            srv.shutdown()
    finally:
        shutil.rmtree(bdir, ignore_errors=True)


def test_manifest_traversal_blocked():
    srv, base, get = _serve()
    try:
        # "../" in slug must not reach the manifest branch
        st, _ = get("/builds/..%2F..%2Fdata/manifest.json")
        assert st == 404
    finally:
        srv.shutdown()


def test_health_marker_patch26():
    assert '"build": "patch26-ui"' in open(os.path.join(ROOT, "server.py")).read()


# ---------------------------------------------------------------- client
CLIENT = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
CSS = open(os.path.join(ROOT, "console-layout.css"), encoding="utf-8").read()


def test_big_typing_space():
    assert '<textarea class="chatinput" id="input" placeholder="What would you like to do?"' in CLIENT
    assert "gpt-input big" in CLIENT
    assert "gpt-actions" in CLIENT
    # Enter sends, Shift+Enter new line
    assert "e.key==='Enter' && !e.shiftKey" in CLIENT
    # auto-grow logic
    assert "Math.min(this.scrollHeight" in CLIENT


def test_header_bar_removed():
    assert "#app .reactorhead{display:none" in CSS


def test_code_workspace_ui():
    assert "function codeModal(x)" in CLIENT
    assert "m.id='codeOv'" in CLIENT
    assert "cws-tree" in CLIENT
    assert "manifest.json" in CLIENT  # fetches the file list
    assert "View code" in CLIENT      # button in the build card
    assert "cws-pre" in CSS and "codecard" in CSS


def test_old_single_line_input_gone():
    assert '<input type="text" class="chatinput" id="input"' not in CLIENT
