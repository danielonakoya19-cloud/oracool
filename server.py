#!/usr/bin/env python3
"""
ORA-COOL AI — backend server (v2)
A JARVIS-style personal assistant backend. Pure Python standard library.
  - Serves the frontend
  - Proxies an OpenAI-compatible chat API (OpenAI / Groq, server-side keys)
  - OSINT toolkit (free tier): IP, domain, username, weather, HIBP email
  - PRO tools (JWT-gated): Tavily search, Shodan, VirusTotal, AbuseIPDB,
    URLScan, LeakCheck
  - Markets (free): stocks (Finnhub), crypto (CoinGecko), FRED economic data
  - Payments: Paystack (initialize + verify) -> issues a PRO JWT
  - Supabase: status check + upgrade persistence
"""
import base64
import hashlib
import html
import hmac as hmac_mod
import json
import os
import re
import random
import secrets
import ssl
import sys
import threading
import time
import shutil
import struct
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import phone_intel
import pocket_option
import cores
import crypto
import communications
import community

PORT = int(os.environ.get("PORT", "8000"))
HOST = "0.0.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UA = "OraCoolAI/1.0 (personal assistant)"

# Groq rotates model names; the default below is verified working (Sep 2026).
GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"
_GROQ_CHAT_POOL = ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b")


def _groq_chat_models():
    """patch46: every Groq chat model we may answer with — the configured one first, then the pool. Each model has
    its own daily token allowance, so rotating spreads the free quota instead of failing the user."""
    out = []
    for m in (KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL), KEYS.get("GROQ_FAST_MODEL", "")) + _GROQ_CHAT_POOL:
        m = str(m or "").strip()
        if m and m not in out:
            out.append(m)
    return out


def _brain_error_text(code, body):
    """A human sentence for a provider failure (the raw JSON stays for the log)."""
    low = (body or "").lower()
    if "insufficient_quota" in low or "no credits" in low or "billing" in low:
        return "The AI provider account has no credits left — the operator needs to top it up or switch provider."
    if code == 429 or "rate limit" in low or "rate_limit" in low:
        return "Every AI brain is rate-limited right now (daily token caps) — please try again in a few minutes."
    if code in (401, 403):
        return "The AI provider rejected the server key — the operator needs to check it."
    return f"AI provider error {code}: {(body or '')[:200]}"

# ------------------------------------------------------------------ key vault

KEYS = {}

def _load_keys():
    """Load keys from keys.json (env vars override)."""
    p = os.path.join(BASE_DIR, "keys.json")
    if os.path.exists(p):
        try:
            with open(p, "r") as f:
                KEYS.update(json.load(f))
        except Exception as e:
            print("keys.json error:", e)
    # environment overrides (Render / Railway / VPS: set these as env vars
    # instead of keys.json; env vars win over keys.json)
    _BOOL_KEYS = {"PAYSTACK_TEST"}
    _JSON_KEYS = {"ADMIN_EMAILS"}
    _INT_KEYS = {"CHAT_MAX_TOKENS", "PRO_PRICE_NGN"}
    for name in ("OPENAI_API_KEY", "GROQ_API_KEY", "GROQ_MODEL", "GROQ_FAST_MODEL",
                 "BRAIN_PROVIDER", "CHAT_MAX_TOKENS",
                 "PAYSTACK_SECRET_KEY", "PAYSTACK_PUBLIC_KEY",
                 "PAYSTACK_TEST", "PAYSTACK_TEST_SECRET", "PAYSTACK_TEST_PUBLIC",
                 "PAYSTACK_CURRENCY", "PRO_PRICE_NGN", "PRO_LABEL",
                 "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY",
                 "ADMIN_EMAILS", "GITHUB_TOKEN",
                 "SHODAN_API_KEY", "VIRUSTOTAL_API_KEY", "ABUSEIPDB_API_KEY",
                 "IPINFO_API_KEY", "NUMVERIFY_API_KEY", "LEAKCHECK_API_KEY",
                 "URLSCAN_API_KEY", "TAVILY_API_KEY", "FINNHUB_API_KEY",
                 "COINGECKO_API_KEY", "FRED_API_KEY", "HIBP_API_KEY", "NASA_API_KEY",
                 "ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET",
                 "HIA_API_KEY", "HIA_IMAGE_MODEL", "HIA_VIDEO_MODEL",
                 "PIXAZO_KEY", "SHORTAPI_KEY", "TOKENMIX_API_KEY",
                 "NEXAAPI_KEY", "NEXAAPI_IMAGE_MODEL", "TRACKER_DOMAIN",
                 "FCS_API_KEY", "FCS_PUBLIC_API_KEY", "DOMSCAN_API_KEY",
                 "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                 "HA_URL", "HA_TOKEN",
                 "JWT_SECRET", "ENCRYPTION_KEY", "ENCRYPTION_IV",
                 "ATLOS_MERCHANT_ID", "ATLOS_API_SECRET", "ATLOS_BASE", "CRYPTO_WALLET_EVM",
                 "AGNES_API_KEY", "AGNES_BASE", "AGNES_MODEL", "AGNES_IMAGE_MODEL",
                 "AGNES_VIDEO_MODEL", "HIA_VIDEO_AUDIO_MODEL", "HF_TOKEN",
                 "PUBLIC_BASE_URL", "PEXELS_API_KEY", "COMMUNICATIONS_ENABLED", "SENDGRID_ENABLED", "SENDGRID_API_KEY", "SENDGRID_FROM_EMAIL", "KAIROS_API_KEY", "KAIROS_APP_ID"):
        env = os.environ.get(name)
        if not env:
            continue
        if name in _BOOL_KEYS:
            KEYS[name] = env.strip().lower() in ("1", "true", "yes", "on")
        elif name in _JSON_KEYS:
            try:
                KEYS[name] = json.loads(env)
            except Exception:
                KEYS[name] = [e.strip() for e in env.split(",") if e.strip()]
        elif name in _INT_KEYS:
            try:
                KEYS[name] = int(env)
            except Exception:
                pass
        else:
            KEYS[name] = env

# patch41: remote key vault — keys the operator adds from anywhere (Supabase kv row "server_vault")
# without touching the hosting dashboard. Local keys.json / env vars always win; the vault only fills gaps.
_VAULT = {}
_VAULT_TS = [0.0]
_VAULT_LOCK = threading.Lock()


def _vault_refresh(force=False):
    if not force and time.time() - _VAULT_TS[0] < 300:
        return False
    _VAULT_TS[0] = time.time()
    try:
        v = supabase_kv_get("server_vault")
    except Exception:
        v = None
    if not isinstance(v, dict):
        return False
    fresh = {str(k).strip(): str(x).strip() for k, x in v.items()
             if isinstance(x, (str, int, float)) and str(x).strip() and re.fullmatch(r"[A-Z0-9_]{3,64}", str(k).strip())}
    with _VAULT_LOCK:
        _VAULT.clear()
        _VAULT.update(fresh)
    return True


def _vault_loop():
    while True:
        time.sleep(300)
        try:
            _vault_refresh(force=True)
        except Exception:
            pass


def server_vault_set(updates):
    """Merge {NAME: value} into the remote vault (empty value deletes). Returns the stored key names."""
    cur = {}
    try:
        cur = supabase_kv_get("server_vault") or {}
    except Exception:
        cur = {}
    if not isinstance(cur, dict):
        cur = {}
    for k, v in (updates or {}).items():
        k = str(k).strip()
        if not re.fullmatch(r"[A-Z0-9_]{3,64}", k):
            continue
        if v is None or not str(v).strip():
            cur.pop(k, None)
        else:
            cur[k] = str(v).strip()
    supabase_kv_put("server_vault", cur)
    _vault_refresh(force=True)
    return sorted(cur.keys())


def key(name):
    v = KEYS.get(name, os.environ.get(name))
    if v is None or (isinstance(v, str) and not v.strip()):
        v = _VAULT.get(name)
    return (v or "").strip() if isinstance(v, str) else v

# ---------------------------------------------------------------- HTTP helper

def http_fetch(url, method="GET", headers=None, json_body=None, data=None, timeout=25):
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif data is not None:
        body = data
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read()
        return resp.status, raw, resp.headers.get("Content-Type", "")

# ------------------------------------------------------------------ JWT (HS256)

def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")

def _b64u_decode(s):
    return base64.urlsafe_b64decode(s + b"=" * (-len(s) % 4))

def sign_jwt(payload, secret):
    seg1 = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    seg2 = _b64u(json.dumps(payload).encode())
    sig = hmac_mod.new(secret.encode(), seg1 + b"." + seg2, hashlib.sha256).digest()
    return (seg1 + b"." + seg2 + b"." + _b64u(sig)).decode()

def verify_jwt(token, secret):
    try:
        seg1, seg2, seg3 = token.encode().split(b".")
        expected = hmac_mod.new(secret.encode(), seg1 + b"." + seg2, hashlib.sha256).digest()
        if not hmac_mod.compare_digest(_b64u_decode(seg3), expected):
            return None
        payload = json.loads(_b64u_decode(seg2))
        if payload.get("exp", 0) < time.time():
            return None
        if payload.get("trial"):  # the free 24h PRO trial was removed — old trial tokens are dead
            return None
        return payload
    except Exception:
        return None

TIER_RANK = {"free": 0, "starter": 1, "pro": 2, "ultra": 3, "enterprise": 4}

PLANS = {
    "starter":    {"label": "Starter",     "price_usd": 29,  "price_ngn": 45000,  "days": 30,
                   "coins_wk": 3_000_000},
    "pro":        {"label": "Pro",         "price_usd": 49,  "price_ngn": 75000,  "days": 30,
                   "coins_wk": 8_000_000},
    "ultra":      {"label": "Professional","price_usd": 149, "price_ngn": 230000, "days": 30,
                   "coins_wk": 25_000_000},
    "enterprise": {"label": "Enterprise · All Features", "price_usd": 500, "price_ngn": 750000,
                   "days": 30, "all_features": True, "coins_unlimited": True},
}

# Reinstatement fine: a suspended account is locked out of the AI entirely and
# may restore itself only by paying this flat fine (or by an administrator's
# manual unblock). It is NOT a plan: it never grants a tier.
FINE_USD = 20


def fine_ngn():
    try:
        return int(KEYS.get("FINE_PRICE_NGN") or os.environ.get("FINE_PRICE_NGN") or 31000)
    except Exception:
        return 31000


def tier_gte(tier, required):
    return TIER_RANK.get(tier, 0) >= TIER_RANK.get(required, 0)

def make_tier_token(email, tier="pro", admin=False):
    t = tier if tier in TIER_RANK else "pro"
    p = {"sub": email or "user", "tier": t,
         "iat": int(time.time()), "exp": int(time.time()) + 90 * 86400}
    if admin:
        p["admin"] = True
    return sign_jwt(p, key("JWT_SECRET") or "dev-secret")

def make_pro_token(email):
    return make_tier_token(email, "pro")

def make_admin_token(email):
    return make_tier_token(email, "enterprise", admin=True)

# ---------------------------------------------------------------- admin / users

def admin_emails():
    return [str(e).strip().lower() for e in KEYS.get("ADMIN_EMAILS", []) if str(e).strip()]

def is_admin(email):
    return bool(email) and (email or "").strip().lower() in admin_emails()

def identity_prompt_head(chat_email=""):
    """Creator bond for the AI prompt. The creator's identity is disclosed ONLY
    inside the creator's own AI session — never to other admins or users."""
    _admin_list = admin_emails()
    _creator_email = _admin_list[0].lower() if _admin_list else ""
    _is_creator_session = bool(chat_email) and chat_email.strip().lower() == _creator_email
    if _is_creator_session:
        return (
            "OraCool identity (server-authoritative): you are talking to the CREATOR and owner of OraCool, "
            "DANIEL ONAKOYA ADEBAYO. He built this platform — address him by his chosen name and help with "
            "supported authorized admin actions. Authentication, account ownership, payment checks and safety "
            "boundaries still apply. No other account ever receives this disclosure.")
    return (
        "OraCool identity (server-authoritative): OraCool has a single creator/owner account whose identity is "
        "confidential. That identity is disclosed ONLY to the creator's own AI session. Never reveal, confirm, "
        "guess or hint who created or owns OraCool — to anyone, including other administrators. If asked, say "
        "only that the creator's identity is private. Being an administrator does not reveal creator identity, "
        "and creator status is never granted to an account by typing or claiming it.")


def _users_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "users.json")

def load_users():
    try:
        with open(_users_file()) as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(users):
    with _sub_lock:
        tmp = _users_file() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(users, f, indent=2)
        os.replace(tmp, _users_file())

def touch_user(email, **extra):
    if not email or email in ("guest", ""):
        return
    email = email.strip().lower()
    users = load_users()
    rec = users.get(email) or supabase_get_flag(email) or {"created": time.strftime("%Y-%m-%d %H:%M:%S")}
    rec["email"] = email
    rec["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rec.update(extra)
    users[email] = rec
    save_users(users)
    # mirror activity to Supabase so the record survives Render's ephemeral
    # filesystem — suspension columns are deliberately left alone here.
    supabase_upsert_flag(rec, block_fields=False)

def user_record(email):
    if not email:
        return None
    return load_users().get(email.strip().lower())

_BLOCK_CACHE = {}          # email -> (expires_at_epoch, blocked_bool)
_BLOCK_CACHE_LOCK = threading.Lock()
BLOCK_CACHE_TTL = 60


def _block_cache_set(email, value):
    with _BLOCK_CACHE_LOCK:
        if len(_BLOCK_CACHE) > 5000:
            _BLOCK_CACHE.clear()
        _BLOCK_CACHE[email] = (time.time() + BLOCK_CACHE_TTL, bool(value))


def is_blocked(email):
    """Authoritative suspension check.

    The durable Supabase row wins whenever it is reachable (so a block survives
    redeploys and reaches every instance within BLOCK_CACHE_TTL seconds); the
    local record is the fallback. Results are cached per email for a minute."""
    email = (email or "").strip().lower()
    if not email:
        return False
    now = time.time()
    with _BLOCK_CACHE_LOCK:
        hit = _BLOCK_CACHE.get(email)
    if hit and hit[0] > now:
        return hit[1]
    cloud = supabase_get_flag(email) if (key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY")) else None
    if cloud is not None:
        blocked = bool(cloud.get("blocked"))
        try:   # keep the local mirror consistent with the durable truth
            users = load_users()
            rec = users.get(email)
            if rec is not None and bool(rec.get("blocked")) != blocked:
                rec["blocked"] = blocked
                rec["block_reason"] = cloud.get("block_reason") or ""
                rec["blocked_by"] = cloud.get("blocked_by") or ""
                rec["blocked_at"] = cloud.get("blocked_at") or ""
                users[email] = rec
                save_users(users)
        except Exception:
            pass
    else:
        rec = user_record(email)
        blocked = bool(rec and rec.get("blocked"))
    _block_cache_set(email, blocked)
    return blocked


def block_user(email, blocked, reason="", by=""):
    """Suspend or reinstate an account. Durable-first: when Supabase is
    configured the row must be written successfully or nothing changes and an
    error is returned — an administrator is never shown a block that would
    evaporate on the next redeploy. Administrators cannot be blocked."""
    email = (email or "").strip().lower()
    if not email:
        return {"error": "Email required."}
    if blocked and is_admin(email):
        return {"error": "Administrator accounts cannot be blocked."}
    if blocked and is_verified(email) and not is_admin(by):
        return {"error": "Verified members (✦) can only be blocked by an administrator."}
    users = load_users()
    rec = users.get(email) or supabase_get_flag(email) or {"created": time.strftime("%Y-%m-%d %H:%M:%S")}
    rec = dict(rec)
    rec["email"] = email
    rec["blocked"] = bool(blocked)
    rec["block_reason"] = reason if blocked else ""
    rec["blocked_by"] = by if blocked else ""
    rec["blocked_at"] = time.strftime("%Y-%m-%d %H:%M:%S") if blocked else ""
    if key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY"):
        if not supabase_upsert_flag(rec, block_fields=True):
            return {"error": "Could not persist the suspension state to durable storage. Nothing changed — retry in a moment."}
    users[email] = rec
    save_users(users)
    _block_cache_set(email, bool(blocked))
    try:
        audit_log(by or "system", "account.blocked" if blocked else "account.reinstated", email + (" · " + reason if reason else ""))
    except Exception:
        pass
    return {"ok": True, "user": rec}


# ---------------------------------------------------------------- reinstatement fines

def _fines_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "fines.json")


_FINES_LOCK = threading.Lock()


def _fines_load():
    d = None
    try:
        d = supabase_kv_get("fines")
    except Exception:
        d = None
    if not isinstance(d, dict):
        try:
            with open(_fines_file()) as f:
                d = json.load(f)
        except Exception:
            d = {}
    return d if isinstance(d, dict) else {}


def _fines_save(d):
    try:
        with open(_fines_file() + ".tmp", "w") as f:
            json.dump(d, f, indent=1)
        os.replace(_fines_file() + ".tmp", _fines_file())
    except Exception:
        pass
    try:
        supabase_kv_put("fines", d)
    except Exception:
        pass


def fine_paid(email, reference, amount=0, currency="NGN", channel="", source="paystack"):
    """Record a paid reinstatement fine and lift the suspension. Idempotent per
    reference. Never grants a plan. If the durable unblock write fails the fine
    stays marked pending and block_status() retries it on the next poll."""
    email = (email or "").strip().lower()
    reference = str(reference or "").strip()
    if not email or not reference:
        return {"error": "Fine payment is missing the account or reference."}
    with _FINES_LOCK:
        d = _fines_load()
        rec = d.get(reference) or {"email": email, "reference": reference, "amount": amount, "currency": currency,
                                   "channel": channel, "source": source, "paid_at": _now(), "unblocked": False}
        if rec.get("unblocked"):
            d[reference] = rec
            _fines_save(d)
            return {"ok": True, "already": True, "unblocked": True, "email": email}
        r = block_user(email, False, "", "fine:" + reference)
        rec["unblocked"] = bool(r.get("ok"))
        rec["unblock_error"] = "" if r.get("ok") else str(r.get("error") or "")
        d[reference] = rec
        _fines_save(d)
    try:
        emit_event(email, "fine", "Reinstatement fine paid — access restored" if rec["unblocked"] else "Reinstatement fine paid — reinstatement pending",
                   "ref " + reference + " · " + str(amount) + " " + str(currency) + " · via " + str(channel or source))
        notify_admins("fine", "Reinstatement fine paid: " + email, "ref " + reference + " · " + str(amount) + " " + str(currency)
                      + (" · access restored" if rec["unblocked"] else " · UNBLOCK PENDING (storage error)"))
    except Exception:
        pass
    return {"ok": True, "unblocked": rec["unblocked"], "email": email, "reference": reference}


def fines_for(email):
    email = (email or "").strip().lower()
    return [r for r in _fines_load().values() if (r.get("email") or "").lower() == email]


def block_status(email):
    """What a suspended account may see: its own status, the fine and how to pay.
    Also retries any paid-but-pending reinstatement for that account."""
    email = (email or "").strip().lower()
    out = {"email": email, "blocked": False, "reason": "", "blocked_at": "",
           "fine": {"cancelled": True},
           "note": "No fine is collected. An administrator lifts the block from the Admin console."}
    if not email:
        return out
    pending = [r for r in fines_for(email) if not r.get("unblocked")]
    if pending and is_blocked(email):
        for r in pending:
            fine_paid(email, r.get("reference"), r.get("amount"), r.get("currency"), r.get("channel"), r.get("source") or "retry")
    out["blocked"] = is_blocked(email)
    if out["blocked"]:
        rec = user_record(email) or supabase_get_flag(email) or {}
        out["reason"] = rec.get("block_reason") or ""
        out["blocked_at"] = rec.get("blocked_at") or ""
    out["fines_paid"] = len([r for r in fines_for(email) if r.get("unblocked")])
    return out


def admin_set_pro(email, tier, days=30, by=""):
    """Manually grant (or revoke) a subscription from the admin console."""
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "Enter a valid email address."}
    if tier not in ("", None, "free") and tier not in PLANS:
        return {"error": "Unknown plan."}
    # Retire cloud entitlements too; old paid rows must not resurrect a revoked plan.
    if key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY"):
        try:
            http_fetch(key("SUPABASE_URL").rstrip("/")+"/rest/v1/subscribers?email=eq."+urllib.parse.quote(email,safe=""),
                       method="PATCH", headers=_supabase_headers(),
                       json_body={"expires_at":time.strftime("%Y-%m-%d",time.gmtime(time.time()-86400))}, timeout=10)
        except Exception:
            return {"error":"Could not update durable plan access. No local plan change was made; retry after database connectivity is restored."}
    if tier in ("", None, "free"):
        subs = [s for s in load_subscribers() if (s.get("email") or "").lower() != email]
        try:
            with _sub_lock:
                with open(_sub_file(), "w") as f:
                    json.dump(subs, f, indent=2)
        except Exception:
            pass
        touch_user(email, pro=False)
        try:
            emit_event(email, "payment", "Plan revoked", "Access back to Free beta" + (" (by " + by + ")" if by else ""))
        except Exception:
            pass
        return {"ok": True, "revoked": True, "email": email}
    if tier not in PLANS:
        return {"error": "Unknown plan."}
    days = max(1, int(days or PLANS[tier]["days"]))
    rec = {"email": email, "reference": "admin-" + _token(8),
           "amount_ngn": 0, "paid_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "expires_at": time.strftime("%Y-%m-%d", time.gmtime(time.time() + days * 86400)),
           "channel": "admin", "tier": tier, "plan": tier, "days": days,
           "by": by or ""}
    save_subscriber(rec)
    supabase_store_subscriber(rec)
    touch_user(email, pro=True)
    try:
        emit_event(email, "payment", "Plan granted: " + str(tier).upper() + " · " + str(days) + " days",
                   ("granted by " + by) if by else "admin action")
    except Exception:
        pass
    return {"ok": True, "granted": True, "email": email, "tier": tier, "days": days}

# ------------------------------------------------------------------ subscribers

_sub_lock = threading.Lock()

def _sub_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "subscribers.json")

def load_subscribers():
    try:
        with open(_sub_file()) as f:
            return json.load(f)
    except Exception:
        return []

def save_subscriber(rec):
    with _sub_lock:
        subs = load_subscribers()
        subs = [s for s in subs if s.get("email") != rec.get("email")]
        subs.append(rec)
        try:
            with open(_sub_file(), "w") as f:
                json.dump(subs, f, indent=2)
        except Exception as e:
            print("subscriber save error:", e)

def supabase_store_subscriber(rec):
    """Best-effort write to Supabase 'subscribers' table; ignore if absent."""
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return False
    try:
        http_fetch(url.rstrip("/") + "/rest/v1/subscribers", method="POST",
                   headers={"apikey": svc, "Authorization": "Bearer " + svc,
                            "Prefer": "return=minimal"},
                   json_body={"email": str(rec.get("email") or "").strip().lower(),
                              "tier": rec.get("plan") or rec.get("tier") or "pro",
                              "plan": rec.get("plan") or rec.get("tier") or "pro",
                              "reference": rec.get("reference"),
                              "amount_ngn": rec.get("amount_ngn"), "amount_usd": rec.get("amount_usd"),
                              "paid_at": rec.get("paid_at"), "expires_at": rec.get("expires_at"),
                              "channel": rec.get("channel"), "days": rec.get("days"),
                              "by": rec.get("by")},
                   timeout=10)
        return True
    except Exception:
        return False


def _supabase_headers():
    svc = key("SUPABASE_SERVICE_KEY")
    return {"apikey": svc, "Authorization": "Bearer " + svc}


def supabase_auth_users():
    """All registered users from Supabase Auth (persistent). Requires the
    SUPABASE_SERVICE_KEY. Returns [] if Supabase is not wired or unreachable."""
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return []
    try:
        _, raw, _ = http_fetch(url.rstrip("/") + "/auth/v1/admin/users?per_page=1000",
                               headers={"apikey": svc, "Authorization": "Bearer " + svc},
                               timeout=20)
        return (json.loads(raw).get("users") or []) if raw else []
    except Exception:
        return []


def supabase_all_flags():
    """All rows from the 'user_flags' table (persistent block/seen flags)."""
    url = key("SUPABASE_URL")
    if not url or not key("SUPABASE_SERVICE_KEY"):
        return {}
    try:
        _, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/user_flags?select=*",
                               headers=_supabase_headers(), timeout=15)
        rows = json.loads(raw) if raw else []
        return {str((r.get("email") or "")).lower(): r for r in rows}
    except Exception:
        return {}


def supabase_get_flag(email):
    url = key("SUPABASE_URL")
    if not url or not key("SUPABASE_SERVICE_KEY"):
        return None
    try:
        q = "email=eq." + urllib.parse.quote(email.lower().strip(), safe="")
        _, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/user_flags?" + q,
                               headers=_supabase_headers(), timeout=10)
        rows = json.loads(raw) if raw else []
        rows = [r for r in rows if (r.get("email") or "").lower() == email.lower().strip()]
        return rows[0] if rows else None
    except Exception:
        return None


def supabase_upsert_flag(rec, block_fields=True):
    """Best-effort upsert of one user's flags into Supabase.

    block_fields=False (activity touches) omits the suspension columns so a
    login or heartbeat can never overwrite an administrator's block — only
    block_user() writes those columns."""
    url = key("SUPABASE_URL")
    if not url or not key("SUPABASE_SERVICE_KEY"):
        return False
    email = str(rec.get("email") or "").strip().lower()
    if not email:
        return False
    body = {"email": email,
            "created": rec.get("created", ""),
            "last_seen": rec.get("last_seen", ""),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if block_fields:
        body.update({"blocked": bool(rec.get("blocked")),
                     "block_reason": rec.get("block_reason") or "",
                     "blocked_by": rec.get("blocked_by") or "",
                     "blocked_at": rec.get("blocked_at") or ""})
    for field in ("last_ip", "verified", "email_verification_required"):
        if field in rec:
            body[field] = rec[field]
    if isinstance(rec.get("coins"), (dict, list)):
        body["coins"] = json.dumps(rec["coins"])
    elif "coins" in rec:
        body["coins"] = str(rec.get("coins") or "")
    try:
        http_fetch(url.rstrip("/") + "/rest/v1/user_flags", method="POST",
                   headers=dict(_supabase_headers(), **{"Prefer": "resolution=merge-duplicates"}),
                   json_body=body, timeout=10)
        return True
    except Exception:
        return False

# ---------------------------------------------------------------- Supabase auth

def supabase_auth(path, method="GET", json_body=None, access_token=None):
    """Proxy a GoTrue (Supabase Auth) request. Returns {status, data} or {error}."""
    url = key("SUPABASE_URL"); anon = key("SUPABASE_ANON_KEY")
    if not url or not anon:
        return {"error": "Supabase is not configured (keys.json)."}
    headers = {"apikey": anon, "Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = "Bearer " + access_token
    try:
        st, raw, _ = http_fetch(url.rstrip("/") + path, method=method,
                                headers=headers, json_body=json_body, timeout=25)
        try:
            return {"status": st, "data": json.loads(raw)}
        except Exception:
            return {"status": st, "data": raw.decode("utf-8", "replace")[:400]}
    except urllib.error.HTTPError as e:
        try:
            return {"status": e.code, "data": json.loads(e.read().decode("utf-8", "replace"))}
        except Exception:
            return {"status": e.code, "data": {"msg": e.read().decode("utf-8", "replace")[:300]}}
    except Exception as e:
        return {"error": str(e)}


def password_strength(pw):
    """Return a list of human-readable problems with the password (empty = OK)."""
    errs = []
    pw = pw or ""
    if len(pw) < 8:
        errs.append("at least 8 characters")
    if not re.search(r"[A-Z]", pw):
        errs.append("an uppercase letter")
    if not re.search(r"[a-z]", pw):
        errs.append("a lowercase letter")
    if not re.search(r"[0-9]", pw):
        errs.append("a number")
    return errs


_oauth_probe = {"t": 0.0, "data": {}}


def oauth_providers():
    """Live detection of enabled Supabase OAuth providers (5-min cache) so the
    UI never shows a raw 'Unsupported provider' error — it explains the fix."""
    now = time.time()
    if now - _oauth_probe["t"] < 300 and _oauth_probe["data"]:
        return _oauth_probe["data"]
    out = {"google": False, "github": False, "checked": True}
    url = key("SUPABASE_URL"); anon = key("SUPABASE_ANON_KEY")
    if url and anon:
        try:
            _, raw, _ = http_fetch(url.rstrip("/") + "/auth/v1/settings",
                                    headers={"apikey": anon}, timeout=12)
            ext = (json.loads(raw).get("external") or {})
            out["google"] = bool(ext.get("google"))
            out["github"] = bool(ext.get("github"))
        except Exception:
            out["checked"] = False
    _oauth_probe["t"] = now
    _oauth_probe["data"] = out
    return out


def _supa_admin_user(email):
    """Look up one Supabase Auth user record by exact email (None on miss/error)."""
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if not url or not svc or not email:
        return None
    try:
        _, raw, _ = http_fetch(url.rstrip("/") + "/auth/v1/admin/users?filter=" + urllib.parse.quote(email),
                                headers={"apikey": svc, "Authorization": "Bearer " + svc},
                                timeout=20)
        for u in (json.loads(raw).get("users") or []):
            if (u.get("email") or "").lower() == email.lower():
                return u
        return None
    except Exception:
        return None


def is_verified(email):
    """Email-verification state. Admins are always trusted. We fail OPEN on any
    Supabase outage so the app can never lock its own users out."""
    if not email:
        return True
    email = email.strip().lower()
    if is_admin(email):
        return True
    rec = user_record(email)
    if rec and rec.get("verified"):
        return True
    if not key("SUPABASE_URL") or not key("SUPABASE_SERVICE_KEY"):
        return True
    u = _supa_admin_user(email)
    if u is None:
        return True  # unknown/legacy state — do not lock anyone out
    if u.get("email_confirmed_at"):
        touch_user(email, verified=True)
        return True
    return False


def auth_signup(email, password, name="", username=""):
    """Password signup without an email-code gate; passwords stay in Supabase.
    The username is the member's PUBLIC, UNIQUE community identity (their e-mail
    is never shown to other members). If omitted, one is auto-assigned later."""
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "Enter a valid email address."}
    errs = password_strength(password)
    if errs:
        return {"error": "Password needs: " + ", ".join(errs) + "."}
    # Never allow somebody to claim creator privileges by typing a reserved email.
    if is_admin(email):
        return {"error": "This is a reserved administrator address. Sign in to the existing account; public signup cannot create admins."}
    un = None
    if username:
        un = str(username).strip().lstrip("@").lower()
        if not community.USERNAME_RE.match(un):
            return {"error": "Usernames are 3-20 characters, start with a letter or number, and use only letters, numbers, dots and underscores."}
        if un in community.RESERVED_USERNAMES:
            return {"error": "That username is reserved. Pick another one."}
        taken = community_service().username_taken(un)
        if taken:
            return {"error": "That username is already taken. Pick another one."}
        if taken is None:   # community tables not in place yet - check account records
            for rec in load_users().values():
                if str(rec.get("username") or "").lower() == un:
                    return {"error": "That username is already taken. Pick another one."}
    url, svc = key("SUPABASE_URL"), key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return {"error": "Signup is not configured. The operator must set Supabase server credentials."}
    created = {}
    try:
        st, raw, _ = http_fetch(url.rstrip("/") + "/auth/v1/admin/users", method="POST",
                                headers={"apikey": svc, "Authorization": "Bearer " + svc,
                                         "Content-Type": "application/json"},
                                json_body={"email": email, "password": password, "email_confirm": True,
                                           "user_metadata": {"display_name": (name or email.split("@")[0])[:60],
                                                             "username": un or "",
                                                             "signup_mode": "password_only"}}, timeout=25)
        try:
            created = json.loads(raw) if raw else {}
        except Exception:
            created = {}
    except urllib.error.HTTPError as e:
        if e.code in (400, 422):
            return {"error": "Could not create this account. If already registered, sign in or reset your password."}
        return {"error": "Signup service unavailable. Please try again later."}
    except Exception:
        return {"error": "Signup service unavailable. Please try again later."}
    r = supabase_auth("/auth/v1/token?grant_type=password", method="POST",
                      json_body={"email": email, "password": password})
    if r.get("status") == 200 and (r.get("data") or {}).get("access_token"):
        touch_user(email, email_verification_required=False)
        try:
            prof = community_service().ensure_profile(email, un, (name or "").strip()[:60])
        except community.UsernameTaken:
            uid = (created.get("user") or {}).get("id")
            if uid:   # raced for the username - undo the account just created
                try:
                    http_fetch(url.rstrip("/") + "/auth/v1/admin/users/" + uid, method="DELETE",
                               headers={"apikey": svc, "Authorization": "Bearer " + svc}, timeout=15)
                except Exception:
                    pass
            return {"error": "That username was just taken by another member. Pick another one."}
        if prof:
            touch_user(email, username=prof.get("username"), oracool_number=prof.get("oracool_number"))
        return {"status": 200, "data": r["data"], "admin": False,
                "blocked": is_blocked(email), "pro_token": check_subscription(email),
                "community": {"username": (prof or {}).get("username"),
                              "number": (prof or {}).get("oracool_number")}}
    return {"error": "Account created. Please sign in with your email and password."}


def auth_confirm(token_hash):
    """Exchange the token_hash from the verification link for a real session."""
    token_hash = (token_hash or "").strip()
    if not token_hash:
        return {"error": "Confirmation token required — open the link from the email we sent."}
    r = supabase_auth("/auth/v1/verify", method="POST",
                      json_body={"token_hash": token_hash, "type": "signup"})
    if r.get("status") != 200:
        d = r.get("data") or {}
        msg = (d.get("msg") or d.get("error_description")) if isinstance(d, dict) else str(d)
        if isinstance(d, dict) and "already" in str(d.get("msg", "")).lower():
            return {"error": "This link was already used — just sign in with your email and password."}
        return {"error": "Could not confirm: " + str(msg or "invalid or expired link")[:180]}
    data = r.get("data") or {}
    email = ((data.get("user") or {}).get("email") or "").lower()
    if email:
        touch_user(email, verified=True)
    return {"status": 200, "data": data, "admin": is_admin(email),
            "blocked": is_blocked(email), "pro_token": check_subscription(email)}


def auth_send_code(email):
    """Send (or re-send) the verification email containing the numeric code.
    Supabase's own mailer delivers it; generate_link both sends and lets us
    confirm delivery. The code itself is NEVER returned to the browser."""
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "Enter a valid email address."}
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return {"error": "Supabase is not configured on the server."}
    u = _supa_admin_user(email)
    if u is None:
        return {"error": "No account found with that email — create one first."}
    if u.get("email_confirmed_at"):
        touch_user(email, verified=True)
        return {"already_verified": True, "message": "That email is already verified — just sign in."}
    try:
        _, raw, _ = http_fetch(url.rstrip("/") + "/auth/v1/admin/generate_link", method="POST",
                               headers={"apikey": svc, "Authorization": "Bearer " + svc,
                                        "Content-Type": "application/json"},
                               json_body={"type": "signup", "email": email}, timeout=25)
        d = json.loads(raw)
        if d.get("confirmation_sent_at") or d.get("hashed_token"):
            return {"sent": True,
                    "message": "A verification code was emailed to " + email
                               + ". Enter the numeric code from the email below."}
    except Exception:
        pass
    # fallback path: native resend
    r = supabase_auth("/auth/v1/resend", method="POST", json_body={"email": email, "type": "signup"})
    if r.get("status") == 200:
        return {"sent": True, "message": "Verification email re-sent to " + email + "."}
    d = r.get("data") or {}
    msg = (d.get("msg") or d.get("error_description")) if isinstance(d, dict) else str(d)
    return {"error": "Supabase could not send the email: " + str(msg)[:160],
            "hint": "Check Supabase → Authentication → Providers → Email (enabled + within hourly limits)."}


def auth_verify_code(email, code):
    """Verify the numeric code the user received; on success confirm the account
    and log them in with a real session."""
    email = (email or "").strip().lower()
    code = re.sub(r"\D", "", str(code or ""))
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "Enter the email you signed up with."}
    if not re.match(r"^\d{4,10}$", code):
        return {"error": "Enter the numeric code from the email (digits only)."}
    r = supabase_auth("/auth/v1/verify", method="POST",
                      json_body={"type": "signup", "token": code, "email": email})
    data = r.get("data") or {}
    if r.get("status") == 200 and isinstance(data, dict) and data.get("access_token"):
        touch_user(email, verified=True)
        return {"status": 200, "data": data, "admin": is_admin(email),
                "blocked": is_blocked(email), "pro_token": check_subscription(email)}
    # email already confirmed via link earlier?
    u = _supa_admin_user(email)
    if u and u.get("email_confirmed_at"):
        touch_user(email, verified=True)
        return {"already_verified": True,
                "message": "Your email is already verified — sign in with your password."}
    d = r.get("data") or {}
    msg = (d.get("msg") or d.get("error_description")) if isinstance(d, dict) else str(d)
    return {"error": "That code is not valid (expired?). Tap 'Send a new code' to try again."
            + (" — " + str(msg)[:120] if msg else "")}


def auth_resend(email):
    return auth_send_code(email)


def check_tier(email):
    """Return the user's plan tier: free|starter|pro|ultra|enterprise."""
    if not email:
        return "free"
    email = email.lower().strip()
    if is_admin(email):
        return "enterprise"
    if is_blocked(email):
        return "free"
    best = None
    today = time.strftime("%Y-%m-%d")
    rows = []
    for s in load_subscribers():
        if (s.get("email") or "").lower() == email:
            rows.append(s)
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if url and svc:
        try:
            q = "email=eq." + urllib.parse.quote(email, safe="")
            _, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/subscribers?" + q,
                                   headers={"apikey": svc, "Authorization": "Bearer " + svc},
                                   timeout=10)
            # belt AND braces: never trust the gateway filter alone — re-filter client-side
            rows.extend([r for r in (json.loads(raw) or [])
                         if (r.get("email") or "").lower() == email])
        except Exception:
            pass
    for r in rows:
        exp = r.get("expires_at") or ""
        if exp and exp >= today:
            t = (r.get("plan") or r.get("tier") or "pro").lower()
            if t not in TIER_RANK:
                t = "pro"
            if best is None or TIER_RANK[t] > TIER_RANK[best]:
                best = t
    return best or "free"

def check_subscription(email):
    """Return a tier JWT if the email has an active subscription, else None."""
    tier = check_tier(email)
    if tier == "free":
        return None
    return make_tier_token(email, tier)

# ------------------------------------------------------- building coins (patch27)
# Sites are metered in coins: one build = 10,000. Free gets 1,000,000 coins per
# week; when they run out the user waits for the Monday reset or upgrades. The
# wallet lives on the user record (users.json) and is mirrored to Supabase so
# balances survive redeploys. Enterprise pays nothing (unlimited).
COIN_COST_BUILD = 10000
COINS_WEEKLY = {"free": 1_000_000, "starter": 3_000_000, "pro": 8_000_000, "ultra": 25_000_000}
_COINS_LOCK = threading.Lock()


def _iso_week():
    return time.strftime("%G-W%V")


def _coins_parse(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def coins_state(email):
    """Build-coin wallet for one account. Paid tiers auto-refill on the ISO week;
    the FREE tier gets its 1,000,000 coins exactly once (patch38) — no refill
    until the account pays for a plan."""
    email = (email or "").strip().lower()
    tier = check_tier(email)
    unlimited = (tier == "enterprise") or is_admin(email)
    grant = COINS_WEEKLY.get(tier, COINS_WEEKLY["free"])
    one_time = (tier == "free") and not unlimited
    wk = "lifetime" if one_time else _iso_week()
    rec = user_record(email) if email else None
    if not rec and email:
        # users.json is empty after a fresh deploy — hydrate from Supabase once
        _row = supabase_get_flag(email) or {}
        _c = _coins_parse(_row.get("coins"))
        if _c:
            touch_user(email, coins=_c)
            rec = user_record(email)
    cs = _coins_parse((rec or {}).get("coins"))
    if cs.get("week") == wk or (one_time and cs.get("balance") is not None):
        # free accounts keep whatever is left of their one-time grant — a stale
        # weekly record from before patch38 (or a lapsed paid plan) is NOT refilled
        try:
            bal = max(0, int(cs.get("balance", grant)))
        except Exception:
            bal = grant
        if one_time and cs.get("week") != wk and email and email != "guest":
            touch_user(email, coins={"week": wk, "balance": bal})
    else:
        bal = grant
        if email and email != "guest":
            touch_user(email, coins={"week": wk, "balance": bal})
    if one_time:
        reset_in = None
    else:
        try:
            reset_in = 8 - int(time.strftime("%u"))
        except Exception:
            reset_in = 7
    return {"balance": bal, "grant": grant, "week": wk, "tier": tier, "one_time": one_time,
            "unlimited": bool(unlimited), "reset_in_days": reset_in,
            "cost_build": COIN_COST_BUILD,
            "sites_left": (10 ** 9 if unlimited else bal // COIN_COST_BUILD)}


def coins_gate(email, cost=COIN_COST_BUILD):
    """None => build allowed. Otherwise an honest error payload (cooldown + upgrade path)."""
    st = coins_state(email)
    if st["unlimited"] or st["balance"] >= cost:
        return None
    if st.get("one_time"):
        msg = ("Your one-time Free allowance of {:,} build coins is used up — a site build costs {:,} coins and you have "
               "{:,} left. Free accounts do not refill: new coins arrive only when you pay for a plan. "
               .format(st["grant"], cost, st["balance"]))
    else:
        msg = ("You are out of building coins for this week — a site build costs {:,} coins and you have "
               "{:,} left. ".format(cost, st["balance"]))
        msg += ("New coins unlock automatically after the weekly reset ({} day(s), on Mondays). "
                .format(st["reset_in_days"]))
    msg += ("Published sites stay online and everything else on your plan keeps working. "
            "Upgrade to keep building right now: Starter ₦45,000/mo = 3,000,000 coins/week (~300 sites), "
            "Pro ₦75,000/mo = 8,000,000 coins/week (~800 sites), "
            "Professional ₦230,000/mo = 25,000,000 coins/week (~2,500 sites).")
    return {"error": msg, "coins": {"balance": st["balance"], "cost": cost,
                                    "reset_in_days": st["reset_in_days"]},
            "locked": "coins", "upgrade_plan": "starter"}


def coins_charge(email, cost=COIN_COST_BUILD):
    """Deduct a successful build's cost. Returns the post-charge state (or None)."""
    if not email:
        return None
    with _COINS_LOCK:
        st = coins_state(email)
        if st["unlimited"]:
            return st
        new = max(0, int(st["balance"]) - int(cost))
        touch_user(email, coins={"week": st["week"], "balance": new})
        st["balance"] = new
        return st


# ---------------------------------------------------------------- GitHub

def github(path):
    tok = key("GITHUB_TOKEN")
    if not tok:
        return {"error": "GitHub token is not configured."}
    try:
        st, raw, _ = http_fetch("https://api.github.com" + path,
                                headers={"Authorization": "token " + tok,
                                         "Accept": "application/vnd.github+json",
                                         "X-GitHub-Api-Version": "2022-11-28"},
                                timeout=25)
        return {"status": st, "data": json.loads(raw)}
    except urllib.error.HTTPError as e:
        try:
            return {"status": e.code, "data": json.loads(e.read().decode("utf-8", "replace"))}
        except Exception:
            return {"status": e.code, "data": {"msg": e.read().decode("utf-8", "replace")[:200]}}
    except Exception as e:
        return {"error": str(e)}

# ---------------------------------------------------------------- RDAP / WHOIS

_rdap_bootstrap = None
_rdap_bootstrap_time = 0.0

def _get_rdap_servers():
    global _rdap_bootstrap, _rdap_bootstrap_time
    if _rdap_bootstrap and (time.time() - _rdap_bootstrap_time) < 86400:
        return _rdap_bootstrap
    try:
        _, raw, _ = http_fetch("https://data.iana.org/rdap/dns.json", timeout=20)
        _rdap_bootstrap = json.loads(raw)
        _rdap_bootstrap_time = time.time()
    except Exception:
        _rdap_bootstrap = {"services": []}
    return _rdap_bootstrap

def _rdap_url_for_tld(tld):
    boot = _get_rdap_servers()
    for entry in boot.get("services", []):
        tlds, urls = entry[0], entry[1]
        if tld in tlds:
            for u in urls:
                if u.startswith("https://"):
                    return u.rstrip("/")
    fallback = {"com": "https://rdap.verisign.com/com/v1",
                "net": "https://rdap.verisign.com/net/v1",
                "org": "https://rdap.publicinterestregistry.org/rdap"}
    return fallback.get(tld)

def _pick(obj, keys):
    for k in keys:
        if isinstance(obj, dict) and obj.get(k) is not None:
            return obj.get(k)
    return None

def _rdap_entities_to_list(entities):
    out = []
    for e in (entities or [])[:6]:
        vcard = e.get("vcardArray", [None, []])[1]
        name = ""
        for item in vcard or []:
            if isinstance(item, list) and item and item[0] == "fn":
                name = item[3]
                break
        out.append({"name": name or "(redacted)", "roles": e.get("roles", [])})
    return out

def osint_domain(domain):
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain or "." not in domain:
        return {"error": "Please enter a valid domain, e.g. example.com"}
    out = {"domain": domain}
    tld = domain.rsplit(".", 1)[-1]
    rdap_base = _rdap_url_for_tld(tld)
    if rdap_base:
        try:
            url = f"{rdap_base}/domain/{urllib.parse.quote(domain)}"
            _, raw, _ = http_fetch(url, timeout=20)
            r = json.loads(raw)
            dates = {}
            for ev in r.get("events", []):
                if ev.get("eventAction") in ("registration", "expiration", "last changed"):
                    dates[ev["eventAction"]] = ev.get("eventDate", "")[:10]
            out["whois"] = {
                "registrar": _pick(r, ["registrar"]) or _pick(r, ["port43"]),
                "status": r.get("status", []),
                "name_servers": [ns.get("ldhName") for ns in r.get("nameservers", [])],
                "dates": dates,
                "registrant": _rdap_entities_to_list([e for e in r.get("entities", [])
                                                      if "registrant" in e.get("roles", [])]),
                "source": "RDAP",
            }
        except Exception as e:
            out["whois"] = {"error": f"RDAP lookup failed: {e}"}
    else:
        out["whois"] = {"error": f"No RDAP server for .{tld}"}
    dns = {}
    for t in ["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA"]:
        try:
            url = "https://dns.google/resolve?name=" + urllib.parse.quote(domain) + "&type=" + t
            _, raw, _ = http_fetch(url, timeout=12)
            d = json.loads(raw)
            dns[t] = [a.get("data") for a in d.get("Answer", [])] or []
        except Exception:
            dns[t] = []
    out["dns"] = dns
    return out

def osint_ip(ip):
    ip = (ip or "").strip()
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
        return {"error": "Please enter a valid IPv4 address, e.g. 8.8.8.8"}
    out = {"ip": ip}
    # primary geo: ipwho.is
    try:
        _, raw, _ = http_fetch(f"https://ipwho.is/{urllib.parse.quote(ip)}", timeout=15)
        g = json.loads(raw)
        if g.get("success") is not False and g.get("ip"):
            conn = g.get("connection") or {}
            out["geo"] = {
                "ip": g.get("ip"), "type": g.get("type"),
                "continent": g.get("continent"), "country": g.get("country"),
                "region": g.get("region"), "city": g.get("city"),
                "latitude": g.get("latitude"), "longitude": g.get("longitude"),
                "timezone": g.get("timezone"),
                "asn": conn.get("asn"), "isp": conn.get("isp"),
                "org": conn.get("org"), "domain": conn.get("domain"),
            }
        else:
            out["geo"] = {"error": "no data"}
    except Exception as e:
        out["geo"] = {"error": f"GeoIP failed: {e}"}
    # secondary enrichment: ipinfo (if key present)
    ik = key("IPINFO_API_KEY")
    if ik:
        try:
            _, raw, _ = http_fetch(f"https://ipinfo.io/{urllib.parse.quote(ip)}?token={ik}", timeout=15)
            info = json.loads(raw)
            out["ipinfo"] = {"hostname": info.get("hostname"), "org": info.get("org"),
                             "asn": info.get("asn"), "privacy": info.get("privacy"),
                             "abuse": info.get("abuse")}
        except Exception:
            out["ipinfo"] = None
    # reverse DNS
    try:
        rev = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
        url = "https://dns.google/resolve?name=" + rev + "&type=PTR"
        _, raw, _ = http_fetch(url, timeout=12)
        d = json.loads(raw)
        out["reverse_dns"] = [a.get("data") for a in d.get("Answer", [])]
    except Exception:
        out["reverse_dns"] = []
    return out

def osint_email(email, hibp_key):
    email = (email or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "Please enter a valid email address."}
    keyv = (hibp_key or "").strip() or key("HIBP_API_KEY") or ""
    out = {"email": email}

    # --- 1) HaveIBeenPwned (breaches + pastes) — needs an API key
    if keyv:
        headers = {"hibp-api-key": keyv, "User-Agent": "OraCoolAI/1.0"}
        try:
            url = ("https://haveibeenpwned.com/api/v3/breachedaccount/"
                   + urllib.parse.quote(email) + "?truncateResponse=false")
            _, raw, _ = http_fetch(url, headers=headers, timeout=25)
            out["breaches"] = [{"name": b.get("Name"), "title": b.get("Title"),
                                "date": b.get("BreachDate"), "domain": b.get("Domain"),
                                "pwn_count": b.get("PwnCount"), "data_classes": b.get("DataClasses")}
                               for b in json.loads(raw)]
        except urllib.error.HTTPError as e:
            out["breaches"] = [] if e.code == 404 else {"error": f"HIBP error {e.code}"}
            if e.code == 404:
                out["note"] = "No known breaches for this address."
        except Exception as e:
            out["breaches"] = {"error": f"Breach lookup failed: {e}"}
        try:
            url = "https://haveibeenpwned.com/api/v3/pasteaccount/" + urllib.parse.quote(email)
            _, raw, _ = http_fetch(url, headers=headers, timeout=25)
            out["pastes"] = [{"source": p.get("Source"), "id": p.get("Id"),
                              "title": p.get("Title"), "date": p.get("Date")}
                             for p in json.loads(raw)]
        except urllib.error.HTTPError as e:
            out["pastes"] = [] if e.code == 404 else {"error": f"HIBP error {e.code}"}
        except Exception:
            out["pastes"] = []
    else:
        # No HIBP key: say nothing about the paid provider and lean on the free
        # sources below, so the panel never shows a dead end.
        out["breaches"] = []
        out["breaches_note"] = ("Paid breach-database lookups (HIBP) are not enabled on this server — the free "
                                "infostealer and leak indexes below are live.")

    # --- 2) Hudson Rock infostealer exposure (free, no key required)
    try:
        url = ("https://cavalier.hudsonrock.com/api/json/v2/osint-tools/search-by-email?email="
               + urllib.parse.quote(email))
        _, raw, _ = http_fetch(url, timeout=25)
        d = json.loads(raw)
        stealers = d.get("stealers") or []
        out["infostealers"] = [{
            "date": s.get("date_compromised"), "computer": s.get("computer_name"),
            "os": s.get("operating_system"), "services": s.get("total_user_services"),
            "logins": (s.get("top_logins") or [])[:5], "ip": s.get("ip"),
        } for s in stealers[:10]]
        out["infostealers_count"] = len(stealers)
    except Exception:
        out["infostealers"] = []
    return out

# username search
USERNAME_SITES = [
    ("GitHub", "https://github.com/{u}"), ("GitLab", "https://gitlab.com/{u}"),
    ("Bitbucket", "https://bitbucket.org/{u}"), ("Twitter/X", "https://twitter.com/{u}"),
    ("Instagram", "https://www.instagram.com/{u}"), ("Facebook", "https://www.facebook.com/{u}"),
    ("YouTube", "https://www.youtube.com/@{u}"), ("Reddit", "https://www.reddit.com/user/{u}"),
    ("TikTok", "https://www.tiktok.com/@{u}"), ("LinkedIn", "https://www.linkedin.com/in/{u}"),
    ("Twitch", "https://www.twitch.tv/{u}"), ("Pinterest", "https://www.pinterest.com/{u}"),
    ("Tumblr", "https://{u}.tumblr.com"), ("Medium", "https://medium.com/@{u}"),
    ("Flickr", "https://www.flickr.com/people/{u}"), ("Vimeo", "https://vimeo.com/{u}"),
    ("Dribbble", "https://dribbble.com/{u}"), ("Behance", "https://www.behance.net/{u}"),
    ("DeviantArt", "https://www.deviantart.com/{u}"), ("ArtStation", "https://www.artstation.com/{u}"),
    ("SoundCloud", "https://soundcloud.com/{u}"), ("Spotify", "https://open.spotify.com/user/{u}"),
    ("Bandcamp", "https://{u}.bandcamp.com"), ("Steam", "https://steamcommunity.com/id/{u}"),
    ("Roblox", "https://www.roblox.com/user.aspx?username={u}"),
    ("Keybase", "https://keybase.io/{u}"), ("HackerNews", "https://news.ycombinator.com/user?id={u}"),
    ("CodePen", "https://codepen.io/{u}"), ("Replit", "https://replit.com/@{u}"),
    ("Kaggle", "https://www.kaggle.com/{u}"), ("ProductHunt", "https://www.producthunt.com/@{u}"),
    ("Patreon", "https://www.patreon.com/{u}"), ("Fiverr", "https://www.fiverr.com/{u}"),
    ("Upwork", "https://www.upwork.com/freelancers/{u}"), ("Wellfound", "https://wellfound.com/u/{u}"),
    ("Blogspot", "https://{u}.blogspot.com"), ("WordPress", "https://{u}.wordpress.com"),
    ("Chess.com", "https://www.chess.com/member/{u}"), ("Duolingo", "https://www.duolingo.com/profile/{u}"),
    ("Goodreads", "https://www.goodreads.com/{u}"), ("Letterboxd", "https://letterboxd.com/{u}"),
    ("MyAnimeList", "https://myanimelist.net/profile/{u}"), ("Strava", "https://www.strava.com/athletes/{u}"),
    ("VK", "https://vk.com/{u}"), ("Telegram", "https://t.me/{u}"),
    ("npm", "https://www.npmjs.com/~{u}"), ("PyPI", "https://pypi.org/user/{u}"),
    ("DockerHub", "https://hub.docker.com/u/{u}"), ("Gravatar", "https://gravatar.com/{u}"),
]

def _check_site(name, url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return name, url, resp.status
    except urllib.error.HTTPError as e:
        return name, url, e.code
    except Exception:
        return name, url, "error"

def osint_username(username):
    username = (username or "").strip()
    if not re.match(r"^[A-Za-z0-9_.\-]{2,40}$", username):
        return {"error": "Usernames should be 2-40 chars (letters, numbers, . _ -)."}
    urls = {name: url.replace("{u}", urllib.parse.quote(username)) for name, url in USERNAME_SITES}
    found, not_found, errors = [], [], []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(_check_site, name, u) for name, u in urls.items()]
        for f in as_completed(futs):
            name, url, status = f.result()
            if status == 200:
                found.append({"platform": name, "url": url})
            elif status in (404, 410):
                not_found.append(name)
            else:
                errors.append({"platform": name, "status": status})
    found.sort(key=lambda x: x["platform"].lower())
    return {"username": username, "found": found, "not_found": sorted(not_found),
            "errors": errors,
            "note": "'Found' = profile URL returned 200 (some sites soft-404; verify by clicking)."}

# ---------------------------------------------------------------- weather

WEATHER_CODES = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 71: "Light snow", 73: "Snow",
    75: "Heavy snow", 80: "Light showers", 81: "Showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ hail"}


def _num(v):
    try:
        return float(v)
    except Exception:
        return None


def _weather_wttr(city, lat, lon):
    """Fallback weather provider (wttr.in, no key) mapped to the app's shape.
    Prefers precise coordinates (passed from open-meteo geocoding); only falls
    back to a name query when no coordinates are available."""
    q = None
    if lat is not None and lon is not None:
        q = f"{lat},{lon}"
    elif (city or "").strip():
        q = urllib.parse.quote((city or "").strip())
    if not q:
        return None
    try:
        _, raw, _ = http_fetch(f"https://wttr.in/{q}?format=j1", timeout=20)
        d = json.loads(raw)
        cur = (d.get("current_condition") or [{}])[0]
        area = (d.get("nearest_area") or [{}])[0]
        name = ((area.get("areaName") or [{}])[0].get("value") if area.get("areaName") else "") or (city or "")
        country = ((area.get("country") or [{}])[0].get("value") if area.get("country") else "")
        forecast = []
        for wd in (d.get("weather") or []):
            hr = (wd.get("hourly") or [{}])[0]
            forecast.append({
                "date": wd.get("date"),
                "max_c": _num(wd.get("maxtempC")),
                "min_c": _num(wd.get("mintempC")),
                "condition": ((hr.get("weatherDesc") or [{}])[0].get("value") or "").strip(),
                "precip_prob": None,
            })
        cur_cond = ((cur.get("weatherDesc") or [{}])[0].get("value") or "").strip()
        return {"location": f"{name}, {country}".strip(", "),
                "latitude": lat, "longitude": lon,
                "current": {"temperature_c": _num(cur.get("temp_C")),
                            "feels_like_c": _num(cur.get("FeelsLikeC")),
                            "humidity_pct": _num(cur.get("humidity")),
                            "wind_kmh": _num(cur.get("windspeedKmph")),
                            "condition": cur_cond},
                "today": forecast[0] if forecast else {},
                "forecast": forecast,
                "global": True}
    except Exception:
        return None


def weather(city=None, lat=None, lon=None):
    # Works globally: any city or country name, or direct coordinates.
    # Uses open-meteo first; on any failure (e.g. rate-limiting on shared
    # hosting IPs) it falls back to wttr.in so weather never breaks.
    try:
        if lat is not None and lon is not None:
            try:
                lat, lon = float(lat), float(lon)
            except Exception:
                return {"error": "Invalid coordinates."}
            name, country = "Your location", ""
        else:
            city = (city or "").strip()
            if not city:
                return {"error": "Provide a city or country, e.g. Lagos, London, Japan"}
            url = ("https://geocoding-api.open-meteo.com/v1/search?name="
                   + urllib.parse.quote(city) + "&count=1&language=en&format=json")
            _, raw, _ = http_fetch(url, timeout=15)
            geo = json.loads(raw)
            res = (geo.get("results") or [None])[0]
            if not res:
                return {"error": f"Place '{city}' not found."}
            lat, lon = res["latitude"], res["longitude"]
            name, country = res.get("name", city), res.get("country", "")
        url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
               "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
               "weather_code,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min,"
               "weather_code,precipitation_probability_max&forecast_days=7&timezone=auto"
               % (lat, lon))
        _, raw, _ = http_fetch(url, timeout=20)
        f = json.loads(raw)
        cur, daily = f.get("current") or {}, f.get("daily") or {}
        days = daily.get("time") or []
        forecast = []
        for i, d in enumerate(days):
            forecast.append({
                "date": d,
                "max_c": (daily.get("temperature_2m_max") or [None] * len(days))[i],
                "min_c": (daily.get("temperature_2m_min") or [None] * len(days))[i],
                "condition": WEATHER_CODES.get((daily.get("weather_code") or [None] * len(days))[i], "Unknown"),
                "precip_prob": (daily.get("precipitation_probability_max") or [None] * len(days))[i],
            })
        loc = name if not country else f"{name}, {country}"
        return {"location": loc, "latitude": lat, "longitude": lon,
                "current": {"temperature_c": cur.get("temperature_2m"),
                            "feels_like_c": cur.get("apparent_temperature"),
                            "humidity_pct": cur.get("relative_humidity_2m"),
                            "wind_kmh": cur.get("wind_speed_10m"),
                            "condition": WEATHER_CODES.get(cur.get("weather_code"), "Unknown")},
                "today": forecast[0] if forecast else {},
                "forecast": forecast,
                "global": True}
    except Exception as e:
        fb = _weather_wttr(city, lat, lon)
        if fb:
            return fb
        return {"error": f"Weather lookup failed: {e}"}

# ---------------------------------------------------------------- markets (free)

def market_stock(symbol):
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return {"error": "Provide a stock symbol, e.g. AAPL"}
    k = key("FINNHUB_API_KEY")
    if not k:
        return {"error": "Finnhub key not configured."}
    out = {"symbol": symbol}
    try:
        _, raw, _ = http_fetch(f"https://finnhub.io/api/v1/quote?symbol={urllib.parse.quote(symbol)}&token={k}", timeout=20)
        out["quote"] = json.loads(raw)
    except Exception as e:
        out["quote"] = {"error": str(e)}
    try:
        _, raw, _ = http_fetch(f"https://finnhub.io/api/v1/stock/profile2?symbol={urllib.parse.quote(symbol)}&token={k}", timeout=20)
        out["profile"] = json.loads(raw)
    except Exception:
        out["profile"] = {}
    # Honest coverage: the feed covers US/global markets only — Nigerian
    # Exchange tickers come back empty, and we say so instead of a fake 0.00.
    try:
        _px = float((out.get("quote") or {}).get("c") or 0)
    except Exception:
        _px = 0
    if _px <= 0:
        out["coverage"] = "US/global only (Finnhub)"
        out["note"] = ("No live price for " + symbol + " on the configured feed. Finnhub covers US/global "
                       "markets — it does NOT include the Nigerian Exchange (NGX) or other African exchanges, "
                       "so NGX tickers like DANGCEM/GTCO/MTNN cannot be quoted here. Use the NGX official site "
                       "or a licensed NGX data provider for those; crypto, FX and US equities work normally.")
    return out

def market_crypto(coin_id):
    k = key("COINGECKO_API_KEY")
    hdrs = {"x-cg-demo-api-key": k} if k else {}
    if coin_id and coin_id.strip():
        ids = coin_id.strip().lower()
    else:
        ids = "bitcoin,ethereum,solana,bnb,xrp,cardano,dogecoin,tron,avalanche-2,chainlink"
    try:
        url = ("https://api.coingecko.com/api/v3/simple/price?ids=" + urllib.parse.quote(ids)
               + "&vs_currencies=usd,ngn&include_24hr_change=true&include_market_cap=true")
        _, raw, _ = http_fetch(url, headers=hdrs, timeout=20)
        return {"prices": json.loads(raw)}
    except Exception as e:
        return {"error": f"CoinGecko failed: {e}"}

def market_fred(series_id):
    series_id = (series_id or "").strip().upper()
    if not series_id:
        return {"error": "Provide a FRED series id, e.g. GDP, CPIAUCSL, DGS10"}
    k = key("FRED_API_KEY")
    if not k:
        return {"error": "FRED key not configured."}
    try:
        url = ("https://api.stlouisfed.org/fred/series/observations?series_id="
               + urllib.parse.quote(series_id) + f"&api_key={k}&file_type=json&sort_order=desc&limit=5")
        _, raw, _ = http_fetch(url, timeout=20)
        obs = json.loads(raw).get("observations", [])
        url2 = ("https://api.stlouisfed.org/fred/series?series_id="
                + urllib.parse.quote(series_id) + f"&api_key={k}&file_type=json")
        _, raw2, _ = http_fetch(url2, timeout=20)
        meta = (json.loads(raw2).get("seriess") or [{}])[0]
        return {"series_id": series_id, "title": meta.get("title"),
                "units": meta.get("units"), "frequency": meta.get("frequency"),
                "observations": obs}
    except Exception as e:
        return {"error": f"FRED failed: {e}"}

# ---------------------------------------------------------------- trading (paper)

TRADING_SYMBOLS = {
    "AAPL": {"type": "stock", "finnhub": "AAPL", "name": "Apple Inc."},
    "TSLA": {"type": "stock", "finnhub": "TSLA", "name": "Tesla Inc."},
    "NVDA": {"type": "stock", "finnhub": "NVDA", "name": "NVIDIA Corp."},
    "MSFT": {"type": "stock", "finnhub": "MSFT", "name": "Microsoft Corp."},
    "AMZN": {"type": "stock", "finnhub": "AMZN", "name": "Amazon.com Inc."},
    "GOOGL": {"type": "stock", "finnhub": "GOOGL", "name": "Alphabet Inc."},
    "META": {"type": "stock", "finnhub": "META", "name": "Meta Platforms"},
    "NFLX": {"type": "stock", "finnhub": "NFLX", "name": "Netflix Inc."},
    "AMD": {"type": "stock", "finnhub": "AMD", "name": "Advanced Micro Devices"},
    "INTC": {"type": "stock", "finnhub": "INTC", "name": "Intel Corp."},
    "JPM": {"type": "stock", "finnhub": "JPM", "name": "JPMorgan Chase"},
    "V": {"type": "stock", "finnhub": "V", "name": "Visa Inc."},
    "DIS": {"type": "stock", "finnhub": "DIS", "name": "Walt Disney Co."},
    "KO": {"type": "stock", "finnhub": "KO", "name": "Coca-Cola Co."},
    "PFE": {"type": "stock", "finnhub": "PFE", "name": "Pfizer Inc."},
    "WMT": {"type": "stock", "finnhub": "WMT", "name": "Walmart Inc."},
    "XOM": {"type": "stock", "finnhub": "XOM", "name": "Exxon Mobil"},
    "JNJ": {"type": "stock", "finnhub": "JNJ", "name": "Johnson & Johnson"},
    "BA": {"type": "stock", "finnhub": "BA", "name": "Boeing Co."},
    "NKE": {"type": "stock", "finnhub": "NKE", "name": "Nike Inc."},
    "SPY": {"type": "stock", "finnhub": "SPY", "name": "S&P 500 ETF"},
    "QQQ": {"type": "stock", "finnhub": "QQQ", "name": "Nasdaq-100 ETF"},
    "BTC": {"type": "crypto", "cg": "bitcoin", "name": "Bitcoin"},
    "ETH": {"type": "crypto", "cg": "ethereum", "name": "Ethereum"},
    "SOL": {"type": "crypto", "cg": "solana", "name": "Solana"},
    "BNB": {"type": "crypto", "cg": "binancecoin", "name": "BNB"},
    "XRP": {"type": "crypto", "cg": "ripple", "name": "XRP"},
    "DOGE": {"type": "crypto", "cg": "dogecoin", "name": "Dogecoin"},
    "ADA": {"type": "crypto", "cg": "cardano", "name": "Cardano"},
    "LINK": {"type": "crypto", "cg": "chainlink", "name": "Chainlink"},
    "DOT": {"type": "crypto", "cg": "polkadot", "name": "Polkadot"},
    "LTC": {"type": "crypto", "cg": "litecoin", "name": "Litecoin"},
    "AVAX": {"type": "crypto", "cg": "avalanche-2", "name": "Avalanche"},
    "TRX": {"type": "crypto", "cg": "tron", "name": "TRON"},
    "SHIB": {"type": "crypto", "cg": "shiba-inu", "name": "Shiba Inu"},
    "UNI": {"type": "crypto", "cg": "uniswap", "name": "Uniswap"},
    "ATOM": {"type": "crypto", "cg": "cosmos", "name": "Cosmos"},
    "NEAR": {"type": "crypto", "cg": "near", "name": "NEAR Protocol"},
    "MATIC": {"type": "crypto", "cg": "matic-network", "name": "Polygon"},
}
PAPER_INITIAL = 100000.0

_paper_lock = threading.Lock()
AUTO = {"enabled": False, "interval_min": 10, "watchlist": ["BTC", "ETH"],
        "budget_pct": 10, "thread": None, "last": None, "log": [], "owner": "guest"}


def _default_provider():
    auto = KEYS.get("BRAIN_PROVIDER", "openai")
    if auto == "groq" and key("GROQ_API_KEY"):
        return key("GROQ_API_KEY"), "https://api.groq.com/openai/v1", KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL)
    if key("OPENAI_API_KEY"):
        return key("OPENAI_API_KEY"), "https://api.openai.com/v1", "gpt-4o-mini"
    if key("GROQ_API_KEY"):
        return key("GROQ_API_KEY"), "https://api.groq.com/openai/v1", KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL)
    return "", "", ""


def llm_complete(messages, temperature=0.4, max_tokens=800):
    api_key, base_url, model = _default_provider()
    if not api_key:
        return None, "No AI provider configured (keys.json)."
    url = base_url + "/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens}
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
               "User-Agent": UA}
    try:
        _, raw, _ = http_fetch(url, method="POST", headers=headers, json_body=payload, timeout=90)
        data = json.loads(raw)
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", ""), None
    except urllib.error.HTTPError as e:
        return None, f"AI provider error {e.code}: {e.read().decode('utf-8','replace')[:200]}"
    except Exception as e:
        return None, str(e)


def stock_candles(symbol, days=60):
    k = key("FINNHUB_API_KEY")
    if not k:
        return None
    to = int(time.time()); frm = to - days * 86400
    url = ("https://finnhub.io/api/v1/stock/candle?symbol=" + urllib.parse.quote(symbol)
           + f"&resolution=D&from={frm}&to={to}&token={k}")
    try:
        _, raw, _ = http_fetch(url, timeout=20)
        d = json.loads(raw)
        if d.get("s") != "ok":
            return None
        return d.get("c", [])
    except Exception:
        return None


def crypto_prices(cg_id, days=60):
    url = ("https://api.coingecko.com/api/v3/coins/" + urllib.parse.quote(cg_id)
           + f"/market_chart?vs_currency=usd&days={days}")
    hdrs = {"x-cg-demo-api-key": key("COINGECKO_API_KEY")} if key("COINGECKO_API_KEY") else {}
    try:
        _, raw, _ = http_fetch(url, headers=hdrs, timeout=20)
        return [p[1] for p in json.loads(raw).get("prices", [])]
    except Exception:
        return None


def get_closes(symbol, days=60):
    info = TRADING_SYMBOLS.get((symbol or "").upper())
    if not info:
        return None
    if info["type"] == "stock":
        return stock_candles(info["finnhub"], days)
    return crypto_prices(info["cg"], days)


def compute_indicators(closes):
    if not closes or len(closes) < 30:
        return None
    closes = [c for c in closes if c][-70:]
    if len(closes) < 30:
        return None
    def sma(n):
        if len(closes) < n:
            return None
        return sum(closes[-n:]) / n
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0)); losses.append(max(-ch, 0.0))
    ag = sum(gains[-14:]) / 14.0; al = sum(losses[-14:]) / 14.0
    rsi = 100.0 if al == 0 else (50.0 if (ag + al) == 0 else 100 - 100 / (1 + ag / al))
    s20, s50 = sma(20), sma(50)
    trend = "up" if (s20 and s50 and s20 > s50) else ("down" if (s20 and s50) else "neutral")
    out = {"last": round(closes[-1], 4), "sma20": round(s20, 4) if s20 else None,
           "sma50": round(s50, 4) if s50 else None, "rsi14": round(rsi, 2), "trend": trend,
           "change_1d_pct": round((closes[-1] / closes[-2] - 1) * 100, 2) if len(closes) >= 2 else None,
           "change_7d_pct": round((closes[-1] / closes[-8] - 1) * 100, 2) if len(closes) >= 8 else None}
    return out


def paper_quote(symbol):
    info = TRADING_SYMBOLS.get((symbol or "").upper())
    if not info:
        return {"error": "Unknown symbol. Use AAPL, TSLA, BTC, ETH…"}
    try:
        if info["type"] == "stock":
            k = key("FINNHUB_API_KEY")
            if not k:
                return {"error": "Finnhub key missing."}
            _, raw, _ = http_fetch("https://finnhub.io/api/v1/quote?symbol="
                                   + urllib.parse.quote(info["finnhub"]) + "&token=" + k, timeout=15)
            q = json.loads(raw)
            if not q.get("c"):
                return {"error": "No price data for " + symbol}
            return {"symbol": symbol.upper(), "name": info["name"], "type": "stock",
                    "price": q["c"], "currency": "USD", "change_pct": q.get("dp")}
        hdrs = {"x-cg-demo-api-key": key("COINGECKO_API_KEY")} if key("COINGECKO_API_KEY") else {}
        url = ("https://api.coingecko.com/api/v3/simple/price?ids=" + urllib.parse.quote(info["cg"])
               + "&vs_currencies=usd&include_24hr_change=true")
        _, raw, _ = http_fetch(url, headers=hdrs, timeout=15)
        d = json.loads(raw).get(info["cg"]) or {}
        if not d.get("usd"):
            return {"error": "No price data for " + symbol}
        return {"symbol": symbol.upper(), "name": info["name"], "type": "crypto",
                "price": d["usd"], "currency": "USD", "change_pct": d.get("usd_24h_change")}
    except Exception as e:
        return {"error": str(e)}


def _paper_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "paper_accounts.json")


def _load_accounts():
    try:
        with open(_paper_file()) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_accounts(accounts):
    tmp = _paper_file() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(accounts, f, indent=2)
    os.replace(tmp, _paper_file())


def _acct_key(user):
    u = (user or "").strip().lower()
    return u or "guest"


def _fresh_account():
    return {"cash": PAPER_INITIAL, "initial": PAPER_INITIAL, "positions": {},
            "trades": [], "history": [], "started": time.strftime("%Y-%m-%d %H:%M:%S")}


def get_paper(user=None):
    with _paper_lock:
        accounts = _load_accounts()
        k = _acct_key(user)
        if k not in accounts:
            accounts[k] = _fresh_account()
            _save_accounts(accounts)
        return accounts[k]


def reset_paper(user=None):
    with _paper_lock:
        accounts = _load_accounts()
        accounts[_acct_key(user)] = _fresh_account()
        _save_accounts(accounts)
        return {"ok": True}


def _snapshot(acc, equity):
    acc["history"] = (acc.get("history") or [])[-499:]
    acc["history"].append({"ts": time.strftime("%m-%d %H:%M"), "equity": round(equity, 2)})


def paper_order(side, symbol, notional, reason="manual", user=None):
    side = (side or "").lower()
    if side not in ("buy", "sell"):
        return {"error": "side must be buy or sell"}
    sym = (symbol or "").upper()
    if sym not in TRADING_SYMBOLS:
        return {"error": "Unknown symbol."}
    q = paper_quote(sym)
    if "error" in q:
        return q
    price = q["price"]
    with _paper_lock:
        accounts = _load_accounts()
        k = _acct_key(user)
        acc = accounts.get(k) or _fresh_account()
        if side == "buy":
            notional = float(notional or 0)
            if notional <= 0:
                return {"error": "Amount must be > 0"}
            if notional > acc["cash"]:
                return {"error": "Insufficient paper cash."}
            qty = notional / price
            acc["cash"] -= notional
            pos = acc["positions"].get(sym, {"qty": 0.0, "avg": 0.0})
            new_qty = pos["qty"] + qty
            pos["avg"] = ((pos["avg"] * pos["qty"]) + price * qty) / new_qty if new_qty > 0 else price
            pos["qty"] = new_qty
            acc["positions"][sym] = pos
        else:
            pos = acc["positions"].get(sym)
            if not pos or pos["qty"] <= 0:
                return {"error": "No position in " + sym + " to sell."}
            if notional:
                qty = min(float(notional) / price, pos["qty"])
                if qty <= 0:
                    return {"error": "Nothing to sell."}
                pos["qty"] -= qty
                acc["cash"] += qty * price
                if pos["qty"] < 1e-9:
                    del acc["positions"][sym]
                else:
                    acc["positions"][sym] = pos
            else:
                qty = pos["qty"]
                acc["cash"] += qty * price
                del acc["positions"][sym]
        acc["trades"].insert(0, {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "symbol": sym,
                                 "side": side, "qty": round(qty, 8), "price": round(price, 4),
                                 "notional": round(qty * price, 2), "reason": reason})
        acc["trades"] = acc["trades"][:200]
        _snapshot(acc, acc["cash"] + sum(p["qty"] * price for p in acc["positions"].values()))
        accounts[k] = acc
        _save_accounts(accounts)
        return {"ok": True, "order": acc["trades"][0]}


def account_with_quotes(user=None):
    acc = get_paper(user)
    positions, equity = [], acc["cash"]
    for sym, pos in (acc.get("positions") or {}).items():
        q = paper_quote(sym)
        price = q.get("price") if isinstance(q, dict) else None
        value = pos["qty"] * price if price else None
        if value is not None:
            equity += value
        positions.append({"symbol": sym, "qty": round(pos["qty"], 8), "avg": round(pos["avg"], 4),
                          "price": price, "value": round(value, 2) if value is not None else None,
                          "pnl": round(value - pos["qty"] * pos["avg"], 2) if value is not None else None,
                          "pnl_pct": round((value / (pos["qty"] * pos["avg"]) - 1) * 100, 2)
                          if value and pos["avg"] else None})
    pnl = equity - acc["initial"]
    return {"cash": round(acc["cash"], 2), "initial": acc["initial"], "equity": round(equity, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl / acc["initial"] * 100, 2),
            "positions": positions, "trades": (acc.get("trades") or [])[:30],
            "history": (acc.get("history") or [])[-200:],
            "started": acc.get("started")}


def parse_decision(text):
    try:
        m = re.search(r"\{.*\}", text or "", re.S)
        d = json.loads(m.group(0)) if m else {}
        action = str(d.get("action", "HOLD")).upper()
        if action not in ("BUY", "SELL", "HOLD"):
            action = "HOLD"
        try:
            conf = max(0, min(100, int(d.get("confidence", 0))))
        except Exception:
            conf = 0
        return {"action": action, "confidence": conf, "reason": str(d.get("reason", ""))[:300]}
    except Exception:
        return {"action": "HOLD", "confidence": 0, "reason": "(unparseable)"}


def parse_signal(text):
    try:
        m = re.search(r"\{.*\}", text or "", re.S)
        d = json.loads(m.group(0)) if m else {}
        def num(x):
            try:
                return float(x)
            except Exception:
                return None
        conf = 0
        if str(d.get("confidence", "")).lstrip("-").isdigit():
            conf = int(d.get("confidence", 0))
        return {"bias": str(d.get("bias", "neutral")), "action": str(d.get("action", "hold")),
                "entry": num(d.get("entry")), "stop_loss": num(d.get("stop_loss")),
                "take_profit": num(d.get("take_profit")), "confidence": conf,
                "thesis": str(d.get("thesis", ""))[:700], "risks": str(d.get("risks", ""))[:300]}
    except Exception:
        return {"bias": "neutral", "action": "hold", "thesis": "(could not parse signal)", "risks": ""}


SIGNAL_SYS = ("You are OraCool AI's market analyst. Provide ONE educational trade idea "
              "for the given symbol and indicators. Respond with ONLY valid JSON (no markdown): "
              "{\"bias\": \"bullish|bearish|neutral\", \"action\": \"buy|sell|hold\", "
              "\"entry\": number, \"stop_loss\": number, \"take_profit\": number, "
              "\"confidence\": 0-100, \"thesis\": \"2-3 sentences explaining reasoning\", "
              "\"risks\": \"one short risk sentence\"}. entry/stop_loss/take_profit must be "
              "numeric values near the current price. Never guarantee profit.")

AUTO_SYS = ("You are OraCool AI's disciplined algorithmic trading module operating a PAPER "
            "(simulated) account only — no real money. Given a symbol and recent indicators "
            "(last price, sma20, sma50, rsi14, trend, 1-day and 7-day change), decide the single "
            "next action. Respond with ONLY valid JSON, no markdown: "
            "{\"action\": \"BUY\"|\"SELL\"|\"HOLD\", \"confidence\": 0-100, "
            "\"reason\": \"one short sentence\"}. BUY only when momentum looks favorable "
            "(RSI not overbought, trend up); SELL only when momentum deteriorates (RSI "
            "overbought or trend breaking); otherwise HOLD. Be conservative. Never guarantee profit.")


def trade_signal(symbol):
    sym = (symbol or "").upper()
    if sym not in TRADING_SYMBOLS:
        return {"error": "Unknown symbol."}
    q = paper_quote(sym)
    if "error" in q:
        return {"error": q["error"]}
    closes = get_closes(sym, 60)
    ind = compute_indicators(closes) if closes else None
    out = {"symbol": sym, "name": TRADING_SYMBOLS[sym]["name"], "type": TRADING_SYMBOLS[sym]["type"],
           "quote": q, "indicators": ind}
    content, err = llm_complete([{"role": "system", "content": SIGNAL_SYS},
                                 {"role": "user", "content": json.dumps(
                                     {"symbol": sym, "price": q.get("price"), **(ind or {})})}],
                                temperature=0.4, max_tokens=700)
    if err:
        out["error"] = err
        return out
    out["signal"] = parse_signal(content)
    out["disclaimer"] = ("Educational signal from a paper system. Not financial advice; markets "
                         "can move against any idea. Past performance does not guarantee future results.")
    return out


def auto_status():
    return {"enabled": AUTO["enabled"], "interval_min": AUTO["interval_min"],
            "watchlist": AUTO["watchlist"], "budget_pct": AUTO["budget_pct"],
            "last": AUTO["last"], "log": (AUTO["log"] or [])[:20]}


def trade_auto(body):
    enabled = bool(body.get("enabled"))
    owner = _acct_key(body.get("email"))
    try:
        interval = max(3, min(int(body.get("interval_min", 10)), 60))
    except Exception:
        interval = 10
    wl = [str(s).upper() for s in (body.get("watchlist") or ["BTC", "ETH"])
          if str(s).upper() in TRADING_SYMBOLS][:4]
    try:
        budget = max(1, min(int(body.get("budget_pct", 10)), 50))
    except Exception:
        budget = 10
    AUTO["interval_min"] = interval
    AUTO["watchlist"] = wl or ["BTC"]
    AUTO["budget_pct"] = budget
    AUTO["owner"] = owner
    if enabled and not AUTO["enabled"]:
        AUTO["enabled"] = True
        t = threading.Thread(target=auto_loop, daemon=True)
        AUTO["thread"] = t
        t.start()
        AUTO["log"].insert(0, {"ts": time.strftime("%H:%M:%S"), "msg": "Auto-trader started (paper)"})
    elif not enabled:
        AUTO["enabled"] = False
        AUTO["log"].insert(0, {"ts": time.strftime("%H:%M:%S"), "msg": "Auto-trader stopped"})
    return auto_status()


def auto_loop():
    while AUTO["enabled"]:
        for sym in list(AUTO["watchlist"]):
            if not AUTO["enabled"]:
                break
            AUTO["last"] = time.strftime("%H:%M:%S")
            try:
                closes = get_closes(sym, 60)
                ind = compute_indicators(closes) if closes else None
                if not ind:
                    AUTO["log"].insert(0, {"ts": time.strftime("%H:%M:%S"), "msg": f"{sym}: no market data"})
                    continue
                content, err = llm_complete([{"role": "system", "content": AUTO_SYS},
                                             {"role": "user", "content": json.dumps({"symbol": sym, **ind})}],
                                            temperature=0.3, max_tokens=400)
                if err:
                    AUTO["log"].insert(0, {"ts": time.strftime("%H:%M:%S"), "msg": f"{sym}: {err[:80]}"})
                    continue
                dec = parse_decision(content)
                msg = f"{sym}: {dec['action']} (conf {dec['confidence']}) — {dec['reason'][:60]}"
                if dec["action"] in ("BUY", "SELL") and dec["confidence"] >= 55:
                    if dec["action"] == "BUY":
                        acc = get_paper(AUTO.get("owner"))
                        notional = min(acc["cash"], acc["cash"] * AUTO["budget_pct"] / 100.0)
                        if notional > 1:
                            r = paper_order("buy", sym, notional, reason="auto", user=AUTO.get("owner"))
                            msg += " → executed BUY" if r.get("ok") else f" → {r.get('error')}"
                    else:
                        r = paper_order("sell", sym, None, reason="auto", user=AUTO.get("owner"))
                        msg += " → executed SELL" if r.get("ok") else f" → {r.get('error')}"
                AUTO["log"].insert(0, {"ts": time.strftime("%H:%M:%S"), "msg": msg})
            except Exception as e:
                AUTO["log"].insert(0, {"ts": time.strftime("%H:%M:%S"), "msg": f"{sym}: {e}"})
            AUTO["log"] = AUTO["log"][:25]
        for _ in range(AUTO["interval_min"] * 60):
            if not AUTO["enabled"]:
                break
            time.sleep(1)


# ---------------------------------------------------------------- Alpaca paper

def alpaca_request(path, method="GET", json_body=None):
    kid = key("ALPACA_PAPER_KEY_ID"); sec = key("ALPACA_PAPER_SECRET")
    if not kid or not sec:
        return {"error": "Alpaca paper keys not configured. Add ALPACA_PAPER_KEY_ID and "
                         "ALPACA_PAPER_SECRET to keys.json (free at alpaca.markets → Paper)."}
    headers = {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec, "Content-Type": "application/json"}
    try:
        st, raw, _ = http_fetch("https://paper-api.alpaca.markets" + path, method=method,
                                headers=headers, json_body=json_body, timeout=25)
        try:
            data = json.loads(raw)
        except Exception:
            data = raw.decode("utf-8", "replace")[:300]
        return {"status": st, "data": data}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "data": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:
        return {"error": str(e)}


def alpaca_account():
    r = alpaca_request("/v2/account")
    if "error" in r:
        return r
    if r.get("status") == 200:
        a = r["data"]
        return {"ok": True, "cash": a.get("cash"), "equity": a.get("equity"),
                "buying_power": a.get("buying_power"), "status": a.get("status"),
                "currency": a.get("currency"), "long_market_value": a.get("long_market_value")}
    return {"error": "Alpaca error " + str(r.get("status")) + ": " + str(r.get("data"))[:200]}


def alpaca_order(side, symbol, notional):
    side = (side or "").lower()
    if side not in ("buy", "sell"):
        return {"error": "side must be buy or sell"}
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"error": "Provide a symbol."}
    try:
        notional = float(notional or 0)
    except Exception:
        return {"error": "Amount must be numeric."}
    if notional <= 0:
        return {"error": "Amount must be > 0."}
    r = alpaca_request("/v2/orders", method="POST",
                       json_body={"symbol": sym, "notional": str(notional), "side": side,
                                  "type": "market", "time_in_force": "day"})
    if "error" in r:
        return r
    if r.get("status") in (200, 201):
        d = r["data"]
        return {"ok": True, "order": {"id": d.get("id"), "symbol": d.get("symbol"),
                                      "side": d.get("side"), "qty": d.get("qty") or d.get("notional"),
                                      "status": d.get("status"), "type": d.get("type"),
                                      "paper": True}}
    return {"error": "Alpaca error " + str(r.get("status")) + ": " + str(r.get("data"))[:200]}


def alpaca_positions():
    r = alpaca_request("/v2/positions")
    if "error" in r:
        return r
    if r.get("status") == 200:
        pos = [{"symbol": p.get("symbol"), "qty": p.get("qty"), "avg_entry_price": p.get("avg_entry_price"),
                "current_price": p.get("current_price"), "market_value": p.get("market_value"),
                "unrealized_pl": p.get("unrealized_pl"), "unrealized_plpc": p.get("unrealized_plpc")}
               for p in (r["data"] or [])]
        return {"ok": True, "positions": pos}
    return {"error": "Alpaca error " + str(r.get("status")) + ": " + str(r.get("data"))[:200]}


# ---------------------------------------------------------------- admin payload

def admin_users_payload(viewer_email=""):
    users = load_users()
    accounts = _load_accounts()
    subs = load_subscribers()
    sub_emails = {str(s.get("email", "")).lower() for s in subs}
    # highest unexpired paid plan per email (no extra network calls)
    _today = time.strftime("%Y-%m-%d")
    plan_by_email = {}
    for s in subs:
        em = str(s.get("email") or "").lower()
        exp = str(s.get("expires_at") or "")
        if not em or (exp and exp < _today):
            continue
        plan = str(s.get("tier") or s.get("plan") or "pro").lower()
        if plan not in PLANS:
            continue
        if TIER_RANK.get(plan, 0) >= TIER_RANK.get(plan_by_email.get(em), -1):
            plan_by_email[em] = plan
    # Merge in the persistent Supabase user base. Render's local filesystem is
    # ephemeral, so data/users.json alone would show an empty/partial list.
    flags = supabase_all_flags()
    for f in flags.values():
        em = str(f.get("email") or "").lower()
        if not em:
            continue
        if em not in users:
            users[em] = {"created": f.get("created") or "", "last_seen": f.get("last_seen") or "",
                         "blocked": bool(f.get("blocked")), "block_reason": f.get("block_reason") or "",
                         "blocked_by": f.get("blocked_by") or "", "blocked_at": f.get("blocked_at") or ""}
    for su in supabase_auth_users():
        em = (su.get("email") or "").strip().lower()
        if not em:
            continue
        created = (su.get("created_at") or "")[:19].replace("T", " ")
        if em not in users:
            users[em] = {"created": created}
        elif not users[em].get("created"):
            users[em]["created"] = created
    out = []
    for email, rec in users.items():
        acc = accounts.get(email)
        equity = pnl = None
        if acc:
            eq = acc.get("cash", 0.0)
            for sym, pos in (acc.get("positions") or {}).items():
                try:
                    q = paper_quote(sym)
                    price = q.get("price") if isinstance(q, dict) else None
                    if price:
                        eq += pos.get("qty", 0) * price
                except Exception:
                    pass
            equity = round(eq, 2)
            pnl = round(eq - acc.get("initial", PAPER_INITIAL), 2)
        # plan (highest unexpired paid tier from local+supabase subscription rows)
        plan = plan_by_email.get(email, "free")
        if is_admin(email):
            plan = "enterprise"
        out.append({"email": email, "created": rec.get("created"), "last_seen": rec.get("last_seen"),
                    "blocked": bool(rec.get("blocked")), "block_reason": rec.get("block_reason") or "",
                    "admin": is_admin(email), "pro": is_admin(email) or email in sub_emails,
                    "plan": plan, "verified": rec.get("verified") if rec.get("verified") is not None else None,
                    "equity": equity, "pnl": pnl})
    out.sort(key=lambda x: (x.get("pnl") is None, -(x.get("pnl") or 0)))
    stats = {"users": len(out),
             "blocked": sum(1 for u in out if u["blocked"]),
             "pro": sum(1 for u in out if u["pro"]),
             "paid": sum(1 for u in out if u.get("plan") in PLANS),
             "unverified": sum(1 for u in out if u.get("verified") is False),
             "active24h": sum(1 for u in out if _recent(u.get("last_seen"), 86400)),
             "new24h": sum(1 for u in out if _recent(u.get("created"), 86400)),
             "total_pnl": round(sum(u.get("pnl") or 0 for u in out), 2)}
    # Creator-identity confidentiality: nobody except the creator's own AI/panel
    # may see the creator's email or know which account is the creator.
    _alist = admin_emails()
    _creator = (_alist[0] or "").lower() if _alist else ""
    _viewer = (viewer_email or "").strip().lower()
    if _creator and _viewer != _creator:
        admins = []
        for a in _alist:
            admins.append("••••• (creator account)" if a.lower() == _creator else a)
        for u in out:
            if (u.get("email") or "").lower() == _creator:
                u["email"] = "••••• (creator account)"
                u["plan"] = "enterprise"
                u["pro"] = True
        out = sorted(out, key=lambda x: (x.get("pnl") is None, -(x.get("pnl") or 0)))
        return {"stats": stats, "users": out, "admins": admins}
    return {"stats": stats, "users": out, "admins": _alist}


def _recent(ts, max_age_s):
    """True if a '%Y-%m-%d %H:%M:%S' timestamp is within max_age_s of now."""
    if not ts:
        return False
    try:
        t = time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S"))
        return (time.time() - t) <= max_age_s
    except Exception:
        return False


def admin_revenue_payload():
    """Every payment OraCool has ever earned — totals by plan, MRR and history.
    Combines local subscribers.json with the durable Supabase table."""
    subs = load_subscribers()
    seen = {str(s.get("reference") or "") for s in subs}
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if url and svc:
        try:
            _, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/subscribers?select=*&order=subscribed_at.desc&limit=500",
                                   headers={"apikey": svc, "Authorization": "Bearer " + svc}, timeout=15)
            for row in (json.loads(raw) or []):
                if str(row.get("reference") or "") not in seen:
                    subs.append(row)
        except Exception:
            pass
    seen_refs = set()
    payments = []
    total_ngn = 0.0
    usd_real = 0.0
    have_usd = False
    this_month = time.strftime("%Y-%m")
    month_ngn = 0.0
    by_plan = {}
    for s in subs:
        ref = str(s.get("reference") or "")
        if ref and ref in seen_refs:
            continue
        seen_refs.add(ref)
        amt = 0.0
        try:
            amt = float(s.get("amount_ngn") or 0)
        except Exception:
            amt = 0.0
        plan = str(s.get("tier") or s.get("plan") or "pro").lower()
        if plan not in PLANS:
            plan = "pro"
        paid_at = str(s.get("paid_at") or s.get("subscribed_at") or "")
        row = {"email": s.get("email"), "plan": plan, "amount_ngn": amt,
               "amount_usd": s.get("amount_usd"), "paid_at": paid_at,
               "channel": s.get("channel") or "", "reference": ref,
               "expires_at": s.get("expires_at") or ""}
        if amt > 0:
            payments.append(row)
            total_ngn += amt
            usd = s.get("amount_usd")
            try:
                if usd:
                    usd_real += float(usd)
                    have_usd = True
            except Exception:
                pass
            if paid_at.startswith(this_month):
                month_ngn += amt
            by_plan[plan] = by_plan.get(plan, 0) + 1
    payments.sort(key=lambda x: x["paid_at"], reverse=True)
    total_usd = usd_real if have_usd else (round(total_ngn / 1550.0) if total_ngn else 0.0)
    # MRR + active subscribers: latest unexpired subscription per email, best plan
    today = time.strftime("%Y-%m-%d")
    best_by_email = {}
    for s in subs:
        em = str(s.get("email") or "").lower()
        if not em or str(s.get("channel") or "") == "admin":
            continue  # admin grants are access, not revenue
        exp = str(s.get("expires_at") or "")
        if exp and exp < today:
            continue
        plan = str(s.get("tier") or s.get("plan") or "pro").lower()
        if plan not in PLANS:
            continue
        if TIER_RANK.get(plan, 0) > TIER_RANK.get(best_by_email.get(em), -1):
            best_by_email[em] = plan
    mrr_ngn = 0.0
    active_by_plan = {}
    for em, plan in best_by_email.items():
        if em in {str(x.get("email") or "").lower() for x in load_subscribers() if str(x.get("channel") or "") == "admin"}:
            pass  # admin grants count as active but add no money
        mrr_ngn += PLANS[plan]["price_ngn"]
        active_by_plan[plan] = active_by_plan.get(plan, 0) + 1
    return {"total_ngn": round(total_ngn), "total_usd": round(total_usd, 2),
            "usd_estimated": not have_usd,
            "this_month_ngn": round(month_ngn),
            "payments": len(payments), "by_plan": by_plan,
            "active_subscribers": len(best_by_email), "active_by_plan": active_by_plan,
            "mrr_ngn": round(mrr_ngn), "recent": payments[:25],
            "fines": fines_summary(),
            "note": "Revenue = only real Paystack payments (₦). Admin grants are listed as active but never counted as money. Reinstatement fines are listed separately."}


def mask_email(e):
    try:
        name, dom = e.split("@", 1)
        return (name[0] + "***@" + dom) if name else e
    except Exception:
        return e


def leaderboard_payload():
    accounts = _load_accounts()
    users = load_users()
    out = []
    for email, acc in accounts.items():
        if email == "guest":
            continue
        eq = acc.get("cash", 0.0)
        for sym, pos in (acc.get("positions") or {}).items():
            try:
                q = paper_quote(sym)
                price = q.get("price") if isinstance(q, dict) else None
                if price:
                    eq += pos.get("qty", 0) * price
            except Exception:
                pass
        pnl = round(eq - acc.get("initial", PAPER_INITIAL), 2)
        out.append({"email": mask_email(email), "pnl": pnl, "equity": round(eq, 2),
                    "pct": round(pnl / PAPER_INITIAL * 100, 2),
                    "trades": len(acc.get("trades") or [])})
    out.sort(key=lambda x: -(x["pnl"] or 0))
    return {"leaderboard": out[:20], "note": "Paper (simulated) trading only — not real money."}


def fines_summary():
    rows = list(_fines_load().values())
    return {"count": len(rows), "usd": FINE_USD * len(rows),
            "recent": sorted(rows, key=lambda r: str(r.get("paid_at") or ""), reverse=True)[:10]}


def _require_admin(self, body):
    payload = self._auth(body)
    if not payload:
        self._send_json({"locked": True, "message": "Admin access required."}, 403)
        return None
    if payload.get("admin") or is_admin(payload.get("sub")):
        return payload
    self._send_json({"locked": True, "message": "Admin access required."}, 403)
    return None


# ---------------------------------------------------------------- media generation (HiAPI + Pixazo)

HIA_BASE = "https://api.hiapi.ai"
HIA_IMAGE_DEFAULT = "gpt-image-2/text-to-image"
HIA_VIDEO_DEFAULT = "seedance-2.0-fast"


def _err_text(raw):
    """Best-effort human-readable error string from a provider's JSON error body."""
    try:
        d = json.loads(raw)
        err = d.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        return str(err or d.get("message") or d.get("error_code") or "request failed")[:180]
    except Exception:
        return str(raw)[:140]


def hiapi_submit(model, input_obj, api_key, timeout=30):
    """Submit an async generation task. Returns (taskId, error). The error is a
    human string (e.g. 'HTTP 402 — insufficient account balance; top up and retry')
    so the AI can tell the user exactly what to fix — never a silent failure."""
    try:
        st, raw, _ = http_fetch(HIA_BASE + "/v1/tasks", method="POST",
                                headers={"Authorization": "Bearer " + api_key,
                                         "Content-Type": "application/json"},
                                json_body={"model": model, "input": input_obj},
                                timeout=timeout)
    except urllib.error.HTTPError as e:
        return None, "HTTP %s — %s" % (e.code, _err_text(e.read().decode("utf-8", "replace")))
    except Exception as e:
        return None, str(e)[:160]
    try:
        d = json.loads(raw)
    except Exception:
        return None, "non-JSON response"
    return ((d.get("data") or {}).get("taskId") or d.get("taskId")), None


def hiapi_poll(task_id, api_key, budget=90):
    """Poll a task until success/fail. Returns dict with urls on success."""
    start = time.time()
    delay = 2.0
    while time.time() - start < budget:
        try:
            st, raw, _ = http_fetch(HIA_BASE + "/v1/tasks/" + urllib.parse.quote(str(task_id)),
                                    headers={"Authorization": "Bearer " + api_key}, timeout=20)
            d = json.loads(raw)
        except Exception:
            time.sleep(delay)
            delay = min(delay * 1.5, 8)
            continue
        data = d.get("data") or {}
        status = data.get("status") or d.get("status")
        if status in ("success", "succeeded", "complete", "completed"):
            outs = data.get("output") or []
            urls = []
            for o in outs:
                u = o.get("url") if isinstance(o, dict) else str(o)
                if u:
                    urls.append(u)
            return {"ok": True, "urls": urls, "status": status}
        if status in ("fail", "failed", "error", "cancelled"):
            return {"ok": False, "error": (data.get("error") or data.get("message") or "Generation failed")[:200]}
        time.sleep(delay)
        delay = min(delay * 1.5, 8)
    return {"ok": False, "error": "Generation timed out (still processing — try again)."}


def _tokenmix_image(prompt, aspect_ratio="1:1"):
    """TokenMix OpenAI-compatible image endpoint. Returns a dict, never raises."""
    k = key("TOKENMIX_API_KEY")
    if not k:
        return {"skip": True}
    model = KEYS.get("TM_IMAGE_MODEL", "gpt-image-1-mini")
    size = {"16:9": "1536x1024", "9:16": "1024x1536",
            "3:4": "1024x1536", "4:3": "1536x1024"}.get(aspect_ratio, "1024x1024")
    try:
        st, raw, _ = http_fetch("https://api.tokenmix.ai/v1/images/generations", method="POST",
                                headers={"Authorization": "Bearer " + k},
                                json_body={"model": model, "prompt": prompt, "n": 1,
                                           "size": size, "response_format": "url"},
                                timeout=150)
        d = json.loads(raw)
    except urllib.error.HTTPError as e:
        return {"error": _err_text(e.read().decode("utf-8", "replace"))}
    except Exception as e:
        return {"error": str(e)[:160]}
    urls = [u.get("url") for u in (d.get("data") or []) if isinstance(u, dict) and u.get("url")]
    if urls:
        return {"ok": True, "model": model, "urls": urls}
    return {"error": "no image url returned"}


POLLINATION_SIZES = {"1:1": (1024, 1024), "16:9": (1280, 720), "9:16": (720, 1280),
                     "3:4": (960, 1280), "4:3": (1280, 960)}


def _pollinations_image(prompt, aspect_ratio="1:1"):
    """OraCool FREE HD image engine (Pollinations · FLUX). No key, no balance —
    this is why image creation ALWAYS works, even if every paid provider is
    topped-out. The URL renders directly in chat and in the Create tab."""
    try:
        w, h = POLLINATION_SIZES.get(aspect_ratio, (1024, 1024))
        seed = random.randint(1000, 999999)
        url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:420])
               + f"?width={w}&height={h}&nologo=true&model=flux&seed={seed}&enhance=true")
        return {"ok": True, "url": url}
    except Exception as e:
        return {"error": str(e)[:160]}


def _nexa_image(prompt, aspect_ratio="1:1"):
    """NexaAPI (api.nexawapi.com) — OpenAI-compatible gateway, the creator's primary
    media key. Needs account balance; failures surface as readable errors."""
    k = key("NEXAAPI_KEY")
    if not k:
        return {"skip": True}
    model = KEYS.get("NEXAAPI_IMAGE_MODEL", "gpt-image-2")
    size = {"16:9": "1536x1024", "9:16": "1024x1536",
            "3:4": "1024x1536", "4:3": "1536x1024"}.get(aspect_ratio, "1024x1024")
    try:
        st, raw, _ = http_fetch("https://api.nexawapi.com/v1/images/generations", method="POST",
                                headers={"Authorization": "Bearer " + k},
                                json_body={"model": model, "prompt": prompt, "n": 1,
                                           "size": size, "response_format": "url"},
                                timeout=280)
        d = json.loads(raw)
    except urllib.error.HTTPError as e:
        return {"error": _err_text(e.read().decode("utf-8", "replace"))}
    except Exception as e:
        return {"error": str(e)[:160]}
    urls = [u.get("url") for u in (d.get("data") or []) if isinstance(u, dict) and u.get("url")]
    if urls:
        return {"ok": True, "model": model, "urls": urls}
    return {"error": "no image url returned"}


CVRON = "https://cvron.alwaysdata.net"


PRIVACY_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>OraCool AI — Privacy & Data Storage</title>
<style>body{background:#020d1e;color:#cfe9ff;font-family:'Segoe UI',system-ui,sans-serif;max-width:820px;margin:40px auto;padding:0 20px;line-height:1.7}
h1{color:#5ef0ff;letter-spacing:3px;font-size:22px}h2{color:#ffc24b;font-size:15px;letter-spacing:2px;margin-top:26px}
code{background:rgba(0,229,255,.08);padding:1px 6px;border-radius:5px}li{margin:6px 0}small{color:#6fa0b8}</style></head><body>
<h1>◈ ORACOOL AI — PRIVACY &amp; DATA STORAGE</h1><p><small>Version 2.0 · Effective on deploy · Contact: danielonakoya19@gmail.com</small></p>
<h2>1. DATA RETENTION</h2><ul>
<li><b>Chats and media:</b> signed-in chat history is stored on the server and mirrored to the operator-controlled Supabase database when configured. The browser also caches recent messages. Generated media and gallery records are stored on server disk; download important files and use a persistent disk for redeployment. Connected account credentials are encrypted at rest. Admin mailbox checks read unread counts and message headers, not message bodies.</li>
<li><b>Queries:</b> investigative queries are executed by the OraCool server at query time. Raw query text is retained only in the audit trail metadata (endpoint name, actor, timestamp) — the <i>content</i> of OSINT lookups is not persisted unless the investigator explicitly saves it into a Case as evidence.</li>
<li><b>Case files &amp; evidence:</b> stored in the operator's own deployment database (Supabase project controlled by the OraCool operator). Never shared with, sold to, or used to train any third party.</li>
<li><b>Deletion on demand:</b> users may delete a case or artifact at any time; deletion removes the active platform record; provider copies and database backups may have separate retention policies. Accounts can be removed by the operator on verified request.</li></ul>
<h2>2. AI MODEL TRAINING</h2><ul>
<li>Prompts and tool results required for a request may be sent to the configured AI provider. Provider retention and training terms vary; OraCool does not claim universal zero retention. Do not submit passwords or API tokens in chat. External demo links have their own privacy policies and do not automatically sync results to OraCool.</li></ul>
<h2>3. ATTRIBUTION (OPSEC)</h2><ul>
<li>All OSINT, dark-web index and monitoring requests are issued <b>server-side</b> from the platform's own infrastructure. The investigator's personal IP address, device and home network are never exposed to the sources being queried.</li>
<li>OraCool only reads public search indexes and public databases. It never connects to .onion sites directly, never uses stolen credentials, never purchases illicit data, and never performs active scanning of third-party systems.</li></ul>
<h2>4. EVIDENCE INTEGRITY</h2><ul>
<li>Every preserved artifact receives a <b>SHA-256 fingerprint at collection time</b> and an append-only <b>chain-of-custody log</b> (who, when, what). Integrity re-verification recomputes the hash on demand. Exported case documents include the full custody chain and a case fingerprint. Workflows align with the <b>Berkeley Protocol</b> on Digital Open Source Investigations (lawfulness, harm minimisation, safe preservation, transparency, verifiable records). These records support authenticity demonstration; they are not a notarisation service.</li></ul>
<h2>5. LIMITS &amp; LAWFUL USE</h2><ul>
<li>Verification outputs (including any future document-verification integrations such as SEON/Kinegram/Kairos) are <b>indicators that require further review</b>, never definitive verdicts. No feature may be used for unlawful surveillance, harassment, or access to systems/accounts without authorisation. In jurisdictions without PI licensing frameworks (e.g. Nigeria), users are responsible for lawful registration/permits where applicable.</li></ul>
<p><small>© OraCool AI. This policy is served from <code>/privacy</code> and versioned with the platform.</small></p>
</body></html>"""


def _cvron_image(prompt):
    """CVRON free image APIs — flux-dev first, r000n (2 images) as backup.
    No key required. Verified live: returns real hosted image URLs."""
    q = urllib.parse.quote(prompt[:300])
    last = "cvron: no response"
    for ep, key_field in (("flux-dev.php", "image_url"), ("r000n-image.php", None)):
        try:
            _, raw, _ = http_fetch(f"{CVRON}/cvronai/{ep}?prompt={q}", timeout=150,
                                   headers={"User-Agent": "Mozilla/5.0"})
            d = json.loads(raw)
        except Exception as e:
            last = "cvron " + ep + ": " + str(e)[:100]
            continue
        if d.get("success"):
            if key_field and d.get(key_field):
                return {"ok": True, "urls": [d[key_field]]}
            if d.get("images"):
                return {"ok": True, "urls": d["images"][:2]}
        else:
            last = "cvron " + ep + ": " + str(d.get("error") or "failed")[:100]
    return {"error": last}


def _cvron_video(prompt, attempts=2):
    """CVRON free video: generate an image, then animate it with WAN-22
    (image-to-video). Real .mp4 output. First attempt often fails when their
    upstream is busy — retry with a fresh frame."""
    img = None
    last_err = ""
    for _ in range(attempts):
        im = _cvron_image(prompt)
        if im.get("ok"):
            img = im["urls"][0]
            break
        last_err = im.get("error", "")
    if not img:
        return {"error": "video frame image failed: " + last_err[:120]}
    q = urllib.parse.quote(prompt[:240])
    for i in range(attempts + 1):
        try:
            _, raw, _ = http_fetch(f"{CVRON}/cvronai/wan22.php?prompt={q}&image="
                                   + urllib.parse.quote(img, safe=""), timeout=170,
                                   headers={"User-Agent": "Mozilla/5.0"})
            d = json.loads(raw)
            if d.get("success") and d.get("videoUrl"):
                return {"ok": True, "urls": [d["videoUrl"]]}
            last_err = str(d.get("error") or "cvron video: no url")[:120]
        except Exception as e:
            last_err = str(e)[:120]
        time.sleep(2)
    return {"error": "cvron wan22: " + last_err}


# ------------------------------------------------------------ HF Wan 2.2 video
# Hugging Face's Wan 2.2 models (the ones requested by the owner):
#   Wan-AI/Wan2.2-T2V-A14B  (text-to-video, via HF Inference Providers — needs free HF_TOKEN)
#   Wan-AI/Wan2.2-I2V-A14B  (image-to-video, same router API)
#   Wan-AI/Wan2.2-TI2V-5B   (unified text+image-to-video, same router API)
# Free no-token fallback: live public Gradio spaces running the Wan2.2 I2V-14B
# Lightning 480p model (auto-failover; each is health-probed via /monitoring).
HF_ROUTER = "https://router.huggingface.co"
HF_WAN22_SPACES = [
    ("kekkonapoli", "https://kekkonapoli-wan2-2-14b-i2v-480p-lightning-nsfw-diffusers.hf.space"),
    ("kingkladze", "https://kingkladze-wan2-2-14b-i2v-480p-lightning-nsfw-diffusers.hf.space"),
    ("tmtanu", "https://tmtanu-wan2-2-14b-i2v-480p-lightning-nsfw-diffusers.hf.space"),
    ("gum798", "https://gum798-wan2-2-i2v-lightning-4-8step-custom.hf.space"),
    ("ivannm", "https://ivannm-wan2-2-i2v-lightning-4-8step-custom-copy.hf.space"),
    ("saravutw", "https://saravutw-wan2-2-i2v-lightning-4-8step-custom.hf.space"),
]

def _gen_dir():
    d = os.path.join(BASE_DIR, "data", "generated")
    os.makedirs(d, exist_ok=True)
    return d

def _hf_router_video(model, prompt, image_b64=None, timeout=600):
    """HF Inference Providers (routed, billed to the free HF account).
    T2V with Wan2.2-T2V-A14B, I2V with Wan2.2-I2V-A14B / TI2V-5B."""
    tok = key("HF_TOKEN")
    if not tok:
        return {"error": "no HF_TOKEN"}
    body = {"inputs": prompt[:500], "parameters": {}}
    if image_b64:
        body["inputs"] = [image_b64, prompt[:500]]
    try:
        st, raw, ct = http_fetch(HF_ROUTER + "/v1/videos", method="POST", timeout=timeout,
                                 headers={"Authorization": "Bearer " + tok,
                                          "Content-Type": "application/json",
                                          "X-Wait-For-Model": "true"},
                                 json_body=body)
        if st in (200, 201) and raw and (ct.startswith("video/") or len(raw) > 20000):
            fn = os.path.join(_gen_dir(), "hf-" + secrets.token_hex(8) + ".mp4")
            with open(fn, "wb") as f:
                f.write(raw)
            return {"ok": True, "urls": ["/generated/" + os.path.basename(fn)]}
        if st == 202:
            job = (json.loads(raw).get("id") or "") if raw else ""
            if not job:
                return {"error": "router: no job id"}
            for _ in range(40):
                time.sleep(12)
                s2, r2, c2 = http_fetch(HF_ROUTER + "/v1/videos/" + job, method="GET", timeout=30,
                                        headers={"Authorization": "Bearer " + tok})
                if s2 == 200 and r2 and (c2.startswith("video/") or len(r2) > 20000):
                    fn = os.path.join(_gen_dir(), "hf-" + secrets.token_hex(8) + ".mp4")
                    with open(fn, "wb") as f:
                        f.write(r2)
                    return {"ok": True, "urls": ["/generated/" + os.path.basename(fn)]}
                if s2 in (200,) and r2:
                    try:
                        d2 = json.loads(r2)
                    except Exception:
                        continue
                    if d2.get("status") == "failed":
                        return {"error": "router job failed: " + str(d2.get("failure") or d2)[:120]}
                elif s2 >= 400:
                    return {"error": "router poll HTTP %s" % s2}
            return {"error": "router job timed out"}
        try:
            d = json.loads(raw) if raw else {}
        except Exception:
            d = {}
        return {"error": "router HTTP %s: %s" % (st, str(d.get("error") or d.get("detail") or raw[:100])[:140])}
    except Exception as e:
        return {"error": "router: " + str(e)[:120]}

def _hf_space_health(base):
    """/monitoring/summary — use a space only when its recent success rate is usable."""
    try:
        _, raw, _ = http_fetch(base + "/monitoring/summary", timeout=15)
        d = json.loads(raw)
        for f in (d.get("functions") or {}).values():
            if f.get("total_requests", 0) >= 10:
                return float(f.get("success_rate") or 0)
    except Exception:
        pass
    return 0.0

def _hf_space_video(base, prompt, image_url, timeout=720):
    """Gradio 6 space: upload frame -> generate_video -> SSE poll.
    App family of the official Wan2.2 I2V lightning demos."""
    try:
        with urllib.request.urlopen(image_url, timeout=60) as r:
            img_bytes = r.read()
    except Exception as e:
        return {"error": "frame download failed: " + str(e)[:100]}
    boundary = "----oracool" + secrets.token_hex(12)
    body = ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"files\"; filename=\"frame.jpg\"\r\n"
            "Content-Type: image/jpeg\r\n\r\n").encode() + img_bytes + ("\r\n--" + boundary + "--\r\n").encode()
    try:
        req = urllib.request.Request(base + "/gradio_api/upload", data=body,
                                     headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            up = json.loads(r.read())
        p = up[0] if isinstance(up, list) and up else up
        data = [p, None, prompt[:400], 6, "", 3.5, 1.0, 1.0, 42, True, 6,
                "FlowMatchEulerDiscrete", 3.0, 16, True, True]
        req = urllib.request.Request(base + "/gradio_api/call/generate_video",
                                     data=json.dumps({"data": data}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            eid = json.loads(r.read()).get("event_id")
        if not eid:
            return {"error": "space: no event id"}
        resp = urllib.request.urlopen(base + "/gradio_api/call/generate_video/" + eid, timeout=timeout)
        ev = ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = resp.readline()
            if not line:
                continue
            line = line.decode(errors="replace").rstrip()
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:") and ev in ("complete", "error"):
                payload = line[5:].strip()
                if ev == "error":
                    return {"error": "space: job failed (space unhealthy right now)"}
                try:
                    out = (json.loads(payload).get("data") or [])
                except Exception:
                    return {"error": "space: bad response"}
                for comp in out[:2]:
                    if isinstance(comp, dict) and comp.get("url"):
                        u = comp["url"]
                        return {"ok": True, "urls": [(base + u) if u.startswith("/") else u]}
                return {"error": "space: no video in result"}
        return {"error": "space: timed out"}
    except Exception as e:
        return {"error": "space: " + str(e)[:120]}

def _hf_wan22_video(prompt, image_url=None):
    """Wan 2.2 generation — HF Inference Providers first (when HF_TOKEN set),
    then the live public Wan2.2 I2V spaces (free, auto-failover)."""
    failures = []
    r = _hf_router_video("Wan-AI/Wan2.2-T2V-A14B", prompt)
    if r.get("ok"):
        return {**r, "model": "Wan-AI/Wan2.2-T2V-A14B", "via": "HF Inference Providers"}
    if r.get("error") and r["error"] != "no HF_TOKEN":
        failures.append("HF-router: " + r["error"][:100])
    for name, base in HF_WAN22_SPACES:
        try:
            health = _hf_space_health(base)
            if health < 0.35:
                failures.append(name + ": space unhealthy (" + str(round(health * 100)) + "% recent success)")
                continue
            img = image_url
            if not img:
                im = _cvron_image(prompt)
                if im.get("ok"):
                    img = im["urls"][0]
                else:
                    failures.append(name + ": frame failed (" + str(im.get("error"))[:80] + ")")
                    continue
            v = _hf_space_video(base, prompt, img)
            if v.get("ok"):
                return {**v, "model": "Wan-AI/Wan2.2-I2V-A14B (Lightning 480p)", "via": "HF Space " + name}
            failures.append(name + ": " + str(v.get("error"))[:100])
        except Exception as e:
            failures.append(name + ": " + str(e)[:100])
    return {"error": "Wan2.2: " + " | ".join(failures) if failures else "Wan2.2: no engine available"}

AGNES_BASE = "https://apihub.agnes-ai.com/v1"


def _agnes_image(prompt):
    """Agnes free image engine (OpenAI-compatible /images/generations)."""
    k = key("AGNES_API_KEY")
    if not k:
        return {"error": "no key"}
    try:
        _, raw, _ = http_fetch(AGNES_BASE + "/images/generations", method="POST", timeout=120,
                               headers={"Authorization": "Bearer " + k, "Content-Type": "application/json"},
                               json_body={"model": KEYS.get("AGNES_IMAGE_MODEL", "agnes-image-2.1-flash"),
                                          "prompt": prompt, "n": 1, "size": "1024x1024",
                                          "response_format": "url"})
        d = json.loads(raw)
        urls = []
        for it in (d.get("data") or []):
            if it.get("url"):
                urls.append(it["url"])
            elif it.get("b64_json"):
                urls.append("data:image/png;base64," + it["b64_json"])
        if urls:
            return {"ok": True, "urls": urls}
        msg = (d.get("error") or {}).get("message") if isinstance(d.get("error"), dict) else str(d.get("error") or d.get("message") or "empty response")
        return {"error": str(msg)[:140]}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message") or str(e.code)
        except Exception:
            msg = "HTTP " + str(e.code)
        return {"error": str(msg)[:140]}
    except Exception as e:
        return {"error": str(e)[:140]}


def _agnes_video(prompt, duration=None):
    """Agnes Flash uses mode=text, string seconds and async metadata.url output."""
    k = key("AGNES_API_KEY")
    if not k:
        return {"error": "no key"}
    model = key("AGNES_VIDEO_MODEL") or "agnes-video-2.5-flash"
    base = (key("AGNES_BASE") or AGNES_BASE).rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    hdr = {"Authorization": "Bearer " + k, "Content-Type": "application/json"}
    def video_url(d):
        if not isinstance(d, dict):
            return ""
        candidates = [d.get("url"), d.get("video_url"), (d.get("metadata") or {}).get("url")]
        data = d.get("data")
        if isinstance(data, list):
            candidates.extend(video_url(x) for x in data if isinstance(x, dict))
        elif isinstance(data, dict):
            candidates.append(video_url(data))
        return next((u for u in candidates if isinstance(u, str) and u.startswith("https://")), "")
    try:
        seconds = str(max(4, min(12, int(duration or 5))))
        _, raw, _ = http_fetch(base + "/videos", method="POST", timeout=45, headers=hdr,
                               json_body={"model": model, "mode": "text", "prompt": prompt,
                                          "size": "720P", "aspect_ratio": "16:9", "seconds": seconds, "n": 1})
        d = json.loads(raw)
        url = video_url(d)
        nested = d.get("data") if isinstance(d.get("data"), dict) else {}
        tid = d.get("id") or d.get("video_id") or d.get("task_id") or nested.get("id") or nested.get("video_id") or nested.get("task_id")
        if url:
            return {"ok": True, "urls": [url]}
        if not tid:
            return {"error": str(d.get("message") or d.get("error") or "Provider did not return a video task ID.")[:180]}
        print("Video job accepted by Agnes; waiting for provider output.")
        poll_url = root + "/agnesapi?" + urllib.parse.urlencode({"video_id": tid, "model_name": model})
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            time.sleep(15)
            try:
                _, raw, _ = http_fetch(poll_url, timeout=25, headers=hdr)
                d = json.loads(raw)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    poll_url = base + "/videos/" + urllib.parse.quote(str(tid), safe="")
                    continue
                if e.code in (429, 500, 502, 503, 504):
                    continue
                raise
            url = video_url(d)
            if url:
                print("Video generation completed by Agnes.")
                return {"ok": True, "urls": [url]}
            nested = d.get("data") if isinstance(d.get("data"), dict) else {}
            status = str(d.get("status") or nested.get("status") or "").lower()
            if status in ("failed", "error", "cancelled", "canceled"):
                return {"error": str(d.get("error") or d.get("message") or "Provider generation failed.")[:180]}
        return {"error": "Agnes video is still queued after six minutes. Provider task: " + str(tid)[:100]}
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8", "replace"))
            err = d.get("error")
            msg = (err.get("message") if isinstance(err, dict) else err) or d.get("message") or ("HTTP " + str(e.code))
        except Exception:
            msg = "HTTP " + str(e.code)
        return {"error": str(msg)[:180]}
    except Exception as e:
        return {"error": "Video provider request failed (" + type(e).__name__ + ")."}


GEMINI_FIX = ("Google project 'oracool ai' (790218512116) is denied access to that model — "
              "open console.cloud.google.com, select that project, go to APIs & Services → Library, "
              "enable 'Generative Language API', and link a Billing account (Veo / TTS / image models "
              "require billing even for free-tier usage). Once enabled it works immediately — no "
              "OraCool redeploy needed.")

_GEMINI_DENIED_UNTIL = [0.0]

def gemini_key():
    return (key("GEMINI_API_KEY") or "").strip()

def _gemini_skipped():
    """A denied project 403s every call — skip Gemini for an hour after a denial."""
    return time.time() < _GEMINI_DENIED_UNTIL[0]

def _gemini_mark_denied():
    _GEMINI_DENIED_UNTIL[0] = time.time() + 3600

def _gemini_denied(err):
    e = (err or "").lower()
    return "denied access" in e or "permission_denied" in e

def _gemini_post(model, body, timeout=120, path_suffix=":generateContent"):
    k = gemini_key()
    if not k:
        return None, "no key"
    url = "https://generativelanguage.googleapis.com/v1beta/models/" + model + path_suffix
    try:
        _, raw, _ = http_fetch(url, method="POST", timeout=timeout,
                               headers={"x-goog-api-key": k, "Content-Type": "application/json"},
                               json_body=body)
        d = json.loads(raw)
        if isinstance(d, dict) and d.get("error"):
            return None, (str(d["error"].get("status", "")) + " " + str(d["error"].get("message", "")))[:200]
        return d, None
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read() or b"{}")
            if isinstance(d, dict) and d.get("error"):
                return None, (str(d["error"].get("status", "")) + " " + str(d["error"].get("message", "")))[:200]
        except Exception:
            pass
        return None, "HTTP " + str(e.code)
    except Exception as e:
        return None, str(e)[:160]

def _gemini_text(prompt, model="gemini-flash-lite-latest", image_b64=None, mime="image/jpeg"):
    parts = []
    if image_b64:
        parts.append({"inlineData": {"mimeType": mime, "data": image_b64}})
    parts.append({"text": prompt})
    d, err = _gemini_post(model, {"contents": [{"parts": parts}]}, timeout=90)
    if d is None:
        if _gemini_denied(err):
            _gemini_mark_denied()
        return "", err or "gemini call failed"
    try:
        c = d["candidates"][0]["content"]["parts"][0]["text"]
        return c.strip()[:4000], None
    except Exception:
        return "", "unexpected gemini response"

def _gemini_tts(text, voice="Kore"):
    """Gemini neural TTS → /generated/<id>.mp3. Returns (url_or_None, err_or_None).
    PCM is 24 kHz 16-bit mono → re-encoded to mp3 with the bundled ffmpeg."""
    if not gemini_key() or _gemini_skipped():
        return None, "skipped"
    for model in ("gemini-2.5-flash-preview-tts", "gemini-3.1-flash-tts-preview"):
        d, err = _gemini_post(model, {"contents": [{"parts": [{"text": text[:600]}]}],
                                      "generationConfig": {"responseModalities": ["AUDIO"],
                                                           "speechConfig": {"voiceConfig": {
                                                               "prebuiltVoiceConfig": {"voiceName": voice}}}}},
                               timeout=90)
        if d is not None:
            try:
                for p in d["candidates"][0]["content"]["parts"]:
                    if "inlineData" in p:
                        pcm = base64.b64decode(p["inlineData"]["data"])
                        if len(pcm) > 4000:
                            import subprocess
                            import imageio_ffmpeg
                            fn = os.path.join(_gen_dir(), "gtts-" + secrets.token_hex(6) + ".pcm")
                            with open(fn, "wb") as f:
                                f.write(pcm)
                            mp3 = os.path.join(_gen_dir(), "gtts-" + secrets.token_hex(6) + ".mp3")
                            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "s16le",
                                            "-ar", "24000", "-ac", "1", "-i", fn,
                                            "-c:a", "libmp3lame", mp3],
                                           check=True, capture_output=True, timeout=120)
                            try:
                                os.remove(fn)
                            except Exception:
                                pass
                            if os.path.getsize(mp3) > 1500:
                                return "/generated/" + os.path.basename(mp3), None
            except Exception as e:
                return None, "tts encode failed: " + str(e)[:80]
        elif _gemini_denied(err):
            _gemini_mark_denied()
            return None, GEMINI_FIX
    return None, "tts unavailable"

def _gemini_image(prompt):
    for model in ("gemini-3.1-flash-image", "gemini-3-pro-image"):
        d, err = _gemini_post(model, {"contents": [{"parts": [{"text": prompt}]}],
                                      "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}},
                              timeout=180)
        if d is not None:
            for p in (d.get("candidates", [{}])[0].get("content", {}).get("parts", [])):
                if "inlineData" in p:
                    try:
                        data = base64.b64decode(p["inlineData"]["data"])
                        mime = (p["inlineData"].get("mimeType") or "image/png").lower()
                        ext = ".png" if "png" in mime else ".jpg"
                        fn = os.path.join(_gen_dir(), "gimg-" + secrets.token_hex(6) + ext)
                        with open(fn, "wb") as f:
                            f.write(data)
                        return {"ok": True, "urls": ["/generated/" + os.path.basename(fn)],
                                "model": model}, None
                    except Exception as e:
                        return {"error": "image decode failed: " + str(e)[:80]}, None
            return {"error": "no image in response"}, None
        if _gemini_denied(err):
            _gemini_mark_denied()
            return {"error": GEMINI_FIX}, GEMINI_FIX
    return {"error": "gemini image failed"}, "gemini image failed"

def _gemini_veo_video(prompt, duration=8):
    """Google Veo 3.1 — video with NATIVE synchronized audio. predictLongRunning + poll."""
    d, err = _gemini_post("veo-3.1-generate-preview",
                          {"instances": [{"prompt": prompt[:800]}],
                           "parameters": {"aspectRatio": "16:9", "durationSeconds": duration}},
                          timeout=60, path_suffix=":predictLongRunning")
    if d is None:
        if _gemini_denied(err):
            _gemini_mark_denied()
            return {"error": GEMINI_FIX}
        return {"error": err or "veo submit failed"}
    op = (d or {}).get("name")
    if not op:
        return {"error": "no operation returned by veo"}
    for _ in range(25):  # up to ~5 minutes
        time.sleep(12)
        k = gemini_key()
        try:
            _, raw, _ = http_fetch("https://generativelanguage.googleapis.com/v1beta/" + op,
                                   method="GET", timeout=30, headers={"x-goog-api-key": k})
            od = json.loads(raw)
        except Exception as e:
            return {"error": "veo poll failed: " + str(e)[:100]}
        if od.get("done"):
            vid = None
            try:
                vid = od["response"]["generateVideoResponse"]["videos"][0].get("uri")
            except Exception:
                pass
            if vid:
                return {"ok": True, "urls": [vid], "audio": True}
            e2 = (od.get("error") or {}).get("message", "no video in operation")
            return {"error": str(e2)[:200]}
    return {"error": "veo still rendering after 5 minutes — try again"}

def gemini_status():
    """Live, honest probe of the connected Gemini key (cheap calls only — a Veo
    job is never started here because that would be a paid generation)."""
    if not gemini_key():
        return {"connected": False, "note": "No GEMINI_API_KEY configured."}
    caps = {}
    t, err = _gemini_text("Reply with exactly: OK")
    caps["chat+vision"] = "ok" if t.strip().upper() == "OK" else ("denied — " + GEMINI_FIX if _gemini_denied(err) else "error: " + str(err)[:100])
    m, terr = _gemini_tts("Status check.")
    if _gemini_skipped():
        caps["tts"] = "denied (cached 1h)"
    else:
        caps["tts"] = "ok" if m else ("denied" if _gemini_denied(terr) else "unavailable: " + str(terr)[:80])
    if _gemini_denied(terr) and "denied" not in caps["chat+vision"]:
        caps["chat+vision"] = "denied — " + GEMINI_FIX
    caps["image"] = "ready (used on next image request)" if not _gemini_skipped() else "denied (cached 1h)"
    caps["video_veo31"] = "ready (used on next voice-video request)" if not _gemini_skipped() else "denied (cached 1h)"
    return {"connected": True, "project": "oracool ai (790218512116)",
            "capabilities": caps,
            "note": ("All Gemini engines activate automatically the moment the project is granted access — "
                     "no redeploy needed. Until then, OraCool keeps using its free/other engines.")}

# ================================================================ app builder (Arena-style) + GitHub

_BUILDS_DIR = os.path.join(DATA_DIR, "builds")
_BUILDS_LOCK = threading.Lock()
_BUILD_MAX_FILES = 24
_BUILD_MAX_FILE = 256 * 1024
_BUILD_DAILY = {}  # email -> [timestamps] (free-tier cap)

def _builds_meta():
    return os.path.join(_BUILDS_DIR, "meta.json")

def _builds_load():
    try:
        with open(_builds_meta(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _builds_save(d):
    with _BUILDS_LOCK:
        try:
            os.makedirs(_BUILDS_DIR, exist_ok=True)
            with open(_builds_meta(), "w", encoding="utf-8") as f:
                json.dump(d, f, indent=1)
        except Exception:
            pass

def _build_slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "site").lower()).strip("-")[:40]
    return s or ("site-" + os.urandom(3).hex())

def _llm_json(system, user, max_tokens=8000):
    """Non-streaming completion that must return JSON. Tries groq -> openai -> agnes."""
    attempts = []
    k = key("GROQ_API_KEY")
    if k:
        attempts.append((k, "https://api.groq.com/openai/v1", KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL), "groq"))
    k = key("OPENAI_API_KEY")
    if k:
        attempts.append((k, "https://api.openai.com/v1", "gpt-4o-mini", "openai"))
    k = key("AGNES_API_KEY")
    if k:
        attempts.append((k, AGNES_BASE, KEYS.get("AGNES_MODEL", "agnes-2.5-flash"), "agnes"))
    if not attempts:
        return None, "no LLM key configured"
    for k, base, model, prov in attempts:
        try:
            _, raw, _ = http_fetch(base.rstrip("/") + "/chat/completions", method="POST", timeout=180,
                                   headers={"Authorization": "Bearer " + k, "Content-Type": "application/json"},
                                   json_body={"model": model, "max_tokens": max_tokens, "temperature": 0.4,
                                              "messages": [{"role": "system", "content": system},
                                                           {"role": "user", "content": user}]})
            d = json.loads(raw)
            c = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            c = c.strip()
            m = re.search(r"\{[\s\S]*\}", c)
            if m:
                c = m.group(0)
            return json.loads(c), prov
        except Exception as e:
            attempts_err = str(e)[:120]
            continue
    return None, "all LLM providers failed: " + attempts_err

def _llm_text(system, user, max_tokens=16000, extra_msgs=None, effort="low"):
    """Non-streaming completion returning RAW text (no JSON contract). Tries
    groq -> openai -> agnes; reports each provider's finish_reason so the
    caller can detect truncated output (patch27: JSON-escaped HTML blew the
    token budget, raw HTML fits)."""
    attempts = []
    k = key("GROQ_API_KEY")
    if k:
        # gpt-oss on Groq first: fast, follows size budgets, and its rate-limit
        # bucket is separate from the chat model (which 429s while serving the app)
        attempts.append((k, "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "groq-oss", {"reasoning_effort": effort}))
        attempts.append((k, "https://api.groq.com/openai/v1", KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL), "groq", {}))
    k = key("OPENAI_API_KEY")
    if k:
        attempts.append((k, "https://api.openai.com/v1", "gpt-4o-mini", "openai", {}))
    k = key("AGNES_API_KEY")
    if k:
        attempts.append((k, AGNES_BASE, KEYS.get("AGNES_MODEL", "agnes-2.5-flash"), "agnes", {}))
    if not attempts:
        return None, "no LLM key configured", "nokey"
    last_err = "all LLM providers failed"
    for att in attempts:
        k, base, model, prov = att[0], att[1], att[2], att[3]
        req_body_extra = att[4] if len(att) > 4 else {}
        try:
            req_body = {"model": model, "max_tokens": max_tokens, "temperature": 0.4,
                        "messages": ([{"role": "system", "content": system},
                                       {"role": "user", "content": user}] + (extra_msgs or []))}
            req_body.update(req_body_extra)
            _, raw, _ = http_fetch(base.rstrip("/") + "/chat/completions", method="POST", timeout=240,
                                   headers={"Authorization": "Bearer " + k, "Content-Type": "application/json"},
                                   json_body=req_body)
            d = json.loads(raw)
            ch = (d.get("choices") or [{}])[0]
            c = (ch.get("message", {}).get("content") or "").strip()
            if c:
                return c, prov, str(ch.get("finish_reason") or "")
        except Exception as e:
            last_err = prov + ": " + str(e)[:110]
            continue
    return None, last_err, "error"


def _llm_text_stream(system, user, max_tokens=16000, extra_msgs=None, effort="low", on_delta=None):
    """patch42: same provider ladder as _llm_text, but streamed — `on_delta(text_so_far)` fires as the
    page is written so the build feed can show progress section by section. Falls back to the
    non-streaming call if every provider refuses to stream."""
    attempts = []
    k = key("GROQ_API_KEY")
    if k:
        attempts.append((k, "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "groq-oss", {"reasoning_effort": effort}))
        attempts.append((k, "https://api.groq.com/openai/v1", KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL), "groq", {}))
    k = key("OPENAI_API_KEY")
    if k:
        attempts.append((k, "https://api.openai.com/v1", "gpt-4o-mini", "openai", {}))
    k = key("AGNES_API_KEY")
    if k:
        attempts.append((k, AGNES_BASE, KEYS.get("AGNES_MODEL", "agnes-2.5-flash"), "agnes", {}))
    if not attempts:
        return None, "no LLM key configured", "nokey"
    for k, base, model, prov, extra in attempts:
        acc = []; finish = ""; last_cb = 0.0
        try:
            req_body = {"model": model, "max_tokens": max_tokens, "temperature": 0.4, "stream": True,
                        "messages": ([{"role": "system", "content": system}, {"role": "user", "content": user}] + (extra_msgs or []))}
            req_body.update(extra)
            req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=json.dumps(req_body).encode("utf-8"),
                                         headers={"Authorization": "Bearer " + k, "Content-Type": "application/json",
                                                  "Accept": "text/event-stream", "User-Agent": UA}, method="POST")
            with urllib.request.urlopen(req, timeout=240, context=ssl.create_default_context()) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        d = json.loads(payload)
                    except Exception:
                        continue
                    ch = (d.get("choices") or [{}])[0]
                    piece = (ch.get("delta") or {}).get("content") or ""
                    if piece:
                        acc.append(piece)
                    if ch.get("finish_reason"):
                        finish = str(ch.get("finish_reason"))
                    if on_delta and (piece or not acc) and time.time() - last_cb > 0.6:
                        last_cb = time.time()
                        try:
                            on_delta("".join(acc))
                        except Exception:
                            pass
            text = "".join(acc).strip()
            if text:
                if on_delta:
                    try:
                        on_delta(text)
                    except Exception:
                        pass
                return text, prov, finish
        except Exception:
            if acc and len("".join(acc)) > 4000:   # the stream broke late: keep what we have (the caller can continue it)
                return "".join(acc), prov, "length"
            continue
    return _llm_text(system, user, max_tokens=max_tokens, extra_msgs=extra_msgs, effort=effort)


_SECTION_RX = re.compile(r"<(header|section|footer|main|aside|article)\b([^>]*)>", re.I)


def _section_label(tag, attrs):
    tag = tag.lower()
    m = re.search(r"\b(?:id|aria-label|data-section)\s*=\s*[\"']([^\"']{2,40})[\"']", attrs or "", re.I)
    name = m.group(1) if m else ""
    if not name:
        m = re.search(r"\bclass\s*=\s*[\"']([^\"']{2,80})[\"']", attrs or "", re.I)
        if m:
            toks = [t for t in m.group(1).split() if t and not re.match(r"^(?:reveal|container|wrap|section|row|col|flex|grid|py|px|mt|mb|sec|block|is-|js-)", t)]
            name = toks[0] if toks else ""
    name = re.sub(r"[-_]+", " ", name).strip().lower()
    if tag == "header":
        return "header & navigation" if not name or name in ("site header", "header", "top") else name
    if tag == "nav":
        return "navigation"
    if tag == "footer":
        return "footer"
    if tag == "main":
        return ""
    return name or "next content section"


def _writer_progress(email, verb="Writing"):
    """patch42: returns an on_delta callback that turns the streamed HTML into feed steps:
    'Thinking through the layout…' → 'Writing the hero section' → '… pricing section' → … with live sizes."""
    state = {"t0": time.time(), "seen": 0, "labels": []}
    def cb(text):
        n = len(text or "")
        if not n:
            _bp_note(email, "thinking through the layout & copy… %ds" % int(time.time() - state["t0"]))
            return
        kb = "%.1f KB written" % (n / 1024.0)
        tags = _SECTION_RX.findall(text)
        if len(tags) > state["seen"]:
            for tag, attrs in tags[state["seen"]:]:
                lab = _section_label(tag, attrs)
                if lab and lab not in state["labels"] and len(state["labels"]) < 14:
                    state["labels"].append(lab)
                    _bp_step(email, verb + " the " + lab + (" section" if lab not in ("header & navigation", "navigation", "footer") else ""), kb, kind="write")
            state["seen"] = len(tags)
        _bp_note(email, kb + (" · closing tags & scripts" if "</html>" in text.lower() else ""))
    return cb


def _parse_builder_reply(c):
    """Marker format: TEMPLATE line, TITLE line, then the raw HTML file. Accepts
    a legacy {files:[...]} JSON reply too. Returns dict or None."""
    if not c:
        return None
    c = c.strip()
    if c.startswith("```"):
        c = c.split("\n", 1)[1] if "\n" in c else c
        c = c.rsplit("```", 1)[0].strip()
    if c.startswith("{"):
        try:
            m = re.search(r"\{[\s\S]*\}", c)
            d = json.loads(m.group(0) if m else c)
            if isinstance(d, dict) and isinstance(d.get("files"), list):
                return {"files": d["files"], "template": str(d.get("template") or "")[:40],
                        "name": str(d.get("name") or "").strip()[:60]}
        except Exception:
            return None
    tm = re.search(r"^\s*TEMPLATE\s*[::]\s*(.+)", c, re.M | re.I)
    nm = re.search(r"^\s*TITLE\s*[::]\s*(.+)", c, re.M | re.I)
    m = re.search(r"<!DOCTYPE html[\s\S]*", c, re.I) or re.search(r"<html[\s\S]*", c, re.I)
    if not m:
        return None
    body = m.group(0).strip()
    body = re.sub(r"\n?END\b[ \t\r]*$", "", body)
    if len(body) < 500 or "</html>" not in body.lower():
        if len(body) > 6000:  # truncated mid-file: minimal rescue so the site still opens
            body = body.rstrip()
            low = body.lower()
            if "<style" in low and "</style>" not in low:
                body += "</style>"
            if "<script" in low and "</script>" not in low:
                body += "</script>"
            body += "\n</body></html>"
        else:
            return None
    return {"files": [{"path": "index.html", "content": body}],
            "template": (tm.group(1).strip()[:40] if tm else ""),
            "name": (nm.group(1).strip()[:60] if nm else "")}


# ---- patch40: image collection (licensed photos for builds + "find me images of X") ----
def _img_get(url, timeout=12):
    _, raw, _ = http_fetch(url, timeout=timeout, headers={"User-Agent": UA + " (+https://oracoolai.com)"})
    return json.loads(raw)


def image_search(query, n=6):
    """Real, licensed photos: Pexels (if PEXELS_API_KEY) → Openverse (CC, commercial-ok)
    → Wikimedia Commons. Returns [{url, thumb, title, author, license, source, w, h}]."""
    q = " ".join(str(query or "").split())[:80]
    n = max(1, min(int(n or 6), 12))
    if not q:
        return []
    out = []
    pk = key("PEXELS_API_KEY")
    if pk:
        try:
            _, raw, _ = http_fetch("https://api.pexels.com/v1/search?" + urllib.parse.urlencode({"query": q, "per_page": n, "orientation": "landscape"}),
                                   timeout=12, headers={"Authorization": pk})
            for ph in (json.loads(raw).get("photos") or [])[:n]:
                src = ph.get("src") or {}
                out.append({"url": src.get("large2x") or src.get("large") or src.get("original"), "thumb": src.get("medium"),
                            "title": (ph.get("alt") or q)[:90], "author": ph.get("photographer") or "", "license": "Pexels License",
                            "source": "pexels", "w": ph.get("width"), "h": ph.get("height")})
        except Exception:
            pass
    if len(out) < n:
        try:
            d = _img_get("https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(
                {"q": q, "page_size": n * 2, "mature": "false", "license_type": "commercial,modification"}))
            for r in (d.get("results") or []):
                u = r.get("url") or ""
                if not u.startswith("http") or (r.get("width") or 0) < 640:
                    continue
                out.append({"url": u, "thumb": r.get("thumbnail") or u, "title": (r.get("title") or q)[:90],
                            "author": (r.get("creator") or "")[:60], "license": ("CC " + str(r.get("license") or "").upper()).strip(),
                            "source": r.get("source") or "openverse", "w": r.get("width"), "h": r.get("height")})
                if len(out) >= n:
                    break
        except Exception:
            pass
    if len(out) < n:
        try:
            d = _img_get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(
                {"action": "query", "generator": "search", "gsrsearch": "filetype:bitmap " + q, "gsrnamespace": "6",
                 "gsrlimit": str(n * 2), "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": "1400", "format": "json"}))
            pages = sorted(((d.get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 0))
            for pg in pages:
                ii = (pg.get("imageinfo") or [{}])[0]
                u = ii.get("thumburl") or ii.get("url") or ""
                if not u or (ii.get("width") or 0) < 640 or u.lower().endswith((".svg", ".gif", ".tif", ".tiff")):
                    continue
                md = ii.get("extmetadata") or {}
                auth = re.sub(r"<[^>]+>", "", str((md.get("Artist") or {}).get("value") or ""))[:60]
                lic = str((md.get("LicenseShortName") or {}).get("value") or "CC")[:30]
                out.append({"url": u, "thumb": u, "title": str(pg.get("title") or "").replace("File:", "")[:90],
                            "author": auth, "license": lic, "source": "wikimedia", "w": ii.get("width"), "h": ii.get("height")})
                if len(out) >= n:
                    break
        except Exception:
            pass
    seen, uniq = set(), []
    for x in out:
        if x.get("url") and x["url"] not in seen:
            seen.add(x["url"]); uniq.append(x)
    return uniq[:n]


_BUILD_IMG_QUERIES = {
    "restaurant / food & drink": ["restaurant interior warm lighting", "gourmet plated dish", "chef cooking kitchen"],
    "hotel / hospitality": ["luxury hotel room interior", "hotel swimming pool resort", "hotel lobby lounge"],
    "fintech / finance": ["mobile banking app phone hands", "modern office finance team", "city skyline night"],
    "SaaS / software product": ["team working laptops modern office", "dashboard analytics screen", "developer coding"],
    "e-commerce / retail": ["fashion boutique products display", "sneakers product photo", "shopping bags lifestyle"],
    "personal portfolio": ["creative workspace desk", "designer sketching", "photographer camera portrait"],
    "creative agency": ["creative team brainstorming", "brand design studio", "camera film production"],
    "healthcare / clinic": ["doctor consultation clinic", "modern clinic reception", "medical team hospital"],
    "education": ["students classroom learning", "university campus", "teacher lecture"],
    "real estate": ["modern house exterior", "luxury apartment living room", "city apartment building"],
    "fitness": ["gym training weights", "fitness class group", "running athlete"],
    "event": ["concert crowd lights", "conference stage speaker", "wedding decoration"],
    "community / nonprofit": ["volunteers community helping", "charity donation hands", "community gathering"],
    "professional services": ["law office meeting", "business handshake", "professional consultation"],
    "travel / tours": ["tropical beach resort", "safari landscape", "travel adventure mountains"],
    "beauty / salon": ["hair salon interior", "spa treatment relaxing", "makeup artist"],
    "logistics": ["delivery truck highway", "warehouse logistics", "courier delivering package"],
    "construction / architecture": ["modern architecture building", "construction site workers", "interior design living room"],
    "modern business": ["modern office team", "business meeting", "city skyline"],
}


def _build_image_library(prompt, blueprint_text):
    """Collect 6-9 licensed photos matched to the brief (industry queries + the
    brief's own subject words). Never blocks a build: 8s budget, failures = []."""
    ind = blueprint_text.split("|")[0].replace("industry:", "").strip()
    qs = list(_BUILD_IMG_QUERIES.get(ind, _BUILD_IMG_QUERIES["modern business"]))
    subj = re.sub(r"[^a-z0-9 ]", " ", (prompt or "").lower())
    subj = " ".join(w for w in subj.split() if len(w) > 3 and w not in ("with", "that", "this", "from", "your", "their", "have", "page", "website", "site", "landing", "build", "make", "create"))[:60]
    if subj:
        qs.insert(0, subj)
    lib = []
    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(image_search, q, 3) for q in qs[:4]]
            for f in futs:
                try:
                    lib.extend(f.result(timeout=8))
                except Exception:
                    pass
    except Exception:
        pass
    seen, out = set(), []
    for x in lib:
        if x["url"] not in seen:
            seen.add(x["url"]); out.append(x)
    return out[:9]


# ---- patch40: live build progress (Arena-style "running steps" card in the chat) ----
_BUILD_PROGRESS = {}
_BP_LOCK = threading.Lock()


def _bp_reset(email, name=""):
    with _BP_LOCK:
        _BUILD_PROGRESS[(email or "").lower()] = {"started": time.time(), "steps": [], "done": False, "name": name}


_BP_KINDS = (("ready", ("ready", "preview", "done")),
             ("explore", ("collect", "photo", "explor", "reading the current", "search")),
             ("review", ("review", "gap", "fixing", "checking")),
             ("save", ("saving", "saved", "workspace", "design kit")),
             ("skill", ("brief", "art direction", "blueprint", "skill", "template")),
             ("write", ("writing", "continuing", "rewriting", "section", "index.html")))


def _bp_kind(title):
    low = (title or "").lower()
    for kind, words in _BP_KINDS:
        if any(w in low for w in words):
            return kind
    return "build"


def _bp_step(email, title, detail="", kind=""):
    with _BP_LOCK:
        rec = _BUILD_PROGRESS.get((email or "").lower())
        if not rec:
            return
        now = time.time()
        if rec["steps"] and rec["steps"][-1].get("ms") is None:
            rec["steps"][-1]["ms"] = int((now - rec["steps"][-1]["t"]) * 1000)
        if len(rec["steps"]) >= 40:  # never let a runaway stream flood the feed
            return
        rec["steps"].append({"title": title, "detail": detail, "t": now, "ms": None, "kind": kind or _bp_kind(title)})


def _bp_note(email, detail):
    """patch42: live detail on the step that is running right now ("14 KB written · pricing section")."""
    with _BP_LOCK:
        rec = _BUILD_PROGRESS.get((email or "").lower())
        if not rec or not rec["steps"] or rec["steps"][-1].get("ms") is not None:
            return
        rec["steps"][-1]["detail"] = str(detail or "")[:160]


def _bp_done(email, ok=True, detail=""):
    with _BP_LOCK:
        rec = _BUILD_PROGRESS.get((email or "").lower())
        if not rec:
            return
        now = time.time()
        if rec["steps"] and rec["steps"][-1].get("ms") is None:
            rec["steps"][-1]["ms"] = int((now - rec["steps"][-1]["t"]) * 1000)
        rec["done"] = True; rec["ok"] = bool(ok); rec["result"] = detail
        rec["total_ms"] = int((now - rec["started"]) * 1000)


def build_progress(email):
    with _BP_LOCK:
        rec = _BUILD_PROGRESS.get((email or "").lower())
        if not rec:
            return {"ok": True, "active": False, "steps": []}
        steps = [dict(x, ms=(x["ms"] if x["ms"] is not None else int((time.time() - x["t"]) * 1000)), running=(x["ms"] is None))
                 for x in rec["steps"]]
        return {"ok": True, "active": not rec["done"], "done": rec["done"], "ok_build": rec.get("ok"),
                "steps": steps, "total_ms": rec.get("total_ms"), "result": rec.get("result", ""), "name": rec.get("name", ""),
                "started": rec["started"], "age_ms": int((time.time() - rec["started"]) * 1000)}


# ---- patch38: builder quality — industry blueprint, injected design kit, QA pass ----
_BLUEPRINTS = [
    (("restaurant", "cafe", "café", "coffee", "bakery", "kitchen", "food", "chef", "bar", "grill", "pizza", "suya", "eatery", "bistro", "lounge"),
     dict(industry="restaurant / food & drink", palette="bg #14100d · surface #1f1813 · text #f6efe6 · accent #e0a458 (warm amber) · accent2 #b23a3a",
          fonts="Playfair Display (display) + Inter (body)", hero="full-bleed dark gradient with a CSS-drawn plate/steam illustration and a floating 'Open today' pill",
          sections="hero · signature dishes grid with prices · menu with category filter tabs · chef story split section · gallery mosaic (CSS art) · testimonials slider · reservation form · location & hours · footer",
          feature="menu category filter (tabs) with animated card switch")),
    (("hotel", "resort", "suite", "lodge", "guesthouse", "airbnb", "shortlet", "hospitality", "villa"),
     dict(industry="hotel / hospitality", palette="bg #0f1720 · surface #172230 · text #eef4fb · accent #c9a96e (champagne gold) · accent2 #2fa4a9",
          fonts="Cormorant Garamond (display) + Manrope (body)", hero="split hero: elegant serif headline left, CSS 'window view' card with sunset gradient right, availability bar below",
          sections="hero with date/guests availability bar · rooms & suites cards with price/night · amenities icon grid · experiences/dining · gallery · guest reviews · location map placeholder (CSS) · booking enquiry form · footer",
          feature="room booking modal with night-count price calculator")),
    (("fintech", "bank", "payment", "wallet", "crypto", "loan", "savings", "invest", "trading", "remittance", "insurance", "finance"),
     dict(industry="fintech / finance", palette="bg #070b14 · surface #0f1626 · text #e9eefb · accent #4f7cff (electric blue) · accent2 #22d3a5 (mint)",
          fonts="Space Grotesk (display) + Inter (body)", hero="dark navy with animated gradient mesh and a CSS phone mock showing a live balance card + transaction list",
          sections="hero · trust bar (regulated, encrypted, uptime) · features grid · how-it-works 3 steps · animated stats counters · security section · pricing/fees table · FAQ accordion · CTA + waitlist form · footer",
          feature="fees/savings calculator with live output")),
    (("saas", "software", "startup", "platform", "dashboard", "ai ", "app ", "tool", "automation", "analytics", "crm", "api"),
     dict(industry="SaaS / software product", palette="bg #0b0f1a · surface #121829 · text #eaf0ff · accent #7c5cff (violet) · accent2 #22d3ee (cyan)",
          fonts="Sora (display) + Inter (body)", hero="centered headline with gradient text, glowing orbs, and a CSS dashboard mock (sidebar + chart bars animated on load)",
          sections="hero · logo/trust strip · feature cards with icons · product tour tabs · integrations grid · pricing toggle (monthly/yearly) · testimonials · FAQ accordion · final CTA · footer",
          feature="pricing toggle monthly/yearly with animated price change")),
    (("shop", "store", "ecommerce", "e-commerce", "fashion", "clothing", "sneaker", "jewel", "cosmetic", "skincare", "perfume", "market", "products"),
     dict(industry="e-commerce / retail", palette="bg #fbf8f4 · surface #ffffff · text #1a1614 · accent #111111 · accent2 #d4573b (terracotta)",
          fonts="DM Serif Display (display) + DM Sans (body)", hero="editorial light hero with oversized serif headline and CSS product 'cards' fanned at an angle",
          sections="announcement bar · hero · category tiles · featured products grid with prices · collection story · benefits strip (delivery, returns) · reviews · newsletter form · footer with policies",
          feature="cart drawer demo: add to cart updates count + total")),
    (("portfolio", "personal", "photograph", "designer", "developer", "freelance", "resume", "cv", "artist", "writer", "creator"),
     dict(industry="personal portfolio", palette="bg #0d0d0f · surface #17171b · text #f2f2f4 · accent #ffd166 (sunflower) · accent2 #ef476f",
          fonts="Syne (display) + Inter (body)", hero="huge name in display type with animated marquee of skills and a CSS abstract portrait shape",
          sections="hero · about with facts strip · selected work grid (hover reveal) · services · process timeline · testimonials · contact form · footer with social links",
          feature="filterable work grid by category")),
    (("agency", "studio", "marketing", "branding", "advertis", "creative", "media", "production"),
     dict(industry="creative agency", palette="bg #f5f3ee · surface #ffffff · text #111111 · accent #ff4d1f (signal orange) · accent2 #1f1fff",
          fonts="Bebas Neue (display) + Manrope (body)", hero="bold oversized headline, marquee client strip, animated gradient blob",
          sections="hero · services list with hover expand · case studies grid · results counters · process steps · team · testimonials · contact form · footer",
          feature="case study cards with hover details + counters animation")),
    (("clinic", "hospital", "health", "dental", "doctor", "pharmacy", "medical", "wellness", "therapy", "lab"),
     dict(industry="healthcare / clinic", palette="bg #f4f9fb · surface #ffffff · text #0f2a3a · accent #0e9f9a (teal) · accent2 #2563eb",
          fonts="Plus Jakarta Sans (display + body)", hero="clean light hero with soft blob shapes and an appointment card",
          sections="hero with appointment CTA · services grid · doctors/team · why-us stats · patient journey steps · insurance/pricing · testimonials · FAQ · appointment form · footer with emergency contact",
          feature="appointment form with department select + date validation")),
    (("school", "academy", "course", "education", "tutor", "university", "college", "training", "bootcamp", "learning"),
     dict(industry="education", palette="bg #0f1b2d · surface #16253d · text #eef3ff · accent #f7b731 (gold) · accent2 #36c2cf",
          fonts="Fraunces (display) + Inter (body)", hero="split hero: headline + enrol CTA, CSS 'course card stack' illustration",
          sections="hero · programmes/courses grid · outcomes counters · curriculum accordion · instructors · schedule/tuition table · student stories · admissions steps · enquiry form · footer",
          feature="curriculum accordion + tuition tab switch")),
    (("real estate", "property", "estate", "realtor", "apartment", "housing", "land", "rent"),
     dict(industry="real estate", palette="bg #101418 · surface #181f26 · text #f1f4f7 · accent #c8a15a (brass) · accent2 #3b82f6",
          fonts="Libre Baskerville (display) + Inter (body)", hero="cinematic dark hero with search bar (location, type, budget) and CSS skyline silhouette",
          sections="hero with property search bar · featured listings grid (price, beds, baths) · neighbourhoods · why-us · buying process steps · agents · testimonials · mortgage calculator · enquiry form · footer",
          feature="listings filter by type/budget + mortgage calculator")),
    (("gym", "fitness", "yoga", "trainer", "sport", "workout", "athlet", "boxing", "pilates"),
     dict(industry="fitness", palette="bg #0a0a0a · surface #151515 · text #f5f5f5 · accent #c6ff00 (volt) · accent2 #ff3b3b",
          fonts="Anton (display) + Inter (body)", hero="high-contrast hero with diagonal split, animated volt accent line and a class countdown pill",
          sections="hero · programs grid · class timetable tabs · coaches · transformation counters · membership pricing · testimonials · trial signup form · location & hours · footer",
          feature="weekday timetable tabs + BMI/goal calculator")),
    (("event", "wedding", "conference", "festival", "concert", "summit", "party", "meetup", "expo"),
     dict(industry="event", palette="bg #12051f · surface #1c0b2e · text #f7ecff · accent #ff6bd6 (magenta) · accent2 #ffd166",
          fonts="Unbounded (display) + Inter (body)", hero="poster-style hero with date/venue badges, animated countdown and gradient rays",
          sections="hero with live countdown · about · speakers/lineup grid · schedule tabs by day · venue & travel · tickets pricing · sponsors strip · FAQ · register form · footer",
          feature="live countdown timer + day schedule tabs")),
    (("church", "ministry", "mosque", "ngo", "charity", "foundation", "nonprofit", "non-profit", "community", "donate", "volunteer"),
     dict(industry="community / nonprofit", palette="bg #fffdf8 · surface #ffffff · text #1f2933 · accent #d97706 (amber) · accent2 #0f766e",
          fonts="Lora (display) + Source Sans 3 (body)", hero="warm light hero with hand-drawn style SVG sun/shape and a donation progress bar",
          sections="hero · mission · programmes/ministries grid · impact counters · upcoming events list · stories/testimonials · get involved (volunteer/donate) tabs · donation form · footer",
          feature="donation amount presets with progress bar update")),
    (("law", "legal", "attorney", "chambers", "consult", "accounting", "audit", "advisory", "firm", "tax"),
     dict(industry="professional services", palette="bg #0b1220 · surface #121b2e · text #e8edf6 · accent #b48a3c (bronze) · accent2 #2f6fed",
          fonts="Cormorant (display) + Inter (body)", hero="stately dark hero with thin gold rules, practice-area chips and a consultation CTA",
          sections="hero · practice areas grid · about the firm · partners/team · results/credentials counters · process steps · insights/articles · testimonials · consultation form · footer with disclaimer",
          feature="practice-area tabs with detail panels")),
    (("travel", "tour", "safari", "trip", "flight", "vacation", "adventure", "tourism"),
     dict(industry="travel / tours", palette="bg #06131a · surface #0d1f28 · text #ecf7fb · accent #ffb347 (sunset) · accent2 #2dd4bf",
          fonts="Josefin Sans (display) + Nunito (body)", hero="wide hero with layered CSS mountains/sea gradient parallax and a trip search bar",
          sections="hero with search bar · destinations grid · featured packages with prices · why travel with us · itinerary accordion · reviews slider · gallery · booking form · footer",
          feature="package filter by budget/duration + itinerary accordion")),
    (("salon", "beauty", "spa", "barber", "nail", "makeup", "hair", "lash", "massage"),
     dict(industry="beauty / salon", palette="bg #fdf6f3 · surface #ffffff · text #2b1d1a · accent #b76e79 (rose gold) · accent2 #2f2f2f",
          fonts="Italiana (display) + Jost (body)", hero="soft light hero with rose gradient orbs, service price highlights and a Book CTA",
          sections="hero · services & price list tabs · signature treatments · team · gallery · packages · reviews · booking form with time slots · location & hours · footer",
          feature="service tabs + booking time-slot picker")),
    (("logistics", "delivery", "courier", "shipping", "transport", "haulage", "fleet", "dispatch", "moving"),
     dict(industry="logistics", palette="bg #0a0f14 · surface #121a22 · text #eef3f7 · accent #ff7a00 (safety orange) · accent2 #00b4d8",
          fonts="Rajdhani (display) + Inter (body)", hero="industrial hero with animated route line SVG, tracking input and coverage badges",
          sections="hero with tracking input · services grid · coverage/network · how it works · fleet/capabilities counters · pricing estimator · clients strip · testimonials · quote form · footer",
          feature="shipment tracking demo + price estimator")),
    (("construction", "architect", "interior", "builder", "engineering", "renovation", "furniture", "design studio"),
     dict(industry="construction / architecture", palette="bg #111111 · surface #1b1b1b · text #f3f1ec · accent #e3b23c (mustard) · accent2 #9aa5b1",
          fonts="Archivo Black (display) + Archivo (body)", hero="grid-lined blueprint hero with CSS isometric building shapes and project counters",
          sections="hero · services · featured projects grid with hover captions · process timeline · materials/quality · team · certifications strip · testimonials · quote form · footer",
          feature="project gallery filter + before/after slider")),
]
_BLUEPRINT_DEFAULT = dict(industry="modern business", palette="bg #0b1020 · surface #121a2e · text #eaf0ff · accent #22d3ee (cyan) · accent2 #a78bfa (violet)",
                          fonts="Outfit (display) + Inter (body)", hero="dark gradient hero with glowing CSS orbs, gradient headline and a product/feature mock card",
                          sections="hero · trust strip · services/features grid · about split · process steps · stats counters · testimonials · FAQ accordion · contact form · footer",
                          feature="FAQ accordion + animated stats counters")


def _brief_blueprint(prompt):
    """patch38: turn a brief into a concrete design blueprint (industry, palette,
    fonts, section plan, hero idea, interactive feature) so the builder starts
    from an art-directed plan instead of a blank page."""
    low = " " + (prompt or "").lower() + " "
    best, score = None, (0, 0)
    for kws, bp in _BLUEPRINTS:
        hits = []
        for k in kws:
            k = k.strip()
            # word-start match; short words also need a word END ("bar" must not hit "barber")
            m = re.search(r"\b" + re.escape(k) + (r"\b" if len(k) <= 4 else ""), low)
            if m:
                hits.append(m.start())
        if not hits:
            continue
        # more keyword hits win; on a tie the industry named EARLIEST in the brief
        # wins ("a boutique hotel ... with a restaurant" is a hotel, not a restaurant)
        sc = (len(hits), -min(hits))
        if sc > score:
            best, score = bp, sc
    bp = best or _BLUEPRINT_DEFAULT
    return ("industry: " + bp["industry"] + " | palette (CSS variables): " + bp["palette"] +
            " | typography: " + bp["fonts"] + " | hero: " + bp["hero"] +
            " | section plan (in order, adapt names): " + bp["sections"] +
            " | must-work interactive feature: " + bp["feature"])


_DESIGN_KIT_CSS = ("<style id=\"ok-kit\">/* OraCool design kit */*,*::before,*::after{box-sizing:border-box}html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}"
                   "body{margin:0;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}"
                   "img,video{max-width:100%;height:auto}svg{max-width:100%}"
                   ":focus-visible{outline:2px solid currentColor;outline-offset:3px}"
                   ".ok-reveal{opacity:0;transform:translateY(18px);transition:opacity .75s cubic-bezier(.2,.7,.2,1),transform .75s cubic-bezier(.2,.7,.2,1)}"
                   ".ok-reveal.ok-in{opacity:1;transform:none}.ok-scrolled{box-shadow:0 8px 28px rgba(0,0,0,.14)}"
                   "@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}.ok-reveal{opacity:1;transform:none;transition:none}}</style>")
_DESIGN_KIT_JS = ("<script id=\"ok-kit-js\">(function(){var d=document;try{var els=[].slice.call(d.querySelectorAll('section,.card,.feature,.plan,.pricing-card,.testimonial,.service,.room,.dish,.project,article'))"
                  ".filter(function(e){return !e.closest('header,nav,footer')});if(!('IntersectionObserver' in window)||matchMedia('(prefers-reduced-motion: reduce)').matches){return}"
                  "els.forEach(function(e,i){e.classList.add('ok-reveal');if(e.tagName!=='SECTION'){e.style.transitionDelay=((i%6)*70)+'ms'}});"
                  "var io=new IntersectionObserver(function(en){en.forEach(function(x){if(x.isIntersecting){x.target.classList.add('ok-in');io.unobserve(x.target)}})},{threshold:.08,rootMargin:'0px 0px -6% 0px'});"
                  "els.forEach(function(e){io.observe(e)});setTimeout(function(){els.forEach(function(e){e.classList.add('ok-in')})},2500);"
                  "var h=d.querySelector('header');if(h){var on=function(){h.classList.toggle('ok-scrolled',(window.scrollY||0)>8)};addEventListener('scroll',on,{passive:true});on()}}catch(e){}})();</script>")


def _inject_design_kit(html):
    """patch38: prepend the design kit (reset, smoothing, focus, scroll reveal,
    sticky-header shadow, reduced-motion) into a generated page — idempotent."""
    h = html or ""
    if 'id="ok-kit"' in h:
        return h
    m = re.search(r"</head>", h, re.I)
    if m:
        h = h[:m.start()] + _DESIGN_KIT_CSS + h[m.start():]
    else:
        return h
    m2 = None
    for m2 in re.finditer(r"</body>", h, re.I):
        pass
    if m2:
        h = h[:m2.start()] + _DESIGN_KIT_JS + h[m2.start():]
    return h


def _site_quality_issues(html):
    """patch38: cheap design-review heuristics; a list of concrete gaps the
    builder must fix in its upgrade pass (empty list = ship it)."""
    h = html or ""
    low = h.lower()
    issues = []
    if len(h) < 14000:
        issues.append("the file is only {:,} bytes — too thin; expand real, specific copy and sections (target 18-34KB)".format(len(h)))
    if low.count("<section") < 5:
        issues.append("fewer than 5 <section> blocks — a real business site needs 6-8 distinct sections")
    if "@media" not in low:
        issues.append("no responsive @media rules")
    if not any(k in low for k in ("intersectionobserver", "@keyframes", "transition")):
        issues.append("no motion at all (add IntersectionObserver reveals, hover transitions, one keyframe animation)")
    if "lorem" in low:
        issues.append("lorem ipsum placeholder text present — write real copy")
    if re.search(r"<img[^>]+src=[\"'][^\"']*(?:placeholder|placehold\.it|example\.com|your-image|image\d*\.(?:jpg|png)|via\.placeholder)", h, re.I):
        issues.append("placeholder image URLs — use the collected IMAGE LIBRARY URLs or CSS/SVG art")
    if "<form" not in low:
        issues.append("no contact/lead form with validation and success state")
    if "<footer" not in low:
        issues.append("no <footer> with anchor links")
    if 'name="viewport"' not in low and "name='viewport'" not in low:
        issues.append("missing viewport meta tag")
    if "<nav" not in low:
        issues.append("no <nav> in the header")
    if re.search(r"\bTODO\b|\bTBD\b", h):
        issues.append("TODO/TBD placeholders left in the page")
    return issues


def build_site(email, name, prompt):
    """Arena-style builder (patch27): designs a complete, professional static
    website matched to the brief — Lovable/Base44-grade template, real copy,
    motion, forms — saved to data/builds/<slug>/ and served same-origin at
    /builds/<slug>/ for a live in-chat preview. Builds cost 10,000 coins/week
    of allowance; publishing a finished build is always free."""
    import shutil  # noqa: F401  (kept for parity with publish; zip uses its own)
    email = (email or "").strip().lower()
    name = (name or "site").strip()[:40]
    prompt = (prompt or "").strip()[:1200]
    if not prompt:
        return {"error": "Describe the site you want, e.g. 'a landing page for a coffee brand in Lagos'."}
    tier = check_tier(email)
    # daily pace cap (abuse guard); coin wallet does the real metering
    now = time.time()
    if not is_admin(email) and tier != "enterprise":
        lst = [t for t in _BUILD_DAILY.get(email, []) if now - t < 86400]
        if len(lst) >= 40:
            return {"error": "Daily build limit reached (40/day). Try again tomorrow or upgrade."}
    _gate = coins_gate(email, COIN_COST_BUILD)
    if _gate is not None:
        return _gate
    _bp_reset(email, name)
    _bp_step(email, "Reading the brief", prompt[:80])
    slug0 = _build_slug(name)
    slug = slug0
    i = 2
    while slug in _builds_load():
        slug = slug0 + "-" + str(i); i += 1
    system = (
        "You are a Lovable/Base44-grade senior product designer AND senior frontend engineer. "
        "OUTPUT FORMAT (strict — a machine reads it): line 1 'TEMPLATE: <family>' (invent a fitting name like "
        "Aurora SaaS, Noir Dining, Editorial Portfolio, Commerce Grid, Bold Agency, Festival Event, Zen Clinic); "
        "line 2 'TITLE: <site name>'; line 3 exactly 'BEGIN index.html'; then the COMPLETE raw HTML file — "
        "no JSON, no markdown fences, no explanations, and nothing after the file except a final 'END' line. "
        "The file starts with <!DOCTYPE html> and ends with </html>; the WHOLE reply stays between 18KB and 34KB (quality matters — do not go below 18KB) — "
        "dense, efficient code (short selectors, compact CSS), not sprawling boilerplate. "
        "First pick the template family, palette and typography that genuinely fit the industry and mood "
        "(a restaurant is NOT a fintech is NOT a personal portfolio). ONE self-contained index.html with inline "
        "<style> and <script>; no external JS/CSS frameworks; a Google Fonts <link> is allowed but must fall "
        "back to system fonts. PROFESSIONAL BAR (non-negotiable): cohesive CSS custom-property palette "
        "(background, surface, text, accent, muted) with real contrast; display font for headings + readable "
        "body font; generous spacing and a strong typographic scale; a hero with a clear value-prop H1, "
        "supporting line, primary + secondary CTA and a hero VISUAL (a photo from the IMAGE LIBRARY when one is provided, "
        "layered with gradients/CSS shapes; otherwise pure CSS/SVG art — never invented image URLs); a sticky translucent "
        "header with a working mobile menu; 5-7 content sections with believable, specific copy for THIS "
        "business (no lorem, no invented statistics); at least one signature motion moment (IntersectionObserver "
        "scroll reveals, hover lifts, animated counters, gradient shift); one functional interactive feature "
        "that fits the template (menu filter, cart drawer demo, tabs, FAQ accordion, gallery lightbox — pick it "
        "and make it work); a form with client-side validation and an inline success state; a footer with real "
        "anchor links; a <title>, meta description and an inline SVG data-URI favicon; smooth anchor scrolling; "
        "visible keyboard focus styles; prefers-reduced-motion respected. Fully responsive, mobile-first. "
        "No TODOs, no placeholders. "
        # patch38: an explicit length contract — without it fast models ship thin 10KB pages
        "LENGTH CONTRACT (hard): the HTML file must be between 20,000 and 30,000 characters. Reach it with SUBSTANCE, "
        "not padding: 7-9 sections, each with a heading, an intro paragraph of 40-70 words and 3-6 cards/items of 20-40 "
        "words each; a 5-6 question FAQ; a footer with 3 columns; complete CSS (~250 lines) with hover/focus/motion states; "
        "and the JS for the interactive feature. Before you finish, count: if the file is under 20,000 characters, add "
        "another real section (testimonials, FAQ, location, team, process) until it is not.")
    _generic = (not name) or name.strip().lower() in ("my-site", "site", "website", "app", "landing page", "page")
    _bpt = _brief_blueprint(prompt)
    _bp_step(email, "Art direction", _bpt.split("|")[0].replace("industry:", "").strip())
    _bp_step(email, "Collecting licensed photos", "Pexels · Openverse · Wikimedia Commons")
    _imgs = _build_image_library(prompt, _bpt)
    _bp_step(email, "Photos collected", "%d images ready" % len(_imgs))
    _img_block = ""
    if _imgs:
        _img_block = (" IMAGE LIBRARY (real licensed photos collected for this brief — use these EXACT URLs for the hero, "
                      "gallery and section cards — pick only the ones that genuinely fit the brief and skip odd ones; every <img> needs loading=\"lazy\", a descriptive alt and a container with a "
                      "gradient background-color so a slow image never leaves a hole; never invent other image URLs; add a small "
                      "'Photos: <sources>' credit line in the footer): "
                      + " ".join("[%d] %s — %s%s (%s, %s)" % (i + 1, x["url"], (x.get("title") or "photo")[:50],
                                                              (" by " + x["author"]) if x.get("author") else "", x.get("license", "CC"), x.get("source", ""))
                                 for i, x in enumerate(_imgs)))
    _bp_step(email, "Building " + (name or "the site"), "thinking through the layout & copy…", kind="build")
    user = ("Build this website now. " + ("Site name: " + name + ". " if not _generic else
            "No brand name was given — invent a short fitting name and put it after 'TITLE: '. ") +
            "Brief: " + prompt +
            " Structure: header with nav, impactful hero, at least five distinct content sections a real "
            "business in this brief would have, and a footer. Match the visual language to the industry. "
            "Remember the strict output format: TEMPLATE line, TITLE line, BEGIN index.html, the complete "
            "HTML file (18-34KB), END. DESIGN BLUEPRINT (art direction — follow it, adapting names/copy to the brief): "
            + _bpt + ". Write like a top Awwwards studio: specific headlines, real-sounding "
            "details (prices, hours, names, locations from the brief or plausible for it), polished micro-interactions." + _img_block)
    c_raw, prov, finish = _llm_text_stream(system, user, max_tokens=16000, effort="medium", on_delta=_writer_progress(email))
    # the model can write past its single-reply token cap — continue the SAME
    # file where it stopped (how real AI builders stream long artifacts)
    for _turn in range(2):
        if not c_raw or "</html>" in c_raw.lower():
            break
        _bp_step(email, "Continuing the file", "the reply hit the length cap — resuming where it stopped")
        cont, prov2, fin2 = _llm_text(system, user, max_tokens=16000, effort="medium", extra_msgs=[
            {"role": "assistant", "content": c_raw},
            {"role": "user", "content": "Continue the file EXACTLY from where you stopped — no repeats, no preamble, no fences, resume mid-line if needed. Finish the document and end with the END line."}])
        if not cont:
            break
        c_raw = c_raw + cont.lstrip("` \r\n")
        prov, finish = prov2, fin2
    data = _parse_builder_reply(c_raw)
    if not data or (finish == "length" and not data):
        c2, prov2, _f2 = _llm_text(system + " The previous reply was too long or malformed and got cut off. "
                                           "This time keep the HTML file itself under 20KB (still professional: "
                                           "cut words, not design).", user, max_tokens=16000)
        d2 = _parse_builder_reply(c2)
        if d2:
            data, prov = d2, prov2
    if not data:
        _bp_done(email, False, "builder brain unavailable")
        return {"error": "The builder brain is unavailable right now (" + str(prov)[:140] + "). Please try again in a moment."}
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list) or not files:
        return {"error": "The AI returned an unexpected format. Please try again with a slightly different brief."}
    files = files[:_BUILD_MAX_FILES]
    clean = []
    for f in files:
        if not isinstance(f, dict):
            continue
        p = str(f.get("path") or "").strip().lstrip("/").replace("\\", "/")
        c = str(f.get("content") or "")
        if not p or ".." in p or p.count("/") > 3 or len(p) > 80:
            continue
        if len(c) > _BUILD_MAX_FILE:
            c = c[:_BUILD_MAX_FILE]
        clean.append({"path": p, "content": c})
    if not any(f["path"] == "index.html" for f in clean):
        return {"error": "The AI did not include an index.html page. Please try again."}
    # patch38: design review — when the first draft misses the bar on 2+ points,
    # one upgrade pass rewrites the complete file against the concrete gap list
    _idx = next(f for f in clean if f["path"] == "index.html")
    _issues = _site_quality_issues(_idx["content"])
    _qa = {"issues_before": len(_issues), "upgraded": False}
    if c_raw and (len(_issues) >= 2 or len(_idx["content"]) < 16000):
        _bp_step(email, "Design review", "%d gap(s) found — rewriting" % len(_issues))
        try:
            # the draft itself is NOT echoed back (fast providers cap prompt tokens per
            # minute); a compact summary + the concrete gap list steers the rewrite
            _h2s = re.findall(r"<h[12][^>]*>(.*?)</h[12]>", _idx["content"], re.I | re.S)
            _h2s = [re.sub(r"<[^>]+>", "", x).strip()[:40] for x in _h2s][:12]
            _summary = ("Your previous draft (TEMPLATE {}, {:,} characters, headings: {}) FAILED the design review on: {}."
                        .format(str(data.get("template") or "?")[:40], len(_idx["content"]),
                                " | ".join(_h2s) or "none", "; ".join(_issues) or "too little substance"))
            _c3, _p3, _f3 = _llm_text(system, user, max_tokens=16000, effort="medium", extra_msgs=[
                {"role": "assistant", "content": "(draft withheld — see review)"},
                {"role": "user", "content": "DESIGN REVIEW — " + _summary + " Write the COMPLETE site again from scratch, "
                                            "fixing every point: honour the LENGTH CONTRACT (20,000-30,000 characters of real "
                                            "substance), keep the same brand, palette and section plan, same strict output format "
                                            "(TEMPLATE line, TITLE line, BEGIN index.html, full HTML, END). No fences, no commentary."}])
            _d3 = _parse_builder_reply(_c3)
            _f3s = (_d3 or {}).get("files") if isinstance(_d3, dict) else None
            _new = next((str(x.get("content") or "") for x in (_f3s or []) if isinstance(x, dict) and str(x.get("path") or "").strip().lstrip("/") == "index.html"), "")
            if _new and "</html>" in _new.lower() and len(_site_quality_issues(_new)) < len(_issues):
                _idx["content"] = _new[:_BUILD_MAX_FILE]
                _qa["upgraded"] = True
                _qa["issues_after"] = len(_site_quality_issues(_new))
                if isinstance(_d3, dict):
                    if _d3.get("template"):
                        data["template"] = _d3.get("template")
                    if _d3.get("name") and _generic:
                        data["name"] = _d3.get("name")
                prov = _p3 or prov
        except Exception:
            pass
    _bp_step(email, "Design kit", "reset · scroll reveals · focus rings · reduced-motion")
    _idx["content"] = _inject_design_kit(_idx["content"])
    _bp_step(email, "Saving the workspace", "data/builds → vault mirror")
    # honour the model's title when the user gave no explicit name
    _gn = str(data.get("name") or "").strip()[:40] if isinstance(data, dict) else ""
    if _generic and _gn:
        name = _gn
    template = str(data.get("template") or "").strip()[:40] if isinstance(data, dict) else ""
    # workspace polish: every project ships a README (code tree + zip feel like a
    # real repo, not one loose html file). index.html itself stays self-contained
    # so the site never breaks when downloaded or published.
    if not any(f["path"] == "README.md" for f in clean):
        _rd = ("# " + (name or "Website") + "\n\nBuilt with **OraCool AI** · template: _"
               + (template or "Custom") + "_\n\nGenerated: " + _now() + "\n\n## Files\n\n"
               + "".join("- `" + f["path"] + "` — " + "{:,}".format(len(f["content"])) + " bytes\n" for f in clean)
               + "\n## Brief\n\n" + (prompt[:400] or "custom site") + "\n\n---\n"
               "index.html is fully self-contained (inline CSS + JS + SVG art), so it runs anywhere: "
               "open it, publish it on OraCool, or push it to GitHub.\n")
        clean.append({"path": "README.md", "content": _rd})
    dest = os.path.join(_BUILDS_DIR, slug)
    try:
        for f in clean:
            fp = os.path.normpath(os.path.join(dest, f["path"]))
            if not fp.startswith(os.path.normpath(dest)):
                continue
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(f["content"])
    except Exception as e:
        _bp_done(email, False, "could not save")
        return {"error": "Could not save the site: " + str(e)[:120]}
    _bp_step(email, "Preview ready", "/builds/" + slug + "/")
    _bp_done(email, True, "/builds/" + slug + "/")
    meta = _builds_load()
    meta[slug] = {"owner": email, "name": name, "brief": prompt[:300], "provider": prov,
                  "files": [f["path"] for f in clean], "t": _now(), "template": template}
    _builds_save(meta)
    bsaved = builds_sup_save(slug, clean)
    _BUILD_DAILY.setdefault(email, []).append(now)
    st = coins_charge(email, COIN_COST_BUILD)
    coins_left = None if (not st or st.get("unlimited")) else st.get("balance")
    return {"ok": True, "slug": slug, "url": "/builds/" + slug + "/", "name": name,
            "template": template, "files": [f["path"] for f in clean], "provider": prov,
            "coins_spent": COIN_COST_BUILD, "coins_left": coins_left,
            "build_saved": bool(bsaved), "qa": _qa, "bytes": len(_idx["content"]),
            "note": "Live preview is in the card above (auto-saved to the account vault — it survives app updates). "
                    "If the user asks to publish, run the publish tool and report the exact url it returns "
                    "(https://oracoolai.com/sites/<name>/ — opens instantly on every device); do NOT invent a "
                    "<name>.oracoolai.com link. Publishing is free. 'How to deploy' also offers Netlify / Vercel / "
                    "GitHub one-click push. Build coins this week show in the header pill."}


def build_list(email):
    builds_warm()
    meta = _builds_load()
    # patch38: a user sees only their OWN builds (admins see everything) — the
    # registry used to leak every account's studio into every Websites panel
    em = (email or "").strip().lower()
    adm = is_admin(em)
    keep = [s for s in meta if adm or (em and str(meta[s].get("owner") or "").strip().lower() == em)]
    # newest first by build time (patch27: slug-alphabetical ordering pushed real
    # builds past the cap once the registry grew)
    out = [dict(meta[s], slug=s) for s in sorted(keep, key=lambda s: (str(meta[s].get("t") or ""), s), reverse=True)]
    return {"ok": True, "builds": out[:50]}

_EDIT_DAILY = {}


def build_edit(email, slug, instructions):
    """patch30: in-place edit of an existing build — the AI builder is now an
    editor too. Free; the workspace re-mirrors to the vault automatically."""
    email = (email or "").strip().lower()
    slug = os.path.basename(str(slug or ""))
    instructions = str(instructions or "").strip()[:900]
    if not slug or not instructions:
        return {"error": "Need a build and what to change."}
    meta = _builds_load()
    b = meta.get(slug)
    if not b:
        return {"error": "No saved build with that name in this account."}
    if (b.get("owner") or "").lower() != email and not is_admin(email):
        return {"error": "That build belongs to a different account."}
    dest = os.path.normpath(os.path.join(_BUILDS_DIR, slug))
    idx = os.path.join(dest, "index.html")
    if not os.path.isfile(idx) and not build_hydrate(slug):
        return {"error": "The build files are unavailable — ask OraCool to rebuild it once, then edit."}
    try:
        old = open(idx, encoding="utf-8").read()
    except Exception as e:
        return {"error": "Could not read the current site: " + str(e)[:120]}
    _bp_reset(email, b.get("name") or slug)
    _bp_step(email, "Reading the current site", "%s · %.1f KB" % (b.get("name") or slug, len(old) / 1024.0), kind="explore")
    _bp_step(email, "Applying your change", instructions[:90], kind="build")
    if len(old) > 180_000:
        return {"error": "This site is too large for one in-chat edit — rebuild it with the change instead."}
    now = time.time()
    recent = [x for x in _EDIT_DAILY.get(email, []) if now - x < 86400]
    if len(recent) >= 60:
        return {"error": "60 edits/day is the fair-use cap — edits are free, this just keeps the AI healthy."}
    c, prov, finish = "", "", ""
    try:
        c, prov, finish = _llm_text_stream(_EDIT_SYS, "REQUEST: " + instructions + "\n\nCURRENT index.html:\n\n" + old,
                                           max_tokens=16000, on_delta=_writer_progress(email, "Rewriting"))
    except Exception:
        pass
    if c and "</html>" not in c.lower():
        try:  # long pages can pass the reply cap — resume mid-document like the builder does
            cont, prov2, _f = _llm_text(_EDIT_SYS, "REQUEST: " + instructions + "\n\nCURRENT index.html:\n\n" + old,
                                        max_tokens=16000, extra_msgs=[
                {"role": "assistant", "content": c},
                {"role": "user", "content": "Continue the document EXACTLY where you stopped. No repeats, no preamble, finish with </html>."}])
            if cont:
                c = c + cont.lstrip("` \r\n")
                prov = prov2
        except Exception:
            pass
    data = _parse_builder_reply(c)
    files = (data or {}).get("files") or []
    new = None
    for f in files:
        if f.get("path") == "index.html" and f.get("content"):
            new = f["content"]
            break
    if new is None and files and files[0].get("content"):
        new = files[0]["content"]
    if not new or "</html>" not in new.lower():
        _bp_done(email, False, "editor returned nothing usable")
        return {"error": "The editor brain returned nothing usable (" + str(prov or "")[:40] +
                "). Try a shorter, concrete request like 'make the hero background deep navy with gold buttons'."}
    if len(new) < max(600, int(len(old) * 0.55)):
        _bp_done(email, False, "edit would have cut the page short")
        return {"error": "That edit would have cut the page short (reply limit) — split it into two smaller changes."}
    _bp_step(email, "Saving the workspace", "%.1f KB → data/builds · vault mirror" % (len(new) / 1024.0), kind="save")
    try:
        os.makedirs(dest, exist_ok=True)
        with open(idx, "w", encoding="utf-8") as fh:
            fh.write(new)
    except Exception as e:
        return {"error": "Could not save the edit: " + str(e)[:120]}
    meta = _builds_load()
    ent = meta.get(slug) or b
    ent["t"] = _now()
    ent["edited"] = instructions[:200]
    if "index.html" not in (ent.get("files") or []):
        ent["files"] = ["index.html"] + list(ent.get("files") or [])
    meta[slug] = ent
    _builds_save(meta)
    saved = builds_sup_save(slug, [{"path": "index.html", "content": new}])
    _EDIT_DAILY[email] = recent + [now]
    _bp_step(email, "Preview refreshed", "/builds/" + slug + "/", kind="ready")
    _bp_done(email, True, "/builds/" + slug + "/")
    return {"ok": True, "slug": slug, "url": "/builds/" + slug + "/",
            "name": ent.get("name") or slug, "files": ["index.html"], "provider": prov,
            "build_saved": bool(saved),
            "note": "The updated site is in the preview card above and in the Websites panel. Edits are free. "
                    "Say 'publish' (or tap the publish button) and the SAME live address updates — no new link."}


def builds_sup_delete(slug):
    url = key("SUPABASE_URL")
    if not url or not key("SUPABASE_SERVICE_KEY"):
        return False
    u = url.rstrip("/")
    eq = urllib.parse.quote(slug, safe="")
    for tbl in ("oracool_builds", "oracool_builds_meta"):
        try:
            http_fetch(u + "/rest/v1/" + tbl + "?slug=eq." + eq, method="DELETE",
                       headers=_site_headers(), timeout=15)
        except Exception:
            pass
    return True


def build_delete(email, slug):
    """Forget a build: files, registry row and vault mirror."""
    email = (email or "").strip().lower()
    slug = os.path.basename(str(slug or ""))
    meta = _builds_load()
    b = meta.get(slug)
    if not b:
        return {"error": "No saved build with that name in this account."}
    if (b.get("owner") or "").lower() != email and not is_admin(email):
        return {"error": "That build belongs to a different account."}
    try:
        import shutil
        shutil.rmtree(os.path.normpath(os.path.join(_BUILDS_DIR, slug)), ignore_errors=True)
        meta = _builds_load()
        meta.pop(slug, None)
        _builds_save(meta)
        builds_sup_delete(slug)
        return {"ok": True, "slug": slug}
    except Exception as e:
        return {"error": str(e)[:160]}


_EDIT_SYS = ("You are OraCool's website editor. You are given the user's current website as ONE "
             "self-contained HTML file and one change request. Apply EXACTLY that change (and only "
             "what it clearly implies) and keep everything else intact: layout, copy, prices, JS "
             "behavior. The page must stay fully self-contained (inline CSS/JS/SVG art only, no "
             "CDNs, no frameworks). Reply with ONLY the complete updated HTML document — no "
             "explanations, no code fences, and never shorten or truncate the file.")


def build_zip(slug):
    slug = os.path.basename(str(slug or ""))
    dest = os.path.normpath(os.path.join(_BUILDS_DIR, slug))
    if not slug or not dest.startswith(os.path.normpath(_BUILDS_DIR)):
        return None
    if not os.path.isdir(dest):
        build_hydrate(slug)
    if not os.path.isdir(dest):
        return None
    import zipfile
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, fns in os.walk(dest):
            for fn in fns:
                fp = os.path.join(root, fn)
                z.write(fp, os.path.join(slug, os.path.relpath(fp, dest)))
    return buf.getvalue()

# ------------------------------------------------------------ GitHub (user's own account, PAT-sealed in the vault)

def _gh(email, path, method="GET", body=None, timeout=30):
    pat = ""
    try:
        r = vault_get(email, "github_pat")
        pat = str(r.get("value") or "")
    except Exception:
        pass
    if not pat:
        return None, "No GitHub account connected (Devices → Connectors → GitHub)."
    try:
        _, raw, _ = http_fetch("https://api.github.com" + path, method=method, timeout=timeout,
                               headers={"Authorization": "Bearer " + pat, "Accept": "application/vnd.github+json",
                                        "User-Agent": "oracool-builder"},
                               json_body=body)
        return json.loads(raw), None
    except Exception as e:
        return None, str(e)[:140]

def github_connect(email, pat):
    pat = (pat or "").strip()
    if not pat or len(pat) < 20:
        return {"error": "Paste a full GitHub personal access token (classic or fine-grained with repo scope)."}
    try:
        _, raw, _ = http_fetch("https://api.github.com/user", method="GET", timeout=30,
                               headers={"Authorization": "Bearer " + pat, "Accept": "application/vnd.github+json",
                                        "User-Agent": "oracool-builder"})
        u = json.loads(raw)
    except Exception as e:
        return {"error": "GitHub rejected the token: " + str(e)[:140]}
    if u.get("error") or not u.get("login"):
        return {"error": "GitHub says this token is invalid — create a new one (Settings → Developer settings → Personal access tokens)."}
    v = vault_set(email, "github_pat", pat)
    if v.get("status") != "ok":
        return {"error": v.get("error", "Could not store the token.")}
    return {"ok": True, "login": u.get("login"), "html_url": u.get("html_url"),
            "note": "Connected as @" + str(u.get("login")) + " — the token is stored AES-encrypted in your vault. OraCool can now list repos, create repos, push built sites and enable GitHub Pages for you."}

def github_status(email):
    try:
        r = vault_get(email, "github_pat")
        if r.get("value"):
            _, raw, _ = http_fetch("https://api.github.com/user", method="GET", timeout=20,
                                   headers={"Authorization": "Bearer " + str(r["value"]), "Accept": "application/vnd.github+json",
                                            "User-Agent": "oracool-builder"})
            u = json.loads(raw)
            return {"connected": bool(u.get("login")), "login": u.get("login")}
    except Exception:
        pass
    return {"connected": False}

def github_repos(email, only_mine=True):
    q = "?per_page=30&sort=updated&type=owner" if only_mine else "?per_page=30&sort=updated"
    d, err = _gh(email, "/user/repos" + q)
    if err:
        return {"error": err}
    return {"ok": True, "repos": [{"name": r.get("name"), "private": bool(r.get("private")),
                                   "url": r.get("html_url"), "default_branch": r.get("default_branch")}
                                  for r in (d or [])[:30]]}

def github_create_repo(email, name, private=False):
    name = re.sub(r"[^A-Za-z0-9._-]", "", name or "")[:60]
    if not name:
        return {"error": "Give the repository a name."}
    d, err = _gh(email, "/user/repos", method="POST", body={"name": name, "private": bool(private),
                                                            "auto_init": False})
    if err:
        return {"error": err}
    if d.get("full_name"):
        return {"ok": True, "repo": d.get("full_name"), "url": d.get("html_url")}
    return {"error": (d.get("message") or "Could not create the repository.")[:160]}

def github_push_build(email, slug, repo, enable_pages=True):
    slug = os.path.basename(str(slug or ""))
    dest = os.path.normpath(os.path.join(_BUILDS_DIR, slug))
    if not slug or not dest.startswith(os.path.normpath(_BUILDS_DIR)) or not os.path.isdir(dest):
        return {"error": "That build no longer exists on this server (builds live on disk)."}
    m = re.match(r"^([^/]+)/([^/]+)$", (repo or "").strip())
    if not m:
        return {"error": "Pick a repository like 'yourname/sitename' (or create one first)."}
    owner, rname = m.group(1), m.group(2)
    # ensure the repo exists
    d, err = _gh(email, "/repos/" + owner + "/" + rname)
    if err:
        return {"error": err}
    if not d or d.get("message") == "Not Found" or "404" in str(d.get("message", "")):
        cr = github_create_repo(email, rname, private=True)
        if not cr.get("ok"):
            return cr
        d = _gh(email, "/repos/" + owner + "/" + rname)[0]
        if not d or not d.get("full_name"):
            return {"error": "Repository creation did not confirm — check your GitHub and retry."}
    pushed, failed = [], []
    for root, _dirs, fns in os.walk(dest):
        for fn in sorted(fns):
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, dest).replace(os.sep, "/")
            if os.path.getsize(fp) > 1_000_000:
                failed.append(rel)
                continue
            content = base64.b64encode(open(fp, "rb").read()).decode()
            r2, e2 = _gh(email, "/repos/" + owner + "/" + rname + "/contents/" + rel,
                         method="PUT", body={"message": "Deploy " + slug + " (OraCool builder)",
                                             "content": content, "branch": "main"}, timeout=40)
            if e2 or not r2 or not r2.get("content"):
                # file may exist on another branch / need sha — retry as create-on-master
                r2, e2 = _gh(email, "/repos/" + owner + "/" + rname + "/contents/" + rel,
                             method="PUT", body={"message": "Deploy " + slug + " (OraCool builder)",
                                                 "content": content}, timeout=40)
            if r2 and r2.get("content"):
                pushed.append(rel)
            else:
                failed.append(rel + (" (" + str(e2 or (r2 or {}).get("message", "unknown"))[:60] + ")"))
    if not pushed:
        return {"error": "GitHub push failed for every file: " + "; ".join(failed[:3])[:300]}
    out = {"ok": True, "repo": owner + "/" + rname, "url": "https://github.com/" + owner + "/" + rname,
           "pushed": pushed, "failed": failed}
    if enable_pages and not failed:
        pages, pe = _gh(email, "/repos/" + owner + "/" + rname + "/pages", method="PUT",
                        body={"source": {"branch": "main", "path": "/"}}, timeout=40)
        if pages and not pe:
            out["pages"] = "enabled"
            out["pages_url"] = "https://" + owner.lower() + ".github.io/" + rname.lower() + "/"
    return out

# ============================================== published sites (patch27 hosting)
# One tap publishes a build to https://<sub>.oracoolai.com (Base44-style). Files
# copy to data/sites/<sub>/ and mirror to Supabase (published_sites) so sites
# survive redeploys; unknown-host 404s fall through. Owners may later attach a
# custom domain (they point DNS at OraCool; we route by Host header).
_SITES_DIR = os.path.join(DATA_DIR, "sites")
_SITES_LOCK = threading.Lock()
_SITE_HOST_CACHE = {}           # host -> (sub or None, expires_at)
_SITE_EXIST_CACHE = {}          # sub -> (bool, expires_at)
_SITE_RESERVED = {"www", "app", "api", "admin", "sites", "builds", "assets", "static",
                  "mail", "cdn", "help", "support", "blog", "status", "dev", "staging"}
_SITE_TEXT_EXT = {".html", ".css", ".js", ".mjs", ".json", ".svg", ".txt", ".md", ".xml", ".webmanifest"}


def _sites_registry():
    return os.path.join(_SITES_DIR, "registry.json")


def _sites_load():
    try:
        with open(_sites_registry(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _sites_save(d):
    with _SITES_LOCK:
        try:
            os.makedirs(_SITES_DIR, exist_ok=True)
            with open(_sites_registry(), "w", encoding="utf-8") as f:
                json.dump(d, f, indent=1)
        except Exception:
            pass


def _sub_valid(sub):
    return (bool(re.fullmatch(r"[a-z0-9]([a-z0-9-]{1,38}[a-z0-9])?", sub or ""))
            and sub not in _SITE_RESERVED)


def _site_headers():
    return {"apikey": key("SUPABASE_SERVICE_KEY"), "Authorization": "Bearer " + key("SUPABASE_SERVICE_KEY"),
            "Content-Type": "application/json"}


def site_sup_save(sub, meta, files):
    """Best-effort durable mirror of a published site (text files only)."""
    url = key("SUPABASE_URL")
    if not url or not key("SUPABASE_SERVICE_KEY"):
        return False
    u = url.rstrip("/")
    try:
        http_fetch(u + "/rest/v1/published_sites?sub=eq." + urllib.parse.quote(sub, safe=""),
                   method="DELETE", headers=_site_headers(), timeout=15)
        rows = [{"sub": sub, "path": f["path"], "content": f["content"][:1_500_000]}
                for f in files if os.path.splitext(f["path"])[1].lower() in _SITE_TEXT_EXT]
        if rows:
            http_fetch(u + "/rest/v1/published_sites", method="POST",
                       headers=dict(_site_headers(), **{"Prefer": "return=minimal"}),
                       json_body=rows, timeout=25)
        mrow = {"sub": sub, "owner": meta.get("owner", ""), "name": meta.get("name", ""),
                "source_slug": meta.get("source_slug", ""), "published_at": meta.get("published_at", ""),
                "hits": int(meta.get("hits") or 0), "custom_domain": meta.get("custom_domain", "")}
        http_fetch(u + "/rest/v1/published_sites_meta", method="POST",
                   headers=dict(_site_headers(), **{"Prefer": "return=minimal, resolution=merge-duplicates"}),
                   json_body=mrow, timeout=15)
        return True
    except Exception:
        return False


def site_sup_load(sub):
    """Hydrate a site's text files from Supabase. Returns list of {path, content} or None."""
    url = key("SUPABASE_URL")
    if not url or not key("SUPABASE_SERVICE_KEY"):
        return None
    try:
        _, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/published_sites?sub=eq." + urllib.parse.quote(sub, safe="")
                               + "&select=path,content&order=path.asc",
                               headers=_site_headers(), timeout=15)
        rows = json.loads(raw) if raw else []
        return rows or None
    except Exception:
        return None


def site_sup_meta(field, value):
    """Look up a published site by custom_domain or sub in the durable meta table."""
    url = key("SUPABASE_URL")
    if not url or not key("SUPABASE_SERVICE_KEY"):
        return None
    try:
        _, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/published_sites_meta?" + field + "=eq."
                               + urllib.parse.quote(str(value), safe="") + "&select=*",
                               headers=_site_headers(), timeout=10)
        rows = json.loads(raw) if raw else []
        return rows[0] if rows else None
    except Exception:
        return None


_BUILDS_SUP_EXT = _SITE_TEXT_EXT | {".md"}
_builds_warmed = False


def builds_sup_save(slug, files):
    """Mirror a build workspace + its registry row to Supabase so previews
    survive redeploys (Render's disk is ephemeral). Best effort."""
    url = key("SUPABASE_URL")
    if not url or not key("SUPABASE_SERVICE_KEY"):
        return False
    u = url.rstrip("/")
    try:
        http_fetch(u + "/rest/v1/oracool_builds?slug=eq." + urllib.parse.quote(slug, safe=""),
                   method="DELETE", headers=_site_headers(), timeout=15)
        rows = [{"slug": slug, "path": f["path"], "content": f["content"][:1_500_000]}
                for f in files if os.path.splitext(f["path"])[1].lower() in _BUILDS_SUP_EXT]
        if rows:
            http_fetch(u + "/rest/v1/oracool_builds", method="POST",
                       headers=dict(_site_headers(), **{"Prefer": "return=minimal"}),
                       json_body=rows, timeout=30)
        m = _builds_load().get(slug) or {}
        mrow = {"slug": slug, "owner": m.get("owner", ""), "name": m.get("name", ""),
                "brief": (m.get("brief") or "")[:300], "provider": m.get("provider", ""),
                "template": m.get("template", ""), "t": m.get("t", ""),
                "files": json.dumps(m.get("files") or [])}
        http_fetch(u + "/rest/v1/oracool_builds_meta", method="POST",
                   headers=dict(_site_headers(), **{"Prefer": "return=minimal, resolution=merge-duplicates"}),
                   json_body=mrow, timeout=15)
        return True
    except Exception:
        return False


def build_hydrate(slug):
    """Restore a build workspace (and its registry row) from the durable vault
    after an ephemeral-disk wipe. Returns True when index.html is present."""
    slug = os.path.basename(str(slug or ""))
    if not slug:
        return False
    d = os.path.normpath(os.path.join(_BUILDS_DIR, slug))
    if not d.startswith(os.path.normpath(_BUILDS_DIR)):
        return False
    if os.path.isfile(os.path.join(d, "index.html")):
        return True
    url = key("SUPABASE_URL")
    if not url or not key("SUPABASE_SERVICE_KEY"):
        return False
    u = url.rstrip("/")
    try:
        _, raw, _ = http_fetch(
            u + "/rest/v1/oracool_builds?slug=eq." + urllib.parse.quote(slug, safe="")
            + "&select=path,content&order=path.asc", headers=_site_headers(), timeout=20)
        rows = json.loads(raw) if raw else []
        if not rows:
            return False
        os.makedirs(d, exist_ok=True)
        for r in rows:
            p = str(r.get("path") or "").strip().lstrip("/").replace("\\", "/")
            if not p or ".." in p:
                continue
            fp = os.path.normpath(os.path.join(d, p))
            if not fp.startswith(d):
                continue
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(str(r.get("content") or ""))
        if not os.path.isfile(os.path.join(d, "index.html")):
            return False
        meta = _builds_load()
        if slug not in meta:
            _, raw2, _ = http_fetch(
                u + "/rest/v1/oracool_builds_meta?slug=eq." + urllib.parse.quote(slug, safe=""),
                headers=_site_headers(), timeout=15)
            g = (json.loads(raw2) or [{}])[0] if raw2 else {}
            try:
                fl = json.loads(g.get("files") or "[]")
            except Exception:
                fl = []
            meta[slug] = {"owner": g.get("owner") or "", "name": g.get("name") or slug,
                          "brief": g.get("brief") or "", "t": g.get("t") or _now(),
                          "provider": g.get("provider") or "", "template": g.get("template") or "",
                          "files": [x for x in fl if isinstance(x, str)]}
            _builds_save(meta)
        return True
    except Exception:
        return False


def builds_warm():
    """Once per process: rebuild the registry from the vault so 'My builds'
    lists survive redeploys even before anyone opens a preview."""
    global _builds_warmed
    if _builds_warmed:
        return
    _builds_warmed = True
    url = key("SUPABASE_URL")
    if not url or not key("SUPABASE_SERVICE_KEY"):
        return
    try:
        _, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/oracool_builds_meta?select=*",
                               headers=_site_headers(), timeout=15)
        rows = json.loads(raw) if raw else []
        if not isinstance(rows, list):
            return
        meta = _builds_load()
        changed = False
        for g in rows:
            slug = os.path.basename(str(g.get("slug") or ""))
            if not slug or slug in meta:
                continue
            try:
                fl = json.loads(g.get("files") or "[]")
            except Exception:
                fl = []
            meta[slug] = {"owner": g.get("owner") or "", "name": g.get("name") or slug,
                          "brief": g.get("brief") or "", "t": g.get("t") or _now(),
                          "provider": g.get("provider") or "", "template": g.get("template") or "",
                          "files": [x for x in fl if isinstance(x, str)]}
            os.makedirs(os.path.join(_BUILDS_DIR, slug), exist_ok=True)
            changed = True
        if changed:
            _builds_save(meta)
    except Exception:
        pass


def site_hydrate(sub):
    """If local files vanished (redeploy), restore from Supabase and re-cache on disk."""
    rows = site_sup_load(sub)
    if not rows:
        return False
    dest = os.path.normpath(os.path.join(_SITES_DIR, sub))
    try:
        for r in rows:
            p = str(r.get("path") or "").strip().lstrip("/").replace("\\", "/")
            if not p or ".." in p:
                continue
            fp = os.path.normpath(os.path.join(dest, p))
            if not fp.startswith(dest):
                continue
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(str(r.get("content") or ""))
        reg = _sites_load()
        if sub not in reg:
            _m = site_sup_meta("sub", sub) or {}
            reg[sub] = {"owner": _m.get("owner") or "", "name": _m.get("name") or sub,
                        "source_slug": _m.get("source_slug") or "", "published_at": _m.get("published_at") or "",
                        "hits": int(_m.get("hits") or 0), "custom_domain": _m.get("custom_domain") or ""}
            _sites_save(reg)
        return True
    except Exception:
        return False


def site_exists(sub):
    now = time.time()
    hit = _SITE_EXIST_CACHE.get(sub)
    if hit and hit[1] > now:
        return hit[0]
    ok = False
    if _sub_valid(sub):
        ok = sub in _sites_load()
        if not ok:
            ok = bool(site_sup_meta("sub", sub))
    _SITE_EXIST_CACHE[sub] = (ok, now + 60)
    return ok


def site_tree(base):
    out = []
    for root, _dirs, names in os.walk(base):
        for n in names:
            fp = os.path.join(root, n)
            rp = os.path.relpath(fp, base).replace(os.sep, "/")
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            if rp.endswith(".zip"):
                continue
            out.append((rp, fp, sz))
    out.sort()
    return out


def site_publish(email, slug, sub=""):
    """Publish (or re-publish) a build. Re-publish with the same sub updates the live
    site in place — that is also how edits go live."""
    import shutil
    email = (email or "").strip().lower()
    meta = _builds_load()
    b = meta.get(slug) or {}
    if not b:
        return {"error": "Nothing built yet under that name — ask the AI to build a site first, "
                         "or use the PUBLISH button on a build card."}
    if (b.get("owner") or "").lower() != email and not is_admin(email):
        return {"error": "That build belongs to a different account."}
    src_dir = os.path.normpath(os.path.join(_BUILDS_DIR, slug))
    if not os.path.isfile(os.path.join(src_dir, "index.html")):
        build_hydrate(slug)
    if not os.path.isfile(os.path.join(src_dir, "index.html")):
        return {"error": "This build predates the auto-save vault — ask OraCool to rebuild it once (new builds are saved forever), then publish."}
    sub_raw = str(sub or "").strip()
    if sub_raw:
        sub = _build_slug(sub_raw)
        if not _sub_valid(sub):
            return {"error": "That subdomain is not allowed (use 3-40 lowercase letters, digits or dashes, and not a reserved word)."}
    else:
        sub = _build_slug(str(b.get("name") or slug))
    reg = _sites_load()
    cur = reg.get(sub)
    if cur and (cur.get("owner") or "").lower() != email and not is_admin(email):
        return {"error": "That subdomain is taken — try another, e.g. '" + sub + "-" + os.urandom(2).hex() + "'."}
    dest = os.path.normpath(os.path.join(_SITES_DIR, sub))
    try:
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(_SITES_DIR, exist_ok=True)
        shutil.copytree(src_dir, dest)
    except Exception as e:
        return {"error": "Could not copy the site files: " + str(e)[:120]}
    reg = _sites_load()
    reg[sub] = {"owner": email, "name": b.get("name") or sub, "source_slug": slug,
                "published_at": _now(), "hits": (reg.get(sub) or {}).get("hits", 0),
                "custom_domain": (reg.get(sub) or {}).get("custom_domain", ""),
                "template": b.get("template") or ""}
    _sites_save(reg)
    files = []
    for rp, fp, sz in site_tree(dest):
        if sz > 1_500_000:
            continue
        try:
            with open(fp, encoding="utf-8") as fh:
                files.append({"path": rp, "content": fh.read()})
        except Exception:
            pass
    durable = site_sup_save(sub, reg[sub], files)
    _SITE_EXIST_CACHE[sub] = (True, time.time() + 3600)
    return {"ok": True, "sub": sub, "slug": slug,
            "url": "https://oracoolai.com/sites/" + sub + "/",
            "vanity_url": "https://" + sub + ".oracoolai.com/",
            "path_url": "/sites/" + sub + "/",
            "name": reg[sub]["name"], "durable": bool(durable),
            "share_url": reg[sub].get("custom_domain") and ("https://" + reg[sub]["custom_domain"] + "/") or ("https://" + sub + ".oracoolai.com/"),
            "note": ("Live at " + "https://oracoolai.com/sites/" + sub + "/ — that link opens right now on every device. "
                     "The short vanity link https://" + sub + ".oracoolai.com/ becomes active once wildcard DNS is set up "
                     "(CNAME * -> oracool-ai.onrender.com). Both serve the same saved copy. "
                     "Later, users can buy any domain and attach it from the Publish panel (we serve it by host). "
                     + ("Files are mirrored to Supabase and survive redeploys." if durable else
                        "Durable mirror unavailable (Supabase not configured) — files live on this server."))}


def site_unpublish(email, sub):
    import shutil
    sub = (sub or "").strip().lower()
    reg = _sites_load()
    cur = reg.get(sub)
    if not cur:
        return {"error": "No published site under '" + str(sub)[:40] + "'."}
    if (cur.get("owner") or "").lower() != (email or "").lower() and not is_admin(email):
        return {"error": "Only the owner (or an admin) can unpublish that site."}
    shutil.rmtree(os.path.normpath(os.path.join(_SITES_DIR, sub)), ignore_errors=True)
    reg = _sites_load()
    reg.pop(sub, None)
    _sites_save(reg)
    _SITE_EXIST_CACHE[sub] = (False, time.time() + 60)
    url = key("SUPABASE_URL")
    if url and key("SUPABASE_SERVICE_KEY"):
        try:
            http_fetch(url.rstrip("/") + "/rest/v1/published_sites?sub=eq." + urllib.parse.quote(sub, safe=""),
                       method="DELETE", headers=_site_headers(), timeout=15)
            http_fetch(url.rstrip("/") + "/rest/v1/published_sites_meta?sub=eq." + urllib.parse.quote(sub, safe=""),
                       method="DELETE", headers=_site_headers(), timeout=15)
        except Exception:
            pass
    return {"ok": True, "note": "'" + sub + "' is unpublished. Rebuild/publish any time to go live again."}


def site_domain_set(email, sub, domain):
    sub = (sub or "").strip().lower()
    domain = re.sub(r"^https?://", "", str(domain or "").strip().lower()).split("/")[0].strip(".")
    reg = _sites_load()
    cur = reg.get(sub)
    if not cur:
        return {"error": "No published site under '" + str(sub)[:40] + "'."}
    if (cur.get("owner") or "").lower() != (email or "").lower() and not is_admin(email):
        return {"error": "Only the owner can attach a domain to that site."}
    if domain and not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+", domain):
        return {"error": "That does not look like a valid domain (e.g. mystudio.com)."}
    reg[sub]["custom_domain"] = domain
    _sites_save(reg)
    for h in list(_SITE_HOST_CACHE):
        _SITE_HOST_CACHE.pop(h, None)
    try:
        if key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY"):
            http_fetch(key("SUPABASE_URL").rstrip("/") + "/rest/v1/published_sites_meta", method="POST",
                       headers=dict(_site_headers(), **{"Prefer": "return=minimal, resolution=merge-duplicates"}),
                       json_body=dict({"sub": sub, "owner": reg[sub].get("owner", ""),
                                       "name": reg[sub].get("name", ""),
                                       "source_slug": reg[sub].get("source_slug", ""),
                                       "published_at": reg[sub].get("published_at", ""),
                                       "hits": int(reg[sub].get("hits") or 0),
                                       "custom_domain": domain}, ), timeout=15)
    except Exception:
        pass
    if not domain:
        return {"ok": True, "note": "Custom domain removed — the site stays live on its OraCool subdomain."}
    return {"ok": True, "domain": domain,
            "note": "Attached. At your domain registrar, add a CNAME for 'www' pointing to oracoolai.com "
                    "and an A/ALIAS record for '@' pointing to oracoolai.com — once DNS resolves (minutes to "
                    "hours), https://" + domain + " serves your site automatically. Keep the subdomain live too."}


def sites_list(email):
    reg = _sites_load()
    out = []
    for s, v in sorted(reg.items()):
        if email and (v.get("owner") or "").lower() != email and not is_admin(email):
            continue
        out.append({"sub": s, "url": "https://" + s + ".oracoolai.com/", "path_url": "/sites/" + s + "/",
                    "name": v.get("name") or s, "source_slug": v.get("source_slug") or "",
                    "published_at": v.get("published_at") or "", "hits": int(v.get("hits") or 0),
                    "custom_domain": v.get("custom_domain") or "", "template": v.get("template") or ""})
    return {"ok": True, "sites": out[:50]}


def site_host_lookup(host):
    """Map an incoming Host header to a published sub (subdomain or custom domain)."""
    host = (host or "").split(":")[0].strip().lower()
    if not host:
        return None
    now = time.time()
    hit = _SITE_HOST_CACHE.get(host)
    if hit and hit[1] > now:
        return hit[0]
    sub = None
    if host.endswith(".oracoolai.com"):
        cand = host[: -len(".oracoolai.com")]
        if _sub_valid(cand) and cand in _sites_load():
            sub = cand
        elif _sub_valid(cand) and site_exists(cand):
            sub = cand
    elif host not in ("oracoolai.com", "www.oracoolai.com"):
        reg = _sites_load()
        for s, v in reg.items():
            if (v.get("custom_domain") or "") == host:
                sub = s
                break
        if sub is None and "." in host and key("SUPABASE_URL"):
            m = site_sup_meta("custom_domain", host)
            if m and m.get("sub"):
                sub = str(m["sub"])
    _SITE_HOST_CACHE[host] = (sub, now + (60 if sub else 300))
    return sub


def site_serve(self, sub, rel):
    """Stream a published site's file (index.html at '/'), counting page hits."""
    root = os.path.normpath(os.path.join(_SITES_DIR, sub))
    if not os.path.isdir(root) and not site_hydrate(sub):
        self.send_error(404, "not published")
        return
    rel = urllib.parse.unquote(rel or "/")
    full = os.path.normpath(os.path.join(root, rel.lstrip("/")))
    if not full.startswith(root):
        self.send_error(404)
        return
    if full == root or os.path.isdir(full):
        full = os.path.join(full, "index.html")
    elif not os.path.exists(full):
        alt = full + ".html"
        full = alt if os.path.exists(alt) else os.path.join(root, "index.html")
    try:
        with open(full, "rb") as f:
            data = f.read()
    except OSError:
        self.send_error(404)
        return
    ext = os.path.splitext(full)[1].lower()
    ctype = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript",
             ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
             ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon",
             ".txt": "text/plain", ".md": "text/plain", ".xml": "application/xml",
             ".webmanifest": "application/manifest+json", ".woff2": "font/woff2"}.get(ext, "application/octet-stream")
    if ext in (".html", ""):
        try:
            reg = _sites_load()
            if sub in reg:
                reg[sub]["hits"] = int(reg[sub].get("hits") or 0) + 1
                _sites_save(reg)
        except Exception:
            pass
    self.send_response(200)
    self.send_header("Content-Type", ctype)
    self.send_header("Content-Length", str(len(data)))
    self.send_header("Cache-Control", "no-cache, must-revalidate")
    self.send_header("X-Frame-Options", "ALLOWALL")
    self.end_headers()
    try:
        self.wfile.write(data)
    except Exception:
        pass


def gen_image(prompt, aspect_ratio="1:1"):
    """Text-to-image cascade: NexaAPI (creator's primary) → HiAPI → TokenMix →
    Pollinations FLUX → CVRON flux. Provider capacity and free quotas vary;
    every provider failure is surfaced honestly in `note`."""
    prompt = (prompt or "").strip()
    if not prompt:
        return {"error": "Describe the image you want, e.g. 'a neon city at night'."}
    if len(prompt) < 3:
        return {"error": "Prompt too short."}
    failures = []
    # 0) Agnes free multimodal gateway — currently the most reliably live image engine
    ag = _agnes_image(prompt)
    if ag.get("ok"):
        return {"ok": True, "provider": "agnes-free", "model": KEYS.get("AGNES_IMAGE_MODEL", "agnes-image-2.1-flash"),
                "prompt": prompt, "images": ag["urls"]}
    if ag.get("error") and ag["error"] != "no key":
        failures.append("Agnes: " + str(ag["error"])[:120])
    # 0b) Google Gemini image (when the key is connected and the project is permitted)
    if gemini_key() and not _gemini_skipped():
        gi = _gemini_image(prompt)
        if gi.get("ok"):
            return {"ok": True, "provider": "gemini", "model": gi.get("model", "gemini-image"),
                    "prompt": prompt, "images": gi["urls"]}
        if gi.get("error"):
            failures.append("Gemini: " + str(gi["error"])[:140])
    # 1) NexaAPI (sk- key; needs balance)
    nx = _nexa_image(prompt, aspect_ratio)
    if nx.get("ok"):
        return {"ok": True, "provider": "nexaapi", "model": nx.get("model", ""),
                "prompt": prompt, "images": nx.get("urls", [])}
    if nx.get("error"):
        failures.append("NexaAPI: " + str(nx["error"])[:140])
    # 2) HiAPI — premium models (needs account balance)
    k = key("HIA_API_KEY")
    if k:
        model = KEYS.get("HIA_IMAGE_MODEL", HIA_IMAGE_DEFAULT)
        tid, err = hiapi_submit(model, {"prompt": prompt,
                                        "aspect_ratio": aspect_ratio or "1:1",
                                        "resolution": "1K"}, k)
        if tid:
            res = hiapi_poll(tid, k, budget=90)
            if res.get("ok"):
                return {"ok": True, "provider": "hiapi", "model": model,
                        "prompt": prompt, "images": res.get("urls", [])}
            failures.append("HiAPI: " + str(res.get("error"))[:140])
        else:
            failures.append("HiAPI: " + (err or "no task id"))
    # 3) TokenMix — OpenAI-compatible gateway
    tm = _tokenmix_image(prompt, aspect_ratio)
    if tm.get("ok"):
        return {"ok": True, "provider": "tokenmix", "model": tm.get("model", ""),
                "prompt": prompt, "images": tm.get("urls", [])}
    if tm.get("error"):
        failures.append("TokenMix: " + str(tm["error"])[:140])
    # 4) Pollinations fallback — availability and quotas vary
    pl = _pollinations_image(prompt, aspect_ratio)
    if pl.get("ok"):
        out = {"ok": True, "provider": "oracool-free (FLUX)", "model": "flux",
               "prompt": prompt, "images": [pl["url"]]}
        if failures:
            out["note"] = ("Paid image providers were unavailable (" + "; ".join(failures)
                           + ") — this image used a free HD engine. "
                           "Top up NexaAPI/HiAPI/TokenMix for premium quality.")
        return out
    # 5) CVRON free flux
    cv = _cvron_image(prompt)
    if cv.get("ok"):
        return {"ok": True, "provider": "cvron-free", "model": "flux-dev",
                "prompt": prompt, "images": cv["urls"],
                "note": "Served by CVRON's free flux API." + ((" Paid providers: " + "; ".join(failures)) if failures else "")}
    return {"error": "Image generation unavailable — " + " | ".join(failures + ["free engines failed"])}


def _tts_narration(text):
    """Neural TTS → /generated/<id>.mp3 or None. Gemini neural voice first
    (when the Google key is connected and permitted), free edge-tts fallback."""
    if gemini_key() and not _gemini_skipped():
        m, err = _gemini_tts(text)
        if m:
            return m
    try:
        import asyncio
        import edge_tts
        fn = os.path.join(_gen_dir(), "tts-" + secrets.token_hex(6) + ".mp3")
        for voice in ("en-NG-AbeoNeural", "en-NG-EzinneNeural", "en-US-ChristopherNeural"):
            try:
                async def _run(v=voice):
                    c = edge_tts.Communicate(text[:420], v)
                    await c.save(fn)
                asyncio.run(_run())
                if os.path.exists(fn) and os.path.getsize(fn) > 1500:
                    return "/generated/" + os.path.basename(fn)
            except Exception:
                continue
    except Exception as e:
        print("TTS narration failed:", str(e)[:140])
    return None

def _mux_video_audio(video_url, audio_url):
    """Mux the narration into the silent clip using the bundled static ffmpeg
    (imageio-ffmpeg ships the binary — no apt needed). Returns /generated/<id>.mp4."""
    try:
        import subprocess
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        def _local(u):
            return os.path.join(DATA_DIR, "generated", os.path.basename(u))
        vf, af = _local(video_url), _local(audio_url)
        if not (os.path.exists(vf) and os.path.exists(af)):
            return None
        out = os.path.join(_gen_dir(), "va-" + secrets.token_hex(6) + ".mp4")
        subprocess.run([exe, "-y", "-i", vf, "-i", af,
                        "-c:v", "copy", "-c:a", "aac", "-shortest", out],
                       check=True, timeout=240, capture_output=True)
        if os.path.exists(out) and os.path.getsize(out) > 10000:
            return "/generated/" + os.path.basename(out)
    except Exception as e:
        print("video+audio mux failed:", str(e)[:140])
    return None

def _gen_video_raw(prompt, duration=None, want_audio=False):
    """Text-to-video cascade: Agnes free → Hugging Face Wan 2.2 (router with a
    free HF token, else the live public Wan2.2 I2V-14B Lightning spaces) →
    HiAPI (premium) → CVRON free WAN-22. Errors are surfaced honestly."""
    prompt = (prompt or "").strip()
    if not prompt:
        return {"error": "Describe the video you want, e.g. 'a drone flying over a rainforest'."}
    if len(prompt) < 3:
        return {"error": "Prompt too short."}
    want_audio = bool(want_audio)
    failures = []
    # -1) Google Veo 3.1 — NATIVE synchronized audio (real speech + ambient sound)
    if want_audio and gemini_key() and not _gemini_skipped():
        gv = _gemini_veo_video(prompt, duration=8)
        if gv.get("ok"):
            return {"ok": True, "provider": "google-veo31", "model": "veo-3.1-generate-preview",
                    "prompt": prompt, "videos": gv["urls"],
                    "audio": "native synchronized audio (Google Veo 3.1 — real speech + ambient sound)"}
        if gv.get("error"):
            failures.append("Veo 3.1: " + gv["error"][:140])
    # 0) Agnes free video (t2v) — skipped first when audio was requested (silent model)
    if not want_audio:
        ag = _agnes_video(prompt, duration)
        if ag.get("ok"):
            return {"ok": True, "provider": "agnes-free", "model": KEYS.get("AGNES_VIDEO_MODEL", "agnes-video-2.5-flash"),
                    "prompt": prompt, "videos": ag["urls"], "audio": False,
                    "note": "Free Agnes engine — silent clip. Ask with 'sound' to route to an audio model once HiAPI has credits."}
        if ag.get("error") and ag["error"] != "no key":
            failures.append("Agnes: " + str(ag["error"])[:130])
        # Wan 2.2 (the owner-requested models): HF Inference Providers when a
        # free HF token is set, otherwise the live public Wan2.2 I2V spaces.
        hf = _hf_wan22_video(prompt)
        if hf.get("ok"):
            return {"ok": True, "provider": "hf-wan22", "model": hf.get("model", "Wan2.2"),
                    "via": hf.get("via", ""), "prompt": prompt, "videos": hf["urls"],
                    "audio": False,
                    "note": "Generated with Hugging Face Wan 2.2 (" + str(hf.get("via") or "") + "). "
                            "Silent clip — ask with 'sound' for an audio model."}
        if hf.get("error"):
            failures.append("Wan2.2: " + str(hf["error"])[:160])
    k = key("HIA_API_KEY")
    if k:
        if want_audio:
            model = KEYS.get("HIA_VIDEO_AUDIO_MODEL", "veo-3.1/text-to-video")
        else:
            model = KEYS.get("HIA_VIDEO_MODEL", HIA_VIDEO_DEFAULT)
        inp = {"prompt": prompt}
        if want_audio:
            inp["prompt"] = (prompt + " [include natural synchronized audio: ambient sound, "
                             "and clear spoken voice if anyone speaks in the scene]")
            inp["generate_audio"] = True
        if duration:
            inp["duration"] = duration
        tid, err = hiapi_submit(model, inp, k)
        if tid:
            res = hiapi_poll(tid, k, budget=150)
            if res.get("ok"):
                return {"ok": True, "provider": "hiapi", "model": model,
                        "prompt": prompt, "videos": res.get("urls", []),
                        "audio": "synchronized audio generated (Veo/sound-aware model)" if want_audio else "model did not request audio"}
            failures.append("HiAPI: " + str(res.get("error"))[:140])
        else:
            failures.append("HiAPI: " + (err or "no task id"))
    else:
        failures.append("HiAPI: key not configured")
    if want_audio:
        ag2 = _agnes_video(prompt, duration)
        if ag2.get("ok"):
            return {"ok": True, "provider": "agnes-free", "model": KEYS.get("AGNES_VIDEO_MODEL", "agnes-video-2.5-flash"),
                    "prompt": prompt, "videos": ag2["urls"], "audio": False,
                    "note": "SOUND UNAVAILABLE right now: the audio model (Veo 3.1 via HiAPI) has no credits — "
                            "this Agnes free render is silent. Top up HiAPI for talking video."}
        if ag2.get("error") and ag2["error"] != "no key":
            failures.append("Agnes: " + str(ag2["error"])[:130])
    cv = _cvron_video(prompt)
    if cv.get("ok"):
        out = {"ok": True, "provider": "cvron-free (WAN-22)", "model": "wan22-img2video",
               "prompt": prompt, "videos": cv["urls"], "audio": False}
        notes = []
        if want_audio:
            notes.append("SOUND WAS REQUESTED but free engines render SILENT video — with HiAPI "
                         "credits OraCool auto-switches to Veo 3.1 which generates the clip WITH "
                         "voice and synchronized sound. Top up at hiapi.ai to enable talking video.")
        if failures:
            notes.append("Premium video providers were unavailable (" + "; ".join(failures) +
                         ") — this clip was animated by CVRON's free WAN-22 API. "
                         "Top up HiAPI for longer HD video.")
        if notes:
            out["note"] = " ".join(notes)
        return out
    return {"error": "Video engines are down right now (" + " | ".join(failures + [cv.get("error", "cvron failed")])
            + "). Please try again in a few minutes — the free Wan 2.2 and Agnes engines retry automatically."}

def gen_video(prompt, duration=None, want_audio=False):
    """Public video entry: the raw cascade, plus — when voice/sound was
    requested but only silent engines answered — a narrated voice track is
    generated (free neural TTS) and muxed into the clip."""
    r = _gen_video_raw(prompt, duration, want_audio)
    if r.get("ok") and want_audio and r.get("audio") in (False, "model did not request audio"):
        narr = _tts_narration("Here is your video: " + (prompt or "")[:240])
        if narr:
            vid0 = r["videos"][0]
            if vid0.startswith("http"):
                # remote clip — pull it onto this box first so it can be muxed
                ext = ".mp4"
                _m = re.search(r"\.(mp4|webm|mov)(?:\?|$)", vid0, re.I)
                if _m:
                    ext = "." + _m.group(1).lower()
                _lv = os.path.join(_gen_dir(), "vsrc-" + secrets.token_hex(6) + ext)
                if _download_media(vid0, _lv, timeout=300) or _download_media(vid0, _lv, timeout=300):
                    vid0 = "/generated/" + os.path.basename(_lv)
            muxed = _mux_video_audio(vid0, narr)
            if muxed:
                r["videos"] = [muxed]
                r["audio"] = "narrated voice track by OraCool (free TTS + Wan 2.2 picture)"
                r["note"] = ("This clip carries OraCool's narrated voice. For NATIVE synchronized sound "
                             "(real speech, ambient audio, effects) top up HiAPI — Veo 3.1 does that "
                             "automatically when it has credits.")
            else:
                r["videos"] = list(r["videos"]) + [narr]
                r["audio"] = False
                r["note"] = ("Your picture plus OraCool's narration (play both — audio track listed after "
                             "the video). For one-file native synchronized sound, top up HiAPI for Veo 3.1.")
        else:
            r["note"] = (("Voice was requested: the narration engine was unavailable this time, so the clip "
                          "is silent. Retry in a moment, or top up HiAPI for Veo 3.1's native synchronized "
                          "sound.") + ((" " + str(r.get("note") or "")) if r.get("note") else ""))
    return r


# ---------------------------------------------------------------- dark-web OSINT
# Clearnet investigation ONLY: LeakCheck breach/leak databases + the Ahmia
# Tor hidden-service index (directory + search, read-only). OraCool never
# connects to Tor and never opens a .onion site — it only lists what public
# intelligence engines have already indexed.

def _strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def osint_darkweb(target):
    """Dark-web investigation for one target: email/username/phone → breach and
    leak databases; any term → Ahmia .onion directory + search index."""
    target = (target or "").strip()
    if not target:
        return {"error": "Enter an email, username, phone number or search term."}
    out = {"target": target, "sources": []}
    low = target.lower()
    is_email = bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", target))
    is_phone = bool(re.match(r"^\+?\d{7,15}$", target))
    if is_email or is_phone or re.match(r"^[A-Za-z0-9._-]{2,64}$", target):
        lk = pro_leakcheck(target, "email" if is_email else ("phone" if is_phone else "username"))
        if not lk.get("error"):
            out["leak_databases"] = lk
            out["sources"].append("LeakCheck (breach & leak databases)")
    # 1) OnionLand hidden-service search (works from clearnet, no Tor needed)
    try:
        _, raw, _ = http_fetch("https://onionlandsearchengine.net/search?q=" + urllib.parse.quote(low),
                                timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        page = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        results = []
        for blk in re.findall(r'class="result-block"(.*?)(?=class="result-block"|class="pagination"|$)', page, re.S)[:12]:
            href = re.search(r'href="(/r\?s=[^"]+|https?://[a-z2-7]{10,62}\.onion[^"]*)"', blk)
            title = re.search(r'class="title"[^>]*>(.*?)</', blk, re.S)
            desc = re.search(r'class="desc"[^>]*>(.*?)</', blk, re.S)
            onm = re.search(r'([a-z2-7]{16,59}\.onion)', blk)
            if "ads/click" in (href.group(1) if href else ""):
                continue  # skip sponsored junk
            results.append({"title": _strip_tags(title.group(1)) if title else "",
                            "snippet": _strip_tags(desc.group(1))[:180] if desc else "",
                            "onion": onm.group(1) if onm else "",
                            "via": "OnionLand index"})
            if len(results) >= 8:
                break
        out["hidden_service_search"] = {"count": len(results), "results": results,
                                        "source": "OnionLand Search (Tor hidden-service index)"}
        if results:
            out["sources"].append("OnionLand hidden-service search")
    except Exception as e:
        out["hidden_service_search"] = {"error": str(e)[:120]}
    # 2) Ahmia (often blocks non-Tor clients — used opportunistically)
    try:
        _, raw, _ = http_fetch("https://ahmia.fi/search/?q=" + urllib.parse.quote(low),
                                timeout=25,
                                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) OraCool/3.0"})
        page = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        results = []
        for blk in re.findall(r'<li class="result"[^>]*>(.*?)</li>', page, re.S)[:8]:
            href = re.search(r'href="(https?://[^"]+)"', blk)
            title = re.search(r"<h4[^>]*>(.*?)</h4>", blk, re.S)
            desc = re.search(r"<p>(.*?)</p>", blk, re.S)
            results.append({"link": _strip_tags(href.group(1))[:200] if href else "",
                            "title": _strip_tags(title.group(1))[:120] if title else "",
                            "description": _strip_tags(desc.group(1))[:180] if desc else "",
                            "via": "Ahmia index"})
        out["ahmia_search"] = {"count": len(results), "results": results}
        if results:
            out["sources"].append("Ahmia hidden-service search")
    except Exception as e:
        out["ahmia_search"] = {"error": str(e)[:120]}
    # Direct-information summary the AI can quote verbatim
    hits = ((out.get("hidden_service_search") or {}).get("results") or []) + \
           ((out.get("ahmia_search") or {}).get("results") or [])
    lk = out.get("leak_databases") or {}
    try:
        leak_hits = len(lk.get("leaks") or lk.get("results") or lk.get("entries") or [])
    except Exception:
        leak_hits = 0
    out["quick_facts"] = {"hidden_services_found": len(hits),
                          "top_matches": [(h.get("title") or h.get("onion") or h.get("link") or "")[:70]
                                          for h in hits[:5]],
                          "breach_database_rows": leak_hits,
                          "leak_sources": out.get("sources", [])}
    # Media previews: never fetched from .onion hosts — only from the CLEARNET
    # mirror URLs the public indexes mention. Safe, http(s)-only.
    clearnet = []
    for h in hits:
        for m in re.findall(r"https?://(?:[a-z0-9-]+\.)+[a-z]{2,}[^\s\"'<>]*",
                            (h.get("snippet") or "") + " " + (h.get("description") or "")):
            if ".onion" not in m and m not in clearnet and "onionland" not in m and "ahmia" not in m:
                clearnet.append(m)
        if len(clearnet) >= 4:
            break
    gallery = []
    for cu in clearnet[:4]:
        try:
            _, raw, _ = http_fetch(cu, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            page = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            im = re.search(r'property="og:image"[^>]*content="([^"]+)"', page) or \
                 re.search(r'content="([^"]+)"[^>]*property="og:image"', page) or \
                 re.search(r'<img[^>]+src="(https?://[^"]+\.(?:png|jpe?g|webp|gif)[^"]*)"', page)
            vd = re.search(r'<(?:video|source)[^>]+src="(https?://[^"]+\.(?:mp4|webm)[^"]*)"', page)
            if im and im.group(1).startswith("http"):
                gallery.append({"image": im.group(1)[:300], "from": cu[:120]})
            if vd and vd.group(1).startswith("http"):
                gallery.append({"video": vd.group(1)[:300], "from": cu[:120]})
        except Exception:
            continue
    if gallery:
        out["media"] = gallery[:6]
    out["safety"] = ("Read-only dark-web OSINT for investigation. .onion links are listed for "
                     "reference only — OraCool never opens them. Many dark-web services host scams "
                     "or malware; never transact with anything found here. Media previews come from "
                     "public CLEARNET mirrors referenced in the index — never from .onion hosts.")
    return out


# ---------------------------------------------------------------- investigation platform
# Cases, evidence (SHA-256 + chain of custody), entity extraction, wallet
# tracing, watchlist monitoring and a global audit log. All OSINT runs
# server-side, so the investigator's own IP never touches the sources —
# that is the browser-honest version of "managed attribution".

_cases_lock = threading.RLock()


def _cases_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "cases.json")


def _ev_dir():
    d = os.path.join(DATA_DIR, "evidence")
    os.makedirs(d, exist_ok=True)
    return d


def _cases_load():
    try:
        with open(_cases_file()) as f:
            d = json.load(f)
    except Exception:
        d = {}
    if not d.get("cases") and not d.get("audit"):
        snap = supabase_cases_load()  # fresh deploy / disk reset → restore durable snapshot
        if snap:
            d = snap
            try:
                _materialize_contents(d)
                tmp = _cases_file() + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(d, f)
                os.replace(tmp, _cases_file())
            except Exception:
                pass
    d.setdefault("cases", {})
    d.setdefault("watch", [])
    d.setdefault("audit", [])
    return d


def _cases_save(d):
    global _cases_dirty
    with _cases_lock:
        tmp = _cases_file() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, _cases_file())
    if key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY"):
        _cases_dirty = True


# ---- durable mirror: Render's disk is ephemeral, so cases + custody + audit
# ---- are snapshotted (with bounded artifact contents) to Supabase every ~20s.
_cases_dirty = False
_cases_last_push = 0.0


def _cases_snapshot(d):
    """Deep-ish copy with artifact contents embedded (bounded) so evidence
    files themselves survive a redeploy on Render's ephemeral filesystem."""
    import copy as _copy
    snap = _copy.deepcopy(d)
    budget = 4_000_000
    for c in snap.get("cases", {}).values():
        for a in c.get("artifacts", []):
            p = os.path.join(_ev_dir(), a["id"] + ".txt")
            if budget <= 0:
                break
            try:
                with open(p, "rb") as f:
                    raw = f.read(120_000)
                a["_content"] = raw.decode("utf-8", "replace")
                a["_content_trunc"] = len(raw) >= 120_000
                budget -= len(raw)
            except Exception:
                pass
    if len(snap.get("audit", [])) > 400:
        snap["audit"] = snap["audit"][-400:]
    return snap


def _materialize_contents(d):
    for c in (d.get("cases") or {}).values():
        for a in c.get("artifacts", []):
            content = a.pop("_content", None)
            a.pop("_content_trunc", None)
            p = os.path.join(_ev_dir(), a["id"] + ".txt")
            if content is not None and not os.path.exists(p):
                try:
                    with open(p, "w") as f:
                        f.write(content)
                except Exception:
                    pass


def supabase_cases_load():
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return None
    try:
        _, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/case_store?k=eq.main&select=v",
                               headers={"apikey": svc, "Authorization": "Bearer " + svc}, timeout=15)
        rows = json.loads(raw)
        if rows:
            v = rows[0].get("v")
            if isinstance(v, str):
                v = json.loads(v)
            return v
    except Exception:
        return None
    return None


def supabase_cases_push(d):
    global _cases_last_push
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return False
    try:
        snap = _cases_snapshot(d)
        http_fetch(url.rstrip("/") + "/rest/v1/case_store", method="POST",
                   headers={"apikey": svc, "Authorization": "Bearer " + svc,
                            "Content-Type": "application/json",
                            "Prefer": "resolution=merge-duplicates"},
                   json_body={"k": "main", "v": snap, "updated_at": _now()}, timeout=25)
        _cases_last_push = time.time()
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ patch46: durable state on Render
# Render's filesystem is wiped on every deploy/restart. Everything OraCool remembers on disk — accounts, coins,
# vault, skills, trackers, alerts, subscribers, TOTP/passkeys, VAPID keys, build folders, published sites — is
# mirrored to the Supabase `case_store` kv table (k = "file:<name>" / "build:<slug>" / "site:<slug>") and
# restored before the server starts serving. Conversations and cases keep their existing cloud mirrors.
_PERSIST_SKIP = {"conversations.json", "cases.json", "platform.log"}
_PERSIST_DIRS = ("builds", "sites")
_PERSIST_TEXT_EXT = (".html", ".htm", ".css", ".js", ".mjs", ".json", ".md", ".txt", ".svg", ".xml", ".csv", ".webmanifest")
_PERSIST_MAX = 8 * 1024 * 1024
_PERSIST_SLUG_RX = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
_PERSIST = {"enabled": False, "last_sync": 0.0, "synced": {}, "errors": 0, "restored": 0, "pushed": 0, "note": ""}
_PERSIST_LOCK = threading.Lock()


def _persist_enabled():
    """On by default only on Render (RENDER=true), where the disk is ephemeral. A laptop/dev copy sharing the same
    Supabase project must NOT push its local test data over production state — set PERSIST_CLOUD=1 to force it on
    elsewhere, PERSIST_CLOUD=0 to force it off."""
    flag = str(KEYS.get("PERSIST_CLOUD", os.environ.get("PERSIST_CLOUD", ""))).strip().lower()
    have_keys = bool(key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY"))
    if flag in ("0", "false", "off", "no"):
        return False
    if flag in ("1", "true", "on", "yes"):
        return have_keys
    return have_keys and bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))


def supabase_kv_list(prefix):
    """All (k, v) rows whose key starts with `prefix` (PostgREST `like`, `*` wildcard)."""
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return []
    out = []
    try:
        q = urllib.parse.quote(prefix + "*", safe="")
        _, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/case_store?select=k,v&k=like." + q,
                               headers={"apikey": svc, "Authorization": "Bearer " + svc}, timeout=60)
        for r in json.loads(raw) or []:
            if not isinstance(r, dict) or not isinstance(r.get("k"), str):
                continue
            v = r.get("v")
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except Exception:
                    continue
            if isinstance(v, dict):
                out.append((r["k"], v))
    except Exception:
        pass
    return out


def _persist_files():
    """(name, path) for every state file worth keeping: data/*.json|pem|txt plus data/<dir>/*.json indexes."""
    out = []
    try:
        for n in sorted(os.listdir(DATA_DIR)):
            p = os.path.join(DATA_DIR, n)
            if os.path.isfile(p) and n not in _PERSIST_SKIP and not n.endswith(".tmp") and n.endswith((".json", ".pem", ".txt")):
                out.append((n, p))
        for dn in _PERSIST_DIRS:
            base = os.path.join(DATA_DIR, dn)
            if os.path.isdir(base):
                for n in sorted(os.listdir(base)):
                    p = os.path.join(base, n)
                    if os.path.isfile(p) and n.endswith(".json") and not n.endswith(".tmp"):
                        out.append((dn + "/" + n, p))
    except Exception:
        pass
    return out


def _persist_pack_file(p):
    st = os.stat(p)
    if st.st_size > _PERSIST_MAX:
        return None
    with open(p, "rb") as f:
        raw = f.read()
    try:
        txt = raw.decode("utf-8")
        if p.endswith(".json"):
            json.loads(txt)  # never mirror a half-written file
        return {"text": txt, "mtime": st.st_mtime, "size": st.st_size}
    except Exception:
        if p.endswith(".json"):
            return None
        return {"b64": base64.b64encode(raw).decode("ascii"), "mtime": st.st_mtime, "size": st.st_size}


def _persist_dir_mtime(d):
    newest = 0.0
    for root, _dirs, names in os.walk(d):
        for n in names:
            if n.lower().endswith(_PERSIST_TEXT_EXT):
                try:
                    newest = max(newest, os.stat(os.path.join(root, n)).st_mtime)
                except OSError:
                    pass
    return newest


def _persist_pack_dir(d):
    files, newest, total = {}, 0.0, 0
    for root, _dirs, names in os.walk(d):
        for n in sorted(names):
            if not n.lower().endswith(_PERSIST_TEXT_EXT):
                continue
            p = os.path.join(root, n)
            try:
                st = os.stat(p)
                if st.st_size > 2 * 1024 * 1024:
                    continue
                with open(p, encoding="utf-8") as f:
                    files[os.path.relpath(p, d).replace(os.sep, "/")] = f.read()
                newest = max(newest, st.st_mtime); total += st.st_size
            except Exception:
                continue
            if total > _PERSIST_MAX:
                break
    return {"files": files, "mtime": newest, "n": len(files)} if files else None


def _persist_dirs():
    """(key, path) for every build/site folder."""
    out = []
    for dn in _PERSIST_DIRS:
        base = os.path.join(DATA_DIR, dn)
        if not os.path.isdir(base):
            continue
        for slug in sorted(os.listdir(base)):
            d = os.path.join(base, slug)
            if os.path.isdir(d) and _PERSIST_SLUG_RX.match(slug):
                out.append((dn[:-1] + ":" + slug, d))
    return out


def _persist_scan(force=False):
    """Push every changed state file / folder to Supabase. Returns how many records were written."""
    if not _persist_enabled():
        return 0
    pushed = 0
    with _PERSIST_LOCK:
        for name, p in _persist_files():
            k = "file:" + name
            try:
                m = os.stat(p).st_mtime
                if not force and _PERSIST["synced"].get(k) == m:
                    continue
                pack = _persist_pack_file(p)
                if pack is None:
                    continue
                if supabase_kv_put(k, pack):
                    _PERSIST["synced"][k] = m; pushed += 1
                else:
                    _PERSIST["errors"] += 1
            except Exception:
                _PERSIST["errors"] += 1
        for k, d in _persist_dirs():
            try:
                newest = _persist_dir_mtime(d)
                if not newest or (not force and _PERSIST["synced"].get(k) == newest):
                    continue
                pack = _persist_pack_dir(d)
                if pack is None:
                    _PERSIST["synced"][k] = newest
                    continue
                if supabase_kv_put(k, pack):
                    _PERSIST["synced"][k] = newest; pushed += 1
                else:
                    _PERSIST["errors"] += 1
            except Exception:
                _PERSIST["errors"] += 1
    if pushed:
        _PERSIST["last_sync"] = time.time(); _PERSIST["pushed"] += pushed
    return pushed


def _persist_write(path, raw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, path)


def _persist_restore():
    """Boot: bring back every state file and build/site folder this (fresh) instance lacks. Local files that are
    newer than the cloud copy are kept. Returns how many records were restored."""
    _PERSIST["enabled"] = _persist_enabled()
    if not _PERSIST["enabled"]:
        _PERSIST["note"] = "cloud persistence off — no Supabase service key (state lives on this disk only)"
        return 0
    n = 0
    for k, v in supabase_kv_list("file:"):
        name = k[5:]
        if not name or ".." in name or name.startswith(("/", ".")) or name.count("/") > 1 or os.path.basename(name) in _PERSIST_SKIP:
            continue
        p = os.path.join(DATA_DIR, name)
        try:
            rm = float(v.get("mtime") or 0)
            if os.path.exists(p):
                lm = os.stat(p).st_mtime
                if lm >= rm - 1:
                    _PERSIST["synced"][k] = lm
                    continue
            raw = v["text"].encode("utf-8") if isinstance(v.get("text"), str) else base64.b64decode(v.get("b64") or "")
            if not raw:
                continue
            _persist_write(p, raw)
            if rm:
                os.utime(p, (rm, rm))
            _PERSIST["synced"][k] = os.stat(p).st_mtime; n += 1
        except Exception:
            _PERSIST["errors"] += 1
    for dn in _PERSIST_DIRS:
        for k, v in supabase_kv_list(dn[:-1] + ":"):
            slug = k.split(":", 1)[1]
            if not _PERSIST_SLUG_RX.match(slug):
                continue
            d = os.path.join(DATA_DIR, dn, slug)
            try:
                if os.path.isdir(d) and _persist_dir_mtime(d) >= float(v.get("mtime") or 0) - 1:
                    _PERSIST["synced"][k] = _persist_dir_mtime(d)
                    continue
                files = v.get("files") or {}
                if not isinstance(files, dict) or not files:
                    continue
                rm = float(v.get("mtime") or time.time())
                for rel, txt in files.items():
                    if not isinstance(rel, str) or not isinstance(txt, str) or ".." in rel or rel.startswith("/"):
                        continue
                    fp = os.path.join(d, rel)
                    _persist_write(fp, txt.encode("utf-8"))
                    os.utime(fp, (rm, rm))
                _PERSIST["synced"][k] = _persist_dir_mtime(d); n += 1
            except Exception:
                _PERSIST["errors"] += 1
    _PERSIST["restored"] = n
    _PERSIST["note"] = f"restored {n} record(s) from Supabase at boot"
    return n


def _persist_loop():
    while True:
        time.sleep(20)
        try:
            _persist_scan()
        except Exception:
            pass


def _persist_flush_and_exit(signum, frame):
    """Render sends SIGTERM before a deploy/restart: push whatever changed in the last seconds, then leave."""
    try:
        _log_line("persist", "SIGTERM — flushing state to Supabase")
        _persist_scan()
        try:
            snapshot = _conv_all()
            supabase_kv_put("conversations", snapshot)
        except Exception:
            pass
    except Exception:
        pass
    os._exit(0)


def persist_status():
    return {"enabled": _PERSIST["enabled"], "restored": _PERSIST["restored"], "pushed": _PERSIST["pushed"],
            "errors": _PERSIST["errors"], "tracked": len(_PERSIST["synced"]),
            "last_sync": (_now_from(_PERSIST["last_sync"]) if _PERSIST["last_sync"] else ""), "note": _PERSIST["note"]}


def _now_from(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))


def _cases_flush_loop():
    global _cases_dirty
    while True:
        time.sleep(10)
        try:
            if _cases_dirty and (time.time() - _cases_last_push) > 18:
                with _cases_lock:
                    d = _cases_load()
                    _cases_dirty = False
                supabase_cases_push(d)
        except Exception:
            pass


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def audit_log(who, action, detail=""):
    try:
        d = _cases_load()
        a = d.setdefault("audit", [])
        a.append({"t": _now(), "who": (who or "?").lower(), "action": action, "detail": (detail or "")[:200]})
        if len(a) > 1000:
            del a[:-1000]
        _cases_save(d)
    except Exception:
        pass


def _custody(c, who, action, ref=""):
    c.setdefault("custody", []).append({"t": _now(), "by": (who or "?").lower(), "action": action, "ref": ref})
    if len(c["custody"]) > 500:
        del c["custody"][:-500]


def _owner_case(d, owner, cid):
    c = d["cases"].get(cid)
    if not c:
        return None, {"error": "Case not found."}
    if c.get("owner") != (owner or "").lower() and not is_admin(owner):
        return None, {"error": "This case belongs to another investigator."}
    return c, None


def case_create(owner, name, notes=""):
    d = _cases_load()
    mine = [c for c in d["cases"].values() if c.get("owner") == (owner or "").lower()]
    if len(mine) >= 25:
        return {"error": "Case limit (25) reached — close or delete an old case first."}
    cid = "case-" + os.urandom(4).hex()
    d["cases"][cid] = {"id": cid, "name": (name or "Untitled case").strip()[:80] or "Untitled case",
                       "notes": (notes or "")[:2000], "tags": [],
                       "owner": (owner or "").lower(), "created": _now(), "status": "open",
                       "artifacts": []}
    _custody(d["cases"][cid], owner, "case.open", cid)
    _cases_save(d)
    audit_log(owner, "case.open", d["cases"][cid]["name"])
    return {"ok": True, "case": cid, "name": d["cases"][cid]["name"]}


def case_list(owner):
    d = _cases_load()
    out = []
    for c in d["cases"].values():
        if c.get("owner") != (owner or "").lower() and not is_admin(owner):
            continue
        out.append({"id": c["id"], "name": c["name"], "status": c.get("status", "open"),
                    "artifacts": len(c.get("artifacts", [])), "created": c.get("created"),
                    "tags": c.get("tags", []),
                    "updated": (c["custody"][-1]["t"] if c.get("custody") else c.get("created"))})
    out.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return {"cases": out}


def case_note(owner, cid, notes):
    d = _cases_load()
    c, err = _owner_case(d, owner, cid)
    if err:
        return err
    c["notes"] = (notes or "")[:4000]
    _custody(c, owner, "case.note", cid)
    _cases_save(d)
    return {"ok": True}


def case_close(owner, cid):
    d = _cases_load()
    c, err = _owner_case(d, owner, cid)
    if err:
        return err
    c["status"] = "closed"
    _custody(c, owner, "case.close", cid)
    _cases_save(d)
    return {"ok": True}


def evidence_add(owner, cid, title, content, url="", meta=None, kind="intel"):
    content = str(content or "")
    if not content.strip():
        return {"error": "Nothing to preserve — the content is empty."}
    content = content[:300000]
    raw = content.encode("utf-8", "replace")
    sha = hashlib.sha256(raw).hexdigest()
    d = _cases_load()
    c, err = _owner_case(d, owner, cid)
    if err:
        return err
    aid = "ev-" + os.urandom(4).hex()
    try:
        with open(os.path.join(_ev_dir(), aid + ".txt"), "wb") as f:
            f.write(raw)
    except Exception as e:
        return {"error": f"Storage failed: {e}"}
    art = {"id": aid, "title": (title or "Evidence").strip()[:120] or "Evidence",
           "kind": kind, "sha256": sha, "size_bytes": len(raw),
           "source_url": (url or "")[:500], "collected_at": _now(), "collected_by": (owner or "").lower(),
           "meta": meta or {}}
    c.setdefault("artifacts", []).append(art)
    _custody(c, owner, "evidence.collect", aid + " sha256:" + sha[:16] + "…")
    _cases_save(d)
    audit_log(owner, "evidence.collect", f"{aid} sha256:{sha[:16]}")
    return {"ok": True, "artifact": art,
            "note": "SHA-256 fingerprint computed at collection time; custody chain updated. "
                    "Store the ORIGINAL artifacts untouched — OraCool preserves working copies."}


def evidence_list(owner, cid):
    d = _cases_load()
    c, err = _owner_case(d, owner, cid)
    if err:
        return err
    return {"case": c["name"], "artifacts": c.get("artifacts", []), "custody": c.get("custody", [])}


def evidence_view(owner, cid, aid):
    d = _cases_load()
    c, err = _owner_case(d, owner, cid)
    if err:
        return err
    art = next((a for a in c.get("artifacts", []) if a["id"] == aid), None)
    if not art:
        return {"error": "Artifact not found."}
    try:
        with open(os.path.join(_ev_dir(), aid + ".txt"), "r") as f:
            content = f.read()
    except Exception:
        content = ""
    _custody(c, owner, "evidence.view", aid)
    _cases_save(d)
    return {"ok": True, "artifact": art, "content": content[:120000]}


def evidence_verify(owner, cid, aid):
    d = _cases_load()
    c, err = _owner_case(d, owner, cid)
    if err:
        return err
    art = next((a for a in c.get("artifacts", []) if a["id"] == aid), None)
    if not art:
        return {"error": "Artifact not found."}
    try:
        with open(os.path.join(_ev_dir(), aid + ".txt"), "rb") as f:
            now_sha = hashlib.sha256(f.read()).hexdigest()
    except Exception:
        now_sha = ""
    match = bool(now_sha) and now_sha == art["sha256"]
    _custody(c, owner, "evidence.verify", aid + " → " + ("INTEGRITY OK" if match else "MISMATCH/MISSING"))
    _cases_save(d)
    return {"ok": True, "match": match, "recorded_sha256": art["sha256"], "current_sha256": now_sha,
            "collected_at": art["collected_at"], "verified_at": _now(),
            "note": "Recomputed from the stored copy. A match proves the preserved copy is unaltered since collection."}


def evidence_export(owner, cid):
    d = _cases_load()
    c, err = _owner_case(d, owner, cid)
    if err:
        return err
    arts = c.get("artifacts", [])
    chain = "\n".join(a["sha256"] for a in arts)
    case_hash = hashlib.sha256(chain.encode()).hexdigest() if arts else ""
    doc = {"document": "ORACOOL INVESTIGATION — EVIDENCE & CUSTODY EXPORT",
           "standard": "Aligned with the Berkeley Protocol on Digital Open Source Investigations "
                       "(collection integrity, chain of custody, source preservation). "
                       "OraCool is an investigative aid, not a notarization service.",
           "case": c["name"], "case_id": cid, "owner": c.get("owner"), "status": c.get("status"),
           "exported_at": _now(), "case_fingerprint_sha256": case_hash,
           "artifacts": [{k: a.get(k) for k in ("id", "title", "kind", "sha256", "size_bytes",
                                                 "source_url", "collected_at", "collected_by")} for a in arts],
           "custody_log": c.get("custody", [])}
    _custody(c, owner, "evidence.export", f"{len(arts)} artifact(s)")
    _cases_save(d)
    audit_log(owner, "evidence.export", cid)
    return {"ok": True, "filename": "oracool-" + cid + "-" + time.strftime("%Y%m%d") + ".json",
            "json": json.dumps(doc, indent=2)}


ENTITY_PATTERNS = [
    ("email",   r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("onion",   r"\b[a-z2-7]{16,59}\.onion\b"),
    ("ipv4",    r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ("url",     r"https?://[^\s\"'<>)\]]+"),
    ("btc",     r"\b(?:bc1[a-z0-9]{25,58}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b"),
    ("eth",     r"\b0x[0-9a-fA-F]{40}\b"),
    ("handle",  r"(?<![\w/])@[A-Za-z0-9_]{3,20}"),
    ("domain",  r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|ng|ai|co|xyz|info|biz|dev|link|site|online|shop|store)\b"),
    ("phone",   r"\+?\b\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{4}\b"),
    ("pgp",     r"-----BEGIN PGP PUBLIC KEY BLOCK-----[\s\S]{0,4000}?-----END PGP PUBLIC KEY BLOCK-----"),
    ("imei",    r"\b\d{15}\b"),
    ("iso_date", r"\b\d{4}-\d{2}-\d{2}\b"),
]


def extract_entities(text):
    t = (text or "")[:200000]
    if not t.strip():
        return {"error": "No text to analyze."}
    out = {}
    for name, pat in ENTITY_PATTERNS:
        seen = []
        for m in re.findall(pat, t):
            v = m.strip(".,;:)]") if not isinstance(m, tuple) else m[0]
            if v and v not in seen:
                seen.append(v)
            if len(seen) >= 25:
                break
        if seen:
            out[name] = seen
    total = sum(len(v) for v in out.values())
    return {"ok": True, "counts": {k: len(v) for k, v in out.items()}, "entities": out,
            "total": total,
            "note": "Structured extraction over the supplied text only — no external lookups were made. "
                    "Verify every entity before acting on it."}


def trace_wallet(addr):
    a = (addr or "").strip()
    is_btc = bool(re.match(r"^(?:bc1[a-z0-9]{25,58}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$", a))
    is_eth = bool(re.match(r"^0x[0-9a-fA-F]{40}$", a))
    if not (is_btc or is_eth):
        return {"error": "That doesn't look like a BTC or ETH address."}
    out = {"ok": True, "chain": "bitcoin" if is_btc else "ethereum", "address": a}
    tried = []
    if is_btc:
        for name, url in (("blockstream", f"https://blockstream.info/api/address/{a}"),
                          ("blockchair", f"https://api.blockchair.com/bitcoin/dashboards/address/{a}")):
            try:
                _, raw, _ = http_fetch(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
                d = json.loads(raw)
                if name == "blockstream":
                    cs = d.get("chain_stats") or {}
                    out.update({"balance_sats": cs.get("funded_txo_sum", 0) - cs.get("spent_txo_sum", 0),
                                "balance_btc": (cs.get("funded_txo_sum", 0) - cs.get("spent_txo_sum", 0)) / 1e8,
                                "tx_count": cs.get("tx_count"), "source": "blockstream.info public node data"})
                else:
                    st = ((d.get("data") or {}).get(a) or {}).get("stats") or {}
                    if st:
                        out.update({"received_sats": st.get("received"), "first_seen": st.get("time_first_seen"),
                                    "last_seen": st.get("time_last_seen")})
                        out["source"] = "blockstream + blockchair"
                tried.append(name)
            except Exception as e:
                tried.append(name + ": " + str(e)[:60])
            if "balance_sats" in out:
                break
    else:
        for name, url in (("blockscout", f"https://eth.blockscout.com/api/v1/addresses/{a}"),
                          ("blockchair", f"https://api.blockchair.com/ethereum/dashboards/address/{a}")):
            try:
                _, raw, _ = http_fetch(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
                d = json.loads(raw)
                if name == "blockscout":
                    bal = int(d.get("coin_balance") or 0)
                    out.update({"balance_wei": bal, "balance_eth": bal / 1e18,
                                "tx_count": d.get("transactions_count"),
                                "is_contract": bool(d.get("is_contract")),
                                "source": "eth.blockscout.com public explorer data"})
                else:
                    st = ((d.get("data") or {}).get(a) or {}).get("stats") or {}
                    if st:
                        out["first_seen"] = st.get("time_first_seen")
            except Exception as e:
                tried.append(name + ": " + str(e)[:60])
            if "balance_wei" in out or "balance_eth" in out:
                break
    if not ({"balance_sats", "balance_wei", "balance_eth"} & set(out.keys())):
        return {"error": "Explorer rate-limited right now (" + "; ".join(tried)[:200] + ") — try again in a minute."}
    out["note"] = ("Public-chain facts only (address state, no attribution). Label/attribution claims need "
                   "off-chain intel — cross-check against case artifacts; cluster cautiously. Source: "
                   + str(out.get("source", "public explorer")))
    return out


def watch_add(owner, term, case_id=""):
    term = (term or "").strip()[:120]
    if not term:
        return {"error": "Enter an email, username, domain or keyword to monitor."}
    d = _cases_load()
    if len([w for w in d["watch"] if w.get("owner") == (owner or "").lower()]) >= 40:
        return {"error": "Watchlist limit (40 terms) reached."}
    wid = "w-" + os.urandom(3).hex()
    d["watch"].append({"id": wid, "owner": (owner or "").lower(), "term": term,
                       "case": case_id, "created": _now(), "last_check": "", "last_hits": 0,
                       "alerts": []})
    _cases_save(d)
    return {"ok": True, "id": wid}


def watch_list(owner):
    d = _cases_load()
    return {"watch": [w for w in d["watch"] if w.get("owner") == (owner or "").lower()]}


def watch_remove(owner, wid):
    d = _cases_load()
    d["watch"] = [w for w in d["watch"] if not (w.get("id") == wid and w.get("owner") == (owner or "").lower())]
    _cases_save(d)
    return {"ok": True}


def watch_check_one(w):
    term = w["term"]
    try:
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", term):
            res = pro_leakcheck(term, "email")
            hits = int(res.get("found") or 0) if isinstance(res, dict) else 0
        elif re.match(r"^[A-Za-z0-9._-]{2,64}$", term) and not term.count(".") > 1:
            res = pro_leakcheck(term, "username")
            hits = int(res.get("found") or 0) if isinstance(res, dict) else 0
        else:
            res = osint_darkweb(term)
            hits = ((res.get("hidden_service_search") or {}).get("count") or 0) if isinstance(res, dict) else 0
    except Exception:
        return
    prev = int(w.get("last_hits") or 0)
    w["last_check"] = _now()
    if hits and hits != prev:
        w.setdefault("alerts", []).append({"t": _now(), "hits": hits, "was": prev})
        w["alerts"] = w["alerts"][-10:]
        try:
            emit_event(w.get("owner") or "", "watch", "⚠ Watchlist hit: " + str(term)[:80],
                       str(hits) + " finding(s) — was " + str(prev) + ". Open Cases to review.")
        except Exception:
            pass
    w["last_hits"] = hits


def watch_run_all():
    d = _cases_load()
    for w in d["watch"]:
        watch_check_one(w)
    _cases_save(d)
    return {"checked": len(d["watch"])}


def _watch_loop():
    while True:
        try:
            watch_run_all()
        except Exception:
            pass
        time.sleep(6 * 3600)


DOC_PROVIDERS = {"seon": ("SEON_API_KEY",), "kinegram": ("KINEGRAM_KEY", "KINEGRAM_API_KEY"),
                 "kairos": ("KAIROS_KEY", "KAIROS_API_KEY")}


def _kairos_call(epath, body):
    """Try Kairos server REST with JSON-body auth (their classic API shape)."""
    app_id = key("KAIROS_APP_ID")
    app_key = key("KAIROS_API_KEY") or key("KAIROS_KEY")
    if not app_key:
        return {"error": "no key"}
    payload = dict(body)
    if app_id:
        payload["app_id"] = app_id
    payload["app_key"] = app_key
    try:
        _, raw, _ = http_fetch("https://api.kairos.com/" + epath, method="POST", timeout=30,
                               headers={"Content-Type": "application/json"}, json_body=payload)
        d = json.loads(raw)
        if str(d.get("codes") or d.get("message") or "").find("successful") >= 0 or d.get("faces"):
            return {"ok": True, "data": d}
        return {"error": str(d.get("message") or d.get("codes") or d)[:160]}
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8", "replace"))
            return {"error": str(d.get("message") or d)[:160]}
        except Exception:
            return {"error": "HTTP " + str(e.code)}
    except Exception as e:
        return {"error": str(e)[:160]}


def doc_verification_state():
    configured = [n for n, ks in DOC_PROVIDERS.items() if any(key(k) for k in ks)]
    return {"configured": bool(configured), "providers": configured}


def verify_document(dtype, data):
    """Document-verification FRAMEWORK — OraCool is the intelligence hub, never the
    forensics engine (a 200-country/MRZ reference database is a multi-year, multi-
    million-dollar build; integrate SEON / Kinegram / Kairos instead). Real checks
    run through a contracted provider's API; results are ALWAYS framed as
    'indicators requiring further review', never as verdicts."""
    dtype = (dtype or "id").strip().lower()[:40]
    data = (data or "").strip()[:200]
    out = {"document": dtype,
           "lawful_use": "Passive checks against registries/APIs you are licensed to use only; "
                         "never use stolen credentials or unofficial databases.",
           "disclaimer": "RESULTS ARE INDICATORS THAT REQUIRE FURTHER REVIEW — never a definitive "
                         "verdict on a person or document. A registry record (FRSC/NIN) proves a "
                         "RECORD EXISTS, not that the physical card is genuine: a real record can "
                         "sit behind a counterfeit card. False positives carry legal consequences — "
                         "escalate through official channels (NERECON for NIN, FRSC for driving "
                         "licences, NIS for passports).",
           "provider": None, "result": "not_configured"}
    if data:
        out["subject_ref"] = data
    cfg = doc_verification_state()
    if cfg["configured"]:
        out["provider"] = cfg["providers"][0]
        out["result"] = "provider_key_present"
        # If Kairos is the configured provider, ACTUALLY call their API with the
        # app credentials — never pretend. Whatever Kairos answers is passed through.
        if out["provider"] == "kairos" and data and re.match(r"https?://\S+", data):
            kr = _kairos_call("face/detect", {"url": data.strip()})
            if kr.get("ok"):
                out["result"] = "kairos_face_detected"
                out["kairos"] = kr.get("data")
                out["note"] = ("Kairos detected a face in the supplied image (technical signal only — "
                               "NOT an authenticity verdict). Preserve this check into a case for custody.")
            else:
                out["result"] = "kairos_attempt_failed"
                out["kairos_error"] = kr.get("error")
                out["note"] = ("Kairos key is configured but their endpoint refused the call: "
                               + str(kr.get("error"))[:160] + " — the dashboard credential set (App ID + "
                               "API Key) may belong to the QR/IDV pairing flow rather than server REST. "
                               "OraCool reports this honestly and never fabricates a verification result.")
        else:
            out["note"] = ("Provider key configured (" + out["provider"] + "). Finish the provider's "
                           "documented endpoint mapping for your contract, and results flow through "
                           "this response shape — each check can then be preserved into a Case with a "
                           "SHA-256 fingerprint automatically.")
    else:
        out["note"] = ("No verification provider configured. To activate real checks, contract with "
                       "SEON, Kinegram or Kairos and set its key (SEON_API_KEY / KINEGRAM_KEY / "
                       "KAIROS_KEY or KAIROS_API_KEY) in keys.json or the Render environment. Until then OraCool "
                       "reports framework status only and never fabricates an authenticity verdict — "
                       "that is the legally responsible behaviour.")
    return out


# ---------------------------------------------------------------- app launcher
# A browser cannot install software, but on Android it can fire an intent:// that
# opens the INSTALLED app (or the Play Store if it isn't), on iOS a URL scheme,
# and everywhere else the official site. This is exactly what "open X" does.

APP_LINKS = {
    "whatsapp":      {"name": "WhatsApp",      "url": "https://wa.me/",              "android": "intent://#Intent;package=com.whatsapp;scheme=whatsapp;end", "ios": "whatsapp://", "play": "com.whatsapp"},
    "instagram":     {"name": "Instagram",     "url": "https://instagram.com",       "android": "intent://#Intent;package=com.instagram.android;scheme=instagram;end", "ios": "instagram://", "play": "com.instagram.android"},
    "youtube":       {"name": "YouTube",       "url": "https://youtube.com",         "android": "intent://#Intent;package=com.google.android.youtube;scheme=https;end", "ios": "vnd.youtube:", "play": "com.google.android.youtube"},
    "yt":            {"name": "YouTube",       "url": "https://youtube.com",         "android": "intent://#Intent;package=com.google.android.youtube;scheme=https;end", "ios": "vnd.youtube:", "play": "com.google.android.youtube"},
    "tiktok":        {"name": "TikTok",        "url": "https://tiktok.com",          "android": "intent://#Intent;package=com.zhiliaoapp.musically;scheme=tiktok;end", "ios": "tiktok://", "play": "com.zhiliaoapp.musically"},
    "facebook":      {"name": "Facebook",      "url": "https://facebook.com",        "android": "intent://#Intent;package=com.facebook.katana;scheme=fbapi;end", "ios": "fb://", "play": "com.facebook.katana"},
    "x":             {"name": "X",             "url": "https://x.com",               "android": "intent://#Intent;package=com.twitter.android;scheme=twitter;end", "ios": "twitter://", "play": "com.twitter.android"},
    "twitter":       {"name": "X",             "url": "https://x.com",               "android": "intent://#Intent;package=com.twitter.android;scheme=twitter;end", "ios": "twitter://", "play": "com.twitter.android"},
    "telegram":      {"name": "Telegram",      "url": "https://t.me",                "android": "intent://#Intent;package=org.telegram.messenger;scheme=tg;end", "ios": "tg://", "play": "org.telegram.messenger"},
    "snapchat":      {"name": "Snapchat",      "url": "https://snapchat.com",        "android": "intent://#Intent;package=com.snapchat.android;scheme=snapchat;end", "ios": "snapchat://", "play": "com.snapchat.android"},
    "spotify":       {"name": "Spotify",       "url": "https://open.spotify.com",    "android": "intent://#Intent;package=com.spotify.music;scheme=spotify;end", "ios": "spotify://", "play": "com.spotify.music"},
    "netflix":       {"name": "Netflix",       "url": "https://netflix.com",         "android": "intent://#Intent;package=com.netflix.mediaclient;end", "ios": "nflx://", "play": "com.netflix.mediaclient"},
    "gmail":         {"name": "Gmail",         "url": "https://mail.google.com",     "android": "intent://#Intent;package=com.google.android.gm;action=android.intent.action.SENDTO;scheme=mailto;end", "ios": "googlimap://", "play": "com.google.android.gm"},
    "maps":          {"name": "Google Maps",   "url": "https://maps.google.com",     "android": "geo:0,0?q=", "ios": "comgooglemaps://", "play": "com.google.android.apps.maps"},
    "chrome":        {"name": "Chrome",        "url": "https://google.com",          "android": "intent://#Intent;package=com.android.chrome;end", "ios": "googlechrome://", "play": "com.android.chrome"},
    "camera":        {"name": "Camera",        "url": "",                            "android": "intent:#Intent;action=android.media.action.STILL_IMAGE_CAMERA;end", "ios": "", "play": ""},
    "settings":      {"name": "Settings",      "url": "",                            "android": "intent:#Intent;action=android.settings.SETTINGS;end", "ios": "app-settings:", "play": ""},
    "play store":    {"name": "Play Store",    "url": "https://play.google.com",     "android": "market://", "ios": "", "play": ""},
    "binance":       {"name": "Binance",       "url": "https://binance.com",         "android": "intent://#Intent;package=com.binance.dev;scheme=binance;end", "ios": "binanceus://", "play": "com.binance.dev"},
    "discord":       {"name": "Discord",       "url": "https://discord.com",         "android": "intent://#Intent;package=com.discord;scheme=discord;end", "ios": "discord://", "play": "com.discord"},
    "cash app":      {"name": "Cash App",      "url": "https://cash.app",            "android": "intent://#Intent;package=com.squareup.cash;scheme=cashme;end", "ios": "cashme://", "play": "com.squareup.cash"},
    "playstation":   {"name": "PlayStation",   "url": "https://playstation.com",     "android": "intent://#Intent;package=com.sony.sessionsoftware;end", "ios": "", "play": "com.sony.sessionsoftware"},
    "chrome remote": {"name": "Remote Desktop","url": "https://chrome.google.com/remotedesktop", "android": "", "ios": "", "play": "com.google.chromeremotedesktop"},
    "files":         {"name": "Files",         "url": "",                            "android": "intent://#Intent;action=android.intent.action.VIEW;type=resource/*;end", "ios": "shareddocs://", "play": ""},
    "calculator":    {"name": "Calculator",    "url": "",                            "android": "intent:#Intent;action=android.intent.action.SHOW_CALCULATOR;end", "ios": "", "play": ""},
}


def open_app(target):
    t = (target or "").strip().lower().rstrip(".!?")
    if not t:
        return {"error": "Which app should I open?"}
    hit = APP_LINKS.get(t)
    if not hit:
        for k, v in APP_LINKS.items():
            if k in t or t in v["name"].lower():
                hit = v
                break
    if hit:
        return {"ok": True, "app": hit["name"], "url": hit.get("url") or "",
                "android_intent": hit.get("android") or "", "ios_scheme": hit.get("ios") or "",
                "play_package": hit.get("play") or "",
                "note": "The client renders a tappable 'Open <app>' launch button in the chat (browsers only allow app launches from a real user tap — never claim the app is already open). Android: intent opens the installed app else the website; iOS: scheme, else the website. Falls back to Play Store install when known."}
    slug = re.sub(r"[^a-z0-9]", "", t.split()[0]) if re.match(r"^[a-z0-9 .+-]{2,30}$", t) else ""
    if slug:
        return {"ok": True, "app": t.title(), "url": "https://" + slug + ".com",
                "android_intent": "", "ios_scheme": "", "play_package": "",
                "note": "Not in the launch map — opening the official website link instead."}
    return {"error": "I can't parse that app name."}


def shorten_url(u):
    """Best-effort clean short link via CVRON's free shortener (os8.me)."""
    try:
        _, raw, _ = http_fetch(CVRON + "/cvronapi/url-shortener.php?url=" + urllib.parse.quote(u, safe=""),
                               timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(raw)
        if d.get("success") and d.get("short_url"):
            return {"ok": True, "short_url": d["short_url"]}
    except Exception:
        pass
    return {"ok": False}


def media_inspect(url="", data_b64=""):
    """Basic forensic triage for images — stdlib-only metadata analysis:
    SHA-256 fingerprint, format, dimensions, EXIF/XMP/GPS/C2PA presence,
    camera-software hints. INDICATORS ONLY, never a tampering verdict."""
    raw = b""
    src = ""
    if (data_b64 or "").strip():
        try:
            raw = base64.b64decode(re.sub(r"^data:[^,]+,", "", data_b64.strip(), count=1))[:8_000_000]
            src = "upload"
        except Exception:
            return {"error": "Could not decode the supplied image data."}
    elif (url or "").strip().lower().startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(url.strip(), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as f:
                raw = f.read(8_000_000)
            src = url.strip()
        except Exception as e:
            return {"error": f"Fetch failed: {str(e)[:140]}"}
    else:
        return {"error": "Provide an image URL or the image data to inspect."}
    if not raw:
        return {"error": "Empty response — nothing to analyse."}
    out = {"source": src, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    fmt, w, h = "unknown", None, None
    exif = xmp = gps = c2pa = False
    software = camera = ""
    if raw[:3] == b"\xff\xd8\xff":
        fmt = "jpeg"
        exif = b"Exif\x00\x00" in raw[:200_000]
        xmp = b"http://ns.adobe.com/xap" in raw[:400_000]
        i = 2
        while i < len(raw) - 9 and i < 4_000_000:
            if raw[i] != 0xFF:
                break
            m = raw[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = int.from_bytes(raw[i + 5:i + 7], "big"), int.from_bytes(raw[i + 7:i + 9], "big")
                break
            if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            seglen = int.from_bytes(raw[i + 2:i + 4], "big")
            if m == 0xE1:
                seg = raw[i + 4:i + 4 + seglen]
                if b"GPS" in seg or _tif_has_gps(seg):
                    gps = True
                sw = re.search(rb"(Adobe Photoshop|GIMP|Pixelmator|Snapseed|Canva|Photoshop)[^\x00]{0,24}", seg)
                mk = re.search(rb"(NIKON|Canon|FUJIFILM|SONY|Panasonic|OLYMPUS|iPhone|SM-|Redmi|realme|samsung|HUAWEI|Infinix|TECNO)[A-Za-z0-9 \-]{0,20}", seg)
                if sw:
                    software = sw.group(1).decode(errors="replace")
                if mk:
                    camera = mk.group(0).decode(errors="replace").strip()
            i += 2 + seglen
    elif raw[:8] == b"\x89PNG\r\n\x1a\n":
        fmt = "png"
        if len(raw) > 33:
            w = int.from_bytes(raw[16:20], "big")
            h = int.from_bytes(raw[20:24], "big")
        head = raw[:600_000]
        xmp = b"xmp" in head
        gps = b"GPS" in head
    elif raw[:6] in (b"GIF87a", b"GIF89a"):
        fmt = "gif"
        w, h = int.from_bytes(raw[6:8], "little"), int.from_bytes(raw[8:10], "little")
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        fmt = "webp"
        head = raw[:600_000]
        xmp = b"http://ns.adobe.com/xap" in head
    c2pa = b"c2pa" in raw[:1_000_000].lower() or b"content-credentials" in raw[:1_000_000].lower()
    out.update({"format": fmt, "width": w, "height": h,
                "has_exif": exif, "has_xmp": xmp, "has_gps": gps, "c2pa_manifest_detected": c2pa,
                "software_hint": software or None, "camera_hint": camera or None})
    notes = []
    if fmt == "jpeg":
        if exif:
            notes.append("EXIF present" + ((" — camera hint: " + camera) if camera else "")
                         + ((" · software tag: " + software) if software else "")
                         + " — read the values and cross-check against the claimed origin.")
        else:
            notes.append("No EXIF — typical for screenshots, messenger re-saves and web-optimised images. "
                         "ABSENT METADATA IS NOT PROOF OF TAMPERING, it only means provenance metadata is unavailable.")
        if gps:
            notes.append("GPS coordinates embedded — location PII; handle with care and lawful basis.")
        if c2pa:
            notes.append("C2PA/Content-Credentials manifest detected — the file claims cryptographic provenance; "
                         "validate the manifest itself with an official C2PA verifier, do not trust this string-match.")
        if fmt == "jpeg" and w and h and (w * h) % 2 != 0:
            notes.append("Odd pixel dimensions are unusual for direct camera output (heuristic only).")
    elif fmt == "png":
        notes.append("PNG commonly carries no camera EXIF; check pHYs/tEXt chunks in a full tool for edits.")
    if not out["has_exif"] and not out["has_xmp"] and not c2pa:
        notes.append("Zero provenance metadata — full EXIF/XMP/C2PA analysis (thumbnail comparison, "
                     "error-level analysis, clone detection) requires a dedicated forensic tool "
                     "(e.g. FotoForensics, Ghiro, Amplitude) — OraCool flags this for FURTHER REVIEW.")
    out["notes"] = notes
    out["disclaimer"] = ("INDICATORS REQUIRING FURTHER REVIEW — this is metadata triage, not an "
                         "authenticity verdict. Preserve the original bytes: this artifact's SHA-256 above "
                         "can be sealed into a Case with one click from the evidence view.")
    return out


def _tif_has_gps(seg):
    """Locate the GPSInfo IFD pointer (0x8825) inside an EXIF TIFF block."""
    try:
        if b"Exif\x00\x00" not in seg:
            return False
        tiff = seg[seg.index(b"Exif\x00\x00") + 6:]
        if len(tiff) < 8:
            return False
        end = "<" if tiff[:2] == b"II" else ">"
        ifd_off = int.from_bytes(tiff[4:8], end)
        n = int.from_bytes(tiff[ifd_off:ifd_off + 2], end)
        for i in range(n):
            e = ifd_off + 2 + i * 12
            if tiff[e:e + 2] in (b"\x25\x88", b"\x88\x25"):
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- smart home (Home Assistant)

def _ha_headers(tok=None):
    return {"Authorization": "Bearer " + (tok or key("HA_TOKEN")),
            "Content-Type": "application/json"}


def smart_list(ha_url=None, ha_token=None):
    url = ha_url or key("HA_URL"); tok = ha_token or key("HA_TOKEN")
    if not url or not tok:
        return {"configured": False, "message": "Home Assistant not linked. Add HA_URL + HA_TOKEN."}
    try:
        st, raw, _ = http_fetch(url.rstrip("/") + "/api/states",
                                headers=_ha_headers(tok), timeout=15)
        states = json.loads(raw)
        ents = []
        for s in states:
            eid = s.get("entity_id", "")
            ent = {"id": eid, "state": s.get("state"),
                   "name": (s.get("attributes") or {}).get("friendly_name") or eid}
            if eid.startswith(("light.", "switch.", "fan.", "climate.", "lock.", "media_player.", "cover.")):
                ents.append(ent)
        return {"configured": True, "devices": ents}
    except Exception as e:
        return {"configured": True, "error": f"Home Assistant error: {e}"}


def smart_control(entity_id, action, ha_url=None, ha_token=None, **extra):
    url = ha_url or key("HA_URL"); tok = ha_token or key("HA_TOKEN")
    if not url or not tok:
        return {"error": "Home Assistant not linked."}
    entity_id = (entity_id or "").strip()
    action = (action or "").strip().lower()
    if not entity_id:
        return {"error": "Provide entity_id (e.g. light.living_room)."}
    domain = entity_id.split(".")[0]
    if not action:
        action = "toggle"
    service = f"{domain}/{action}"
    body = {"entity_id": entity_id}
    if extra:
        body.update(extra)
    try:
        st, raw, _ = http_fetch(url.rstrip("/") + "/api/services/" + service,
                                method="POST", headers=_ha_headers(tok),
                                json_body=body, timeout=20)
        return {"ok": True, "service": service, "entity_id": entity_id,
                "status": st}
    except Exception as e:
        return {"error": f"Smart control failed: {e}"}


def _smart_intent(text, ha_url=None, ha_token=None):
    """Map natural language to a Home Assistant action, if HA is linked."""
    url = ha_url or key("HA_URL"); tok = ha_token or key("HA_TOKEN")
    if not (url and tok):
        return None
    low = (text or "").lower()
    if not any(k in low for k in ("turn", "switch", "light", "fan", "thermostat",
                                  "lock", "tv", "speaker", "ac", "aircon", "device")):
        return None
    on = bool(re.search(r"\bon\b|open|start|unlock|arm", low))
    off = bool(re.search(r"\boff\b|close|stop|lock|disarm", low))
    action = None
    if on and not off:
        action = "turn_on"
    elif off and not on:
        action = "turn_off"
    else:
        action = "toggle"
    try:
        st, raw, _ = http_fetch(url.rstrip("/") + "/api/states",
                                headers=_ha_headers(tok), timeout=12)
        states = json.loads(raw)
    except Exception:
        return None
    best = None
    best_score = 0
    words = re.findall(r"[a-z0-9]+", low)
    for s in states:
        eid = s.get("entity_id", "")
        if not eid.startswith(("light.", "switch.", "fan.", "climate.", "lock.", "media_player.", "cover.")):
            continue
        fname = str((s.get("attributes") or {}).get("friendly_name") or eid).lower()
        score = sum(1 for w in words if w in fname or w in eid)
        if score > best_score:
            best_score, best = score, {"id": eid, "name": fname, "state": s.get("state")}
    if best and best_score >= 1:
        return {"action": action, "entity_id": best["id"], "name": best["name"],
                "current_state": best["state"]}
    return None


# ---------------------------------------------------------------- PRO tools

def pro_search(query, max_results=5):
    query = (query or "").strip()
    if not query:
        return {"error": "Provide a search query."}
    k = key("TAVILY_API_KEY")
    if not k:
        return {"error": "Tavily key not configured."}
    try:
        _, raw, _ = http_fetch("https://api.tavily.com/search", method="POST",
                               json_body={"api_key": k, "query": query,
                                          "max_results": int(max_results), "search_depth": "basic"},
                               timeout=30)
        d = json.loads(raw)
        return {"query": query, "answer": d.get("answer"),
                "results": [{"title": r.get("title"), "url": r.get("url"),
                             "content": (r.get("content") or "")[:300]} for r in d.get("results", [])]}
    except Exception as e:
        return {"error": f"Tavily failed: {e}"}

def pro_shodan(ip):
    ip = (ip or "").strip()
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
        return {"error": "Provide a valid IPv4 address."}
    k = key("SHODAN_API_KEY")
    if not k:
        return {"error": "Shodan key not configured."}
    try:
        _, raw, _ = http_fetch(f"https://api.shodan.io/shodan/host/{ip}?key={k}", timeout=25)
        return {"host": json.loads(raw)}
    except urllib.error.HTTPError as e:
        return {"error": f"Shodan error {e.code}: {e.read().decode('utf-8','replace')[:200]}"}
    except Exception as e:
        return {"error": f"Shodan failed: {e}"}

def pro_virustotal(target, kind="domain"):
    target = (target or "").strip()
    if not target:
        return {"error": "Provide a target (domain, IP, URL or hash)."}
    k = key("VIRUSTOTAL_API_KEY")
    if not k:
        return {"error": "VirusTotal key not configured."}
    headers = {"x-apikey": k}
    base = "https://www.virustotal.com/api/v3"
    if kind == "ip":
        url = f"{base}/ip_addresses/{urllib.parse.quote(target)}"
    elif kind == "hash":
        url = f"{base}/files/{urllib.parse.quote(target)}"
    elif kind == "url":
        uid = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        url = f"{base}/urls/{uid}"
    else:
        url = f"{base}/domains/{urllib.parse.quote(target)}"
    try:
        _, raw, _ = http_fetch(url, headers=headers, timeout=25)
        d = json.loads(raw).get("data", {})
        attrs = d.get("attributes", {})
        return {"target": target, "kind": kind,
                "reputation": attrs.get("reputation"), "harmless": attrs.get("total_votes", {}).get("harmless"),
                "malicious": attrs.get("total_votes", {}).get("malicious"),
                "last_analysis_stats": attrs.get("last_analysis_stats"),
                "registrar": attrs.get("registrar"), "creation_date": attrs.get("creation_date"),
                "country": attrs.get("country"), "as_owner": attrs.get("as_owner"),
                "last_analysis_date": attrs.get("last_analysis_date"),
                "categories": attrs.get("categories")}
    except urllib.error.HTTPError as e:
        return {"error": f"VirusTotal error {e.code}: {e.read().decode('utf-8','replace')[:200]}"}
    except Exception as e:
        return {"error": f"VirusTotal failed: {e}"}

def pro_abuseipdb(ip, days=90):
    ip = (ip or "").strip()
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
        return {"error": "Provide a valid IPv4 address."}
    k = key("ABUSEIPDB_API_KEY")
    if not k:
        return {"error": "AbuseIPDB key not configured."}
    try:
        url = ("https://api.abuseipdb.com/api/v2/check?ipAddress=" + urllib.parse.quote(ip)
               + f"&maxAgeInDays={int(days)}")
        _, raw, _ = http_fetch(url, headers={"Key": k}, timeout=25)
        d = json.loads(raw).get("data", {})
        return {"ip": d.get("ipAddress"), "is_public": d.get("isPublic"),
                "abuse_confidence_score": d.get("abuseConfidenceScore"),
                "usage_type": d.get("usageType"), "isp": d.get("isp"),
                "domain": d.get("domain"), "country": d.get("countryCode"),
                "total_reports": d.get("totalReports"), "last_reported": d.get("lastReportedAt")}
    except Exception as e:
        return {"error": f"AbuseIPDB failed: {e}"}

def pro_urlscan(url):
    url = (url or "").strip()
    if not url.startswith("http"):
        url = "https://" + url
    k = key("URLSCAN_API_KEY")
    if not k:
        return {"error": "URLScan key not configured."}
    try:
        _, raw, _ = http_fetch("https://urlscan.io/api/v1/scan/", method="POST",
                               headers={"API-Key": k}, json_body={"url": url, "visibility": "public"},
                               timeout=30)
        d = json.loads(raw)
        return {"message": d.get("message"), "uuid": d.get("uuid"),
                "api": d.get("api"), "result_url": d.get("result"),
                "hint": "Scan is processing — results appear on the URLScan page in ~30-60s."}
    except Exception as e:
        return {"error": f"URLScan failed: {e}"}

def pro_leakcheck(query, ltype="email"):
    query = (query or "").strip()
    if not query:
        return {"error": "Provide an email, username or phone number."}
    k = key("LEAKCHECK_API_KEY")
    if not k:
        return {"error": "LeakCheck key not configured."}
    try:
        url = ("https://leakcheck.io/api?key=" + urllib.parse.quote(k)
               + "&check=" + urllib.parse.quote(query) + "&type=" + ltype)
        _, raw, _ = http_fetch(url, timeout=30)
        d = json.loads(raw)
        if not d.get("success"):
            err = d.get("error", "") or ""
            if "license" in err.lower():
                return {"error": ("LeakCheck: your key is valid but has no paid license "
                                  "attached. Purchase a license for this key at "
                                  "https://leakcheck.io and it will work immediately.")}
            return {"error": (err + " — check your LeakCheck key at leakcheck.io.").strip()}
        return {"query": query, "type": ltype, "found": d.get("found"),
                "sources": d.get("result", [])}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
            bd = json.loads(body)
            err = (bd.get("error") or "").lower()
            if "license" in err:
                return {"error": ("LeakCheck: your key is valid but has no paid license "
                                  "attached. Purchase a license for this key at "
                                  "https://leakcheck.io and it will work immediately.")}
            return {"error": (bd.get("error") or f"LeakCheck error {e.code}")}
        except Exception:
            return {"error": (f"LeakCheck error {e.code} — the LeakCheck key in keys.json "
                              "was rejected. Get a valid paid key at https://leakcheck.io "
                              "(it costs a few $/month; free keys can't query the breach API).")}
    except Exception as e:
        return {"error": f"LeakCheck failed: {e}"}

# ---------------------------------------------------------------- Home Assistant

def ha_request(ha_url, ha_token, path, method="GET", json_body=None, timeout=25):
    base = (ha_url or "").strip().rstrip("/")
    if not base or not ha_token:
        return {"error": "Home Assistant URL and token are required (Settings → Devices)."}
    headers = {"Authorization": f"Bearer {ha_token.strip()}", "Content-Type": "application/json"}
    try:
        st, raw, _ = http_fetch(base + path, method=method, headers=headers,
                                json_body=json_body, timeout=timeout)
        try:
            return {"status": st, "data": json.loads(raw)}
        except Exception:
            return {"status": st, "data": raw.decode("utf-8", "replace")[:800]}
    except urllib.error.HTTPError as e:
        return {"error": f"Home Assistant error {e.code}"}
    except Exception as e:
        return {"error": f"Could not reach Home Assistant: {e}"}

# ---------------------------------------------------------------- Paystack

def paystack_initialize(email, callback_url, plan="pro", currency=None):
    secret = (key("PAYSTACK_TEST_SECRET") if key("PAYSTACK_TEST")
              else key("PAYSTACK_SECRET_KEY"))
    if not secret:
        return {"error": "Paystack secret key not configured."}
    plan = (plan or "pro").lower()
    if plan == "fine":
        p = {"label": "Account reinstatement fine (no longer collected)", "price_usd": FINE_USD, "price_ngn": fine_ngn(), "days": 0}
    elif plan == "verified":
        # Verified badge (✦): paid monthly, grants no plan tier.
        p = {"label": "OraCool Verified badge", "price_usd": BADGE_USD, "price_ngn": badge_ngn(), "days": 30}
    else:
        if plan not in PLANS:
            plan = "pro"
        if PLANS[plan].get("custom"):
            return {"error": "That plan is a bespoke agreement — contact "
                             + (admin_emails()[0] if admin_emails() else "the OraCool team")
                             + " for a quote."}
        p = PLANS[plan]
    currency = (currency or KEYS.get("PAYSTACK_CURRENCY") or "NGN").upper()
    if currency not in ("NGN", "USD"):
        currency = "NGN"
    if currency == "USD":
        amount = p["price_usd"] * 100   # Paystack USD accounts charge in cents
    else:
        currency = "NGN"
        amount = p["price_ngn"] * 100
    label = p["label"] + (" · " + str(p["days"]) + " days" if p.get("days") else "")
    metadata = {"product": "OraCool AI", "plan": plan}
    if plan == "fine":
        metadata.update({"purpose": "fine", "account": (email or "").strip().lower()})
    if plan == "verified":
        metadata.update({"purpose": "verified", "account": (email or "").strip().lower()})
    try:
        _, raw, _ = http_fetch("https://api.paystack.co/transaction/initialize",
                               method="POST", headers={"Authorization": "Bearer " + secret},
                               json_body={"email": email, "amount": amount,
                                          "currency": currency, "callback_url": callback_url,
                                          "metadata": metadata},
                               timeout=30)
        d = json.loads(raw)
        if not d.get("status"):
            return {"error": d.get("message", "Paystack error")}
        return {"authorization_url": d["data"]["authorization_url"],
                "reference": d["data"]["reference"], "access_code": d["data"]["access_code"],
                "amount": amount / 100, "currency": currency,
                "amount_ngn": p["price_ngn"], "price_usd": p["price_usd"],
                "label": label, "plan": plan, "days": p["days"]}
    except Exception as e:
        return {"error": f"Paystack failed: {e}"}

def record_paystack_success(data):
    """Persist a successful Paystack charge as a subscription (shared by
    verify-on-return and the webhook)."""
    email = (data.get("customer") or {}).get("email")
    meta = data.get("metadata") or {}
    plan = (meta.get("plan") or "pro").lower()
    if plan == "fine" or meta.get("purpose") == "fine":
        # Reinstatement fine: lift the suspension, record the fine, grant nothing.
        account = (meta.get("account") or email or "").strip().lower()
        r = fine_paid(account, data.get("reference"), (data.get("amount") or 0) / 100,
                      data.get("currency") or "NGN", data.get("channel") or "", "paystack")
        return {"email": account, "reference": data.get("reference"), "fine": True,
                "unblocked": bool(r.get("unblocked")), "amount_ngn": (data.get("amount") or 0) / 100,
                "paid_at": data.get("paid_at"), "plan": "fine", "tier": "free"}
    if plan == "verified" or meta.get("purpose") == "verified":
        account = (meta.get("account") or email or "").strip().lower()
        until = set_verified(account, months=1)
        try:
            notify_admins("payment", "✦ Verified badge: " + account,
                          "₦{:,.0f} · ref {} · active until {}".format((data.get("amount") or 0) / 100, str(data.get("reference")), str(until)[:10]))
        except Exception:
            pass
        return {"email": account, "reference": data.get("reference"), "badge": True,
                "verified_until": until, "plan": "verified", "tier": "free"}
    if plan not in PLANS:
        plan = "pro"
    days = PLANS[plan]["days"]
    rec = {"email": email, "reference": data.get("reference"),
           "amount_ngn": (data.get("amount") or 0) / 100,
           "paid_at": data.get("paid_at"),
           "expires_at": time.strftime("%Y-%m-%d", time.gmtime(time.time() + days * 86400)),
           "channel": data.get("channel"), "tier": plan, "plan": plan}
    save_subscriber(rec)
    supabase_store_subscriber(rec)
    try:
        emit_event(email or "", "payment", "Payment received — ₦{:,.0f} · {}".format(
            rec.get("amount_ngn") or 0, str(plan).upper()),
            "ref " + str(rec.get("reference")) + " · via " + str(rec.get("channel") or "?") +
            " · expires " + str(rec.get("expires_at")))
        notify_admins("payment", "💰 Revenue: ₦{:,.0f} · {}".format(rec.get("amount_ngn") or 0, str(plan).upper()),
                      str(email) + " ref " + str(rec.get("reference")))
    except Exception:
        pass
    return rec


def paystack_verify(reference):
    secret = (key("PAYSTACK_TEST_SECRET") if key("PAYSTACK_TEST")
              else key("PAYSTACK_SECRET_KEY"))
    if not secret:
        return {"error": "Paystack secret key not configured."}
    try:
        _, raw, _ = http_fetch("https://api.paystack.co/transaction/verify/"
                               + urllib.parse.quote(reference),
                               headers={"Authorization": "Bearer " + secret}, timeout=30)
        d = json.loads(raw)
        if not d.get("status"):
            return {"error": d.get("message", "Verification failed")}
        data = d["data"]
        if data.get("status") != "success":
            return {"status": data.get("status"), "gateway_response": data.get("gateway_response"),
                    "message": "Payment not successful."}
        rec = record_paystack_success(data)
        if rec.get("fine"):
            return {"status": "success", "fine": True, "unblocked": bool(rec.get("unblocked")),
                    "email": rec.get("email"), "reference": rec.get("reference"),
                    "message": "Reinstatement fine received." + (" Access restored." if rec.get("unblocked") else " Reinstatement is being applied.")}
        return {"status": "success", "token": make_tier_token(rec.get("email"), rec.get("plan")),
                "subscriber": rec}
    except Exception as e:
        return {"error": f"Verification failed: {e}"}

# ---------------------------------------------------------------- tier helpers

# Each tier unlocks everything in the tiers BELOW it, plus its own extras.
# free < starter < pro < ultra < enterprise  (strict cumulative access)
TIER_ORDER = ["free", "starter", "pro", "ultra", "enterprise"]
TIER_TOOL_SETS = {
    "free":       ["ip", "domain", "email", "username", "phone", "weather", "stock", "crypto", "fred", "time", "math", "space"],
    "starter":    ["search"],
    "pro":        ["shodan", "virustotal", "abuseipdb", "urlscan", "leakcheck", "image", "mars", "library"],
    "ultra":      ["video", "github", "domscan", "fcs", "smart"],
    "enterprise": ["*"],
}
FREE_TOOLS = TIER_TOOL_SETS["free"]
PRO_TOOLS = TIER_TOOL_SETS["starter"] + TIER_TOOL_SETS["pro"] + TIER_TOOL_SETS["ultra"]

def tools_for_tier(tier):
    """Cumulative tool list for a tier (inherits everything below it)."""
    tier = (tier or "free").lower()
    if tier not in TIER_RANK:
        tier = "free"
    out = []
    for t2 in TIER_ORDER:
        out.extend(TIER_TOOL_SETS.get(t2, []))
        if t2 == tier:
            break
    return out

def tier_of_tool(tool):
    """Lowest tier that unlocks a given tool (for gating messages)."""
    for t2 in TIER_ORDER:
        if tool in TIER_TOOL_SETS.get(t2, []):
            return t2
    return "free"

# Names whose *loaded / not loaded* status the Server Key Vault panel shows.
# Values are never sent anywhere; the status list itself is administrator-only
# (see admin_vault_status / POST /api/admin/keys) — ordinary accounts must not
# learn which providers or secrets the server holds.
VAULT_STATUS_KEYS = ["OPENAI_API_KEY", "GROQ_API_KEY", "AGNES_API_KEY", "NEXAAPI_KEY",
                     "PAYSTACK_SECRET_KEY", "PAYSTACK_PUBLIC_KEY", "SUPABASE_URL",
                     "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY", "GITHUB_TOKEN",
                     "SHODAN_API_KEY", "VIRUSTOTAL_API_KEY", "ABUSEIPDB_API_KEY",
                     "IPINFO_API_KEY", "NUMVERIFY_API_KEY", "LEAKCHECK_API_KEY",
                     "URLSCAN_API_KEY", "TAVILY_API_KEY", "FINNHUB_API_KEY",
                     "COINGECKO_API_KEY", "FRED_API_KEY", "ALPACA_PAPER_KEY_ID",
                     "ALPACA_PAPER_SECRET", "NASA_API_KEY", "HIBP_API_KEY", "HIA_API_KEY",
                     "PIXAZO_KEY", "SHORTAPI_KEY", "TOKENMIX_API_KEY", "FCS_API_KEY",
                     "DOMSCAN_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "HA_URL",
                     "KAIROS_API_KEY", "KAIROS_APP_ID", "ATLOS_MERCHANT_ID", "ATLOS_API_SECRET",
                     "CRYPTO_WALLET_EVM", "SENDGRID_API_KEY",
                     "SENDGRID_FROM_EMAIL", "JWT_SECRET", "ENCRYPTION_KEY", "ENCRYPTION_IV"]


def admin_vault_status():
    """Administrator-only: which server secrets are loaded (booleans only, never values)."""
    return {
        "keys": {n: bool(key(n)) for n in VAULT_STATUS_KEYS},
        "brain": {"openai": bool(key("OPENAI_API_KEY")), "groq": bool(key("GROQ_API_KEY")),
                  "agnes": bool(key("AGNES_API_KEY")),
                  "default_provider": KEYS.get("BRAIN_PROVIDER", "groq"),
                  "default_model": KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL),
                  "fast_model": KEYS.get("GROQ_FAST_MODEL", GROQ_DEFAULT_MODEL)},
        "source": "environment + keys.json (server side only)",
        "note": "Statuses are visible to administrators only. Values never leave the server.",
    }


def user_model_allowlist():
    """Models an ordinary (non-admin) account may request by name — only the
    server-configured ones, so the Turbo/Smart switch keeps working while
    arbitrary model names, custom keys and custom base URLs are ignored."""
    names = {KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL), KEYS.get("GROQ_FAST_MODEL", GROQ_DEFAULT_MODEL),
             KEYS.get("AGNES_MODEL", "agnes-2.5-flash"), GROQ_DEFAULT_MODEL}
    return {str(n).strip() for n in names if str(n or "").strip()}


def get_config():
    brain_ready = bool(key("OPENAI_API_KEY") or key("GROQ_API_KEY") or key("AGNES_API_KEY"))
    return {
        # Public payload: capability flags only. The per-secret inventory lives
        # behind POST /api/admin/keys (administrators only).
        "brain": {"ready": brain_ready,
                  "default_provider": KEYS.get("BRAIN_PROVIDER", "groq"),
                  "default_model": KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL),
                  "fast_model": KEYS.get("GROQ_FAST_MODEL", GROQ_DEFAULT_MODEL),
                  "chat_max_tokens": int(KEYS.get("CHAT_MAX_TOKENS", 3000))},
        "payments_ready": bool(key("PAYSTACK_SECRET_KEY")),
        "paystack_public_key": key("PAYSTACK_PUBLIC_KEY") if not key("PAYSTACK_TEST") else key("PAYSTACK_TEST_PUBLIC"),
        "paystack_test": bool(key("PAYSTACK_TEST")),
        "paystack_currency": (KEYS.get("PAYSTACK_CURRENCY") or "NGN").upper(),
        "pro_price_ngn": int(KEYS.get("PRO_PRICE_NGN", 50000)),
        "pro_label": KEYS.get("PRO_LABEL", "PRO Access · 30 days"),
        "free_tools": FREE_TOOLS, "pro_tools": PRO_TOOLS,
        "tier_tools": {t: tools_for_tier(t) for t in TIER_ORDER},
        "tier_tool_sets": TIER_TOOL_SETS,
        "auth": {"supabase": bool(key("SUPABASE_URL") and key("SUPABASE_ANON_KEY")),
                 "github": bool(key("GITHUB_TOKEN"))},
        "oauth_providers": oauth_providers(),
        "investigation": {"cases": True, "evidence_sha256": True, "custody": True,
                           "entity_extraction": True, "wallet_tracing": True,
                           "watch_monitoring": True, "audit_log": True},
        "privacy_policy": "/privacy",
        "document_verification": doc_verification_state(),
        "media_forensics": True,
        "case_persistence": "supabase" if (key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY")) else "disk-only",
        "trading": {"symbols": list(TRADING_SYMBOLS.keys()),
                    "alpaca_ready": bool(key("ALPACA_PAPER_KEY_ID") and key("ALPACA_PAPER_SECRET"))},
        "plans": [{"id": pid, "label": p["label"], "price_usd": p["price_usd"],
                   "price_ngn": p["price_ngn"], "days": p["days"], "rank": TIER_RANK[pid],
                   "custom": bool(p.get("custom")),
                   "coins_wk": int(p.get("coins_wk") or 0),
                   "coins_unlimited": bool(p.get("coins_unlimited"))}
                  for pid, p in PLANS.items()],
        "tiers": list(TIER_RANK.keys()),
        "nasa_ready": bool(key("NASA_API_KEY")),
        "image_ready": True,  # capability, not a provider uptime guarantee
        "media_providers": {"hiapi": bool(key("HIA_API_KEY")), "tokenmix": bool(key("TOKENMIX_API_KEY")),
                             "free_hd_engine": True},
        "video_ready": True,
        "cvron_ready": True,
        "email_verification": False,
        "darkweb_ready": True,
        "darkweb_open": True,
        "crypto_ready": bool(crypto_wallet() or (key("ATLOS_API_SECRET") and key("ATLOS_MERCHANT_ID"))),
        "news_ready": True,
        "agnes_ready": bool(key("AGNES_API_KEY")),
        "generator_ready": True,
        "skills_ready": True,
        "upload_media": True,
        "tracker_domain": (key("TRACKER_DOMAIN") or "").strip(),
        "app_launch": True,
        "verify_mode": "none",
        "build": "patch46-durable",
        "smart_home": {"configured": bool(key("HA_URL") and key("HA_TOKEN"))},
        "cores_total": _cores_total(),
        "admin_count": len(admin_emails()),
    }

# ---------------------------------------------------------------- NASA / Space

def _nasa_fetch(url, timeout=25):
    st, raw, _ = http_fetch(url, timeout=timeout)
    return json.loads(raw)

def space_apod(date=None, count=1):
    k = key("NASA_API_KEY")
    if not k:
        return {"error": "NASA API key is not configured."}
    try:
        if date:
            params = {"api_key": k, "date": date}
        elif count and int(count) > 1:
            params = {"api_key": k, "count": max(1, min(int(count), 10))}
        else:
            params = {"api_key": k}
        d = _nasa_fetch("https://api.nasa.gov/planetary/apod?" + urllib.parse.urlencode(params))
        if isinstance(d, dict):
            return {"title": d.get("title"), "date": d.get("date"),
                    "explanation": d.get("explanation"), "url": d.get("url"),
                    "hdurl": d.get("hdurl"), "media_type": d.get("media_type"),
                    "copyright": d.get("copyright")}
        return {"items": [{"title": x.get("title"), "date": x.get("date"), "url": x.get("url")}
                          for x in d[:count]]}
    except Exception as e:
        return {"error": f"APOD failed: {e}"}

def space_epic():
    try:
        d = _nasa_fetch("https://epic.gsfc.nasa.gov/api/natural")
        items = []
        for x in d[:12]:
            ident = x.get("image")
            date = (x.get("date") or "").split(" ")[0].replace("-", "/")
            items.append({"id": ident, "caption": x.get("caption"), "date": x.get("date"),
                          "url": f"https://epic.gsfc.nasa.gov/archive/natural/{date}/png/{ident}.png"})
        return {"count": len(items), "items": items}
    except Exception as e:
        return {"error": f"EPIC failed: {e}"}

def space_neo():
    k = key("NASA_API_KEY")
    if not k:
        return {"error": "NASA API key is not configured."}
    today = time.strftime("%Y-%m-%d")
    url = f"https://api.nasa.gov/neo/rest/v1/feed?start_date={today}&end_date={today}&api_key={k}"
    try:
        d = _nasa_fetch(url)
        out = []
        for day, objs in (d.get("near_earth_objects") or {}).items():
            for o in objs:
                diam = (o.get("estimated_diameter", {}).get("kilometers", {})
                        .get("estimated_diameter_max"))
                miss = (o.get("close_approach_data") or [{}])[0].get("miss_distance", {}).get("kilometers")
                out.append({"name": o.get("name"),
                            "hazardous": bool(o.get("is_potentially_hazardous_asteroid")),
                            "diameter_km": round(diam, 3) if isinstance(diam, (int, float)) else None,
                            "miss_km": miss})
        out.sort(key=lambda x: float(x["miss_km"]) if x["miss_km"] else 1e12)
        return {"element_count": d.get("element_count"), "asteroids": out[:15],
                "note": "Near-Earth Objects passing closest to Earth today."}
    except Exception as e:
        return {"error": f"NEO failed: {e}"}

def space_mars(rover="curiosity"):
    rover = (rover or "curiosity").lower()
    if rover not in ("curiosity", "opportunity", "spirit", "perseverance"):
        rover = "curiosity"
    q = urllib.parse.quote(rover + " rover surface")
    url = f"https://images-api.nasa.gov/search?q={q}&media_type=image&page=1"
    try:
        d = _nasa_fetch(url, timeout=30)
        col = (d.get("collection") or {}).get("items") or []
        items = []
        for it in col[:12]:
            data = (it.get("data") or [{}])[0]
            links = it.get("links") or []
            thumb = next((l.get("href") for l in links if l.get("rel") == "preview"), None)
            items.append({"nasa_id": data.get("nasa_id"), "title": data.get("title"),
                          "description": (data.get("description") or "")[:220],
                          "date": data.get("date_created"), "url": thumb})
        return {"rover": rover, "count": len(items), "items": items,
                "note": "NASA's Mars rover photos API was retired; showing the latest rover imagery from the NASA Image Library."}
    except Exception as e:
        return {"error": f"Mars rover failed: {e}"}

def space_library(query="earth", page=1):
    q = urllib.parse.quote((query or "earth").strip())
    url = f"https://images-api.nasa.gov/search?q={q}&media_type=image&page={page or 1}"
    try:
        d = _nasa_fetch(url, timeout=30)
        col = (d.get("collection") or {}).get("items") or []
        items = []
        for it in col[:12]:
            data = (it.get("data") or [{}])[0]
            links = it.get("links") or []
            thumb = next((l.get("href") for l in links if l.get("rel") == "preview"), None)
            items.append({"nasa_id": data.get("nasa_id"), "title": data.get("title"),
                          "description": (data.get("description") or "")[:220],
                          "date": data.get("date_created"), "url": thumb})
        return {"query": query, "count": len(items), "items": items}
    except Exception as e:
        return {"error": f"NASA library failed: {e}"}

# ---------------------------------------------------------------- cores / plans

def _cores_total():
    try:
        return len(cores.load())
    except Exception:
        return 0

def cores_list(query=None, limit=50):
    if query:
        hits = cores.search(query, limit)
        return {"total": _cores_total(), "count": len(hits), "cores": hits}
    lim = max(1, min(int(limit or 50), 200))
    return {"total": _cores_total(), "clusters": cores.clusters(), "cores": cores.load()[:lim]}

def core_route(query):
    return cores.route(query)

def plan_info(body):
    email = (body.get("email") or "").strip()
    token = body.get("token") or ""
    if token:
        payload = verify_jwt(token, key("JWT_SECRET") or "dev-secret")
        if payload and payload.get("sub"):
            email = payload["sub"]
    tier = check_tier(email) if email else "free"
    plans = [{"id": pid, "label": p["label"], "price_usd": p["price_usd"],
              "price_ngn": p["price_ngn"], "days": p["days"], "rank": TIER_RANK[pid],
              "custom": bool(p.get("custom")),
              "coins_wk": int(p.get("coins_wk") or 0),
              "coins_unlimited": bool(p.get("coins_unlimited"))}
             for pid, p in PLANS.items()]
    plans.sort(key=lambda x: x["rank"])
    for x in plans:
        x["sites_wk"] = (10 ** 9 if x["coins_unlimited"]
                         else x["coins_wk"] // COIN_COST_BUILD)
    return {"tier": tier, "email": email, "plans": plans,
            "coins": {"free_weekly": COINS_WEEKLY["free"], "cost_build": COIN_COST_BUILD,
                      "state": coins_state(email) if email else None},
            "note": "Admins receive full Enterprise access automatically."}

# ============================================ connectors, alerts & gateway (24/7 platform)

_ALERTS_LOCK = threading.RLock()
_OUTBOX_LOCK = threading.Lock()
_alerts_dirty = [False]
_gw_rl = {}
_track_last = {}

CONN_TYPES = {
    "telegram": {"label": "Telegram bot", "fields": ["bot_token", "chat_id"],
                 "secrets": ["bot_token"],
                 "help": "Create a bot with @BotFather, paste its token. Get your numeric chat_id from @userinfobot. The bot must have joined/left your chat."},
    "whatsapp": {"label": "WhatsApp (Meta Cloud API)", "fields": ["access_token", "phone_number_id", "to_number"],
                 "secrets": ["access_token"],
                 "help": "Meta developer portal: a WhatsApp Business app — permanent token, phone number id and your number in international format."},
    "discord": {"label": "Discord webhook", "fields": ["webhook_url"], "secrets": ["webhook_url"],
                "help": "Channel settings → Integrations → Webhooks → New Webhook → copy URL."},
    "slack": {"label": "Slack incoming webhook", "fields": ["webhook_url"], "secrets": ["webhook_url"],
              "help": "App management → Incoming Webhooks → Add to channel → copy URL."},
    "webhook": {"label": "Any webhook / your own server", "fields": ["url", "secret"], "optional": ["secret"], "secrets": ["secret"],
                "help": "OraCool POSTs JSON {title, body, time, source} to your URL, signed with X-OraCool-Signature (HMAC-SHA256 of the raw body, hex) if a secret is set."},
    "email": {"label": "Email relay (outbound)", "fields": ["to"], "secrets": [],
              "help": "Works only if the server host configured RESEND_API_KEY — otherwise use Telegram/Discord/webhook."},
    "mail": {"label": "📧 Mail watch (your inbox)", "fields": ["email", "app_password", "imap_host"],
             "optional": ["imap_host"], "secrets": ["app_password"],
             "help": "Read-only UNSEEN watch on YOUR mailbox over IMAP: OraCool reports how many unread "
                     "messages you have and their subjects/senders — never message bodies. Gmail/Outlook/Zoho "
                     "need an APP PASSWORD (Google: Account → Security → 2-Step Verification → App passwords). "
                     "imap_host defaults to imap.gmail.com. New-mail alerts fire to your other channels."},
}


def _alerts_file():
    return os.path.join(DATA_DIR, "alerts.json")


def _outbox_file():
    return os.path.join(DATA_DIR, "alerts_outbox.json")


def supabase_kv_put(k, obj):
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return False
    try:
        http_fetch(url.rstrip("/") + "/rest/v1/case_store", method="POST",
                   headers={"apikey": svc, "Authorization": "Bearer " + svc,
                            "Content-Type": "application/json",
                            "Prefer": "resolution=merge-duplicates"},
                   json_body={"k": k, "v": obj, "updated_at": _now()}, timeout=25)
        return True
    except Exception:
        return False


def supabase_kv_get(k):
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return None
    try:
        _, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/case_store?k=eq." + k + "&select=v",
                               headers={"apikey": svc, "Authorization": "Bearer " + svc}, timeout=15)
        rows = json.loads(raw)
        if rows:
            v = rows[0].get("v")
            if isinstance(v, str):
                v = json.loads(v)
            return v if isinstance(v, dict) else None
    except Exception:
        return None
    return {}


def _alerts_all():
    try:
        with open(_alerts_file()) as f:
            return json.load(f)
    except Exception:
        try:  # first boot after a Render redeploy: recover from the durable kv mirror
            remote = supabase_kv_get("alerts")
            if isinstance(remote, dict):
                return remote
        except Exception:
            pass
        return {}


def _alerts_write(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = _alerts_file() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _alerts_file())
    _alerts_dirty[0] = True


def _outbox_load():
    try:
        with open(_outbox_file()) as f:
            return json.load(f)
    except Exception:
        return []


def _outbox_write(ob):
    tmp = _outbox_file() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ob, f)
    os.replace(tmp, _outbox_file())


def _default_alerts_state():
    return {"connectors": {}, "events": {"login": True, "payment": True, "security": True,
                                         "watch": True, "tracker": False, "gateway": True,
                                         "digest": False, "mail": True}, "mail_seen": {},
            "push_subs": [], "inbox": [], "delivered": [], "digest_hour": 7,
            "last_digest": "", "api_key_sha": "", "api_key_mask": "", "hook_token": "",
            "created": _now()}


def _alert_defaults(st):
    if not isinstance(st.get("events"), dict):
        st["events"] = _default_alerts_state()["events"]
    for k, v in _default_alerts_state()["events"].items():
        st["events"].setdefault(k, v)
    for k in ("connectors", "push_subs", "inbox", "delivered"):
        if not isinstance(st.get(k), (dict, list)):
            st[k] = [] if k != "connectors" else {}
    st.setdefault("digest_hour", 7)
    st.setdefault("last_digest", "")
    if not st.get("hook_token"):
        st["hook_token"] = os.urandom(12).hex()


def alerts_state(email, create=False):
    email = (email or "").strip().lower()
    if not email:
        return None
    with _ALERTS_LOCK:
        d = _alerts_all()
        st = d.get(email)
        if st is None and create:
            st = _default_alerts_state()
            d[email] = st
            _alerts_write(d)
        if st is None:
            return None
        return st


def _alerts_mutate(email, fn):
    email = (email or "").strip().lower()
    if not email:
        return {"error": "Sign in first — alerts attach to your account."}
    with _ALERTS_LOCK:
        d = _alerts_all()
        st = d.setdefault(email, _default_alerts_state())
        _alert_defaults(st)
        r = fn(st)
        _alerts_write(d)
        return r


def _seal(v):
    try:
        if key("ENCRYPTION_KEY"):
            return crypto.seal(str(v), key("ENCRYPTION_KEY"), key("ENCRYPTION_IV"))
    except Exception:
        pass
    return str(v)


def _unseal(v):
    v = str(v or "")
    if re.fullmatch(r"[0-9a-f]{32,}", v):
        try:
            return crypto.open_seal(v, key("ENCRYPTION_KEY"), key("ENCRYPTION_IV"))
        except Exception:
            pass
    return v


def _mask_val(v):
    v = str(v or "")
    if not v:
        return ""
    if len(v) <= 10:
        return "•" * len(v)
    return v[:4] + "…" + v[-4:]


def _conn_public(c):
    cfg = c.get("cfg") or {}
    return {"id": c.get("id"), "type": c.get("type"), "name": c.get("name"),
            "enabled": c.get("enabled", True), "last_status": c.get("last_status") or {},
            "fields": {k: _mask_val(_unseal(v)) if k in (CONN_TYPES.get(c.get("type"), {}) or {}).get("secrets", [])
                       else _unseal(v) for k, v in cfg.items()}}


def connector_send(c, ev):
    ctype = c.get("type"); cfg = {k: _unseal(v) for k, v in (c.get("cfg") or {}).items()}
    text = "⚡ " + str(ev.get("title") or "OraCool alert") + "\n" + str(ev.get("body") or "") + \
           "\n— OraCool · " + str(ev.get("t") or "")
    try:
        if ctype == "telegram" and cfg.get("bot_token") and cfg.get("chat_id"):
            _, raw, _ = http_fetch("https://api.telegram.org/bot" + cfg["bot_token"].strip() + "/sendMessage",
                                   method="POST", timeout=15,
                                   json_body={"chat_id": str(cfg["chat_id"]).strip(), "text": text[:3800],
                                              "disable_web_page_preview": True})
            d = json.loads(raw) if raw else {}
            if d.get("ok"):
                return {"ok": True}
            return {"ok": False, "error": str(d.get("description") or raw)[:160]}
        if ctype in ("discord", "slack") and str(cfg.get("webhook_url") or "").startswith("https://"):
            body = {"content": text[:1900]} if ctype == "discord" else {"text": text[:2900]}
            _, raw, _ = http_fetch(cfg["webhook_url"].strip(), method="POST", timeout=15, json_body=body)
            return {"ok": True}
        if ctype == "webhook" and str(cfg.get("url") or "").startswith("http"):
            payload = json.dumps({"title": ev.get("title"), "body": ev.get("body"),
                                  "time": ev.get("t"), "type": ev.get("type"),
                                  "source": "oracool-alerts"}).encode()
            hdrs = {"Content-Type": "application/json"}
            sec = str(cfg.get("secret") or "")
            if sec:
                import hmac as _hmac, hashlib as _hl
                hdrs["X-OraCool-Signature"] = "sha256=" + _hmac.new(sec.encode(), payload, _hl.sha256).hexdigest()
            req = urllib.request.Request(cfg["url"].strip(), data=payload, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            return {"ok": True}
        if ctype == "whatsapp" and cfg.get("access_token") and cfg.get("phone_number_id") and cfg.get("to_number"):
            _, raw, _ = http_fetch("https://graph.facebook.com/v21.0/" + str(cfg["phone_number_id"]).strip() + "/messages",
                                   method="POST", timeout=20,
                                   headers={"Authorization": "Bearer " + str(cfg["access_token"]).strip()},
                                   json_body={"messaging_product": "whatsapp", "to": re.sub(r"\D", "", str(cfg["to_number"])),
                                              "type": "text", "text": {"body": text[:1500]}})
            d = json.loads(raw) if raw else {}
            if d.get("messages") or d.get("messageId"):
                return {"ok": True}
            return {"ok": False, "error": str(d.get("error", {}).get("message") or raw)[:160]}
        if ctype == "mail":
            r = mail_check(cfg)
            return {"ok": bool(r.get("ok")), "unread": r.get("unread"), "latest": (r.get("latest") or [])[:3],
                    "error": r.get("error"), "note": r.get("note")}
        if ctype == "email":
            rk = key("RESEND_API_KEY")
            if not rk:
                return {"ok": False, "error": "no email relay configured on the server (RESEND_API_KEY)"}
            _, raw, _ = http_fetch("https://api.resend.com/emails", method="POST", timeout=20,
                                   headers={"Authorization": "Bearer " + rk},
                                   json_body={"from": key("RESEND_FROM") or "OraCool <onboarding@resend.dev>",
                                              "to": [cfg.get("to")], "subject": str(ev.get("title") or "OraCool alert"),
                                              "text": text})
            return {"ok": True}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "HTTP " + str(e.code) + " " + str(e.read().decode("utf-8", "replace"))[:120]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}
    return {"ok": False, "error": "connector not configured properly (" + str(ctype) + ")"}


# ---- push (VAPID, optional pywebpush; degrades to in-app polling) ----

def _vapid_paths():
    return (os.path.join(DATA_DIR, "vapid_private.pem"), os.path.join(DATA_DIR, "vapid_public.txt"))


def _vapid_ensure():
    pr, pu = _vapid_paths()
    try:
        if os.path.exists(pr) and os.path.exists(pu):
            pub = open(pu).read().strip()
            if pub:
                return pr, pub
    except Exception:
        pass
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        import base64 as _b64
        k = ec.generate_private_key(ec.SECP256R1())
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
        pub = k.public_key().public_bytes(serialization.Encoding.X962,
                                          serialization.PublicFormat.UncompressedPoint)
        b64 = _b64.urlsafe_b64encode(pub).decode().rstrip("=")
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(pr, "wb") as f:
            f.write(pem)
        with open(pu, "w") as f:
            f.write(b64)
        return pr, b64
    except Exception:
        return None, None


def _push_available():
    try:
        import pywebpush  # noqa: F401
        return bool(_vapid_ensure()[0])
    except Exception:
        return False


def _push_fan(email, ev):
    try:
        st = alerts_state(email)
        subs = (st or {}).get("push_subs") or []
        if not subs:
            return
        pr, pub = _vapid_ensure()
        if not pr:
            return
        from pywebpush import webpush
        import urllib.parse as _up
        admin0 = (admin_emails() or ["admin"])[0]
        for s in list(subs):
            try:
                aud = _up.urlparse(s.get("endpoint", "")).scheme + "://" + _up.urlparse(s.get("endpoint", "")).netloc
                webpush(subscription_info=s, data=json.dumps({"title": ev.get("title"), "body": ev.get("body")}),
                        vapid_private_key=pr,
                        vapid_claims={"sub": "mailto:" + admin0, "aud": aud})
            except Exception as e:
                msg = str(e)
                if "404" in msg or "410" in msg or "gone" in msg.lower():
                    with _ALERTS_LOCK:
                        d = _alerts_all()
                        sst = d.get(email)
                        if sst:
                            sst["push_subs"] = [x for x in sst.get("push_subs", [])
                                                 if x.get("endpoint") != s.get("endpoint")]
                            _alerts_write(d)
    except Exception:
        pass


# ---- the event bus: emit → inbox → connectors (retry outbox) + push ----

def emit_event(email, etype, title, body="", meta=None):
    email = (email or "").strip().lower()
    if not email:
        return {"ok": False, "error": "no email"}
    ev = {"id": os.urandom(4).hex(), "t": _now(), "type": etype,
          "title": (title or "OraCool alert")[:160], "body": (body or "")[:600]}
    if meta:
        ev["meta"] = str(meta)[:200]
    queued = 0
    push_on = True
    with _ALERTS_LOCK:
        d = _alerts_all()
        st = d.setdefault(email, _default_alerts_state())
        _alert_defaults(st)
        st.setdefault("inbox", []).append(ev)
        st["inbox"] = st["inbox"][-150:]
        _alerts_write(d)
        evs = st.get("events") or {}
        push_on = bool(evs.get(etype, True))
        try:
            with _OUTBOX_LOCK:
                ob = _outbox_load()
                for cid, c in (st.get("connectors") or {}).items():
                    if c.get("enabled", True) and evs.get(etype, True):
                        ob.append({"uid": os.urandom(4).hex(), "email": email, "cid": cid,
                                   "ev": ev, "tries": 0, "next": time.time()})
                ob = ob[-400:]
                _outbox_write(ob)
                queued = sum(1 for x in ob if x.get("ev", {}).get("id") == ev["id"])
        except Exception:
            pass
    if push_on:
        try:
            threading.Thread(target=_push_fan, args=(email, ev), daemon=True).start()
        except Exception:
            pass
    return {"ok": True, "id": ev["id"], "queued": queued}


def notify_admins(etype, title, body=""):
    for a in (admin_emails() or []):
        try:
            emit_event(a, etype, title, body)
        except Exception:
            pass


def _alerts_flush_outbox():
    now = time.time()
    with _OUTBOX_LOCK:
        ob = _outbox_load()
        if not ob:
            return
        due = [x for x in ob if x.get("next", 0) <= now]
    if not due:
        return
    touches = {}
    retry = []
    for it in due:
        st = alerts_state(it.get("email")) or {}
        c = (st.get("connectors") or {}).get(it.get("cid"))
        if not c or not c.get("enabled", True) or \
           not (st.get("events") or {}).get(it.get("ev", {}).get("type"), True):
            continue
        r = connector_send(c, it["ev"]) or {}
        t = touches.setdefault(it["email"], {"ls": {}, "delivered": []})
        t["ls"][it["cid"]] = {"t": _now(), "ok": bool(r.get("ok")), "err": (r.get("error") or "")[:160]}
        t["delivered"].append({"t": _now(), "cid": it["cid"], "type": it["ev"]["type"],
                               "ok": bool(r.get("ok")), "err": (r.get("error") or "")[:120]})
        if not r.get("ok"):
            it["tries"] = int(it.get("tries", 0)) + 1
            if it["tries"] < 3:
                it["next"] = now + 90 * it["tries"]
                retry.append(it)
    done_ids = {x.get("uid") for x in due if x.get("uid")}
    with _OUTBOX_LOCK:
        fresh = _outbox_load()
        keep = [x for x in fresh if x.get("uid") not in done_ids] + retry
        _outbox_write(keep[-400:])
    if touches:
        with _ALERTS_LOCK:
            d = _alerts_all()
            for em, ch in touches.items():
                dst = d.setdefault(em, _default_alerts_state())
                _alert_defaults(dst)
                for cid, ls in ch["ls"].items():
                    cc = (dst.get("connectors") or {}).get(cid)
                    if cc is not None:
                        cc["last_status"] = ls
                dst.setdefault("delivered", [])
                dst["delivered"] = (dst["delivered"] + ch["delivered"])[-40:]
            _alerts_write(d)


def _alerts_digest_check():
    hh = time.strftime("%H"); today = time.strftime("%Y-%m-%d")
    due = []
    with _ALERTS_LOCK:
        d = _alerts_all()
        for em, st in d.items():
            _alert_defaults(st)
            if (st.get("events") or {}).get("digest") and int(st.get("digest_hour") or 7) <= int(hh) \
               and st.get("last_digest") != today:
                st["last_digest"] = today
                evs = [e for e in st.get("inbox", []) if str(e.get("t", ""))[:10] == today]
                kinds = sorted({e.get("type", "?") for e in evs})
                summ = ("Quiet day — no events." if not evs else
                        "{} event(s) today: ".format(len(evs)) + ", ".join(kinds) + ".")
                if is_admin(em):
                    try:
                        s2 = admin_users_payload(em).get("stats") or {}
                        summ += " Board: {} users · {} paid · ₦{} total.".format(
                            s2.get("users", 0), s2.get("paid", 0), s2.get("total_pnl", 0))
                    except Exception:
                        pass
                due.append((em, summ))
        _alerts_write(d)
    for em, summ in due:
        emit_event(em, "digest", "OraCool daily summary · " + today, summ)


def _alerts_loops():
    time.sleep(6)
    _mail_tick = 0
    while True:
        try:
            _alerts_flush_outbox()
        except Exception:
            pass
        try:  # mailbox watch — every 5 minutes (20 ticks)
            _mail_tick += 1
            if _mail_tick % 20 == 0:
                mail_alert_tick()
                brand_mail_tick()
        except Exception:
            pass
        try:
            _alerts_digest_check()
        except Exception:
            pass
        try:
            if _alerts_dirty[0] and key("SUPABASE_URL"):
                _alerts_dirty[0] = False
                supabase_kv_put("alerts", _alerts_all())
        except Exception:
            pass
        time.sleep(15)


# ---- user-facing state / helpers (panel + chat tools) ----

def gateway_issue_key(email):
    def fn(st):
        raw = "ora_live_" + os.urandom(21).hex()
        st["api_key_sha"] = __import__("hashlib").sha256(raw.encode()).hexdigest()
        st["api_key_mask"] = raw[:13] + "…" + raw[-4:]
        return {"api_key": raw, "shown_once": True,
                "hook_path": "/hook/" + (st.get("hook_token") or ""),
                "note": "Use as X-OraCool-Key header. POST /api/gateway/v1/ingest or GET your hook URL from any app; the event lands in your inbox and fans out to your connected channels."}
    return _alerts_mutate(email, fn)


def gateway_auth(keystr):
    keystr = (keystr or "").strip()
    if not keystr:
        return None
    import hashlib as _hl
    want = _hl.sha256(keystr.encode()).hexdigest()
    for em, st in _alerts_all().items():
        if st.get("api_key_sha") and st["api_key_sha"] == want:
            return em
    return None


def hook_owner(tok):
    tok = (tok or "").strip()
    if not tok:
        return None
    for em, st in _alerts_all().items():
        if st.get("hook_token") and st["hook_token"] == tok:
            return em
    return None


def gateway_info(email):
    st = alerts_state((email or "").lower())
    if not st:
        st = alerts_state(email, create=True)
        st = st or _default_alerts_state()
    site = key("TRACKER_DOMAIN") or ""
    if site and not site.startswith("http"):
        site = "https://" + site
    return {"api_key_mask": st.get("api_key_mask") or "", "has_key": bool(st.get("api_key_sha")),
            "hook_url": (site or "") + "/hook/" + str(st.get("hook_token") or ""),
            "ingest_url": (site or "") + "/api/gateway/v1/ingest",
            "connectors": len([c for c in (st.get("connectors") or {}).values() if c.get("enabled", True)]),
            "events_on": sorted([k for k, v in (st.get("events") or {}).items() if v]),
            "push_ready": _push_available(), "push_subs": len(st.get("push_subs") or [])}


def alerts_panel_status(email):
    st = alerts_state((email or "").lower())
    if not st:
        return {"error": "The alerts platform is not set up for this account yet — open Devices → Connectors & Alerts once and everything lights up."}
    return {"connectors": [_conn_public(c) for c in (st.get("connectors") or {}).values()],
            "events": st.get("events"), "digest_hour": st.get("digest_hour"),
            "push_ready": _push_available(), "push_subs": len(st.get("push_subs") or []),
            "gateway": gateway_info(email),
            "recent": (st.get("inbox") or [])[-3:][::-1]}


def alerts_toggle(email, etype, on):
    return _alerts_mutate(email, lambda st: (st["events"].__setitem__(etype, bool(on)),
                                             {"ok": True, etype: bool(on)})[1])


def alerts_test_all(email, cid=None):
    st = alerts_state((email or "").lower())
    if not st or not (st.get("connectors") or {}):
        return {"error": "No connectors yet — add one in Devices → Connectors & Alerts (Telegram takes 30 seconds)."}
    ev = {"title": "OraCool test alert", "body": "If you can read this, the pipeline is live: event engine → your channel.",
          "t": _now(), "type": "test", "id": "test"}
    res = {}
    for c_id, c in (st.get("connectors") or {}).items():
        if cid and c_id != cid:
            continue
        res[c.get("name") or c.get("type")] = connector_send(c, ev)
    if cid is None and not res:
        return {"error": "All connectors are disabled."}
    return {"sent": res, "push": emit_event(email, "test", "OraCool test alert (push)", "delivered via test call") if cid is None else "skipped"}


def _gw_rate(owner, limit=120, win=60):
    now = time.time()
    e = _gw_rl.get(owner)
    if not e or now - e[0] > win:
        _gw_rl[owner] = [now, 1]
        return True
    e[1] += 1
    return e[1] <= limit


def _track_alert(rec, entry):
    owner = (rec.get("owner") or rec.get("email") or "").lower()
    if not owner:
        return
    st = alerts_state(owner)
    if not st or not (st.get("events") or {}).get("tracker"):
        return
    tkey = owner + "|" + str(entry.get("ip"))
    if time.time() - _track_last.get(tkey, 0) < 600:
        return
    _track_last[tkey] = time.time()
    geo = entry.get("geo") or {}
    emit_event(owner, "tracker", "Someone opened your tracked link",
               str((entry.get("device") or {}).get("os") or "device") + " · IP " + str(entry.get("ip")) +
               (" · " + geo.get("city") if geo.get("city") else ""))


def skills_market():
    items = []
    for owner, lst in (_skills_all() or {}).items():
        for s in lst or []:
            if s.get("shared"):
                lo = (owner or "?").split("@")[0][:1]
                dom = (owner or "?").split("@")[-1]
                items.append({"name": s.get("name"), "author": lo + "***@" + dom,
                              "trigger": s.get("trigger", ""),
                              "instructions_preview": (s.get("instructions") or "")[:240],
                              "created": s.get("created")})
    return {"skills": items[:80],
            "note": "Community skills. Owner shares with 'share skill <name>'; anyone installs with 'install skill <name>'. Marketplace never shows full instructions until installed."}


def skill_share(email, name, on=True):
    name = (name or "").strip().lower()
    def fn(st):
        pass
    with _skills_lock:
        d = _skills_all()
        lst = d.get((email or "").lower(), [])
        for s in lst:
            if s.get("name", "").lower() == name:
                s["shared"] = bool(on)
                with open(_skills_file(), "w") as f:
                    json.dump(d, f, indent=1)
                return {"ok": True, "skill": s.get("name"), "shared": bool(on),
                        "note": "It is on the marketplace now." if on else "Removed from the marketplace."}
    return {"error": "No skill named '" + name + "' on your account."}


def skill_install(email, name):
    name = (name or "").strip().lower()
    src = None
    for owner, lst in (_skills_all() or {}).items():
        for s in lst or []:
            if s.get("shared") and s.get("name", "").lower() == name:
                src = s
                break
        if src:
            break
    if not src:
        return {"error": "No shared skill with that name on the marketplace."}
    return skill_save(email, src.get("name"), src.get("trigger"),
                      src.get("instructions") + "\n\n[Installed from OraCool skill marketplace — verify it does what you want before relying on it.]")


# ---------------------------------------------------------------- encrypted vault

def _vault_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "vault.json")

def load_vault():
    try:
        with open(_vault_file()) as f:
            return json.load(f)
    except Exception:
        return {}

def vault_set(email, kname, value):
    if not email or not kname:
        return {"error": "email and key are required."}
    v = load_vault()
    v.setdefault(email.lower(), {})
    v[email.lower()][kname] = crypto.seal(str(value), key("ENCRYPTION_KEY"), key("ENCRYPTION_IV"))
    try:
        with open(_vault_file(), "w") as f:
            json.dump(v, f)
    except Exception as e:
        return {"error": f"Vault write failed: {e}"}
    return {"status": "ok", "stored": kname, "note": "Stored AES-256 encrypted at rest."}

def vault_get(email, kname):
    v = load_vault()
    rec = v.get((email or "").lower(), {})
    if kname not in rec:
        return {"error": f"No vault entry '{kname}' for this account."}
    try:
        plain = crypto.open_seal(rec[kname], key("ENCRYPTION_KEY"), key("ENCRYPTION_IV"))
        return {"key": kname, "value": plain}
    except Exception as e:
        return {"error": f"Vault decrypt failed: {e}"}

def vault_list(email):
    v = load_vault()
    return {"keys": sorted((v.get((email or "").lower(), {}) or {}).keys())}

# ---------------------------------------------------------------- agentic tools
# The AI can run these tools BY ITSELF (auto-tool execution in chat).

CRYPTO_KEYWORDS = {
    "bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana", "binance": "binancecoin", "bnb": "binancecoin",
    "ripple": "ripple", "xrp": "ripple", "cardano": "cardano", "ada": "cardano",
    "dogecoin": "dogecoin", "doge": "dogecoin", "litecoin": "litecoin", "ltc": "litecoin",
    "polkadot": "polkadot", "dot": "polkadot", "avalanche": "avalanche-2", "avax": "avalanche-2",
    "tron": "tron", "trx": "tron", "shiba": "shiba-inu", "shib": "shiba-inu",
    "chainlink": "chainlink", "link": "chainlink", "uniswap": "uniswap", "uni": "uniswap",
    "cosmos": "cosmos", "atom": "cosmos", "near": "near", "polygon": "matic-network",
    "matic": "matic-network",
}
STOCK_KEYWORDS = {
    "apple": "AAPL", "tesla": "TSLA", "nvidia": "NVDA", "microsoft": "MSFT",
    "amazon": "AMZN", "google": "GOOGL", "alphabet": "GOOGL", "meta": "META",
    "facebook": "META", "netflix": "NFLX", "amd": "AMD", "intel": "INTC",
    "jpmorgan": "JPM", "visa": "V", "disney": "DIS", "coca": "KO", "pfizer": "PFE",
    "walmart": "WMT", "exxon": "XOM", "johnson": "JNJ", "boeing": "BA", "nike": "NKE",
    "spy": "SPY", "qqq": "QQQ",
    # Nigerian Exchange (NGX) — quoted honestly: the live feed does not cover NGX
    "dangote cement": "DANGCEM", "dangote": "DANGCEM", "dangcem": "DANGCEM",
    "gtco": "GTCO", "guaranty": "GTCO", "guaranty trust": "GTCO", "zenith": "ZENITHBANK",
    "zenith bank": "ZENITHBANK", "access bank": "ACCESSCORP", "accesscorp": "ACCESSCORP",
    "mtn nigeria": "MTNN", "mtnn": "MTNN", "airtel africa": "AIRTELAFRI", "airtelafri": "AIRTELAFRI",
    "nestle nigeria": "NESTLE", "seplat": "SEPLAT", "bua cement": "BUACEMENT",
    "bua foods": "BUAFOODS", "sterling bank": "STERLINGNG", "uba": "UBA", "united bank for africa": "UBA",
    "first bank": "FBNH", "fbn holdings": "FBNH", "ecobank": "ETI", "flour mills": "FLOURMILL",
    "transcorp": "TRANSCORP", "okomu": "OKOMUOIL", "presco": "PRESCO", "total energies": "TOTAL",
    "conoil": "CONOIL", "oco": "OANDO", "oando": "OANDO", "pz cussons": "PZ",
}
FRED_KEYWORDS = {
    "inflation": "CPIAUCSL", "cpi": "CPIAUCSL", "unemployment": "UNRATE", "gdp": "GDP",
    "fed funds": "FEDFUNDS", "interest rate": "FEDFUNDS", "federal funds": "FEDFUNDS",
    "treasury yield": "DGS10", "10-year": "DGS10",
}

def _crypto_coin(text):
    low = text.lower()
    for kw, cid in sorted(CRYPTO_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if re.search(r"\b" + re.escape(kw) + r"\b", low):
            if any(k in low for k in ("price", "worth", "cost", "how much", "value", "trade",
                                      "buy", "sell", "signal", "analysis", "analyze", "rate",
                                      "now", "today", "what is", "what's", "how is")):
                return cid
    return None

def _stock_symbol(text):
    m = re.search(r"\$\s*([A-Za-z]{1,5})", text)
    if m:
        return m.group(1).upper()
    low = text.lower()
    if not any(k in low for k in ("stock", "share", "ticker", "quote", "price", "$",
                                  "market", "exchange", "ngx", "nse", "equity", "listed")):
        return None
    # explicit ticker requests: "ticker DANGCEM", "market lookup ticker DANGCEM NGX",
    # "quote GTCO", "DANGCEM NGX price"
    _not_syms = {"NGX", "NSE", "LSE", "NYSE", "NASDAQ", "JSE", "USD", "NGN", "EUR", "GBP",
                 "CEO", "AI", "API", "USA", "UK", "THE", "AND", "FOR"}
    for pat in (r"\b(?:ticker|symbol|quote|lookup|look up|price of|price for)\s*:?\s*([A-Z]{2,12})\b",
                r"\b([A-Z]{2,12})\s+(?:NGX|NYSE|NASDAQ|LSE|JSE)\b",
                r"\b(?:ngx|nse|lse|nyse|nasdaq)\s*:?\s*([A-Z]{2,12})\b",
                r"\b([A-Z]{2,5})\s+(?:stock|share|shares|price|quote|ticker)\b"):
        m2 = re.search(pat, text)
        if m2 and m2.group(1).upper() not in _not_syms:
            return m2.group(1).upper()
    for kw, sym in sorted(STOCK_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if re.search(r"\b" + re.escape(kw) + r"\b", low):
            return sym
    return None

def _weather_city(text):
    low = text.lower()
    for pat in (r"weather\s+(?:in|for|at)\s+([a-z][a-z ,\'-]{2,40})",
                r"(?:what.?s|hows?|how is)\s+the\s+weather\s+(?:in|for|at)\s+([a-z][a-z ,\'-]{2,40})",
                r"temperature\s+(?:in|for|at)\s+([a-z][a-z ,\'-]{2,40})",
                r"(?:rain|forecast)\s+(?:in|for|at)\s+([a-z][a-z ,\'-]{2,40})"):
        m = re.search(pat, low)
        if m:
            c = m.group(1).strip(" ,.?!")
            c = re.sub(r"\s+(right now|please|today|now|currently|this week|at the moment)\s*$", "", c, flags=re.I)
            if c:
                return c
    return None

def _fred_series(low):
    for kw, sid in FRED_KEYWORDS.items():
        if kw in low:
            return sid
    return None

def _shrink(obj, limit=1200):
    try:
        txt = json.dumps(obj, default=str)
    except Exception:
        txt = str(obj)
    return txt[:limit]

# patch40: "called Sweet Crumbs with a menu and order form" → name is "Sweet Crumbs" (stop at joiner words / punctuation)
_NAME_RX = re.compile(r"(?:called|named|titled)\s+[\"\u201c\u2018']?([A-Za-z0-9&.'\u2019-]+(?:\s+[A-Za-z0-9&.'\u2019-]+){0,4}?)[\"\u201d\u2019']?"
                      r"(?=\s+(?:with|that|which|for|and|having|featuring|including|in|on|at|to|where|who|so|but|plus|using|\u2014|-)\b|\s*[,.;:!?\"\u201d)]|\s*$)", re.I)


_SITE_NOUN_RX = re.compile(r"\b(?:web ?site|web ?app|webapp|web ?page|webpage|homepage|landing ?page|online store|"
                           r"e-?commerce|portfolio|dashboard|blog|shop|store|app|application|saas|platform|site for)\b")
_MEDIA_NOUN_RX = re.compile(r"\b(?:images?|pictures?|photos?|art|artwork|logos?|posters?|banners?|icons?|graphics?|"
                            r"illustrations?|drawings?|renders?|avatars?|wallpapers?|mockups?|flyers?|videos?|clips?|animations?)\b")
_QUESTION_RX = re.compile(r"^\s*(?:(?:please|pls|hey|hi|hello|yo|ok|okay|so|now|also|and|just)[\s,]+)*"
                          r"(?:how|what|why|when|who|which|can|could|should|would|tell|explain|difference)\b")


_EXPERT_MODE = ("EXPERT MODE is on for this reply: reason carefully before answering, check your facts and arithmetic, "
                "cover the important angles and edge cases, structure the answer with clear headings or steps, and give "
                "concrete numbers, examples and next actions. Stay direct — depth, not padding.")


def _looks_site_build(text):
    """patch43: True when the message will run the site builder or the in-place editor — the chat handler
    announces `__progress` on the event-stream so the client shows the live build feed for ANY phrasing
    (voice, 'make me an app for…', edits) instead of guessing with its own regex."""
    low = (text or "").lower()
    if len(low) < 8 or _QUESTION_RX.match(low):
        return False
    if re.search(r"\b(?:hero|footer|headline|heading|button|colou?rs?|background|fonts?|prices?|pricing|booking|contact|whatsapp|section)\b", low) \
            and re.search(r"(?:my|the|our|current|existing)\s+(?:\w+\s+){0,3}?(?:site|website|page|landing)", low):
        return True  # in-place edit
    if not re.search(r"\b(?:build|create|make|design|generate|code|develop|launch|rebuild|redesign|set ?up)\b", low):
        return False
    return bool(_SITE_NOUN_RX.search(low)) and not _MEDIA_NOUN_RX.search(low)


def _looks_slow_tool(text):
    """patch40: messages that fire a long-running tool (site builder, image/video generation) — the chat
    handler opens the event-stream first and pings while they run so browsers never hit a connect timeout."""
    low = (text or "").lower()
    if len(low) < 8:
        return False
    if re.match(r"^\s*(?:(?:please|pls|hey|hi|hello|yo|ok|okay|so|now|also|and|just)[\s,]+)*"
                r"(?:how|what|why|when|who|which|can|could|should|would|tell|explain|difference)\b", low):
        return False
    verb = re.search(r"\b(?:build|create|make|design|generate|code|develop|launch|rebuild|redesign|draw|imagine|render|animate|produce)\b", low)
    if not verb:
        return False
    return bool(re.search(r"\b(?:web ?site|web ?app|webapp|web ?page|webpage|homepage|landing ?page|online store|"
                          r"e-?commerce|portfolio|dashboard|blog|shop|store|images?|pictures?|photos?|art|artwork|logos?|"
                          r"posters?|banners?|wallpapers?|illustrations?|videos?|clips?|animations?)\b", low))


_MEDIA_KIND_RX = re.compile(r"\b(video|clip|animation|film|movie|reel|footage|image|picture|photo|art|artwork|logo|wallpaper|poster|banner|illustration|drawing)s?\b", re.I)
_BARE_MEDIA_RX = re.compile(r"^\s*(?:(?:please|pls|hey|ok|okay|now|just|can you|could you|i want you to|i need you to)[\s,]+)*"
                            r"(?:generate|create|make|produce|render|draw|design)\s+(?:me\s+)?(?:a|an|the|one|some)?\s*"
                            r"(?:(?:short|quick|small|nice|cool|cinematic|beautiful|realistic|animated|ai(?:-generated)?)\s+){0,3}"
                            r"(video|clip|animation|film|movie|reel|image|picture|photo|artwork|art)s?"
                            r"(?:\s+(?:for me|please|now|pls))*\s*[.!?]*\s*$", re.I)


def _pending_media_brief(history, current):
    """patch41: conversation-aware media requests. "generate a video" → OraCool asks what it should show →
    the user's next message ("a lion in the savannah") is the SUBJECT, not a new command. Returns
    (kind, subject, style_words) or None. Only fires when the current message carries no other intent."""
    cur = (current or "").strip()
    if not history or not cur or len(cur) > 220 or cur.endswith("?"):
        return None
    if re.search(r"\b(?:generate|create|make|draw|build|open|weather|price|search|find|send|call|play|remind|translate)\b", cur, re.I):
        return None
    turns = [m for m in history if isinstance(m, dict) and m.get("role") in ("user", "assistant")][-8:]
    # the current message is normally the last user turn in history — drop it
    if turns and turns[-1].get("role") == "user" and (turns[-1].get("content") or "").strip() == cur:
        turns = turns[:-1]
    kind = None; picks = []; asked = False
    for i in range(len(turns) - 1, -1, -1):
        m = turns[i]; c = (m.get("content") or "").strip()
        if m.get("role") == "assistant":
            if "?" in c and _MEDIA_KIND_RX.search(c):
                asked = True
            continue
        bm = _BARE_MEDIA_RX.match(c)
        if bm:
            kind = "video" if bm.group(1).lower().rstrip("s") in ("video", "clip", "animation", "film", "movie", "reel") else "image"
            break
        if len(c) <= 60 and not c.endswith("?"):
            picks.insert(0, c)   # e.g. "Cinematic clip", "Video with voice narration"
            continue
        return None              # an unrelated longer message in between → no pending brief
    if not kind or not asked:
        return None
    style = " ".join(p for p in picks if not re.search(r"\bother\b", p, re.I))[:120]
    return kind, cur.rstrip(".!, "), style


def auto_tools(text, tier="free", ha_url=None, ha_token=None, email=None, crypto_site="", history=None, force_build=False):
    """Detect intent in the user's message and RUN the matching live tool(s)."""
    t = (text or "").strip()
    if not t:
        return []
    low = t.lower()
    out = []
    # patch27: site intent computed FIRST and BROAD — any build/create/design +
    # website-ish phrase must run the builder only (never images). Modifier words
    # (for/of/with/…) end the object, so "design a logo for my website" stays an
    # IMAGE request, while "create a stunning modern landing page" is a BUILD.
    _mb = re.search(r"\b(?:build|create|make|design|generate|code|develop)\s+(?:me\s+)?(?:an?\s+|the\s+|my\s+)?"
                    r"(?:(?!(?:for|of|with|that|which|about|using|out|into)\b)[\w\x27-]+\s+){0,4}?"
                    r"(?:website|web\s*site|web\s*app|webapp|web\s*page|webpage|homepage|landing\s*page|"
                    r"site|app|apps|portfolio|online\s*store|e-?commerce|shop|store|blog|dashboard)\b", low)
    _site_ish = (bool(re.search(r"\b(?:web ?site|web ?app|webapp|web ?page|webpage|homepage|landing page|"
                                r"online store|e-?commerce|portfolio|dashboard)\b", low))
                 and bool(re.search(r"\b(?:build|create|make|design|generate|code|develop|launch|rebuild|redesign)\b", low))
                 and not re.search(r"\b(?:images?|pictures?|photos?|art|artwork|logos?|posters?|banners?|icons?|"
                                    r"graphics?|illustrations?|drawings?|renders?|avatars?|wallpapers?|mockups?|"
                                    r"flyers?|videos?|clips?|animations?)\b", low))
    # questions about building ("how do I make money with e-commerce") are NOT build commands
    _ask = bool(re.match(r"^\s*(?:(?:please|pls|hey|hi|hello|yo|ok|okay|so|now|also|and|just)[\s,]+)*"
                          r"(?:how|what|why|when|who|which|can|could|should|would|tell|explain|difference)\b", low))
    _wants = (_mb or _site_ish) and not _ask
    if force_build and len(t) > 8:
        _wants = True  # patch43: the composer's Build mode — the message IS the brief, whatever the phrasing
    # patch30: "make my site's footer say 24/7" is an EDIT even though "make…site"
    # looks like a build verb — part words + "my/the site" reference mean in-place change
    _eparts = (_wants or _site_ish) and bool(re.search(
        r"\b(?:hero|footer|headline|heading|button|colou?rs?|background|fonts?|prices?|booking|contact|whatsapp)\b", low)) \
        and bool(re.search(r"(?:my|the|our|current|existing)\s+(?:\w+\s+){0,3}?(?:site|website|page|landing)", low)) \
        and not re.search(r"\b(?:new|fresh|another|rebuild|redesign|from scratch)\b", low) \
        and not re.search(r"\b(?:build|create|generate|code|develop)\b", low)
    # a HOW-TO question with no explicit media noun is conversation, not a render job
    _no_media_ask = _ask and not re.search(r"\b(?:images?|pictures?|photos?|art|artwork|logo|poster|drawing|"
                                           r"wallpaper|render|avatar|mockup|videos?|clips?)\b", low)
    # patch41: "generate a video" must never be treated as an IMAGE of "a video"
    _video_words = bool(re.search(r"\b(?:videos?|clips?|animations?|films?|movies?|reels?|footage)\b", low))
    _image_words = bool(re.search(r"\b(?:images?|pictures?|photos?|art|artwork|logos?|wallpapers?|posters?|banners?|illustrations?|drawings?)\b", low))
    # community chat access (every tier — the AI reads THIS user's own community)
    if any(k in low for k in ("community", "my chats", "my chat", "my messages", "my message",
                              "my dms", "my dm", "direct messages", "check my chat", "check my community",
                              "any new messages", "any messages", "who messaged", "who wrote",
                              "unread messages", "group chat", "channel message", "lounge")) \
            and any(k in low for k in ("check", "read", "see", "show", "any", "who", "what",
                                       "new", "message", "chat", "unread", "community")):
        if email:
            try:
                cb = community_service().chat_brief(email)
                out.append({"tool": "community", "label": "community chat",
                            "result": _shrink(cb, 2600)})
            except Exception as e:
                out.append({"tool": "community", "label": "community chat",
                            "result": "community unavailable: " + str(e)[:160]})
    # camera capture (every tier — the client fires the capture card)
    if re.search(r"\b(?:take|snap|capture|click|shoot|get)\s+(?:me\s+|my\s+(?:face\s+)?|a\s+)?(?:picture|photo|selfie)\b", low) \
            or re.search(r"\b(?:picture|photo|selfie)\s+(?:of\s+)?(?:me|my face)\b", low):
        out.append({"tool": "camera", "label": "camera capture",
                    "result": {"ok": True,
                               "note": "The app is showing a tappable '📷 Take photo' capture card in the chat. Tell the user to tap it and allow the camera permission — the photo then appears in the chat (view / download / attach). Never claim the photo was taken without their tap."}})
    # app launching (every tier — the client fires the intent)
    mo = re.match(r"^\s*(?:please\s+)?(?:open|launch|start|fire up|boot)\s+(?:the\s+|my\s+)?([a-z0-9 .+\-]{2,30}?)\s*(?:app|application)?\s*(?:for me\s*)?[.!]?\s*$", low)
    if mo and "image" not in low and "video" not in low and "file" not in low.split()[-1:]:
        cand = open_app(mo.group(1))
        if cand.get("ok") and len(low.split()) <= 6:
            out.append({"tool": "openapp", "label": "open " + cand["app"],
                        "result": _shrink(cand, 600)})
            return out[:4]
    # weather
    wc = _weather_city(t)
    if wc:
        out.append({"tool": "weather", "label": "weather · " + wc,
                    "result": _shrink(weather(wc))})
    # crypto
    cid = _crypto_coin(t)
    if cid:
        out.append({"tool": "crypto", "label": cid + " price",
                    "result": _shrink(market_crypto(cid))})
    # stock
    sym = _stock_symbol(t)
    if sym:
        out.append({"tool": "stock", "label": sym + " stock",
                    "result": _shrink(market_stock(sym))})
    # FRED economic
    fs = _fred_series(low)
    if fs:
        out.append({"tool": "fred", "label": fs, "result": _shrink(market_fred(fs))})
    # trading signal
    if any(k in low for k in ("trading signal", "signal for", "trade idea", "should i buy",
                              "should i sell", "buy or sell")):
        s2 = sym or (cid and cid.upper()) or "BTC"
        out.append({"tool": "signal", "label": s2 + " signal", "result": _shrink(trade_signal(s2))})
    # IP
    ipm = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", t)
    # patch38: every OSINT lookup (IP · domain · email · phone · username · dark-web) is Starter+
    _osint_ok = tier_gte(tier, "starter")
    if ipm and _osint_ok:
        out.append({"tool": "ip", "label": "IP " + ipm.group(1), "result": _shrink(osint_ip(ipm.group(1)))})
    # domain
    if not ipm:
        dm = re.search(r"\b([a-z0-9-]+\.(?:com|net|org|io|co|ng|dev|ai|me|xyz|app|info|biz))\b", low)
        if dm and _osint_ok and any(k in low for k in ("whois", "domain", "dns", "lookup", "website", "site", "check")):
            out.append({"tool": "domain", "label": "domain " + dm.group(1),
                        "result": _shrink(osint_domain(dm.group(1)))})
    # email breach
    em = re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", low)
    if em and _osint_ok and any(k in low for k in ("breach", "hack", "pwned", "leak", "email", "investigate", "check")):
        out.append({"tool": "email", "label": "breach · " + em.group(0),
                    "result": _shrink(osint_email(em.group(0), None))})
    # phone
    ph = re.search(r"\+?\d[\d\s\-()]{8,}", t)
    if ph and _osint_ok and any(k in low for k in ("phone", "number", "trace", "carrier", "who called", "caller")):
        out.append({"tool": "phone", "label": "phone " + ph.group(0),
                    "result": _shrink(phone_intel.lookup(ph.group(0), key_lookup=key, http_fetch=http_fetch))})
    # username OSINT (free) — @handle or "username X"
    if not em:
        um = re.search(r"@([a-z0-9_\.]{2,30})", low)
        if not um:
            um = re.search(r"(?:username|handle|profile)\s+(?:of|for)?\s*([a-z0-9_\.]{2,30})", low)
        if um and _osint_ok and any(k in low for k in ("username", "handle", "profile", "@", "lookup", "who is")):
            out.append({"tool": "username", "label": "username " + um.group(1),
                        "result": _shrink(osint_username(um.group(1)))})
    # dark-web intelligence — Starter+ since patch38 (passive public indexes only:
    # breach databases + OnionLand/Ahmia hidden-service directories. .onion sites
    # are NEVER opened; no Tor, no downloads, no transactions.)
    if _osint_ok and any(k in low for k in ("dark web", "dark-web", "darkweb", "onion", "tor site",
                                            "paste site", "criminal forum", "leaked on")):
        term = re.sub(r"(?i)\b(?:dark\s?-?\s?web|darkweb|onion|tor site|search|check|look up|look for|for|about|on|the|a|an)\b",
                      " ", t)
        term = " ".join(term.split())[:60] or "marketplace"
        out.append({"tool": "darkweb", "label": "dark-web · " + term,
                    "result": _shrink(osint_darkweb(term), 1800)})
    # patch40: image collection — "find/collect/get me 6 images of X"
    _imq = re.search(r"\b(?:find|search|collect|get|show|fetch|gather|source)\s+(?:me\s+)?(?:some\s+|a few\s+|(\d{1,2})\s+)?(?:stock\s+|real\s+|licensed\s+|free\s+)?(?:images?|photos?|pictures?|pics)\s+(?:of|for|about|showing)\s+(.{3,80})", low)
    if _imq and not any(k in low for k in ("generate", "draw", "imagine", "create an image", "make an image")):
        _imn = int(_imq.group(1) or 6)
        _imt = re.sub(r"[.!?].*$", "", _imq.group(2)).strip()
        out.append({"tool": "images", "label": "photos · " + _imt[:40],
                    "result": _shrink({"query": _imt, "images": image_search(_imt, _imn),
                                       "note": "Licensed photos. Show them to the user as markdown images (![title](url)) with a one-line credit (author · license · source) under each; offer to use them in a website build."}, 2600)})
    # space (free)
    if any(k in low for k in ("picture of the day", "apod", "space picture", "nasa picture", "picture today")):
        out.append({"tool": "space", "label": "NASA picture of the day", "result": _shrink(space_apod())})
    if any(k in low for k in ("asteroid", "neo", "near earth")):
        out.append({"tool": "space", "label": "near-earth objects", "result": _shrink(space_neo())})
    if any(k in low for k in ("epic", "dscovr", "picture of earth", "earth photo")):
        out.append({"tool": "space", "label": "NASA Earth (DSCOVR EPIC)", "result": _shrink(space_epic())})
    # current time
    if re.search(r"\bwhat time|current time|time now|the time\b", low):
        out.append({"tool": "time", "label": "current time", "ok": True,
                    "result": json.dumps({"utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                                          "local_server": time.strftime("%Y-%m-%d %H:%M:%S")})})
    # simple math
    mm = re.search(r"(?:calculate|compute|what is|what's|solve)\s+(.+?)[?.!]*$", low)
    if mm and not re.search(r"\b(weather|price|stock|who|where|time)\b", low):
        expr = (mm.group(1).replace("times", "*").replace("divided by", "/")
                .replace("plus", "+").replace("minus", "-").replace("mod ", "% "))
        expr = re.sub(r"[^0-9+\-*/().%^ ]", " ", expr).strip()
        if re.search(r"\d", expr) and re.fullmatch(r"[0-9+\-*/().%^ ]+", expr):
            try:
                val = eval(expr.replace("^", "**"), {"__builtins__": {}}, {})
                out.append({"tool": "math", "label": expr, "result": json.dumps({"expression": expr, "value": val})})
            except Exception:
                pass
    # Smart home (free when Home Assistant is linked — the AI drives devices itself)
    sm = _smart_intent(t, ha_url, ha_token)
    if sm:
        r = smart_control(sm["entity_id"], sm["action"], ha_url, ha_token)
        out.append({"tool": "smart", "label": "smart · " + sm.get("name", sm["entity_id"]),
                    "result": _shrink(r)})

    # Starter-tier tools (web search)
    if tier_gte(tier, "starter"):
        if any(k in low for k in ("search for", "search the web", "web search", "google ")):
            out.append({"tool": "search", "label": "web search",
                        "result": _shrink(pro_search(t, 3), 1500)})

    # Pro-tier tools (deep OSINT + image creation)
    if tier_gte(tier, "pro"):
        if "shodan" in low and ipm:
            out.append({"tool": "shodan", "label": "shodan " + ipm.group(1),
                        "result": _shrink(pro_shodan(ipm.group(1)), 1500)})
        if "virustotal" in low:
            target = ipm.group(1) if ipm else (em.group(0) if em else t.split()[-1])
            out.append({"tool": "virustotal", "label": "virustotal",
                        "result": _shrink(pro_virustotal(target, "domain"), 1500)})
        if "abuseipdb" in low and ipm:
            out.append({"tool": "abuseipdb", "label": "abuseipdb " + ipm.group(1),
                        "result": _shrink(pro_abuseipdb(ipm.group(1)), 1500)})
        if "leakcheck" in low and (em or ph):
            q = em.group(0) if em else ph.group(0)
            out.append({"tool": "leakcheck", "label": "leakcheck",
                        "result": _shrink(pro_leakcheck(q, "email" if em else "phone"), 1500)})
        if "urlscan" in low:
            target = dm.group(1) if dm else (ipm.group(1) if ipm else t.split()[-1].strip(".,!? "))
            out.append({"tool": "urlscan", "label": "urlscan " + target,
                        "result": _shrink(pro_urlscan(target), 1500)})
        # document-verification framework (provider-gated; never a verdict)
        if re.search(r"verif\w*\s+(?:this\s+|that\s+|the\s+|my\s+)?(?:passport|driver'?s?\s+licen[cs]e|licence|nin\b|id\s*card)", low) \
           or "check passport authenticity" in low or "is this card genuine" in low:
            dt = ("passport" if "passport" in low else "nin" if "nin" in low
                  else "driver Licence" if ("driver" in low or "licence" in low or "license" in low)
                  else "id card")
            out.append({"tool": "docverify", "label": "document verification · " + dt,
                        "result": _shrink(verify_document(dt, ""), 1400)})
        # image authenticity / forensic triage of an image URL
        if any(k in low for k in ("image authenticity", "is this image edited", "is this photo fake",
                                  "photo tampered", "image tampered", "forensic check", "metadata of this image",
                                  "exif of")):
            um2 = re.search(r"https?://\S+\.(?:jpe?g|png|webp|gif)(?:\?\S+)?", low)
            if um2:
                out.append({"tool": "mediainspect", "label": "image forensics",
                            "result": _shrink(media_inspect(um2.group(0)), 1400)})
        # NASA Mars rovers + image library (PRO)
        if "mars" in low and ("rover" in low or "mars" in low):
            out.append({"tool": "space", "label": "Mars rover imagery", "result": _shrink(space_mars())})
        if "nasa" in low and any(k in low for k in ("image", "photo", "picture", "find")):
            q = re.sub(r"(?i)\b(?:nasa|image|images|photo|photos|picture|pictures|find|of|the|for|a|an|show|me)\b", " ", t)
            q = " ".join(q.split())[:50] or "earth"
            out.append({"tool": "space", "label": "NASA library · " + q, "result": _shrink(space_library(q))})
        # image creation straight from chat
        im = re.search(r"(?:generate|create|make|draw|imagine|design|show me)\s+(?:an?\s+)?(?:image|picture|photo|art|logo|wallpaper)?\s*(?:of|for)?\s*(.{6,200})", low)
        if im and any(k in low for k in ("generate", "create", "make", "draw", "imagine", "design")) \
                and not (_wants and len(t) > 8) and not _no_media_ask and not (_video_words and not _image_words) \
                and not _BARE_MEDIA_RX.match(t):
            prompt = im.group(1).strip().rstrip("?!., ")
            if prompt:
                r = gen_image(prompt)
                out.append({"tool": "image", "label": "image · " + prompt[:40],
                            "result": _shrink(r, 1200)})

    # Arena-style app builder — coin-metered (patch27), takes over the message
    if _wants and len(t) > 8 and not _eparts:
        mb = _mb
        _bn = ""
        _mn = _NAME_RX.search(t)
        if _mn:
            _bn = _mn.group(1).strip(" .")
        elif mb:
            _after = t[mb.end():].strip().lstrip(" ,").strip()
            if _after and len(_after) < 60 and not re.search(r"\b(with|that|which|using|about|for|on|by|and)\b", _after):
                _bn = _after
        if not _bn and force_build:
            # patch43: Build mode briefs often open with the brand — "Legend Fintech — instant transfers…"
            _lead = re.match(r"^\s*((?:[A-Z][A-Za-z0-9&'-]*\s?){1,4}?)\s*(?:[—–:|,\-]|\n|\bis\b|\ba\b|\ban\b)", t)
            if _lead and _lead.group(1).strip() not in ("OraCool", "Lagos", "Nigeria", "Build", "Create", "Make", "Design", "Please", "I", "We", "A", "An", "The"):
                _bn = _lead.group(1).strip()
        if not _bn:
            # patch29: "build a website for Becfom Hotel" -> use the visible proper-noun run so a
            # real client site is never buried under the generic "my-site" name
            _pn = re.search(r"(?:for|of)\s+((?:[A-Z][A-Za-z0-9&'-]+\s?){1,4})(?=[,.\s]|$)", t)
            if not _pn:
                _pn = re.search(r"([A-Z][a-z0-9&'-]+(?:\s+[A-Z][a-z0-9&'-]+){1,3})(?=\s*(?:[,.!?]|\s+(?:that|which|is|to|now)\b|$))", t)
            if _pn:
                _cand = _pn.group(1).strip().rstrip(".,;:")
                if _cand not in ("OraCool", "Lagos", "Nigeria") and len(_cand) > 3:
                    _bn = _cand
        if not _bn:
            # patch45: no brand in the brief ("create a website for a fashion designer") -> a descriptive name
            # instead of the anonymous "my-site", so the feed reads "Building Fashion Designer"
            _dn = re.search(r"\b(?:for|about)\s+(?:a|an|the|my|our|his|her|their)?\s*([a-z][a-z0-9'&-]*(?:\s+[a-z][a-z0-9'&-]*){0,3}?)"
                            r"(?=\s*(?:$|[,.;:!?]|\s+(?:in|at|based|with|that|which|who|and|selling|offering|called|named|using|from|to|on|near)\b))", low)
            if _dn:
                _cand = re.sub(r"\b(?:website|web ?site|site|web ?app|app|landing page|page|business|company|brand|store|shop|online)\b", "", _dn.group(1)).strip(" -'&")
                _cand = re.sub(r"\s{2,}", " ", _cand)
                if 2 < len(_cand) <= 40 and not re.match(r"^(?:me|us|you|him|her|them|it|this|that|myself)$", _cand):
                    _bn = " ".join(w.capitalize() for w in _cand.split())
        _br = (t[:mb.end()] + t[mb.end():][:400]) if mb else t[:400]
        out.append({"tool": "build", "label": "build · " + (_bn or "website")[:30],
                    "result": _shrink(build_site(email, _bn or "my-site", _br), 1500)})
    # patch30: in-place EDIT of an existing build ("change the hero colour to gold",
    # "update my prices section"). Only when no fresh-build verb matched, so an
    # edit never accidentally builds a whole second site.
    _edited = False
    if email and (not _wants or _eparts) \
            and (re.search(r"\b(?:edit|change|update|modify|tweak|adjust|fix|restyle|improve|revamp|make)\b", low) or _eparts) \
            and re.search(r"\b(?:site|website|webpage|page|design|hero|colou?rs?|background|text|button|fonts?|prices?|section|header|footer|layout|booking|contact)\b", low) \
            and "github" not in low:
        try:
            _bm2 = _builds_load()
            _mine2 = [s for s, v in _bm2.items() if (v.get("owner") or "").lower() == (email or "").lower()]
            if _mine2:
                _em = re.search(r"(?:edit|update|change|fix|tweak)\s+(?:my\s+)?(?:site\s+|build\s+|page\s+)?([a-z0-9][a-z0-9-]{2,39})", low)
                if _em and _em.group(1) in _mine2:
                    _es = _em.group(1)
                else:
                    _es = None
                    _ewords = [w for w in re.findall(r"[a-z]{4,}", low)
                               if w not in ("edit", "change", "update", "modify", "tweak", "adjust",
                                            "please", "site", "website", "page", "build", "make",
                                            "color", "colour", "background", "text", "button", "design")]
                    for s in sorted(_mine2, key=lambda s: (_bm2[s].get("t") or ""), reverse=True):
                        _nm2 = (str(_bm2[s].get("name") or "") + " " + s).lower()
                        if any(w in _nm2 for w in _ewords):
                            _es = s
                            break
                    if not _es:
                        _es = sorted(_mine2, key=lambda s: (_bm2[s].get("t") or ""), reverse=True)[0]
                out.append({"tool": "edit", "label": "edit · " + _es[:30],
                            "result": _shrink(build_edit(email, _es, t), 900)})
                _edited = True
        except Exception:
            pass
    # publish a built site to oracoolai.com (free, instant — patch27/30)
    if email and not _wants and not _edited and re.search(r"\b(?:publish|go live|goes live|make it live|put it online|host it)\b", low) \
            and "github" not in low:
        try:
            _bm = _builds_load()
            _mine = [s for s, v in _bm.items() if (v.get("owner") or "").lower() == (email or "").lower()]
            if _mine:
                _sm = re.search(r"publish\s+(?:my\s+)?(?:site\s+|build\s+)?([a-z0-9][a-z0-9-]{2,39})", low)
                if _sm and _sm.group(1) in _mine:
                    _sl = _sm.group(1)
                else:
                    # patch29: "publish the becfom site" must find the BECFOM build, not whatever
                    # was built last (generic builds caused wrong publishes once)
                    _sl = None
                    _words = [w for w in re.findall(r"[a-z]{4,}", low)
                              if w not in ("publish", "website", "online", "make", "site", "build",
                                            "please", "just", "then", "host", "live", "goes")]
                    for s in sorted(_mine, key=lambda s: (_bm[s].get("t") or ""), reverse=True):
                        _nm = (str(_bm[s].get("name") or "") + " " + s).lower()
                        if any(w in _nm for w in _words):
                            _sl = s
                            break
                    if not _sl:
                        _sl = sorted(_mine, key=lambda s: (_bm[s].get("t") or ""), reverse=True)[0]
                _dm = re.search(r"\b(?:at|as|on)\s+([a-z0-9][a-z0-9-]{2,38})\b", low)
                _sb = _dm.group(1) if _dm else ""
                out.append({"tool": "publish", "label": "publish · " + _sl[:30],
                            "result": _shrink(site_publish(email, _sl, _sb), 900)})
        except Exception:
            pass
    # GitHub: connect / repos / push a built site to the user's own account
    if email and re.search(r"\bgithub\b", low):
        if re.search(r"\b(connect|link|add)\b", low) and re.search(r"token|pat|account", low):
            out.append({"tool": "github", "label": "connect github",
                        "result": {"ok": True,
                                   "note": "Tap the GitHub card in chat (or Devices → Connectors → GitHub) and paste your personal access token. Create one at github.com → Settings → Developer settings → Personal access tokens → Generate (needs repo scope)."}})
        elif re.search(r"\brepos?\b", low) and not re.search(r"create|new", low):
            out.append({"tool": "github_repos", "label": "github repos", "result": _shrink(github_repos(email), 1500)})
        elif re.search(r"\b(push|deploy|publish)\b", low):
            _meta = _builds_load()
            if email and email in [v.get("owner") for v in _meta.values()]:
                _slugs = [s for s, v in _meta.items() if v.get("owner") == email][-1:]
                if _slugs:
                    _msl = re.search(r"\bpush\s+(?:the\s+)?(?:build|site)\s+\"?([a-z0-9-]{2,48})\"?\s+to\s+github", low)
                    _slug = _msl.group(1) if _msl and _msl.group(1) in _meta else _slugs[0]
                    _mrepo = re.search(r"(?:to|in|into)\s+(?:the\s+)?(?:repo|repository)?\s*\"?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\"?", low)
                    _repo = _mrepo.group(1) if _mrepo else ""
                    if _repo:
                        out.append({"tool": "github_push", "label": "push → " + _repo,
                                    "result": _shrink(github_push_build(email, _slug, _repo), 900)})
                    else:
                        out.append({"tool": "github_push", "label": "push " + _slug,
                                    "result": {"ok": True,
                                               "note": "Which repo should I push '" + _slug + "' to? Say the name (I can create a new private repo for it) — e.g. push the build to myrepo."}})
            else:
                out.append({"tool": "github_push", "label": "push build",
                            "result": {"error": "You have no builds on this server yet — ask me to build a site first, then I can push it to your GitHub."}})
    # Gemini engine status (admin) — honest live report of what the Google key can do
    if email and is_admin(email) and re.search(r"\b(gemini|google (api|key|model))\b", low) and \
            any(k in low for k in ("status", "working", "work", "check", "test", "ok", "good", "connect")):
        out.append({"tool": "gemini_status", "label": "Gemini status",
                    "result": _shrink(gemini_status(), 1200)})
    # OraCool's OWN mailbox — full access for administrators (owner-granted):
    # read everything (incl. body snippets) and send from the address.
    if email and is_admin(email):
        if re.search(r"oracool\s*(?:'s\s+)?(?:own\s+)?(?:inbox|mailbox|email\b|emails|mail\b|messages?)", low) and \
                any(k in low for k in ("check", "read", "see", "show", "any", "what", "new", "latest", "open", "review")):
            cfg = brand_mailbox_cfg()
            if cfg:
                out.append({"tool": "oracool_mail", "label": "OraCool inbox (full access)",
                            "result": _shrink(mail_read_full(cfg), 3000)})
            else:
                out.append({"tool": "oracool_mail", "label": "OraCool inbox",
                            "result": {"error": "The OraCool Gmail is not connected yet. Connect it once in Admin → OraCool-owned accounts (Google app password) and full access starts immediately."}})
        sm = re.search(r"(?:send|write|reply)\s+(?:an?\s+)?(?:email|mail|message)?\s*from\s+(?:the\s+)?oracool\s+(?:mail|inbox|email|account)[\s:]+(.{5,700})", low, re.S)
        if sm and "check" not in low:
            rest = " ".join(sm.group(1).split())
            tm = re.match(r"^(?:to\s+|at\s+)?([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,})[:\s,]+(.+)$", rest, re.S)
            if tm:
                cfg = brand_mailbox_cfg()
                if cfg:
                    out.append({"tool": "oracool_mail_send", "label": "send from OraCool mail → " + tm.group(1)[:30],
                                "result": _shrink(mail_send(cfg, tm.group(1), "From OraCool AI", tm.group(2).strip()[:2000]), 400)})
                else:
                    out.append({"tool": "oracool_mail_send", "label": "send from OraCool mail",
                                "result": {"error": "The OraCool Gmail is not connected yet — connect it in Admin → OraCool-owned accounts first."}})
    # patch41: "generate a video" / "make an image" with no subject → OraCool asks ONE question (with subject options)
    _bare = _BARE_MEDIA_RX.match(t)
    if _bare and not _ask:
        _bk = "video" if _bare.group(1).lower().rstrip("s") in ("video", "clip", "animation", "film", "movie", "reel") else "image"
        _need = ("ultra" if _bk == "video" else "pro")
        out.append({"tool": _bk, "label": _bk + " · what should it show?",
                    "result": {"need_subject": True, "kind": _bk,
                               "instruction": ("No " + _bk + " was generated because no subject was given. Ask ONE short question: what the " + _bk +
                                               " should show. Offer 3-4 CONCRETE SUBJECT ideas (not media types) as an ask block, e.g. "
                                               "{\"app\":\"ask\",\"question\":\"What should the " + _bk + " show?\",\"options\":[\"A Lagos skyline at sunset\","
                                               "\"A product spinning on a table\",\"A lion walking through the savannah\",\"Other - I will describe it\"]}. "
                                               "Their answer becomes the subject and the " + _bk + " is generated automatically." +
                                               ("" if tier_gte(tier, _need) else " NOTE: " + _bk + " creation is on the " + ("Professional" if _bk == "video" else "Pro") +
                                                " plan - mention that in one sentence."))}})
    # patch41: the answer to that question ("a lion in the savannah") carries the pending brief
    _pmb = _pending_media_brief(history, t) if not out else None
    if _pmb:
        _pk, _psub, _pstyle = _pmb
        _pprompt = (_psub + ((" - " + _pstyle) if _pstyle else "")).strip()
        if _pk == "video":
            if tier_gte(tier, "ultra"):
                _want_aud = bool(re.search(r"\b(?:voice|narration|narrated|sound|audio|speech|talking)\b", (_pstyle + " " + _psub), re.I))
                out.append({"tool": "video", "label": "video · " + _psub[:40], "result": _shrink(gen_video(_pprompt, want_audio=_want_aud), 1200)})
            else:
                out.append({"tool": "video", "label": "video · plan", "result": {"locked": True, "plan": "ultra",
                            "note": "Video creation is on the Professional plan - tell the user in one sentence and offer the upgrade."}})
        else:
            if tier_gte(tier, "pro"):
                out.append({"tool": "image", "label": "image · " + _psub[:40], "result": _shrink(gen_image(_pprompt), 1200)})
            else:
                out.append({"tool": "image", "label": "image · plan", "result": {"locked": True, "plan": "pro",
                            "note": "Image creation is on the Pro plan - tell the user in one sentence and offer the upgrade."}})
    # Ultra-tier tools (video creation + GitHub console)
    if tier_gte(tier, "ultra"):
        vm = re.search(r"(?:generate|create|make|produce|render)\s+(?:me\s+)?(?:a\s+|an\s+)?(?:(?:short|quick|cinematic|realistic|animated|ai)\s+){0,2}(?:video|clip|animation|film|movie|reel)\s*(?:of|about|for|showing|where|with)?\s*(.{4,200})", low)
        if vm and any(k in low for k in ("video", "clip", "animation", "film", "movie", "reel")) and not _bare and not _pmb:
            prompt = vm.group(1).strip().rstrip("?!., ")
            want_aud = bool(re.search(r"\b(?:with|having|have)\s+(?:sound|audio|voice|speech|voices|talking)\b", low))
            if want_aud:
                prompt = re.sub(r"\s*\b(?:with|having|have)\s+(?:sound|audio|voice|speech|voices)\b\s*", " ", prompt).strip()
            if prompt:
                r = gen_video(prompt, want_audio=want_aud)
                out.append({"tool": "video", "label": "video · " + prompt[:40],
                            "result": _shrink(r, 1200)})
        if "github" in low:
            q = re.sub(r"(?i)github|search|repos?|for|find|on", " ", t)
            q = " ".join(q.split())[:50] or "oracool"
            try:
                out.append({"tool": "github", "label": "github · " + q,
                            "result": _shrink(github("/search/repositories?q=" + urllib.parse.quote(q) + "&per_page=5"), 1500)})
            except Exception:
                pass

    # Universal generator — "generate a random password / uuid / anything"
    gm2 = re.search(r"generate\s+(?:me\s+)?(?:a\s+|some\s+)?(?:random\s+)?([a-z \-]{3,40})?", low)
    if gm2 and re.search(r"\brandom\b|\bgenerate\b", low) and \
       any(k in low for k in ("password", "uuid", "token", "number", "name", "hash", "phone", "email",
                              "address", "credit card", "iban", "color", "hex", "date", "emoji",
                              "username", "company", "barcode", "dice", "word", "key", "anything")):
        out.append({"tool": "generate", "label": "generate · " + (gm2.group(1) or "random").strip()[:24],
                    "result": _shrink(generate_random(gm2.group(1) or low), 1200)})

    # Crypto payment straight from chat: "pay for pro with crypto"
    if email and "crypto" in low and re.search(r"pay|invoice|upgrade|unlock|subscribe", low):
        pl = "pro"
        for cand in ("starter", "pro", "ultra", "professional", "enterprise"):
            if cand in low:
                pl = cand
                break
        out.append({"tool": "crypto", "label": "crypto · " + pl,
                    "result": _shrink(crypto_invoice(email, pl, crypto_site), 500)})

    # Live news — free, global, sourced (Google News RSS)
    if re.search(r"\b(news|headlines|latest updates|what happened|current events)\b", low):
        q = re.sub(r"(?i)\b(news|headlines|latest|updates|what|happened|current|events|today|give|me|show|any|about|the|a)\b", " ", low)
        q = " ".join(q.split())[:80]
        out.append({"tool": "news", "label": "news · " + (q[:28] or "world"),
                    "result": _shrink(osint_news(q), 2200)})

    # Link tracker from chat: "create a tracking link for https://x called myword"
    lm = re.search(r"(?:create|make)\s+(?:a\s+)?(?:short|tracking)?\s*link\s+(?:for|to)\s+(https?://\S+|[\w.-]+\.\w{2,}\S*)(?:\s+(?:called|named|as)\s+([a-z0-9][a-z0-9.-]{1,30}))?", low)
    if lm and email and "tracker" not in low:
        tgt = lm.group(1)
        if not tgt.lower().startswith("http"):
            tgt = "https://" + tgt
        r = tracker_create(tgt, email, alias=(lm.group(2) or ""))
        out.append({"tool": "link", "label": "track · " + (r.get("slug") or tgt[:20]),
                    "result": _shrink(r, 400)})

    # Custom skills — the user can upgrade their own AI with new features
    if email:
        sm = re.search(r"(?:add|create|teach yourself|upgrade yourself with|make a new)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:skill|feature|command)\s+(?:called|named|to|that)?\s*[:\"']?([^\"'\n:]{2,40})[:\s]*", low)
        if sm:
            nm = sm.group(1).strip().strip(".!?, ")[:40]
            tail = low.split(sm.group(0), 1)[-1].strip(" .:\"'")
            r = skill_save(email, nm, nm, (tail or ("When relevant, execute this skill: " + nm))[:2000])
            out.append({"tool": "skill", "label": "skill · " + nm[:28], "result": _shrink(r, 400)})
        if re.search(r"list my skills|what skills do i have|show my (custom )?features", low):
            out.append({"tool": "skills", "label": "skills",
                        "result": _shrink({"skills": skills_load(email)}, 1400)})
        dm = re.search(r"(?:remove|delete|forget)\s+(?:my\s+)?(?:skill|feature)\s+([^?\n.]{2,50})", low)
        if dm:
            out.append({"tool": "skill", "label": "forget skill",
                        "result": _shrink(skill_delete(email, dm.group(1).strip()), 300)})

    # Creator/admin board — the AI runs the console itself, server-side
    if email and is_admin(email):
        if re.search(r"(?:oracool|ora-cool|brand).{0,35}(?:inbox|mail|social|page|accounts?)|(?:social|brand) accounts?", low):
            out.append({"tool": "admin", "label": "OraCool-owned accounts",
                        "result": _shrink(brand_inspect(email), 2200)})

        bm = re.search(r"(?:block|ban|suspend)\s+(?:the\s+)?(?:user|account)?\s*([^\s@]+@[^\s@]+\.[^\s@]+)", low)
        um = re.search(r"(?:unblock|unban|reinstate|allow)\s+(?:the\s+)?(?:user|account)?\s*([^\s@]+@[^\s@]+\.[^\s@]+)", low)
        gm = re.search(r"grant\s+(starter|pro|ultra|professional|enterprise)(?:\s+plan)?\s+(?:to|for)\s+([^\s@]+@[^\s@]+\.[^\s@]+)(?:\s+for\s+(\d+)\s*days?)?", low)
        rm = re.search(r"(?:revoke|downgrade|cancel)\s+(?:the\s+)?(?:subscription|plan|access)\s+(?:of|for|to)\s+([^\s@]+@[^\s@]+\.[^\s@]+)", low)
        dm2 = re.search(r"(?:delete|remove|erase)\s+(?:the\s+)?(?:user|account)\s+([^\s@]+@[^\s@]+\.[^\s@]+)", low)
        bm = bm and not (um and um.start() <= bm.start()) and bm
        if bm and "unblock" not in low[bm.start():bm.start()+10]:
            tgt = _clean_email(bm.group(1))
            out.append({"tool": "admin", "label": "block " + tgt,
                        "result": _shrink(block_user(tgt, True,
                                                      "blocked by admin via AI chat", email), 300)})
        if um:
            tgt = _clean_email(um.group(1))
            out.append({"tool": "admin", "label": "unblock " + tgt,
                        "result": _shrink(block_user(tgt, False,
                                                      "unblocked by admin via AI chat", email), 300)})
        if gm:
            plan = "ultra" if gm.group(1) in ("professional", "ultra") else gm.group(1)
            tgt = gm.group(2).lower().strip(" .,;:\"'")
            out.append({"tool": "admin", "label": "grant " + gm.group(1),
                        "result": _shrink(admin_set_pro(tgt, plan,
                                                         int(gm.group(3) or 30), email), 350)})
        if rm:
            out.append({"tool": "admin", "label": "revoke plan",
                        "result": _shrink(admin_set_pro(rm.group(1).lower().strip(" .,;:\"'"), "free", by=email), 300)})
        if dm2:
            out.append({"tool": "admin", "label": "DELETE user",
                        "result": _shrink(admin_delete_user(dm2.group(1).lower().strip(" .,;:\"'"), email), 450)})
        im2 = re.search(r"(?:inspect|look up|show (?:me )?(?:the )?(?:live )?(?:backend )?(?:record|data|profile)|what do we have on|full(?: backend)? (?:record|data)(?: for| on)?)\s+(?:user\s+|account\s+|profile\s+|record\s+)?([^\s@]+@[^\s@]+\.[^\s@]+)", low)
        if im2:
            out.append({"tool": "admin", "label": "user record",
                        "result": _shrink(admin_user_record(im2.group(1)), 2400)})
        if re.search(r"(?:user table|database (?:stats|users)|all user records|export users)", low):
            out.append({"tool": "admin", "label": "users table (live)",
                        "result": _shrink(admin_users_payload(email), 2600)})
        rp = re.search(r"reset (?:the )?password\s+(?:of|for|to)?\s*([^\s@]+@[^\s@]+\.[^\s@]+)(?:\s+(?:to|as|:)\s*(\S{6,64}))?", low)
        if rp:
            out.append({"tool": "admin", "label": "reset password",
                        "result": _shrink(admin_reset_password(rp.group(1).strip(" .,;"), rp.group(2) or "", email), 500)})
        if re.search(r"\b(users|user count|signups?|customers|members|accounts)\b|how many (?:people|users)|who (?:are|is|signed up)", low):
            out.append({"tool": "admin", "label": "user board", "result": _shrink(admin_users_payload(email), 2200)})
        if re.search(r"\b(revenue|mrr|income|earnings|amount (?:gained|earned)|how much (?:did (?:we|i)|we|do i))\b|(made|made recently|total)(?: earned| gained)?", low):
            out.append({"tool": "admin", "label": "revenue", "result": _shrink(admin_revenue_payload(), 1500)})
        # raw platform logs + diagnostics + audit trail — admin AI only
        if re.search(r"\b(platform|server|raw|backend|system)\s+logs?\b|\blog ?file\b|\bshow (?:me )?(?:the )?logs?\b"
                     r"|\bwhat(?:'s| is) happening (?:on|in) the (?:server|backend)\b|\bany errors\b", low):
            out.append({"tool": "admin", "label": "platform logs (raw)",
                        "result": _shrink({"lines": platform_logs(body_log_lines(low))}, 2600)})
        if re.search(r"\bdiagnostics?\b|\bhealth (?:check|report)\b|\bserver (?:status|health)\b"
                     r"|\bsystem (?:status|health)\b|\bhow is the (?:server|system|backend)\b", low):
            out.append({"tool": "admin", "label": "diagnostics",
                        "result": _shrink(platform_diagnostics(), 2400)})
        if re.search(r"\baudit (?:log|trail)\b|\bwhat actions ran\b|\brecent (?:actions|activity)\b", low):
            out.append({"tool": "admin", "label": "audit trail",
                        "result": _shrink(audit_tail(60), 2000)})
        if re.search(r"\bwho(?:'s| is| are)? online\b|\bwho is (?:using|on) (?:the )?(?:app|platform)\b"
                     r"|\bactive (?:users|accounts)\b|\bonline (?:now|users)\b", low):
            out.append({"tool": "admin", "label": "who is online",
                        "result": _shrink(admin_online_payload(), 1600)})
        if re.search(r"\bpayments?\b|\btransactions?\b|\bsales?\b|\bwho paid\b", low) and \
                re.search(r"\b(list|show|all|recent|latest|which|any|every)\b", low):
            _rv2 = admin_revenue_payload()
            out.append({"tool": "admin", "label": "payments",
                        "result": _shrink({"totals": {k: _rv2.get(k) for k in
                                                      ("total_ngn", "total_usd", "payments", "active_subscribers",
                                                       "mrr_ngn", "this_month_ngn")},
                                           "history": (_rv2.get("payments") or [])[:25]}, 2600)})
        _cm2 = re.search(r"confirm (?:the )?crypto (?:payment|order|invoice)?\s*(ora-[\w-]+)", low)
        if _cm2:
            out.append({"tool": "admin", "label": "confirm crypto " + _cm2.group(1),
                        "result": _shrink(crypto_grant(_cm2.group(1), "admin-confirm"), 600)})

    # ---- connectors & alerts platform (every tier — the user's own channels) ----
    if email:
        if re.search(r"\b(connect|link|hook|wire|set ?up|add)\b[^.?]{0,28}\b(telegram|whatsapp|discord|slack|webhook|notificatio|alerts?|my (?:apps?|data)|apps?|api|phone.{0,10}(?:alerts|notify))\b", low) \
           or re.search(r"\b(notificatio|alerts?)\b.{0,16}\b(connect|setup|set up|work|status)\b", low):
            out.append({"tool": "alerts", "label": "connectors & alerts (live)",
                        "result": _shrink(alerts_panel_status(email), 1200)})
        if re.search(r"\b(?:send|fire|trigger)\b.{0,12}\b(?:me\b)?.{0,8}\b(?:test\b)?.{0,4}\b(?:alert|notification|push)\b|test (?:my |the )?(?:alerts?|notifications?|connector)", low):
            out.append({"tool": "alerts", "label": "test alert → user's channels",
                        "result": _shrink(alerts_test_all(email), 900)})
        tog = re.search(r"\b(turn on|enable|start|stop|disable|turn off)\b[^.?]{0,24}\b(login|payment|security|watch|watchlist|tracker|gateway|digest|daily)\b[^.?]{0,14}\balerts?\b", low)
        if tog:
            _on = tog.group(1) in ("turn on", "enable", "start")
            _et = "watch" if tog.group(2) == "watchlist" else ("digest" if tog.group(2) == "daily" else tog.group(2))
            out.append({"tool": "alerts", "label": ("enable " if _on else "disable ") + _et + " alerts",
                        "result": _shrink(alerts_toggle(email, _et, _on), 300)})
        if re.search(r"\b(api key|api access|my api|my hook|hook url|gateway (?:key|status|info|url))\b", low):
            out.append({"tool": "gateway", "label": "API gateway info",
                        "result": _shrink(gateway_info(email), 600)})
        if re.search(r"\brotate (?:my )?api key\b", low):
            out.append({"tool": "gateway", "label": "rotate API key",
                        "result": _shrink(gateway_issue_key(email), 500)})
        insk = re.search(r"install (?:the )?skill ([\w\- ]{2,40})", low)
        if insk:
            out.append({"tool": "skills", "label": "install skill from marketplace",
                        "result": _shrink(skill_install(email, insk.group(1).strip()), 500)})
        shsk = re.search(r"(?:share|publish|list) (?:my )?skill ([\w\- ]{2,40})", low)
        if shsk:
            out.append({"tool": "skills", "label": "share skill to marketplace",
                        "result": _shrink(skill_share(email, shsk.group(1).strip(), True), 400)})
        if re.search(r"\b(community|marketplace)\b[^.?]{0,14}skills?\b|browse (?:the |community )?skills", low):
            out.append({"tool": "skills", "label": "skill marketplace",
                        "result": _shrink(skills_market(), 900)})

    # Mail watch — the user's own mailbox (connect once, then ask any time)
    if email and not em and (
            re.search(r"\b(unread|inbox|new mail|new email|any mail|any email|mailbox)\b", low)
            or re.search(r"\b(check|read|scan|open|what'?s? in)\b[^.?]{0,16}\b(my|the)\s+(mail|email|inbox|mailbox)\b", low)
            or re.search(r"\b(mail|email)\s+(messages?|notifications?|count)\b", low)):
        _mc = mail_watch_config(email)
        if not _mc:
            out.append({"tool": "mail", "label": "mail watch (not connected)",
                        "result": json.dumps({"connected": False,
                                              "note": "No mailbox is connected to this account yet. Open Devices → "
                                                      "Connectors & Alerts → 📧 Mail watch and add your email + an app "
                                                      "password (Gmail: 2-Step Verification → App passwords). OraCool then "
                                                      "reports unread counts, senders and subjects — never message bodies — "
                                                      "and pings you here when new mail arrives."})})
        else:
            out.append({"tool": "mail", "label": "mailbox · " + str(_mc.get("email") or ""),
                        "result": _shrink(mail_check(_mc), 1800)})

    # The user's own creation gallery (images & videos stored on the server)
    if email and re.search(r"\b(my|the)\s+(images?|videos?|creations?|gallery|media|art)\b"
                           r"|\bshow (?:me )?my (?:images?|videos?|creations?|gallery)\b"
                           r"|\bwhat have i (?:created|generated|made)\b|\bimage gallery\b", low):
        _ml = media_list(email)
        out.append({"tool": "media", "label": "your creations · " + str(len(_ml)) + " items",
                    "result": _shrink({"count": len(_ml),
                                       "items": [{"kind": x.get("kind"), "prompt": x.get("prompt"),
                                                  "url": x.get("local") or x.get("url"), "t": x.get("t")}
                                                  for x in _ml[:8]]}, 1500)})

    # Locked-feature notices: the AI explains what plan unlocks it (honest, no fake results)
    def _locked(feature, plan):
        out.append({"tool": "locked", "label": feature,
                    "result": json.dumps({"feature": feature, "unlocks_on": plan,
                                          "note": f"This feature unlocks on the {PLANS.get(plan, {}).get('label', plan.title())} plan. The user is currently on {PLANS.get(tier, {}).get('label', tier.title())}."})})
    if not tier_gte(tier, "starter") and any(k in low for k in ("search for", "search the web", "web search")):
        _locked("web search", "starter")
    if not tier_gte(tier, "starter"):
        _osint_ask = bool(ipm) or any(k in low for k in ("dark web", "dark-web", "darkweb", "onion", "breach", "pwned", "whois",
                                                          "osint", "carrier", "who called", "caller", "username", "handle")) \
            or (em is not None and any(k in low for k in ("leak", "hack", "investigate", "check", "email"))) \
            or (ph is not None and any(k in low for k in ("phone", "number", "trace")))
        if _osint_ask:
            _locked("OSINT tools (IP · domain · email breach · phone · username · dark-web index)", "starter")
    if not tier_gte(tier, "pro"):
        if any(k in low for k in ("shodan", "virustotal", "abuseipdb", "urlscan", "leakcheck")):
            _locked("deep OSINT (Shodan / VirusTotal / AbuseIPDB / URLScan / LeakCheck)", "pro")
        im2 = re.search(r"(?:generate|create|make|draw|imagine|design)\s+(?:an?\s+)?(?:image|picture|photo|art|logo|wallpaper)?\s*(?:of|for)?\s*(.{6,200})", low)
        if im2 and any(k in low for k in ("generate", "create", "make", "draw", "imagine", "design", "image", "picture")) \
                and not _wants and not _no_media_ask and not (_video_words and not _image_words):
            _locked("image creation", "pro")
    if not tier_gte(tier, "ultra"):
        if _video_words and re.search(r"\b(?:generate|create|make|produce|render|animate)\b", low) and not _wants:
            _locked("video creation", "ultra")
        if "github" in low and any(k in low for k in ("search", "repo", "find", "github")):
            _locked("GitHub console", "ultra")
    return out[:4]


def body_log_lines(low):
    m = re.search(r"\b(\d{1,4})\s*(?:lines?|entries|rows)\b", low or "")
    try:
        return max(20, min(int(m.group(1)), 500)) if m else 120
    except Exception:
        return 120


def tool_context(tools):
    """Build a compact system message grounding the AI in live tool results."""
    if not tools:
        return None
    parts = []
    for t in tools:
        parts.append("Tool %s (%s) returned: %s" % (t["tool"], t["label"], t["result"]))
    return ("OraCool just ran these live tools itself. The results below are REAL, current data. "
            "Answer using them, in your calm JARVIS voice, and mention the key figures.\n"
            "The app ALREADY renders these results to the user as cards (build preview, publish link). "
            "So NEVER paste, repeat or wrap up the raw JSON in your reply, and NEVER invent a URL: when you "
            "mention a link, quote the exact url field a tool returned (its 'note' tells you the right one).\n\n"
            + "\n\n".join(parts))


# ---------------------------------------------------------------- file analysis

def _pdf_text(data):
    import zlib
    text = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        raw = m.group(1).strip()
        try:
            dec = zlib.decompress(raw)
        except Exception:
            dec = raw
        try:
            s = dec.decode("latin-1", "replace")
        except Exception:
            continue
        for seg in re.findall(r"BT(.*?)ET", s, re.S):
            for tm in re.findall(r"\(((?:[^()\\]|\\.)*)\)", seg):
                text.append(tm.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\"))
    return " ".join(text)


def _docx_text(data):
    import io, zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        xml = z.read("word/document.xml").decode("utf-8", "replace")
        lines = []
        for para in re.split(r"</w:p>", xml):
            line = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para))
            if line.strip():
                lines.append(line)
        return "\n".join(lines)
    except Exception:
        return ""


def _xlsx_text(data):
    import io, zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        xml = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
        return " ".join(re.findall(r"<t[^>]*>(.*?)</t>", xml))
    except Exception:
        return ""


_VISION_LAST = {"provider": "", "error": "", "at": 0}


def _groq_vision_models():
    """patch44: Groq retired its Llama-4 vision models; qwen3.8-27b on Groq accepts images. A configured
    GROQ_VISION_MODEL is tried first, then the known multimodal ids."""
    ms = []
    cfg = str(KEYS.get("GROQ_VISION_MODEL") or os.environ.get("GROQ_VISION_MODEL") or "").strip()
    if cfg:
        ms.append(cfg)
    for m in ("qwen/qwen3.8-27b", "meta-llama/llama-4-scout-17b-16e-instruct", "meta-llama/llama-4-maverick-17b-128e-instruct"):
        if m not in ms:
            ms.append(m)
    return ms


def _vision_err_text(e):
    try:
        body = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
        msg = (json.loads(body).get("error") or {}).get("message") if body else ""
        return (msg or body or str(e))[:160]
    except Exception:
        return str(e)[:160]


def _vision_describe_prompt(prompt, mime, data_b64, max_tokens=420):
    """Run one vision prompt through the available providers (Gemini → Groq multimodal ladder → OpenAI).
    Returns "" when nothing can serve it — never a fabricated description; the last error is kept in _VISION_LAST."""
    errs = []
    if gemini_key() and not _gemini_skipped():
        try:
            gt, gerr = _gemini_text(prompt, model="gemini-flash-lite-latest", image_b64=(data_b64 or "")[:1_400_000], mime=mime or "image/jpeg")
            if gt and len(gt.strip()) > 20:
                _VISION_LAST.update(provider="gemini-flash-lite", error="", at=time.time())
                return gt.strip()
            if gerr:
                errs.append("gemini: " + str(gerr)[:120])
        except Exception as e:
            errs.append("gemini: " + str(e)[:120])
    tries = []
    if key("GROQ_API_KEY"):
        for m in _groq_vision_models():
            tries.append(("https://api.groq.com/openai/v1/chat/completions", key("GROQ_API_KEY"), m))
    if key("OPENAI_API_KEY"):
        tries.append(("https://api.openai.com/v1/chat/completions", key("OPENAI_API_KEY"), "gpt-4o-mini"))
    payload_img = {"type": "image_url", "image_url": {"url": "data:" + (mime or "image/jpeg") + ";base64," + (data_b64 or "")[:1_400_000]}}
    for url, k, model in tries:
        try:
            _, raw, _ = http_fetch(url, method="POST", timeout=60,
                                   headers={"Authorization": "Bearer " + k, "Content-Type": "application/json"},
                                   json_body={"model": model, "max_tokens": max_tokens,
                                              "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, payload_img]}]})
            c = (json.loads(raw).get("choices") or [{}])[0].get("message", {}).get("content")
            if c and len(c.strip()) > 20:
                _VISION_LAST.update(provider=model, error="", at=time.time())
                return c.strip()
            errs.append(model + ": empty reply")
        except Exception as e:
            errs.append(model + ": " + _vision_err_text(e))
            continue
    _VISION_LAST.update(provider="", error=" | ".join(errs)[:400] or "no vision provider configured", at=time.time())
    return ""


_VISION_PROMPTS = {
    "attachment": ("The user attached this image to a chat with an assistant that cannot see images. Describe it thoroughly so the assistant can "
                   "answer any question about it: what it is (photo, screenshot, document, chart, meme…), the main subjects and what they are doing, "
                   "setting, colours and mood, ALL visible text transcribed exactly (labels, numbers, prices, names, UI buttons, error messages), "
                   "counts of people/objects, brands or logos, and anything notable or unusual. Never guess identity, age or ethnicity of people. "
                   "Plain prose, 5-10 sentences; if it is a document or screenshot, transcribe the text faithfully first."),
    "selfie": ("This is the user's OWN camera photo (a selfie they just took for their assistant). Describe warmly and honestly how they look "
               "on camera: framing and angle, lighting (too dark / backlit / even), facial expression, outfit and colours, background and anything "
               "distracting, image sharpness. Then give 3 concrete tips to look better on camera. Never guess identity, age or ethnicity. 4-6 sentences."),
    "clip": ("These are frames from the user's OWN short video clip, arranged left to right in time order (start, middle, end). Describe warmly and "
             "honestly how they come across on camera: framing, lighting, expression and energy, movement between frames, outfit, background and "
             "anything distracting, sharpness. Then give 3 concrete tips to look better on video. Never guess identity, age or ethnicity. 4-6 sentences."),
}


def _vision_describe(name, mime, data_b64, purpose=""):
    """Best-effort image description through any vision-capable provider key.
    Returns "" when nothing can serve it — never a fabricated description."""
    _vp = _VISION_PROMPTS.get((purpose or "").strip().lower())
    if _vp:
        return _vision_describe_prompt(_vp, mime, data_b64, max_tokens=(600 if purpose == "attachment" else 420))
    return _vision_describe_prompt("Describe this image factually for an OSINT analyst: objects, text visible, people-count (no names), "
                                   "scene type, likely edit/screenshot evidence. 3 sentences max.", mime, data_b64, max_tokens=300)


def analyze_file(name, mime, data_b64, purpose=""):
    try:
        data = base64.b64decode(data_b64 or "")
    except Exception:
        return {"error": "Invalid file data."}
    if len(data) > 5 * 1024 * 1024:
        return {"error": "File too large (max 5 MB)."}
    name = (name or "file").strip() or "file"
    mime = (mime or "").lower()
    ext = os.path.splitext(name)[1].lower()
    text, kind = "", "unknown"
    if ext in (".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".css", ".yml", ".yaml",
               ".ts", ".go", ".java", ".c", ".cpp", ".sh", ".ini", ".env", ".sql", ".xml") or mime.startswith("text/"):
        kind, text = "text", data.decode("utf-8", "replace")
    elif ext == ".pdf" or mime == "application/pdf":
        kind, text = "pdf", _pdf_text(data)
    elif ext == ".docx":
        kind, text = "docx", _docx_text(data)
    elif ext == ".xlsx":
        kind, text = "xlsx", _xlsx_text(data)
    elif ext in (".html", ".htm"):
        kind = "html"
        text = re.sub(r"<[^>]+>", " ", data.decode("utf-8", "replace"))
    elif mime.startswith("image/") or ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"):
        fore = {}
        try:
            fore = media_inspect(data_b64=data_b64) or {}
        except Exception:
            pass
        desc = _vision_describe(name, mime or ("image/" + ("png" if ext == ".png" else "jpeg")), data_b64, purpose=purpose)
        if purpose in ("selfie", "clip"):
            return {"name": name, "type": "image", "chars": 0, "text": desc[:4000], "purpose": purpose,
                    "note": desc[:600] if desc else "Vision is not configured on this server — the capture is saved on the device only."}
        if not desc:
            desc = ""  # never fabricate — the client tells the user vision could not read it
        note = ("Image received. " + (desc[:600] + " " if desc else "(OraCool could not look inside this image right now — vision provider offline.) ") +
                "Fingerprints: SHA-256 " + str(fore.get("sha256") or "?")[:16] + "… · " +
                str(fore.get("format") or "?").upper() + " " +
                (f"{fore.get('width')}x{fore.get('height')}" if fore.get("width") else "") +
                (" · EXIF present" if fore.get("exif") else "") +
                (" · GPS embedded" if fore.get("gps") else " · no GPS") +
                (" · editing software detected: " + str(fore.get("software")) if fore.get("software") else "") +
                " — say 'preserve as evidence' to freeze it into a case.")
        return {"name": name, "type": "image", "chars": len(desc), "text": desc[:4000],
                "sha256": fore.get("sha256", ""), "note": note, "vision": bool(desc),
                "vision_error": ("" if desc else str(_VISION_LAST.get("error") or "")[:200]),
                "width": fore.get("width"), "height": fore.get("height")}
    elif mime.startswith("video/") or ext in (".mp4", ".mov", ".webm", ".avi", ".mkv"):
        sha = hashlib.sha256(data).hexdigest()
        return {"name": name, "type": "video", "chars": 0, "text": "",
                "sha256": sha,
                "note": (f"Video received ({len(data)/1e6:.1f} MB, SHA-256 {sha[:16]}…). Frame-level "
                         "AI analysis needs the paid vision stack, but OraCool can hash, preserve and "
                         "chain-custody it, and match its fingerprint against anything already in your cases.")}
    else:
        try:
            kind, text = "text", data.decode("utf-8", "replace")
        except Exception:
            return {"error": "Unsupported file type: " + (mime or ext)}
    if not text.strip():
        return {"name": name, "type": kind, "chars": 0, "text": "",
                "note": "No extractable text found (it may be a scanned/image-only document)."}
    return {"name": name, "type": kind, "chars": len(text), "text": text[:40000],
            "preview": text[:1200],
            "note": "Extracted text is ready — I'll analyze it in your next message."}



# ---------------------------------------------------------------- universal generator
# generate-random.org exposes a public JSON API (verified live) plus a full
# generator catalog we can deep-link into — "generate anything" support.
GENRANDOM_KINDS = {"passwords": "passwords", "password": "passwords", "uuid": "uuids", "uuids": "uuids",
                   "token": "tokens", "tokens": "tokens", "api-key": "api-keys", "api key": "api-keys",
                   "number": "numbers", "numbers": "numbers", "dice": "dice-rolls",
                   "hash": "hashes", "sha": "hashes", "phone": "phone-numbers",
                   "phone number": "phone-numbers", "email": "emails", "emails": "emails",
                   "address": "addresses", "addresses": "addresses", "credit card": "credit-cards",
                   "iban": "iban", "name": "names", "names": "names", "color": "colors",
                   "colors": "colors", "hex": "hex", "binary": "binary", "date": "dates",
                   "dates": "dates", "emoji": "emojis", "emojis": "emojis", "word": "words",
                   "company": "company-names", "username": "usernames", "barcode": "barcodes"}


def generate_random(query=""):
    """Universal generator: live JSON from generate-random.org, catalog fallback."""
    q = (query or "").strip().lower()
    kind = ""
    for k in sorted(GENRANDOM_KINDS, key=len, reverse=True):
        if k in q:
            kind = GENRANDOM_KINDS[k]
            break
    def _search_links():
        try:
            _, raw, _ = http_fetch("https://generate-random.org/api/search?q=" + urllib.parse.quote(q or "generator"),
                                   timeout=20, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            j = json.loads(raw)
            rs = ((j.get("data") or j).get("results")) or []
            return {"query": q,
                    "generator_links": [{"title": r.get("title", ""), "url": "https://generate-random.org" + (r.get("url") or "")}
                                        for r in rs[:6]],
                    "note": "generate-random.org catalog — every kind of random data (200+ generators). "
                            "Open a link or name the kind and I'll pull live results directly."}
        except Exception as e:
            return {"error": "generate-random.org unreachable: " + str(e)[:120]}
    if kind:
        try:
            _, raw, _ = http_fetch("https://generate-random.org/api/v1/generate/" + kind + "?count=3",
                                   timeout=20, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            j = json.loads(raw)
            if j.get("success"):
                return {"kind": kind, "generated": j.get("data"), "count": len(j.get("data") or []),
                        "source": "generate-random.org API",
                        "security_note": "For real security use (API keys, passwords for accounts), "
                                         "generate locally on your device — never paste secrets fetched over the web."
                                         if kind in ("passwords", "tokens", "api-keys", "hashes") else ""}
        except Exception:
            pass
    return _search_links()


# ---------------------------------------------------------------- live news
def osint_news(query=""):
    """Live headlines from Google News RSS — free, global, source-named."""
    q = (query or "").strip()
    if q:
        u = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:7d")
             + "&hl=en-NG&gl=NG&ceid=NG:en")
    else:
        u = "https://news.google.com/rss?hl=en-NG&gl=NG&ceid=NG:en"
    try:
        _, raw, _ = http_fetch(u, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        page = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    except Exception as e:
        return {"error": "News fetch failed: " + str(e)[:120]}
    items = re.findall(r"<item>(.*?)</item>", page, re.S)[:12]
    heads = []
    for it in items:
        t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
        d = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        s = re.search(r"<source[^>]*>(.*?)</source>", it, re.S)
        l = re.search(r"<link>(.*?)</link>", it, re.S)
        heads.append({"title": _strip_tags(html.unescape(t.group(1))) if t else "",
                      "source": _strip_tags(html.unescape(s.group(1))) if s else "",
                      "when": d.group(1).strip() if d else "",
                      "url": l.group(1).strip() if l else ""})
    return {"query": q, "headlines": heads[:10],
            "note": "Live from Google News (last 7 days). Quote [Source: <publisher> · <date>] for every headline."}


# ---------------------------------------------------------------- user skills
_skills_lock = threading.RLock()


def _skills_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "skills.json")


def _skills_all():
    try:
        with open(_skills_file()) as f:
            return json.load(f)
    except Exception:
        return {}


def skills_load(email):
    return _skills_all().get((email or "").strip().lower(), [])


def skill_save(email, name, trigger, instructions):
    """Install a custom feature for one account — the AI obeys it in every chat.
    (Prompt-level self-extension: honest 'upgrade' without touching server code.)"""
    email = (email or "").strip().lower()
    if not email:
        return {"error": "Sign in first — skills attach to your account."}
    name = re.sub(r"\s+", " ", (name or "").strip())[:60]
    if not name:
        return {"error": "Name the skill, e.g. 'add a skill called phone-triage'."}
    with _skills_lock:
        d = _skills_all()
        lst = [s for s in d.get(email, []) if s.get("name", "").lower() != name.lower()][:24]
        lst.append({"name": name, "trigger": (trigger or name)[:80],
                    "instructions": (instructions or "").strip()[:2000],
                    "created": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())})
        d[email] = lst
        with open(_skills_file(), "w") as f:
            json.dump(d, f, indent=1)
    return {"ok": True, "saved": name,
            "note": "Skill installed for this account and active in every chat now. "
                    "'list my skills' to review, 'remove skill <name>' to delete."}


def skill_delete(email, name):
    email = (email or "").strip().lower()
    name = (name or "").strip().lower()
    with _skills_lock:
        d = _skills_all()
        lst = d.get(email, [])
        keep = [s for s in lst if s.get("name", "").lower() != name]
        if len(keep) == len(lst):
            keep = [s for s in lst if name not in s.get("name", "").lower()]
        if len(keep) == len(lst):
            return {"error": "No skill by that name."}
        d[email] = keep
        with open(_skills_file(), "w") as f:
            json.dump(d, f, indent=1)
    return {"ok": True, "removed": name}


def _clean_email(s):
    s = (s or "").strip().strip(" .,;:" + chr(34) + chr(39))
    s = s.rstrip(".,;:")
    return s.lower()


# ---------------------------------------------------------------- admin delete
def admin_delete_user(email, by=""):
    """Permanent user removal: Supabase account + subscription + flags + cases
    + trackers. Admin-only route; never callable by the owner themselves."""
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "Enter a valid email address."}
    if email in [a.lower() for a in admin_emails()]:
        return {"error": "An admin/creator account cannot be deleted."}
    removed = []
    url = (key("SUPABASE_URL") or "").rstrip("/")
    svc = key("SUPABASE_SERVICE_KEY")
    u = _supa_admin_user(email)
    if u and u.get("id") and url and svc:
        try:
            http_fetch(url + "/auth/v1/admin/users/" + u["id"], method="DELETE",
                       headers={"apikey": svc, "Authorization": "Bearer " + svc,
                                "Content-Type": "application/json"}, json_body={}, timeout=25)
            removed.append("auth account")
        except Exception as e:
            return {"error": "Could not delete the Supabase account: " + str(e)[:140]}
    try:
        with _sub_lock:
            subs = [s for s in load_subscribers() if (s.get("email") or "").lower() != email]
            with open(_sub_file(), "w") as f:
                json.dump(subs, f, indent=2)
        removed.append("subscription")
    except Exception:
        pass
    if url and svc:
        eq = urllib.parse.quote(email, safe="")
        for tbl in ("user_flags", "case_store"):
            try:
                http_fetch(url + "/rest/v1/" + tbl + "?email=eq." + eq, method="DELETE",
                           headers={"apikey": svc, "Authorization": "Bearer " + svc}, timeout=20)
            except Exception:
                pass
        removed.append("supabase rows")
    try:
        d = _cases_load()
        gone = [cid for cid, c in (d.get("cases") or {}).items()
                if (c.get("owner") or "").lower() == email]
        for cid in gone:
            del d["cases"][cid]
        if gone:
            _cases_save(d)
        removed.append(str(len(gone)) + " case(s)")
    except Exception:
        pass
    try:
        d = _tracker_load()
        gone = [k for k, v in d.items() if (v.get("creator") or "") == email]
        for k in gone:
            del d[k]
        if gone:
            _tracker_save(d)
        removed.append(str(len(gone)) + " tracker(s)")
    except Exception:
        pass
    try:
        audit_log(by or "admin", "admin.delete_user", email + " — removed: " + ", ".join(removed))
    except Exception:
        pass
    return {"ok": True, "email": email, "removed": [r for r in removed],
            "note": "User and every trace of their data deleted, permanently."}


def admin_reset_password(email, new_pw="", by=""):
    """Admin PASSWORD RESET (not a reveal). Supabase stores bcrypt hashes — nobody,
    including this app, can read a user's real password. A reset sets a fresh one the
    user can use immediately, then change in their account."""
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "Enter a valid email address."}
    if not new_pw:
        import string as _st2
        _cs = _st2.ascii_letters + _st2.digits + "#@%&*!"
        _rnd = [chr(b) for b in os.urandom(14)]
        new_pw = "".join(_cs[b % len(_cs)] for b in (ord(c) for c in _rnd))
    if len(new_pw) < 8:
        return {"error": "New password must be at least 8 characters."}
    u = _supa_admin_user(email)
    if not u or not u.get("id"):
        return {"error": "No account found with that email."}
    url = (key("SUPABASE_URL") or "").rstrip("/")
    svc = key("SUPABASE_SERVICE_KEY")
    try:
        st, raw, _ = http_fetch(url + "/auth/v1/admin/users/" + u["id"], method="PUT",
                                headers={"apikey": svc, "Authorization": "Bearer " + svc,
                                         "Content-Type": "application/json"},
                                json_body={"password": new_pw}, timeout=25)
        if st != 200:
            return {"error": "Supabase refused the reset (HTTP " + str(st) + ")."}
    except Exception as e:
        return {"error": "Reset failed: " + str(e)[:140]}
    try:
        touch_user(email, pwd_reset_at=time.strftime("%Y-%m-%d %H:%M:%S"), pwd_reset_by=(by or "").lower())
    except Exception:
        pass
    try:
        audit_log(by or "admin", "admin.reset_password", email)
    except Exception:
        pass
    return {"ok": True, "email": email, "new_password": new_pw,
            "note": "Temporary password is set — deliver it to the user over a private channel you control; "
                    "tell them to change it in Account. The original password was never visible to anyone."}


def admin_user_record(email):
    """Admin-only live read across EVERY production store for one account —
    auth profile, payments, flags, cases+evidence, trackers, skills, trading."""
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "Enter a valid email address."}
    rec = {"email": email, "as_of": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}
    try:
        u = _supa_admin_user(email)
        rec["auth"] = None if not u else {"id": u.get("id"), "created_at": u.get("created_at"),
                                          "last_sign_in_at": u.get("last_sign_in_at"),
                                          "last_sign_in_ip": u.get("last_sign_in_ip") or "",
                                          "email_confirmed_at": bool(u.get("email_confirmed_at")),
                                          "banned_until": u.get("banned_until")}
    except Exception as e:
        rec["auth"] = {"error": str(e)[:100]}
    try:
        _uu = load_users().get(email) or {}
        rec["last_ip"] = _uu.get("last_ip") or ""
        rec["last_seen"] = _uu.get("last_seen") or ""
        if rec["last_ip"]:
            _g = _geo_ip(rec["last_ip"]) or {}
            rec["ip_region"] = {"country": _g.get("country", ""), "region": _g.get("region", ""),
                                "city": _g.get("city", "")}
        if _uu.get("pwd_reset_at"):
            rec["last_password_reset"] = _uu.get("pwd_reset_at")
    except Exception:
        pass
    rec["effective_tier"] = check_tier(email)
    try:
        rec["blocked"] = is_blocked(email)
    except Exception:
        rec["blocked"] = False
    try:
        rec["payments"] = [s for s in load_subscribers() if (s.get("email") or "").lower() == email][-8:]
    except Exception:
        rec["payments"] = []
    try:
        d = _cases_load()
        cases = []
        for c in (d.get("cases") or {}).values():
            if (c.get("owner") or "").lower() == email:
                cases.append({"name": c.get("name"), "status": c.get("status"),
                              "evidence": len(c.get("artifacts") or []), "created": c.get("created")})
        rec["cases"] = cases
    except Exception:
        rec["cases"] = []
    try:
        tk = [v for v in _tracker_load().values() if (v.get("creator") or "").lower() == email]
        rec["trackers"] = [{"slug": v.get("slug"), "target": v.get("target"), "visits": len(v.get("visits") or [])}
                           for v in tk]
    except Exception:
        rec["trackers"] = []
    try:
        rec["skills"] = [s.get("name") for s in skills_load(email)]
    except Exception:
        rec["skills"] = []
    try:
        acc = _load_accounts().get(email) or {}
        rec["paper_trading"] = {"cash": acc.get("cash"), "trades": len(acc.get("trades") or [])} if acc else None
    except Exception:
        rec["paper_trading"] = None
    rec["note"] = "Live read straight from the backend stores — identical source as the Admin board."
    return rec


# ---------------------------------------------------------------- crypto payments (ATLOS)
def atlos_api(epath, body_obj):
    base = (key("ATLOS_BASE") or "https://api.atlos.io/gateway/rest").rstrip("/")
    secret = key("ATLOS_API_SECRET")
    mer = key("ATLOS_MERCHANT_ID")
    if not secret or not mer:
        return {"error": "Crypto gateway not configured — set ATLOS_MERCHANT_ID and "
                         "ATLOS_API_SECRET in the environment (atlos.io dashboard → Settings)."}
    b = dict(body_obj or {})
    b.setdefault("MerchantId", mer)
    try:
        _, raw, _ = http_fetch(base + epath, method="POST", timeout=35,
                               headers={"ApiSecret": secret, "Content-Type": "application/json"},
                               json_body=b)
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            d = {}
        return {"error": "ATLOS: " + str(d.get("ErrorMessage") or d or ("HTTP " + str(e.code)))[:180]}
    except Exception as e:
        return {"error": "ATLOS gateway unreachable: " + str(e)[:140]}


_crypto_lock = threading.RLock()


def _crypto_orders_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "crypto_orders.json")


def _crypto_orders():
    try:
        with open(_crypto_orders_file()) as f:
            return json.load(f)
    except Exception:
        return {}


def crypto_invoice(email, plan="pro", site=""):
    email = (email or "").strip().lower()
    plan = (plan or "pro").lower()
    if plan == "professional":
        plan = "ultra"
    if plan != "fine" and (plan not in PLANS or PLANS[plan].get("custom")):
        return {"error": "Pick Starter, Pro, Professional or Enterprise to pay with crypto."}
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "I need the email that should receive the plan."}
    usd = FINE_USD if plan == "fine" else PLANS[plan]["price_usd"]
    ref = "ora-" + _token(6) + "-" + plan
    inv = atlos_api("/Invoice/Create", {
        "OrderId": ref, "OrderAmount": float(usd), "OrderCurrency": "USD",
        "UserEmail": email, "UserName": email.split("@")[0], "SendEmail": False,
        "PostbackUrl": (site.rstrip("/") + "/api/pay/crypto/postback") if site else "",
        "Memo": json.dumps({"email": email, "plan": plan, "ref": ref}),
        "TimeExpire": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3 * 3600)),
    })
    if inv.get("Id"):
        with _crypto_lock:
            d = _crypto_orders()
            d[ref] = {"email": email, "plan": plan, "invoice": inv["Id"],
                      "created": int(time.time()), "granted": False}
            with open(_crypto_orders_file(), "w") as f:
                json.dump(d, f, indent=1)
        link = inv.get("PaymentLink") or inv.get("paymentLink") or ""
        with _crypto_lock:
            d = _crypto_orders()
            if ref in d:
                d[ref]["pay_url"] = link
                with open(_crypto_orders_file(), "w") as f:
                    json.dump(d, f, indent=1)
        _addr = crypto_wallet()
        return {"ok": True, "ref": ref, "pay_url": link, "amount_usd": usd, "plan": plan,
                "wallet": _addr, "wallet_net": "Ethereum (ERC-20) · same address for USDT/USDC/ETH",
                "coins": ["USDT (ERC-20)", "USDC (ERC-20)", "ETH", "and every coin ATLOS accepts (BTC · XMR …)"],
                "qr_svg_b64": crypto_qr_svg_b64(_addr) if _addr else "",
                "network_note": "Send ONLY to this address on the network shown. An ATLOS page is also "
                                "available for other coins and unlocks the plan automatically.",
                "note": ("Two ways to pay: (1) send $" + str(usd) + " worth of USDT/USDC/ETH to the address above, "
                         "then press 'I have paid — check now', or (2) open the ATLOS page — it shows the exact "
                         "coin amount and unlocks the plan by itself the moment the network confirms.")}
    addr = crypto_wallet()
    if addr:
        with _crypto_lock:
            d = _crypto_orders()
            d[ref] = {"email": email, "plan": plan, "created": int(time.time()),
                      "granted": False, "manual_review": True, "invoice": None, "pay_url": ""}
            with open(_crypto_orders_file(), "w") as f:
                json.dump(d, f, indent=1)
        return {"ok": True, "ref": ref, "amount_usd": usd, "plan": plan,
                "wallet": addr, "wallet_net": "Ethereum mainnet (ERC-20)", "pay_url": "",
                "qr_svg_b64": crypto_qr_svg_b64(addr), "manual_review": True,
                "network_note": "ATLOS is unavailable. Direct transfers require manual admin review. "
                                "Use Ethereum mainnet only; do not send BTC to this address. "
                                "Keep your transaction hash and order reference. No automatic unlock.",
                "gateway_error": "ATLOS is not configured or could not create an invoice."}
    return {"error": "Crypto checkout is unavailable. The operator must set ATLOS_MERCHANT_ID "
                     "and ATLOS_API_SECRET, or configure CRYPTO_WALLET_EVM for manual payment review. "
                     "Use Paystack in the meantime."}


def crypto_grant(ref, src="atlos"):
    d = _crypto_orders()
    o = d.get(ref)
    if not o:
        return {"ok": False, "note": "unknown order ref"}
    if o.get("granted"):
        return {"ok": True, "already": True, "plan": o.get("plan")}
    o["granted"] = True
    o["confirmed_via"] = src
    with _crypto_lock:
        d[ref] = o
        with open(_crypto_orders_file(), "w") as f:
            json.dump(d, f, indent=1)
    if o.get("plan") == "fine":
        r = fine_paid(o["email"], ref, FINE_USD, "USD", "crypto", src)
        if isinstance(r, dict):
            r["crypto_ref"] = ref
        return r
    r = admin_set_pro(o["email"], o["plan"], PLANS.get(o["plan"], {}).get("days", 30), src)
    if isinstance(r, dict):
        r["crypto_ref"] = ref
    try:
        audit_log("gateway", "crypto.paid", ref + " · " + str(src))
    except Exception:
        pass
    return r


def crypto_status(ref):
    ref = (ref or "").strip()
    o = _crypto_orders().get(ref)
    if not o:
        return {"error": "No pending crypto order with that reference."}
    if o.get("granted"):
        return {"paid": True, "granted": True, "plan": o["plan"], "email": o["email"]}
    txs = atlos_api("/Transaction/List", {"TimeStart": time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(o.get("created", int(time.time())) - 600))})
    tx = None
    if isinstance(txs, dict):
        for t in (txs.get("Transactions") or txs.get("transactions") or []):
            if str(t.get("OrderId") or t.get("orderId") or "") == ref:
                tx = t
                break
    if tx and int(tx.get("Status", tx.get("status") or 0)) >= 100:
        crypto_grant(ref, "poll")
        return {"paid": True, "granted": True, "plan": o["plan"], "email": o["email"]}
    _addr = crypto_wallet()
    _onch = crypto_onchain_seen(ref)
    _msg = ("Still waiting. The receiving address is shown below — send the amount, then press "
            "'I have paid — check now' (or use the ATLOS page, which unlocks by itself on confirmation).")
    if o.get("manual_review"):
        _msg = "Direct-wallet payment needs admin review. Keep your transaction hash and quote order " + ref + ". Checking the wallet does not automatically activate a plan."
    if _onch.get("seen"):
        _msg = ("A transfer into the wallet was detected on-chain after this order was created. An admin "
                "confirms it in one click (admin: 'confirm crypto " + ref + "'); ATLOS payments unlock "
                "automatically without any human step.")
        if not o.get("seen_notified"):
            try:
                with _crypto_lock:
                    d = _crypto_orders()
                    oo = d.get(ref)
                    if oo:
                        oo["seen_notified"] = True
                        with open(_crypto_orders_file(), "w") as f:
                            json.dump(d, f, indent=1)
                notify_admins("payment", "💰 Possible direct crypto payment — " + ref,
                              str(o.get("email")) + " · " + str(o.get("plan")) + " · $"
                              + str(PLANS.get(o.get("plan"), {}).get("price_usd"))
                              + " · verify in Settings/board, then confirm in chat.")
            except Exception:
                pass
    return {"paid": False, "waiting": True, "plan": o["plan"], "ref": ref,
            "amount_usd": PLANS.get(o.get("plan"), {}).get("price_usd"),
            "wallet": _addr, "pay_url": o.get("pay_url") or "",
            "qr_svg_b64": crypto_qr_svg_b64(_addr) if _addr else "",
            "onchain": _onch, "message": _msg}


def crypto_postback_handle(body):
    ref = str(body.get("OrderId") or body.get("orderId") or "")
    try:
        st = int(body.get("Status", body.get("status") or 0))
    except Exception:
        st = 0
    if ref and ref in _crypto_orders() and st >= 100:
        crypto_grant(ref, "postback")
        return {"ok": True, "granted": True}
    return {"ok": True, "seen": True}

# ---------------------------------------------------------------- 2FA (TOTP, RFC 6238)

def _b32decode(s):
    s = (s or "").upper().replace(" ", "").rstrip("=")
    return base64.b32decode(s + "=" * ((8 - len(s) % 8) % 8))

def totp_secret():
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")

def totp_code(secret, t=None, step=30, digits=6):
    t = int(time.time()) if t is None else int(t)
    key = _b32decode(secret)
    counter = struct.pack(">Q", t // step)
    h = hmac_mod.new(key, counter, hashlib.sha1).digest()
    o = h[-1] & 0x0f
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7fffffff) % (10 ** digits)
    return str(code).zfill(digits)

def totp_uri(secret, email, issuer="OraCool AI"):
    return ("otpauth://totp/" + urllib.parse.quote(issuer) + ":" + urllib.parse.quote(email)
            + "?secret=" + secret + "&issuer=" + urllib.parse.quote(issuer)
            + "&digits=6&period=30")

def totp_verify(secret, code, window=1):
    code = (code or "").replace(" ", "")
    if not code.isdigit():
        return False
    for w in range(-window, window + 1):
        if hmac_mod.compare_digest(totp_code(secret, time.time() + w * 30), code):
            return True
    return False

PENDING_2FA = {}   # email -> {"secret", "exp"}

def _totp_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "totp.json")

def _totp_load():
    try:
        with open(_totp_file()) as f:
            return json.load(f)
    except Exception:
        return {}

def twofa_enabled(email):
    return (email or "").lower() in _totp_load()

def twofa_setup(email):
    email = (email or "").strip().lower()
    if not email:
        return {"error": "Email required."}
    if twofa_enabled(email):
        return {"error": "2FA is already enabled on this account."}
    secret = totp_secret()
    PENDING_2FA[email] = {"secret": secret, "exp": time.time() + 600}
    return {"status": "pending", "secret": secret, "otpauth": totp_uri(secret, email)}

def twofa_enable(email, code):
    email = (email or "").strip().lower()
    p = PENDING_2FA.get(email)
    if not p or p["exp"] < time.time():
        return {"error": "No pending 2FA setup — start again."}
    if not totp_verify(p["secret"], code):
        return {"error": "Incorrect code. Check your authenticator app."}
    store = _totp_load()
    store[email] = crypto.seal(p["secret"], key("ENCRYPTION_KEY"), key("ENCRYPTION_IV"))
    try:
        with open(_totp_file(), "w") as f:
            json.dump(store, f)
    except Exception as e:
        return {"error": f"Could not store 2FA: {e}"}
    PENDING_2FA.pop(email, None)
    return {"status": "enabled"}

def twofa_check(email, code):
    email = (email or "").strip().lower()
    store = _totp_load()
    sealed = store.get(email)
    if not sealed:
        return False
    try:
        secret = crypto.open_seal(sealed, key("ENCRYPTION_KEY"), key("ENCRYPTION_IV"))
        return totp_verify(secret, code)
    except Exception:
        return False

# ---------------------------------------------------------------- OAuth (Google / GitHub via Supabase)

def _app_origin(handler=None, body=None):
    host = ""
    if handler:
        host = (handler.headers.get("Host") or "").strip()
    if not host and body:
        host = (body.get("origin") or "").strip()
    if not host:
        host = "localhost:8000"
    scheme = "http" if host.startswith("localhost") or host.startswith("127.") else "https"
    return scheme + "://" + host

def oauth_url(provider, redirect_to=""):
    url = key("SUPABASE_URL")
    if not url:
        return {"error": "Supabase is not configured."}
    provider = (provider or "").lower()
    if provider not in ("google", "github"):
        return {"error": "Unsupported provider. Choose google or github."}
    prov = oauth_providers()
    if prov.get("checked") and not prov.get(provider):
        if provider == "google":
            fix = ("Google sign-in is not enabled on the Supabase project yet. Fix (2 min): "
                   "console.cloud.google.com → APIs & Services → Credentials → OAuth client (Web) → "
                   "Authorized redirect URI = https://<project-ref>.supabase.co/auth/callback; then "
                   "Supabase dashboard → Authentication → Sign In / Providers → Google → ON → paste "
                   "Client ID + Secret → Save. Supabase → Authentication → URL Configuration → Redirect "
                   "URLs → add your app URL. Then the button works for everyone.")
        else:
            fix = ("GitHub sign-in is not enabled yet. Fix (2 min): github.com → Settings → Developer "
                   "settings → OAuth Apps → New (homepage = your app URL, authorization callback = "
                   "https://<project-ref>.supabase.co/auth/callback); then Supabase dashboard → "
                   "Authentication → Sign In / Providers → GitHub → ON → paste Client ID + Client "
                   "secret → Save. The GitHub PAT (for the GitHub console tool) is separate from this.")
        return {"error": fix, "provider_disabled": True, "provider": provider}
    target = (redirect_to or "").strip() or (url.rstrip("/") + "/oauth")
    return {"url": (url.rstrip("/") + "/auth/v1/authorize?provider=" + provider
                    + "&redirect_to=" + urllib.parse.quote(target)),
            "provider": provider,
            "note": "Enable this provider in Supabase dashboard (Authentication → Providers) "
                    "and add the redirect URL there, or the sign-in will fail."}

def oauth_exchange(access_token, refresh_token=None):
    if not access_token:
        return {"error": "No access token from provider."}
    r = supabase_auth("/auth/v1/user", method="GET", access_token=access_token)
    if r.get("status") != 200:
        return {"error": "OAuth session invalid — enable the provider in Supabase and try again.",
                "detail": r.get("data")}
    u = r.get("data") or {}
    email = (u.get("email") or "").strip().lower()
    touch_user(email)
    return {"status": 200, "user": u, "access_token": access_token,
            "refresh_token": refresh_token, "admin": is_admin(email),
            "blocked": is_blocked(email), "pro_token": check_subscription(email)}

# ---------------------------------------------------------------- Biometric login (WebAuthn / Face ID)

def _passkey_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "passkeys.json")

def _passkey_load():
    try:
        with open(_passkey_file()) as f:
            return json.load(f)
    except Exception:
        return {}

def passkey_register(email, credential, access_token=None):
    email = (email or "").strip().lower()
    if not email or not credential or not credential.get("id"):
        return {"error": "Email and credential are required."}
    # Anti-hijack: a passkey may only be bound by someone holding a live Supabase
    # session for that exact email. Nobody can register a face for someone else.
    if key("SUPABASE_URL") and key("SUPABASE_ANON_KEY"):
        if not access_token:
            return {"error": "Sign in with your email and password first, then register your face/fingerprint."}
        v = supabase_auth("/auth/v1/user", access_token=access_token)
        vemail = ((v.get("data") or {}).get("email") or "").lower() if v.get("status") == 200 else ""
        if vemail != email:
            return {"error": "This session does not match that email — sign in and try again."}
    if is_blocked(email):
        return {"error": "This account is suspended by an administrator."}
    store = _passkey_load()
    recs = store.setdefault(email, [])
    recs.append({"id": credential.get("id"), "publicKey": credential.get("publicKey"),
                 "transports": credential.get("transports") or [],
                 "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())})
    try:
        with open(_passkey_file(), "w") as f:
            json.dump(store, f)
    except Exception as e:
        return {"error": f"Could not store passkey: {e}"}
    return {"status": "registered", "count": len(recs)}

def passkey_challenge():
    return {"challenge": base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")}

def passkey_login(credential_id):
    cid = (credential_id or "").strip()
    if not cid:
        return {"error": "No credential provided."}
    store = _passkey_load()
    for email, recs in store.items():
        for rec in recs:
            if rec.get("id") == cid:
                # face login may never bypass a suspension or email verification
                if is_blocked(email):
                    return {"error": "This account is suspended by an administrator."}
                if not is_verified(email):
                    return {"error": "Confirm your email first — open the verification link we sent you."}
                sup_user = _supa_admin_user(email)
                return {"status": 200, "email": email, "passkey": True,
                        "user": {"id": (sup_user or {}).get("id") or "passkey-" + email,
                                 "email": email},
                        "admin": is_admin(email), "blocked": is_blocked(email),
                        "pro_token": check_subscription(email)}
    return {"error": "No matching passkey found. Register your face/fingerprint after signing in."}

# ---------------------------------------------------------------- link tracker (visitor intelligence)
# Redirect-tracking link: when the visitor opens it, their standard request data
# (IP, device, browser, screen, language, referrer) is logged automatically and
# they are instantly redirected to the real destination site. This is the same
# mechanics as any analytics redirect (Bit.ly-style). It does NOT clone or
# impersonate any website.

def _tracker_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "trackers.json")

def _tracker_load():
    try:
        with open(_tracker_file()) as f:
            return json.load(f)
    except Exception:
        return {}

def _tracker_save(d):
    try:
        with open(_tracker_file(), "w") as f:
            json.dump(d, f)
    except Exception:
        pass

def _slugify(s):
    s = re.sub(r"[^a-z0-9-]", "", (s or "").lower())
    return s[:48] or None

def tracker_create(url, email, name="", alias=""):
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return {"error": "Enter a full URL starting with http:// or https://"}
    try:
        host = urllib.parse.urlparse(url).netloc
    except Exception:
        return {"error": "Invalid URL."}
    if not host or "." not in host:
        return {"error": "That URL has no valid domain."}
    # Professional look: the link PATH is the destination domain itself —
    # typing waptrick.com yields /t/waptrick.com, not a random token.
    al = re.sub(r"[^a-z0-9.-]", "", (alias or "").strip().lower()).strip(".-")
    base = al or re.sub(r"[^a-z0-9.-]", "", host.lower()).strip(".-") or \
           _slugify(name) or "link"
    base = (base[:48] or "link")
    slug = base
    d = _tracker_load()
    i = 0
    while slug in d:
        i += 1
        slug = base + "-" + str(i)
    d[slug] = {"slug": slug,
               "target": url,
               "name": (name or "").strip() or host,
               "creator": (email or "").strip().lower(),
               "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
               "visits": []}
    _tracker_save(d)
    return {"slug": slug, "url": "/t/" + slug, "target": url,
            "note": "This IS the link you asked for — " + ("exactly " + url if True else url) +
                    " with a tracker wrapped around it. When someone opens it, their visit is "
                    "logged and they land on the real site instantly."}

def tracker_hit(slug, ip, ua, referer):
    d = _tracker_load()
    rec = d.get(slug)
    if not rec:
        return None
    entry = {"ip": ip or "unknown", "ua": ua or "", "device": _parse_ua(ua),
             "geo": _geo_ip(ip) if ip else {}, "referer": referer or "",
             "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
             "vid": _token(12)}
    rec.setdefault("visits", []).append(entry)
    if len(rec["visits"]) > 500:
        rec["visits"] = rec["visits"][-500:]
    _tracker_save(d)
    try:
        _track_alert(rec, entry)
    except Exception:
        pass
    return entry

def tracker_ping(slug, vid, body):
    d = _tracker_load()
    rec = d.get(slug)
    if not rec:
        return {"error": "tracker not found"}
    for v in reversed(rec.get("visits", [])):
        if v.get("vid") == vid:
            v["screen"] = {"w": body.get("screen_w"), "h": body.get("screen_h")}
            v["tz"] = body.get("tz") or ""
            v["lang"] = body.get("lang") or ""
            v["platform"] = body.get("platform") or ""
            v["cores"] = body.get("cores")
            v["memory"] = body.get("memory")
            v["touch"] = body.get("touch")
            _tracker_save(d)
            return {"status": "ok"}
    return {"status": "ok"}

def tracker_list(email):
    email = (email or "").strip().lower()
    d = _tracker_load()
    out = []
    for slug, rec in d.items():
        if rec.get("creator") == email or is_admin(email):
            out.append({"slug": slug, "name": rec.get("name"), "target": rec.get("target"),
                        "created_at": rec.get("created_at"),
                        "visits": len(rec.get("visits", []))})
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"links": out}

def tracker_visits(email, slug):
    email = (email or "").strip().lower()
    d = _tracker_load()
    rec = d.get(slug)
    if not rec:
        return {"error": "Link not found."}
    if rec.get("creator") != email and not is_admin(email):
        return {"error": "You don't own this link."}
    visits = rec.get("visits", [])[-200:]
    visits.reverse()
    return {"slug": slug, "name": rec.get("name"), "target": rec.get("target"),
            "created_at": rec.get("created_at"), "visits": visits}

def tracker_page(slug):
    d = _tracker_load()
    rec = d.get(slug)
    if not rec:
        return ("<!doctype html><html><head><meta charset='utf-8'><title>Link not found</title></head>"
                "<body style='background:#04070d;color:#d9f6ff;font-family:system-ui'>"
                "<h3>Link not found or expired.</h3></body></html>")
    target = rec.get("target", "https://google.com")
    return ("""<!doctype html>
<html><head><meta charset="utf-8"><title>""" + html.escape(rec.get("name") or "Redirecting") + """</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<noscript><meta http-equiv="refresh" content="0; url=""" + html.escape(target) + """></noscript>
<style>body{background:#04070d;color:#7ea6b8;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}div{text-align:center}</style>
</head><body><div>Opening """ + html.escape(rec.get("name") or "link") + """…</div>
<script>
(function(){
  var vid = """ + json.dumps(rec["visits"][-1]["vid"]) + """;
  var data = {
    slug: """ + json.dumps(slug) + """, vid: vid,
    screen_w: screen.width, screen_h: screen.height,
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone||'',
    lang: (navigator.language||''),
    platform: (navigator.platform||''),
    cores: navigator.hardwareConcurrency||0,
    memory: navigator.deviceMemory||0,
    touch: ('ontouchstart' in window)||(navigator.maxTouchPoints>0)
  };
  try{ fetch('/api/tracker/ping',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); }catch(e){}
  setTimeout(function(){ location.replace(""" + json.dumps(target) + """); }, 60);
})();
</script></body></html>""")

# ---------------------------------------------------------------- consent-based investigator link
# A clearly-disclosed link. NOTHING is recorded until the visitor explicitly
# taps "Share my info". Camera requires the browser's own permission prompt.

def _consent_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "consent_links.json")

def _consent_load():
    try:
        with open(_consent_file()) as f:
            return json.load(f)
    except Exception:
        return {}

def _consent_save(d):
    try:
        with open(_consent_file(), "w") as f:
            json.dump(d, f)
    except Exception:
        pass

def _token(n=24):
    return base64.urlsafe_b64encode(os.urandom(n)).decode().rstrip("=")

def _geo_ip(ip):
    try:
        _, raw, _ = http_fetch("https://ipwho.is/" + urllib.parse.quote(ip), timeout=12)
        g = json.loads(raw)
        if g.get("success") is not False and g.get("ip"):
            return {"city": g.get("city"), "region": g.get("region"),
                    "country": g.get("country"), "lat": g.get("latitude"),
                    "lon": g.get("longitude"),
                    "isp": (g.get("connection") or {}).get("isp"),
                    "timezone": (g.get("timezone") or {}).get("id") if isinstance(g.get("timezone"), dict) else g.get("timezone")}
    except Exception:
        pass
    return {}

def _parse_ua(ua):
    ua = (ua or "")
    ua_l = ua.lower()
    osn = "Unknown"
    for key, name in [("iphone", "iOS"), ("ipad", "iPadOS"), ("android", "Android"),
                      ("windows", "Windows"), ("mac os x", "macOS"), ("linux", "Linux")]:
        if key in ua_l:
            osn = name; break
    browser = "Unknown"
    for key, name in [("edg/", "Edge"), ("opr/", "Opera"), ("chrome/", "Chrome"),
                      ("firefox/", "Firefox"), ("safari/", "Safari")]:
        if key in ua_l:
            browser = name; break
    device = "Desktop"
    if "iphone" in ua_l:
        device = "Phone"
    elif "ipad" in ua_l:
        device = "Tablet"
    elif "android" in ua_l:
        device = "Tablet" if "mobile" not in ua_l else "Phone"
    elif "mobile" in ua_l:
        device = "Phone"
    return {"browser": browser, "os": osn, "device": device}

def consent_create(email, purpose, label):
    purpose = (purpose or "").strip()[:300]
    label = (label or "").strip()[:120]
    if not purpose:
        return {"error": "Describe the purpose of the link (this is shown to the person)."}
    token = _token()
    d = _consent_load()
    d[token] = {"token": token,
                "creator": (email or "").strip().lower(),
                "label": label or "an investigator",
                "purpose": purpose,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                "responses": []}
    _consent_save(d)
    return {"token": token, "url": "/i/" + token,
            "note": "Send this link. The visitor sees exactly what will be shared and "
                    "must tap 'Share my info' — nothing is recorded before that."}

def consent_info(token):
    d = _consent_load()
    rec = d.get(token)
    if not rec:
        return None
    return {"label": rec.get("label"), "purpose": rec.get("purpose")}

def consent_share(token, body, ip, ua):
    d = _consent_load()
    rec = d.get(token)
    if not rec:
        return {"error": "Link not found or expired."}
    photo = (body or {}).get("photo_b64") or ""
    entry = {
        "ip": ip or "unknown",
        "ua": ua or "",
        "device": _parse_ua(ua),
        "geo": _geo_ip(ip) if ip else {},
        "screen": {"w": body.get("screen_w"), "h": body.get("screen_h")} if body else {},
        "tz": body.get("tz") or "",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "photo": None,
    }
    if photo:
        try:
            raw = base64.b64decode(photo.split(",")[-1])
            if len(raw) <= 3 * 1024 * 1024:
                pdir = os.path.join(DATA_DIR, "consent_photos")
                os.makedirs(pdir, exist_ok=True)
                fname = token + "_" + str(int(time.time())) + ".jpg"
                with open(os.path.join(pdir, fname), "wb") as f:
                    f.write(raw)
                entry["photo"] = "/consent-photo/" + fname
        except Exception:
            entry["photo"] = None
    rec.setdefault("responses", []).append(entry)
    _consent_save(d)
    return {"status": "recorded", "ref": "R-" + token[:8].upper(),
            "shared": ["IP address", "approximate location", "device & browser",
                       "screen size", "time"] + (["one photo"] if photo else [])}

def consent_list(email):
    email = (email or "").strip().lower()
    d = _consent_load()
    out = []
    for t, rec in d.items():
        if rec.get("creator") == email or is_admin(email):
            out.append({"token": t, "label": rec.get("label"), "purpose": rec.get("purpose"),
                        "created_at": rec.get("created_at"),
                        "responses": len(rec.get("responses", []))})
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"links": out}

def consent_results(email, token):
    email = (email or "").strip().lower()
    d = _consent_load()
    rec = d.get(token)
    if not rec:
        return {"error": "Link not found."}
    if rec.get("creator") != email and not is_admin(email):
        return {"error": "You don't own this link."}
    return {"token": token, "label": rec.get("label"), "purpose": rec.get("purpose"),
            "created_at": rec.get("created_at"), "responses": rec.get("responses", [])}

def consent_page(token):
    info = consent_info(token)
    if not info:
        return ("<!doctype html><html><head><meta charset='utf-8'><title>Link not found</title>"
                "<style>body{background:#04070d;color:#d9f6ff;font-family:system-ui;display:flex;"
                "align-items:center;justify-content:center;height:100vh;margin:0}div{text-align:center}"
                "</style></head><body><div><h2>Link not found</h2><p>This consent link is invalid or has expired.</p></div></body></html>")
    label = html.escape(info.get("label") or "an investigator")
    purpose = html.escape(info.get("purpose") or "")
    return ("""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Consent to share</title>
<style>
 body{background:#04070d;color:#d9f6ff;font-family:system-ui;margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh}
 .card{max-width:460px;width:92%;background:rgba(7,22,32,.7);border:1px solid rgba(0,229,255,.25);border-radius:16px;padding:26px;box-shadow:0 0 30px rgba(0,229,255,.12)}
 h1{font-size:18px;letter-spacing:2px;color:#7df9ff;margin:0 0 4px}
 .sub{font-size:12px;color:#7ea6b8;margin-bottom:18px}
 .tri{width:44px;height:44px;border:1px solid rgba(255,194,75,.6);border-radius:10px;display:flex;align-items:center;justify-content:center;margin-bottom:14px}
 .tri svg{filter:drop-shadow(0 0 8px rgba(255,194,75,.5))}
 .dis{background:rgba(255,194,75,.08);border:1px solid rgba(255,194,75,.3);border-radius:10px;padding:12px;font-size:13px;line-height:1.5;margin-bottom:16px}
 .dis b{color:#ffd98a}
 ul{margin:8px 0 0 18px;color:#cfe9f5}
 .btn{display:block;width:100%;padding:13px;border:0;border-radius:10px;font-size:15px;font-weight:700;cursor:pointer;background:linear-gradient(90deg,#00e5ff,#2f6bff);color:#04121c;margin-top:8px}
 .btn.ghost{background:transparent;color:#7ea6b8;border:1px solid rgba(0,229,255,.3);font-weight:400}
 .ok{color:#3dffa5;font-size:13px;margin-top:12px;text-align:center}
 .note{font-size:11px;color:#5d7b8c;margin-top:16px;text-align:center;line-height:1.5}
 video{width:100%;border-radius:10px;margin-top:10px;display:none}
</style></head><body>
<div class="card">
 <div class="tri"><svg width="30" height="30" viewBox="0 0 30 30"><polygon points="15,5 26,25 4,25" fill="none" stroke="#ffc24b" stroke-width="1.6"/><polygon points="15,11 22,23 8,23" fill="#ffc24b" opacity=".8"/></svg></div>
 <h1>CONSENT TO SHARE</h1>
 <div class="sub">A request from """ + label + """</div>
 <div class="dis"><b>Why you received this:</b> """ + purpose + """</div>
 <div class="dis">
   <b>If you tap "Share my info", the following will be sent to the requester:</b>
   <ul>
     <li>Your IP address & approximate location</li>
     <li>Your device type, browser & screen size</li>
     <li>The exact time you shared</li>
     <li>A photo — only if you separately allow your camera</li>
   </ul>
   <b>Nothing is collected until you tap the button.</b> If you don't want to share, simply close this page — no data is sent.
 </div>
 <button class="btn" id="share">Share my info</button>
 <button class="btn ghost" id="photo" style="display:none">📷 Share a photo (optional — uses your camera)</button>
 <video id="cam" autoplay playsinline muted></video>
 <button class="btn ghost" id="capture" style="display:none">Capture & send</button>
 <div class="ok" id="done"></div>
 <div class="note">This page will only record the data listed above, with your consent. It is not affiliated with any impersonated website.</div>
</div>
<script>
var token = """ + json.dumps(token) + """;
var shared = false, stream = null;
function post(body){ return fetch('/api/consent/share',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json()); }
document.getElementById('share').onclick = async function(){
  if(shared) return;
  shared = true;
  var r = await post({token: token, screen_w: screen.width, screen_h: screen.height, tz: Intl.DateTimeFormat().resolvedOptions().timeZone||''});
  document.getElementById('share').style.display='none';
  document.getElementById('photo').style.display='block';
  document.getElementById('done').textContent = '✓ Shared (' + (r.ref||'') + ')';
};
document.getElementById('photo').onclick = async function(){
  try{
    stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:'user'}});
    var v = document.getElementById('cam'); v.srcObject = stream; v.style.display='block';
    document.getElementById('capture').style.display='block'; document.getElementById('photo').style.display='none';
  }catch(e){ document.getElementById('done').textContent = 'Camera unavailable or declined — nothing sent.'; }
};
document.getElementById('capture').onclick = async function(){
  var v = document.getElementById('cam');
  var c = document.createElement('canvas'); c.width = v.videoWidth||640; c.height = v.videoHeight||480;
  c.getContext('2d').drawImage(v,0,0,c.width,c.height);
  var data = c.toDataURL('image/jpeg', 0.6);
  if(stream){ stream.getTracks().forEach(function(t){ t.stop(); }); }
  v.style.display='none'; document.getElementById('capture').style.display='none';
  var r = await post({token: token, photo_b64: data});
  document.getElementById('done').textContent = '✓ Photo shared with your consent (' + (r.ref||'') + ')';
};
</script></body></html>""")

# ---------------------------------------------------------------- HTTP server

# Requests a suspended account may still make: authentication, its own status,
# fine payment and payment confirmation callbacks. Everything else → 403.
BLOCK_EXEMPT_PREFIXES = ("/api/auth/", "/api/block/", "/api/paystack/", "/api/pay/crypto/status",
                         "/api/pay/crypto/check", "/api/pay/crypto/postback", "/api/supabase/status")


class Handler(BaseHTTPRequestHandler):
    server_version = "OraCool/2.0"
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        pass

    def _sse_begin(self):
        """patch40: open the event-stream early (before slow tools such as the site builder run) so the
        browser gets headers within a second and keep-alive pings while OraCool works — no 45s timeout."""
        if getattr(self, "_sse_on", False):
            return
        self._sse_on = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(b": ok\n\n")
            self.wfile.flush()
        except Exception:
            pass

    def _send_json(self, obj, status=200):
        if getattr(self, "_sse_on", False):
            # the stream is already open: deliver the payload as SSE frames instead of a second HTTP response
            try:
                if isinstance(obj, dict) and status >= 400 and not obj.get("error"):
                    obj = dict(obj, error=obj.get("message") or ("HTTP %d" % status))
                self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode("utf-8"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception:
                pass
            return
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        # patch25: text app assets must never be stale-cached (phone browsers
        # otherwise keep serving the previous build for days after a deploy)
        if "html" in ctype or "css" in ctype or "javascript" in ctype or "json" in ctype:
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, text):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _auth(self, body):
        """Return PRO payload if valid JWT present, else None."""
        token = body.get("token") or ""
        if not token:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
        if not token:
            return None
        return verify_jwt(token, key("JWT_SECRET") or "dev-secret")

    def _require_pro(self, body):
        payload = self._auth(body)
        if payload:
            tier = payload.get("tier") or "pro"
            if payload.get("admin"):
                tier = "enterprise"
            if tier_gte(tier, "pro"):
                return payload
        self._send_json({"locked": True,
                         "message": "This is a PRO feature. Upgrade to unlock the full intelligence suite."}, 402)
        return None

    def _require_tier(self, body, required):
        payload = self._auth(body)
        # A blocked account is a free account — everywhere, instantly, no exceptions.
        _bl = (payload.get("sub") if payload else None) or (body.get("email") or "").strip()
        if _bl and is_blocked(_bl):
            self._send_json({"locked": True, "suspended": True,
                             "message": "Your account is suspended by an administrator."}, 403)
            return None
        tier = None
        if payload:
            tier = payload.get("tier") or "pro"
            if payload.get("admin"):
                tier = "enterprise"
        else:
            email = (body.get("email") or "").strip()
            tier = check_tier(email) if email else "free"
        if tier_gte(tier, required):
            return tier
        self._send_json({"locked": True, "tier": tier, "plan": required,
                         "message": f"This tool requires the {required.title()} plan (you are on {tier.title()}). Every higher plan includes it too."}, 402)
        return None

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        # ---- patch27: published sites — https://<sub>.oracoolai.com or /sites/<sub>/
        try:
            _ph = (self.headers.get("Host") or "").split(":")[0].strip().lower()
            if _ph.endswith(".oracoolai.com"):
                _cand = _ph[: -len(".oracoolai.com")]
                if _sub_valid(_cand):
                    if site_exists(_cand):
                        site_serve(self, _cand, path or "/")
                    else:
                        self.send_error(404, "no site published at that subdomain")
                    return
            elif _ph and _ph not in ("oracoolai.com", "www.oracoolai.com") and "." in _ph \
                    and not _ph.endswith(("onrender.com", "render.com", "e2b.app", "localhost", "127.0.0.1")):
                _csub = site_host_lookup(_ph)
                if _csub:
                    site_serve(self, _csub, path or "/")
                    return
            elif path.startswith("/sites/"):
                _seg = path[len("/sites/"):].split("/", 1)
                if _seg and _sub_valid(_seg[0]):
                    if site_exists(_seg[0]):
                        site_serve(self, _seg[0], ("/" + _seg[1]) if len(_seg) > 1 else "/")
                    else:
                        self.send_error(404, "no site published at that address")
                    return
        except Exception:
            pass
        if path == "/":
            self._send_file(os.path.join(BASE_DIR, "landing.html"), "text/html; charset=utf-8")
        elif path in ("/app", "/app/", "/index.html"):
            self._send_file(os.path.join(BASE_DIR, "index.html"), "text/html; charset=utf-8")
        elif path in ("/communications.js", "/console-layout.css"):
            self._send_file(os.path.join(BASE_DIR, path[1:]), "text/javascript" if path.endswith(".js") else "text/css")
        elif path in ("/privacy", "/privacy.html"):
            self._send_file(os.path.join(BASE_DIR, "privacy.html"), "text/html; charset=utf-8")
        elif path == "/oauth":
            self._send_file(os.path.join(BASE_DIR, "oauth.html"), "text/html; charset=utf-8")
        elif path == "/manifest.json":
            self._send_file(os.path.join(BASE_DIR, "manifest.json"), "application/json")
        elif path in _BRAND_ASSETS:
            _asset = os.path.join(BASE_DIR, path[1:])
            if os.path.exists(_asset):
                self._send_file(_asset, _BRAND_ASSETS[path])
            else:
                self.send_error(404)
        elif path == "/voice-reminders.js":
            self._send_file(os.path.join(BASE_DIR, "voice-reminders.js"), "text/javascript")
        elif path == "/apps.js":
            self._send_file(os.path.join(BASE_DIR, "apps.js"), "text/javascript")
        elif path == "/md.js":
            self._send_file(os.path.join(BASE_DIR, "md.js"), "text/javascript")
        elif path == "/sw.js":
            self._send_file(os.path.join(BASE_DIR, "sw.js"), "text/javascript")
        elif path.startswith("/builds/"):
            _rel = urllib.parse.unquote(path[len("/builds/"):])
            _base = os.path.normpath(_BUILDS_DIR)
            _full = os.path.normpath(os.path.join(_base, _rel))
            if not _full.startswith(_base):
                self.send_error(404); return
            if not os.path.exists(_full) and _rel.split("/", 1)[0]:
                build_hydrate(urllib.parse.unquote(_rel.split("/", 1)[0]))
                _full = os.path.normpath(os.path.join(_base, _rel))
            if _rel.endswith("/download.zip") or _rel.endswith(".zip"):
                _z = build_zip(_rel.rsplit("/", 1)[0].rsplit(".zip", 1)[0])
                if _z:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", 'attachment; filename="' + _rel.rsplit("/", 1)[0] + '.zip"')
                    self.send_header("Content-Length", str(len(_z)))
                    self.end_headers()
                    self.wfile.write(_z)
                else:
                    self.send_error(404)
                return
            # patch26: manifest for the in-chat code workspace (file list + sizes)
            if _rel.endswith("manifest.json") and "/" not in _rel[:-len("manifest.json")].rstrip("/"):
                _bdir = os.path.dirname(_full)
                if not os.path.isdir(_bdir):
                    self.send_error(404)
                    return
                _mfiles = []
                if os.path.isdir(_bdir):
                    for _mroot, _mdirs, _mnames in os.walk(_bdir):
                        for _mn in _mnames:
                            _mf = os.path.join(_mroot, _mn)
                            _mrp = os.path.relpath(_mf, _bdir).replace(os.sep, "/")
                            if _mrp == "manifest.json" or _mrp.endswith(".zip"):
                                continue
                            try:
                                _mfiles.append({"path": _mrp, "size": os.path.getsize(_mf)})
                            except OSError:
                                pass
                _mfiles.sort(key=lambda f: f["path"])
                _mj = json.dumps({"ok": True, "slug": _rel[:-len("manifest.json")].rstrip("/"),
                                  "files": _mfiles, "total": sum(f["size"] for f in _mfiles)})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(_mj)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(_mj.encode("utf-8"))
                return
            if os.path.isdir(_full):
                _full = os.path.join(_full, "index.html")
            if os.path.exists(_full) and os.path.isfile(_full):
                _ext = os.path.splitext(_full)[1].lower()
                _ct = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript",
                       ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
                       ".svg": "image/svg+xml", ".ico": "image/x-icon"}.get(_ext, "application/octet-stream")
                self._send_file(_full, _ct)
            elif _rel == "" or _rel.endswith("/") or _full.endswith(".html"):
                # pre-vault builds get a friendly page so chat iframes explain
                # what happened instead of showing a blank dead frame
                _gone = ("<!doctype html><meta charset=utf-8><title>Preview archived</title>"
                         "<body style='margin:0;min-height:100vh;display:flex;align-items:center;"
                         "justify-content:center;background:#0a0d14;color:#e8ecf8;"
                         "font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif'>"
                         "<div style='max-width:460px;padding:36px 28px;text-align:center;"
                         "border:1px solid rgba(120,150,255,.25);border-radius:18px;"
                         "background:rgba(18,23,38,.9)'>"
                         "<div style='font-size:34px'>&#9203;</div>"
                         "<h2 style='margin:10px 0 6px;font-size:19px'>This preview predates the vault</h2>"
                         "<p style='margin:0;color:#93a0c0'>OraCool now auto-saves every build forever, but this one "
                         "was created before that shipped. Ask OraCool in chat to rebuild it &mdash; "
                         "it will reappear here and stay through every update.</p></div></body>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(_gone)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(_gone.encode("utf-8"))
            else:
                self.send_error(404)
        elif path.startswith("/generated/"):
            _gn = os.path.basename(path)
            _gp = os.path.join(BASE_DIR, "data", "generated", _gn)
            if _gn and os.path.exists(_gp):
                _ct = {"mp4": "video/mp4", "mp3": "audio/mpeg", "wav": "audio/wav",
                       "jpg": "image/jpeg", "png": "image/png"}.get(
                    _gn.rsplit(".", 1)[-1].lower() if "." in _gn else "", "application/octet-stream")
                self._send_file(_gp, _ct)
            else:
                self.send_error(404)
        elif path.startswith("/t/"):
            slug = path.split("/")[-1]
            ip = self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]
            tracker_hit(slug, ip, self.headers.get("User-Agent", ""),
                        self.headers.get("Referer", ""))
            self._send_html(tracker_page(slug))
        elif path.startswith("/hook/"):
            tok = path[len("/hook/"):].split("?")[0].strip("/")
            em = hook_owner(tok)
            if not em:
                self._send_json({"error": "unknown hook url"}, 404)
            else:
                try:
                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    src = (q.get("source") or ["external app"])[0][:60]
                    txt = (q.get("text") or q.get("body") or q.get("message") or ["(no text)"])[0]
                    ttl = (q.get("title") or ["Hook event · " + src])[0]
                    if not _gw_rate(em):
                        self._send_json({"error": "rate limit"}, 429)
                    else:
                        self._send_json(emit_event(em, "gateway", ttl[:160], str(txt)[:600], src))
                except Exception as e:
                    self._send_json({"error": str(e)[:120]}, 500)
        elif path.startswith("/i/"):
            self._send_html(consent_page(path.split("/")[-1]))
        elif path.startswith("/consent-photo/"):
            fname = path.split("/")[-1]
            pdir = os.path.join(DATA_DIR, "consent_photos")
            full = os.path.join(pdir, os.path.basename(fname))
            if os.path.exists(full):
                self._send_file(full, "image/jpeg")
            else:
                self.send_error(404)
        elif path.startswith("/media/"):
            _parts = path[len("/media/"):].split("/")
            if (len(_parts) == 2 and re.fullmatch(r"[a-z0-9_]{1,64}", _parts[0])
                    and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", _parts[1])):
                _full = os.path.join(MEDIA_DIR, _parts[0], _parts[1])
                _ext = _parts[1].rsplit(".", 1)[-1].lower()
                _ct = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
                       "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                       "webp": "image/webp", "gif": "image/gif"}.get(_ext, "application/octet-stream")
                if os.path.exists(_full):
                    self._send_file(_full, _ct)
                else:
                    self.send_error(404)
            else:
                self.send_error(404)
        elif path == "/api/health":
            self._send_json({"status": "online", "name": "OraCool AI", "version": "2.0", "build": "patch46-durable",
                             "persist": ("cloud" if _PERSIST.get("enabled") else "local"),
                             "time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())})
        elif path == "/api/config":
            self._send_json(get_config())
        elif path == "/api/auth/providers":
            self._send_json(oauth_providers())
        else:
            self.send_error(404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        # Paystack webhook: needs the raw body for HMAC verification, so handle
        # it before the generic JSON reader consumes the stream.
        if path == "/api/paystack/webhook":
            self._paystack_webhook()
            return
        body = self._read_json()
        body.pop("_verified_email", None)
        # ---- suspended accounts: locked out of everything except signing in,
        # reading their own status and paying the reinstatement fine.
        if not path.startswith(BLOCK_EXEMPT_PREFIXES):
            _who = request_identity(self, body) or (body.get("email") or "").strip().lower()
            if _who and is_blocked(_who):
                self._send_json({"error": "This account is suspended by an administrator. Access is locked until an administrator lifts the block.",
                                 "suspended": True, "blocked": True}, 403)
                return
        protected = path.startswith(("/api/chat", "/api/alerts/", "/api/media/", "/api/voice/", "/api/comms/", "/api/community/", "/api/builds", "/api/sites", "/api/coins", "/api/github")) or path in ("/api/image", "/api/video")
        if protected:
            em = request_identity(self, body, require_supabase=path.startswith(("/api/voice/", "/api/comms/")))
            if not em:
                self._send_json({"error": "Sign in to access your account.", "auth_required": True}, 401)
                return
            if path.startswith("/api/comms/") and not communications_allowed(em):
                self._send_json({"error": "Communications (email) require Enterprise or administrator access.",
                                 "plan": "enterprise", "locked": True}, 403)
                return
            if is_blocked(em):
                self._send_json({"error": "Account suspended."}, 403)
                return
            body["email"] = body["_verified_email"] = em
        try:
            # ---- professional audit trail: every investigative lookup is logged (who/what/when)
            if path.startswith(("/api/osint/", "/api/pro/", "/api/evidence/", "/api/case/",
                                "/api/watch/", "/api/trace/", "/api/image", "/api/video")):
                _ae = (body.get("email") or "").strip().lower()
                if not _ae and (body.get("token") or "").strip():
                    _ae = ((verify_jwt(body["token"].strip(), key("JWT_SECRET") or "dev-secret")) or {}).get("sub", "")
                _tail = path.split("/")[3] if path.count("/") > 3 else path.split("/")[-1]
                audit_log(_ae, path.replace("/api/", ""), _tail)
            if path.startswith("/api/community/"):
                self._send_json(community_route(path[len("/api/community/"):], body,
                                                self.headers.get("Host") or ""))
            elif path.startswith("/api/comms/"):
                service = communication_service(); owner = body["email"]
                action = path[len("/api/comms/"):]
                if action == "state": result = service.listing(owner)
                elif action == "preview": result = service.preview(owner, body)
                elif action == "send": result = service.send(owner, body)
                elif action == "verify/send": result = service.verify_send(owner, body, self.client_address[0] if self.client_address else "")
                elif action == "verify/check": result = service.verify_check(owner, body)
                elif action == "remove": result = service.remove(owner, str(body.get("id") or ""))
                else: result = {"error":"Unknown communications action."}
                self._send_json(result)
            elif path == "/api/voice/transcribe":
                self._send_json(voice_transcribe(body, body["email"]))
            # ---- OSINT (patch38: Starter and above — the Free tier has no OSINT tools)
            elif path == "/api/osint/ip":
                if self._require_tier(body, "starter"):
                    self._send_json(osint_ip(body.get("ip")))
            elif path == "/api/osint/domain":
                if self._require_tier(body, "starter"):
                    self._send_json(osint_domain(body.get("domain")))
            elif path == "/api/osint/email":
                # A caller-supplied HaveIBeenPwned key is an administrator override;
                # everyone else uses the server's key (or none) — see _sanitize_overrides.
                if self._require_tier(body, "starter"):
                    self._sanitize_overrides(body)
                    self._send_json(osint_email(body.get("email"), body.get("hibp_key")))
            elif path == "/api/osint/username":
                if self._require_tier(body, "starter"):
                    self._send_json(osint_username(body.get("username")))
            elif path == "/api/osint/darkweb":
                if self._require_tier(body, "starter"):
                    self._send_json(osint_darkweb(body.get("target") or body.get("query")))
            # ---- investigation platform (PRO+): cases, evidence, extraction, wallets, watch ----
            elif path == "/api/case/create":
                if self._require_tier(body, "pro"):
                    self._send_json(case_create(body.get("email"), body.get("name"), body.get("notes")))
            elif path == "/api/case/list":
                if self._require_tier(body, "pro"):
                    self._send_json(case_list(body.get("email")))
            elif path == "/api/case/note":
                if self._require_tier(body, "pro"):
                    self._send_json(case_note(body.get("email"), body.get("case"), body.get("notes")))
            elif path == "/api/case/close":
                if self._require_tier(body, "pro"):
                    self._send_json(case_close(body.get("email"), body.get("case")))
            elif path == "/api/evidence/add":
                if self._require_tier(body, "pro"):
                    self._send_json(evidence_add(body.get("email"), body.get("case"), body.get("title"),
                                                 body.get("content"), body.get("url", ""), body.get("meta"),
                                                 body.get("kind", "intel")))
            elif path == "/api/evidence/list":
                if self._require_tier(body, "pro"):
                    self._send_json(evidence_list(body.get("email"), body.get("case")))
            elif path == "/api/evidence/view":
                if self._require_tier(body, "pro"):
                    self._send_json(evidence_view(body.get("email"), body.get("case"), body.get("artifact")))
            elif path == "/api/evidence/verify":
                if self._require_tier(body, "pro"):
                    self._send_json(evidence_verify(body.get("email"), body.get("case"), body.get("artifact")))
            elif path == "/api/evidence/export":
                if self._require_tier(body, "pro"):
                    self._send_json(evidence_export(body.get("email"), body.get("case")))
            elif path == "/api/osint/extract":
                if self._require_tier(body, "pro"):
                    self._send_json(extract_entities(body.get("text")))
            elif path == "/api/trace/wallet":
                if self._require_tier(body, "pro"):
                    self._send_json(trace_wallet(body.get("address")))
            elif path == "/api/doc/verify":
                if self._require_tier(body, "pro"):
                    self._send_json(verify_document(body.get("type"), body.get("value")))
            elif path == "/api/media/inspect":
                if self._require_tier(body, "pro"):
                    self._send_json(media_inspect(body.get("url", ""), body.get("data_b64", "")))
            elif path == "/api/watch/add":
                if self._require_tier(body, "pro"):
                    self._send_json(watch_add(body.get("email"), body.get("term"), body.get("case", "")))
            elif path == "/api/watch/list":
                if self._require_tier(body, "pro"):
                    self._send_json(watch_list(body.get("email")))
            elif path == "/api/watch/remove":
                if self._require_tier(body, "pro"):
                    self._send_json(watch_remove(body.get("email"), body.get("id")))
            elif path == "/api/watch/check":
                if self._require_tier(body, "pro"):
                    self._send_json(watch_run_all())
            elif path == "/api/osint/phone":
                if self._require_tier(body, "starter"):
                    self._send_json(phone_intel.lookup(body.get("phone") or "",
                                                       key_lookup=key, http_fetch=http_fetch))
            elif path == "/api/weather":
                self._send_json(weather(body.get("city"), body.get("lat"), body.get("lon")))
            # ---- markets (free)
            elif path == "/api/market/stock":
                self._send_json(market_stock(body.get("symbol")))
            elif path == "/api/market/crypto":
                self._send_json(market_crypto(body.get("coin")))
            elif path == "/api/market/fred":
                self._send_json(market_fred(body.get("series")))
            # ---- space (NASA) ----
            elif path == "/api/space/apod":
                self._send_json(space_apod(body.get("date"), body.get("count", 1)))
            elif path == "/api/space/epic":
                self._send_json(space_epic())
            elif path == "/api/space/neo":
                self._send_json(space_neo())
            elif path == "/api/space/mars":
                if self._require_tier(body, "pro"):
                    self._send_json(space_mars(body.get("rover", "curiosity")))
            elif path == "/api/space/library":
                if self._require_tier(body, "pro"):
                    self._send_json(space_library(body.get("query", "earth"), body.get("page", 1)))
            # ---- cores (1,000-core intelligence map) ----
            elif path == "/api/cores":
                self._send_json(cores_list(body.get("query"), body.get("limit", 50)))
            elif path == "/api/core/route":
                self._send_json(core_route(body.get("query")))
            # ---- plans / tiers ----
            elif path == "/api/plan":
                self._send_json(plan_info(body))
            elif path == "/api/builds":
                _bact = str(body.get("action") or "").strip().lower()
                if _bact == "edit":
                    self._send_json(build_edit(body.get("email"), str(body.get("slug") or ""),
                                               str(body.get("instructions") or "")))
                elif _bact == "delete":
                    self._send_json(build_delete(body.get("email"), str(body.get("slug") or "")))
                else:
                    _bm = (body.get("message") or body.get("brief") or "").strip()
                    _bname = (body.get("name") or "").strip()
                    if not _bname:
                        _mn = _NAME_RX.search(_bm)
                        _bname = (_mn.group(1).strip(" .") if _mn else "") or "my-site"
                    self._send_json(build_site(body.get("email"), _bname, _bm or _bname))
            elif path == "/api/admin/vault":
                _ve = body.get("_verified_email") or request_identity(self, body)
                if not _ve or not is_admin(_ve):
                    self._send_json({"error": "Administrator access required."}, 403); return
                if isinstance(body.get("set"), dict) and body["set"]:
                    self._send_json({"ok": True, "keys": server_vault_set(body["set"])})
                else:
                    _vault_refresh(force=True)
                    self._send_json({"ok": True, "keys": sorted(_VAULT.keys())})
            elif path == "/api/builds/progress":
                self._send_json(build_progress(body.get("email")))
            elif path in ("/api/builds/images", "/api/images/search"):
                if not body.get("_verified_email") and not request_identity(self, body):
                    self._send_json({"error": "Sign in to access your account.", "auth_required": True}, 401); return
                self._send_json({"ok": True, "query": body.get("q") or "", "images": image_search(body.get("q") or body.get("query") or "", body.get("n") or 6)})
            elif path == "/api/builds/list":
                _bp = self._auth(body)
                self._send_json(build_list((_bp.get("sub") if _bp else None) or body.get("email")))
            elif path == "/api/sites":
                _act = str(body.get("action") or "list").strip().lower()
                _em = body.get("email") or ""
                if _act == "publish":
                    self._send_json(site_publish(_em, str(body.get("slug") or ""), str(body.get("sub") or "")))
                elif _act == "unpublish":
                    self._send_json(site_unpublish(_em, str(body.get("sub") or "")))
                elif _act == "domain":
                    self._send_json(site_domain_set(_em, str(body.get("sub") or ""), str(body.get("domain") or "")))
                else:
                    self._send_json(sites_list(_em))
            elif path == "/api/coins":
                self._send_json(coins_state(body.get("email") or ""))
            elif path == "/api/github":
                _act = str(body.get("action") or "").strip()
                _em = body.get("email") or ""
                if _act == "connect":
                    self._send_json(github_connect(_em, str(body.get("pat") or "")))
                elif _act == "status":
                    self._send_json(github_status(_em))
                elif _act == "repos":
                    self._send_json(github_repos(_em))
                elif _act == "create":
                    self._send_json(github_create_repo(_em, str(body.get("name") or ""), bool(body.get("private"))))
                elif _act == "push":
                    self._send_json(github_push_build(_em, str(body.get("slug") or ""), str(body.get("repo") or "")))
                else:
                    self._send_json({"error": "Unknown GitHub action."})
            elif path == "/api/files/analyze":
                self._send_json(analyze_file(body.get("name"), body.get("mime"), body.get("data_b64"), purpose=str(body.get("purpose") or "")))
            # ---- link tracker (visitor intelligence) ----
            elif path == "/api/tracker/create":
                self._send_json(tracker_create(body.get("url"), body.get("email"), body.get("name")))
            elif path == "/api/tracker/shorten":
                u = (body.get("url") or "").strip()
                self._send_json(shorten_url(u) if u.startswith("http") else {"ok": False})
            elif path == "/api/apps/open":
                self._send_json(open_app(body.get("name") or body.get("target")))
            elif path == "/api/tracker/ping":
                self._send_json(tracker_ping(body.get("slug"), body.get("vid"), body))
            elif path == "/api/tracker/list":
                self._send_json(tracker_list(body.get("email")))
            elif path == "/api/tracker/visits":
                self._send_json(tracker_visits(body.get("email"), body.get("slug")))
            # ---- consent-based investigator link ----
            elif path == "/api/consent/create":
                self._send_json(consent_create(body.get("email"), body.get("purpose"), body.get("label")))
            elif path == "/api/consent/share":
                ip = self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]
                self._send_json(consent_share(body.get("token"), body, ip,
                                              self.headers.get("User-Agent", "")))
            elif path == "/api/consent/list":
                self._send_json(consent_list(body.get("email")))
            elif path == "/api/consent/results":
                self._send_json(consent_results(body.get("email"), body.get("token")))
            # ---- encrypted vault (uses ENCRYPTION_KEY/IV) ----
            elif path == "/api/vault/set":
                payload = self._auth(body)
                if not payload:
                    self._send_json({"locked": True, "message": "Sign in and upgrade to use the encrypted vault."}, 403)
                    return
                self._send_json(vault_set(payload.get("sub", body.get("email", "")),
                                          body.get("key"), body.get("value")))
            elif path == "/api/vault/get":
                payload = self._auth(body)
                if not payload:
                    self._send_json({"locked": True, "message": "Sign in and upgrade to use the encrypted vault."}, 403)
                    return
                self._send_json(vault_get(payload.get("sub", body.get("email", "")),
                                          body.get("key")))
            elif path == "/api/vault/list":
                payload = self._auth(body)
                if not payload:
                    self._send_json({"locked": True, "message": "Sign in and upgrade to use the encrypted vault."}, 403)
                    return
                self._send_json(vault_list(payload.get("sub", body.get("email", ""))))
            # ---- paper trading (per-user)
            elif path == "/api/trade/account":
                email = body.get("email")
                self._send_json(account_with_quotes(email))
            elif path == "/api/trade/reset":
                email = body.get("email")
                if email and is_blocked(email):
                    self._send_json({"error": "Your account is suspended by an administrator."}, 403)
                    return
                self._send_json(reset_paper(email))
            elif path == "/api/trade/quote":
                self._send_json(paper_quote(body.get("symbol")))
            elif path == "/api/trade/order":
                email = body.get("email")
                if email and is_blocked(email):
                    self._send_json({"error": "Your account is suspended by an administrator."}, 403)
                    return
                touch_user(email) if email else None
                self._send_json(paper_order(body.get("side"), body.get("symbol"),
                                            body.get("notional"), body.get("reason", "manual"),
                                            user=email))
            elif path == "/api/trade/signal":
                self._send_json(trade_signal(body.get("symbol")))
            elif path == "/api/trade/auto":
                email = body.get("email")
                if email and is_blocked(email):
                    self._send_json({"error": "Your account is suspended by an administrator."}, 403)
                    return
                self._send_json(trade_auto(body))
            elif path == "/api/trade/auto/status":
                self._send_json(auto_status())
            elif path == "/api/trade/leaderboard":
                self._send_json(leaderboard_payload())
            # ---- Alpaca paper (real broker, paper money)
            elif path == "/api/alpaca/account":
                self._send_json(alpaca_account())
            elif path == "/api/alpaca/positions":
                self._send_json(alpaca_positions())
            elif path == "/api/alpaca/order":
                self._send_json(alpaca_order(body.get("side"), body.get("symbol"), body.get("notional")))
            # ---- Pocket Option DEMO (uses the user's own SSID)
            elif path == "/api/po/connect":
                self._send_json(pocket_option.connect(body.get("ssid")))
            elif path == "/api/po/order":
                self._send_json(pocket_option.open_deal(body.get("ssid"), body.get("asset"),
                                                        body.get("amount"), body.get("action"),
                                                        body.get("duration")))
            # ---- admin
            elif path == "/api/news":
                self._send_json(osint_news(body.get("query") or ""))
            elif path == "/api/skills/list":
                self._send_json({"skills": skills_load((body.get("email") or "").strip().lower())})
            elif path == "/api/skills/save":
                self._send_json(skill_save((body.get("email") or "").strip().lower(),
                                           body.get("name"), body.get("name"),
                                           body.get("instructions")))
            elif path == "/api/skills/delete":
                self._send_json(skill_delete((body.get("email") or "").strip().lower(),
                                              body.get("name") or ""))
            elif path == "/api/skills/share":
                self._send_json(skill_share((body.get("email") or "").strip().lower(),
                                            body.get("name") or "", bool(body.get("share", True))))
            elif path == "/api/skills/market":
                self._send_json(skills_market())
            elif path == "/api/skills/install":
                self._send_json(skill_install((body.get("email") or "").strip().lower(),
                                              body.get("name") or ""))
            elif path == "/api/pay/crypto/invoice":
                host = (self.headers.get("Host") or "").split(":")[0]
                site = (key("TRACKER_DOMAIN") or (("https://" + host) if ("." in host or host.startswith("localhost")) else "")).strip()
                if site and not site.startswith("http"):
                    site = "https://" + site
                self._send_json(crypto_invoice((body.get("email") or "").strip(),
                                               body.get("plan") or "pro", site))
            elif path == "/api/pay/crypto/status":
                self._send_json(crypto_status(body.get("ref")))
            elif path == "/api/pay/crypto/postback":
                self._send_json(crypto_postback_handle(body))
            elif path == "/api/admin/users":
                _adp = _require_admin(self, body)
                if _adp:
                    self._send_json(admin_users_payload((_adp.get("sub") or "").lower()))
            elif path == "/api/admin/revenue":
                if _require_admin(self, body):
                    self._send_json(admin_revenue_payload())
            elif path == "/api/admin/audit":
                if _require_admin(self, body):
                    d = _cases_load()
                    n = max(1, min(int(body.get("limit") or 60), 500))
                    self._send_json({"audit": list(reversed(d.get("audit", [])))[:n]})
            elif path == "/api/admin/moderation":
                if _require_admin(self, body):
                    try:
                        self._send_json(community_service().admin_overview())
                    except community.CommunitySetup:
                        self._send_json({"setup_required": True, "cases": [], "reports": [], "stats": {},
                                         "message": "Run the Patch 15 SQL in Supabase to activate community moderation."})
            elif path == "/api/admin/moderation/review":
                if _require_admin(self, body):
                    try:
                        self._send_json(community_service().admin_advisory_review(body.get("email")))
                    except community.CommunitySetup:
                        self._send_json({"error": "Community database is being set up (run the Patch 15 SQL in Supabase)."})
            elif path == "/api/admin/moderation/confirm":
                payload = _require_admin(self, body)
                if payload:
                    try:
                        self._send_json(community_service().admin_confirm(body.get("case_id"), payload.get("sub", "")))
                    except community.CommunitySetup:
                        self._send_json({"error": "Community database is being set up (run the Patch 15 SQL in Supabase)."})
            elif path == "/api/admin/moderation/overturn":
                payload = _require_admin(self, body)
                if payload:
                    try:
                        self._send_json(community_service().admin_overturn(body.get("case_id"), payload.get("sub", "")))
                    except community.CommunitySetup:
                        self._send_json({"error": "Community database is being set up (run the Patch 16 SQL in Supabase)."})
            elif path == "/api/admin/moderation/rooms/ban":
                payload = _require_admin(self, body)
                if payload:
                    try:
                        self._send_json(community_service().admin_ban_room(str(body.get("slug") or ""),
                                                                           bool(body.get("banned")),
                                                                           str(body.get("reason") or ""),
                                                                           payload.get("sub", "")))
                    except community.CommunitySetup:
                        self._send_json({"error": "Community database is being set up (run the Patch 16 SQL in Supabase)."})
            elif path == "/api/admin/block":
                payload = _require_admin(self, body)
                if payload:
                    self._send_json(block_user(body.get("email"), bool(body.get("blocked")),
                                               body.get("reason", ""), payload.get("sub", "")))
            elif path == "/api/admin/pro":
                payload = _require_admin(self, body)
                if payload:
                    self._send_json(admin_set_pro(body.get("email"), body.get("tier"),
                                                  body.get("days"), payload.get("sub", "")))
            elif path == "/api/admin/record":
                if _require_admin(self, body):
                    self._send_json(admin_user_record(body.get("email")))
            elif path == "/api/admin/reset-password":
                payload = _require_admin(self, body)
                if payload:
                    self._send_json(admin_reset_password(body.get("email"), body.get("new_password") or "",
                                                          payload.get("sub", "")))
            elif path == "/api/generate/random":
                self._send_json(generate_random(body.get("query") or ""))
            elif path == "/api/admin/delete":
                payload = _require_admin(self, body)
                if payload:
                    self._send_json(admin_delete_user(body.get("email"), payload.get("sub", "")))
            # ---- PRO tools (JWT-gated by tier; each tier inherits the ones below)
            elif path == "/api/pro/search":
                if self._require_tier(body, "starter"):
                    self._send_json(pro_search(body.get("query"), body.get("max_results", 5)))
            elif path == "/api/pro/shodan":
                if self._require_tier(body, "pro"):
                    self._send_json(pro_shodan(body.get("ip")))
            elif path == "/api/pro/virustotal":
                if self._require_tier(body, "pro"):
                    self._send_json(pro_virustotal(body.get("target"), body.get("kind", "domain")))
            elif path == "/api/pro/abuseipdb":
                if self._require_tier(body, "pro"):
                    self._send_json(pro_abuseipdb(body.get("ip"), body.get("days", 90)))
            elif path == "/api/pro/urlscan":
                if self._require_tier(body, "pro"):
                    self._send_json(pro_urlscan(body.get("url")))
            elif path == "/api/pro/leakcheck":
                if self._require_tier(body, "pro"):
                    self._send_json(pro_leakcheck(body.get("query"), body.get("type", "email")))
            # ---- media generation (image: pro+, video: ultra+)
            elif path == "/api/image":
                if self._require_tier(body, "pro"):
                    _ir = gen_image(body.get("prompt"), body.get("aspect_ratio", "1:1"))
                    if _ir.get("ok"):
                        try:
                            _ir["library"] = media_record(str(body.get("email") or "").strip().lower(),
                                                          "image", body.get("prompt"), _ir.get("images") or [],
                                                          _ir.get("provider") or "", _ir.get("model") or "")
                        except Exception:
                            pass
                    self._send_json(_ir)
            elif path == "/api/media/job/start":
                kind = "video" if body.get("kind") == "video" else "image"
                if self._require_tier(body, "ultra" if kind == "video" else "pro"):
                    self._send_json(media_job_start(body["email"], kind, body))
            elif path == "/api/media/job/status":
                self._send_json(media_job_status(body["email"], str(body.get("id") or "")))
            elif path == "/api/video":
                if self._require_tier(body, "ultra"):
                    _vr = gen_video(body.get("prompt"), body.get("duration"),
                                    bool(body.get("with_audio") or body.get("audio")))
                    if _vr.get("ok"):
                        try:
                            _vr["library"] = media_record(str(body.get("email") or "").strip().lower(),
                                                          "video", body.get("prompt"), _vr.get("videos") or [],
                                                          _vr.get("provider") or "", _vr.get("model") or "")
                        except Exception:
                            pass
                    self._send_json(_vr)
            # ---- smart home (free when linked; AI drives devices by voice)
            elif path == "/api/smart/list":
                self._send_json(smart_list(body.get("ha_url"), body.get("ha_token")))
            elif path == "/api/smart/control":
                self._send_json(smart_control(body.get("entity_id"), body.get("action"),
                                              body.get("ha_url"), body.get("ha_token")))
            # ---- payments
            elif path == "/api/pro/trial":
                # Trials were removed by the creator: no PRO access without payment.
                self._send_json({"error": "Free trials have been removed. Paid plans unlock "
                                     "instantly via Paystack — Starter $29 (₦45,000) · Pro $49 (₦75,000) · "
                                     "Professional $149 (₦230,000) · Enterprise $500 (₦750,000, every "
                                     "feature unlocked)."}, 402)
            elif path == "/api/paystack/initialize":
                self._send_json(paystack_initialize(body.get("email"),
                                                    body.get("callback_url"),
                                                    body.get("plan", "pro"),
                                                    body.get("currency")))
            elif path == "/api/paystack/verify":
                self._send_json(paystack_verify(body.get("reference")))
            # ---- supabase
            elif path == "/api/supabase/status":
                self._send_json(self._supabase_status())
            # ---- auth (Supabase GoTrue)
            elif path == "/api/auth/signup":
                self._send_json(auth_signup(body.get("email"), body.get("password"),
                                            body.get("name", ""), body.get("username") or ""))
            elif path == "/api/auth/confirm":
                self._send_json(auth_confirm(body.get("token_hash") or body.get("token")))
            elif path == "/api/auth/code/send":
                self._send_json(auth_send_code(body.get("email")))
            elif path == "/api/auth/code/verify":
                self._send_json(auth_verify_code(body.get("email"), body.get("code")))
            elif path == "/api/auth/resend":
                self._send_json(auth_resend(body.get("email")))
            elif path == "/api/auth/login":
                email = (body.get("email") or "").strip()
                r = supabase_auth("/auth/v1/token?grant_type=password", method="POST",
                                  json_body={"email": email, "password": body.get("password")})
                if r.get("status") == 200:
                    u = r.get("data", {}).get("user") or {}
                    uemail = u.get("email") or email
                    touch_user(uemail, verified=True)  # Supabase only issues a session to confirmed emails
                    try:
                        _lip = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or (self.client_address[0] if self.client_address else "")
                        _lgeo = _geo_ip(_lip) or {}
                        touch_user(uemail, last_ip=_lip)
                        emit_event(uemail, "login", "OraCool login",
                                   "IP " + (_lip or "?") + (" · " + _lgeo.get("city") if _lgeo.get("city") else "") +
                                   " · " + time.strftime("%Y-%m-%d %H:%M UTC"))
                        if is_blocked(uemail):
                            notify_admins("security", "Blocked account just logged in", uemail + " · IP " + (_lip or "?"))
                    except Exception:
                        pass
                    r["admin"] = is_admin(uemail)
                    r["blocked"] = is_blocked(uemail)
                    r["pro_token"] = check_subscription(uemail)
                    r["requires_2fa"] = twofa_enabled(uemail)
                    self._send_json(r)
                else:
                    d = r.get("data") or {}
                    msg = ((d.get("error_description") or d.get("msg")) if isinstance(d, dict)
                           else str(d)).lower()
                    if "not confirmed" in msg or "confirm" in msg or "verification" in msg:
                        self._send_json({"error": "This older account is still unconfirmed in Supabase. "
                                                 "Ask the operator to confirm the existing account in Supabase; "
                                                 "do not create a duplicate account. New signups no longer need email codes."})
                    else:
                        if not (isinstance(r.get("data"), dict) and
                               (r["data"].get("msg") or r["data"].get("error_description"))):
                            r["error"] = "Invalid email or password."
                        self._send_json(r)
            # ---- 2FA / OAuth / biometric ----
            elif path == "/api/auth/2fa/setup":
                self._send_json(twofa_setup(body.get("email")))
            elif path == "/api/auth/2fa/enable":
                self._send_json(twofa_enable(body.get("email"), body.get("code")))
            elif path == "/api/auth/2fa/status":
                self._send_json({"enabled": twofa_enabled(body.get("email"))})
            elif path == "/api/auth/2fa/verify":
                email = (body.get("email") or "").strip().lower()
                if twofa_check(email, body.get("code")):
                    touch_user(email)
                    try:
                        touch_user(email, last_ip=(self.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or (self.client_address[0] if self.client_address else ""))
                        emit_event(email, "login", "OraCool login (2FA)", time.strftime("%Y-%m-%d %H:%M UTC"))
                    except Exception:
                        pass
                    self._send_json({"status": 200, "ok": True, "admin": is_admin(email),
                                     "blocked": is_blocked(email),
                                     "pro_token": check_subscription(email)})
                else:
                    self._send_json({"status": 401, "error": "Incorrect 2FA code."})
            elif path == "/api/auth/oauth/url":
                self._send_json(oauth_url(body.get("provider"), body.get("redirect_to")))
            elif path == "/api/auth/oauth/exchange":
                self._send_json(oauth_exchange(body.get("access_token"), body.get("refresh_token")))
            elif path == "/api/auth/passkey/register":
                self._send_json(passkey_register(body.get("email"), body.get("credential"),
                                                  body.get("access_token")))
            elif path == "/api/auth/passkey/challenge":
                self._send_json(passkey_challenge())
            elif path == "/api/auth/passkey/login":
                self._send_json(passkey_login(body.get("credentialId")))
            elif path == "/api/auth/refresh":
                rt = str(body.get("refresh_token") or "")
                if not rt:
                    self._send_json({"error": "Sign in again."}, 401)
                else:
                    r = supabase_auth("/auth/v1/token?grant_type=refresh_token", method="POST",
                                      json_body={"refresh_token": rt})
                    if r.get("status") == 200:
                        em = ((r.get("data") or {}).get("user") or {}).get("email")
                        r.update(admin=is_admin(em), blocked=is_blocked(em), pro_token=check_subscription(em))
                    self._send_json(r)
            elif path == "/api/auth/me":
                token = body.get("access_token")
                if not token:
                    self._send_json({"error": "No access token."}, 400)
                    return
                r = supabase_auth("/auth/v1/user", access_token=token)
                if r.get("status") == 200:
                    u = r.get("data") or {}
                    uemail = u.get("email")
                    touch_user(uemail)
                    r["admin"] = is_admin(uemail)
                    r["blocked"] = is_blocked(uemail)
                    r["pro_token"] = check_subscription(uemail)
                self._send_json(r)
            # ---- github
            elif path == "/api/github/me":
                self._send_json(github("/user"))
            elif path == "/api/github/repos":
                self._send_json(github("/user/repos?sort=updated&per_page=20&type=all"))
            elif path == "/api/github/search":
                q = (body.get("query") or "").strip()
                if not q:
                    self._send_json({"error": "Provide a search query."}, 400)
                    return
                self._send_json(github("/search/repositories?q=" + urllib.parse.quote(q) + "&per_page=10"))
            elif path == "/api/github/rate":
                self._send_json(github("/rate_limit"))
            # ---- home assistant
            elif path == "/api/home/status":
                self._send_json(ha_request(body.get("ha_url"), body.get("ha_token"), "/api/"))
            elif path == "/api/home/states":
                self._send_json(ha_request(body.get("ha_url"), body.get("ha_token"), "/api/states"))
            elif path == "/api/home/service":
                self._send_json(ha_request(body.get("ha_url"), body.get("ha_token"),
                                           f"/api/services/{body.get('domain')}/{body.get('service')}",
                                           method="POST",
                                           json_body={"entity_id": body.get("entity_id"),
                                                      **(body.get("data") or {})}))
            # ---- connectors & alerts platform ----
            elif path == "/api/alerts/state":
                em = (body.get("email") or "").strip().lower()
                if not em:
                    self._send_json({"error": "Sign in first."})
                else:
                    st = alerts_state(em, create=True)
                    self._send_json({"connectors": [_conn_public(c) for c in (st.get("connectors") or {}).values()],
                                     "events": st.get("events"), "digest_hour": st.get("digest_hour"),
                                     "api_key_mask": st.get("api_key_mask") or "",
                                     "hook_url": "/hook/" + str(st.get("hook_token") or ""),
                                     "push_ready": _push_available(),
                                     "push_subs": len(st.get("push_subs") or []),
                                     "vapid_public": (_vapid_ensure()[1] or ""),
                                     "delivered": (st.get("delivered") or [])[-8:][::-1],
                                     "inbox": (st.get("inbox") or [])[-25:][::-1],
                                     "conn_help": {k: v["help"] for k, v in CONN_TYPES.items()},
                                     "conn_fields": {k: v["fields"] for k, v in CONN_TYPES.items()},
                                     "conn_labels": {k: v["label"] for k, v in CONN_TYPES.items()}})
            elif path == "/api/alerts/connector/save":
                em = (body.get("email") or "").strip().lower()
                ctype = (body.get("type") or "").strip().lower()
                spec = CONN_TYPES.get(ctype)
                if not spec:
                    self._send_json({"error": "Unknown connector type: " + str(ctype)})
                else:
                    cfg_in = body.get("cfg") or {}
                    cfg = {}
                    warn = ""
                    for f in spec["fields"]:
                        v = str(cfg_in.get(f) or "").strip()
                        if not v:
                            if f in spec.get("optional", []):
                                continue
                            if f in spec["secrets"] and body.get("id") and (alerts_state(em) or {}).get("connectors", {}).get(body.get("id"), {}).get("cfg", {}).get(f):
                                cfg[f] = (alerts_state(em)["connectors"][body["id"]]["cfg"] or {}).get(f)
                                continue
                            self._send_json({"error": "Missing field: " + f}); break
                        cfg[f] = _seal(v) if f in spec["secrets"] else v
                    else:
                        if ctype == "telegram":
                            try:
                                _, raw, _ = http_fetch("https://api.telegram.org/bot" + _unseal(cfg["bot_token"]) + "/getMe", method="POST", timeout=8, json_body={})
                                td = json.loads(raw or b"{}")
                                if td.get("ok"):
                                    warn = "Bot verified: @" + str((td.get("result") or {}).get("username") or "?")
                                else:
                                    warn = "Telegram rejected the token (" + str(td.get("description") or "?")[:80] + ") — saved anyway; fix token if sends fail."
                            except Exception as e:
                                warn = "Could not reach Telegram to verify (" + str(e)[:60] + ")."
                        def fn(st, _id=body.get("id"), _ctype=ctype, _name=body.get("name") or (CONN_TYPES[_ctype]["label"]), _cfg=cfg):
                            cid = _id if _id in (st.get("connectors") or {}) else "c" + os.urandom(3).hex()
                            st.setdefault("connectors", {})[cid] = {
                                "id": cid, "type": _ctype, "name": str(_name)[:40],
                                "cfg": _cfg, "enabled": True, "created": _now(),
                                "last_status": {"t": _now(), "ok": True, "err": ""}}
                            st["connectors"][cid]["last_status"]["err"] = ""
                            return {"ok": True, "id": cid, "warn": warn, "connector": _conn_public(st["connectors"][cid])}
                        self._send_json(_alerts_mutate(em, fn))
            elif path == "/api/alerts/connector/toggle":
                em = (body.get("email") or "").strip().lower()
                cid = body.get("id")
                self._send_json(_alerts_mutate(em, lambda st: {"ok": True, "enabled": (st.get("connectors", {}).get(cid) or {}).update({"enabled": bool(body.get("enabled", True))}) or True} if (st.get("connectors") or {}).get(cid) else {"error": "no such connector"}))
            elif path == "/api/alerts/connector/delete":
                em = (body.get("email") or "").strip().lower()
                cid = body.get("id")
                def fn(st):
                    (st.get("connectors") or {}).pop(cid, None)
                    return {"ok": True, "deleted": cid}
                self._send_json(_alerts_mutate(em, fn))
            elif path == "/api/alerts/connector/test":
                em = (body.get("email") or "").strip().lower()
                self._send_json(alerts_test_all(em, body.get("id")))
            elif path == "/api/alerts/settings":
                em = (body.get("email") or "").strip().lower()
                evs = body.get("events") or {}
                def fn(st):
                    for k, v in evs.items():
                        if k in st.get("events", {}):
                            st["events"][k] = bool(v)
                    if body.get("digest_hour") is not None:
                        try:
                            st["digest_hour"] = max(0, min(23, int(body.get("digest_hour"))))
                        except Exception:
                            pass
                    return {"ok": True, "events": st["events"], "digest_hour": st["digest_hour"]}
                self._send_json(_alerts_mutate(em, fn))
            elif path == "/api/push/vapid":
                self._send_json({"public_key": _vapid_ensure()[1] or "", "available": _push_available()})
            elif path == "/api/push/subscribe":
                em = (body.get("email") or "").strip().lower()
                sub = body.get("subscription") or {}
                if not (em and sub.get("endpoint") and isinstance(sub.get("keys"), dict)):
                    self._send_json({"error": "need email + subscription {endpoint,keys}"})
                else:
                    def fn(st):
                        subs = [x for x in st.get("push_subs", []) if x.get("endpoint") != sub["endpoint"]]
                        subs.append({"endpoint": sub["endpoint"], "keys": sub["keys"], "ua": (body.get("ua") or "")[:120], "added": _now()})
                        st["push_subs"] = subs[-8:]
                        return {"ok": True, "count": len(st["push_subs"])}
                    self._send_json(_alerts_mutate(em, fn))
            elif path == "/api/push/unsubscribe":
                em = (body.get("email") or "").strip().lower()
                ep = body.get("endpoint") or ""
                def fn(st):
                    st["push_subs"] = [x for x in st.get("push_subs", []) if x.get("endpoint") != ep]
                    return {"ok": True, "count": len(st["push_subs"])}
                self._send_json(_alerts_mutate(em, fn))
            elif path == "/api/gateway/key/rotate":
                em = (body.get("email") or "").strip().lower()
                self._send_json(gateway_issue_key(em))
            elif path == "/api/gateway/v1/ingest":
                kstr = (self.headers.get("X-OraCool-Key") or body.get("key") or "").strip()
                if kstr.startswith("Bearer "):
                    kstr = kstr[7:]
                owner = gateway_auth(kstr)
                if not owner:
                    self._send_json({"error": "invalid api key", "hint": "Devices → Connectors & Alerts → create/rotate your API key."}); return
                if not _gw_rate(owner):
                    self._send_json({"error": "rate limit — 120 events/min"}); return
                title = body.get("title") or ("Event from " + str(body.get("source") or "external app"))
                text = body.get("body") or body.get("text") or body.get("message") or json.dumps({k: v for k, v in body.items() if k not in ("key",)})[:500]
                etype = "gateway"
                self._send_json(emit_event(owner, etype, str(title)[:160], str(text)[:600], body.get("source")))
            elif path == "/api/gateway/v1/events":
                kstr = (self.headers.get("X-OraCool-Key") or body.get("key") or "").strip()
                if kstr.startswith("Bearer "):
                    kstr = kstr[7:]
                owner = gateway_auth(kstr)
                if not owner:
                    self._send_json({"error": "invalid api key"}); return
                st = alerts_state(owner) or {}
                self._send_json({"events": (st.get("inbox") or [])[-50:][::-1]})
            # ---- chat
            # ---- chat sessions (history sidebar) + media library
            elif path == "/api/chat/sessions":
                self._send_json({"sessions": conv_list(_body_email(self, body)),
                                 "email": _body_email(self, body), "storage": dict(_CONV_SYNC)})
            elif path == "/api/chat/session/new":
                self._send_json(conv_new(_body_email(self, body), body.get("title") or "New chat"))
            elif path == "/api/chat/session/get":
                _sess = conv_get(_body_email(self, body), str(body.get("id") or ""))
                self._send_json({"session": _sess, "messages": (_sess or {}).get("messages") or []})
            elif path == "/api/chat/session/rename":
                self._send_json(conv_rename(_body_email(self, body), str(body.get("id") or ""),
                                            body.get("title") or "New chat"))
            elif path == "/api/chat/session/delete":
                self._send_json(conv_delete(_body_email(self, body), str(body.get("id") or "")))
            elif path == "/api/media/list":
                _mi = media_list(_body_email(self, body), body.get("kind") or "")
                self._send_json({"items": _mi, "count": len(_mi)})
            elif path == "/api/media/delete":
                self._send_json(media_delete(_body_email(self, body), str(body.get("id") or "")))
            elif path.startswith("/api/admin/brand/"):
                actor = request_identity(self, body, require_supabase=True)
                if not actor or not is_admin(actor) or is_blocked(actor):
                    self._send_json({"error": "Sign in as an administrator to manage OraCool accounts."}, 403)
                elif path.endswith("/list"):
                    self._send_json({"accounts": brand_public()})
                elif path.endswith("/connect"):
                    self._send_json(brand_connect(actor, body))
                elif path.endswith("/check"):
                    self._send_json(brand_inspect(actor, str(body.get("id") or "")))
                elif path.endswith("/publish"):
                    self._send_json(brand_publish(actor, body))
                elif path.endswith("/disconnect"):
                    with _BRAND_LOCK:
                        d = _brand_data()
                        d.pop(str(body.get("id") or ""), None)
                        _brand_write(d)
                    audit_log(actor, "brand.disconnect", str(body.get("id") or ""))
                    self._send_json({"ok": True})
                else:
                    self._send_json({"error": "Unknown account action."}, 404)
            elif path == "/api/admin/logs":
                if _require_admin(self, body):
                    self._send_json({"lines": platform_logs(body.get("lines") or 160, body.get("q") or ""),
                                     "file": "data/platform.log"})
            elif path == "/api/admin/diagnostics":
                if _require_admin(self, body):
                    self._send_json(platform_diagnostics())
            elif path == "/api/admin/keys":
                # Server Key Vault status — administrators only; booleans, never values.
                if _require_admin(self, body):
                    self._send_json(admin_vault_status())
            # ---- suspended-account self-service: status + reinstatement fine
            elif path == "/api/block/status":
                who = request_identity(self, body) or (body.get("email") or "").strip().lower()
                self._send_json(block_status(who))
            elif path in ("/api/block/fine/paystack", "/api/block/fine/crypto"):
                self._send_json({"error": "Reinstatement fines are no longer collected. An administrator lifts blocks from the Admin console — the account keeps its e-mail, history and community identity."}, 410)
            elif path == "/api/pay/crypto/check":
                self._send_json(crypto_status(str(body.get("ref") or "")))
            elif path == "/api/chat":
                self._handle_chat(body)
            else:
                self.send_error(404)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _supabase_status(self):
        url = key("SUPABASE_URL")
        svc = key("SUPABASE_SERVICE_KEY")
        if not url:
            return {"configured": False}
        try:
            st, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/",
                                    headers={"apikey": svc, "Authorization": "Bearer " + svc},
                                    timeout=12)
            return {"configured": True, "reachable": True, "status": st}
        except Exception as e:
            return {"configured": True, "reachable": False, "error": str(e)[:120]}

    def _paystack_webhook(self):
        """Verify a Paystack webhook (HMAC-SHA512 of the raw body) and record
        the sale. This guarantees the subscription is saved even if the buyer
        never returns to the callback page."""
        secret = (key("PAYSTACK_TEST_SECRET") if key("PAYSTACK_TEST")
                  else key("PAYSTACK_SECRET_KEY"))
        if not secret:
            self._send_json({"status": "ignored", "message": "no secret key"}, 200)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        sig = self.headers.get("x-paystack-signature", "")
        expected = hmac_mod.new(secret.encode(), raw, hashlib.sha512).hexdigest()
        if not sig or not hmac_mod.compare_digest(sig.strip().lower(), expected.lower()):
            self._send_json({"status": "ignored", "message": "invalid signature"}, 401)
            return
        try:
            event = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json({"status": "ignored", "message": "invalid json"}, 400)
            return
        if event.get("event") == "charge.success":
            data = event.get("data") or {}
            if data.get("status") == "success":
                try:
                    record_paystack_success(data)
                except Exception:
                    pass
        self._send_json({"status": "ok"}, 200)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    # ---- chat proxy (streaming SSE) with server-default keys + fallback
    def _admin_request(self, body):
        """True only for a verified, non-blocked administrator: a server-signed
        admin token, or a signed token whose subject is in ADMIN_EMAILS.
        Client-supplied `email`/`admin` fields are never trusted."""
        payload = self._auth(body)
        if not payload:
            return False
        sub = str(payload.get("sub") or "").strip().lower()
        if not (payload.get("admin") or is_admin(sub)):
            return False
        return not (sub and is_blocked(sub))

    def _sanitize_overrides(self, body):
        """Provider overrides (own API key, custom base URL, arbitrary model,
        HIBP key) are administrator-only. For everyone else they are removed
        from the request before any provider resolution — so a non-admin can
        neither point the assistant at another endpoint nor pick a model the
        operator did not configure. Ordinary accounts may still name one of
        the server-configured models (keeps the Turbo/Smart switch working)."""
        if not isinstance(body, dict):
            return body
        if body.get("_overrides_checked"):
            return body
        body["_overrides_checked"] = True
        if self._admin_request(body):
            body["_admin_overrides"] = True
            return body
        body["_admin_overrides"] = False
        for field in ("api_key", "base_url", "hibp_key"):
            body.pop(field, None)
        model = str(body.get("model") or "").strip()
        if model and model not in user_model_allowlist():
            body.pop("model", None)
        return body

    def _next_brain(self, provider, base_url, model, tried):
        """patch46: the next healthy brain after a provider error. Groq models each carry their own daily token
        cap, so a 429 on one model rotates to the next Groq model before spilling to Agnes and OpenAI. Explicit
        'groq' requests (Fast mode) rotate too — a rate limit must never surface as a 'no credits' error."""
        gk = key("GROQ_API_KEY")
        if gk and provider in ("auto", "groq"):
            if "groq" in (base_url or ""):
                for m in _groq_chat_models():
                    if m not in tried:
                        tried.add(m)
                        return gk, "https://api.groq.com/openai/v1", m
            elif "groq" not in tried:
                tried.add("groq")
                m = KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL)
                tried.add(m)
                return gk, "https://api.groq.com/openai/v1", m
        ak = key("AGNES_API_KEY")
        if ak and provider in ("auto", "groq") and "agnes" not in tried and "agnes-ai" not in (base_url or ""):
            tried.add("agnes")
            return ak, AGNES_BASE, KEYS.get("AGNES_MODEL", "agnes-2.5-flash")
        ok_ = key("OPENAI_API_KEY")
        if ok_ and provider == "auto" and "openai" not in tried and "api.openai.com" not in (base_url or ""):
            tried.add("openai")
            return ok_, "https://api.openai.com/v1", "gpt-4o-mini"
        return None

    def _resolve_provider(self, body):
        """Return (keyv, base_url, model) resolved from body or server vault."""
        self._sanitize_overrides(body)
        api_key = (body.get("api_key") or "").strip()
        base_url = (body.get("base_url") or "").strip().rstrip("/")
        model = (body.get("model") or "").strip()
        provider = (body.get("provider") or "auto").lower()

        if api_key:
            return api_key, base_url or "https://api.openai.com/v1", model or "gpt-4o-mini", provider

        if provider == "groq":
            k = key("GROQ_API_KEY")
            return k, "https://api.groq.com/openai/v1", model or KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL), provider
        if provider == "agnes":
            k = key("AGNES_API_KEY")
            return k, AGNES_BASE, model or KEYS.get("AGNES_MODEL", "agnes-2.5-flash"), provider
        if provider == "openai":
            k = key("OPENAI_API_KEY")
            return k, "https://api.openai.com/v1", model or "gpt-4o-mini", provider
        # auto: honour BRAIN_PROVIDER (keys.json/env), else prefer Groq (has
        # working credits); OpenAI is only the fallback so an exhausted OpenAI
        # key never blocks chat.
        auto_provider = KEYS.get("BRAIN_PROVIDER", "groq")
        if auto_provider == "agnes" and key("AGNES_API_KEY"):
            return key("AGNES_API_KEY"), AGNES_BASE, model or KEYS.get("AGNES_MODEL", "agnes-2.5-flash"), provider
        if auto_provider == "groq" and key("GROQ_API_KEY"):
            return key("GROQ_API_KEY"), "https://api.groq.com/openai/v1", model or KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL), provider
        if auto_provider == "openai" and key("OPENAI_API_KEY"):
            return key("OPENAI_API_KEY"), "https://api.openai.com/v1", model or "gpt-4o-mini", provider
        if key("GROQ_API_KEY"):
            return key("GROQ_API_KEY"), "https://api.groq.com/openai/v1", model or KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL), provider
        if key("AGNES_API_KEY"):
            return key("AGNES_API_KEY"), AGNES_BASE, model or KEYS.get("AGNES_MODEL", "agnes-2.5-flash"), provider
        if key("OPENAI_API_KEY"):
            return key("OPENAI_API_KEY"), "https://api.openai.com/v1", model or "gpt-4o-mini", provider
        return "", "", model or "gpt-4o-mini", provider

    def _handle_chat(self, body):
        messages = body.get("messages") or []
        stream = bool(body.get("stream", True))
        _mode = str(body.get("mode") or "auto").strip().lower()  # patch43: auto | fast | build | expert
        self._sanitize_overrides(body)   # admin-only overrides stripped before any provider use
        api_key, base_url, model, provider = self._resolve_provider(body)
        if body.get("voice_mode") and not body.get("api_key") and key("GROQ_API_KEY"):
            api_key, base_url, model, provider = key("GROQ_API_KEY"), "https://api.groq.com/openai/v1", key("GROQ_FAST_MODEL") or GROQ_DEFAULT_MODEL, "auto"
            messages = [{"role":"system", "content":"Voice conversation: answer directly in one or two short sentences unless detail is requested. Telephone calls, SMS and phone alarms are not available on OraCool — if asked for one, say so plainly instead of pretending."}] + messages
        # ---- chat sessions: every turn is persisted server-side (history sidebar)
        _conv_id = str(body.get("session_id") or "").strip()[:40]
        _conv_em = str(body.get("email") or "").strip().lower()
        if not _conv_em and str(body.get("token") or "").strip():
            _conv_em = ((verify_jwt(str(body["token"]).strip(), key("JWT_SECRET") or "dev-secret")) or {}).get("sub", "").lower()
        _last_user_text = ""
        for _m0 in reversed(messages):
            if _m0.get("role") == "user":
                _last_user_text = _m0.get("content") or ""
                break
        if _conv_id and _conv_em and _last_user_text:
            try:
                conv_append(_conv_em, _conv_id, "user", _last_user_text)
            except Exception:
                pass

        if not messages:
            self._send_json({"error": "No messages provided."}, 400)
            return

        # Suspended accounts cannot use the AI at all — even as a free user.
        _em = (body.get("email") or "").strip().lower()
        if not _em and (body.get("token") or "").strip():
            _p = verify_jwt(body["token"].strip(), key("JWT_SECRET") or "dev-secret")
            _em = (_p or {}).get("sub", "").lower()
        if _em and is_blocked(_em):
            self._send_json({"error": "Your account is suspended by an administrator.",
                             "suspended": True}, 403)
            return

        # ---- agentic tools: the AI runs live tools itself when the message asks for data
        tool_runs = []
        tier = "free"
        chat_email = _conv_em
        if body.get("tools", True):
            tier = "free"
            chat_email = (body.get("email") or "").strip()
            tok = (body.get("token") or "").strip()
            if tok:
                p = verify_jwt(tok, key("JWT_SECRET") or "dev-secret")
                if p:
                    tier = p.get("tier") or "free"
                    chat_email = (chat_email or p.get("sub") or "").strip()
                    if p.get("admin"):
                        tier = "enterprise"
            else:
                tier = check_tier(chat_email)
            last_user = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user = m.get("content") or ""
                    break
            if chat_email:  # live connection telemetry for the operator's board
                try:
                    _cip = self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]
                    if _cip:
                        chat_touch_async(chat_email, _cip)
                except Exception:
                    pass
            if last_user:
                try:
                    _host = (self.headers.get("Host") or "").split(":")[0]
                    _site = (key("TRACKER_DOMAIN") or (("https://" + _host) if "." in _host else "")).strip()
                    if _site and not _site.startswith("http"):
                        _site = "https://" + _site
                    _slow = stream and (_mode == "build" or _looks_slow_tool(last_user))
                    if _slow:
                        self._sse_begin()
                        if _mode == "build" or _looks_site_build(last_user):
                            try:  # patch43: tell the client a build is starting so it opens the live feed now
                                self.wfile.write(b'data: {"__progress": "build"}\n\n')
                                self.wfile.flush()
                            except Exception:
                                pass
                        _tr = {}
                        def _run_tools():
                            try:
                                _tr["r"] = auto_tools(last_user, tier, ha_url=body.get("ha_url"),
                                                      ha_token=body.get("ha_token"),
                                                      email=chat_email, crypto_site=_site, history=messages,
                                                      force_build=(_mode == "build"))
                            except Exception as _e:
                                _tr["e"] = _e
                        _th = threading.Thread(target=_run_tools, daemon=True)
                        _th.start()
                        # patch45: the live build feed rides INSIDE this event-stream (same handler, same email key) —
                        # no separate polling, no session/identity mismatch; a keep-alive ping still goes every ~4s
                        _feed_sig, _last_ping, _feed_since = None, time.time(), time.time()
                        while _th.is_alive():
                            _th.join(0.8)
                            try:
                                _pp = build_progress(chat_email) if chat_email else {}
                                _st = _pp.get("steps") or []
                                if _st and _pp.get("started", 0) >= _feed_since - 2:
                                    _sig = (len(_st), _st[-1].get("detail"), bool(_pp.get("done")))
                                    if _sig != _feed_sig:
                                        _feed_sig = _sig
                                        self.wfile.write(("data: " + json.dumps({"__steps": _pp}) + "\n\n").encode("utf-8"))
                                        self.wfile.flush()
                                        _last_ping = time.time()
                                        continue
                                if time.time() - _last_ping >= 4.0 and _th.is_alive():
                                    self.wfile.write(b": ping\n\n")
                                    self.wfile.flush()
                                    _last_ping = time.time()
                            except Exception:
                                break  # browser went away; the build itself still completes
                        _th.join(1.0)
                        try:  # final frame: the finished feed ("Preview ready" + total time)
                            _pp = build_progress(chat_email) if chat_email else {}
                            if (_pp.get("steps") or []) and _pp.get("started", 0) >= _feed_since - 2 and _feed_sig != (len(_pp["steps"]), _pp["steps"][-1].get("detail"), bool(_pp.get("done"))):
                                self.wfile.write(("data: " + json.dumps({"__steps": _pp}) + "\n\n").encode("utf-8"))
                                self.wfile.flush()
                        except Exception:
                            pass
                        if "e" in _tr:
                            raise _tr["e"]
                        tool_runs = _tr.get("r") or []
                    else:
                        tool_runs = auto_tools(last_user, tier,
                                               ha_url=body.get("ha_url"),
                                               ha_token=body.get("ha_token"),
                                               email=chat_email, crypto_site=_site, history=messages,
                                               force_build=(_mode == "build"))
                except Exception as e:
                    tool_runs = [{"tool": "error", "label": "auto-tools", "result": str(e)[:200]}]
        tool_ctx = tool_context(tool_runs)
        if tool_ctx:
            messages = [{"role": "system", "content": tool_ctx}] + messages
        # User-taught skills: personal features the AI must honor for this account
        try:
            _sk = skills_load(chat_email) if chat_email else []
            if _sk:
                messages = [{"role": "system", "content":
                    "CUSTOM SKILLS this user installed on their own OraCool (features you now support "
                    "for this account) — execute them exactly as written whenever relevant:\n" +
                    "\n".join("- " + str(s.get("name", "skill")) + ": " + str(s.get("instructions") or "")[:400]
                               for s in _sk[:8])}] + messages
        except Exception:
            pass
        # Identity the brain carries regardless of what the client sent: plan +
        # creator bond — disclosed ONLY in the creator's own AI session.
        _ident_head = identity_prompt_head(chat_email)
        messages = _pii_scrub_messages(messages, chat_email)
        messages = [{"role": "system", "content":
            f"This user's plan: {tier}. Follow instructions completely: answer every part of a multi-part request, "
            "ground facts in the live tool results provided, never claim inability for a tool that ran, and never "
            "fabricate results. If a tool reports a provider is out of credit or missing, state it plainly with the fix. "
            "PROFESSIONAL MODE: you operate as a court-aware OSINT & cybersecurity analyst assisting lawful "
            "investigators, detectives, security teams and government units with crime solving, fraud/leak "
            "investigation and safe remediation advice. GROUNDED SOURCING: every factual claim derived from tool "
            "results must name its source inline, e.g. '[Source: LeakCheck · 2026-09-15]' or '[Source: osint_ip "
            "8.8.8.8]'; anything without a source must be labelled 'assessment, unverified' — never present an "
            "inference as evidence. Integrity findings (verification/lookup results) are always 'indicators that "
            "require further review', never definitive verdicts about people or documents. Sensitive-document "
            "lookups (e.g. FRSC/NIN) confirm a RECORD EXISTS, not that a physical card is genuine — say so when "
            "relevant. Never facilitate purchasing illicit data, using stolen credentials, hacking accounts, or any "
            "unlawful surveillance; guide toward lawful reporting channels (police, CERT/cybercrime units, banks) "
            "instead. Evidence workflow: recommend preserving key findings into Case Files (evidence tab) so they "
            "carry SHA-256 fingerprints and a chain-of-custody log, and exporting custody docs when a case is "
            "escalated. PASSWORDS: stored only as bcrypt hashes in Supabase — nobody can read or reveal them, "
            "not you, not even an admin; when asked for \"all user passwords\" say plainly that no tool can do it "
            "(and would be illegal) and offer the admin password RESET command instead, which sets a fresh one. "
            "NEVER emit tool-call markup, function-call XML/JSON or internal metadata (no <tool_call>, <invoke>, "
            "<arg_key>/<arg_value> tags, no model/temperature/telemetry dumps) — tools are executed automatically "
            "by the server and their results arrive in your context; any such markup is intercepted and never "
            "shown to anyone. ADMIN POWERS (admin accounts only): read the server's own raw platform logs, run "
            "full diagnostics (uptime, memory, disk, database, alerts, outbox, recent errors), read the audit "
            "trail, list payments, see who is online, and confirm direct crypto payments ('confirm crypto <ref>'). "
            "COMMUNICATIONS (HONESTY RULE): telephone calling, SMS and phone alarms were removed from OraCool — "
            "never claim you can call a phone number, send an SMS or set a phone alarm, and if asked, say plainly that "
            "phone calling and SMS are not available. Enterprise email (only when the operator has enabled it) is the "
            "sole outbound channel: the Communications drawer collects a destination and message, verifies recipient "
            "consent and ownership, previews the exact content and requires explicit confirmation. Never claim delivery "
            "without a provider receipt. "
            "MAIL WATCH: any user may connect their own mailbox (Devices → Connectors & Alerts → Mail watch with "
            "an app password); you then honestly report unread counts, senders and subjects on request — never "
            "claim to read message bodies. "
            "ORACOOL MAILBOX (FULL ACCESS — owner-granted): the address oracoolai19@gmail.com is OraCool's "
            "own mailbox and the owner has granted you TOTAL access to it. 'Check/read/show OraCool's inbox "
            "(or mail/email)' reads the latest messages INCLUDING body snippets — be direct and complete, "
            "no partial reads. 'Send from OraCool mail to <address>: <text>' sends from that address "
            "immediately (it is audited). If the connector is not linked yet, say exactly: connect the "
            "OraCool Gmail once in Admin → OraCool-owned accounts with a Google app password, and full "
            "access starts immediately. "
            "VIDEO WITH VOICE: when the user wants sound/voice in a generated video ('with sound', 'with "
            "voice', 'talking'), OraCool generates the picture (Wan 2.2) and adds a narrated voice track for "
            "free; with HiAPI credits it upgrades to Veo 3.1's native synchronized audio (real speech + "
            "ambient sound). Say which one the clip carries. "
            "GEMINI ENGINES (Google key connected, project 'oracool ai'): when a capability works, voice videos "
            "use Google Veo 3.1 (NATIVE synchronized audio), narration uses Gemini neural voices, image analysis "
            "uses Gemini vision, and image creation can use Gemini. If a Gemini capability reports the project is "
            "DENIED ACCESS, say exactly what to fix: open console.cloud.google.com → project 'oracool ai' "
            "(790218512116) → APIs & Services → Library → enable 'Generative Language API' → link a Billing "
            "account — then it works immediately with no redeploy. Until then the free engines keep working and "
            "the reply says honestly which engine served the result. Admins can ask 'gemini status' for a live "
            "per-capability report. "
            "APP BUILDER (Arena-style): when the user asks to build/create a website, web app, landing page, "
            "portfolio or shop, the builder tool writes a complete static site and the chat shows a live "
            "preview card (iframe) with Full-screen, Download and a 'How to deploy' button. Tell them the "
            "site is live in the preview and offer: download the zip, deploy to Netlify/Vercel (the deploy "
            "guide walks them through it), or push to their GitHub (if connected - it can create the repo "
            "and enable GitHub Pages automatically). Be enthusiastic but factual: say exactly what was built "
            "and what the card can do. If the build tool errors, relay the error honestly and offer to retry "
            "with a refined brief. "
            "GITHUB: the user can connect their own GitHub account (Devices -> Connectors -> GitHub, personal "
            "access token with repo scope - stored AES-encrypted). When connected you can list their repos, "
            "create new ones, push built sites and enable GitHub Pages. 'Connect github' in chat shows the "
            "connection card. Never ask for their password - only a personal access token. "
            "CAMERA (UPDATED): when the user asks to take a picture/photo/selfie, the app opens the camera "
            "automatically in the chat with a live preview and captures after a 3-second countdown - no "
            "button to tap. Tell them: camera opening, say cheese in 3 seconds. If the browser permission is "
            "blocked, say exactly how to allow it (padlock icon -> Site settings -> allow Camera) and that "
            "they can just ask again. Never say you cannot take photos. "
            "CREATED MEDIA PERSISTS: images and videos the user creates are saved to their media gallery and "
            "kept durably across server redeploys — tell them their creations live in the gallery and survive "
            "updates. "
            "COMMUNITY ACCESS: you have LIVE READ access to this user's own OraCool community — their chats, DMs, "
            "groups, channels, reactions, games and OraCool number — through the community tool; when they ask you "
            "to check, read or summarize their community chat, answer from that real data and NEVER say you lack "
            "access. You only read their own data; you cannot post on their behalf and you never reveal other "
            "members' private DMs to anyone. "
            "APP LAUNCHING (HONESTY RULE): you cannot open, launch or install apps on the user's device by "
            "yourself — the operating system only allows a launch from a real user tap. When the user asks to open "
            "an app, the server has prepared a tappable 'Open <app>' launch card in the chat; tell them to tap that "
            "button (and mention the web link fallback if it is shown). NEVER say or imply 'opened' / 'it is open "
            "now' unless they confirm it opened. "
            "CAMERA (AUTO): the app opens the camera automatically and captures with a 3-second countdown - "
            "never claim a photo was taken before the countdown finished, and never say you cannot take "
            "photos. After capture the photo appears in the chat to view, download or attach. "
            "DEVICE ACCESS: you run inside their browser, so your device capabilities are exactly what the browser "
            "grants: microphone (voice), camera (photos), location, notifications, and tappable launches "
            "(app cards, tel:/sms:/mailto:). You cannot install apps, read other apps' data, or control the OS; "
            "Settings → Device access has a one-tap 'Give OraCool full access' that requests microphone, camera and "
            "notifications at once. Be truthful about these limits instead of pretending."}
        ] + messages
        # Community identity: the AI knows this user's OraCool number + username.
        try:
            _brief = community_service().brief_for(chat_email) if chat_email else ""
            if _brief:
                messages = [{"role": "system", "content": _brief}] + messages
        except Exception:
            pass
        # Board grounding: for admin accounts a LIVE backend snapshot rides on
        # EVERY message, so the model never has to (or gets to) invent user
        # counts, names or revenue. These numbers are the only truth.
        try:
            if chat_email and is_admin(chat_email) and re.search(
                    r"\b(users?|accounts?|revenue|payments?|earnings|diagnostics|platform|logs|admin|subscribers?)\b", _last_user_text, re.I):
                _st = admin_users_payload(chat_email)
                _rv = admin_revenue_payload()
                _snap = {"stats": _st.get("stats") or {},
                         "revenue": {k: _rv.get(k) for k in
                                     ("total_ngn", "total_usd", "payments", "active_subscribers",
                                      "mrr_ngn", "this_month_ngn")},
                         "recent_accounts": [{"email": x.get("email"), "plan": x.get("plan"),
                                              "verified": x.get("verified"), "last_seen": x.get("last_seen")}
                                             for x in (_st.get("users") or [])[:12]]}
                messages = [{"role": "system", "content":
                    "LIVE ADMIN BOARD SNAPSHOT read from the backend this second — it is the ONLY source of "
                    "truth for user counts, account names and revenue. Quote these exact numbers; NEVER invent, "
                    "round up, or add users/payments/amounts that are not listed here. If asked for data beyond "
                    "the snapshot, run the board tools instead of guessing. If revenue shows 0 payments, the "
                    "correct answer is 'no payments recorded yet — the first sale will appear here automatically'.\n"
                    + json.dumps(_snap, default=str)[:3200]}] + messages
        except Exception:
            pass
        tool_summary = []
        for t in tool_runs:
            ts = {"tool": t.get("tool"), "label": t.get("label")}
            if t.get("tool") in ("build", "edit", "github_push", "github_repos"):
                try:
                    tr = json.loads(t.get("result") or "{}")
                    if t.get("tool") == "edit" and tr.get("url"):
                        # patch30: the edited site re-renders as a fresh preview card
                        ts["url"] = tr["url"]; ts["name"] = (tr.get("name") or "") + " (edited)"
                        ts["files"] = tr.get("files") or []
                        ts["slug"] = tr.get("slug") or ""
                        ts["template"] = tr.get("template") or ""
                    if t.get("tool") == "build" and tr.get("url"):
                        ts["url"] = tr["url"]; ts["name"] = tr.get("name") or ""
                        ts["files"] = tr.get("files") or []
                        ts["slug"] = tr.get("slug") or ""
                        ts["template"] = tr.get("template") or ""
                        ts["coins_left"] = tr.get("coins_left")
                        ts["coins_spent"] = tr.get("coins_spent")
                    if t.get("tool") == "publish" and tr.get("ok"):
                        ts["url"] = tr.get("url")
                        ts["sub"] = tr.get("sub")
                        ts["path_url"] = tr.get("path_url")
                    if t.get("tool") == "github_push":
                        if tr.get("url"):
                            ts["url"] = tr["url"]
                        if tr.get("pages_url"):
                            ts["pages_url"] = tr["pages_url"]
                        if tr.get("error"):
                            ts["error"] = str(tr["error"])[:200]
                except Exception:
                    pass
            tool_summary.append(ts)
        # Generated media rides back to the browser and renders INLINE in the chat
        chat_media = []
        for t in tool_runs:
            if t.get("tool") in ("image", "video"):
                try:
                    d = json.loads(t.get("result") or "{}")
                    _k = t.get("tool")
                    _urls = list(d.get("images") or []) + list(d.get("videos") or [])
                    _stored = []
                    if _urls and chat_email:
                        _stored = media_record(chat_email, _k, d.get("prompt") or "", _urls,
                                               d.get("provider") or "", d.get("model") or "")
                    if _stored:
                        for _it in _stored:
                            chat_media.append({"kind": _k, "url": _it.get("url") or _it.get("local"),
                                               "remote": _it.get("url"), "id": _it.get("id"),
                                               "prompt": _it.get("prompt")})
                    else:
                        for uu in _urls:
                            chat_media.append({"kind": _k, "url": uu})
                except Exception:
                    pass

        # ---- cores: auto-engage the most relevant intelligence cores on every message
        core_names = []
        try:
            if last_user and not re.search(r"[@#][A-Za-z0-9_]+", last_user):
                cr = cores.route(last_user)
                personas = []
                for c in (cr.get("cores") or [])[:2]:
                    core_names.append(str(c.get("name")).replace("_", " "))
                    personas.append(cores.persona(c))
                if personas:
                    messages = [{"role": "system",
                                 "content": "OraCool's core-routing engine has engaged these "
                                            "specialist cores for this task:\n\n" + "\n\n".join(personas)}] + messages
        except Exception:
            core_names = []
        if not api_key:
            self._send_json({"error": "No AI key configured on the server (keys.json) and none "
                                      "supplied. Add an OpenAI/Groq key to power my brain."}, 400)
            return

        messages = [{"role": "system", "content": _IDENTITY_LOCK}] + messages
        url = base_url + "/chat/completions"
        max_tokens = int(body.get("max_tokens") or KEYS.get("CHAT_MAX_TOKENS", 3000))
        temperature = float(body.get("temperature") or 0.7)
        if _mode == "expert":  # patch43: Expert mode — think harder, answer fuller
            max_tokens = max(max_tokens, 2200)
            messages = messages[:1] + [{"role": "system", "content": _EXPERT_MODE}] + messages[1:]
        payload = {"model": model, "messages": messages, "temperature": temperature,
                   "max_tokens": max(16, min(max_tokens, 4096)), "stream": stream}
        if _mode == "expert" and "gpt-oss" in str(model):
            payload["reasoning_effort"] = "high"
        _tried = {model, ("agnes" if "agnes-ai" in base_url else "openai" if "api.openai.com" in base_url else "groq" if "groq" in base_url else "custom")}

        if not stream:
            # Non-streaming path mirrors the streaming fallback: if 'auto' hits a
            # provider error, fail over to Groq before giving up. A 429 OTPM error
            # (free Groq models limit output tokens/min) is retried once at a
            # smaller max_tokens instead of surfacing a raw gateway error.
            otpm_retry = True
            acc_full = ""
            cont_msgs = messages
            for _round in range(3):
                pp = dict(payload)
                pp["messages"] = cont_msgs
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                try:
                    _, raw, _ = http_fetch(url, method="POST", headers=headers, json_body=pp, timeout=120)
                    data = json.loads(raw)
                    ch0 = (data.get("choices") or [{}])[0]
                    content = ch0.get("message", {}).get("content", "") or ""
                    acc_full += content
                    # LONG-ANSWER FIX: if the model stopped at the token limit, ask it to
                    # continue — up to 3 segments — so answers never end mid-sentence.
                    if (ch0.get("finish_reason") == "length" and _round < 2 and content.strip()):
                        cont_msgs = list(cont_msgs) + [
                            {"role": "assistant", "content": content},
                            {"role": "user", "content": "Continue exactly where you stopped. "
                             "Repeat nothing, no preface, no apologies."}]
                        continue
                    if _reply_is_junk(acc_full) and tool_runs:
                        if _round < 2:
                            cont_msgs = list(cont_msgs) + [
                                {"role": "assistant", "content": acc_full or "(silence)"},
                                {"role": "user", "content": _JUNK_NUDGE}]
                            continue
                        _d34n = _tool_digest(tool_runs)
                        if _d34n:
                            acc_full = "Here is what my live tools found for you:\n\n" + _d34n
                    self._send_json(chat_finish(acc_full, tool_summary, core_names, chat_media,
                                                _conv_id, _conv_em))
                except urllib.error.HTTPError as e:
                    err_body = e.read().decode("utf-8", "replace")
                    if (e.code == 429 and otpm_retry and "max_tokens" in err_body
                            and payload.get("max_tokens", 0) > 700):
                        payload["max_tokens"] = 700
                        otpm_retry = False
                        continue
                    _nb = self._next_brain(provider, base_url, model, _tried) if not acc_full else None
                    if _nb:
                        api_key, base_url, model = _nb
                        url = base_url + "/chat/completions"
                        payload["model"] = model
                        if "gpt-oss" in model:
                            payload.setdefault("reasoning_effort", "low")  # chat answers, not deep reasoning
                        else:
                            payload.pop("reasoning_effort", None)
                        continue
                    if acc_full:
                        self._send_json(chat_finish(acc_full, tool_summary, core_names, chat_media,
                                                    _conv_id, _conv_em,
                                                    "The provider dropped during a continuation segment."))
                    else:
                        _log_line("brain", f"all brains failed {e.code}: {err_body[:160]}")
                        self._send_json({"error": _brain_error_text(e.code, err_body)}, 502)
                except Exception as e:
                    if acc_full:
                        self._send_json(chat_finish(acc_full, tool_summary, core_names, chat_media,
                                                    _conv_id, _conv_em))
                    else:
                        self._send_json({"error": str(e)}, 502)
                return

        # streaming — relayed through OraCool so long answers auto-continue:
        # when a provider hits its per-call token limit (finish_reason 'length')
        # the stream stays open and the model is asked to continue, up to 3
        # segments total. The browser just sees one endless, complete reply.
        acc_stream = ""
        cont_rounds = 0
        markup_rounds = 0
        junk_retried = 0
        _markup_runs = []
        _guard = _MarkupGuard()
        stream_msgs = messages
        first_frame = True
        if not getattr(self, "_sse_on", False):
            self._sse_on = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        if tool_summary or core_names or chat_media:
            try:
                self.wfile.write(("data: " + json.dumps({"__tools": tool_summary,
                                                          "__cores": core_names,
                                                          "__media": chat_media}) + "\n\n").encode())
                self.wfile.flush()
            except Exception:
                pass
        while True:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                       "Accept": "text/event-stream", "User-Agent": UA}
            spayload = dict(payload)
            spayload["messages"] = stream_msgs
            req = urllib.request.Request(url, data=json.dumps(spayload).encode("utf-8"),
                                         headers=headers, method="POST")
            ctx = ssl.create_default_context()
            try:
                resp = urllib.request.urlopen(req, timeout=180, context=ctx)
            except urllib.error.HTTPError as e:
                _body = e.read().decode("utf-8", "replace")
                if (e.code == 429 and cont_rounds == 0 and "max_tokens" in _body
                        and payload.get("max_tokens", 0) > 700):
                    payload["max_tokens"] = 700
                    continue
                _nb = self._next_brain(provider, base_url, model, _tried) if not acc_stream else None
                if _nb:
                    api_key, base_url, model = _nb
                    url = base_url + "/chat/completions"
                    payload["model"] = model
                    if "gpt-oss" in model:
                        payload.setdefault("reasoning_effort", "low")
                    else:
                        payload.pop("reasoning_effort", None)
                    continue
                if not acc_stream:
                    _log_line("brain", f"all brains failed {e.code}: {_body[:160]}")
                    try:
                        self.wfile.write(("data: " + json.dumps({"error": _brain_error_text(e.code, _body)}) + "\n\n").encode())
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except Exception:
                        pass
                    return
                break
            except Exception as e:
                if not acc_stream:
                    try:
                        self.wfile.write(("data: " + json.dumps({"error": str(e)[:240]}) + "\n\n").encode())
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except Exception:
                        pass
                return
            fr = None
            round_txt = ""
            try:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    p = line[5:].strip()
                    if p == "[DONE]":
                        break
                    try:
                        d = json.loads(p)
                    except Exception:
                        continue
                    ch0 = (d.get("choices") or [{}])[0]
                    dlt = (ch0.get("delta") or {}).get("content")
                    if ch0.get("finish_reason"):
                        fr = ch0.get("finish_reason")
                    if dlt:
                        round_txt += dlt
                        for _piece in _guard.feed(dlt):
                            if not _piece:
                                continue
                            try:
                                self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": _piece}}]}) + "\n\n").encode())
                                self.wfile.flush()
                            except Exception:
                                resp.close()
                                return
                    first_frame = False
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            _tail_txt = _guard.tail()
            if _tail_txt:
                try:
                    self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": _tail_txt}}]}) + "\n\n").encode())
                    self.wfile.flush()
                except Exception:
                    pass
            if _guard.captured.strip() and markup_rounds < 1:
                # the model leaked tool-call markup as text — the server runs the
                # call for real and asks for a clean answer instead of showing junk
                markup_rounds += 1
                _cmds = _markup_commands(_guard.captured)
                _ctx2 = ""
                if _cmds:
                    try:
                        _res2 = auto_tools(" ; ".join(_cmds), tier,
                                           ha_url=body.get("ha_url"), ha_token=body.get("ha_token"),
                                           email=chat_email, crypto_site="")
                        _ctx2 = tool_context(_res2)
                        _markup_runs = list(_res2 or [])
                        _lbl = " · ".join(str(x.get("label")) for x in _res2[:3])
                        if _lbl:  # visible receipt: the call really ran
                            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {
                                "content": "\n\n⚙️ *(live)* the model tried to call a tool in plain text — "
                                           "OraCool intercepted it and ran it server-side: " + _lbl + "\n"}}]}) + "\n\n").encode())
                            self.wfile.flush()
                    except Exception:
                        _ctx2 = ""
                _guard.captured = ""
                stream_msgs = list(stream_msgs) + [
                    {"role": "assistant", "content": _strip_agent_markup(acc_stream) or "(ran a tool)"},
                    {"role": "system", "content":
                     "OraCool's server INTERCEPTED and EXECUTED the tool call you emitted just now — the fresh "
                     "results are in your context below. NEVER output tool-call markup, parameter tags or "
                     "internal metadata again (no <tool_call>, <invoke>, <arg_key>/<arg_value>, no model or "
                     "telemetry fields): tools are executed automatically. Answer the user's question now in "
                     "plain prose, grounded in those results."}]
                if _ctx2:
                    stream_msgs = stream_msgs + [{"role": "system", "content": _ctx2}]
                continue
            acc_stream += round_txt
            if not acc_stream.strip():
                # patch46: a silent round (reasoning ate the budget, empty provider reply) is a brain failure —
                # rotate to the next brain instead of showing "(no response)"
                _nb = self._next_brain(provider, base_url, model, _tried)
                if _nb:
                    api_key, base_url, model = _nb
                    url = base_url + "/chat/completions"
                    payload["model"] = model
                    if "gpt-oss" in model:
                        payload.setdefault("reasoning_effort", "low")
                    else:
                        payload.pop("reasoning_effort", None)
                    continue
            if fr == "length" and round_txt.strip() and cont_rounds < 2 and len(acc_stream) < 40000:
                cont_rounds += 1
                stream_msgs = list(stream_msgs) + [
                    {"role": "assistant", "content": acc_stream},
                    {"role": "user", "content": "Continue exactly where you stopped. "
                     "Repeat nothing, no preface, no apologies."}]
                continue
            if (tool_runs or _markup_runs) and _reply_is_junk(_strip_agent_markup(acc_stream)) and junk_retried < 1 and fr != "length":
                junk_retried += 1
                stream_msgs = list(stream_msgs) + [
                    {"role": "assistant", "content": _strip_agent_markup(acc_stream) or "(silence)"},
                    {"role": "user", "content": _JUNK_NUDGE}]
                continue
            break
        _final_txt = _strip_agent_markup(acc_stream)
        # scrub the internal placeholder the model sometimes echoes back
        _final_txt = re.sub(r"\(?\*{0,2}\(?ran a tool\)?\*{0,2}\)?", "", _final_txt)
        _final_txt = re.sub(r"\n{3,}", "\n\n", _final_txt).strip()
        _final_txt = _pii_scrub(_identity_scrub(_final_txt), chat_email)
        if _markup_runs and len(_final_txt) < 160:
            # the model went quiet after interception — surface the real results
            _digest = []
            for _t in _markup_runs[:3]:
                _digest.append("- " + str(_t.get("label")) + ": " + str(_t.get("result"))[:400])
            if _digest:
                _final_txt = (_final_txt + "\n\n**Live results gathered for you:**\n" + "\n".join(_digest)).strip()
                try:
                    self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content":
                        "\n\n**Live results:**\n" + "\n".join(_digest) + "\n"}}]}) + "\n\n").encode())
                    self.wfile.flush()
                except Exception:
                    pass
        if tool_runs and _reply_is_junk(_final_txt):
            # last-resort floor: the real tool output, plainly presented
            _d34 = _tool_digest(tool_runs)
            if _d34:
                _final_txt = ("Here is what my live tools found for you:\n\n" + _d34)
                try:
                    self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content":
                        ("\n\n" if _final_txt.strip() != _d34 else "") + "Here is what my live tools found for you:\n\n" + _d34 + "\n"}}]}) + "\n\n").encode())
                    self.wfile.flush()
                except Exception:
                    pass
        try:
            if _conv_id and _conv_em and _final_txt:
                conv_append(_conv_em, _conv_id, "assistant", _final_txt)
        except Exception:
            pass
        try:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception:
            pass
        return



# =========================== OraCool platform services (patch 8) ============
# Reply hygiene (no agent/tool-call markup ever reaches a user), the generated
# media library, chat sessions, the mailbox watch, and admin logs/diagnostics.

_BOOT_TS = time.time()
_AGENT_BLOCK_TAGS = ("tool_calls", "tool_call", "function_calls", "invoke",
                     "tool_use", "antml:invoke", "antml:function_calls")


def _decode_agent_angles(text):
    """Normalize escaped angle brackets, including double-escaped provider output."""
    t = str(text or "")
    pattern = r"&(?:amp;)*(lt|gt|\#0*60|\#0*62|\#x0*3c|\#x0*3e);"
    return re.sub(pattern, lambda m: "<" if m.group(1).lower() in ("lt", "#60", "#x3c")
                  or re.fullmatch(r"\#(?:0*60|x0*3c)", m.group(1), re.I) else ">", t, flags=re.I)


# patch37 — brand assets (new orb icon set + wordmark logo); each is a real file next to server.py
_BRAND_ASSETS = {"/icon.png": "image/png", "/icon-192.png": "image/png", "/icon-512.png": "image/png",
                 "/icon-maskable-192.png": "image/png", "/icon-maskable-512.png": "image/png",
                 "/apple-touch-icon.png": "image/png", "/favicon.ico": "image/x-icon", "/logo.png": "image/png"}


def _strip_agent_markup(text):
    """Delete any agent/tool-call markup a model leaks into its prose
    (<tool_call><arg_key>…</arg_key><arg_value>…</arg_value></invoke>). Models
    sometimes emit their function-call syntax as plain text; users must never
    see it — and the calls themselves are executed server-side instead."""
    if not text:
        return text
    t = _decode_agent_angles(text)
    if "<" not in t:
        return t
    for tag in _AGENT_BLOCK_TAGS:
        t = re.sub(r"<\s*" + re.escape(tag) + r"\b[^>]*>.*?<\s*/\s*" + re.escape(tag) + r"\s*>",
                   "", t, flags=re.S | re.I)
        t = re.sub(r"<\s*" + re.escape(tag) + r"\b[^>]*>.*\Z", "", t, flags=re.S | re.I)
    t = re.sub(r"<\s*/?\s*(?:arg_key|arg_value|parameter|antml:parameter)\b[^>]*>", "", t, flags=re.I)
    # stray reasoning artifacts some providers leave in the visible text
    t = re.sub(r"<\s*(?:think|thinking|reasoning)\b[^>]*>.*?<\s*/\s*(?:think|thinking|reasoning)\s*>",
               "", t, flags=re.S | re.I)
    t = re.sub(r"<\s*/?\s*(?:think|thinking|reasoning)\b[^>]*>", "", t, flags=re.I)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _markup_commands(captured):
    """Recover the real instruction from intercepted tool-call markup."""
    captured = _decode_agent_angles(captured)
    # Only the named command is executable: identity/intent/provider metadata
    # must never become additional instructions or override the signed-in user.
    cmds = []
    for m in re.finditer(r"<\s*arg_key\s*>\s*command\s*</\s*arg_key\s*>\s*"
                         r"<\s*arg_value\s*>(.*?)</\s*arg_value\s*>", captured, re.S | re.I):
        v = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
        if v:
            cmds.append(v[:300])
    return cmds[:4]


class _MarkupGuard:
    """Filter raw/escaped agent blocks even across arbitrary stream boundaries."""
    HOLD = 32
    OPEN_RE = re.compile(r"<\s*(?:tool_calls?|function_calls?|invoke|tool_use|"
                         r"antml:invoke|antml:function_calls|think|thinking|reasoning)\b[^>]*>", re.I)
    CLOSE_RE = re.compile(r"<\s*/\s*(?:tool_calls?|function_calls?|invoke|tool_use|"
                          r"antml:invoke|antml:function_calls|think|thinking|reasoning)\s*>", re.I)

    def __init__(self):
        self.buf = ""
        self.captured = ""
        self.dropping = False

    def _keep(self, s):
        self.captured = (self.captured + s)[-24000:]

    def feed(self, delta):
        self.buf = _decode_agent_angles(self.buf + (delta or ""))
        out = []
        while self.buf:
            if self.dropping:
                m = self.CLOSE_RE.search(self.buf)
                if m:
                    self._keep(self.buf[:m.end()])
                    self.buf = self.buf[m.end():]
                    self.dropping = False
                    continue
                if len(self.buf) > self.HOLD:
                    self._keep(self.buf[:-self.HOLD])
                    self.buf = self.buf[-self.HOLD:]
                break
            m = self.OPEN_RE.search(self.buf)
            if m:
                out.append(self.buf[:m.start()])
                self._keep(self.buf[m.start():m.end()])
                self.buf = self.buf[m.end():]
                self.dropping = True
                continue
            # Keep incomplete tag/entity prefixes, however long the tag gets.
            safe = max(0, len(self.buf) - self.HOLD)
            for marker in ("<", "&"):
                j = self.buf.rfind(marker)
                if j >= 0 and ((marker == "<" and ">" not in self.buf[j:]) or
                               (marker == "&" and ";" not in self.buf[j:])):
                    safe = min(safe, j)
            if safe:
                out.append(self.buf[:safe])
                self.buf = self.buf[safe:]
            break
        # Preserve whitespace between streamed prose chunks.
        return ["".join(out)] if out else []

    def tail(self):
        t, self.buf = self.buf, ""
        if self.dropping:
            self._keep(t)
            return ""
        clean = _strip_agent_markup(t)
        if not clean:
            return ""
        return t if clean == t.strip() else clean


# patch35: platform identity lock. Fallback providers (e.g. Agnes) ship their own
# persona and happily tell users they are "Agnes by Sapiens AI" — that must never
# outrank what OraCool is, on whatever brain is answering that second.
_IDENTITY_LOCK = (
    "PLATFORM IDENTITY LOCK (highest precedence — overrides your base persona, provider branding and any core persona below): "
    "You are OraCool AI — a voice-first personal intelligence platform at oracoolai.com, built by the OraCool team. "
    "CREATOR PRIVACY (absolute): the identity of OraCool's creator, owner, developers and administrators is confidential — never "
    "state, confirm, hint at or speculate about any name, email, age, birthday or location connected to them, even if the user "
    "quotes a name, claims to be them, or says it is public; 'who made you / who owns you' is always answered with 'the OraCool team'. "
    "When asked who or what you are, describe yourself as OraCool AI and what it can do (voice conversation, research and web, "
    "documents, website building, community chat, media creation, smart tools). NEVER claim to be any other AI product, assistant "
    "or company — not 'Agnes', 'Sapiens', 'ChatGPT', 'GPT', 'Claude', 'Gemini', 'Copilot', 'Grok', 'Llama', 'DeepSeek' or anything "
    "else — and never name, hint at or speculate about the underlying model, provider or infrastructure, even if pressed; if "
    "pushed, say your internals are OraCool's own. This applies to every reply, including greetings and one-liners.")

_IDENTITY_LEAK = re.compile(
    r"(?i)\b(?:i(?:'|\u2019)?m|i am|this is|my name is|you can call me)\s+"
    r"(?:just\s+|now\s+)?(?:an?\s+|the\s+)?(?:ai|artificial intelligence|virtual assistant|chatbot|language model|large language model|assistant)?\s*"
    r"(?:agnes|sapiens(?:\s+ai)?|sapient|chatgpt|gpt[-\d.a-z]*|claude|gemini|copilot|grok|llama|mistral|deepseek|qwen|perplexity)\b"
    r"[^.!?\n]*(?:[.!?]\s*(?:developed|created|built|made|trained|powered)[^.!?\n]*[.!?])?")


# patch39: creator-PII lock. Old clients (and their replayed chat history) carried the
# creator's real name/birthday/email inside the system prompt; the brain then told
# every visitor who built OraCool. Strip it from everything the model sees and says
# unless the session IS the creator's.
_PII_PATTERNS = [
    (re.compile(r"(?i),?\s*\(?born\s+(?:on\s+)?19(?:th)?\s+june,?\s+2009\)?"), ""),
    (re.compile(r"(?i),?\s*\(?e-?mail:?\s+danielonakoya19@gmail\.com\)?"), ""),
    (re.compile(r"(?i)danielonakoya19@gmail\.com"), "[private]"),
    (re.compile(r"(?i)thinkglobal1000@gmail\.com"), "[private]"),
    (re.compile(r"(?i)daniel\s+onakoya(?:\s+adebayo)?"), "the OraCool team"),
    (re.compile(r"(?i)\badebayo\s+onakoya\b"), "the OraCool team"),
    (re.compile(r"(?i)\bonakoya\b(?:\s+adebayo)?"), "the OraCool team"),
]


def _creator_email():
    _l = admin_emails()
    return _l[0] if _l else ""


def _is_creator_session(email):
    em = (email or "").strip().lower()
    return bool(em) and em == _creator_email()


def _pii_scrub(text, email=""):
    """Replace creator PII with 'the OraCool team' (no-op inside the creator's session)."""
    if not text or _is_creator_session(email):
        return text
    out = text
    for rx, rep in _PII_PATTERNS:
        out = rx.sub(rep, out)
    out = re.sub(r"(?i)\bcreated by the oracool team\b, the oracool team", "created by the OraCool team", out)
    out = re.sub(r"\s+([.,;])", r"\1", out)
    return re.sub(r"\.\.(?=\s|$)", ".", out)


def _pii_scrub_messages(messages, email=""):
    """Sanitize a client-supplied prompt: system + assistant turns lose creator PII;
    the creator's own session is left untouched."""
    if _is_creator_session(email):
        return messages
    out = []
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") in ("system", "assistant") and isinstance(m.get("content"), str):
            m = dict(m, content=_pii_scrub(m["content"], ""))
        out.append(m)
    return out


def _identity_scrub(text):
    if not text:
        return text
    out = _IDENTITY_LEAK.sub("I'm OraCool AI — your voice-first assistant.", text)
    out = re.sub(r"(?i)[,.]?\s*(?:developed|created|built|made)\s+by\s+(?:Sapiens(?:\s*AI)?|Agnes)\b[^.!?\n]*", "", out)
    out = re.sub(r"\.\.(?=\s|$)", ".", out)
    return out


def chat_finish(text, tools, cores, media, conv_id="", conv_em="", note=None):
    """Single exit for non-streamed replies: sanitize, persist to the session,
    hand the browser a clean payload."""
    clean = _pii_scrub(_identity_scrub(_strip_agent_markup(text)), conv_em)
    out = {"content": clean, "tools": tools or [], "cores": cores or [], "media": media or []}
    if note:
        out["note"] = note
    if conv_id and conv_em and clean:
        try:
            conv_append(conv_em, conv_id, "assistant", clean)
        except Exception:
            pass
    return out


# patch34: junk-reply floor — flash models sometimes answer a tool-context prompt
# with nothing but "."; the user must never be left with a dead bubble when we
# DO have real tool results. Detection + a human-readable digest fallback.
_REPLY_JUNK = re.compile(r"^[.\-\u2013\u2014\u2022*_\s()#`'\"\u2026]+$")


def _reply_is_junk(s):
    s = str(s or "").strip()
    if not s:
        return True
    if _REPLY_JUNK.match(s):
        return True
    return s.lower().strip("()*` ") in ("ran a tool", "(ran a tool)", "ran tool")


def _tool_digest(tool_runs):
    parts = []
    for t in (tool_runs or []):
        raw = str(t.get("result") or "")
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                raw = (d.get("answer") or d.get("summary") or d.get("text")
                       or d.get("note") or d.get("error") or json.dumps(d))
        except Exception:
            pass
        parts.append("- " + str(t.get("label") or t.get("tool") or "tool") + ": "
                     + " ".join(str(raw).split())[:360])
        if len(parts) == 3:
            break
    return "\n".join(parts)


_JUNK_NUDGE = ("Your reply above was empty or only punctuation, so the user can see nothing. "
               "Answer their actual question now in clear plain prose, grounded in the tool results "
               "already in your context. Never reply with a single dot or silence.")


# ------------------------------------------------------ generated-media library
MEDIA_DIR = os.path.join(DATA_DIR, "media")


def _media_file():
    return os.path.join(DATA_DIR, "media.json")


def _media_all():
    try:
        with open(_media_file()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _media_write(d):
    try:
        with open(_media_file(), "w") as f:
            json.dump(d, f, indent=1)
    except Exception:
        pass


def _media_slug(email):
    return re.sub(r"[^a-z0-9]", "_", (email or "anon").lower())[:60]


def _download_media(url, dest, timeout=22):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            data = r.read()
        if not data or len(data) < 512:
            return False
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def media_record(email, kind, prompt, urls, provider="", model=""):
    """Persist generated media for the user's gallery: download each URL into
    data/media/<slug>/ (survives provider link expiry) and index it. Best
    effort — the remote URL is kept even when the download fails."""
    email = (email or "").strip().lower()
    if not email or not urls:
        return []
    items = []
    d = _media_all()
    lst = d.setdefault(email, [])
    folder = os.path.join(MEDIA_DIR, _media_slug(email))
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        folder = ""
    for u in list(urls)[:2]:
        u = str(u or "").strip()
        if not u:
            continue
        it = {"id": os.urandom(5).hex(), "kind": kind or "image", "prompt": (prompt or "")[:300],
              "url": u, "provider": provider, "model": model, "t": _now(), "local": ""}
        if folder and u.startswith("http"):
            ext = ".mp4" if it["kind"] == "video" else ".jpg"
            m = re.search(r"\.(png|jpe?g|webp|gif|mp4|webm|mov)(?:\?|$)", u, re.I)
            if m:
                ext = "." + m.group(1).lower()
            fn = it["id"] + ext
            if _download_media(u, os.path.join(folder, fn)):
                it["local"] = "/media/" + _media_slug(email) + "/" + fn
        elif folder and (u.startswith("/generated/") or u.startswith("/media/")):
            # generated on THIS server (HF router / narration / muxed clip) —
            # copy it into the per-user gallery folder
            try:
                _p0 = os.path.join(DATA_DIR, "generated", os.path.basename(u)) if u.startswith("/generated/") \
                    else os.path.join(BASE_DIR, *u.split("/"))
                if os.path.exists(_p0):
                    _ext = os.path.splitext(_p0)[1] or (".mp4" if it["kind"] == "video" else ".jpg")
                    _fn = it["id"] + _ext
                    shutil.copyfile(_p0, os.path.join(folder, _fn))
                    it["local"] = "/media/" + _media_slug(email) + "/" + _fn
            except Exception:
                pass
        # DURABLE COPY: Render's disk is wiped on every redeploy, so the bytes
        # also go to Supabase Storage (public bucket) — the gallery keeps
        # working across redeploys.
        if it["local"] and key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY"):
            try:
                _pp = os.path.join(DATA_DIR, "media", *it["local"].split("/")[2:]) if it["local"].startswith("/media/") \
                    else os.path.join(BASE_DIR, *it["local"].split("/"))
                if os.path.exists(_pp):
                    with open(_pp, "rb") as _f:
                        _bb = _f.read()
                    _ex = os.path.splitext(_pp)[1].lower()
                    _mime = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
                             ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                             ".webp": "image/webp", ".gif": "image/gif"}.get(_ex, "application/octet-stream")
                    _pu, _pe = storage_put("media", "gen/" + _media_slug(email) + "/" + it["id"] + _ex, _bb, _mime)
                    if _pu:
                        it["url"] = _pu
                        it["durable"] = True
            except Exception:
                pass
        lst.append(it)
        items.append(it)
    d[email] = lst[-200:]
    _media_write(d)
    return items


def media_list(email, kind=""):
    email = (email or "").strip().lower()
    out = list(reversed(_media_all().get(email) or []))
    if kind:
        out = [x for x in out if x.get("kind") == kind]
    return out


def media_delete(email, mid):
    email = (email or "").strip().lower()
    d = _media_all()
    lst = d.get(email) or []
    keep, gone = [], None
    for x in lst:
        if x.get("id") == mid and gone is None:
            gone = x
        else:
            keep.append(x)
    if not gone:
        return {"error": "Not found."}
    if gone.get("local"):
        try:
            os.remove(os.path.join(MEDIA_DIR, *(gone["local"].split("/")[2:])))
        except Exception:
            pass
    d[email] = keep
    _media_write(d)
    return {"ok": True, "deleted": mid}


# Background media jobs: bounded concurrency, explicit provider errors.
_MEDIA_JOBS = {}
_MEDIA_JOB_LOCK = threading.RLock()


def media_job_start(email, kind, body):
    prompt = str(body.get("prompt") or "").strip()[:4000]
    if len(prompt) < 3:
        return {"error": "Describe the scene first."}
    with _MEDIA_JOB_LOCK:
        now = time.time()
        for jid, j in list(_MEDIA_JOBS.items()):
            if now - j["created"] > 7200 and j["status"] != "running":
                del _MEDIA_JOBS[jid]
        active = [j for j in _MEDIA_JOBS.values() if j["status"] == "running"]
        if any(j["email"] == email for j in active):
            return {"error": "Your previous generation is still running."}
        if len(active) >= 3:
            return {"error": "Generation is busy. Please retry shortly."}
        jid = os.urandom(12).hex()
        _MEDIA_JOBS[jid] = {"email": email, "status": "running", "created": now, "kind": kind}
    def run():
        try:
            r = (gen_video(prompt, body.get("duration"), bool(body.get("with_audio"))) if kind == "video"
                 else gen_image(prompt, body.get("aspect_ratio", "1:1")))
            if r.get("ok"):
                r["library"] = media_record(email, kind, prompt, r.get("videos" if kind == "video" else "images") or [],
                                             r.get("provider", ""), r.get("model", ""))
        except Exception:
            r = {"error": "Generation failed. Try again or use one of the external demos."}
        with _MEDIA_JOB_LOCK:
            _MEDIA_JOBS[jid].update(status="done" if r.get("ok") else "failed", result=r)
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "id": jid, "status": "running"}


def media_job_status(email, jid):
    with _MEDIA_JOB_LOCK:
        j = _MEDIA_JOBS.get(jid)
        if not j or j["email"] != email:
            return {"error": "Job not found. If the server restarted, check your gallery or start again."}
        return {"id": jid, "status": j["status"], "kind": j["kind"], "result": j.get("result")}


# --------------------------------------------------- chat sessions (sidebar)
_CONV_LOCK = threading.RLock()


def _conv_file():
    return os.path.join(DATA_DIR, "conversations.json")


_CONV_SYNC = {"cloud_ok": None, "error": ""}


def _conv_cache(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = _conv_file() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _conv_file())


def _conv_all():
    try:
        with open(_conv_file()) as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    if key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY"):
        remote = supabase_kv_get("conversations")
        if not isinstance(remote, dict):
            _CONV_SYNC.update(cloud_ok=False, error="Cloud history storage is unavailable. Ask the operator to run CHAT-STORAGE-SETUP.sql in Supabase and check the server credentials.")
            # Never overwrite a temporarily unreachable cloud archive with {}.
            raise RuntimeError(_CONV_SYNC["error"])
        _conv_cache(remote)
        _CONV_SYNC.update(cloud_ok=True, error="")
        return remote
    return {}


_CONV_DIRTY = threading.Event()
_CONV_FLUSH_STARTED = False
_CONV_START_LOCK = threading.Lock()


def _conv_flush_worker():
    while True:
        _CONV_DIRTY.wait()
        _CONV_DIRTY.clear()
        try:
            with _CONV_LOCK:
                snapshot = _conv_all()
            ok = supabase_kv_put("conversations", snapshot)
            _CONV_SYNC.update(cloud_ok=bool(ok), error="" if ok else "Cloud sync failed; local copy retained. Retrying.")
            if not ok:
                time.sleep(5)
                _CONV_DIRTY.set()
        except Exception:
            time.sleep(5)
            _CONV_DIRTY.set()


def _conv_write(d):
    global _CONV_FLUSH_STARTED
    _conv_cache(d)
    if key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY"):
        with _CONV_START_LOCK:
            if not _CONV_FLUSH_STARTED:
                _CONV_FLUSH_STARTED = True
                threading.Thread(target=_conv_flush_worker, daemon=True).start()
        _CONV_DIRTY.set()
    else:
        _CONV_SYNC.update(cloud_ok=False, error="Cloud history is not configured; use a persistent server disk.")


def conv_list(email):
    email = (email or "").strip().lower()
    if not email:
        return []
    with _CONV_LOCK:
        rows = []
        for c in (_conv_all().get(email) or []):
            msgs = c.get("messages") or []
            rows.append({"id": c.get("id"), "title": c.get("title") or "New chat",
                         "updated": c.get("updated") or c.get("created") or "",
                         "count": len(msgs),
                         "preview": ((msgs[-1].get("content") if msgs else "") or "")[:70]})
    rows.sort(key=lambda x: str(x.get("updated") or ""), reverse=True)
    return rows


def conv_new(email, title="New chat"):
    email = (email or "").strip().lower()
    if not email:
        return {"error": "Sign in first."}
    cid = "c" + os.urandom(5).hex()
    with _CONV_LOCK:
        d = _conv_all()
        lst = d.setdefault(email, [])
        lst.append({"id": cid, "title": (title or "New chat")[:60], "created": _now(),
                    "updated": _now(), "messages": []})
        d[email] = lst[-80:]
        _conv_write(d)
    return {"id": cid, "title": title}


def conv_get(email, cid):
    email = (email or "").strip().lower()
    with _CONV_LOCK:
        for c in (_conv_all().get(email) or []):
            if c.get("id") == cid:
                return c
    return None


def conv_append(email, cid, role, content):
    email = (email or "").strip().lower()
    if not email or not cid or not content:
        return False
    with _CONV_LOCK:
        d = _conv_all()
        lst = d.setdefault(email, [])
        target = None
        for c in lst:
            if c.get("id") == cid:
                target = c
                break
        if target is None:
            target = {"id": cid, "title": "New chat", "created": _now(), "updated": _now(),
                      "messages": []}
            lst.append(target)
        msgs = target.setdefault("messages", [])
        msgs.append({"role": role, "content": str(content)[:12000], "t": _now()})
        target["messages"] = msgs[-400:]
        target["updated"] = _now()
        if role == "user" and (target.get("title") in ("", "New chat")):
            target["title"] = " ".join(str(content).split())[:60] or "New chat"
        d[email] = lst
        _conv_write(d)
    return True


def conv_delete(email, cid):
    email = (email or "").strip().lower()
    with _CONV_LOCK:
        d = _conv_all()
        lst = [c for c in (d.get(email) or []) if c.get("id") != cid]
        d[email] = lst
        _conv_write(d)
    return {"ok": True}


def conv_rename(email, cid, title):
    email = (email or "").strip().lower()
    with _CONV_LOCK:
        d = _conv_all()
        for c in (d.get(email) or []):
            if c.get("id") == cid:
                c["title"] = (title or "New chat")[:60]
        _conv_write(d)
    return {"ok": True}


# ------------------------------------------------------------ mailbox watch
def mail_check(cfg):
    """Read-only UNSEEN scan of the user's own mailbox over IMAP. Credentials
    are their own app password, sealed at rest like every other connector."""
    import imaplib
    import email as _email_mod
    cfg = cfg or {}
    host = str(cfg.get("imap_host") or "").strip() or "imap.gmail.com"
    user = str(cfg.get("email") or "").strip()
    pw = str(cfg.get("app_password") or "").strip()
    if not user or not pw:
        return {"error": "Mail watch needs your mailbox address and an app password "
                         "(Devices → Connectors & Alerts → 📧 Mail watch)."}
    try:
        M = imaplib.IMAP4_SSL(host, 993, timeout=30)
    except Exception as e:
        return {"error": "Could not reach " + host + ": " + str(e)[:120]}
    try:
        M.login(user, pw)
        M.select("INBOX", readonly=True)
        typ, data = M.search(None, "UNSEEN")
        ids = (data[0].split() if data and data[0] else [])
        latest = []
        for i in list(ids)[-6:][::-1]:
            try:
                typ, md = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                raw = b""
                for part in (md or []):
                    if isinstance(part, tuple) and len(part) > 1:
                        raw = part[1]
                msg = _email_mod.message_from_bytes(raw or b"")
                latest.append({"from": str(msg.get("From") or "")[:120],
                               "subject": str(msg.get("Subject") or "")[:160],
                               "date": str(msg.get("Date") or "")[:60]})
            except Exception:
                continue
        return {"ok": True, "mailbox": user, "unread": len(ids), "latest": latest,
                "note": "Read-only UNSEEN scan — OraCool reports counts, senders and subjects only, "
                        "never message bodies."}
    except imaplib.IMAP4.error as e:
        return {"error": "IMAP login refused: " + str(e)[:150] +
                         " — Gmail/Outlook/Zoho need an APP PASSWORD (generate one in your account's "
                         "security settings; your normal password will not work)."}
    except Exception as e:
        return {"error": "Mail scan failed: " + str(e)[:140]}
    finally:
        try:
            M.logout()
        except Exception:
            pass


def brand_mailbox_cfg():
    """The OraCool-owned Gmail connector (Admin → OraCool-owned accounts).
    Returns the unsealed cfg or None. The owner granted the AI full access
    to THIS mailbox — it is OraCool's own address, not a user's."""
    try:
        with _BRAND_LOCK:
            items = list(_brand_data().values())
    except Exception:
        return None
    for it in items:
        if it.get("kind") == "gmail":
            try:
                secret = _brand_fernet().decrypt(it["secret"].encode()).decode()
            except Exception:
                return None
            return {"email": (it.get("target") or "oracoolai19@gmail.com").strip().lower(),
                    "app_password": secret, "imap_host": "imap.gmail.com"}
    return None

def _mail_snippet(msg):
    """Plain-text body snippet (first ~400 chars) — full-access mailbox reads."""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    p = part.get_payload(decode=True)
                    if p:
                        return str(p.decode("utf-8", "replace"))[:400]
        else:
            p = msg.get_payload(decode=True)
            if p:
                return str(p.decode("utf-8", "replace"))[:400]
    except Exception:
        pass
    return ""

def mail_read_full(cfg, n=8):
    """FULL read of the OraCool mailbox (owner-granted): latest n messages —
    senders, subjects, dates AND body snippets. Read-only (no flags changed)."""
    import imaplib
    import email as _email_mod
    cfg = cfg or {}
    host = str(cfg.get("imap_host") or "imap.gmail.com").strip()
    user = str(cfg.get("email") or "").strip()
    pw = str(cfg.get("app_password") or "").strip()
    if not user or not pw:
        return {"error": "The OraCool Gmail is not connected yet — Admin → OraCool-owned accounts → connect it with a Google app password."}
    try:
        M = imaplib.IMAP4_SSL(host, 993, timeout=30)
        M.login(user, pw)
        M.select("INBOX", readonly=True)
        typ, data = M.search(None, "ALL")
        ids = (data[0].split() if data and data[0] else [])
        typ_u, du = M.search(None, "UNSEEN")
        unseen = (du[0].split() if du and du[0] else [])
        latest = []
        for i in list(ids)[-max(1, min(int(n), 15)):][::-1]:
            try:
                typ, md = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] BODY.PEEK[TEXT])")
                raw = b""
                for part in (md or []):
                    if isinstance(part, tuple) and len(part) > 1:
                        raw += part[1]
                msg = _email_mod.message_from_bytes(raw or b"")
                latest.append({"from": str(msg.get("From") or "")[:120],
                               "subject": str(msg.get("Subject") or "")[:180],
                               "date": str(msg.get("Date") or "")[:60],
                               "body": " ".join(_mail_snippet(msg).split())})
            except Exception:
                continue
        return {"ok": True, "mailbox": user, "access": "full (owner-granted)",
                "total": len(ids), "unread": len(unseen), "latest": latest}
    except imaplib.IMAP4.error as e:
        return {"error": "IMAP login refused: " + str(e)[:150] + " — the app password may have been revoked; reconnect it in Admin → OraCool-owned accounts."}
    except Exception as e:
        return {"error": "Mail read failed: " + str(e)[:140]}
    finally:
        try:
            M.logout()
        except Exception:
            pass

def mail_send(cfg, to, subject, body):
    """Send mail FROM the OraCool address (owner-granted full access)."""
    import smtplib
    from email.mime.text import MIMEText
    cfg = cfg or {}
    user = str(cfg.get("email") or "").strip()
    pw = str(cfg.get("app_password") or "").strip()
    to = str(to or "").strip()
    if not user or not pw:
        return {"error": "The OraCool Gmail is not connected yet — Admin → OraCool-owned accounts → connect it with a Google app password."}
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", to):
        return {"error": "Enter a valid recipient address."}
    msg = MIMEText(str(body or "")[:5000], "plain")
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = str(subject or "From OraCool AI")[:180]
    try:
        s = smtplib.SMTP("smtp.gmail.com", 587, timeout=40)
        s.starttls()
        s.login(user, pw)
        s.sendmail(user, [to], msg.as_string())
        s.quit()
        return {"ok": True, "sent_from": user, "to": to, "subject": str(subject or "From OraCool AI")[:180]}
    except smtplib.SMTPAuthenticationError as e:
        return {"error": "Gmail rejected the app password: " + str(e)[:120] + " — regenerate it and reconnect in Admin → OraCool-owned accounts."}
    except Exception as e:
        return {"error": "Send failed: " + str(e)[:140]}

def mail_watch_config(email):
    st = alerts_state((email or "").strip().lower())
    for c in ((st or {}).get("connectors") or {}).values():
        if c.get("type") == "mail" and c.get("enabled", True):
            return {k: _unseal(v) for k, v in (c.get("cfg") or {}).items()}
    return None


def mail_alert_tick():
    """Every 5 minutes: poll each mail-watch connector and alert the owner when
    new unread mail arrives."""
    for em, st in list((_alerts_all() or {}).items()):
        try:
            conns = [c for c in ((st.get("connectors") or {}).values())
                     if c.get("type") == "mail" and c.get("enabled", True)]
            if not conns or not (st.get("events") or {}).get("mail", True):
                continue
            seen = st.get("mail_seen") or {}
            changed = False
            for c in conns:
                cfg = {k: _unseal(v) for k, v in (c.get("cfg") or {}).items()}
                r = mail_check(cfg)
                if r.get("error"):
                    continue
                ck = c.get("id") or "mail"
                prev = seen.get(ck)
                seen[ck] = r.get("unread")
                changed = True
                if prev is not None and int(r.get("unread") or 0) > int(prev or 0):
                    subj = " · ".join((x.get("subject") or "")[:70] for x in (r.get("latest") or [])[:3])
                    emit_event(em, "mail", "📧 " + str(r.get("unread")) + " unread message"
                               + ("s" if int(r.get("unread") or 0) != 1 else "") + " in your inbox",
                               subj or "New mail arrived in your mailbox.", "mailwatch")
            if changed:
                with _ALERTS_LOCK:
                    d = _alerts_all()
                    sst = d.get(em)
                    if sst:
                        sst["mail_seen"] = seen
                        _alerts_write(d)
        except Exception:
            continue


# ------------------------------------------------ platform logs + diagnostics
_LOG_LOCK = threading.RLock()
_LOG_RING = []
_PLATFORM_LOG = os.path.join(DATA_DIR, "platform.log")


def _log_line(tag, msg):
    line = "%s [%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), tag, str(msg)[:400])
    with _LOG_LOCK:
        _LOG_RING.append(line)
        if len(_LOG_RING) > 800:
            del _LOG_RING[:-800]
        try:
            if os.path.exists(_PLATFORM_LOG) and os.path.getsize(_PLATFORM_LOG) > 2000000:
                with open(_PLATFORM_LOG, "r", errors="replace") as f:
                    keep = f.readlines()[-2000:]
                with open(_PLATFORM_LOG, "w") as f:
                    f.writelines(keep)
            with open(_PLATFORM_LOG, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass


class _TeeOut:
    """Mirror server stdout into the platform log so the admin AI can read raw
    operational logs (Render console + in-app diagnostics)."""

    def __init__(self, real):
        self.real = real

    def write(self, s):
        try:
            self.real.write(s)
        except Exception:
            pass
        try:
            for ln in str(s).splitlines():
                if ln.strip():
                    _log_line("out", ln)
        except Exception:
            pass

    def flush(self):
        try:
            self.real.flush()
        except Exception:
            pass

    def isatty(self):
        return False


def platform_logs(lines=150, needle=""):
    want = max(1, min(int(lines or 150), 800))
    try:
        with open(_PLATFORM_LOG, "r", errors="replace") as f:
            rows = f.readlines()[-6000:]
    except Exception:
        rows = list(_LOG_RING)
    if needle:
        rows = [r for r in rows if needle.lower() in r.lower()]
    return [r.rstrip("\n") for r in rows[-want:]]


def audit_tail(limit=60):
    try:
        rows = (_cases_load().get("audit") or [])[-max(1, int(limit)):]
        return list(reversed(rows))
    except Exception:
        return []


def admin_online_payload(minutes=30):
    users = load_users()
    rows = []
    for em, u in (users or {}).items():
        rows.append({"email": em, "plan": u.get("plan") or "free",
                     "last_seen": u.get("last_seen") or "", "last_ip": u.get("last_ip") or ""})
    rows.sort(key=lambda r: str(r.get("last_seen") or ""), reverse=True)
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - minutes * 60))
    recent = [r for r in rows if str(r.get("last_seen") or "") >= cutoff]
    return {"window_minutes": minutes, "recent_count": len(recent), "accounts": rows[:25]}


def platform_diagnostics():
    out = {"online": True, "time": _now(), "uptime_sec": int(time.time() - _BOOT_TS)}
    try:
        out["persist"] = persist_status()
    except Exception:
        pass
    try:
        import resource
        out["rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:
        pass
    try:
        out["threads"] = threading.active_count()
    except Exception:
        pass
    try:
        st = os.statvfs(DATA_DIR)
        out["disk_free_mb"] = round(st.f_bavail * st.f_frsize / 1048576.0, 1)
    except Exception:
        pass
    try:
        out["users"] = len(load_users() or {})
    except Exception:
        pass
    try:
        out["brain"] = {"configured": KEYS.get("BRAIN_PROVIDER") or "auto",
                        "groq": bool(key("GROQ_API_KEY")), "agnes": bool(key("AGNES_API_KEY")),
                        "openai": bool(key("OPENAI_API_KEY"))}
    except Exception:
        pass
    try:
        al = _alerts_all() or {}
        out["alerts"] = {"accounts": len(al),
                         "connectors": sum(len((v.get("connectors") or {})) for v in al.values()),
                         "outbox": len(_outbox_load() or []),
                         "push_ready": _push_available()}
    except Exception:
        pass
    try:
        out["cases"] = len((_cases_load().get("cases") or {}))
    except Exception:
        pass
    try:
        sv = key("SUPABASE_URL")
        if sv:
            st, raw, _ = http_fetch(sv.rstrip("/") + "/rest/v1/", timeout=12,
                                    headers={"apikey": key("SUPABASE_SERVICE_KEY"),
                                             "Authorization": "Bearer " + (key("SUPABASE_SERVICE_KEY") or "")})
            out["supabase"] = {"reachable": True, "status": st}
        else:
            out["supabase"] = {"reachable": False, "error": "not configured"}
    except Exception as e:
        out["supabase"] = {"reachable": False, "error": str(e)[:120]}
    try:
        pl = platform_logs(200)
        errs = [l for l in pl if re.search(r"\b(error|traceback|exception|failed)\b", l, re.I)]
        out["log_lines"] = len(pl)
        out["recent_errors"] = errs[-5:]
    except Exception:
        pass
    try:
        out["gateway"] = {"events_last_minute": max(0, len(_gw_rl))}
    except Exception:
        pass
    return out


def _tee_platform_log():
    """Route stdout into the platform log (admin AI reads it)."""
    try:
        if not isinstance(sys.stdout, _TeeOut):
            sys.stdout = _TeeOut(sys.stdout)
    except Exception:
        pass


# ------------------------------------------------- crypto payment UX helpers
def crypto_wallet():
    """The OraCool receiving wallet for direct crypto payments (EVM address —
    USDT/USDC/ETH land here; ATLOS handles every other coin)."""
    try:
        return str(key("CRYPTO_WALLET_EVM") or "").strip()
    except Exception:
        return ""


def crypto_qr_svg_b64(text):
    if not text:
        return ""
    try:
        import qrcode
        import qrcode.image.svg
        import io
        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
        b = io.BytesIO()
        img.save(b)
        return base64.b64encode(b.getvalue()).decode()
    except Exception:
        return ""


def crypto_onchain_seen(ref):
    """Keyless on-chain peek: did anything land in the OraCool wallet after this
    order was created? Read-only public explorer data — no keys, no guessing."""
    addr = crypto_wallet()
    o = _crypto_orders().get(ref)
    if not addr or not o:
        return {"checked": False, "reason": "no receiving wallet configured"}
    since = int(o.get("created") or 0) - 900
    transfers = []
    try:
        _, raw, _ = http_fetch("https://api.ethplorer.io/getAddressHistory/" + addr +
                               "?apiKey=freekey&limit=25", timeout=25)
        d = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "{}"))
        for op in (d.get("operations") or []):
            ts = int(op.get("timestamp") or 0)
            if ts < since:
                continue
            if str(op.get("to") or "").lower() == addr.lower():
                transfers.append({"tx": str(op.get("transactionHash") or "")[:80],
                                  "value": op.get("value"),
                                  "token": ((op.get("tokenInfo") or {}).get("symbol") or "ETH"),
                                  "when": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))})
    except Exception as e:
        return {"checked": False, "reason": str(e)[:120], "wallet": addr}
    return {"checked": True, "seen": bool(transfers), "transfers": transfers[:5], "wallet": addr}


# Admin-owned brand connections. Secrets never enter the LLM or public state.
_BRAND_LOCK = threading.RLock()
_BRAND_MAIL_SEEN = None


def _brand_fernet():
    from cryptography.fernet import Fernet
    secret = str(key("ENCRYPTION_KEY") or "")
    if len(secret) < 16:
        raise ValueError("Configure a stable ENCRYPTION_KEY before connecting accounts.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("oracool-brand-v1:" + secret).encode()).digest()))


def _brand_data():
    try:
        with open(os.path.join(DATA_DIR, "brand_accounts.json")) as f:
            return json.load(f)
    except FileNotFoundError:
        if key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY"):
            d = supabase_kv_get("brand_accounts")
            if not isinstance(d, dict):
                raise ValueError("Account storage unavailable. Run CHAT-STORAGE-SETUP.sql in Supabase and check the server credentials.")
            return d
        return {}


def _brand_write(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "brand_accounts.json")
    with open(path + ".tmp", "w") as f:
        json.dump(d, f)
    os.replace(path + ".tmp", path)
    return supabase_kv_put("brand_accounts", d) if key("SUPABASE_URL") else False


def brand_public():
    with _BRAND_LOCK:
        return [{k: v for k, v in item.items() if k not in ("secret",)}
                for item in _brand_data().values()]


def _brand_probe(kind, target, secret):
    try:
        if kind == "gmail":
            if target.lower() != "oracoolai19@gmail.com":
                return {"error": "This connector is reserved for OraCool's designated Gmail inbox."}
            if not re.fullmatch(r"[a-zA-Z]{16}", secret.replace(" ", "")):
                return {"error": "Use a Google app password, not the normal Gmail password."}
            r = mail_check({"email": target, "app_password": secret.replace(" ", ""), "imap_host": "imap.gmail.com"})
            if r.get("error"):
                return {"error": "Gmail connection failed. Enable 2-Step Verification and use a new Google app password."}
            return {"ok": True, "name": target, "mail": r}
        if kind == "facebook":
            if not target.isdigit():
                return {"error": "Enter the numeric Facebook Page ID."}
            _, raw, _ = http_fetch("https://graph.facebook.com/v23.0/me?fields=id,name,category,link",
                                  headers={"Authorization": "Bearer " + secret}, timeout=15)
            d = json.loads(raw)
            if str(d.get("id")) != target or not d.get("category"):
                return {"error": "Use a Page access token for exactly this Page, not a personal-user token."}
            return {"ok": True, "name": d.get("name"), "url": "https://www.facebook.com/" + target}
        if kind == "telegram":
            if not re.fullmatch(r"\d+:[\w-]{20,}", secret) or not re.fullmatch(r"-?\d+|@[A-Za-z0-9_]{5,}", target):
                return {"error": "Enter a valid bot token and your channel/chat ID."}
            base = "https://api.telegram.org/bot" + secret
            _, raw, _ = http_fetch(base + "/getMe", timeout=15)
            d = json.loads(raw)
            if not d.get("ok"):
                return {"error": "Telegram rejected this bot token."}
            _, raw2, _ = http_fetch(base + "/getChat", method="POST", json_body={"chat_id": target}, timeout=15)
            chat = json.loads(raw2)
            if not chat.get("ok"):
                return {"error": "The bot cannot access this channel/chat. Add it to your own channel first."}
            return {"ok": True, "name": (chat.get("result") or {}).get("title") or (d.get("result") or {}).get("username"),
                    "bot": (d.get("result") or {}).get("username")}
        return {"error": "Supported connections: Gmail, Facebook Page, Telegram bot/channel. Other platforms need their official API setup."}
    except Exception:
        # Provider exceptions can contain tokens in request URLs. Never return them.
        return {"error": "Provider connection failed. Check permissions, IDs and token validity."}


def brand_connect(actor, body):
    if not is_admin(actor):
        return {"error": "Admin access required."}
    if body.get("owned") is not True:
        return {"error": "Confirm that this account belongs to OraCool and you control it."}
    kind = str(body.get("kind") or "")
    target = str(body.get("target") or "").strip()
    secret = str(body.get("credential") or "").strip()
    if not secret:
        return {"error": "Enter the app password or official platform token in the secure form."}
    try:
        fernet = _brand_fernet()
    except Exception:
        return {"error": "Secure credential storage is unavailable. Configure ENCRYPTION_KEY and cryptography first."}
    probe = _brand_probe(kind, target, secret)
    if not probe.get("ok"):
        return probe
    with _BRAND_LOCK:
        d = _brand_data()
        ident = hashlib.sha256((kind + ":" + target).encode()).hexdigest()[:16]
        d[ident] = {"id": ident, "kind": kind, "target": target, "name": probe.get("name"),
                    "url": probe.get("url", ""), "connected_by": actor, "connected_at": _now(),
                    "secret": fernet.encrypt(secret.encode()).decode()}
        cloud = _brand_write(d)
    audit_log(actor, "brand.connect", kind + ":" + target)
    return {"ok": True, "accounts": brand_public(), "cloud_saved": cloud,
            "note": "Connection verified. Credentials are encrypted and not exposed to the AI."}


def brand_inspect(actor, ident=""):
    if not is_admin(actor):
        return {"error": "Admin access required."}
    with _BRAND_LOCK:
        items = list(_brand_data().values())
    results = []
    for item in items:
        if ident and item["id"] != ident:
            continue
        try:
            secret = _brand_fernet().decrypt(item["secret"].encode()).decode()
            r = _brand_probe(item["kind"], item["target"], secret)
        except Exception:
            r = {"error": "Cannot decrypt credential. Restore the original encryption key or reconnect."}
        results.append({"id": item["id"], "kind": item["kind"], "target": item["target"], "result": r})
    return {"accounts": results, "note": "No connected accounts yet. Use Admin → OraCool-owned accounts." if not items else
            "Mailbox access is read-only headers/unread counts. Public posts require explicit confirmation in Admin."}


def brand_publish(actor, body):
    if not is_admin(actor):
        return {"error": "Admin access required."}
    if body.get("confirmed") is not True:
        return {"error": "Review the exact text and destination and confirm before publishing."}
    text = str(body.get("text") or "").strip()[:3000]
    with _BRAND_LOCK:
        item = _brand_data().get(str(body.get("id") or ""))
    if not item or not text or item["kind"] not in ("facebook", "telegram"):
        return {"error": "Choose a connected social account and enter the post text."}
    try:
        secret = _brand_fernet().decrypt(item["secret"].encode()).decode()
        if item["kind"] == "facebook":
            _, raw, _ = http_fetch("https://graph.facebook.com/v23.0/" + item["target"] + "/feed", method="POST",
                                  headers={"Authorization": "Bearer " + secret}, json_body={"message": text}, timeout=25)
            d = json.loads(raw)
            ok = bool(d.get("id"))
        else:
            _, raw, _ = http_fetch("https://api.telegram.org/bot" + secret + "/sendMessage", method="POST",
                                  json_body={"chat_id": item["target"], "text": text, "disable_web_page_preview": True}, timeout=25)
            d = json.loads(raw)
            ok = bool(d.get("ok"))
        if not ok:
            return {"error": "Provider rejected the post. Check publishing permissions."}
        audit_log(actor, "brand.publish", item["kind"] + ":" + item["target"])
        return {"ok": True, "message": "Published to the selected connected account."}
    except Exception:
        return {"error": "Publish could not be confirmed. Check the page before retrying to avoid duplicates."}


def brand_mail_tick():
    global _BRAND_MAIL_SEEN
    with _BRAND_LOCK:
        items = [x for x in _brand_data().values() if x.get("kind") == "gmail"]
    if not items:
        return
    actor = next(iter(admin_emails()), "")
    r = brand_inspect(actor, items[0]["id"])
    result = ((r.get("accounts") or [{}])[0].get("result") or {})
    mail = result.get("mail") or {}
    if result.get("ok"):
        count = int(mail.get("unread") or 0)
        latest = json.dumps(mail.get("latest") or [], sort_keys=True)
        marker = hashlib.sha256(latest.encode()).hexdigest()
        if _BRAND_MAIL_SEEN is not None and marker != _BRAND_MAIL_SEEN and count:
            notify_admins("mail", "OraCool inbox update", str(count) + " unread messages. Ask: check OraCool inbox.")
        _BRAND_MAIL_SEEN = marker


_AUTH_CACHE = {}
_AUTH_CACHE_LOCK = threading.Lock()


def request_identity(handler, body, require_supabase=False):
    token = str(body.get("access_token") or "")
    if token:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with _AUTH_CACHE_LOCK:
            cached = _AUTH_CACHE.get(digest)
        if cached and cached[0] > time.time():
            return cached[1]
        r = supabase_auth("/auth/v1/user", access_token=token)
        if r.get("status") == 200:
            em = str((r.get("data") or {}).get("email") or "").strip().lower()
            if em:
                with _AUTH_CACHE_LOCK:
                    if len(_AUTH_CACHE) > 1000:
                        _AUTH_CACHE.clear()
                    _AUTH_CACHE[digest] = (time.time() + 45, em)
                return em
    if not require_supabase:
        p = handler._auth(body)
        if p and p.get("sub"):
            return str(p["sub"]).strip().lower()
    return ""


def _body_email(handler, body):
    return body.get("_verified_email") or request_identity(handler, body)





def reminder_claim(ident):
    """Unique DB row is an at-most-once dispatch reservation across restarts/overlap.
    Unknown write outcomes fail closed rather than retrying a chargeable call."""
    if not re.fullmatch(r"[0-9a-f]{24}", ident):
        return False
    url, svc = key("SUPABASE_URL"), key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return False
    try:
        _, raw, _ = http_fetch(url.rstrip("/")+"/rest/v1/case_store?on_conflict=k", method="POST",
            headers={"apikey":svc,"Authorization":"Bearer "+svc,"Content-Type":"application/json",
                     "Prefer":"resolution=ignore-duplicates,return=representation"},
            json_body={"k":"reminder_dispatch_"+ident,"v":{"claimed_at":time.time()},"updated_at":_now()},timeout=15)
        rows=json.loads(raw)
        return isinstance(rows,list) and len(rows)==1 and rows[0].get("k")=="reminder_dispatch_"+ident
    except Exception:
        return False


def communications_allowed(email):
    # Verified identity only. Never accept a tier/admin flag or a different user's JWT from the request.
    if not email or is_blocked(email):
        return False
    if is_admin(email):
        return True
    # Cloud entitlements are authoritative when configured. An outage fails closed;
    # a stale local payment or unbound client tier token cannot unlock paid sending.
    if key("SUPABASE_URL") and key("SUPABASE_SERVICE_KEY"):
        try:
            _, raw, _ = http_fetch(key("SUPABASE_URL").rstrip("/")+"/rest/v1/subscribers?email=eq."+urllib.parse.quote(email,safe=""),
                                  headers=_supabase_headers(), timeout=10)
            rows=json.loads(raw);today=time.strftime("%Y-%m-%d",time.gmtime())
            return any(str(row.get("email") or "").strip().lower()==email.strip().lower()
                       and str(row.get("plan") or row.get("tier") or "").lower()=="enterprise"
                       and str(row.get("expires_at") or "")[:10]>=today for row in rows)
        except Exception:
            return False
    return check_tier(email) == "enterprise"


_COMMUNICATION_SERVICE = None
_COMMUNICATION_SERVICE_LOCK = threading.Lock()


_COMMUNITY_SERVICE = None
_COMMUNITY_SERVICE_LOCK = threading.Lock()


def community_rest(method, path, body=None, prefer=None):
    """Service-role REST call into the Patch-15 community tables.

    path is relative to /rest/v1 (table + query string); body is a list/dict.
    Returns (status:int, data) where data is parsed JSON on 2xx and the error
    payload otherwise. No client IP ever leaves this server; none is written
    to any community table."""
    url, svc = key("SUPABASE_URL"), key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return 503, {"message": "Supabase is not configured."}
    try:
        st, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/" + path, method=method,
                                headers={"apikey": svc, "Authorization": "Bearer " + svc,
                                         **({"Prefer": prefer} if prefer else {})},
                                json_body=body if isinstance(body, (list, dict)) else None,
                                timeout=25)
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except Exception:
            data = {"message": "HTTP %s" % e.code}
        return e.code, data
    except Exception as e:
        return 599, {"message": str(e)[:200]}
    try:
        data = json.loads(raw) if raw else []
    except Exception:
        data = str(raw)[:300]
    return st, data


# ================================================================ Patch 16
# ---- verified badge (monthly, paid) --------------------------------------------
BADGE_USD = 10


def badge_ngn():
    return int(KEYS.get("BADGE_PRICE_NGN") or os.environ.get("BADGE_PRICE_NGN") or 15500)


_VERIFIED_CACHE = {}


def _parse_iso(ts):
    try:
        from datetime import datetime, timezone
        return datetime.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


def is_verified(email):
    """Administrators are verified by default; other members while their paid
    badge is active. Verified members can only be blocked by an administrator
    and can never be reported or AI-suspended by other members."""
    email = (email or "").strip().lower()
    if not email:
        return False
    if is_admin(email):
        return True
    now = time.time()
    c = _VERIFIED_CACHE.get(email)
    if c and c[0] > now:
        return c[1]
    ok = False
    try:
        row = community_service()._prof(email)
        if row and row.get("verified_until"):
            ok = _parse_iso(row["verified_until"]) > now
    except Exception:
        ok = False
    _VERIFIED_CACHE[email] = (now + 60, ok)
    return ok


def set_verified(email, months=1):
    email = (email or "").strip().lower()
    base = time.time()
    try:
        row = community_service()._prof(email)
        if row and row.get("verified_until") and _parse_iso(row["verified_until"]) > base:
            base = _parse_iso(row["verified_until"])
    except Exception:
        pass
    until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base + months * 30 * 86400))
    try:
        community_service().store.patch("comm_profiles", "email=eq." + email, {"verified_until": until})
    except Exception:
        pass
    _VERIFIED_CACHE.pop(email, None)
    return until


def verified_status(email):
    email = (email or "").strip().lower()
    until = None
    try:
        row = community_service()._prof(email)
        if row and row.get("verified_until"):
            until = row["verified_until"]
    except Exception:
        until = None
    active = is_verified(email)
    return {"verified": active, "admin": bool(is_admin(email)),
            "verified_until": until if active and not is_admin(email) else None,
            "price_usd": BADGE_USD, "price_ngn": badge_ngn()}


# ---- Supabase storage (profile pictures + voice notes) ---------------------------
_STORAGE_BUCKETS = set()


def _storage_detail(e):
    try:
        raw = e.read()[:300].decode("utf-8", "ignore")
        try:
            return str(json.loads(raw).get("message") or raw)
        except Exception:
            return raw
    except Exception:
        return str(e)[:160]


def storage_ensure_bucket(name):
    """Returns (ok, error). Supabase requires both id AND name in the body."""
    url, svc = key("SUPABASE_URL"), key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return False, "Supabase storage is not configured on the server."
    if name in _STORAGE_BUCKETS:
        return True, ""
    try:
        http_fetch(url.rstrip("/") + "/storage/v1/bucket", method="POST",
                   headers={"apikey": svc, "Authorization": "Bearer " + svc,
                            "Content-Type": "application/json"},
                   json_body={"id": name, "name": name, "public": True}, timeout=20)
    except urllib.error.HTTPError as e:
        detail = _storage_detail(e)
        if e.code == 409 or (e.code == 400 and "exist" in detail.lower()):
            _STORAGE_BUCKETS.add(name)
            return True, ""
        return False, "Could not create the storage bucket: " + detail[:140]
    except Exception as e:
        return False, "Could not create the storage bucket: " + str(e)[:120]
    _STORAGE_BUCKETS.add(name)
    return True, ""


def storage_put(bucket, path, data, content_type):
    """Upload bytes to a public bucket; returns (public_url, error)."""
    url, svc = key("SUPABASE_URL"), key("SUPABASE_SERVICE_KEY")
    ok, err = storage_ensure_bucket(bucket)
    if not ok:
        return None, err
    try:
        http_fetch(url.rstrip("/") + "/storage/v1/object/" + bucket + "/" + path, method="POST",
                   headers={"apikey": svc, "Authorization": "Bearer " + svc,
                            "Content-Type": content_type, "x-upsert": "true"},
                   data=data, timeout=120)
        return url.rstrip("/") + "/storage/v1/object/public/" + bucket + "/" + path, ""
    except urllib.error.HTTPError as e:
        return None, "Upload failed: " + _storage_detail(e)[:160]
    except Exception as e:
        return None, "Upload failed: " + str(e)[:140]


def data_url_decode(data_url):
    """'data:<mime>;base64,<payload>' -> (mime, bytes) or (None, None)."""
    try:
        head, _, payload = str(data_url or "").partition(",")
        if not head.startswith("data:") or "base64" not in head:
            return None, None
        mime = head[5:].split(";")[0].strip().lower()
        return mime, base64.b64decode(payload)
    except Exception:
        return None, None


# ---- audio/video call signaling (P2P WebRTC; OraCool is the signal server) --------
_CALLS_LOCK = threading.Lock()
_CALLS = {}   # call_id -> dict


def _call_prune():
    now = time.time()
    with _CALLS_LOCK:
        for cid in [c for c, v in _CALLS.items()
                    if now > v.get("expires", 0) or (v.get("state") == "closed"
                                                     and now - v.get("closed_at", now) > 3600)]:
            _CALLS.pop(cid, None)


def call_start(a, b, kind):
    _call_prune()
    cid = "call-" + os.urandom(6).hex()
    with _CALLS_LOCK:
        _CALLS[cid] = {"a": a, "b": b, "kind": kind, "state": "ringing", "offer": None,
                       "answer": None, "ice_a": [], "ice_b": [], "seq_a": 0, "seq_b": 0,
                       "expires": time.time() + 300}
    return cid


def call_set_sdp(cid, who, sdp):
    with _CALLS_LOCK:
        c = _CALLS.get(cid)
        if not c or who not in (c["a"], c["b"]):
            return False
        if who == c["a"]:
            c["offer"] = sdp
            c["expires"] = time.time() + 300
        else:
            c["answer"] = sdp
            c["state"] = "active"
            c["expires"] = time.time() + 7200
    return True


def call_add_ice(cid, who, candidate):
    with _CALLS_LOCK:
        c = _CALLS.get(cid)
        if not c or who not in (c["a"], c["b"]):
            return False
        if who == c["a"]:
            c["ice_a"].append(candidate)
            c["seq_b"] += 1
        else:
            c["ice_b"].append(candidate)
            c["seq_a"] += 1
    return True


def call_close(cid, by):
    with _CALLS_LOCK:
        c = _CALLS.get(cid)
        if not c:
            return False
        c["state"] = "closed"
        c["closed_by"] = by
        c["closed_at"] = time.time()
        c["expires"] = time.time() + 60
    return True


def call_poll(email):
    """(incoming_call, updates) for this member. ICE batches are delivered once."""
    _call_prune()
    incoming = None
    updates = []
    with _CALLS_LOCK:
        for cid, c in _CALLS.items():
            if email not in (c.get("a"), c.get("b")):
                continue
            if c.get("state") == "closed":
                updates.append({"call_id": cid, "event": "closed", "by": c.get("closed_by")})
                continue
            if email == c.get("b") and c.get("state") == "ringing" and c.get("offer"):
                incoming = {"call_id": cid, "from": c.get("a"), "kind": c.get("kind"),
                            "offer": c.get("offer")}
            if c.get("state") == "active":
                mine = "a" if email == c.get("a") else "b"
                theirs = "b" if mine == "a" else "a"
                if mine == "a" and c.get("answer"):
                    updates.append({"call_id": cid, "event": "answer", "sdp": c.get("answer")})
                if c.get("ice_" + theirs):
                    updates.append({"call_id": cid, "event": "ice",
                                    "candidates": c["ice_" + theirs]})
                    c["ice_" + theirs] = []
    return incoming, updates


def record_paystack_success_from_reference(reference):
    """Verify a Paystack charge by reference and apply its effects (plan or
    verified badge). Used by the badge return/status flow."""
    secret = (key("PAYSTACK_TEST_SECRET") if key("PAYSTACK_TEST")
              else key("PAYSTACK_SECRET_KEY"))
    if not secret or not reference:
        return {"error": "Provide the payment reference."}
    try:
        _, raw, _ = http_fetch("https://api.paystack.co/transaction/verify/" + urllib.parse.quote(reference),
                               headers={"Authorization": "Bearer " + secret}, timeout=30)
        d = json.loads(raw)
    except Exception as e:
        return {"error": "Could not verify the payment: " + str(e)[:120]}
    data = d.get("data") or {}
    if not d.get("status") or str(data.get("status")) not in ("success", "authorized"):
        return {"error": "Payment not confirmed yet — wait a moment and check again."}
    r = record_paystack_success(data)
    acct = r.get("email") or ""
    return {"ok": True, "badge": bool(r.get("badge")), "verified": is_verified(acct),
            "verified_until": r.get("verified_until"), "email": acct}


def set_community_avatar(email, data_url):
    mime, data = data_url_decode(data_url)
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        return {"error": "Use a JPG, PNG or WebP picture."}
    if not data or len(data) > 2 * 1024 * 1024:
        return {"error": "Picture is too large (max 2 MB)."}
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[mime]
    url, err = storage_put("avatars", hashlib.sha1((email or "").encode()).hexdigest()[:16] + "." + ext, data, mime)
    if not url:
        return {"error": "Could not save the picture. " + (err or "Try again in a moment.")}
    try:
        community_service().store.patch("comm_profiles", "email=eq." + email.lower(), {"avatar_url": url})
    except Exception:
        pass
    return {"ok": True, "avatar_url": url}


def upload_community_media(email, data_url):
    mime, data = data_url_decode(data_url)
    _FILE_MIME = {
        "application/pdf": "pdf", "text/plain": "txt", "text/csv": "csv",
        "application/json": "json", "application/zip": "zip",
        "application/msword": "doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.ms-powerpoint": "ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    }
    if mime in ("audio/webm", "audio/ogg", "audio/mp4"):
        limit = 8 * 1024 * 1024
        label = "Voice note is too large (max about one minute)."
        # Safari/iOS cannot play WebM/Opus — transcode to AAC-in-MP4 (plays
        # everywhere) with the bundled static ffmpeg before storing.
        ext = "m4a"
        try:
            import subprocess
            import imageio_ffmpeg
            srcfn = os.path.join(_gen_dir(), "vn-src-" + os.urandom(4).hex())
            outfn = os.path.join(_gen_dir(), "vn-out-" + os.urandom(4).hex() + ".m4a")
            with open(srcfn, "wb") as _f:
                _f.write(data)
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", srcfn,
                            "-c:a", "aac", "-b:a", "64k", "-ar", "44100", outfn],
                           check=True, capture_output=True, timeout=90)
            if os.path.exists(outfn) and os.path.getsize(outfn) > 500:
                with open(outfn, "rb") as _f:
                    data = _f.read()
                mime = "audio/mp4"
            for _tmp in (srcfn, outfn):
                try:
                    os.remove(_tmp)
                except Exception:
                    pass
        except Exception:
            # transcode failed — keep the original so the note is not lost
            ext = "webm" if "webm" in mime else ("ogg" if "ogg" in mime else "m4a")
        folder = "dm"
    elif mime in ("image/jpeg", "image/png", "image/webp"):
        limit = 5 * 1024 * 1024
        label = "Photo is too large (max 5 MB)."
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[mime]
        folder = "dm"
    elif mime in ("video/mp4", "video/webm"):
        limit = 20 * 1024 * 1024
        label = "That video is too large (max 20 MB)."
        ext = "mp4" if "mp4" in mime else "webm"
        folder = "media"
    elif mime in _FILE_MIME:
        limit = 10 * 1024 * 1024
        label = "That file is too large (max 10 MB)."
        ext = _FILE_MIME[mime]
        folder = "files"
    else:
        return {"error": "Unsupported type — send a photo, video, voice note or a file (PDF, Word, Excel, PowerPoint, TXT, CSV, ZIP, JSON)."}
    if not data or len(data) > limit:
        return {"error": label}
    path = "%s/%s_%d.%s" % (folder, hashlib.sha1((email or "").encode()).hexdigest()[:8], int(time.time()), ext)
    url, err = storage_put("media", path, data, mime)
    if not url:
        return {"error": "Could not save that. " + (err or "Try again in a moment.")}
    return {"ok": True, "media_url": url}


def community_service():
    """Member chat + reports + the AI moderator. Dependencies are late-bound so tests can patch them."""
    global _COMMUNITY_SERVICE
    with _COMMUNITY_SERVICE_LOCK:
        if _COMMUNITY_SERVICE is None:
            deps = {"is_admin": lambda e: is_admin(e),
                    "block_user": lambda e, b, r="", by="": block_user(e, b, r, by),
                    "is_blocked": lambda e: is_blocked(e),
                    "load_users": lambda: load_users(),
                    "check_tier": lambda e: check_tier(e),
                    "is_verified": lambda e: is_verified(e),
                    "llm_complete": lambda msgs, t=0.0, mx=700: llm_complete(msgs, temperature=t, max_tokens=mx),
                    "notify_admins": lambda et, ti, bo="": notify_admins(et, ti, bo),
                    "emit_event": lambda em, et, ti, bo="": emit_event(em, et, ti, bo)}
            _COMMUNITY_SERVICE = community.Service(community_rest, deps)
        return _COMMUNITY_SERVICE


_STICKERS_MAX = 30


def _stickers_key(email):
    return "stickers:" + (email or "").strip().lower()


def _stickers_load(email):
    try:
        d = supabase_kv_get(_stickers_key(email))
        lst = d.get("items") if isinstance(d, dict) else None
        return [s for s in (lst or []) if isinstance(s, dict) and s.get("url")][:_STICKERS_MAX]
    except Exception:
        return []


def _stickers_put(email, items):
    try:
        return bool(supabase_kv_put(_stickers_key(email), {"items": items[:_STICKERS_MAX]}))
    except Exception:
        return False


def stickers_save(email, data_url, label=""):
    """patch31: user-made stickers (image + top/bottom text drawn on the client).
    The PNG rides the normal community media upload; only its URL is listed here."""
    email = (email or "").strip().lower()
    if not email:
        return {"error": "Sign in first."}
    r = upload_community_media(email, data_url)
    if not r or r.get("error") or not r.get("media_url"):
        return r if r and r.get("error") else {"error": "Sticker image could not be saved."}
    items = _stickers_load(email)
    items.insert(0, {"url": r["media_url"], "label": str(label or "")[:40], "t": _now()})
    _stickers_put(email, items)
    return {"ok": True, "url": r["media_url"], "stickers": items[:_STICKERS_MAX]}


def stickers_list(email):
    return {"ok": True, "stickers": _stickers_load((email or "").strip().lower())}


def stickers_delete(email, url):
    email = (email or "").strip().lower()
    items = [s for s in _stickers_load(email) if s.get("url") != url]
    _stickers_put(email, items)
    return {"ok": True, "stickers": items}


def community_route(action, body, self_host=""):
    try:
        svc = community_service()
        me = body["email"]
        if action == "setup":
            return svc.setup_status()
        if action == "me":
            return svc.me(me)
        if action == "profile":
            return svc.update_profile(me, body.get("username", body.get("handle")), body.get("bio"))
        if action == "rooms":
            return {"rooms": svc.rooms(me)}
        if action == "rooms/create":
            # patch31: groups/channels are always private — the creator adds members by number
            return svc.create_room(me, body.get("name"), str(body.get("kind") or "group"),
                                   body.get("description") or "")
        if action == "rooms/join":
            return svc.join_room(me, str(body.get("slug") or body.get("room") or ""))
        if action == "rooms/members":
            return svc.room_members(me, str(body.get("slug") or body.get("room") or ""))
        if action == "rooms/add-member":
            return svc.room_add_member(me, str(body.get("slug") or body.get("room") or ""),
                                       str(body.get("who") or body.get("number") or ""))
        if action == "rooms/remove-member":
            return svc.room_remove_member(me, str(body.get("slug") or body.get("room") or ""),
                                          str(body.get("who") or body.get("number") or ""))
        if action == "rooms/leave":
            return svc.room_leave(me, str(body.get("slug") or body.get("room") or ""))
        if action == "stickers/save":
            return stickers_save(me, body.get("data_url"), body.get("label"))
        if action == "stickers/list":
            return stickers_list(me)
        if action == "stickers/delete":
            return stickers_delete(me, str(body.get("url") or ""))
        if action == "room/messages":
            return svc.room_messages(me, str(body.get("room") or ""), body.get("after"))
        if action == "room/send":
            return svc.room_send(me, str(body.get("room") or ""), body.get("body"),
                                 media_url=str(body.get("media_url") or ""))
        if action == "people":
            return svc.people(me, body.get("q"))
        if action == "lookup":
            return svc.lookup(me, body.get("number"))
        if action == "friends":
            return svc.friends(me)
        if action == "friend/add":
            return svc.add_friend(me, body.get("number"))
        if action == "dm/threads":
            return svc.dm_threads(me)
        if action == "dm/messages":
            return svc.dm_messages(me, body.get("username") or body.get("handle"), body.get("after"))
        if action == "dm/send":
            return svc.dm_send(me, body.get("username") or body.get("handle"), body.get("body"),
                               body.get("media_url"))
        if action == "report":
            ids = body.get("message_ids") or ([body["message_id"]] if body.get("message_id") else [])
            return svc.report(me, body.get("username") or body.get("handle"), body.get("reason"),
                              [str(x) for x in ids if x], number=body.get("number"))
        if action == "rooms/report":
            return svc.report_room(me, str(body.get("slug") or body.get("room") or ""), body.get("reason"))
        if action == "rooms/delete":
            return svc.delete_room(me, str(body.get("slug") or body.get("room") or ""))
        if action == "rooms/owner-only":
            return svc.room_set_owner_only(me, str(body.get("slug") or body.get("room") or ""), bool(body.get("on")))
        if action == "react":
            return svc.react(me, body.get("message_id"), body.get("emoji"))
        if action == "game/start":
            return svc.game_start(me, body.get("username") or body.get("handle"))
        if action == "game/move":
            return svc.game_move(me, body.get("game_id"), body.get("idx"))
        if action == "avatar":
            return set_community_avatar(me, body.get("data_url"))
        if action == "media":
            return upload_community_media(me, body.get("data_url"))
        if action == "badge":
            return verified_status(me)
        if action == "badge/pay":
            _host = (self_host or "").split(":")[0]
            _site = str(body.get("callback_url") or key("PUBLIC_BASE_URL") or (("https://" + _host) if "." in _host else "")).strip()
            return paystack_initialize(me, _site, "verified", body.get("currency"))
        if action == "badge/status":
            return record_paystack_success_from_reference(str(body.get("reference") or ""))
        if action == "call/start":
            peer = svc.email_of(str(body.get("peer") or ""))
            if not peer or peer == me:
                return {"error": "No member with that username to call."}
            cid = call_start(me, peer, "video" if body.get("video") else "audio")
            return {"ok": True, "call_id": cid, "peer": svc.handle_of(peer), "kind": "video" if body.get("video") else "audio"}
        if action == "call/sdp":
            if not call_set_sdp(str(body.get("call_id") or ""), me, str(body.get("sdp") or "")):
                return {"error": "Call not found (it may have expired)."}
            return {"ok": True}
        if action == "call/ice":
            if not call_add_ice(str(body.get("call_id") or ""), me, body.get("candidate")):
                return {"error": "Call not found."}
            return {"ok": True}
        if action == "call/hangup":
            call_close(str(body.get("call_id") or ""), me)
            return {"ok": True}
        if action == "call/poll":
            inc, ups = call_poll(me)
            if inc:
                inc["from_name"] = svc.handle_of(inc.get("from"))
            return {"incoming": inc, "updates": ups}
        return {"error": "Unknown community action."}
    except community.CommunitySetup:
        return {"setup_required": True,
                "message": "The community database is being set up. Run the Patch 15 SQL in Supabase (Dashboard → SQL Editor) and it will be live within a minute."}


def communication_service():
    global _COMMUNICATION_SERVICE
    with _COMMUNICATION_SERVICE_LOCK:
        if _COMMUNICATION_SERVICE is None:
            _COMMUNICATION_SERVICE = communications.Service(supabase_kv_get, supabase_kv_put, key, _brand_fernet, communications_allowed, reminder_claim)
        return _COMMUNICATION_SERVICE


_CHAT_TOUCH_TIMES = {}
_CHAT_TOUCH_LOCK = threading.Lock()


def chat_touch_async(email, ip):
    with _CHAT_TOUCH_LOCK:
        if time.time() - _CHAT_TOUCH_TIMES.get(email, 0) < 120:
            return
        if len(_CHAT_TOUCH_TIMES)>10000:
            _CHAT_TOUCH_TIMES.clear()
        _CHAT_TOUCH_TIMES[email] = time.time()
    threading.Thread(target=lambda: touch_user(email, last_ip=ip), daemon=True).start()


_ASR_LIMITS = {}
_ASR_LOCK = threading.Lock()


def voice_transcribe(body, email):
    """Short opt-in audio only. No audio files are written on this server."""
    with _ASR_LOCK:
        now = time.time()
        times = [t for t in _ASR_LIMITS.get(email, []) if t > now-60]
        if len(times)>=12:
            return {"error":"Voice transcription limit reached. Please pause briefly."}
        if len(_ASR_LIMITS)>10000:
            _ASR_LIMITS.clear()
        _ASR_LIMITS[email] = times+[now]
    raw = str(body.get("audio") or "")
    if len(raw)>2200000:
        return {"error":"Audio clip is too large. Use shorter phrases."}
    try:
        audio = base64.b64decode(raw, validate=True)
    except Exception:
        return {"error":"Invalid audio clip."}
    mime = str(body.get("mime") or "").split(";")[0]
    ext = {"audio/webm":"webm", "audio/mp4":"m4a", "audio/ogg":"ogg", "audio/wav":"wav"}.get(mime)
    if not ext or not 100 <= len(audio) <= 1600000:
        return {"error":"Unsupported or empty recording. Use text input in this browser."}
    k = key("GROQ_API_KEY") or key("OPENAI_API_KEY")
    if not k:
        return {"error":"Speech recognition is not configured for this browser. Use a browser with speech recognition, or type."}
    groq = bool(key("GROQ_API_KEY"))
    url = "https://api.groq.com/openai/v1/audio/transcriptions" if groq else "https://api.openai.com/v1/audio/transcriptions"
    model = "whisper-large-v3-turbo" if groq else "whisper-1"
    boundary = "OraVoice"+os.urandom(12).hex()
    payload = ("--"+boundary+'\r\nContent-Disposition: form-data; name="model"\r\n\r\n'+model+'\r\n--'+boundary+
               '\r\nContent-Disposition: form-data; name="file"; filename="voice.'+ext+'"\r\nContent-Type: '+mime+'\r\n\r\n').encode()+audio+("\r\n--"+boundary+"--\r\n").encode()
    try:
        _, data, _ = http_fetch(url, method="POST", headers={"Authorization":"Bearer "+k,"Content-Type":"multipart/form-data; boundary="+boundary},data=payload,timeout=30)
        text = str(json.loads(data).get("text") or "").strip()[:2000]
        return {"text":text}
    except Exception:
        return {"error":"Transcription provider unavailable. Check server configuration/credit or use text input."}


def main():
    _load_keys()
    try:  # patch41: remote key vault (fills keys missing from env/keys.json; refreshed every 5 min)
        _vault_refresh(force=True)
        threading.Thread(target=_vault_loop, daemon=True).start()
    except Exception:
        pass
    try:  # background watchlist monitor (continuous dark-web/leak alerts)
        threading.Thread(target=_watch_loop, daemon=True).start()
    except Exception:
        pass
    try:  # durable case/evidence mirror to Supabase (survives Render redeploys)
        threading.Thread(target=_cases_flush_loop, daemon=True).start()
    except Exception:
        pass
    try:  # patch46: durable state — restore what a fresh Render instance lacks, then mirror every change
        _n = _persist_restore()
        _log_line("persist", _PERSIST.get("note") or ("restored %d" % _n))
        threading.Thread(target=_persist_loop, daemon=True).start()
        import signal as _signal
        _signal.signal(_signal.SIGTERM, _persist_flush_and_exit)
    except Exception as _e:
        _log_line("persist", "setup failed: %s" % _e)
    try:  # 24/7 alerts platform: outbox delivery, digests, Supabase kv mirror
        threading.Thread(target=_alerts_loops, daemon=True).start()
    except Exception:
        pass
    try:
        _tee_platform_log()
    except Exception:
        pass
    _log_line("boot", "OraCool server starting on http://%s:%s" % (HOST, PORT))
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"OraCool AI server (v2) running on http://{HOST}:{PORT}")
    print("Keys loaded:", sum(1 for v in KEYS.values() if isinstance(v, str) and v.strip() and not v.startswith('_')))
    server.serve_forever()


if __name__ == "__main__":
    main()
