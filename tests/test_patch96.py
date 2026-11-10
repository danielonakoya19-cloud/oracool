"""patch96 — multi-page websites, expressive replies, no fake trade predictions."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_SRC = (ROOT / "server.py").read_text(encoding="utf-8")
INDEX_SRC = (ROOT / "index.html").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT))
import server  # noqa: E402


# ---------------------------------------------------------------- multi-page builder

def _mp_reply():
    idx = "<!DOCTYPE html><html><head><title>Home</title></head><body>" + ("x" * 400) + "</body></html>"
    about = "<!DOCTYPE html><html><head><title>About</title></head><body>" + ("y" * 400) + "</body></html>"
    return ("TEMPLATE: Noir Dining\nTITLE: Test Site\nBEGIN index.html\n" + idx +
            "\nEND\nBEGIN about.html\n" + about + "\nEND")


def test_parser_reads_multiple_pages():
    d = server._parse_builder_reply(_mp_reply())
    assert d and d["name"] == "Test Site"
    paths = [f["path"] for f in d["files"]]
    assert paths == ["index.html", "about.html"]


def test_parser_legacy_single_file_still_works():
    d = server._parse_builder_reply("TEMPLATE: X\nTITLE: Y\nBEGIN index.html\n<!DOCTYPE html><html><head></head>"
                                    + "<body>" + ("z" * 700) + "</body></html>\nEND")
    assert d and [f["path"] for f in d["files"]] == ["index.html"]
    d2 = server._parse_builder_reply("TEMPLATE: X\nTITLE: Y\n<!DOCTYPE html><html><head></head><body>"
                                     + ("z" * 700) + "</body></html>")
    assert d2 and d2["files"][0]["path"] == "index.html"


def test_builder_done_detects_truncated_multipage():
    full = _mp_reply()
    assert server._builder_done(full) is True
    cut = full[:full.find("BEGIN about.html") + 80]
    assert server._builder_done(cut) is False
    assert server._builder_done("") is False


def test_nav_issues_flag_missing_pages():
    files = [{"path": "index.html", "content": '<a href="about.html">A</a><a href="menu.html">M</a>'},
             {"path": "about.html", "content": '<a href="index.html">H</a>'}]
    issues = server._nav_issues(files)
    assert any("menu.html" in i for i in issues)
    ok = [{"path": "index.html", "content": '<a href="about.html">A</a>'},
          {"path": "about.html", "content": '<a href="index.html">H</a>'}]
    assert server._nav_issues(ok) == []


def test_builder_prompt_teaches_multipage():
    assert "ONE OR MORE FILES" in SERVER_SRC
    assert "MULTI-PAGE" in SERVER_SRC
    assert "never link a page you did not write" in SERVER_SRC
    assert "6,000 and 14,000" in SERVER_SRC
    assert "Per-page length contract" in SERVER_SRC


def test_design_kit_applies_to_every_page():
    seg = SERVER_SRC[SERVER_SRC.find('Design kit'):SERVER_SRC.find('Design kit') + 500]
    assert "_inject_design_kit(_f[\"content\"])" in seg


# ---------------------------------------------------------------- expressive replies

def test_server_prompt_asks_for_expression():
    assert "REPLY WITH EXPRESSION" in SERVER_SRC
    assert "Never sound flat, robotic" in SERVER_SRC


def test_client_prompt_asks_for_expression():
    assert "REPLY WITH EXPRESSION" in INDEX_SRC


# ---------------------------------------------------------------- no fake trade predictions

def test_server_prompt_forbids_invented_predictions():
    assert "TRADE PREDICTIONS" in SERVER_SRC
    assert "LIVE DATA ONLY" in SERVER_SRC
    assert "never invent, estimate or extrapolate" in SERVER_SRC
    assert "not financial advice" in SERVER_SRC


def test_client_prompt_forbids_invented_predictions():
    assert "LIVE NUMBERS ONLY" in INDEX_SRC
    assert "never invent or estimate a price" in INDEX_SRC
    assert "not financial advice" in INDEX_SRC


def test_new_prompt_text_has_no_internal_ids():
    for marker in ("REPLY WITH EXPRESSION", "TRADE PREDICTIONS"):
        i = SERVER_SRC.find(marker)
        assert i > 0
        assert not re.search(r"patch\d+", SERVER_SRC[i:i + 1400], re.I)
    i = INDEX_SRC.find("REPLY WITH EXPRESSION")
    assert not re.search(r"patch\d+", INDEX_SRC[i:i + 1400], re.I)


# ---------------------------------------------------------------- marker

def test_marker():
    assert SERVER_SRC.count("patch102-wa-bot") == 2
