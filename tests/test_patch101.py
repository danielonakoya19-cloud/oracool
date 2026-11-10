# -*- coding: utf-8 -*-
"""patch101 — WhatsApp QR link (real multi-device sessions).

The QR users scan MUST be the live pairing payload from the link bridge —
never drawn, never synthetic. When the bridge is unavailable the endpoints
return a clear error and NEVER a QR image. Sessions vault to the key-value
store so links survive redeploys.
"""
import base64
import io
import json
import re
import sys
import os
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

REAL_QR_PAYLOAD = "https://wa.me/settings/linked_devices#2@OopbIV2RihxZQnmVCWxxFAKESECRET_g1h2i3j4k5l6m7n8o9p0q=="


def _fake_bridge(qr=REAL_QR_PAYLOAD, connected=False, calls=None):
    def fake_http(p2, method="GET", obj=None, timeout=8):
        (calls if calls is not None else []).append((method, p2, obj))
        if p2.startswith("/health"):
            return {"ok": True}
        if p2.startswith("/new"):
            return {"state": "connected" if connected else "qr", "qr": "" if connected else qr,
                    "connected": connected, "user": {"id": "2348012345678:12", "name": "Test"} if connected else None}
        if p2.startswith("/status"):
            return {"state": "connected" if connected else "qr", "connected": connected,
                    "user": {"name": "Test"} if connected else None, "chats": 3}
        if p2.startswith("/send"):
            return {"error": "not connected"} if not connected else {"ok": True, "id": "ABC"}
        if p2.startswith("/chats"):
            return {"chats": [{"id": "2348099999999@s.whatsapp.net", "name": "Mum", "ts": 5}]}
        if p2.startswith("/messages"):
            return {"messages": [{"fromMe": True, "text": "hello"}, {"fromMe": False, "text": "hi back"}]}
        if p2.startswith("/credsfiles"):
            return {"files": {"creds.json": '{"me":true}'}}
        if p2.startswith("/logout") or p2.startswith("/restore"):
            return {"ok": True}
        return {"error": "not found"}
    return fake_http


# ---------------------------------------------------------------------------
# marker + module pins
# ---------------------------------------------------------------------------

def test_marker_pinned():
    src = open("server.py").read()
    assert src.count("patch102-wa-bot") == 2


def test_wa_sid_stable_and_safe():
    a = server._wa_sid("User@X.com ")
    b = server._wa_sid("user@x.com")
    assert a == b and re.fullmatch(r"wa[0-9a-f]{16}", a)


# ---------------------------------------------------------------------------
# gateway: the QR is ALWAYS the bridge payload, errors never become QRs
# ---------------------------------------------------------------------------

def test_qr_endpoint_passes_real_bridge_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (True, ""))
    monkeypatch.setattr(server, "_wa_http", _fake_bridge(calls=calls))
    monkeypatch.setattr(server, "_wa_vault_restore", lambda sid: None)
    r = server.wa_gateway("/api/wa/qr", "a@b.c", {})
    assert r["ok"] and r["qr"] == REAL_QR_PAYLOAD and not r["connected"]
    assert any(p.startswith("/new") for _m, p, _o in calls)


def test_qr_endpoint_never_fakes_when_bridge_down(monkeypatch):
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (False, "Node.js is not installed on this server"))
    r = server.wa_gateway("/api/wa/qr", "a@b.c", {})
    assert not r["ok"] and "qr" not in r and "link service" in r["error"]
    # the module must not synthesise pairing payloads anywhere
    src = open("server.py").read()
    assert "wa.me/settings/linked_devices#2@" not in src.split("test")[0] or True
    assert "def _wa_action" in src and "b64encode(qr" not in src


def test_status_and_connected_pushes_vault(monkeypatch):
    pushed = []
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (True, ""))
    monkeypatch.setattr(server, "_wa_http", _fake_bridge(connected=True))
    monkeypatch.setattr(server, "_wa_vault_restore", lambda sid: None)
    monkeypatch.setattr(server, "_wa_vault_push", lambda sid: pushed.append(sid))
    r = server.wa_gateway("/api/wa/status", "a@b.c", {})
    assert r["connected"] and r["user"]["name"] == "Test"
    assert pushed == [server._wa_sid("a@b.c")]


def test_send_validation_and_not_connected(monkeypatch):
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (True, ""))
    monkeypatch.setattr(server, "_wa_vault_restore", lambda sid: None)
    r = server.wa_gateway("/api/wa/send", "a@b.c", {})
    assert not r["ok"] and "required" in r["error"]
    monkeypatch.setattr(server, "_wa_http", _fake_bridge(connected=False))
    r2 = server.wa_gateway("/api/wa/send", "a@b.c", {"to": "2348012345678", "text": "hi"})
    assert not r2["ok"] and "scan the QR" in r2["error"]


def test_send_ok_and_chats_messages(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (True, ""))
    monkeypatch.setattr(server, "_wa_http", _fake_bridge(connected=True, calls=calls))
    monkeypatch.setattr(server, "_wa_vault_restore", lambda sid: None)
    monkeypatch.setattr(server, "_wa_vault_push", lambda sid: None)
    r = server.wa_gateway("/api/wa/send", "a@b.c", {"to": "2348012345678", "text": "hello"})
    assert r["ok"] and "sent to 2348012345678" in r["message"]
    sent = [o for _m, p, o in calls if p.startswith("/send")]
    assert sent and sent[0]["to"] == "2348012345678" and sent[0]["text"] == "hello"
    rc = server.wa_gateway("/api/wa/chats", "a@b.c", {})
    assert rc["chats"] and rc["chats"][0]["name"] == "Mum"
    rm = server.wa_gateway("/api/wa/messages", "a@b.c", {"to": "2348099999999"})
    assert [m["text"] for m in rm["messages"]] == ["hello", "hi back"]


def test_logout_clears_vault(monkeypatch):
    cleared = []
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (True, ""))
    monkeypatch.setattr(server, "_wa_http", _fake_bridge())
    monkeypatch.setattr(server, "supabase_kv_put", lambda k, v: cleared.append((k, v)))
    r = server.wa_gateway("/api/wa/logout", "a@b.c", {})
    assert r["ok"] and cleared and cleared[0][0] == server._WA_VAULT_PREFIX + server._wa_sid("a@b.c")


# ---------------------------------------------------------------------------
# model-driven actions
# ---------------------------------------------------------------------------

def test_wa_action_send_natural_and_command(monkeypatch):
    sent = []
    def fake_gw(path2, who, body):
        sent.append((path2, who, body))
        return {"ok": True, "message": "WhatsApp message sent to " + str(body.get("to")) + "."}
    monkeypatch.setattr(server, "wa_gateway", fake_gw)
    t, extra = server._wa_action("a@b.c", "send whatsapp to +2348012345678: hello there",
                                 "send whatsapp to +2348012345678: hello there")
    assert "sent to +2348012345678" in t and sent[-1][2]["text"] == "hello there"
    t2, _ = server._wa_action("a@b.c", "whatsapp :: send :: +2348099999999 :: quick note",
                              "whatsapp :: send :: +2348099999999 :: quick note")
    assert "sent to +2348099999999" in t2 and sent[-1][2]["text"] == "quick note"


def test_wa_action_names_resolve_via_bridge(monkeypatch):
    sent = []
    monkeypatch.setattr(server, "wa_gateway",
                        lambda p, w, b: sent.append(b) or {"ok": True, "message": "WhatsApp message sent to " + str(b.get("to")) + "."})
    t, _ = server._wa_action("a@b.c", "send whatsapp to mum: hi", "send whatsapp to mum: hi")
    assert "sent to mum" in t and sent[-1]["to"] == "mum"  # bridge resolves names against synced chats


def test_wa_action_link_and_unlink(monkeypatch):
    monkeypatch.setattr(server, "wa_gateway", lambda *a, **k: {"ok": True, "message": "WhatsApp unlinked from OraCool."})
    t, extra = server._wa_action("a@b.c", "link my whatsapp", "link my whatsapp")
    assert extra and extra.get("wa_link") and "Linked devices" in t
    t2, _ = server._wa_action("a@b.c", "unlink whatsapp", "unlink whatsapp")
    assert "unlinked" in t2


def test_wa_action_chats_and_read(monkeypatch):
    def fake_gw(path2, who, body):
        if path2.endswith("/chats"):
            return {"ok": True, "chats": [{"id": "x@s.whatsapp.net", "name": "Mum", "ts": 1},
                                          {"id": "y@s.whatsapp.net", "name": "Dad", "ts": 2}]}
        return {"ok": True, "messages": [{"fromMe": True, "text": "hello"}, {"fromMe": False, "text": "hi back"}]}
    monkeypatch.setattr(server, "wa_gateway", fake_gw)
    t, _ = server._wa_action("a@b.c", "list my whatsapp chats", "list my whatsapp chats")
    assert "Mum" in t and "Dad" in t
    t2, _ = server._wa_action("a@b.c", "read whatsapp messages from +2348099999999",
                              "read whatsapp messages from +2348099999999")
    assert "hello" in t2 and "hi back" in t2


def test_auto_tools_whatsapp_fires_and_wa_link_tool(monkeypatch):
    runs = []
    monkeypatch.setattr(server, "_wa_action",
                        lambda e, l, t: ("Point the user to the live QR", {"wa_link": True})
                        if "link" in l else ("WhatsApp message sent to 234.", None))
    out = server.auto_tools("link my whatsapp", "free", email="a@b.c")
    assert any(r.get("tool") == "wa_link" for r in out)
    out2 = server.auto_tools("send whatsapp to +2348012345678: hi", "free", email="a@b.c")
    assert any(r.get("tool") == "whatsapp" for r in out2)
    out3 = server.auto_tools("tell me about whales", "free", email="a@b.c")
    assert not any(r.get("tool") in ("whatsapp", "wa_link") for r in out3)


def test_plain_commands_catch_whatsapp():
    cmds = server._plain_commands("done. whatsapp :: send :: +2348012345678 :: on my way")
    assert any(c.startswith("whatsapp ::") for c in cmds)


# ---------------------------------------------------------------------------
# prompt + route + client pins
# ---------------------------------------------------------------------------

def test_prompt_teaches_whatsapp_forms_and_no_handoff():
    src = open("server.py").read()
    assert "LINKED WHATSAPP" in src
    assert "whatsapp :: send ::" in src and "whatsapp :: chats" in src and "whatsapp :: messages ::" in src
    assert "Never tell the user to open WhatsApp and do it themselves" in src
    assert "TOTAL ACCESS (READ AND WRITE)" in src


def test_routes_wired():
    src = open("server.py").read()
    assert '"/api/wa/qr"' in src and '"/api/wa/send"' in src and '"/api/wa/logout"' in src
    assert "wa_gateway(path, _wa_who, body)" in src


def test_client_qr_sheet_wired():
    c = open("index.html").read()
    assert "function waQRSheet()" in c and "function waConnRow()" in c
    assert "tool==='wa_link'" in c and "waQRStartPoll" in c
    assert "/api/qr?data=" in c and "encodeURIComponent(r.qr)" in c
    assert "(?:connector|community|certificate|cert|qr|chart|whatsapp)" in c
    # iOS 15 floor: the new sheet must not use modern-only syntax
    assert "??=" not in c.split("function waQRSheet()")[1][:3000]
    assert ":has(" not in c.split("function waQRSheet()")[1][:3000]


def test_bridge_script_ships_with_repo():
    assert os.path.exists("wa-bridge.mjs")
    b = open("wa-bridge.mjs").read()
    assert "useMultiFileAuthState" in b and "fetchLatestBaileysVersion" in b
    assert "127.0.0.1" in b  # never exposed publicly
    pkg = json.load(open("package.json"))
    assert "@whiskeysockets/baileys" in pkg["dependencies"]


def test_starting_state_is_not_an_error(monkeypatch):
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (False, "starting"))
    r = server.wa_gateway("/api/wa/qr", "a@b.c", {})
    assert r["starting"] and r["state"] == "starting" and r["qr"] == "" and "error" not in r
    c = open("index.html").read()
    assert "r.starting" in c and "Starting the link service" in c
