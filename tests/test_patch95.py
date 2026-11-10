"""patch95 — image-style "Log in to X" modal with real popup OAuth (GitHub one-tap, Google self-service)."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_SRC = (ROOT / "server.py").read_text(encoding="utf-8")
INDEX_SRC = (ROOT / "index.html").read_text(encoding="utf-8")
OAUTH_SRC = (ROOT / "oauth.html").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT))
import server  # noqa: E402


# ---------------------------------------------------------------- the modal UX (matches the design image)

def test_login_modal_looks_like_the_design():
    assert "function connLogin(" in INDEX_SRC
    assert "Log in to " in INDEX_SRC
    assert "You will be prompted to log in and connect your account" in INDEX_SRC
    assert "Open window" in INDEX_SRC
    assert "cmicons" in INDEX_SRC and "cmdots" in INDEX_SRC   # [app] · · · [service] icon row
    assert "window.open(" in INDEX_SRC
    assert "Use a token instead" in INDEX_SRC                 # fallback, not the front face


def test_rows_open_the_modal():
    i = INDEX_SRC.find("function connRow(")
    seg = INDEX_SRC[i:INDEX_SRC.find("function connRefresh", i)]
    assert "connLogin(" in seg


# ---------------------------------------------------------------- GitHub one-tap

def test_github_login_uses_supabase_oauth_with_scopes():
    assert "provider:'github'" in INDEX_SRC
    assert "scopes:'repo read:org'" in INDEX_SRC
    assert "redirect_to: location.origin + '/oauth'" in INDEX_SRC


def test_oauth_return_carries_provider_token():
    assert "provider_token" in OAUTH_SRC
    assert "window.opener.postMessage({oracool_oauth" in OAUTH_SRC
    assert "oracool_oauth" in OAUTH_SRC     # non-popup sign-in path preserved


def test_client_saves_github_provider_token():
    i = INDEX_SRC.find("if(ev.data.oracool_oauth)")
    assert i > 0
    seg = INDEX_SRC[max(0, i - 800):i + 2400]
    assert "provider_token" in seg
    assert "/api/connectors/save" in seg
    assert "name:'github'" in seg


def test_oauth_url_forwards_scopes():
    assert 'and body.get("scopes") or ""' in SERVER_SRC or 'body.get("scopes") or ""' in SERVER_SRC
    assert '("&scopes=" + urllib.parse.quote(scopes)) if scopes else ""' in SERVER_SRC


# ---------------------------------------------------------------- Google self-service setup

def test_google_config_route_and_helpers():
    assert '"/api/connectors/google/config"' in SERVER_SRC
    assert "def google_oauth_save_cfg(" in SERVER_SRC
    assert "def google_oauth_ready(" in SERVER_SRC
    assert "google_ready" in SERVER_SRC
    assert 'endswith(".apps.googleusercontent.com")' in SERVER_SRC


def test_google_config_is_admin_gated_and_validated():
    assert "is_admin(email)" in SERVER_SRC.split("def google_oauth_save_cfg")[1][:400]
    assert "invalid or deleted" in SERVER_SRC          # live probe rejects dead clients
    assert "redirect_uri_mismatch" in SERVER_SRC        # and explains the missing redirect


def test_google_oauth_uses_operator_config_first():
    assert 'supabase_kv_get("connectors:google_oauth")' in SERVER_SRC
    i = SERVER_SRC.find("def google_oauth_cfg(")
    seg = SERVER_SRC[i:i + 500]
    assert 'key("GOOGLE_CLIENT_ID")' in seg             # keys.json stays the fallback
    assert "google_oauth_cfg().get" in SERVER_SRC       # url + token exchange use it


def test_save_route_verify_unpack_fixed():
    assert "_okv, _vmeta, _verr = _connector_verify(" in SERVER_SRC
    assert "_okv, _msg = _connector_verify(" not in SERVER_SRC


# ---------------------------------------------------------------- popup callback reporting

def test_google_callback_reports_back_to_the_panel():
    assert "oracool_conn:'google'" in SERVER_SRC
    assert "window.opener.postMessage" in SERVER_SRC
    assert "window.close()" in SERVER_SRC


def test_github_verify_notes_token_access_level():
    i = SERVER_SRC.find('def _connector_verify')
    seg = SERVER_SRC[i:i + 1500]
    assert "user/repos?per_page=1" in seg
    assert "full access" in seg


# ---------------------------------------------------------------- marker

def test_marker():
    assert SERVER_SRC.count("patch101-wa-qr") == 2
