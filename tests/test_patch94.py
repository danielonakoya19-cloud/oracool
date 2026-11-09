"""patch94 — connectors panel UX: real scrolling, app icons, dead links removed."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_SRC = (ROOT / "server.py").read_text(encoding="utf-8")
INDEX_SRC = (ROOT / "index.html").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT))
import server  # noqa: E402


# ---------------------------------------------------------------- scrolling + layout

def test_conn_css_is_global_not_in_media_query():
    i = INDEX_SRC.find('.connov{')
    assert i > 0
    before = INDEX_SRC[:i]
    m = before.rfind('@media')
    if m != -1:
        chunk = INDEX_SRC[m:i]
        depth = 0
        for ch in chunk:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
        assert depth <= 0, 'panel CSS is trapped inside a @media block again'


def test_conn_overlay_scrolls_on_touch():
    i = INDEX_SRC.find('.connov{')
    rule = INDEX_SRC[i:INDEX_SRC.find('}', i) + 1]
    assert '-webkit-overflow-scrolling:touch' in rule
    assert 'overflow-y:auto' in rule
    assert 'overscroll-behavior:contain' in rule
    # flex-centre overflow trap must not come back: box scrolls via auto margins
    j = INDEX_SRC.find('.connbox{')
    box = INDEX_SRC[j:INDEX_SRC.find('}', j) + 1]
    assert 'margin:auto' in box
    assert 'max-height:82vh' not in box


# ---------------------------------------------------------------- icons

def test_every_connector_has_an_icon():
    i = INDEX_SRC.find('function connIcon(')
    assert i > 0
    seg = INDEX_SRC[i:INDEX_SRC.find('function connClose', i)]
    for name in server.CONNECTOR_SPECS:
        assert (name + ':') in seg, 'missing icon for ' + name
    assert 'connIcon(c.name)' in INDEX_SRC


# ---------------------------------------------------------------- dead links removed

def test_google_oauth_button_removed_from_client():
    assert 'conngo' not in INDEX_SRC
    assert 'Connect Google' not in INDEX_SRC
    assert 'oracool_conn_intent' not in INDEX_SRC
    assert 'connector OAuth return' not in INDEX_SRC
    # patch95: google/url is back as a REAL popup flow (window.open), never a dead button
    assert '/api/connectors/google/url' in INDEX_SRC
    assert 'window.open(' in INDEX_SRC


def test_google_rows_use_token_paste_now():
    i = INDEX_SRC.find('function connRow(')
    seg = INDEX_SRC[i:INDEX_SRC.find('function connRefresh', i)]
    assert "connLogin(" in seg                 # every row opens the login modal
    assert "c.auth==='google'" not in seg      # no special-case dead button
    # token paste is the modal fallback, not the front face
    assert 'Use a token instead' in INDEX_SRC


# ---------------------------------------------------------------- hints

def test_all_hint_urls_are_https_and_present():
    for name, spec in server.CONNECTOR_SPECS.items():
        u = spec.get("hint_url", "")
        assert u.startswith("https://"), name + " hint_url missing/not https"
        assert spec.get("hint"), name


def test_hint_url_reaches_the_public_payload(monkeypatch):
    store = {"connectors:a@b.c": {"github": {"token": "t", "meta": {}}}}
    monkeypatch.setattr(server, "supabase_kv_get", lambda k: store.get(k))
    pub = {c["name"]: c for c in server.connectors_public("a@b.c")}
    assert pub["github"]["hint_url"] == "https://github.com/settings/tokens"
    assert pub["gmail"]["hint_url"] == "https://developers.google.com/oauthplayground"
    flat = str(pub)
    assert "token\": \"t\"" not in flat


def test_hints_are_shown_as_real_links_in_the_panel():
    assert 'hint_url' in INDEX_SRC
    assert 'rel="noopener noreferrer"' in INDEX_SRC
    assert 'Get one at <a href=' in INDEX_SRC


# ---------------------------------------------------------------- wording + marker

def test_unconnected_message_points_to_links_button(monkeypatch):
    monkeypatch.setattr(server, "connectors_state", lambda email: {})
    st, res = server._connector_call("nobody@x.y", "check my gmail inbox please")
    assert st == "done"
    assert "Link" in res and "card" in res
    assert "Settings" not in res       # the panel is not in Settings anymore


def test_prompt_points_to_links_button():
    assert "\\ud83d\\udd17 Link card" in SERVER_SRC
    assert "Settings \\u2192 Connectors" not in SERVER_SRC
    assert "Links" in INDEX_SRC


def test_marker():
    assert SERVER_SRC.count("patch98-qr-certs") == 2
