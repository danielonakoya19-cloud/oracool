"""patch98 — real QR codes (ISO 18004), certificates platform, visual-richness bar."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_SRC = (ROOT / "server.py").read_text(encoding="utf-8")
INDEX_SRC = (ROOT / "index.html").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT))
import server  # noqa: E402
import pytest  # noqa: E402


# ---------------------------------------------------------------- QR encoder

def test_qr_matches_reference_library():
    qrcode_lib = pytest.importorskip("qrcode")
    from qrcode.util import QRData, MODE_8BIT_BYTE

    def ref_forced(text, ecl, mask):
        q = qrcode_lib.QRCode(error_correction=getattr(qrcode_lib.constants, "ERROR_CORRECT_" + ecl), border=0)
        q.add_data(QRData(text.encode("utf-8"), mode=MODE_8BIT_BYTE))
        q.make(fit=True)
        q.makeImpl(test=False, mask_pattern=mask)
        return [[1 if c else 0 for c in row] for row in q.get_matrix()]

    for ecl in ("M", "L"):
        for t in ("A", "hello world", "https://oracoolai.com/cert/OC-12345678",
                  "x" * 107, "y" * 181, "data with spaces & symbols !@#$%^*()_+"):
            mine = server.qr_matrix(t, ecl)
            hit = any(ref_forced(t, ecl, k) == mine for k in range(8))
            assert hit, (ecl, t[:24])


def test_qr_png_and_svg_render():
    png = server.qr_png("https://oracoolai.com", scale=6)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    svg = server.qr_svg("https://oracoolai.com", px=200)
    assert svg.startswith("<svg") and "shape-rendering" in svg and 'd="M' in svg


def test_qr_scans_with_real_decoder():
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    det = cv2.QRCodeDetector()
    for t in ("https://oracoolai.com/cert/OC-7K2M9Q4X",
              "WIFI:T:WPA;S:OraCool;P:hello123;;",
              "Certificate of Achievement for Ada Lovelace"):
        arr = cv2.imdecode(np.frombuffer(server.qr_png(t, scale=8), np.uint8), cv2.IMREAD_GRAYSCALE)
        val, _pts, _ = det.detectAndDecode(arr)
        assert val == t, (t[:30], repr(val[:30]))


def test_qr_rejects_impossible_payload():
    with pytest.raises(ValueError):
        server.qr_matrix("z" * 5000)


def test_qr_route_exists():
    assert 'elif path == "/api/qr":' in SERVER_SRC
    assert "qr_png(_qd" in SERVER_SRC and "qr_svg(_qd" in SERVER_SRC
    assert "image/png" in SERVER_SRC and "image/svg+xml" in SERVER_SRC


# ---------------------------------------------------------------- certificates

@pytest.fixture()
def kv(monkeypatch):
    store = {}
    monkeypatch.setattr(server, "supabase_kv_get", lambda k: store.get(k))
    monkeypatch.setattr(server, "supabase_kv_put", lambda k, v: store.__setitem__(k, v))
    return store


def test_cert_issue_and_verify(kv):
    rec = server.cert_issue("owner@x.y", "Ada Lovelace", "Advanced Web Design",
                            "6-week studio course", "OraCool Academy", "with distinction")
    assert re.fullmatch(r"OC-[A-Z2-9]{8}", rec["id"])
    assert rec["status"] == "valid"
    assert rec["verify_url"] == "/cert/" + rec["id"]
    import urllib.parse as _up
    assert "oracoolai.com/cert/" in _up.unquote(rec["qr_url"]) and "fmt=png" in rec["qr_url"]
    v = server.cert_verify_public(rec["id"])
    assert v["ok"] is True and v["recipient"] == "Ada Lovelace"
    assert server.cert_verify_public("OC-NOPE1234")["status"] == "not_found"


def test_cert_revoke_owner_only(kv):
    rec = server.cert_issue("owner@x.y", "Bob", "First Aid")
    assert server.cert_revoke("other@x.y", rec["id"])["ok"] is False
    assert server.cert_revoke("owner@x.y", rec["id"])["ok"] is True
    v = server.cert_verify_public(rec["id"])
    assert v["ok"] is False and v["status"] == "revoked"


def test_cert_list(kv):
    rec = server.cert_issue("owner@x.y", "Cara", "Design 101")
    ids = [c["id"] for c in server.cert_list("owner@x.y")]
    assert rec["id"] in ids


def test_cert_page_html_is_real():
    rec = {"id": "OC-ABCD2345", "recipient": "Ada Lovelace", "title": "Advanced Web Design",
           "course": "studio course", "issuer": "OraCool Academy", "note": "",
           "issued_at": 1700000000, "status": "valid", "by": "o@x.y"}
    html = server.cert_page_html(rec)
    assert "Ada Lovelace" in html and "Advanced Web Design" in html
    assert "/api/qr?data=" in html and "Scan to verify" in html
    assert "window.print()" in html and "@media print" in html
    assert "Certificate of Achievement" in html
    assert "OraCool Academy" in html
    gone = server.cert_page_html(None)
    assert "not found" in gone.lower()
    rev = dict(rec, status="revoked")
    assert "revoked" in server.cert_page_html(rev).lower()


def test_cert_routes_exist():
    for r in ('"/api/certs/issue"', '"/api/certs/revoke"', '"/api/certs/list"',
              '"/api/certs/verify"', '"/cert/"'):
        assert r in SERVER_SRC
    assert "cert_issue(" in SERVER_SRC and "cert_page_html(cert_get(" in SERVER_SRC


# ---------------------------------------------------------------- AI tools

def test_auto_tools_qr_natural_and_command(kv):
    out = server.auto_tools("make a qr code for https://example.com/menu please", email="a@b.c")
    hits = [x for x in out if x.get("tool") == "qr"]
    assert hits and hits[0]["result"]["data"] == "https://example.com/menu"
    assert "/api/qr?data=" in hits[0]["result"]["qr_url"]
    out2 = server.auto_tools("qr :: hello world", email="a@b.c")
    assert any(x.get("tool") == "qr" and x["result"]["data"] == "hello world" for x in out2)


def test_auto_tools_qr_skips_when_nothing_to_encode(kv):
    out = server.auto_tools("what is a qr code?", email="a@b.c")
    assert not any(x.get("tool") == "qr" for x in out)


def test_auto_tools_cert_command_and_natural(kv):
    out = server.auto_tools("certificate :: Ada Lovelace :: Advanced Web Design :: studio :: OraCool Academy",
                            email="a@b.c")
    hits = [x for x in out if x.get("tool") == "cert"]
    assert hits and hits[0]["result"]["recipient"] == "Ada Lovelace"
    assert hits[0]["result"]["title"] == "Advanced Web Design"
    assert hits[0]["result"]["issuer"] == "OraCool Academy"
    assert re.fullmatch(r"OC-[A-Z2-9]{8}", hits[0]["result"]["id"])
    out2 = server.auto_tools("issue a certificate for Grace Hopper for Compilers 101", email="a@b.c")
    hits2 = [x for x in out2 if x.get("tool") == "cert"]
    assert hits2 and hits2[0]["result"]["recipient"] == "Grace Hopper"


def test_auto_tools_cert_needs_name_and_title(kv):
    out = server.auto_tools("what is a certificate for students", email="a@b.c")
    assert not any(x.get("tool") == "cert" for x in out)


def test_tool_summary_passes_qr_cert_fields():
    assert 'if t.get("tool") in ("qr", "cert"):' in SERVER_SRC
    for k in ('"qr_url"', '"verify_url"', '"recipient"'):
        assert k in SERVER_SRC


def test_chat_media_carries_qr_images():
    assert 'if d.get("qr_url"):' in SERVER_SRC
    i = SERVER_SRC.find('for t in tool_runs:\n            if t.get("tool") in ("qr", "cert"):')
    assert i > 0
    seg = SERVER_SRC[i:i + 500]
    assert '"kind": "image"' in seg


# ---------------------------------------------------------------- prompts + client

def test_server_prompt_teaches_real_certs_and_qr():
    assert "CERTIFICATES & QR CODES (REAL, NEVER DECORATIVE)" in SERVER_SRC
    assert "certificate :: <recipient full name>" in SERVER_SRC
    assert "qr :: <the exact content" in SERVER_SRC
    assert "never describe one as fake or decorative" in SERVER_SRC


def test_client_prompt_mirror():
    assert "Certificates and QR codes are real" in INDEX_SRC
    assert "qr :: <content>" in INDEX_SRC


def test_client_cards():
    assert "function qrCard(" in INDEX_SRC and "function certCard(" in INDEX_SRC
    assert "Download PNG" in INDEX_SRC
    assert "Open verification page" in INDEX_SRC
    assert "x.tool==='qr'" in INDEX_SRC and "x.tool==='cert'" in INDEX_SRC


# ---------------------------------------------------------------- visual richness

def test_quality_bar_checks_richness():
    for frag in ("almost no gradient work", "fewer than 2 inline SVGs",
                 "no frosted-glass surfaces", "fewer than 2 @keyframes animations",
                 "no testimonials or social-proof section", "no FAQ section"):
        assert frag in SERVER_SRC


def test_design_kit_has_component_library():
    assert ".ok-glass{" in SERVER_SRC and ".ok-grad-text{" in SERVER_SRC
    assert "@keyframes okFloat" in SERVER_SRC and "@keyframes okSheen" in SERVER_SRC
    assert ".ok-marquee" in SERVER_SRC and ".ok-btn-grad" in SERVER_SRC


def test_prompts_mandate_visual_richness_and_cert_platforms():
    assert "VISUAL RICHNESS MANDATE" in SERVER_SRC
    assert "CERTIFICATE PLATFORM BRIEFS" in SERVER_SRC
    assert "CERTIFICATE APPS (course/quiz/training platforms" in SERVER_SRC
    assert "oracoolai.com/api/qr?data=" in SERVER_SRC
    # model-facing text must never carry patch markers
    for needle in ("VISUAL RICHNESS MANDATE", "CERTIFICATE APPS"):
        i = SERVER_SRC.find(needle)
        assert "patch98" not in SERVER_SRC[i:i + 300]


# ---------------------------------------------------------------- marker

def test_marker():
    assert SERVER_SRC.count("patch98-qr-certs") == 2


def test_image_pipeline_never_draws_fake_qr(kv, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "gen_image", lambda *a, **k: calls.append(a) or {"images": []})
    out = server.auto_tools("make me a qr code for https://oracoolai.com/menu please", email="a@b.c")
    assert calls == []                                   # the image engine must never be asked
    assert not any(x.get("tool") == "image" for x in out)
    assert any(x.get("tool") == "qr" for x in out)
    assert "never generate, draw or attach an " in SERVER_SRC
    assert "AI-generated picture of a QR code" in SERVER_SRC
    assert "A QR code is NEVER an image-generation task" in SERVER_SRC
    assert "NEVER an image-generation task" in INDEX_SRC
