# -*- coding: utf-8 -*-
"""patch102 — WhatsApp auto-reply bot (user-armed only) + total access actions."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


def test_marker_pinned():
    src = open("server.py").read()
    assert src.count("patch105-wa-instant") == 2


# ---------------------------------------------------------------------------
# bot gateway
# ---------------------------------------------------------------------------

def test_bot_get_and_set(monkeypatch):
    calls = []
    def fake_http(p2, method="GET", obj=None, timeout=8):
        calls.append((method, p2, obj))
        if p2.startswith("/bot") and method == "POST":
            return {"ok": True, "bot": {"enabled": (obj or {}).get("enabled", False),
                                        "reply": (obj or {}).get("reply") or "",
                                        "rules": (obj or {}).get("rules") or []}}
        if p2.startswith("/bot"):
            return {"ok": True, "bot": {"enabled": True, "reply": "busy", "rules": [{"match": "price", "reply": "50"}]}}
        return {"ok": True}
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (True, ""))
    monkeypatch.setattr(server, "_wa_http", fake_http)
    monkeypatch.setattr(server, "_wa_vault_restore", lambda sid: None)
    r = server.wa_gateway("/api/wa/bot", "a@b.c", {})
    assert r["bot"]["enabled"] and r["bot"]["reply"] == "busy"
    r2 = server.wa_gateway("/api/wa/bot", "a@b.c", {"enabled": True, "reply": "in a meeting"})
    assert r2["ok"] and r2["bot"]["reply"] == "in a meeting"
    assert any(p.startswith("/bot") and m == "POST" for m, p, _o in calls)


def test_action_kinds_reach_bridge(monkeypatch):
    calls = []
    def fake_http(p2, method="GET", obj=None, timeout=8):
        calls.append((method, p2, obj))
        return {"ok": True, "message": "done"}
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (True, ""))
    monkeypatch.setattr(server, "_wa_http", fake_http)
    monkeypatch.setattr(server, "_wa_vault_restore", lambda sid: None)
    r = server.wa_gateway("/api/wa/action", "a@b.c", {"kind": "react", "to": "Mum", "emoji": "😂"})
    assert r["ok"]
    r2 = server.wa_gateway("/api/wa/action", "a@b.c", {"kind": "send", "to": "+2348012345678",
                                                       "image_url": "https://x/y.png", "text": "look"})
    assert r2["ok"]
    r3 = server.wa_gateway("/api/wa/action", "a@b.c", {})
    assert not r3["ok"]
    kinds = [(o or {}).get("kind") for _m, p, o in calls if p.startswith("/action")]
    assert kinds == ["react", "send"]
    media = [o for _m, p, o in calls if p.startswith("/action") and (o or {}).get("image_url")]
    assert media and media[0]["image_url"] == "https://x/y.png"


# ---------------------------------------------------------------------------
# _wa_action: the bot arms ONLY on user command
# ---------------------------------------------------------------------------

def test_bot_on_off_rule_status(monkeypatch):
    sent = []
    def fake_gw(path2, who, body):
        sent.append((path2, body))
        return {"ok": True, "bot": dict(body), "message": "ok"}
    monkeypatch.setattr(server, "wa_gateway", fake_gw)
    t, _ = server._wa_action("a@b.c", "turn on my whatsapp bot replying: I am in a meeting, I will reply later",
                             "turn on my whatsapp bot replying: I am in a meeting, I will reply later")
    assert "bot is ON" in t and sent[-1][1].get("enabled") is True
    assert "in a meeting" in sent[-1][1].get("reply", "")
    t2, _ = server._wa_action("a@b.c", "turn off my whatsapp bot", "turn off my whatsapp bot")
    assert "OFF" in t2 and sent[-1][1].get("enabled") is False
    t3, _ = server._wa_action("a@b.c",
                              "add whatsapp bot rule when they say price reply Our price list is on the site",
                              "add whatsapp bot rule when they say price reply Our price list is on the site")
    assert "rule saved" in t3 and sent[-1][1].get("rules")
    assert sent[-1][1]["rules"][0]["match"].lower() == "price"
    monkeypatch.setattr(server, "wa_gateway",
                        lambda p, w, b: {"ok": True, "bot": {"enabled": True, "reply": "busy", "rules": [{"match": "x", "reply": "y"}]}})
    t4, _ = server._wa_action("a@b.c", "is my whatsapp bot on?", "is my whatsapp bot on?")
    assert "ON" in t4 and "rule" in t4


def test_bot_never_self_arms_without_command(monkeypatch):
    monkeypatch.setattr(server, "wa_gateway",
                        lambda p, w, b: (_ for _ in ()).throw(AssertionError("must not call gateway")))
    t, extra = server._wa_action("a@b.c", "what can you do with my whatsapp?", "what can you do with my whatsapp?")
    assert extra is None or not (extra or {}).get("wa_link")
    # no bot call happened: _wa_action must bail to hint or None without arming
    assert t is None or "bot" in t.lower()


# ---------------------------------------------------------------------------
# total access actions parsing
# ---------------------------------------------------------------------------

def test_send_parses_whatsapp_first_and_reply_verb(monkeypatch):
    sent = []
    monkeypatch.setattr(server, "wa_gateway",
                        lambda p, w, b: sent.append(b) or {"ok": True, "message": "WhatsApp message sent to " + str(b.get("to")) + "."})
    t, _ = server._wa_action("a@b.c", "whatsapp +2348012345678 saying on my way", "whatsapp +2348012345678 saying on my way")
    assert "sent to" in t and sent[-1]["text"] == "on my way"
    t2, _ = server._wa_action("a@b.c", "reply to Mum on whatsapp: I will call you", "reply to Mum on whatsapp: I will call you")
    assert "sent to" in t2 and sent[-1]["to"] == "Mum"
    t3, _ = server._wa_action("a@b.c", "send whatsapp image to +2348012345678: https://x.site/pic.png",
                              "send whatsapp image to +2348012345678: https://x.site/pic.png")
    assert "media sent" in t3 or "sent" in t3
    assert sent[-1].get("kind") == "send" and sent[-1].get("image_url") == "https://x.site/pic.png"


def test_react_delete_mute_archive_markread_about(monkeypatch):
    sent = []
    monkeypatch.setattr(server, "wa_gateway",
                        lambda p, w, b: sent.append(dict(b)) or {"ok": True, "message": str(b.get("kind")) + " ok"})
    server._wa_action("a@b.c", "react to my whatsapp chat with +2348012345678 with 😂",
                      "react to my whatsapp chat with +2348012345678 with 😂")
    assert sent[-1]["kind"] == "react" and sent[-1]["emoji"] == "😂"
    server._wa_action("a@b.c", "delete my last whatsapp message to +2348012345678",
                      "delete my last whatsapp message to +2348012345678")
    assert sent[-1]["kind"] == "delete"
    server._wa_action("a@b.c", "mute my whatsapp chat with +2348012345678", "mute my whatsapp chat with +2348012345678")
    assert sent[-1]["kind"] == "mute"
    server._wa_action("a@b.c", "archive my whatsapp chat with +2348012345678", "archive my whatsapp chat with +2348012345678")
    assert sent[-1]["kind"] == "archive"
    server._wa_action("a@b.c", "mark my whatsapp chat with +2348012345678 read", "mark my whatsapp chat with +2348012345678 read")
    assert sent[-1]["kind"] == "markread"
    server._wa_action("a@b.c", "set my whatsapp about to Building the future",
                      "set my whatsapp about to Building the future")
    assert sent[-1]["kind"] == "about" and "Building the future" in sent[-1]["text"]


def test_read_still_works_with_names(monkeypatch):
    monkeypatch.setattr(server, "wa_gateway",
                        lambda p, w, b: {"ok": True, "messages": [{"fromMe": False, "text": "hello"}]})
    t, _ = server._wa_action("a@b.c", "read my whatsapp chat with Mum", "read my whatsapp chat with Mum")
    assert "hello" in t


# ---------------------------------------------------------------------------
# pins: prompt, routes, client, bridge
# ---------------------------------------------------------------------------

def test_prompt_covers_bot_and_total_access():
    src = open("server.py").read()
    assert "ONLY when the user asks" in src or "only when the user asks" in src.lower()
    assert "turn on my whatsapp bot replying:" in src
    assert "add whatsapp bot rule" in src
    assert "The bot never arms itself" in src
    assert "react" in src and "mute" in src and "archive" in src and "mark-read" in src


def test_routes_and_client_pins():
    src = open("server.py").read()
    assert '"/api/wa/bot"' in src and '"/api/wa/action"' in src
    c = open("index.html").read()
    # the bot panel is CHAT-ONLY by user instruction: no bot controls in the QR sheet
    assert "waBotBox" not in c and "waBotToggle" not in c and "waBotLoad" not in c
    assert "function waQRSheet()" in c and "waQRStartPoll" in c


def test_prebuilt_bridge_bundle_ships_and_is_preferred():
    assert os.path.exists("wa-bridge.bundle.mjs")
    b = open("wa-bridge.bundle.mjs").read()
    assert "linked_devices" in b  # real pairing flow inside the bundle
    src = open("server.py").read()
    assert "wa-bridge.bundle.mjs" in src and "exec node wa-bridge.bundle.mjs" in src
    assert "if [ ! -d node_modules ]" in src  # fallback kept for missing-bundle installs


def test_bridge_ships_bot_and_actions():
    b = open("wa-bridge.mjs").read()
    assert "maybeBotReply" in b and "bot.json" in b and "loadBot" in b
    assert "doAction" in b and "chatModify" in b and "updateProfileStatus" in b
    assert "toJid" in b  # name resolution against synced chats
    assert "sendPresenceUpdate('composing'" in b  # human-ish bot typing


def test_bot_update_never_sends_json_nulls(monkeypatch):
    seen = []
    def fake_http(p2, method="GET", obj=None, timeout=8):
        seen.append(obj)
        return {"ok": True, "bot": {}}
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (True, ""))
    monkeypatch.setattr(server, "_wa_http", fake_http)
    monkeypatch.setattr(server, "_wa_vault_restore", lambda sid: None)
    server.wa_gateway("/api/wa/bot", "a@b.c", {"enabled": True})
    server.wa_gateway("/api/wa/action", "a@b.c", {"kind": "react", "emoji": "x"})
    for obj in seen:
        if obj:
            assert all(v is not None for v in obj.values()), obj


def test_status_reopens_vaulted_session(monkeypatch):
    calls = []
    def fake_http(p2, method="GET", obj=None, timeout=8):
        calls.append(p2)
        if p2.startswith("/status"):
            return {"state": "none", "connected": False}
        if p2.startswith("/new"):
            return {"state": "connected", "qr": "", "connected": True, "user": {"name": "T"}}
        return {"ok": True}
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (True, ""))
    monkeypatch.setattr(server, "_wa_http", fake_http)
    monkeypatch.setattr(server, "_wa_vault_restore", lambda sid: None)
    monkeypatch.setattr(server, "_wa_vault_push", lambda sid: None)
    r = server.wa_gateway("/api/wa/status", "a@b.c", {})
    assert r["connected"] and any(p.startswith("/new") for p in calls)
    b = open("wa-bridge.mjs").read()
    assert "creds.json'))) await openSession" in b.replace('"', "'").replace("`", "'") or "openSession(id) } catch" in b


# ---------------------------------------------------------------------------
# "check my whatsapp" + command forms must ALWAYS produce real content
# ---------------------------------------------------------------------------

def _fake_wa_gateway(path2, who, body):
    if path2.endswith("/status"):
        return {"ok": True, "connected": True, "user": {"name": "T"}, "chats": 3}
    if path2.endswith("/bot"):
        return {"ok": True, "bot": {"enabled": True, "reply": "busy", "rules": []}}
    if path2.endswith("/chats"):
        return {"ok": True, "chats": [{"id": "x@g.us", "name": "Family Group", "ts": 1},
                                      {"id": "2@s.whatsapp.net", "name": "Mum", "ts": 2}]}
    if path2.endswith("/messages"):
        return {"ok": True, "messages": [{"fromMe": False, "text": "hello there"}]}
    return {"ok": True, "message": "ok"}


def test_check_my_whatsapp_returns_real_summary(monkeypatch):
    monkeypatch.setattr(server, "wa_gateway", _fake_wa_gateway)
    t, _ = server._wa_action("a@b.c", "check my whatsapp", "check my whatsapp")
    assert "linked" in t and "Family Group" in t and "Mum" in t and "bot" in t.lower()


def test_check_unlinked_offers_qr(monkeypatch):
    monkeypatch.setattr(server, "wa_gateway",
                        lambda p, w, b: {"ok": True, "connected": False})
    t, extra = server._wa_action("a@b.c", "check my whatsapp", "check my whatsapp")
    assert "not linked" in t and extra and extra.get("wa_link")


def test_check_chat_with_name_reads_messages_not_list(monkeypatch):
    monkeypatch.setattr(server, "wa_gateway", _fake_wa_gateway)
    t, _ = server._wa_action("a@b.c", "check my whatsapp chat with Mum", "check my whatsapp chat with Mum")
    assert "hello there" in t and "Recent WhatsApp chats" not in t


def test_command_forms_chats_and_messages(monkeypatch):
    monkeypatch.setattr(server, "wa_gateway", _fake_wa_gateway)
    t, _ = server._wa_action("a@b.c", "whatsapp :: chats", "whatsapp :: chats")
    assert "Family Group" in t and "Mum" in t
    t2, _ = server._wa_action("a@b.c", "whatsapp :: messages :: Mum", "whatsapp :: messages :: Mum")
    assert "hello there" in t2


def test_placeholder_scrub_and_junk_detection():
    assert server._reply_is_junk("(ran WhatsApp connector tool)")
    assert server._reply_is_junk("ORA-COOL - PHONE WHATSAPP + JARVIS\n\n(ran WhatsApp connector tool)")
    assert not server._reply_is_junk("Your WhatsApp is linked. Recent chats: Family Group.")
    out = server._scrub_tool_placeholders("Done.\n(ran whatsapp connector tool)\n**Live results gathered for you:**")
    assert "ran" not in out and "Live results" in out


def test_summary_says_reconnecting_not_unlinked(monkeypatch):
    monkeypatch.setattr(server, "wa_gateway",
                        lambda p, w, b: {"ok": True, "connected": False, "state": "connecting"})
    t, extra = server._wa_action("a@b.c", "check my whatsapp", "check my whatsapp")
    assert "reconnecting" in t and "still linked" in t and extra is None


def test_json_tool_calls_become_real_commands():
    raw = 'Let me check.\n```json\n{"function": "whatsapp", "tool_input": {"action": "check"}}\n```'
    cmds = server._json_tool_commands(raw)
    assert cmds == ["whatsapp :: check"]
    raw2 = '{"function": "whatsapp", "tool_input": {"action": "send", "to": "+2348012345678", "text": "hi"}}'
    assert server._json_tool_commands(raw2) == ["whatsapp :: send :: +2348012345678 :: hi"]
    raw3 = '{"function": "github", "tool_input": {"action": "list repos"}}'
    assert server._json_tool_commands(raw3)[0].startswith("connector :: github ::")


def test_json_tool_blocks_scrubbed_from_replies():
    raw = 'Checking now.\n```json\n{"function": "whatsapp", "tool_input": {"action": "check"}}\n```\n'
    out = server._scrub_tool_placeholders(raw)
    assert "function" not in out and "Checking now" in out
    only = server._scrub_tool_placeholders('{"function": "whatsapp", "tool_input": {"action": "check"}}')
    assert server._reply_is_junk(only) or not only
    c = open("index.html").read()
    assert '"function"' in c.split("function _stripCmdLines")[1][:900]


# ---------------------------------------------------------------------------
# real contacts + named chats (user: "numbers and chat doesnt relate with my
# whatsapp contacts and chat")
# ---------------------------------------------------------------------------

def test_contacts_endpoint_and_intent(monkeypatch):
    calls = []
    def fake_http(p2, method="GET", obj=None, timeout=8):
        calls.append(p2)
        if p2.startswith("/contacts"):
            return {"contacts": [{"id": "2348012345678@s.whatsapp.net", "name": "John Doe"},
                                 {"id": "2348099999999@s.whatsapp.net", "name": "Mum"}]}
        return {"ok": True}
    monkeypatch.setattr(server, "_wa_ensure_bridge", lambda: (True, ""))
    monkeypatch.setattr(server, "_wa_http", fake_http)
    monkeypatch.setattr(server, "_wa_vault_restore", lambda sid: None)
    r = server.wa_gateway("/api/wa/contacts", "a@b.c", {})
    assert r["contacts"][0]["name"] == "John Doe"
    monkeypatch.setattr(server, "wa_gateway",
                        lambda p, w, b: {"ok": True, "contacts": [{"id": "2348012345678@s.whatsapp.net", "name": "John Doe"}]})
    t, _ = server._wa_action("a@b.c", "list my whatsapp contacts", "list my whatsapp contacts")
    assert "John Doe" in t
    t2, _ = server._wa_action("a@b.c", "find John in my whatsapp contacts", "find John in my whatsapp contacts")
    assert "John Doe" in t2 and "+2348012345678" in t2


def test_chat_labels_show_names_numbers_and_tags(monkeypatch):
    labels = {}
    def fake_gw(path2, who, body):
        return {"ok": True, "chats": [
            {"id": "2348012345678@s.whatsapp.net", "name": "John Doe", "ts": 3},
            {"id": "120363411665436262@g.us", "name": "", "ts": 2},
            {"id": "120363407446052054@newsletter", "name": "", "ts": 1}]}
    monkeypatch.setattr(server, "wa_gateway", fake_gw)
    import re as _re
    t, _ = server._wa_action("a@b.c", "list my whatsapp chats", "list my whatsapp chats")
    assert "John Doe" in t and "[group" in t and "[channel]" in t
    # unnamed p2p shows as +number, never a raw jid
    monkeypatch.setattr(server, "wa_gateway",
                        lambda p, w, b: {"ok": True, "chats": [{"id": "2348099999999@s.whatsapp.net", "name": "", "ts": 1}]})
    t2, _ = server._wa_action("a@b.c", "list my whatsapp chats", "list my whatsapp chats")
    assert "+2348099999999" in t2 and "@s.whatsapp.net" not in t2


def test_bridge_syncs_full_history_and_contacts():
    b = open("wa-bridge.mjs").read()
    assert "syncFullHistory: true" in b
    assert "rec.contacts" in b and "pushName" in b
    assert "'/contacts'" in b or '"/contacts"' in b
    src = open("server.py").read()
    assert '"/api/wa/contacts"' in src


# ---------------------------------------------------------------------------
# whatsapp reliability: actions wait for reconnect, honest copy, phone-jid sends
# ---------------------------------------------------------------------------

def test_wa_actions_wait_for_connection_and_copy_is_honest(monkeypatch):
    src = open("server.py").read()
    assert "_wa_wait_connected" in src and "_WA_NEEDS_CONN" in src
    assert '"/api/wa/send"' in src[src.index("_WA_NEEDS_CONN = ("):src.index("_WA_NEEDS_CONN = (") + 220]
    assert "it stays linked, no QR needed" in src
    assert "NEVER tell them to scan a QR while a linked session exists" in src
    # waiter polls the bridge and stops early on qr/none
    calls = []
    def fake_http(p2, method="GET", obj=None, timeout=8):
        calls.append(p2)
        return {"state": "connecting", "connected": False}
    monkeypatch.setattr(server, "_wa_http", fake_http)
    st = server._wa_wait_connected("wa:a@b.c", budget=4)
    assert st == "connecting" and len(calls) >= 1


def test_bridge_contact_phone_jids_and_lid_preference():
    b = open("wa-bridge.mjs").read()
    assert "function recContact(rec, c)" in b
    assert "phoneNumber" in b  # phone jid mapped alongside @lid
    assert "@lid" in b and "hits.find((j) => !String(j).endsWith('@lid'))" in b
    assert "byName" in b  # /contacts dedupes preferring phone jid


def test_natural_whatsapp_asks_reach_the_wa_tool_and_cores_dont_hijack(monkeypatch):
    src = open("server.py").read()
    # the outer gate no longer requires the literal word "whatsapp"
    assert "_wa_natural" in src and "_wa_said" in src
    # core personas never steal a message that already ran a live tool
    assert "if last_user and not tool_runs and not re.search" in src
    seen = {}
    def fake_gw(path2, who, body):
        seen['to'] = body.get('to')
        return {"ok": True, "messages": [{"fromMe": False, "text": "hi", "ts": 1}]}
    monkeypatch.setattr(server, "wa_gateway", fake_gw)
    t, _ = server._wa_action("a@b.c", "read my last chat with resilience", "read my last chat with RESILIENCE")
    assert seen.get("to") == "RESILIENCE" and "hi" in t
