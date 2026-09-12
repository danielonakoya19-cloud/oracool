#!/usr/bin/env python3
"""
ORA-COOL AI — Pocket Option DEMO connector (v1, best-effort).

⚠️ IMPORTANT (read first):
- This trades Pocket Option's **DEMO (virtual $10,000) account ONLY**. No real money.
- It uses **your SSID** (session id) which you copy from your own logged-in
  Pocket Option browser session. OraCool cannot register accounts or extract an
  SSID for you — the SSID is created by Pocket Option in *your* browser when
  *you* log in, and automating sign-up/login violates their ToS.
- Binary options are extremely high-risk. This is provided for personal,
  educational, demo-mode use only. Never trade real money with it.
- Protocol is based on the open-source pocketoptionapi project; it MUST be
  validated against a real SSID (the sandbox has no live session to test with).

How to get your SSID (30 seconds):
  1. Log in to pocketoption.com in Chrome.
  2. Press F12 → Application tab → left panel → Session Storage → pocketoption.com
  3. Copy the value of the "ssid" key (a long alphanumeric string).
  4. Paste it into OraCool (Settings → Trading).
"""
import json
import time
import threading
import urllib.request
import urllib.parse
import ssl

try:
    import websocket  # websocket-client
except ImportError:
    websocket = None

PO_DEMO = "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket"
PO_SSID_MAX = 128


def check_ssid(ssid):
    ssid = (ssid or "").strip()
    if not ssid:
        return None, "No SSID provided. Paste your Pocket Option session id in Settings → Trading."
    if len(ssid) > PO_SSID_MAX or not ssid.replace("-", "").replace("_", "").isalnum():
        return None, "SSID looks invalid (should be a long alphanumeric session id)."
    return ssid, None


def _open(ssid, timeout=20):
    """Connect to Pocket Option demo socket and authenticate. Returns (ws, info_or_error)."""
    if websocket is None:
        return None, "websocket-client is not installed (pip install websocket-client)."
    try:
        ws = websocket.create_connection(
            PO_DEMO, timeout=timeout,
            header=["User-Agent: Mozilla/5.0"],
            sslopt={"cert_reqs": ssl.CERT_NONE},
            enable_multithread=False)
    except Exception as e:
        return None, f"Connection failed: {e}"

    try:
        # engine.io open packet: '0{...}'
        first = ws.recv()
        # socket.io connect to default namespace
        ws.send("40")
        ws.settimeout(timeout)
        # authenticate
        ws.send("42" + json.dumps(["auth", {"session_id": ssid, "is_demo": 1}]))
        deadline = time.time() + timeout
        messages = []
        authed = False
        while time.time() < deadline:
            try:
                m = ws.recv()
            except Exception:
                break
            if not m:
                continue
            messages.append(m[:300])
            low = m.lower()
            if "auth" in low and ("success" in low or "error" in low or "unauthorized" in low):
                authed = "success" in low and "error" not in low
                break
        return ws, {"authenticated": authed, "messages": messages}
    except Exception as e:
        try:
            ws.close()
        except Exception:
            pass
        return None, f"Auth handshake error: {e}"


def connect(ssid):
    ssid, err = check_ssid(ssid)
    if err:
        return {"error": err}
    ws, res = _open(ssid)
    if ws is None:
        return {"error": res if isinstance(res, str) else "connection failed"}
    info = res if isinstance(res, dict) else {}
    try:
        # request balance
        rid = str(int(time.time() * 1000))
        ws.send("42" + json.dumps(["get-balance", {"requestId": rid, "types": ["*"]}]))
        deadline = time.time() + 12
        balance = None
        while time.time() < deadline:
            try:
                m = ws.recv()
            except Exception:
                break
            if not m:
                continue
            if "balance" in m.lower() or "amount" in m.lower() or "error" in m.lower():
                info["balance_raw"] = m[:300]
                break
        if info.get("authenticated"):
            return {"ok": True, "demo": True, "message": "Authenticated to Pocket Option DEMO.",
                    "detail": info}
        return {"ok": False, "demo": True,
                "message": "SSID not accepted (invalid/expired session). Grab a fresh SSID from your logged-in browser.",
                "detail": info}
    finally:
        try:
            ws.close()
        except Exception:
            pass


def open_deal(ssid, asset, amount, action, duration):
    ssid, err = check_ssid(ssid)
    if err:
        return {"error": err}
    action = (action or "call").lower()
    if action not in ("call", "put"):
        return {"error": "action must be 'call' (up) or 'put' (down)"}
    try:
        amount = float(amount)
        duration = int(duration)
    except Exception:
        return {"error": "amount/duration must be numeric"}
    if amount <= 0:
        return {"error": "amount must be > 0"}
    asset = (asset or "").strip().upper()
    if not asset:
        return {"error": "Provide an asset, e.g. EURUSD_otc"}
    ws, res = _open(ssid)
    if ws is None:
        return {"error": res if isinstance(res, str) else "connection failed"}
    try:
        rid = str(int(time.time() * 1000))
        payload = json.dumps(["open-deal", {
            "asset": asset, "amount": amount, "time": duration,
            "action": action, "isDemo": 1, "requestId": rid,
            "optionType": 100, "tournamentId": 0,
        }])
        ws.send("42" + payload)
        deadline = time.time() + 12
        resp = None
        while time.time() < deadline:
            try:
                m = ws.recv()
            except Exception:
                break
            if not m:
                continue
            if "open-deal" in m.lower() or "error" in m.lower():
                resp = m[:400]
                break
        return {"demo": True, "asset": asset, "amount": amount, "action": action,
                "duration_sec": duration, "raw_response": resp,
                "note": "DEMO trade submitted. Response shown raw — verify in your Pocket Option demo account."}
    finally:
        try:
            ws.close()
        except Exception:
            pass
