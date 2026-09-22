# Patch 28 — the build vault: previews, zips and lists survive every redeploy.
import json
import os
import shutil
import threading
import urllib.error
import urllib.request

import server

PAD = "pad pad pad pad pad pad pad pad " * 30


def _serve():
    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
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


def _no_supa(monkeypatch):
    monkeypatch.setattr(server, "key", lambda k: None)


def test_ghost_preview_gets_recovery_page(monkeypatch):
    """Old (pre-vault) build cards must explain themselves, not show a blank 404."""
    _no_supa(monkeypatch)
    srv, base, get = _serve()
    try:
        st, body = get("/builds/ghostly-p28/")
        assert st == 200
        txt = body.decode("utf-8", "replace")
        assert "predates the vault" in txt and "rebuild it" in txt
        # non-html misses are still plain 404s (no fake pages for assets)
        st2, _ = get("/builds/ghostly-p28/app.js")
        assert st2 == 404
        # and a missing manifest still 404s (json route untouched)
        st3, _ = get("/builds/ghostly-p28/manifest.json")
        assert st3 == 404
    finally:
        srv.shutdown()


def test_build_site_reports_vault_save_flag(monkeypatch):
    """build_site mirrors to the vault and tells the caller whether it stuck.
    With no Supabase configured the flag is False but the build still succeeds."""
    _no_supa(monkeypatch)
    html = ("<html><head><title>V</title></head><body><h1>Vault site</h1>"
            + "<!--" + PAD + "-->" + "</body></html>")
    monkeypatch.setattr(
        server, "_llm_text",
        lambda s, u, max_tokens=16000, extra_msgs=None:
        ("TEMPLATE: Mock Vault\nTITLE: Vaultp28\nBEGIN index.html\n" + html + "\nEND", "mock", "stop"))
    meta = server._builds_load()
    meta.pop("vaultp28", None)
    server._builds_save(meta)
    r = server.build_site("vault.test28@example.com", "Vaultp28 Site", "a one page site")
    assert r.get("ok"), r
    assert r["build_saved"] is False  # no keys -> graceful offline mode
    assert "auto-saved" in r["note"]


def test_vault_roundtrip_with_fake_supabase(monkeypatch):
    """Save -> simulate the disk wipe -> lazy hydrate brings the workspace back,
    including its registry row (so 'My builds' and publish both recover)."""
    files_rows = [{"path": "index.html", "content": "<html>vaulted " + PAD + "</html>"}]
    mrow = {"slug": "p28ghost", "owner": "v28@example.invalid", "name": "P28 Ghost",
            "brief": "b", "provider": "mock", "template": "Mock", "t": "2026-09-22T10:00",
            "files": json.dumps(["index.html"])}
    calls = []

    def fake_fetch(url, method="GET", headers=None, json_body=None, data=None, timeout=25):
        calls.append((method, url))
        if "oracool_builds_meta" in url and method == "GET":
            return 200, json.dumps([mrow]).encode(), None
        if "oracool_builds" in url and method == "GET":
            return 200, json.dumps(files_rows).encode(), None
        return 204, b"", None

    monkeypatch.setattr(server, "http_fetch", fake_fetch)
    monkeypatch.setattr(server, "key",
                        lambda k: "https://fake.supabase.co" if k == "SUPABASE_URL" else "svc")
    # save pushes DELETE + two POSTs
    assert server.builds_sup_save("p28ghost", files_rows) is True
    assert any(m == "DELETE" and "oracool_builds?slug=eq.p28ghost" in u for m, u in calls)
    # wipe the disk like a redeploy would
    shutil.rmtree(os.path.join(server._BUILDS_DIR, "p28ghost"), ignore_errors=True)
    meta = server._builds_load()
    meta.pop("p28ghost", None)
    server._builds_save(meta)
    # hydrate restores files AND the registry row
    assert server.build_hydrate("p28ghost") is True
    idx = os.path.join(server._BUILDS_DIR, "p28ghost", "index.html")
    assert "vaulted" in open(idx, encoding="utf-8").read()
    got = server._builds_load().get("p28ghost") or {}
    assert got.get("owner") == "v28@example.invalid" and got.get("name") == "P28 Ghost"
    # zip and publish both work again after hydration
    assert server.build_zip("p28ghost")
    shutil.rmtree(os.path.join(server._BUILDS_DIR, "p28ghost"), ignore_errors=True)
    meta = server._builds_load()
    meta.pop("p28ghost", None)
    server._builds_save(meta)


def test_builds_warm_rebuilds_registry_once(monkeypatch):
    rows = [{"slug": "p28warm", "owner": "w28@example.invalid", "name": "Warm",
             "brief": "", "provider": "mock", "template": "T", "t": "2026-09-22T11:00",
             "files": json.dumps(["index.html"])}]
    n = {"calls": 0}

    def fake_fetch(url, method="GET", headers=None, json_body=None, data=None, timeout=25):
        n["calls"] += 1
        return 200, json.dumps(rows).encode(), None

    monkeypatch.setattr(server, "http_fetch", fake_fetch)
    monkeypatch.setattr(server, "key",
                        lambda k: "https://fake.supabase.co" if k == "SUPABASE_URL" else "svc")
    monkeypatch.setattr(server, "_builds_warmed", False)
    meta = server._builds_load()
    meta.pop("p28warm", None)
    server._builds_save(meta)
    server.builds_warm()
    assert "p28warm" in server._builds_load()
    first = n["calls"]
    server.builds_warm()          # second call must be a no-op
    assert n["calls"] == first
    shutil.rmtree(os.path.join(server._BUILDS_DIR, "p28warm"), ignore_errors=True)


def test_publish_before_vault_gets_honest_error(monkeypatch):
    """A registry row whose files predate the vault must NOT silently publish
    an empty site — the user gets told to rebuild once."""
    _no_supa(monkeypatch)
    slug = "p28prevault"
    meta = server._builds_load()
    meta[slug] = {"owner": "pv28@example.invalid", "name": "Old", "brief": "",
                  "t": server._now(), "files": ["index.html"]}
    server._builds_save(meta)
    shutil.rmtree(os.path.join(server._BUILDS_DIR, slug), ignore_errors=True)
    r = server.site_publish("pv28@example.invalid", slug, "")
    assert "predates the auto-save vault" in r.get("error", "")


def test_client_links_prefer_the_instant_url():
    html = open(os.path.join(server.BASE_DIR, "index.html"), encoding="utf-8").read()
    # publish modal: primary link is path_url-derived, subdomain demoted to vanity note
    assert "const main=s.main_url||(location.origin+pth);" in html
    assert "vanity link" in html
    # the AI rule must relay the working url, not promise the subdomain
    assert "report exactly that url (oracoolai.com/sites/<sub>/)" in html
