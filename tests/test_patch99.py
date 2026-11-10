"""patch99 — charts (visualize anything), connector unification, presentation-clean UI."""
import pathlib
import re
import sys
import xml.dom.minidom

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_SRC = (ROOT / "server.py").read_text(encoding="utf-8")
INDEX_SRC = (ROOT / "index.html").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT))
import server  # noqa: E402
import pytest  # noqa: E402


# ---------------------------------------------------------------- charts

def test_svg_chart_all_types_valid_xml():
    for kind in ("bar", "line", "area", "pie", "donut"):
        svg = server.svg_chart(kind, ["Jan", "Feb", "Mar"], ["1200", "1800", "2400"], "Demo " + kind)
        xml.dom.minidom.parseString(svg)          # malformed XML raises
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        assert "Demo " + kind in svg
        assert "<rect" in svg or "<path" in svg


def test_svg_chart_values_and_escaping():
    svg = server.svg_chart("bar", ['<img src=x>'], ["5"], '<script>bad</script>')
    assert "<img" not in svg and "<script>" not in svg
    assert "&lt;script&gt;" in svg
    with pytest.raises(ValueError):
        server.svg_chart("bar", [], [], "t")
    # negatives and percentages parse
    svg2 = server.svg_chart("line", ["a", "b"], ["-3", "42%"], "neg")
    assert "-3" in svg2 and "42" in svg2


def test_auto_tools_chart_command_and_natural():
    out = server.auto_tools("chart :: bar :: Jan,Feb,Mar :: 1200,1800,2400 :: Monthly sales", email="a@b.c")
    hits = [x for x in out if x.get("tool") == "chart"]
    assert hits and hits[0]["result"]["type"] == "bar"
    assert hits[0]["result"]["title"] == "Monthly sales"
    assert "<svg" in hits[0]["result"]["svg"]
    out2 = server.auto_tools("visualize my monthly sales: jan 1200, feb 1800, mar 2400", email="a@b.c")
    hits2 = [x for x in out2 if x.get("tool") == "chart"]
    assert hits2 and len(hits2[0]["result"]["values"]) == 3


def test_auto_tools_chart_numbers_only_fallback():
    out = server.auto_tools("plot these numbers 5, 8, 13, 21", email="a@b.c")
    hits = [x for x in out if x.get("tool") == "chart"]
    assert hits and hits[0]["result"]["values"] == ["5", "8", "13", "21"]


def test_tool_summary_passes_chart_fields():
    assert 'if t.get("tool") == "chart":' in SERVER_SRC
    assert 'for _k in ("svg", "title", "type"):' in SERVER_SRC


def test_chart_prompt_and_client():
    assert "VISUALIZATION (REAL CHARTS)" in SERVER_SRC
    assert "chart :: <bar|line|area|pie|donut>" in SERVER_SRC
    assert "Charts are real too" in INDEX_SRC
    assert "function chartCard(" in INDEX_SRC
    assert "x.tool==='chart'" in INDEX_SRC


# ---------------------------------------------------------------- connector unification

def test_gh_uses_unified_tokens_and_clean_errors():
    assert "def _gh_tokens(email):" in SERVER_SRC
    assert "GitHub needs re-linking" in SERVER_SRC
    # _gh must fall through stale tokens (vault then connector state)
    i = SERVER_SRC.find("def _gh(email, path")
    seg = SERVER_SRC[i:i + 1600]
    assert "for pat in toks:" in seg
    assert "if st == 401:" in seg
    assert "_GH_RELINK" in seg


def test_linking_syncs_both_stores_and_reenables():
    i = SERVER_SRC.find("def connectors_try_token")
    seg = SERVER_SRC[i:i + 2400]
    assert 'vault_set(em, "github_pat", cred["token"])' in seg
    assert 'st["off"] = [x for x in (st.get("off") or []) if x != name]' in seg
    j = SERVER_SRC.find("def github_connect")
    seg2 = SERVER_SRC[j:j + 2600]
    assert 'st["github"] = {"token": pat' in seg2
    assert "def github_status(email):" in SERVER_SRC
    k = SERVER_SRC.find("def github_status")
    assert "_gh(email, \"/user\"" in SERVER_SRC[k:k + 400]


def test_unconnected_github_message_is_helpful():
    i = SERVER_SRC.find("def _gh(email, path")
    seg = SERVER_SRC[i:i + 1200]
    assert "Link GitHub card" in seg
    assert "paste your GitHub token here" in seg


# ---------------------------------------------------------------- presentation-clean UI

def test_no_blocking_alerts_in_client():
    assert "function toastMsg(" in INDEX_SRC
    calls = re.findall(r"(?<![\w.])alert\(", INDEX_SRC)
    assert calls == []                       # every alert() became a toast


def test_prompt_never_leaks_patch_markers_in_new_blocks():
    for needle in ("VISUALIZATION (REAL CHARTS)", "CERTIFICATES & QR CODES"):
        i = SERVER_SRC.find(needle)
        assert i > 0 and "patch99" not in SERVER_SRC[i:i + 400]


# ---------------------------------------------------------------- marker

def test_marker():
    assert SERVER_SRC.count("patch102-wa-bot") == 2
