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
import ssl
import threading
import time
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

PORT = int(os.environ.get("PORT", "8000"))
HOST = "0.0.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UA = "OraCoolAI/1.0 (personal assistant)"

# Groq rotates model names; the default below is verified working (Sep 2026).
GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"

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
                 "JWT_SECRET", "ENCRYPTION_KEY", "ENCRYPTION_IV"):
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

def key(name):
    v = KEYS.get(name)
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
        return payload
    except Exception:
        return None

TIER_RANK = {"free": 0, "starter": 1, "pro": 2, "ultra": 3, "enterprise": 4}

PLANS = {
    "starter":    {"label": "Starter",    "price_usd": 20,  "price_ngn": 20000,  "days": 30},
    "pro":        {"label": "PRO",        "price_usd": 50,  "price_ngn": 50000,  "days": 30},
    "ultra":      {"label": "Ultra",      "price_usd": 100, "price_ngn": 100000, "days": 30},
    "enterprise": {"label": "Enterprise", "price_usd": 500, "price_ngn": 500000, "days": 30},
}

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
    rec = users.get(email) or {"created": time.strftime("%Y-%m-%d %H:%M:%S")}
    rec["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rec.update(extra)
    users[email] = rec
    save_users(users)

def user_record(email):
    if not email:
        return None
    return load_users().get(email.strip().lower())

def is_blocked(email):
    rec = user_record(email)
    return bool(rec and rec.get("blocked"))

def block_user(email, blocked, reason="", by=""):
    email = (email or "").strip().lower()
    if not email:
        return {"error": "Email required."}
    users = load_users()
    rec = users.get(email) or {"created": time.strftime("%Y-%m-%d %H:%M:%S")}
    rec["blocked"] = bool(blocked)
    rec["block_reason"] = reason if blocked else ""
    rec["blocked_by"] = by if blocked else ""
    rec["blocked_at"] = time.strftime("%Y-%m-%d %H:%M:%S") if blocked else ""
    users[email] = rec
    save_users(users)
    return {"ok": True, "user": rec}


def admin_set_pro(email, tier, days=30, by=""):
    """Manually grant (or revoke) a subscription from the admin console."""
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "Enter a valid email address."}
    if tier in ("", None, "free"):
        subs = [s for s in load_subscribers() if (s.get("email") or "").lower() != email]
        try:
            with _sub_lock:
                with open(_sub_file(), "w") as f:
                    json.dump(subs, f, indent=2)
        except Exception:
            pass
        touch_user(email, pro=False)
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
                   json_body={"email": rec.get("email"), "tier": "pro",
                              "reference": rec.get("reference"),
                              "paid_at": rec.get("paid_at"),
                              "expires_at": rec.get("expires_at")},
                   timeout=10)
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


def auth_signup(email, password):
    """Create the account (auto-confirmed via admin API) and log the user in."""
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "Enter a valid email address."}
    errs = password_strength(password)
    if errs:
        return {"error": "Password needs: " + ", ".join(errs) + "."}
    url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
    if not url or not svc:
        return {"error": "Supabase is not configured on the server."}
    try:
        http_fetch(url.rstrip("/") + "/auth/v1/admin/users", method="POST",
                   headers={"apikey": svc, "Authorization": "Bearer " + svc,
                            "Content-Type": "application/json"},
                   json_body={"email": email, "password": password,
                              "email_confirm": True}, timeout=25)
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            d = {}
        msg = d.get("msg") or d.get("error_description") or ("error " + str(e.code))
        if e.code == 422:
            return {"error": "That email is already registered — please sign in."}
        return {"error": "Signup failed: " + str(msg)[:200]}
    except Exception as e:
        return {"error": "Signup failed: " + str(e)}
    r = supabase_auth("/auth/v1/token?grant_type=password", method="POST",
                      json_body={"email": email, "password": password})
    if r.get("status") == 200:
        u = r.get("data", {}).get("user") or {}
        touch_user(email)
        return {"status": 200, "data": r["data"], "admin": is_admin(email),
                "blocked": is_blocked(email), "pro_token": check_subscription(email)}
    return {"error": "Account created — please sign in now."}


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
            q = urllib.parse.quote(f"email=eq.{email}")
            _, raw, _ = http_fetch(url.rstrip("/") + "/rest/v1/subscribers?" + q,
                                   headers={"apikey": svc, "Authorization": "Bearer " + svc},
                                   timeout=10)
            rows.extend(json.loads(raw))
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
        out["breaches"] = {"error": "HaveIBeenPwned key not set — add one in Settings or keys.json "
                                    "(https://haveibeenpwned.com/API/Key)."}

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

def weather(city=None, lat=None, lon=None):
    # Works globally: any city or country name, or direct coordinates.
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

def admin_users_payload():
    users = load_users()
    accounts = _load_accounts()
    subs = load_subscribers()
    sub_emails = {str(s.get("email", "")).lower() for s in subs}
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
        out.append({"email": email, "created": rec.get("created"), "last_seen": rec.get("last_seen"),
                    "blocked": bool(rec.get("blocked")), "block_reason": rec.get("block_reason") or "",
                    "admin": is_admin(email), "pro": is_admin(email) or email in sub_emails,
                    "equity": equity, "pnl": pnl})
    out.sort(key=lambda x: (x.get("pnl") is None, -(x.get("pnl") or 0)))
    stats = {"users": len(out),
             "blocked": sum(1 for u in out if u["blocked"]),
             "pro": sum(1 for u in out if u["pro"]),
             "total_pnl": round(sum(u.get("pnl") or 0 for u in out), 2)}
    return {"stats": stats, "users": out, "admins": admin_emails()}


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


def _require_admin(self, body):
    payload = self._auth(body)
    if not payload:
        self._send_json({"locked": True, "message": "Admin access required."}, 403)
        return None
    if payload.get("admin") or is_admin(payload.get("sub")):
        return payload
    self._send_json({"locked": True, "message": "Admin access required."}, 403)
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
    if plan not in PLANS:
        plan = "pro"
    p = PLANS[plan]
    currency = (currency or KEYS.get("PAYSTACK_CURRENCY") or "NGN").upper()
    if currency not in ("NGN", "USD"):
        currency = "NGN"
    if currency == "USD":
        amount = p["price_usd"] * 100   # Paystack USD accounts charge in cents
    else:
        currency = "NGN"
        amount = p["price_ngn"] * 100
    label = p["label"] + " · " + str(p["days"]) + " days"
    try:
        _, raw, _ = http_fetch("https://api.paystack.co/transaction/initialize",
                               method="POST", headers={"Authorization": "Bearer " + secret},
                               json_body={"email": email, "amount": amount,
                                          "currency": currency, "callback_url": callback_url,
                                          "metadata": {"product": "OraCool AI", "plan": plan}},
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
    plan = ((data.get("metadata") or {}).get("plan") or "pro").lower()
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
        return {"status": "success", "token": make_tier_token(rec.get("email"), rec.get("plan")),
                "subscriber": rec}
    except Exception as e:
        return {"error": f"Verification failed: {e}"}

# ---------------------------------------------------------------- tier helpers

FREE_TOOLS = ["ip", "domain", "email", "username", "phone", "weather", "stock", "crypto", "fred"]
PRO_TOOLS = ["search", "shodan", "virustotal", "abuseipdb", "urlscan", "leakcheck"]

def get_config():
    def loaded(names):
        return {n: bool(key(n)) for n in names}
    return {
        "brain": {"openai": bool(key("OPENAI_API_KEY")), "groq": bool(key("GROQ_API_KEY")),
                  "default_provider": KEYS.get("BRAIN_PROVIDER", "groq"),
                  "default_model": KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL),
                  "fast_model": KEYS.get("GROQ_FAST_MODEL", "allam-2-7b"),
                  "chat_max_tokens": int(KEYS.get("CHAT_MAX_TOKENS", 600))},
        "keys": loaded(["OPENAI_API_KEY", "GROQ_API_KEY", "PAYSTACK_SECRET_KEY",
                        "SUPABASE_URL", "GITHUB_TOKEN", "SHODAN_API_KEY",
                        "VIRUSTOTAL_API_KEY", "ABUSEIPDB_API_KEY", "IPINFO_API_KEY",
                        "NUMVERIFY_API_KEY", "LEAKCHECK_API_KEY", "URLSCAN_API_KEY",
                        "TAVILY_API_KEY", "FINNHUB_API_KEY", "COINGECKO_API_KEY",
                        "FRED_API_KEY", "ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET", "NASA_API_KEY"]),
        "paystack_public_key": key("PAYSTACK_PUBLIC_KEY") if not key("PAYSTACK_TEST") else key("PAYSTACK_TEST_PUBLIC"),
        "paystack_test": bool(key("PAYSTACK_TEST")),
        "paystack_currency": (KEYS.get("PAYSTACK_CURRENCY") or "NGN").upper(),
        "pro_price_ngn": int(KEYS.get("PRO_PRICE_NGN", 5000)),
        "pro_label": KEYS.get("PRO_LABEL", "PRO Access · 90 days"),
        "free_tools": FREE_TOOLS, "pro_tools": PRO_TOOLS,
        "auth": {"supabase": bool(key("SUPABASE_URL") and key("SUPABASE_ANON_KEY")),
                 "github": bool(key("GITHUB_TOKEN"))},
        "trading": {"symbols": list(TRADING_SYMBOLS.keys()),
                    "alpaca_ready": bool(key("ALPACA_PAPER_KEY_ID") and key("ALPACA_PAPER_SECRET"))},
        "plans": [{"id": pid, "label": p["label"], "price_usd": p["price_usd"],
                   "price_ngn": p["price_ngn"], "days": p["days"], "rank": TIER_RANK[pid]}
                  for pid, p in PLANS.items()],
        "tiers": list(TIER_RANK.keys()),
        "nasa_ready": bool(key("NASA_API_KEY")),
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
              "price_ngn": p["price_ngn"], "days": p["days"], "rank": TIER_RANK[pid]}
             for pid, p in PLANS.items()]
    plans.sort(key=lambda x: x["rank"])
    return {"tier": tier, "email": email, "plans": plans,
            "note": "Admins receive full Enterprise access automatically."}

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
    if not any(k in low for k in ("stock", "share", "ticker", "quote", "price of", "$")):
        return None
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

def auto_tools(text, tier="free"):
    """Detect intent in the user's message and RUN the matching live tool(s)."""
    t = (text or "").strip()
    if not t:
        return []
    low = t.lower()
    out = []
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
    if ipm:
        out.append({"tool": "ip", "label": "IP " + ipm.group(1), "result": _shrink(osint_ip(ipm.group(1)))})
    # domain
    if not ipm:
        dm = re.search(r"\b([a-z0-9-]+\.(?:com|net|org|io|co|ng|dev|ai|me|xyz|app|info|biz))\b", low)
        if dm and any(k in low for k in ("whois", "domain", "dns", "lookup", "website", "site", "check")):
            out.append({"tool": "domain", "label": "domain " + dm.group(1),
                        "result": _shrink(osint_domain(dm.group(1)))})
    # email breach
    em = re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", low)
    if em and any(k in low for k in ("breach", "hack", "pwned", "leak", "email", "investigate", "check")):
        out.append({"tool": "email", "label": "breach · " + em.group(0),
                    "result": _shrink(osint_email(em.group(0), None))})
    # phone
    ph = re.search(r"\+?\d[\d\s\-()]{8,}", t)
    if ph and any(k in low for k in ("phone", "number", "trace", "carrier", "who called", "caller")):
        out.append({"tool": "phone", "label": "phone " + ph.group(0),
                    "result": _shrink(phone_intel.lookup(ph.group(0), key_lookup=key, http_fetch=http_fetch))})
    # space
    if any(k in low for k in ("picture of the day", "apod", "space picture", "nasa picture", "picture today")):
        out.append({"tool": "space", "label": "NASA picture of the day", "result": _shrink(space_apod())})
    if any(k in low for k in ("asteroid", "neo", "near earth")):
        out.append({"tool": "space", "label": "near-earth objects", "result": _shrink(space_neo())})
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
    # PRO-tier tools (search, shodan, virustotal, abuseipdb, urlscan, leakcheck)
    if tier_gte(tier, "pro"):
        if any(k in low for k in ("search for", "search the web", "web search", "google ")):
            out.append({"tool": "search", "label": "web search",
                        "result": _shrink(pro_search(t, 3), 1500)})
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
    return out[:4]


def tool_context(tools):
    """Build a compact system message grounding the AI in live tool results."""
    if not tools:
        return None
    parts = []
    for t in tools:
        parts.append("Tool %s (%s) returned: %s" % (t["tool"], t["label"], t["result"]))
    return ("OraCool just ran these live tools itself. The results below are REAL, current data. "
            "Answer using them, in your calm JARVIS voice, and mention the key figures.\n\n"
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


def analyze_file(name, mime, data_b64):
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
    elif mime.startswith("image/"):
        return {"name": name, "type": "image", "chars": 0, "text": "",
                "note": "This is an image. Reading it requires a vision model (OpenAI gpt-4o); "
                        "your OpenAI account has no credits right now. Add billing and I'll analyze images too."}
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

def passkey_register(email, credential):
    email = (email or "").strip().lower()
    if not email or not credential:
        return {"error": "Email and credential are required."}
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
                url = key("SUPABASE_URL"); svc = key("SUPABASE_SERVICE_KEY")
                sup_user = None
                if url and svc:
                    try:
                        q = urllib.parse.quote(f"email=eq.{email}")
                        _, raw, _ = http_fetch(url.rstrip("/") + "/auth/v1/admin/users?" + q,
                                               headers={"apikey": svc, "Authorization": "Bearer " + svc},
                                               timeout=15)
                        users = json.loads(raw).get("users") or []
                        if users:
                            sup_user = users[0]
                    except Exception:
                        sup_user = None
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

def tracker_create(url, email, name=""):
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return {"error": "Enter a full URL starting with http:// or https://"}
    try:
        host = urllib.parse.urlparse(url).netloc
    except Exception:
        return {"error": "Invalid URL."}
    if not host or "." not in host:
        return {"error": "That URL has no valid domain."}
    slug = _slugify(name) or _slugify(host) or _slugify(host.split(".")[0])
    base = slug
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
            "note": "Send this link. When someone opens it, their visit is logged and "
                    "they are instantly sent to the real site."}

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

class Handler(BaseHTTPRequestHandler):
    server_version = "OraCool/2.0"
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
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
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, text):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
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
        self._send_json({"locked": True, "tier": tier,
                         "message": f"This space tool requires the {required.title()} plan (you are on {tier.title()})."}, 402)
        return None

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_file(os.path.join(BASE_DIR, "index.html"), "text/html; charset=utf-8")
        elif path == "/oauth":
            self._send_file(os.path.join(BASE_DIR, "oauth.html"), "text/html; charset=utf-8")
        elif path == "/manifest.json":
            self._send_file(os.path.join(BASE_DIR, "manifest.json"), "application/json")
        elif path == "/icon.png":
            icon = os.path.join(BASE_DIR, "icon.png")
            if os.path.exists(icon):
                self._send_file(icon, "image/png")
            else:
                self.send_error(404)
        elif path == "/sw.js":
            self._send_file(os.path.join(BASE_DIR, "sw.js"), "text/javascript")
        elif path.startswith("/t/"):
            slug = path.split("/")[-1]
            ip = self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]
            tracker_hit(slug, ip, self.headers.get("User-Agent", ""),
                        self.headers.get("Referer", ""))
            self._send_html(tracker_page(slug))
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
        elif path == "/api/health":
            self._send_json({"status": "online", "name": "OraCool AI", "version": "2.0",
                             "time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())})
        elif path == "/api/config":
            self._send_json(get_config())
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
        try:
            # ---- free OSINT
            if path == "/api/osint/ip":
                self._send_json(osint_ip(body.get("ip")))
            elif path == "/api/osint/domain":
                self._send_json(osint_domain(body.get("domain")))
            elif path == "/api/osint/email":
                self._send_json(osint_email(body.get("email"), body.get("hibp_key")))
            elif path == "/api/osint/username":
                self._send_json(osint_username(body.get("username")))
            elif path == "/api/osint/phone":
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
                if self._require_tier(body, "starter"):
                    self._send_json(space_apod(body.get("date"), body.get("count", 1)))
            elif path == "/api/space/epic":
                if self._require_tier(body, "pro"):
                    self._send_json(space_epic())
            elif path == "/api/space/neo":
                if self._require_tier(body, "pro"):
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
            elif path == "/api/files/analyze":
                self._send_json(analyze_file(body.get("name"), body.get("mime"), body.get("data_b64")))
            # ---- link tracker (visitor intelligence) ----
            elif path == "/api/tracker/create":
                self._send_json(tracker_create(body.get("url"), body.get("email"), body.get("name")))
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
            elif path == "/api/admin/users":
                if _require_admin(self, body):
                    self._send_json(admin_users_payload())
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
            # ---- PRO tools (JWT-gated)
            elif path == "/api/pro/search":
                if self._require_pro(body):
                    self._send_json(pro_search(body.get("query"), body.get("max_results", 5)))
            elif path == "/api/pro/shodan":
                if self._require_pro(body):
                    self._send_json(pro_shodan(body.get("ip")))
            elif path == "/api/pro/virustotal":
                if self._require_pro(body):
                    self._send_json(pro_virustotal(body.get("target"), body.get("kind", "domain")))
            elif path == "/api/pro/abuseipdb":
                if self._require_pro(body):
                    self._send_json(pro_abuseipdb(body.get("ip"), body.get("days", 90)))
            elif path == "/api/pro/urlscan":
                if self._require_pro(body):
                    self._send_json(pro_urlscan(body.get("url")))
            elif path == "/api/pro/leakcheck":
                if self._require_pro(body):
                    self._send_json(pro_leakcheck(body.get("query"), body.get("type", "email")))
            # ---- payments
            elif path == "/api/pro/trial":
                email = (body.get("email") or "trial@oracool.local").strip()
                token = sign_jwt({"sub": email, "tier": "pro", "trial": True,
                                  "iat": int(time.time()), "exp": int(time.time()) + 86400},
                                 key("JWT_SECRET") or "dev-secret")
                self._send_json({"token": token, "trial": True, "expires_in_hours": 24})
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
                self._send_json(auth_signup(body.get("email"), body.get("password")))
            elif path == "/api/auth/login":
                email = (body.get("email") or "").strip()
                r = supabase_auth("/auth/v1/token?grant_type=password", method="POST",
                                  json_body={"email": email, "password": body.get("password")})
                if r.get("status") == 200:
                    u = r.get("data", {}).get("user") or {}
                    uemail = u.get("email") or email
                    touch_user(uemail)
                    r["admin"] = is_admin(uemail)
                    r["blocked"] = is_blocked(uemail)
                    r["pro_token"] = check_subscription(uemail)
                    r["requires_2fa"] = twofa_enabled(uemail)
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
                self._send_json(passkey_register(body.get("email"), body.get("credential")))
            elif path == "/api/auth/passkey/challenge":
                self._send_json(passkey_challenge())
            elif path == "/api/auth/passkey/login":
                self._send_json(passkey_login(body.get("credentialId")))
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
            # ---- chat
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
    def _resolve_provider(self, body):
        """Return (keyv, base_url, model) resolved from body or server vault."""
        api_key = (body.get("api_key") or "").strip()
        base_url = (body.get("base_url") or "").strip().rstrip("/")
        model = (body.get("model") or "").strip()
        provider = (body.get("provider") or "auto").lower()

        if api_key:
            return api_key, base_url or "https://api.openai.com/v1", model or "gpt-4o-mini", provider

        if provider == "groq":
            k = key("GROQ_API_KEY")
            return k, "https://api.groq.com/openai/v1", model or KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL), provider
        if provider == "openai":
            k = key("OPENAI_API_KEY")
            return k, "https://api.openai.com/v1", model or "gpt-4o-mini", provider
        # auto: honour BRAIN_PROVIDER (keys.json), fall back to whichever key exists
        auto_provider = KEYS.get("BRAIN_PROVIDER", "openai")
        if auto_provider == "groq" and key("GROQ_API_KEY"):
            return key("GROQ_API_KEY"), "https://api.groq.com/openai/v1", model or KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL), provider
        if key("OPENAI_API_KEY"):
            return key("OPENAI_API_KEY"), "https://api.openai.com/v1", model or "gpt-4o-mini", provider
        if key("GROQ_API_KEY"):
            return key("GROQ_API_KEY"), "https://api.groq.com/openai/v1", model or KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL), provider
        return "", "", model or "gpt-4o-mini", provider

    def _handle_chat(self, body):
        messages = body.get("messages") or []
        stream = bool(body.get("stream", True))
        api_key, base_url, model, provider = self._resolve_provider(body)

        if not messages:
            self._send_json({"error": "No messages provided."}, 400)
            return

        # ---- agentic tools: the AI runs live tools itself when the message asks for data
        tool_runs = []
        if body.get("tools", True):
            tier = "free"
            tok = (body.get("token") or "").strip()
            if tok:
                p = verify_jwt(tok, key("JWT_SECRET") or "dev-secret")
                if p:
                    tier = p.get("tier") or "free"
                    if p.get("admin"):
                        tier = "enterprise"
            else:
                tier = check_tier((body.get("email") or "").strip())
            last_user = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user = m.get("content") or ""
                    break
            if last_user:
                try:
                    tool_runs = auto_tools(last_user, tier)
                except Exception as e:
                    tool_runs = [{"tool": "error", "label": "auto-tools", "result": str(e)[:200]}]
        tool_ctx = tool_context(tool_runs)
        if tool_ctx:
            messages = [{"role": "system", "content": tool_ctx}] + messages
        tool_summary = [{"tool": t.get("tool"), "label": t.get("label")} for t in tool_runs]

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

        url = base_url + "/chat/completions"
        max_tokens = int(body.get("max_tokens") or KEYS.get("CHAT_MAX_TOKENS", 600))
        temperature = float(body.get("temperature") or 0.7)
        payload = {"model": model, "messages": messages, "temperature": temperature,
                   "max_tokens": max(16, min(max_tokens, 4096)), "stream": stream}

        if not stream:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            try:
                _, raw, _ = http_fetch(url, method="POST", headers=headers, json_body=payload, timeout=120)
                data = json.loads(raw)
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                self._send_json({"content": content, "tools": tool_summary, "cores": core_names})
            except urllib.error.HTTPError as e:
                self._send_json({"error": f"AI provider error {e.code}: "
                                          f"{e.read().decode('utf-8','replace')[:300]}"}, 502)
            except Exception as e:
                self._send_json({"error": str(e)}, 502)
            return

        # streaming — attempt primary, fallback to Groq on failure if 'auto'
        tried = []
        while True:
            tried.append(base_url)
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                       "Accept": "text/event-stream", "User-Agent": UA}
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers=headers, method="POST")
            ctx = ssl.create_default_context()
            try:
                resp = urllib.request.urlopen(req, timeout=180, context=ctx)
            except urllib.error.HTTPError as e:
                if provider == "auto" and key("GROQ_API_KEY") and "groq" not in base_url:
                    api_key = key("GROQ_API_KEY")
                    base_url = "https://api.groq.com/openai/v1"
                    model = KEYS.get("GROQ_MODEL", GROQ_DEFAULT_MODEL)
                    url = base_url + "/chat/completions"
                    payload["model"] = model
                    continue
                self._send_json({"error": f"AI provider error {e.code}: "
                                          f"{e.read().decode('utf-8','replace')[:300]}"}, 502)
                return
            except Exception as e:
                self._send_json({"error": str(e)}, 502)
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if tool_summary or core_names:
                try:
                    self.wfile.write(("data: " + json.dumps({"__tools": tool_summary, "__cores": core_names}) + "\n\n").encode())
                    self.wfile.flush()
                except Exception:
                    pass
            try:
                for raw_line in resp:
                    try:
                        self.wfile.write(raw_line)
                        self.wfile.flush()
                    except Exception:
                        break
            finally:
                resp.close()
            return


def main():
    _load_keys()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"OraCool AI server (v2) running on http://{HOST}:{PORT}")
    print("Keys loaded:", sum(1 for v in KEYS.values() if isinstance(v, str) and v.strip() and not v.startswith('_')))
    server.serve_forever()


if __name__ == "__main__":
    main()
