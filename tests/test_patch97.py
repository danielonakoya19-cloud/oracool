"""patch97 — easy linking: paste-a-token-anywhere auto-link, one-tap Link cards in chat."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_SRC = (ROOT / "server.py").read_text(encoding="utf-8")
INDEX_SRC = (ROOT / "index.html").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT))
import server  # noqa: E402
import pytest  # noqa: E402


# ---------------------------------------------------------------- token detection

def test_detect_token_connector():
    assert server._detect_token_connector("ghp_" + "a" * 30) == "github"
    assert server._detect_token_connector("github_pat_" + "b" * 24) == "github"
    assert server._detect_token_connector("ntn_" + "c" * 18) == "notion"
    assert server._detect_token_connector("secret_" + "d" * 20) == "notion"
    assert server._detect_token_connector("lin_api_" + "e" * 16) == "linear"
    assert server._detect_token_connector("sl." + "f" * 16) == "dropbox"
    assert server._detect_token_connector("ya29." + "g" * 16) == "google"
    assert server._detect_token_connector("hello world") == ""
    assert server._detect_token_connector("sk-short") == ""


def test_redact_tokens_hides_secrets():
    tok = "ghp_" + "z" * 32
    out = server._redact_tokens("here is my token " + tok + " please link it")
    assert tok not in out
    assert "[token hidden]" in out
    # redaction works even for shapes we do not link
    out2 = server._redact_tokens("slack? " + "sl." + "q" * 16)
    assert "q" * 16 not in out2


# ---------------------------------------------------------------- auto-link

def test_connectors_try_token_links_after_live_verify(monkeypatch):
    store = {}
    monkeypatch.setattr(server, "supabase_kv_get", lambda k: store.get(k))
    monkeypatch.setattr(server, "supabase_kv_put", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(server, "_connector_verify",
                        lambda name, cred: (True, {"login": "octo"}, None))
    r = server.connectors_try_token("a@b.c", "ghp_" + "a" * 30)
    assert r["ok"] is True
    assert r["name"] == "github"
    assert "connected as octo" in r["message"]
    assert "ghp_" not in str(r)              # the token never comes back
    assert (store.get("connectors:a@b.c") or {}).get("github", {}).get("token")


def test_connectors_try_token_rejects_junk():
    r = server.connectors_try_token("a@b.c", "just some text")
    assert r["ok"] is False


def test_autolink_messages_redacts_and_links(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "connectors_try_token", lambda em, tk: calls.append(tk) or {"ok": True})
    msgs = [{"role": "user", "content": "link my github ghp_" + "b" * 30 + " please"}]
    server._conn_autolink_messages("a@b.c", msgs)
    assert calls == ["ghp_" + "b" * 30]
    assert "ghp_" not in msgs[0]["content"]
    assert "[token hidden]" in msgs[0]["content"]


# ---------------------------------------------------------------- link card in chat

def test_auto_tools_emits_link_card_when_unconnected(monkeypatch):
    monkeypatch.setattr(server, "_connector_call",
                        lambda email, text: ("done", "The Gmail connector is not linked yet. Tell the user to tap the link."))
    out = server.auto_tools("check my gmail inbox please", email="a@b.c")
    kinds = [x.get("tool") for x in out]
    assert "connector_link" in kinds
    card = next(x for x in out if x.get("tool") == "connector_link")
    assert card.get("conn") == "gmail"


def test_tool_summary_passes_conn_through():
    assert 'ts["conn"] = t.get("conn") or ""' in SERVER_SRC
    assert '"connector_link"' in SERVER_SRC


# ---------------------------------------------------------------- paste endpoint

def test_paste_route_exists():
    assert '"/api/connectors/paste"' in SERVER_SRC
    assert "connectors_try_token(_emp" in SERVER_SRC
    # the paste field must NOT be "token" — that collides with _auth's JWT body field
    assert 'body.get("connector_token") or body.get("cred")' in SERVER_SRC
    assert "connector_token:list[i].token" in INDEX_SRC


def test_connect_words_trigger_connector_branch():
    # "connect my gmail" is THE linking ask — the branch must fire without action verbs
    for w in ("'connect', 'link', 'unlink', 'disconnect'", "'set up', 'setup', 'authorize'"):
        assert w in SERVER_SRC


# ---------------------------------------------------------------- easier guidance

def test_unconnected_message_points_to_card_and_paste(monkeypatch):
    monkeypatch.setattr(server, "connectors_state", lambda email: {})
    st, res = server._connector_call("nobody@x.y", "check my gmail inbox please")
    assert st == "done"
    assert "Link" in res and "card" in res
    assert "paste" in res
    assert "Settings" not in res


def test_prompts_teach_paste_anywhere_and_no_echo():
    assert "paste the token here and it links automatically" in SERVER_SRC
    assert "NEVER repeat, quote, summarize or store the token" in SERVER_SRC
    assert "paste the token here (it links automatically and is hidden instantly)" in INDEX_SRC
    assert "NEVER repeat, quote or summarize the token" in INDEX_SRC
    # old friction rule retired
    assert "never ask the user to paste tokens into chat" not in INDEX_SRC


# ---------------------------------------------------------------- client

def test_client_has_paste_autolink():
    assert "function connExtractTokens(" in INDEX_SRC
    assert "function connPasteTokens(" in INDEX_SRC
    assert "function connRedactTokens(" in INDEX_SRC
    assert "/api/connectors/paste" in INDEX_SRC
    i = INDEX_SRC.find("async function send(")
    seg = INDEX_SRC[i:i + 2200]
    assert "connExtractTokens(text)" in seg
    assert "connRedactTokens(text)" in seg


def test_client_has_link_cards():
    assert "function connLinkCard(" in INDEX_SRC
    assert "function connCardTap(" in INDEX_SRC
    assert "connector_link" in INDEX_SRC
    assert "Link " in INDEX_SRC


# ---------------------------------------------------------------- marker

def test_marker():
    assert SERVER_SRC.count("patch98-qr-certs") == 2
