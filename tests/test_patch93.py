"""patch93 — Connectors system (GitHub/Drive/Gmail/... usable by the AI) + community fan-out fixes."""
import re
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_SRC = (ROOT / "server.py").read_text(encoding="utf-8")
INDEX_SRC = (ROOT / "index.html").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT))
import server  # noqa: E402
import pytest  # noqa: E402


# ---------------------------------------------------------------- connector engine

def test_connector_specs_cover_the_panel():
    keys = set(server.CONNECTOR_SPECS)
    expected = {"github", "notion", "linear", "dropbox", "vercel", "supabase",
                "drive", "docs", "gmail", "calendar", "slides", "sheets"}
    assert expected <= keys
    assert server.spec_auth("gmail") == "google"
    assert server.spec_auth("drive") == "google"
    assert server.spec_auth("github") == "token"
    assert server.spec_auth("supabase") == "pair"
    for name in expected:
        spec = server.CONNECTOR_SPECS[name]
        assert spec.get("label") and spec.get("desc")


def test_connectors_public_never_leaks_secrets(monkeypatch):
    store = {"connectors:a@b.c": {
        "github": {"token": "ghp_SECRET", "meta": {"verified": "connected as octo", "at": 1}},
        "google": {"access_token": "ya29_SECRET", "refresh_token": "rt_SECRET", "meta": {"email": "a@b.c"}},
    }}
    monkeypatch.setattr(server, "supabase_kv_get", lambda k: store.get(k))
    pub = server.connectors_public("a@b.c")
    flat = str(pub)
    assert "ghp_SECRET" not in flat and "ya29_SECRET" not in flat and "rt_SECRET" not in flat
    by = {c["name"]: c for c in pub}
    assert by["github"]["connected"] is True
    assert by["gmail"]["connected"] is True          # shared google credential
    assert by["drive"]["connected"] is True
    assert by["notion"]["connected"] is False
    for c in pub:
        assert "token" not in c and "access_token" not in c and "cred" not in c


def test_connector_call_unconnected_points_to_settings(monkeypatch):
    monkeypatch.setattr(server, "connectors_state", lambda email: {})
    st, res = server._connector_call("nobody@x.y", "check my gmail inbox please")
    assert st == "done"
    assert "not linked" in res
    assert "Link" in res and "card" in res and "paste" in res


def test_connector_call_runs_when_linked(monkeypatch):
    monkeypatch.setattr(server, "connectors_state", lambda email: {"google": {"access_token": "t", "meta": {}}})
    monkeypatch.setattr(server, "google_access", lambda st: "t")
    monkeypatch.setattr(server, "_connector_do", lambda name, c, low, text: "Gmail inbox:\n- a | b | c")
    st, res = server._connector_call("a@b.c", "check my gmail inbox")
    assert st == "done" and "Gmail inbox" in res


def test_conn_find_id():
    assert server._conn_find_id("open https://docs.google.com/document/d/abcDEF1234_-/edit") == "abcDEF1234_-"
    assert server._conn_find_id("nothing here") == ""


def test_google_oauth_url_shape():
    url = server.connectors_google_url("a@b.c")
    if "url" in url:
        assert "accounts.google.com/o/oauth2/v2/auth" in url["url"]
        assert "client_id=" in url["url"]
        assert "/api/connectors/google/callback" in url["url"]
        assert "state=" in url["url"]
    else:
        assert "GOOGLE_CLIENT_ID" in url.get("error", "")


# ---------------------------------------------------------------- community fixes

class _FakeSvc:
    def __init__(self):
        self.sent = []

    def rooms(self, email=None, **kw):
        return [{"name": "General", "slug": "general", "kind": "channel"},
                {"name": "Dev", "slug": "dev", "kind": "group"},
                {"name": "Ann DM", "slug": "dm-ann", "kind": "dm"}]

    def room_send(self, email, slug, text):
        self.sent.append((slug, text))
        return {"ok": True, "message": {"id": "x"}}

    def dm_send(self, email, who, text):
        return {"ok": True}

    def dm_threads(self, email):
        return []

    def chat_brief(self, email):
        return {"rooms": []}


@pytest.fixture
def fake_community(monkeypatch):
    svc = _FakeSvc()
    monkeypatch.setattr(server, "community_service", lambda: svc)
    return svc


def test_share_to_channels_fans_out(fake_community):
    act = server.community_action("a@b.c", "share to community channels: hello world")
    assert act[0] == "done"
    text = act[1].lower()
    assert "posted" in text and "general" in text and "dev" in text
    slugs = [s for s, _ in fake_community.sent]
    assert "general" in slugs and "dev" in slugs
    assert not any(s.startswith("dm") for s in slugs)   # DM rooms are never fan-out targets
    assert all("hello world" in t for _, t in fake_community.sent)


def test_all_channels_phrase_fans_out(fake_community):
    act = server.community_action("a@b.c", "post this to all my channels: maintenance at 9pm")
    assert act[0] == "done"
    slugs = [s for s, _ in fake_community.sent]
    assert "general" in slugs and "dev" in slugs


def test_targetless_post_asks_with_prefixed_question():
    act = server.community_action("a@b.c", "post to my community: hello there friends")
    assert act[0] == "ask"
    assert act[1].startswith("QUESTION FOR THE USER (ask this exact question, then stop):")


def test_unmatched_dm_target_asks_with_prefixed_question():
    act = server.community_action("a@b.c", "send a dm to @ghostuser saying hello there friend")
    assert act[0] in ("ask", "done")
    if act[0] == "ask":
        assert act[1].startswith("QUESTION FOR THE USER (ask this exact question, then stop):")


# ---------------------------------------------------------------- prompts + routes + client

def test_server_prompt_community_rules():
    assert "NEVER say you cannot post" in SERVER_SRC
    assert "never send them to the Community tab" in SERVER_SRC
    assert "If a tool result begins with QUESTION FOR THE USER" in SERVER_SRC
    assert "community post :: channel-or-user :: message" in SERVER_SRC


def test_server_prompt_connector_rules():
    assert "CONNECTORS: the member can link accounts" in SERVER_SRC
    assert "\\ud83d\\udd17 Link card" in SERVER_SRC   # source-level escape of the link emoji
    assert "Never pretend to have access that is not connected" in SERVER_SRC


def test_prompt_additions_have_no_internal_patch_ids():
    i = SERVER_SRC.find("CONNECTORS: the member can link accounts")
    assert i > 0
    assert not re.search(r"patch\d+", SERVER_SRC[i:i + 1600], re.I)
    j = SERVER_SRC.find("If a tool result begins with QUESTION FOR THE USER")
    assert j > 0
    assert not re.search(r"patch\d+", SERVER_SRC[j:j + 1600], re.I)


def test_connector_routes_exist():
    assert '"/api/connectors"' in SERVER_SRC
    assert '"/api/connectors/save"' in SERVER_SRC
    assert '"/api/connectors/remove"' in SERVER_SRC
    assert '"/api/connectors/google/url"' in SERVER_SRC
    assert '"/api/connectors/google/callback"' in SERVER_SRC
    assert '"/api/connectors"' in SERVER_SRC.split("protected = path.startswith")[1][:400]


def test_auto_tools_has_connector_branch():
    assert "_connector_call(email, t)" in SERVER_SRC
    seg = SERVER_SRC.split("linked connectors (GitHub, Drive, Gmail")[1][:900]
    assert "connected account" in seg


def test_client_connector_ui_wired():
    assert 'id="connBtn"' in INDEX_SRC
    assert "function connOpen()" in INDEX_SRC
    assert "function connRefresh()" in INDEX_SRC
    assert "/api/connectors/save" in INDEX_SRC
    assert "function connIcon(" in INDEX_SRC
    assert "function connRow(" in INDEX_SRC


def test_client_prompt_matches_server_rules():
    assert "QUESTION FOR THE USER" in INDEX_SRC
    assert "Never say posting is impossible" in INDEX_SRC
    assert "never say to copy-paste or to use the community tab" in INDEX_SRC.lower()


def test_client_ios15_safe_and_marker_intact():
    seg = INDEX_SRC.split("function connOpen()")[1][:2400]
    assert "??" not in seg
    assert "?.(" not in seg
    assert SERVER_SRC.count("patch99-viz-clean") == 2
