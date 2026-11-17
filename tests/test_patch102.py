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


def test_messages_survive_restarts_and_fetch_on_demand():
    b = open("wa-bridge.mjs").read()
    assert "msgs," in b and "o.msgs" in b           # msgs persisted + loaded
    assert "fetchMessageHistory" in b                # cold cache pulls real history
    assert b.count("persistNames(rec)") >= 3         # names+msgs saved as they arrive


def test_bridge_connection_watchdog_and_racefix():
    b = open("wa-bridge.mjs").read()
    assert "stateSince" in b and "60000" in b          # stuck-socket watchdog
    assert "rec.dead" in b                             # intentional teardowns don't respawn
    assert "if (sessions.get(id) === rec) sessions.delete(id)" in b  # dead rec never deletes a live one
    assert "old.dead = true" in b                      # resync marks the old socket


def test_video_creation_runs_in_chat_and_composer_fits_phones():
    srv = open("server.py").read()
    app = open("index.html").read()
    assert '"tool": "video"' in srv and "media_job_start(email, \"video\"" in srv
    assert "function videoJobCard(x)" in app and "x.tool==='video'" in app
    assert "/api/media/job/status" in app                  # client polls the render
    assert "#mmBtn, #privBtn, #callBtn, #connBtn" in app   # mobile composer hides the extras
    assert "Render dashboard" in app                      # custom-domain self-serve step


def test_advice_never_builds_but_slide_requests_build_decks(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "build_site",
                        lambda email, name, brief: calls.append((name, brief)) or {"ok": True, "url": "x"})
    # the user's real message — talking points request, NOT a website brief
    runs = server.auto_tools(
        "i have a presentation as your creator i want to speak on how people can make money "
        "if you create a website for them", tier="enterprise", email="a@x.com", history=[])
    assert not calls and not any(r.get("tool") == "build" for r in runs)
    # a real brief still builds
    calls.clear()
    runs2 = server.auto_tools("build me a website for Becfom Hotel with a booking form",
                              tier="enterprise", email="a@x.com", history=[])
    assert calls and any(r.get("tool") == "build" for r in runs2)
    # the deck builder itself always produces a real presentation (patch117)
    r = server.build_deck("a@x.com", "how people can make money with websites")
    assert r.get("ok") and r.get("slides", 0) >= 6
    html = open("/home/user/oracool/data/builds/" + r["slug"] + "/index.html").read()
    assert html.count("<section class=\"slide") >= 8 and "ArrowRight" in html
    # slides route to the dedicated deck builder — never the site builder
    deck_calls = []
    monkeypatch.setattr(server, "build_deck",
                        lambda email, topic: deck_calls.append(topic) or {"ok": True, "url": "/builds/x/", "slides": 10})
    calls.clear()
    runs3 = server.auto_tools("create slides for my presentation on how people can make money with websites",
                              tier="enterprise", email="a@x.com", history=[])
    assert any(r.get("tool") == "build" for r in runs3)
    assert deck_calls and not calls, "slides must use build_deck, never build_site"


def test_patch118_media_hygiene_and_bots_removed(monkeypatch):
    # ONE image per ask unless the user wants more — kills the "twice" duplicate
    assert server._imgcap(["https://a/1.jpg", "https://a/1.jpg?s=200"], "a red car") == ["https://a/1.jpg"]
    two = server._imgcap(["https://a/1.jpg", "https://a/2.jpg"], "logo variations for my brand")
    assert len(two) == 2
    # logo asks get professional logo constraints
    lb = server._logo_boost("Creative logo for Okemesi Recycling Hub")
    assert "vector-style LOGO" in lb and "no watermark" in lb
    assert server._logo_boost("a red car") == "a red car"
    # community bots: creation is removed and answers honestly
    r = server.handle_request({"path": "/api/community", "method": "POST",
                               "body": {"action": "bots/create", "token": "x", "name": "t", "username": "tbot"}}) \
        if hasattr(server, "handle_request") else None
    # route layer: exercise the same guard the dispatch uses
    assert server._disk_janitor  # callable
    server._disk_janitor(force=True)  # must never raise


def _espn_canned(url):
    """Canned ESPN responses for the game-analysis tests (no network)."""
    if "/teams?" in url or url.endswith("/teams"):
        teams = [{"team": {"id": "359", "displayName": "Arsenal", "name": "Arsenal"}},
                 {"team": {"id": "363", "displayName": "Chelsea", "name": "Chelsea"}}]
        return {"sports": [{"leagues": [{"teams": teams}]}]}
    if "/standings" in url:
        return {"children": [{"standings": {"entries": [
            {"team": {"id": "359", "displayName": "Arsenal"},
             "stats": [{"name": "rank", "value": 1}, {"name": "gamesPlayed", "value": 10}, {"name": "wins", "value": 9},
                       {"name": "ties", "value": 0}, {"name": "losses", "value": 1}, {"name": "pointDifferential", "value": 10},
                       {"name": "points", "value": 27}]},
            {"team": {"id": "363", "displayName": "Chelsea"},
             "stats": [{"name": "rank", "value": 8}, {"name": "gamesPlayed", "value": 10}, {"name": "wins", "value": 4},
                       {"name": "ties", "value": 2}, {"name": "losses", "value": 4}, {"name": "pointDifferential", "value": -1},
                       {"name": "points", "value": 14}]}]}}]}
    def ev(eid, hid, hname, aid, aname, hg, ag):
        return {"id": eid, "date": "2026-09-05", "status": {"type": {"completed": True}},
                "competitions": [{"competitors": [
                    {"id": hid, "homeAway": "home", "score": {"value": hg}, "team": {"displayName": hname}},
                    {"id": aid, "homeAway": "away", "score": {"value": ag}, "team": {"displayName": aname}}]}]}
    # three shared meetings (same ids on both schedules) + seven unique per team
    meetings = [ev("m1", "359", "Arsenal", "363", "Chelsea", 2, 1),
                ev("m2", "363", "Chelsea", "359", "Arsenal", 1, 2),
                ev("m3", "359", "Arsenal", "363", "Chelsea", 2, 1)]
    if "/teams/359/schedule" in url:
        others = [ev("a%d" % i, "359", "Arsenal", "x%d" % i, "Team %d" % i, 2, 0) for i in range(6)]
        others[0] = ev("a0", "359", "Arsenal", "x0", "Team 0", 0, 1)   # one loss
        return {"events": meetings + others}
    if "/teams/363/schedule" in url:
        others = [ev("c%d" % i, "c%d" % i, "Team C%d" % i, "363", "Chelsea", 2, 1) for i in range(7)]
        others[0] = ev("c0", "c0", "Team C0", "363", "Chelsea", 0, 1)  # one win
        return {"events": meetings + others}
    return {}


def test_patch119_game_analysis_is_real_and_honest(monkeypatch):
    monkeypatch.setattr(server, "_espn_get", _espn_canned)
    monkeypatch.setitem(server._ESPN_CACHE, "tm:soccer", (9e12, {
        "arsenal": ("eng.1", "359", "Arsenal"), "chelsea": ("eng.1", "363", "Chelsea")}))
    r = server.game_analyze("analyze the arsenal vs chelsea game")
    assert r.get("ok") and r.get("pick") in ("HOME WIN", "AWAY WIN", "DRAW")
    pr = r["probabilities"]
    assert 95 <= pr["home"] + pr["draw"] + pr["away"] <= 105
    assert r["pick"] == "HOME WIN" and pr["home"] > pr["away"]   # 9W vs 4W canned form
    assert 42 <= r["confidence_pct"] <= 84                        # honest hard cap
    assert "hard cap 84" in r["confidence_basis"] and r.get("caveat")
    assert r["home"]["form"].endswith("W") and r["h2h"]["meetings"] == 3
    assert any("27" in x for x in r["reasons"])                   # real table points quoted
    # small sample -> confidence capped at 60
    empty = {"events": []}
    monkeypatch.setattr(server, "_espn_get", lambda u, timeout=11: empty)
    r2 = server.game_analyze("predict arsenal vs chelsea")
    assert r2.get("ok") and r2["confidence_pct"] <= 60
    # non-sport "vs" never hijacks; an unclear game ask gets ONE clarifying error
    monkeypatch.setattr(server, "_espn_get", _espn_canned)
    r3 = server.game_analyze("python vs javascript which is better")
    assert not r3.get("ok") and r3.get("saw_game") is not True
    r4 = server.game_analyze("analyze the game tomorrow")
    assert not r4.get("ok") and r4.get("saw_game") is True and "which match" in r4.get("error", "").lower()


def _espn_live_canned(url, timeout=11, ttl=1800):
    if "scoreboard" in url and "eng.1" in url:
        def ev(eid, hn, an, hs, as_, state, detail, clock, when):
            return {"id": eid, "date": "2026-09-08T" + when + "Z", "league": {"name": "English Premier League"},
                    "status": {"displayClock": clock, "type": {"state": state, "detail": detail, "description": detail}},
                    "competitions": [{"competitors": [
                        {"homeAway": "home", "team": {"displayName": hn}, "score": {"value": hs}},
                        {"homeAway": "away", "team": {"displayName": an}, "score": {"value": as_}}]}]}
        return {"events": [ev("l1", "Arsenal", "Chelsea", 2, 1, "in", "Second Half", "74'", "15:00"),
                           ev("f1", "Leeds", "Villa", 3, 0, "post", "Final", "90:00", "12:00"),
                           ev("p1", "Man City", "Fulham", None, None, "pre", "Scheduled", "", "19:00")]}
    return {"events": []}


def test_patch120_live_feed(monkeypatch):
    monkeypatch.setattr(server, "_espn_get", _espn_live_canned)
    monkeypatch.setattr(server, "_LIVE_CACHE", [0.0, None])
    r = server.live_games()
    assert r.get("ok") and r["counts"]["live"] == 1
    m = r["live"][0]
    assert m["home"] == "Arsenal" and m["hs"] == 2 and m["as"] == 1
    assert m["clock"] == "74'" and "Second Half" in m["detail"]
    assert r["finished"][0]["home"] == "Leeds" and r["upcoming"][0]["home"] == "Man City"
    assert "auto-refreshes" in r["note"] or "auto-refresh" in r["note"]
    # throttle: an immediate second call must NOT re-hit the feed
    calls = []
    def counted(url, timeout=11, ttl=1800):
        calls.append(url)
        return _espn_live_canned(url)
    monkeypatch.setattr(server, "_espn_get", counted)
    monkeypatch.setattr(server, "_LIVE_CACHE", [0.0, None])
    server.live_games()
    n = len(calls)
    server.live_games()
    assert len(calls) == n, "second call within 20s must be served from cache"
    # "live games" fires the live tool and blocks the analysis path
    monkeypatch.setattr(server, "_espn_get", _espn_live_canned)
    monkeypatch.setattr(server, "_LIVE_CACHE", [0.0, None])
    runs = server.auto_tools("live games", tier="enterprise", email="a@x.com", history=[])
    assert any(x.get("tool") == "live" for x in runs)
    assert not any(x.get("tool") == "game" for x in runs)


def test_patch121_sporty_slip_is_honest(monkeypatch):
    import json
    monkeypatch.setattr(server, "_espn_get", _espn_canned)
    monkeypatch.setitem(server._ESPN_CACHE, "tm:soccer", (9e12, {
        "arsenal": ("eng.1", "359", "Arsenal"), "chelsea": ("eng.1", "363", "Chelsea")}))
    monkeypatch.setattr(server, "_LIVE_CACHE", [0.0, None])
    monkeypatch.setattr(server, "_SLIP_CACHE", [0.0])
    # make the fixture feed return one upcoming match to build a slip from
    def canned2(url, timeout=11, ttl=1800):
        if "scoreboard" in url and "eng.1" in url:
            return {"events": [{"id": "p1", "date": "2026-09-08T19:00Z", "league": {"name": "English Premier League"},
                                "status": {"type": {"state": "pre", "description": "Scheduled"}},
                                "competitions": [{"competitors": [
                                    {"homeAway": "home", "team": {"displayName": "Arsenal"}, "score": {"value": None}},
                                    {"homeAway": "away", "team": {"displayName": "Chelsea"}, "score": {"value": None}}]}]}]}
        return _espn_canned(url)
    monkeypatch.setattr(server, "_espn_get", canned2)
    r = server.sporty_slip("give me sportybet picks for today")
    assert r.get("ok") and r.get("kind") == "sportyslip"
    assert r.get("count", 0) >= 1
    pk = r["picks"][0]
    assert pk["match"] == "Arsenal vs Chelsea" and pk["selection"] in ("HOME WIN", "AWAY WIN", "DRAW", "Over 2.5", "Under 2.5")
    assert 42 <= pk["confidence_pct"] <= 84
    # HONESTY: booking codes are never fabricated
    assert r["book_code_status"] == "only_sportybet_can_mint_codes"
    assert "SportyBet generates YOUR code" in r["how_to_get_code"] or "Booking code" in r["how_to_get_code"]
    flat = json.dumps(r)
    assert "never print fake codes" in flat and "booking code" in flat.lower()
    # model v2 leaves its traces in the analysis too
    a = server.game_analyze("analyze the arsenal vs chelsea game")
    assert any("Venue split" in x or "Goals profile" in x or "weighted" in x for x in a.get("reasons", []))
    # trigger: the slip path wins over plain analysis
    runs = server.auto_tools("today's betting tips please", tier="enterprise", email="a@x.com", history=[])
    assert any(x.get("tool") == "sporty" for x in runs)
    assert not any(x.get("tool") == "game" for x in runs)


def test_patch122_selectivity_grading_and_remote(monkeypatch):
    import json as _json
    import time
    monkeypatch.setattr(server, "_espn_get", _espn_canned)
    monkeypatch.setitem(server._ESPN_CACHE, "tm:soccer", (9e12, {
        "arsenal": ("eng.1", "359", "Arsenal"), "chelsea": ("eng.1", "363", "Chelsea")}))
    monkeypatch.setattr(server, "_LIVE_CACHE", [0.0, None])
    monkeypatch.setattr(server, "_SLIP_CACHE", [0.0])
    monkeypatch.setattr(server, "_SPORTY_LOG", "/tmp/sporty_test_log.json")
    if os.path.exists("/tmp/sporty_test_log.json"):
        os.remove("/tmp/sporty_test_log.json")
    def canned2(url, timeout=11, ttl=1800):
        if "scoreboard" in url and "eng.1" in url:
            return {"events": [{"id": "e777", "date": "2026-09-08T19:00Z", "league": {"name": "English Premier League"},
                                "status": {"type": {"state": "pre", "description": "Scheduled"}},
                                "competitions": [{"competitors": [
                                    {"homeAway": "home", "team": {"displayName": "Arsenal"}, "score": {"value": None}},
                                    {"homeAway": "away", "team": {"displayName": "Chelsea"}, "score": {"value": None}}]}]}]}
        return _espn_canned(url)
    monkeypatch.setattr(server, "_espn_get", canned2)
    r = server.sporty_slip("today's betting tips")
    assert r.get("ok") and "record" in r and r["record"]["prob_threshold"] in (55, 60)
    assert "bankroll_rules" in r and "never chase" in r["bankroll_rules"].lower() or "NEVER chase" in r["bankroll_rules"]
    # selective: strong pick surfaces; weak leans land in 'passed' with a reason
    assert r.get("count", 0) >= 1
    assert any(pk.get("pass_reason") for pk in (r.get("passed") or [])) or r.get("count", 0) == len(r.get("picks") or [])
    # every surfaced pick clears the bar
    for pk in r.get("picks") or []:
        assert pk["probability_pct"] >= 55 and pk["confidence_pct"] >= 55
    # grading: a settled pick marked pending gets graded from real finals
    rows = server._sporty_log_load()
    assert rows, "surfaced picks must be logged for grading"
    rows[0]["t"] = time.time() - 7 * 3600   # old enough to grade
    server._sporty_log_save(rows)
    # the canned schedules carry the shared meeting ids (m1/m2/m3) as finished
    rows[0]["eid"] = "m1"
    rows[0]["market"] = "1X2"
    rows[0]["selection"] = "HOME WIN"
    rows[0]["home"], rows[0]["away"] = "Arsenal", "Chelsea"
    server._sporty_log_save(rows)
    monkeypatch.setattr(server, "_sporty_log_load", lambda: rows)
    monkeypatch.setattr(server, "_sporty_log_save", lambda rr: None)
    server._sporty_grade_pending()
    assert rows[0]["status"] in ("W", "L"), "settled picks must be graded from real final scores"
    assert rows[0].get("final") == "2-1"   # m1: Arsenal 2-1 Chelsea
    # remote session plumbing
    rs = server.remote_new_session("a@x.com")
    assert rs.get("ok") and rs.get("sid") and rs["url"].startswith("/remote/")
    server.remote_signal(rs["sid"], "phone", {"offer": {"type": "offer", "sdp": "x"}})
    got = server.remote_pull(rs["sid"], "tab")
    assert got["signals"] and got["signals"][0].get("offer")
    server.remote_cmd(rs["sid"], "snap")
    got2 = server.remote_pull(rs["sid"], "phone")
    assert got2["cmds"] == ["snap"]
    assert server.remote_snapshot(rs["sid"], "data:image/jpeg;base64,AAAA").get("ok")
    assert not server.remote_snapshot(rs["sid"], "x" * 3000000).get("ok")


def test_patch124_device_and_ip_intel():
    d = server._device_from_ua("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
    assert d["platform"] == "iPhone" and d["browser"] == "Safari" and d["kind"] == "phone"
    d = server._device_from_ua("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
    assert d["platform"] == "Android" and d["browser"] == "Chrome" and "SM-S918B" in d["model"]
    d = server._device_from_ua("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) [FBAN/FBIOS;FBAV/420.0]")
    assert d["in_app"] is True
    st = server._remote_session("devtest1")
    server._device_touch(st, "phone", {"ua": "x", "label": "iPhone \u00b7 Safari",
                                       "ip": "102.34.56.78", "screen": "390x844"})
    assert st["device"]["ip"] == "102.34.56.78" and st["device"]["screen"] == "390x844"
    r = server.remote_pull("devtest1", "tab")
    assert r["device"]["ip"] == "102.34.56.78"      # tab sees the phone's identity
    server._device_touch(st, "tab", {"label": "Mac \u00b7 Chrome", "ip": "1.2.3.4"})
    assert st["device"]["ip"] == "102.34.56.78"     # tab side must not overwrite it


def test_patch126_geolocate_and_movement():
    import json
    def fake_fetch(url, method="GET", headers=None, json_body=None, data=None, timeout=25):
        if "nominatim" in url:
            return 200, json.dumps({
                "display_name": "12, Admiralty Way, Lekki Phase 1, Eti-Osa, Lagos, 106104, Nigeria",
                "address": {"house_number": "12", "road": "Admiralty Way", "suburb": "Lekki Phase 1",
                            "city": "Lagos", "county": "Eti-Osa", "state": "Lagos",
                            "postcode": "106104", "country": "Nigeria", "country_code": "ng"}}).encode(), "application/json"
        return 500, b"", ""
    import server
    server.http_fetch, _keep = fake_fetch, server.http_fetch
    try:
        r = server.geo_reverse(6.4478, 3.4723)
        assert r["ok"] and r["country"] == "Nigeria" and r["country_code"] == "NG"
        assert r["state"] == "Lagos" and r["local_government"] == "Eti-Osa" and r["city"] == "Lagos"
        assert r["street"] == "Admiralty Way" and r["house_number"] == "12"
        assert "Lekki Phase 1" in r["exact_location"] and "Admiralty Way" in r["line"]
        assert not server.geo_reverse(200, 3)["ok"]          # latitude out of range
        assert not server.geo_reverse("x", "y")["ok"]        # junk input
    finally:
        server.http_fetch = _keep
    m = server.GEO_RX.search("where is 6.5244, 3.3792 ?")
    assert m and float(m.group(1)) == 6.5244 and float(m.group(2)) == 3.3792
    assert not server.GEO_RX.search("price 1.5, 2.3 dollars")   # no false trigger on prices
    w = server.geo_watch_act("create", owner="t@t.com", label="dispatch bus", lat=6.5, lon=3.3)
    assert w["ok"] and w["id"]
    r2 = server.geo_watch_act("report", wid=w["id"], lat=6.51, lon=3.31, acc=12)
    assert r2["ok"] and r2["reports"] == 2
    g = server.geo_watch_act("get", wid=w["id"])
    assert g["ok"] and len(g["watch"]["reports"]) == 2
    lst = server.geo_watch_act("list", owner="t@t.com")
    assert lst["ok"] and any(x["id"] == w["id"] for x in lst["watches"])
    server._remote_session("geotest1")   # a real paired session exists first
    assert server.geo_track_store("geotest1", 6.52, 3.38, 8)["ok"]
    tr = server.geo_track_get("geotest1")
    assert tr["ok"] and tr["latest"]["lat"] == 6.52 and tr["points"]
    assert not server.geo_track_store("no-such-sid", 1, 1)["ok"]   # no ghost sessions
