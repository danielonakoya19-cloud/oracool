# Patch 30 — Websites panel + in-place AI edits (the builder can now edit).
import json
import os
import shutil

import server

PAD = "pad pad pad pad " * 150


def _mk(slug, owner="e30@example.invalid", body="OLD HERO"):
    d = os.path.join(server._BUILDS_DIR, slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "index.html"), "w") as f:
        f.write("<html><head><title>t</title></head><body><h1>" + body + "</h1>" + PAD + "</body></html>")
    meta = server._builds_load()
    meta[slug] = {"owner": owner, "name": slug.replace("-", " ").title(), "brief": "b",
                  "t": server._now(), "template": "T", "files": ["index.html"]}
    server._builds_save(meta)
    return d


def _no_supa(monkeypatch):
    monkeypatch.setattr(server, "key", lambda k: None)


def test_build_edit_updates_in_place(monkeypatch):
    _no_supa(monkeypatch)
    slug = "p30-edit-site"
    d = _mk(slug)
    try:
        newdoc = "<html><head><title>t</title></head><body><h1>NEW GOLD HERO</h1>" + PAD + "</body></html>"
        monkeypatch.setattr(server, "_llm_text", lambda *a, **k: (newdoc, "mock", "stop"))
        monkeypatch.setattr(server, "_llm_text_stream", lambda *a, **k: (newdoc, "mock", "stop"))
        r = server.build_edit("e30@example.invalid", slug, "make the hero gold")
        assert r.get("ok"), r
        assert r["url"] == "/builds/p30-edit-site/"
        assert r["build_saved"] is False
        body = open(os.path.join(d, "index.html")).read()
        assert "NEW GOLD HERO" in body and "<h1>OLD HERO</h1>" not in body
        assert "edited" in server._builds_load()[slug]
    finally:
        shutil.rmtree(d, ignore_errors=True)
        m = server._builds_load(); m.pop(slug, None); server._builds_save(m)


def test_edit_owner_guard_and_missing(monkeypatch):
    _no_supa(monkeypatch)
    slug = "p30-own-site"
    d = _mk(slug)
    try:
        r = server.build_edit("thief@example.invalid", slug, "change footer")
        assert "different account" in r.get("error", "")
        assert "error" in server.build_edit("e30@example.invalid", "no-such-p30", "x")
    finally:
        shutil.rmtree(d, ignore_errors=True)
        m = server._builds_load(); m.pop(slug, None); server._builds_save(m)


def test_edit_truncation_guard(monkeypatch):
    _no_supa(monkeypatch)
    slug = "p30-trunc"
    d = _mk(slug)
    try:
        tiny = "<html><body><h1>TINY</h1>" + ("z" * 520) + "</body></html>"  # passes the parse floor, fails the 55% guard
        monkeypatch.setattr(server, "_llm_text", lambda *a, **k: (tiny, "mock", "length"))
        monkeypatch.setattr(server, "_llm_text_stream", lambda *a, **k: (tiny, "mock", "length"))
        r = server.build_edit("e30@example.invalid", slug, "shrink everything")
        assert "cut the page short" in r.get("error", "")
        assert "OLD HERO" in open(os.path.join(d, "index.html")).read()  # original intact
    finally:
        shutil.rmtree(d, ignore_errors=True)
        m = server._builds_load(); m.pop(slug, None); server._builds_save(m)


def test_auto_tools_edit_routing_never_rebuilds(monkeypatch):
    calls = {"edit": [], "build": [], "publish": []}
    monkeypatch.setattr(server, "build_edit", lambda em, sl, ins: (calls["edit"].append(sl), {"ok": True})[1])
    def no_build(*a, **k):
        calls["build"].append(a)
        raise AssertionError("must not rebuild")
    monkeypatch.setattr(server, "build_site", no_build)
    monkeypatch.setattr(server, "site_publish", lambda *a: calls["publish"].append(a))
    reg = {"becfom-hotel": {"owner": "p30t@example.invalid", "name": "Becfom Hotel",
                            "t": "2026-09-22T12:00:00", "files": ["index.html"]}}
    monkeypatch.setattr(server, "_builds_load", lambda: reg)
    out = server.auto_tools("change the hero color of my becfom site to gold", tier="free",
                            ha_url=None, ha_token=None, email="p30t@example.invalid")
    assert calls["edit"] == ["becfom-hotel"], calls
    assert not calls["build"] and not calls["publish"]
    assert any(t["tool"] == "edit" for t in out)
    # 'make my site's footer say 24/7' — build verb + part word must STILL be an edit
    calls["edit"].clear()
    out2 = server.auto_tools("make my site's footer say 24/7", tier="free",
                             ha_url=None, ha_token=None, email="p30t@example.invalid")
    assert calls["edit"] == ["becfom-hotel"], calls
    assert any(t["tool"] == "edit" for t in out2)
    # a genuine new build request must stay a build
    calls["edit"].clear()
    def ok_build(em, name, brief):
        calls["build"].append(name)
        return {"ok": True, "slug": "x", "url": "/builds/x/", "note": ""}
    monkeypatch.setattr(server, "build_site", ok_build)
    server.auto_tools("build a website for a new bakery with a gold hero section", tier="free",
                      ha_url=None, ha_token=None, email="p30t@example.invalid")
    assert calls["build"] and not calls["edit"]


def test_build_delete_removes_everything(monkeypatch):
    _no_supa(monkeypatch)
    slug = "p30-gone"
    d = _mk(slug)
    r = server.build_delete("e30@example.invalid", slug)
    assert r.get("ok")
    assert not os.path.isdir(d)
    assert slug not in server._builds_load()
    assert "error" in server.build_delete("e30@example.invalid", slug)


def test_websites_panel_and_card_wiring():
    html = open(os.path.join(server.BASE_DIR, "index.html"), encoding="utf-8").read()
    assert 'data-v="web"' in html and 'id="p-web"' in html
    assert "async function renderWeb()" in html and "function editModal(x){" in html
    assert "id=\"bEdit\"" in html and "editModal(x)" in html          # card Edit button
    assert "x.tool==='edit'&&x.url" in html                            # chat card on edit tool
    assert "else if(v==='web')renderWeb();" in html                    # panel renderer hook
    css = open(os.path.join(server.BASE_DIR, "console-layout.css"), encoding="utf-8").read()
    assert ".webgrid" in css and ".webshot iframe" in css
    # honest hosting wording: no more subdomain promises in marketing lines
    assert "1-click publish to <b>&lt;name&gt;.oracoolai.com</b>" not in html
    assert "hosted on oracoolai.com</b> — live instantly" in html


def test_builds_route_edit_and_delete_actions():
    src = open(os.path.join(server.BASE_DIR, "server.py"), encoding="utf-8").read()
    assert 'if _bact == "edit":' in src and '_bact == "delete"' in src
