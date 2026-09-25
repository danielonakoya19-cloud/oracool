# Patch 27 — professional builder priority, build coins, subdomain publishing
import json
import os
import shutil

import pytest

import server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_BUILD = {"ok": True, "slug": "fake", "url": "/builds/fake/", "name": "Fake",
              "files": ["index.html"], "template": "Aurora SaaS"}


def _net_off(monkeypatch):
    """Keep every test offline: no Supabase hops, no network tiers."""
    monkeypatch.setattr(server, "supabase_upsert_flag", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(server, "supabase_get_flag", lambda e: None, raising=False)


def _run_auto(monkeypatch, text, tier="pro", email="t@t.com"):
    calls = {"img": 0, "build": 0}
    monkeypatch.setattr(server, "build_site",
                        lambda *a, **k: calls.__setitem__("build", calls["build"] + 1) or FAKE_BUILD)
    monkeypatch.setattr(server, "gen_image",
                        lambda *a, **k: calls.__setitem__("img", calls["img"] + 1) or
                        {"images": ["http://x/img.jpg"], "provider": "test"})
    _net_off(monkeypatch)
    runs = server.auto_tools(text, tier=tier, email=email)
    return [r["tool"] for r in runs], calls


# ---------------------------------------------------- build intent beats images
def test_wide_build_phrases_never_image(monkeypatch):
    for ph in ["create a professional and modern website for my gym",
               "make a really cool landing page",
               "design a stunning responsive website called Lumiere",
               "generate a premium online store for sneakers",
               "build me an app that tracks my fuel expenses"]:
        tools, calls = _run_auto(monkeypatch, ph)
        assert "build" in tools, ph
        assert "image" not in tools, ph
        assert calls["img"] == 0, ph


def test_logo_for_website_stays_image(monkeypatch):
    tools, calls = _run_auto(monkeypatch, "design a logo for my website")
    assert calls["img"] == 1 and calls["build"] == 0


def test_plain_image_request_still_works(monkeypatch):
    tools, calls = _run_auto(monkeypatch, "make a picture of a lighthouse at sunset")
    assert calls["img"] == 1 and calls["build"] == 0


def test_question_does_not_trigger_build_or_image(monkeypatch):
    tools, calls = _run_auto(monkeypatch, "how do I make money with an online store")
    assert "build" not in tools and calls["build"] == 0 and calls["img"] == 0


def test_question_with_image_noun_still_images(monkeypatch):
    tools, calls = _run_auto(monkeypatch, "can you create an image of a red fox in snow?")
    assert calls["img"] == 1 and calls["build"] == 0


# ------------------------------------------------------------------ coin wallet
def _wipe_user(email):
    u = server.load_users()
    u.pop(email, None)
    server.save_users(u)


@pytest.fixture()
def wallet(monkeypatch):
    _net_off(monkeypatch)
    monkeypatch.setattr(server, "check_tier", lambda e: "free")
    monkeypatch.setattr(server, "is_admin", lambda e: False)
    monkeypatch.setattr(server, "is_blocked", lambda e: False)
    email = "p27-coins-test@example.invalid"
    _wipe_user(email)
    yield email
    _wipe_user(email)


def test_wallet_grants_and_drains(wallet, monkeypatch):
    email = wallet
    st = server.coins_state(email)
    assert st["balance"] == 1_000_000 and st["cost_build"] == 10_000
    assert st["sites_left"] == 100 and not st["unlimited"]
    assert server.coins_gate(email) is None
    for _ in range(100):
        server.coins_charge(email)
    st2 = server.coins_state(email)
    assert st2["balance"] == 0 and st2["sites_left"] == 0
    err = server.coins_gate(email)
    assert err and err["locked"] == "coins" and "upgrade" in err["error"].lower()
    assert "one-time" in err["error"]  # patch38: Free never refills — honest wording, no cooldown promise


def test_wallet_refills_on_new_week(wallet, monkeypatch):
    email = wallet
    # patch38: the FREE grant is one-time — a stale record is kept, never refilled
    server.touch_user(email, coins={"week": "2020-W01", "balance": 3})
    st = server.coins_state(email)
    assert st["balance"] == 3 and st["one_time"] and st["reset_in_days"] is None
    # paid tiers still refill on the ISO week
    monkeypatch.setattr(server, "check_tier", lambda e: "starter")
    server.touch_user(email, coins={"week": "2020-W01", "balance": 3})
    st = server.coins_state(email)
    assert st["balance"] == 3_000_000 and not st["one_time"]


def test_wallet_partial_balance_kept_same_week(wallet):
    email = wallet
    wk = server._iso_week()
    server.touch_user(email, coins={"week": wk, "balance": 25_000})
    st = server.coins_state(email)
    assert st["balance"] == 25_000 and st["sites_left"] == 2


# ------------------------------------------------------------------- publishing
def _mk_build(slug, email, files=None):
    bdir = os.path.join(server._BUILDS_DIR, slug)
    os.makedirs(bdir, exist_ok=True)
    for path, body in (files or {"index.html": "<html>hello p27</html>"}).items():
        fp = os.path.join(bdir, path)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, "w", encoding="utf-8").write(body)
    meta = server._builds_load()
    meta[slug] = {"owner": email, "name": "P27 Test", "files": ["index.html"], "t": "2026-09-22", "template": "X"}
    server._builds_save(meta)
    return bdir


def _serve():
    import threading
    import urllib.request
    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]

    def get(path, host=None):
        req = urllib.request.Request(base + path)
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    return srv, get


def test_reserved_and_invalid_subs():
    assert not server._sub_valid("www") and not server._sub_valid("api")
    assert not server._sub_valid("ab") and not server._sub_valid("UPPER")
    assert not server._sub_valid("a site!")
    assert server._sub_valid("quick-fix-2")


def test_publish_serve_unpublish_cycle(monkeypatch):
    _net_off(monkeypatch)
    monkeypatch.setattr(server, "key", lambda k: None)
    monkeypatch.setattr(server, "is_admin", lambda e: False)
    email = "p27-pub-test@example.invalid"
    slug, sub = "p27pubsite", "p27pubsite"
    bdir = _mk_build(slug, email)
    try:
        r = server.site_publish(email, slug, sub)
        assert r["ok"] and r["sub"] == sub
        # patch28: primary url is the link that works instantly; the vanity
        # subdomain arrives with wildcard DNS and both serve the same site
        assert r["url"] == "https://oracoolai.com/sites/" + sub + "/"
        assert r["vanity_url"] == "https://" + sub + ".oracoolai.com/"
        assert r["path_url"] == "/sites/" + sub + "/"
        sdir = os.path.join(server._SITES_DIR, sub)
        assert open(os.path.join(sdir, "index.html"), encoding="utf-8").read() == "<html>hello p27</html>"
        reg = server._sites_load()
        assert reg[sub]["owner"] == email
        srv, get = _serve()
        try:
            st, body = get("/sites/" + sub + "/")
            assert st == 200 and b"hello p27" in body
            st2, _ = get("/sites/no-such-site-xyz/")
            assert st2 == 404
            st3, _ = get("/sites/" + sub + "/../../server.py")
            assert st3 in (400, 404)  # traversal blocked
            st4, b4 = get("/", host=sub + ".oracoolai.com")
            assert st4 == 200 and b"hello p27" in b4
            # hits counter moved (two html views)
            assert server._sites_load()[sub]["hits"] >= 2
            # foreign subdomain 404s fast
            st5, _ = get("/", host="notpublished.oracoolai.com")
            assert st5 == 404
        finally:
            srv.shutdown()
        # owner mismatch → refuse
        r2 = server.site_publish("someone-else@example.invalid", slug, sub)
        assert "error" in r2
        # domain attach + unpublish
        rd = server.site_domain_set(email, sub, "mystudio.com")
        assert rd["ok"] and server._sites_load()[sub]["custom_domain"] == "mystudio.com"
        bad = server.site_domain_set(email, sub, "not a domain")
        assert "error" in bad
        ru = server.site_unpublish(email, sub)
        assert ru["ok"]
        assert not os.path.isdir(sdir)
        assert sub not in server._sites_load()
    finally:
        shutil.rmtree(bdir, ignore_errors=True)
        shutil.rmtree(os.path.join(server._SITES_DIR, sub), ignore_errors=True)
        meta = server._builds_load()
        meta.pop(slug, None)
        server._builds_save(meta)
        reg = server._sites_load()
        reg.pop(sub, None)
        server._sites_save(reg)
        server._SITE_EXIST_CACHE.clear()
        server._SITE_HOST_CACHE.clear()
        _wipe_user(email)


def test_publish_requires_existing_build(monkeypatch):
    _net_off(monkeypatch)
    monkeypatch.setattr(server, "key", lambda k: None)
    r = server.site_publish("nobody@example.invalid", "no-such-build-xyz", "")
    assert "error" in r


# --------------------------------------------------------------------- markers
def test_marker_and_endpoints_present():
    src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
    assert src.count('"build": "patch44-vision"') == 2
    assert '"/api/sites"' in src and '"/api/coins"' in src
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    assert "publishModal" in html and "refreshCoins" in html and "coinPill" in html
    css = open(os.path.join(ROOT, "console-layout.css"), encoding="utf-8").read()
    assert ".pubcard" in css and ".coinpill" in css and ".plancard" in css
    sql = open(os.path.join(ROOT, "supabase_complete.sql"), encoding="utf-8").read()
    assert "published_sites_meta" in sql and "add column if not exists coins text" in sql
