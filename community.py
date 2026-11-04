"""OraCool Community — member chat, OraCool numbers & usernames, groups,
channels, direct messages, member reports and the AI moderation review.

Rules (Patch 15):
  * Every signed-in member gets a public identity: a UNIQUE username (chosen
    at signup) and a unique 10-digit OraCool NUMBER. E-mails are never shown;
    IP addresses are never stored in the community schema and are never
    returned to other members.
  * Chat: the built-in rooms (Lounge / Markets / Help), member-created GROUPS
    and CHANNELS, and private DMs. Members add friends by typing the other
    member's OraCool number.
  * Any member can report another member with a written reason (optionally
    pointing at specific messages). One open report per reporter per account;
    admins and the moderator cannot be reported.
  * Only when THREE distinct credible members have open reports against the
    same account does the OraCool moderator (the admin AI) open a case. It
    reviews the dossier — the reported member's own room messages, their
    messages in DM threads with the reporters, their profile, the reports —
    and returns a verdict with a confidence.
  * ONLY the moderator (or a human administrator through the admin console)
    can suspend an account. Reporters never can. The moderator suspends only
    for evidenced illegal / seriously harmful conduct at confidence >= 0.75;
    otherwise it warns, dismisses (which lowers the reporters' credibility)
    or asks the human admins to look. Model failure never suspends anybody.
  * A suspension is the same durable Patch-13 suspension (BLOCKED screen and
    the $20 reinstatement fine). Admins can overturn it.

Storage: the Supabase tables created by supabase_patch15.sql (service-role
REST). Until that script has been run in Supabase, every public method raises
CommunitySetup and the app shows a short "setting up" note instead of erroring.
"""
import hashlib
import json
import re
import secrets
import threading
import time

ROOM_SEED = {"lounge": ("Lounge", "Meet other OraCool members. Say hello."),
             "markets": ("Markets", "Stocks, crypto and paper-trading talk. Educational only."),
             "help": ("Help desk", "Tips, questions and answers about using OraCool.")}
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.]{2,19}$")
RESERVED_USERNAMES = {"oracool", "oracoolai", "admin", "administrator", "moderator", "support",
                      "system", "staff", "root", "official", "oracool_moderator", "help", "lounge",
                      "markets"}
MODERATOR = "moderator@oracool.system"          # sentinel sender for moderator messages
MODERATOR_HANDLE = "OraCool Moderator"
REPORT_THRESHOLD = 3
MIN_REPORTER_AGE_SEC = 3600                      # brand-new accounts do not count toward the threshold
CASE_COOLDOWN_SEC = 24 * 3600
BLOCK_CONFIDENCE = 0.75
BLOCKABLE = {"fraud_or_scam", "threats_or_violence", "sexual_exploitation", "illegal_goods_or_services",
             "hacking_or_malware", "harassment_or_doxxing", "terrorism_or_extremism", "money_laundering"}
VERDICTS = {"block", "warn", "dismiss", "insufficient_evidence"}
MSG_MAX, REASON_MIN, REASON_MAX = 800, 10, 500
RATE_SECONDS, DAILY_MAX = 1.5, 300
ONLINE_WINDOW = 300
READY_TTL = 30                                    # seconds between table-readiness probes
NUMBER_MIN, NUMBER_MAX = 2000000000, 2999999999

MODERATOR_SYSTEM_PROMPT = (
    "You are the OraCool Trust & Safety reviewer (the platform's moderation AI). You decide whether a member of a "
    "chat community should be SUSPENDED for illegal or seriously harmful conduct, based ONLY on the evidence in the "
    "dossier. Reports are accusations, not proof: rely on messages actually written by the reported member. "
    "Suspend ('block') only when the reported member's own messages show they are committing, offering or soliciting "
    "illegal activity or serious harm: fraud or scams, threats or violence, sexual exploitation or child abuse, "
    "selling drugs, weapons or stolen data, hacking, malware or account theft, money laundering, terrorism, or "
    "targeted harassment / doxxing. Rudeness, disagreement, spam, jokes or unpopular opinions are NOT grounds to "
    "suspend — use 'warn' or 'dismiss'. If the dossier contains no messages written by the reported member, answer "
    "'insufficient_evidence'. If the reporters appear to be coordinating a false report, answer 'dismiss'. "
    "Reply with ONLY a JSON object of this exact shape: {\"verdict\": \"block\"|\"warn\"|\"dismiss\"|\"insufficient_evidence\", "
    "\"confidence\": <number 0-1>, \"category\": one of [\"fraud_or_scam\",\"threats_or_violence\",\"sexual_exploitation\","
    "\"illegal_goods_or_services\",\"hacking_or_malware\",\"harassment_or_doxxing\",\"terrorism_or_extremism\","
    "\"money_laundering\",\"none\"], \"summary\": \"<= 60 words, plain language, no personal data\", "
    "\"evidence\": [\"short quote from the member's own messages\", ...]}")


class CommunitySetup(Exception):
    """The Supabase community tables do not exist yet (SQL not run)."""


class UsernameTaken(Exception):
    def __init__(self, username):
        self.username = username
        super().__init__("That username is already taken.")


class _StoreError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, str(message or "")
        super().__init__("%s %s" % (status, self.message))


def _unique(err):
    return isinstance(err, _StoreError) and ("23505" in err.message or "duplicate key" in err.message.lower())


def _ts(s):
    try:
        return time.mktime(time.strptime(str(s)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def _isots(t=None):
    """Proper UTC ISO timestamp for timestamptz columns (never '0.0')."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t if t is not None else time.time()))


def _hid(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def _clean(text, limit):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or ""))
    return text.strip()[:limit]


def _slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s[:30] or "room"


class Store:
    """Thin service-role REST client over the Patch-15 community tables."""

    def __init__(self, rest):
        """rest(method, path, body=None, prefer=None) -> (status:int, data).
        path is relative to /rest/v1 (table + query string)."""
        self.rest = rest
        self._ready = (0.0, False)

    # ------------------------------------------------------------ readiness
    def ready(self):
        exp, ok = self._ready
        if time.time() < exp:
            return ok
        ok = False
        try:
            st, _ = self.rest("GET", "comm_messages?select=id&limit=1")
            ok = st == 200
        except Exception:
            ok = False
        self._ready = (time.time() + READY_TTL, ok)
        return ok

    # ------------------------------------------------------------ primitives
    def _call(self, method, path, body=None, prefer=None):
        st, data = self.rest(method, path, body, prefer)
        if st not in (200, 201, 204):
            msg = data.get("message") or data.get("error_description") or str(data)[:200] \
                if isinstance(data, dict) else str(data)[:200]
            raise _StoreError(st, msg)
        return data

    def get(self, table, query=""):
        data = self._call("GET", table + ("?" + query if query else ""))
        return data if isinstance(data, list) else []

    def one(self, table, query=""):
        rows = self.get(table, query)
        return rows[0] if rows else None

    def insert(self, table, row, returning=True, on_conflict=None):
        q = ("?on_conflict=" + on_conflict) if on_conflict else ""
        prefer = "return=representation, resolution=merge-duplicates" if on_conflict else "return=representation"
        try:
            data = self._call("POST", table + q, body=[row] if isinstance(row, dict) else row, prefer=prefer)
        except _StoreError as e:
            if on_conflict and _unique(e):     # upsert no-op conflict: row already there
                return None
            raise
        if isinstance(data, list):
            return data[0] if data else None
        return data if isinstance(data, dict) else None

    def patch(self, table, query, fields):
        data = self._call("PATCH", table + "?" + query, body=fields, prefer="return=representation")
        return data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])

    def delete(self, table, query):
        self._call("DELETE", table + "?" + query)


class Service:
    def __init__(self, rest, deps):
        """deps: dict of callables — is_admin(email), block_user(email, blocked, reason, by) -> dict,
        is_blocked(email), load_users() -> {email: rec}, check_tier(email), llm_complete(messages, temperature,
        max_tokens) -> (text, err), notify_admins(etype, title, body), emit_event(email, etype, title, body)."""
        self.store = Store(rest)
        self.d = deps
        self.lock = threading.RLock()
        self.presence = {}
        self.last_post = {}
        self.daily = {}
        self.async_reviews = True

    # ------------------------------------------------------------ guards
    def _need(self):
        if not self.store.ready():
            raise CommunitySetup("The community database is not ready yet.")

    def setup_status(self):
        return {"ready": bool(self.store.ready())}

    # ------------------------------------------------------------ profiles
    def _prof(self, email):
        return self.store.one("comm_profiles", "email=eq." + email)

    def _profiles_for(self, emails):
        uniq = [e for e in set(emails) if e and e != MODERATOR]
        out = {}
        for i in range(0, len(uniq), 40):
            chunk = uniq[i:i + 40]
            q = "email=in.(" + ",".join(chunk) + ")"
            try:
                for r in self.store.get("comm_profiles", q):
                    out[r.get("email")] = r
            except _StoreError:
                pass
        return out

    def _auto_username(self, email):
        base = "ora_" + hashlib.sha1(email.encode()).hexdigest()[:4]
        for n in range(0, 100):
            cand = base if n == 0 else "%s%d" % (base, n)
            try:
                if not self.store.one("comm_profiles", "username=eq." + cand):
                    return cand
            except _StoreError:
                return cand
        return base + hashlib.sha1((email + str(time.time())).encode()).hexdigest()[:4]

    def username_taken(self, username):
        """True / False / None (community tables not ready)."""
        username = (username or "").strip().lstrip("@").lower()
        if not username:
            return None
        try:
            return bool(self.store.one("comm_profiles", "username=eq." + username))
        except Exception:
            return None

    def ensure_profile(self, email, username=None, display_name=None):
        """Create (or refresh) the member's public identity. Never raises for
        setup state — returns None until the tables exist."""
        email = (email or "").strip().lower()
        if not email:
            return None
        try:
            if not self.store.ready():
                return None
            row = self._prof(email)
            if row:
                if display_name and row.get("display_name") != display_name:
                    self.store.patch("comm_profiles", "email=eq." + email, {"display_name": display_name[:60]})
                return row
            un = None
            if username:
                un = (username or "").strip().lstrip("@").lower()
                if not USERNAME_RE.match(un) or un in RESERVED_USERNAMES:
                    un = None          # fall back to auto, never break signup
            un = un or self._auto_username(email)
            row = self.store.insert("comm_profiles",
                                    {"email": email, "username": un,
                                     "display_name": (display_name or email.split("@")[0])[:60]})
            if not row:                # lost an upsert race somewhere
                row = self._prof(email)
            return row
        except UsernameTaken:
            raise
        except _StoreError as e:
            if _unique(e) and username:
                raise UsernameTaken(username)
            return None
        except Exception:
            return None

    def profile_by_number(self, number):
        try:
            n = int(number)
        except Exception:
            return None
        if n < NUMBER_MIN or n > NUMBER_MAX:
            return None
        return self.store.one("comm_profiles", "oracool_number=eq." + str(n))

    def profile_by_username(self, username):
        username = (username or "").strip().lstrip("@").lower()
        if not username:
            return None
        return self.store.one("comm_profiles", "username=eq." + username)

    def handle_of(self, email):
        if email == MODERATOR:
            return MODERATOR_HANDLE
        row = self._prof(email) if email else None
        return row.get("username") if row else None

    def number_of(self, email):
        row = self._prof(email) if email else None
        return row.get("oracool_number") if row else None

    def email_of(self, username):
        row = self.profile_by_username(username)
        return row.get("email") if row else None

    def update_profile(self, email, username=None, bio=None):
        self._need()
        email = email.lower()
        row = self._prof(email)
        if not row:
            row = self.ensure_profile(email)
        fields = {}
        if username is not None:
            un = (username or "").strip().lstrip("@").lower()
            if not USERNAME_RE.match(un):
                return {"error": "Usernames are 3-20 characters: start with a letter or number, then letters, numbers, dots and underscores."}
            if un in RESERVED_USERNAMES:
                return {"error": "That username is reserved."}
            other = self.profile_by_username(un)
            if other and other.get("email") != email:
                return {"error": "That username is already taken."}
            fields["username"] = un
        if bio is not None:
            fields["bio"] = _clean(bio, 140)
        if fields:
            row = self.store.patch("comm_profiles", "email=eq." + email, fields) or [row]
            row = row[0] if isinstance(row, list) else row
        return {"ok": True, "profile": self._public_profile(email, row)}

    # ------------------------------------------------ power: AMA + showcase (patch60)
    def can_host(self, email):
        """Power users: paid plans, administrators and Advocates (early access)."""
        if not email:
            return False
        try:
            if self.d["is_admin"](email) or self.d["check_tier"](email) in ("pro", "ultra", "enterprise"):
                return True
            rec = (self.d["load_users"]() or {}).get(email) or {}
            return bool(rec.get("early_access"))
        except Exception:
            return False

    def ama_create(self, email, title, description=""):
        self._need()
        email = (email or "").lower()
        if not self.can_host(email):
            return {"error": "Hosting an AMA is for power users — be on the Pro plan or above, or an Advocate (3+ referrals)."}
        title = _clean(title, 120)
        if len(title) < 4:
            return {"error": "Give the AMA a real title (4+ characters)."}
        for r in self.store.get("comm_amas", "host_email=eq." + email + "&status=eq.live"):
            self.store.patch("comm_amas", "id=eq." + str(r.get("id")), {"status": "ended", "ended_at": _isots()})
        row = self.store.insert("comm_amas", {"host_email": email, "title": title,
                                              "description": _clean(description, 400), "status": "live",
                                              "created_at": _isots(), "started_at": _isots()})
        self.d["notify_admins"]("community", "AMA started", email + " — " + title)
        return {"ok": True, "ama": row}

    def amas(self, email=""):
        self._need()
        rows = self.store.get("comm_amas", "select=*&order=created_at.desc&limit=30")
        hosts = self._profiles_for([r.get("host_email") for r in rows])
        qs = self.store.get("comm_ama_questions", "select=*&order=votes.desc,created_at.desc&limit=200")
        by_ama = {}
        for q in qs:
            by_ama.setdefault(str(q.get("ama_id")), []).append(q)
        out = []
        for r in rows:
            hh = hosts.get(r.get("host_email")) or {}
            out.append({"id": r.get("id"), "title": r.get("title"), "description": r.get("description"),
                        "status": r.get("status"), "created_at": r.get("created_at"),
                        "host": {"username": hh.get("username"), "number": hh.get("oracool_number"),
                                 "email": r.get("host_email")},
                        "mine": (r.get("host_email") or "") == (email or "").lower(),
                        "questions": [self._ama_q_view(q) for q in (by_ama.get(str(r.get("id"))) or [])[:50]]})
        return {"ok": True, "amas": out, "can_host": self.can_host(email)}

    def _ama_q_view(self, q):
        return {"id": q.get("id"), "body": q.get("body"), "votes": int(q.get("votes") or 0),
                "answered": bool(q.get("answered")), "answer": q.get("answer_text") or "",
                "asker": q.get("asker_email"), "created_at": q.get("created_at")}

    def ama_ask(self, email, ama_id, body_text):
        self._need()
        email = (email or "").lower()
        text = _clean(body_text, 300)
        if len(text) < 3:
            return {"error": "Write your question first."}
        ama = self.store.one("comm_amas", "id=eq." + str(ama_id))
        if not ama or ama.get("status") != "live":
            return {"error": "That AMA is not live."}
        row = self.store.insert("comm_ama_questions", {"ama_id": ama_id, "asker_email": email,
                                                       "body": text, "votes": 0, "answered": False,
                                                       "created_at": _isots()})
        return {"ok": True, "question": self._ama_q_view(row or {})}

    def ama_vote(self, email, q_id):
        self._need()
        q = self.store.one("comm_ama_questions", "id=eq." + str(q_id))
        if not q:
            return {"error": "Question not found."}
        votes = int(q.get("votes") or 0) + 1
        self.store.patch("comm_ama_questions", "id=eq." + str(q_id), {"votes": votes})
        return {"ok": True, "votes": votes}

    def ama_answer(self, email, q_id, text):
        self._need()
        email = (email or "").lower()
        q = self.store.one("comm_ama_questions", "id=eq." + str(q_id))
        if not q:
            return {"error": "Question not found."}
        ama = self.store.one("comm_amas", "id=eq." + str(q.get("ama_id")))
        if not ama or (ama.get("host_email") or "").lower() != email:
            return {"error": "Only the AMA host can answer questions."}
        self.store.patch("comm_ama_questions", "id=eq." + str(q_id),
                         {"answered": True, "answer_text": _clean(text, 1200),
                          "answered_at": _isots()})
        return {"ok": True}

    def ama_end(self, email, ama_id):
        self._need()
        email = (email or "").lower()
        ama = self.store.one("comm_amas", "id=eq." + str(ama_id))
        if not ama or (ama.get("host_email") or "").lower() != email:
            return {"error": "Only the host can end this AMA."}
        self.store.patch("comm_amas", "id=eq." + str(ama_id), {"status": "ended", "ended_at": _isots()})
        return {"ok": True}

    def showcase_share(self, email, title, url, description=""):
        self._need()
        email = (email or "").lower()
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if "." not in url:
            return {"error": "Paste the link to your published build."}
        row = self.store.insert("comm_showcase", {"email": email, "title": _clean(title or "My build", 90),
                                                  "url": url[:300], "description": _clean(description, 240),
                                                  "likes": 0, "created_at": _isots()})
        return {"ok": True, "item": row}

    def showcase(self, email=""):
        self._need()
        rows = self.store.get("comm_showcase", "select=*&order=likes.desc,created_at.desc&limit=40")
        owners = self._profiles_for([r.get("email") for r in rows])
        liked = set()
        if email:
            try:
                for l in self.store.get("comm_showcase_likes", "voter_email=eq." + email.lower()):
                    liked.add(str(l.get("item_id")))
            except Exception:
                pass
        return {"ok": True, "items": [{"id": r.get("id"), "title": r.get("title"), "url": r.get("url"),
                                       "description": r.get("description"), "likes": int(r.get("likes") or 0),
                                       "liked": str(r.get("id")) in liked,
                                       "owner": {"username": (owners.get(r.get("email")) or {}).get("username"),
                                                 "number": (owners.get(r.get("email")) or {}).get("oracool_number")}}
                                      for r in rows]}

    def showcase_like(self, email, item_id):
        self._need()
        email = (email or "").lower()
        row = self.store.one("comm_showcase", "id=eq." + str(item_id))
        if not row:
            return {"error": "Showcase item not found."}
        dup = self.store.one("comm_showcase_likes", "item_id=eq." + str(item_id) + "&voter_email=eq." + email)
        if dup:
            return {"ok": True, "likes": int(row.get("likes") or 0), "already": True}
        try:
            self.store.insert("comm_showcase_likes", {"item_id": item_id, "voter_email": email,
                                                      "created_at": _isots()})
        except Exception:
            return {"ok": True, "likes": int(row.get("likes") or 0), "already": True}
        likes = int(row.get("likes") or 0) + 1
        self.store.patch("comm_showcase", "id=eq." + str(item_id), {"likes": likes})
        return {"ok": True, "likes": likes}

    # ------------------------------------------------ bots (patch64, BotFather-style)
    def _bot_uok(self, username):
        u = (username or "").strip().lstrip("@").lower()
        return bool(re.match(r"^[a-z0-9_]{5,32}$", u) and u.endswith("bot") and u != "botfather"), u

    def _bots_store_ok(self):
        try:
            self.store.get("comm_bots", "select=id&limit=1")
            return True
        except Exception:
            return False

    def bot_create(self, email, name, username, brain="", greeting=""):
        """Telegram BotFather flow: /newbot → name → username (must end in
        'bot') → an HTTP API token shown ONCE, never stored in clear."""
        self._need()
        email = (email or "").lower()
        if not self._bots_store_ok():
            return {"error": "The bots database is not ready yet — run supabase_patch64_bots.sql, then retry."}
        name = _clean(name, 40)
        if len(name) < 2:
            return {"error": "Give your bot a name people will see (2+ characters) — like BotFather asks."
                    }
        ok, un = self._bot_uok(username)
        if not ok:
            return {"error": "Usernames must be 5-32 characters and END with 'bot' (Telegram's rule) — e.g. studyhelper_bot."}
        mine = self.store.get("comm_bots", "owner_email=eq." + email)
        if len(mine) >= 5:
            return {"error": "You already have 5 bots — that is the cap per member. Delete one to make room."}
        try:
            if self.store.one("comm_bots", "username=eq." + un):
                return {"error": "That bot username is taken. Try another one."}
        except Exception:
            pass
        tok_id = str(900_000_000 + (int(hashlib.sha1(un.encode()).hexdigest(), 16) % 99_000_000))
        token = tok_id + ":" + "AA" + secrets.token_hex(17)
        row = self.store.insert("comm_bots",
                                {"owner_email": email, "name": name, "username": un,
                                 "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                                 "token_prefix": token[:12], "token_last4": token[-4:],
                                 "brain": _clean(brain, 1200), "greeting": _clean(greeting, 300),
                                 "enabled": True, "replies": 0,
                                 "created_at": _isots()})
        if not row:
            return {"error": "Could not save the bot — try again."}
        try:
            self.d["notify_admins"]("community", "New bot created", email + " → @" + un)
        except Exception:
            pass
        return {"ok": True, "bot": {"id": row.get("id"), "name": name, "username": un},
                "token": token,
                "note": "Copy this token NOW — it is shown only once, exactly like BotFather. "
                        "Your bot answers when anyone @mentions @" + un + " in any room, and developers can call the Bot API with it."}

    def bots(self, email=""):
        self._need()
        email = (email or "").lower()
        if not self._bots_store_ok():
            return {"ok": True, "mine": [], "all": [], "api_base": "https://oracoolai.com/api/bot/v1"}
        try:
            rows = self.store.get("comm_bots", "select=*&order=created_at.desc&limit=200")
        except Exception:
            rows = []
        mine = [{"id": r.get("id"), "name": r.get("name"), "username": r.get("username"),
                 "brain": r.get("brain") or "", "greeting": r.get("greeting") or "",
                 "enabled": r.get("enabled") not in (False,), "replies": int(r.get("replies") or 0),
                 "prefix": (r.get("token_prefix") or "") + "…" + (r.get("token_last4") or ""),
                 "created_at": r.get("created_at")} for r in rows if (r.get("owner_email") or "").lower() == email]
        everyone = [{"id": r.get("id"), "name": r.get("name"), "username": r.get("username"),
                     "greeting": r.get("greeting") or "", "enabled": r.get("enabled") not in (False,),
                     "mine": (r.get("owner_email") or "").lower() == email}
                    for r in rows if r.get("enabled") not in (False,)]
        return {"ok": True, "mine": mine, "all": everyone,
                "api_base": "https://oracoolai.com/api/bot/v1"}

    def _bot_row(self, email, bot_id):
        for r in self.store.get("comm_bots", "select=*&order=created_at.desc&limit=200"):
            if str(r.get("id")) == str(bot_id) and (r.get("owner_email") or "").lower() == (email or "").lower():
                return r
        return None

    def bot_update(self, email, bot_id, brain=None, greeting=None, name=None, enabled=None):
        self._need()
        r = self._bot_row(email, bot_id)
        if not r:
            return {"error": "That is not one of your bots."}
        f = {}
        if brain is not None:
            f["brain"] = _clean(brain, 1200)
        if greeting is not None:
            f["greeting"] = _clean(greeting, 300)
        if name is not None:
            nm = _clean(name, 40)
            if len(nm) >= 2:
                f["name"] = nm
        if enabled is not None:
            f["enabled"] = bool(enabled)
        if f:
            self.store.patch("comm_bots", "id=eq." + str(bot_id), f)
        return {"ok": True}

    def bot_program(self, email, token, bot_id, description):
        """The Bot Builder assistant: developer pastes their bot's API token
        (or owns the bot) and describes what the bot should do — the assistant
        writes the brain (personality + rules) and greeting with the LLM."""
        self._need()
        email = (email or "").lower()
        row = None
        if token:
            row = self.bot_by_token("Bearer " + str(token).strip())
            if not row:
                return {"error": "That bot API token does not match any bot. Copy it from BotFather → /mybots → 🔑 New token."}
        elif bot_id:
            for r in self.store.get("comm_bots", "select=*&order=created_at.desc&limit=200"):
                if str(r.get("id")) == str(bot_id) and (r.get("owner_email") or "").lower() == email:
                    row = r
                    break
        if not row:
            return {"error": "Send me your bot's API token first (BotFather → /mybots → copy the token), then tell me what it should do."}
        desc = _clean(description, 2000)
        if len(desc) < 8:
            return {"error": "Tell me more — what is the bot FOR? e.g. 'a patient WAEC study tutor that quizzes me daily'."}
        _q = ("You configure chat bots for OraCool's community. The developer describes a bot. "
              "Write its configuration as two short lines in this exact format:\n"
              "BRAIN: <2-4 sentences defining the bot's name-use, personality, purpose and hard rules>\n"
              "GREETING: <one warm sentence the bot opens with>\n"
              "Developer's description: " + desc)
        text, err = self.d["llm_complete"]([{"role": "system", "content": _q}], t=0.6, mx=350)
        brain, greeting = "", ""
        if text:
            for line in str(text).splitlines():
                ls = line.strip()
                if ls.upper().startswith("BRAIN:"):
                    brain = ls[6:].strip()
                elif ls.upper().startswith("GREETING:"):
                    greeting = ls[9:].strip()
        if not brain:
            brain = desc[:1000]           # fall back to the raw description
        if not greeting:
            greeting = "Hi! Mention me and I will do my job."
        self.store.patch("comm_bots", "id=eq." + str(row.get("id")),
                         {"brain": _clean(brain, 1200), "greeting": _clean(greeting, 300)})
        try:
            self.d["notify_admins"]("community", "Bot programmed", (row.get("owner_email") or "") + " → @" + str(row.get("username")))
        except Exception:
            pass
        return {"ok": True, "bot": {"id": row.get("id"), "name": row.get("name"), "username": row.get("username")},
                "brain": _clean(brain, 1200), "greeting": _clean(greeting, 300),
                "note": "@" + str(row.get("username")) + " reprogrammed. Mention it in any room to test."}

    def bot_reset_token(self, email, bot_id):
        self._need()
        r = self._bot_row(email, bot_id)
        if not r:
            return {"error": "That is not one of your bots."}
        token = str(900_000_000 + (int(hashlib.sha1(str(r.get("username")).encode()).hexdigest(), 16) % 99_000_000)) \
                + ":" + "AA" + secrets.token_hex(17)
        self.store.patch("comm_bots", "id=eq." + str(bot_id),
                         {"token_hash": hashlib.sha256(token.encode()).hexdigest(),
                          "token_prefix": token[:12], "token_last4": token[-4:]})
        return {"ok": True, "token": token,
                "note": "Old token revoked. Shown once — copy it now."}

    def bot_delete(self, email, bot_id):
        self._need()
        r = self._bot_row(email, bot_id)
        if not r:
            return {"error": "That is not one of your bots."}
        self.store.delete("comm_bots", "id=eq." + str(bot_id))
        return {"ok": True}

    def bot_by_token(self, header):
        m = re.match(r"^\s*Bearer\s+(\S+)\s*$", header or "", re.I)
        if not m:
            return None
        h = hashlib.sha256(m.group(1).encode()).hexdigest()
        try:
            return self.store.one("comm_bots", "token_hash=eq." + h)
        except Exception:
            return None

    def _bots_on_message(self, room, text):
        """Any enabled bot @mentioned in a room replies with its own brain
        (owner-written instructions + OraCool's LLM). Runs in a thread so the
        sender's request never waits on it."""
        try:
            unames = set(re.findall(r"@([a-z0-9_]{5,32}bot)\b", (text or "").lower()))
            if not unames:
                return
            try:
                rows = self.store.get("comm_bots", "select=*&enabled=eq.true&limit=200")
            except Exception:
                rows = []
            target = next((b for b in rows if str(b.get("username")) in unames), None)
            if not target:
                return
            question = (text or "").strip()[:800]
            brain = _clean(target.get("brain") or "", 1200)
            name = target.get("name") or "bot"
            try:
                if brain:
                    sysline = ("You are @" + str(target.get("username")) + " ("" + name + ""), a community bot on OraCool. "
                               "PERSONALITY AND PURPOSE set by your creator: " + brain +
                               " Reply in-character, helpful, 1-4 sentences, no markdown headings.")
                else:
                    sysline = ("You are @" + str(target.get("username")) + " (“" + name
                               + "”), a friendly community bot on OraCool. Reply helpfully in 1-3 sentences.")
                reply, err = self.d["llm_complete"](
                    [{"role": "system", "content": sysline},
                     {"role": "user", "content": question}], t=0.7, mx=300)
            except Exception:
                reply, err = None, "llm down"
            if not reply:
                reply = _clean(target.get("greeting") or "", 300) or ("Hi! I'm " + name + ". My creator hasn't taught me to talk yet — mention me again soon.")
            self.store.insert("comm_messages",
                              {"room_id": room.get("id"), "sender_email": "bot:" + str(target.get("username")),
                               "body": _clean(reply, MSG_MAX), "kind": "chat", "media_url": ""})
            self.store.patch("comm_bots", "id=eq." + str(target.get("id")),
                             {"replies": int(target.get("replies") or 0) + 1})
        except Exception:
            pass

    def bot_send(self, bot, room_slug, text):
        """The Bot API: a developer's app posts into a room AS their bot."""
        self._need()
        r = self._room(str(room_slug or ""))
        if not r:
            return {"error": "Unknown room slug — sendMessage needs the room's slug (e.g. 'lounge')."}
        if r.get("banned"):
            return {"error": "That room is banned."}
        body = _clean(text, MSG_MAX)
        if not body:
            return {"error": "text is required."}
        m = self.store.insert("comm_messages",
                              {"room_id": r["id"], "sender_email": "bot:" + str(bot.get("username")),
                               "body": body, "kind": "chat", "media_url": ""})
        if not m:
            return {"error": "Message could not be saved."}
        self.store.patch("comm_bots", "id=eq." + str(bot.get("id")),
                         {"replies": int(bot.get("replies") or 0) + 1})
        pros = {}
        return {"ok": True, "message": self._view(m, "bot:" + str(bot.get("username")), pros)}

    def _online(self, email):
        return (time.time() - self.presence.get(email, 0)) < ONLINE_WINDOW

    def _public_profile(self, email, row=None):
        row = row or self._prof(email) or {}
        return {"username": row.get("username"), "number": row.get("oracool_number"),
                "display_name": row.get("display_name", ""), "bio": row.get("bio", ""),
                "avatar": row.get("avatar_url", ""),
                "verified": bool(self.d.get("is_verified") and self.d["is_verified"](email)),
                "verified_until": row.get("verified_until"),
                "admin": bool(self.d["is_admin"](email)), "online": self._online(email),
                "joined": str(row.get("created_at", ""))[:10]}

    def me(self, email):
        self._need()
        email = email.lower()
        self.presence[email] = time.time()
        row = self.ensure_profile(email)
        unread = 0
        try:
            unread = self._unread_total(email)
        except Exception:
            unread = 0
        return {"profile": self._public_profile(email, row), "rooms": self.rooms(email),
                "verified": bool(self.d.get("is_verified") and self.d["is_verified"](email)),
                "unread_dm": unread, "threshold": REPORT_THRESHOLD,
                "guidelines": ("Be respectful. Illegal activity — scams, threats, exploitation, hacking, selling "
                               "illegal goods — gets an account suspended after the OraCool moderator reviews reports "
                               "from %d members. Never share passwords or payment details." % REPORT_THRESHOLD)}

    # ------------------------------------------------------------ lookup & friends
    def lookup(self, viewer, number):
        self._need()
        row = self.profile_by_number(number)
        if not row:
            return {"error": "No member with that OraCool number."}
        return {"profile": self._public_profile(row.get("email"), row),
                "is_friend": self._is_friend(viewer, row.get("email"))}

    def _is_friend(self, owner, contact):
        if not owner or not contact or owner == MODERATOR:
            return False
        return bool(self.store.one("comm_contacts",
                                   "owner_email=eq." + owner + "&contact_email=eq." + contact))

    def friends(self, email):
        self._need()
        email = email.lower()
        self.presence[email] = time.time()
        rows = []
        try:
            rows = self.store.get("comm_contacts", "owner_email=eq." + email + "&order=added_at.desc&limit=200")
        except _StoreError:
            rows = []
        pros = self._profiles_for([r.get("contact_email") for r in rows])
        out = []
        for r in rows:
            e = r.get("contact_email")
            if e and e in pros:
                out.append(self._public_profile(e, pros[e]))
        out.sort(key=lambda x: (not x["online"], not x["admin"], str(x.get("username") or "")))
        return {"friends": out}

    def add_friend(self, email, number):
        self._need()
        email = email.lower()
        self.ensure_profile(email)
        row = self.profile_by_number(number)
        if not row:
            return {"error": "No member with that OraCool number."}
        target = row.get("email")
        if target == email:
            return {"error": "That is your own number — you already know yourself."}
        try:
            self.store.insert("comm_contacts", {"owner_email": email, "contact_email": target},
                              returning=False, on_conflict="owner_email,contact_email")
        except _StoreError:
            pass
        return {"ok": True, "profile": self._public_profile(target, row)}

    # ------------------------------------------------------------ rooms / groups / channels
    def _room(self, slug):
        return self.store.one("comm_rooms", "slug=eq." + slug)

    def rooms(self, viewer=None):
        # patch31: the community is private-by-default. There is no public room
        # browsing any more — you only ever see groups/channels you own or that
        # someone added you to. Chats start by sharing OraCool numbers.
        self._need()
        viewer = (viewer or "").lower()
        out = []
        if viewer and viewer != MODERATOR:
            ids = []
            try:
                mem = self.store.get("comm_members", "email=eq." + viewer + "&limit=300") or []
                ids = [m.get("room_id") for m in mem if m.get("room_id")]
            except _StoreError:
                ids = []
            rows = []
            for i in range(0, len(ids), 40):
                chunk = ids[i:i + 40]
                try:
                    rows += self.store.get("comm_rooms",
                                           "id=in.(" + ",".join(chunk) + ")&kind=in.(room,group,channel)&limit=300") or []
                except _StoreError:
                    pass
            seen = set()
            for r in rows:
                if not r or r.get("id") in seen:
                    continue
                seen.add(r.get("id"))
                owner = self.handle_of(r.get("owner_email") or "") if r.get("owner_email") else None
                out.append({"id": r.get("slug"), "name": r.get("name"), "about": r.get("description", ""),
                            "kind": r.get("kind"), "owner": owner,
                            "banned": bool(r.get("banned")),
                            "owner_only": bool(r.get("owner_only_post")),
                            "yours": bool(r.get("owner_email") == viewer)})
        out.sort(key=lambda x: (x["kind"] != "group", x["kind"] != "channel", str(x["name"]).lower()))
        return out

    def create_room(self, email, name, kind, description="", is_public=False):
        # patch31: every group/channel is private to its creator; members are
        # added one by one by OraCool number. (is_public is accepted but ignored.)
        self._need()
        email = email.lower()
        if kind not in ("group", "channel"):
            return {"error": "Kind must be 'group' or 'channel'."}
        name = _clean(name, 40)
        if len(name) < 2:
            return {"error": "Give it a name (2-40 characters)."}
        self.ensure_profile(email)
        base = _slugify(name)
        slug = None
        for n in range(0, 12):
            cand = base if n == 0 else "%s-%d" % (base[:26], n + 1)
            if not self._room(cand):
                slug = cand
                break
        if not slug:
            return {"error": "Could not create that name. Try a different one."}
        try:
            row = self.store.insert("comm_rooms",
                                    {"slug": slug, "name": name, "kind": kind,
                                     "description": _clean(description, 160),
                                     "is_public": False, "owner_email": email},
                                    returning=True)
        except _StoreError:
            row = self._room(slug)
        if not row:
            return {"error": "Could not create the room. Try again."}
        try:
            self.store.insert("comm_members", {"room_id": row["id"], "email": email, "role": "owner"},
                              returning=False, on_conflict="room_id,email")
        except _StoreError:
            pass
        return {"ok": True, "room": {"id": slug, "name": name, "kind": kind,
                                     "about": _clean(description, 160), "owner": self.handle_of(email), "yours": True}}

    def join_room(self, email, slug):
        # patch31: self-join is gone. The creator invites by number — this
        # keeps groups exactly as private as a phone contact list.
        self._need()
        email = email.lower()
        r = self._room(slug)
        if not r:
            return {"error": "Unknown room."}
        if r.get("kind") == "dm":
            return {"error": "Use the message box to chat with that member."}
        if r.get("owner_email") == email or self.d["is_admin"](email) or \
                self.store.one("comm_members", "room_id=eq." + r["id"] + "&email=eq." + email):
            return {"ok": True, "already": True,
                    "room": {"id": slug, "name": r.get("name"), "kind": r.get("kind"),
                             "about": r.get("description", "")}}
        return {"error": "OraCool groups and channels are private — only the creator can add members. "
                "Share your 10-digit OraCool number with them and ask to be added."}

    # ------------------------------------------------------------ membership (patch31)
    def _room_member_ok(self, r, email):
        if r.get("owner_email") == email or self.d["is_admin"](email):
            return True
        try:
            return bool(self.store.one("comm_members", "room_id=eq." + r["id"] + "&email=eq." + email))
        except _StoreError:
            return False

    def _resolve_number_or_handle(self, who):
        who = str(who or "").strip().lstrip("@")
        if re.fullmatch(r"\d{10}", who):
            try:
                p = self.store.one("comm_profiles", "oracool_number=eq." + who + "&select=email,username")
            except _StoreError:
                p = None
            if p and p.get("email"):
                return str(p["email"]).lower()
        return self.email_of(who) or None

    def room_members(self, email, slug):
        self._need()
        email = email.lower()
        r = self._room(slug)
        if not r:
            return {"error": "Unknown room."}
        if not self._room_member_ok(r, email):
            return {"error": "Only members can see who is in this group."}
        try:
            rows = self.store.get("comm_members", "room_id=eq." + r["id"] + "&limit=200") or []
        except _StoreError:
            rows = []
        profs = self._profiles_for([m.get("email") for m in rows if m.get("email")])
        owner = (r.get("owner_email") or "").lower()
        out = []
        for m in rows:
            e = (m.get("email") or "").lower()
            p = profs.get(e) or {}
            item = {"handle": p.get("username") or self.handle_of(e) or e.split("@")[0],
                    "role": "owner" if e == owner else (m.get("role") or "member"),
                    "online": bool(self.presence.get(e) and time.time() - self.presence[e] < 120),
                    "me": e == email}
            if e == email or email == owner or self.d["is_admin"](email):
                item["number"] = p.get("oracool_number") or None
            out.append(item)
        out.sort(key=lambda x: (x["role"] != "owner", str(x["handle"]).lower()))
        return {"ok": True, "members": out, "can_manage": bool(email == owner or self.d["is_admin"](email)),
                "kind": r.get("kind"), "name": r.get("name")}

    def room_add_member(self, email, slug, who):
        self._need()
        email = email.lower()
        r = self._room(slug)
        if not r:
            return {"error": "Unknown room."}
        if r.get("owner_email") != email and not self.d["is_admin"](email):
            return {"error": "Only the creator can add members to their group."}
        peer = self._resolve_number_or_handle(who)
        if not peer:
            return {"error": "No member found for that number/@handle. They must have an OraCool account and have set up their community profile (Community → profile)."}
        if peer == r.get("owner_email"):
            return {"ok": True, "added": "owner already here"}
        try:
            self.store.insert("comm_members", {"room_id": r["id"], "email": peer, "role": "member"},
                              returning=False, on_conflict="room_id,email")
        except _StoreError:
            pass
        return {"ok": True, "added": self.handle_of(peer)}

    def room_remove_member(self, email, slug, who):
        self._need()
        email = email.lower()
        r = self._room(slug)
        if not r:
            return {"error": "Unknown room."}
        if r.get("owner_email") != email and not self.d["is_admin"](email):
            return {"error": "Only the creator can remove members."}
        peer = self._resolve_number_or_handle(who)
        if not peer:
            return {"error": "No member found for that number/@handle."}
        if peer == r.get("owner_email"):
            return {"error": "The owner cannot be removed — delete the group instead."}
        try:
            self.store.delete("comm_members", "room_id=eq." + r["id"] + "&email=eq." + peer)
        except _StoreError:
            return {"error": "Could not remove that member."}
        return {"ok": True, "removed": self.handle_of(peer)}

    def room_leave(self, email, slug):
        self._need()
        email = email.lower()
        r = self._room(slug)
        if not r:
            return {"error": "Unknown room."}
        if r.get("owner_email") == email:
            return {"error": "Owners can't leave their own group — delete it instead (🗑 in the Members panel)."}
        try:
            self.store.delete("comm_members", "room_id=eq." + r["id"] + "&email=eq." + email)
        except _StoreError:
            return {"error": "Could not leave."}
        return {"ok": True}

    def _ensure_dm_room(self, a, b):
        pair = "_".join(sorted([a, b]))
        slug = "dm_" + _hid(pair)
        r = self._room(slug)
        if r:
            return r
        try:
            r = self.store.insert("comm_rooms",
                                  {"slug": slug, "name": "Direct message", "kind": "dm",
                                   "description": "", "is_public": False, "owner_email": a},
                                  returning=True)
        except _StoreError:
            r = self._room(slug)
        for e in (a, b):
            try:
                self.store.insert("comm_members", {"room_id": r["id"], "email": e, "role": "member"},
                                  returning=False, on_conflict="room_id,email")
            except _StoreError:
                pass
        return r

    # ------------------------------------------------------------ messages
    def _view(self, m, viewer, pros=None):
        sender = m.get("sender_email", "")
        if sender == MODERATOR:
            return {"id": m.get("id"), "username": MODERATOR_HANDLE, "number": None,
                    "body": m.get("body", ""), "t": m.get("created_at", ""),
                    "mine": False, "admin": False, "mod": True}
        if sender.startswith("bot:"):
            return {"id": m.get("id"), "username": sender[4:] + " \U0001f916", "number": None,
                    "body": m.get("body", ""), "t": m.get("created_at", ""),
                    "mine": False, "admin": False, "mod": False, "bot": True}
        row = (pros or {}).get(sender) or {}
        return {"id": m.get("id"), "username": row.get("username") or "member",
                "number": row.get("oracool_number"), "avatar": row.get("avatar_url", ""),
                "verified": bool(self.d.get("is_verified") and self.d["is_verified"](sender)),
                "body": m.get("body", ""), "media_url": m.get("media_url", ""),
                "reactions": self._reactions_count(m.get("reactions")),
                "t": m.get("created_at", ""), "mine": sender == viewer,
                "admin": bool(sender and self.d["is_admin"](sender)), "mod": False}

    def room_messages(self, email, room, after_id=None, limit=60):
        self._need()
        email = email.lower()
        self.presence[email] = time.time()
        r = self._room(str(room or ""))
        if not r:
            return {"error": "Unknown room."}
        if r.get("banned"):
            return {"error": "This " + str(r.get("kind") or "room") + " has been banned by an administrator.", "banned": True}
        if r.get("kind") == "dm":
            return {"error": "Use the DM thread for that."}
        if not self._room_member_ok(r, email):
            return {"error": "This " + str(r.get("kind") or "group") + " is private — the creator adds members with their OraCool number."}
        limit = max(5, min(int(limit or 60), 100))
        rows = self.store.get("comm_messages",
                              "room_id=eq." + r["id"] + "&order=id.desc&limit=" + str(limit + 5))
        if after_id:
            try:
                after = int(after_id)
                rows = [m for m in rows if int(m.get("id") or 0) > after]
            except Exception:
                pass
        rows = list(reversed(rows[-limit:]))
        pros = self._profiles_for([m.get("sender_email") for m in rows])
        return {"room": r.get("slug"), "name": r.get("name"), "messages": [self._view(m, email, pros) for m in rows]}

    def _rate_ok(self, email):
        now = time.time()
        if now - self.last_post.get(email, 0) < RATE_SECONDS:
            return "You are sending messages too quickly."
        day = time.strftime("%Y-%m-%d")
        d, n = self.daily.get(email, (day, 0))
        if d != day:
            n = 0
        if n >= DAILY_MAX:
            return "Daily message limit reached."
        self.daily[email] = (day, n + 1)
        self.last_post[email] = now
        return None

    def room_send(self, email, room, body, media_url=""):
        self._need()
        email = email.lower()
        r = self._room(str(room or ""))
        if not r:
            return {"error": "Unknown room."}
        if r.get("banned"):
            return {"error": "This " + str(r.get("kind") or "room") + " has been banned by an administrator.", "banned": True}
        if r.get("kind") == "dm":
            return {"error": "Use the DM thread for that."}
        # Channels: only the creator (channel admin) may post. Groups: locked when the
        # creator switches on "only I can post". Administrators are always allowed.
        if r.get("kind") == "channel":
            if r.get("owner_email") != email and not self.d["is_admin"](email):
                return {"error": "This is a channel — only its creator (the channel admin) can post here.", "locked": True}
        elif r.get("kind") == "group" and r.get("owner_only_post"):
            if r.get("owner_email") != email and not self.d["is_admin"](email):
                return {"error": "This group is locked — only the group admin can post here.", "locked": True}
        media_url = str(media_url or "")
        if media_url and "storage/v1/object/public/" not in media_url:
            return {"error": "Attachments must be uploaded first."}
        _ext = media_url.lower().split(".")[-1].split("?")[0]
        _is_photo = _ext in ("jpg", "jpeg", "png", "webp")
        # voice notes upload to the 'dm' folder; videos to the 'media' folder
        _is_video = _ext in ("mp4",) or (_ext == "webm" and "/dm/" not in media_url)
        _is_file = _ext in ("pdf", "txt", "csv", "json", "zip", "doc", "docx", "xls", "xlsx", "ppt", "pptx")
        body = _clean(body, MSG_MAX) or ("\U0001f5bc\ufe0f Photo" if media_url and _is_photo
                                         else ("\U0001f3ac Video" if media_url and _is_video
                                               else ("\U0001f4ce File" if media_url and _is_file
                                                     else ("\U0001f3a4 Voice note" if media_url else ""))))
        if not body:
            return {"error": "Write a message first."}
        err = self._rate_ok(email)
        if err:
            return {"error": err}
        self.ensure_profile(email)
        if not self._room_member_ok(r, email):
            return {"error": "This " + str(r.get("kind") or "group") + " is private — the creator adds members with their OraCool number."}
        _kind = "voice" if media_url else "chat"
        try:
            m = self.store.insert("comm_messages",
                                  {"room_id": r["id"], "sender_email": email, "body": body,
                                   "kind": _kind, "media_url": media_url or ""})
        except _StoreError:
            # Legacy DBs carry a kind check-constraint without 'voice' (informational
            # column — clients render from media_url); retry as 'chat'.
            m = self.store.insert("comm_messages",
                                  {"room_id": r["id"], "sender_email": email, "body": body,
                                   "kind": "chat", "media_url": media_url or ""})
        if not m:
            return {"error": "Message could not be saved. Try again."}
        pros = self._profiles_for([email])
        threading.Thread(target=self._bots_on_message, args=(r, body), daemon=True).start()
        return {"ok": True, "message": self._view(m, email, pros)}

    # ------------------------------------------------------------ people
    def people(self, email, q=""):
        self._need()
        email = email.lower()
        q = (q or "").strip().lstrip("@").lower()
        rows = self.store.get("comm_profiles", "order=created_at.asc&limit=500")
        out = []
        for row in rows:
            e = row.get("email")
            if e == email or e == MODERATOR:
                continue
            hay = " ".join([str(row.get("username") or ""), str(row.get("display_name") or ""),
                            str(row.get("bio") or ""), str(row.get("oracool_number") or "")]).lower()
            if q and q not in hay:
                continue
            out.append(self._public_profile(e, row))
        out.sort(key=lambda x: (not x["online"], not x["admin"], str(x.get("username") or "")))
        return {"people": out[:100], "total": len(out)}

    # ------------------------------------------------------------ direct messages
    def _resolve_peer(self, email, username):
        if (username or "").strip().lstrip("@") == MODERATOR_HANDLE or (username or "").strip().lower() in ("oracool moderator", "moderator"):
            return MODERATOR
        peer = self.email_of(username)
        if not peer or peer == email:
            return None
        return peer

    def _read_state(self, email, room_id):
        row = self.store.one("comm_read_state", "email=eq." + email + "&room_id=eq." + room_id)
        return int(row.get("last_read_id") or 0) if row else 0

    def _mark_read(self, email, room_id, last_id):
        try:
            self.store.insert("comm_read_state",
                              {"email": email, "room_id": room_id, "last_read_id": int(last_id or 0)},
                              returning=False, on_conflict="email,room_id")
        except _StoreError:
            pass

    def _unread(self, email, room_id):
        try:
            last = self._read_state(email, room_id)
            rows = self.store.get("comm_messages", "room_id=eq." + room_id + "&id=gt." + str(last) +
                                  "&select=id&limit=100")
            return min(len(rows), 99)
        except _StoreError:
            return 0

    def _unread_total(self, email):
        mem = self.store.get("comm_members", "email=eq." + email + "&limit=200")
        total = 0
        room_ids = [m.get("room_id") for m in mem if m.get("room_id")]
        if not room_ids:
            return 0
        rooms = {}
        for i in range(0, len(room_ids), 40):
            chunk = room_ids[i:i + 40]
            for r in self.store.get("comm_rooms", "id=in.(" + ",".join(chunk) + ")&kind=eq.dm&limit=100"):
                rooms[r.get("id")] = True
        for rid in rooms:
            total += self._unread(email, rid)
        return min(total, 99)

    def dm_threads(self, email):
        self._need()
        email = email.lower()
        self.presence[email] = time.time()
        mem = self.store.get("comm_members", "email=eq." + email + "&limit=200")
        out = []
        for m in mem:
            rid = m.get("room_id")
            if not rid:
                continue
            r = self.store.one("comm_rooms", "id=eq." + rid)
            if not r or r.get("kind") != "dm":
                continue
            others = [x for x in self.store.get("comm_members", "room_id=eq." + rid + "&limit=5")
                      if x.get("email") != email]
            peer = others[0].get("email") if others else None
            if not peer:
                continue
            last = self.store.one("comm_messages", "room_id=eq." + rid + "&order=id.desc&limit=1")
            unread = self._unread(email, rid)
            pro = self._prof(peer) or {}
            last_mine = bool(last and last.get("sender_email") == email)
            last_seen = bool(last and last_mine and int(last.get("id") or 0) <= self._read_state(peer, rid))
            out.append({"username": self.handle_of(peer) or "member",
                        "last_mine": last_mine, "last_seen": last_seen,
                        "last_media": bool(last and (last.get("media_url") or "")),
                        "avatar": pro.get("avatar_url", ""),
                        "verified": bool(self.d.get("is_verified") and self.d["is_verified"](peer)),
                        "number": pro.get("oracool_number"),
                        "mod": peer == MODERATOR, "t": last.get("created_at", "") if last else "",
                        "preview": ((last.get("body") or "")[:120]) if last else "",
                        "unread": unread, "online": self._online(peer),
                        "admin": bool(peer != MODERATOR and self.d["is_admin"](peer))})
        out.sort(key=lambda x: str(x.get("t") or ""), reverse=True)
        return {"threads": out}

    def dm_messages(self, email, username, after_id=None, limit=60):
        self._need()
        email = email.lower()
        self.presence[email] = time.time()
        peer = self._resolve_peer(email, username)
        if not peer:
            return {"error": "No member with that username."}
        r = self._ensure_dm_room(email, peer)
        limit = max(5, min(int(limit or 60), 100))
        rows = self.store.get("comm_messages", "room_id=eq." + r["id"] + "&order=id.desc&limit=" + str(limit + 5))
        if after_id:
            try:
                after = int(after_id)
                rows = [m for m in rows if int(m.get("id") or 0) > after]
            except Exception:
                pass
        rows = list(reversed(rows[-limit:]))
        self._mark_read(email, r["id"], rows[-1].get("id") if rows else self._read_state(email, r["id"]))
        pros = self._profiles_for([m.get("sender_email") for m in rows])
        game = self._latest_game(r["id"])
        return {"username": self.handle_of(peer), "mod": peer == MODERATOR, "online": self._online(peer),
                "avatar": (self._prof(peer) or {}).get("avatar_url", ""),
                "peer_seen_upto": self._read_state(peer, r["id"]),
                "game": self._game_view(game, email),
                "messages": [self._view(m, email, pros) for m in rows]}

    def _dm_append(self, sender, recipient, body, mod=False, media_url=""):
        r = self._ensure_dm_room(sender if sender != MODERATOR else recipient, recipient if sender != MODERATOR else sender)
        kind = "mod" if mod else ("voice" if media_url else "chat")
        try:
            m = self.store.insert("comm_messages",
                                  {"room_id": r["id"], "sender_email": sender, "body": body,
                                   "kind": kind, "media_url": media_url or ""})
        except _StoreError:
            # Legacy databases still carry a kind check-constraint without 'voice';
            # the column is informational (clients render from media_url), retry as 'chat'.
            m = self.store.insert("comm_messages",
                                  {"room_id": r["id"], "sender_email": sender, "body": body,
                                   "kind": "chat", "media_url": media_url or ""})
        if sender != MODERATOR and m:
            self._mark_read(sender, r["id"], m.get("id"))
        return m

    def dm_send(self, email, username, body, media_url=""):
        self._need()
        email = email.lower()
        peer = self._resolve_peer(email, username)
        if not peer or peer == MODERATOR:
            return {"error": "No member with that username." if peer != MODERATOR
                    else "The moderator does not take replies. Use Report to reach it."}
        media_url = str(media_url or "")
        if media_url and "storage/v1/object/public/" not in media_url:
            return {"error": "Attachments must be uploaded first."}
        _ext = media_url.lower().split(".")[-1].split("?")[0]
        _is_photo = _ext in ("jpg", "jpeg", "png", "webp")
        _is_video = _ext in ("mp4",) or (_ext == "webm" and "/dm/" not in media_url)
        _is_file = _ext in ("pdf", "txt", "csv", "json", "zip", "doc", "docx", "xls", "xlsx", "ppt", "pptx")
        body = _clean(body, MSG_MAX) or ("🖼️ Photo" if media_url and _is_photo
                                         else ("🎬 Video" if media_url and _is_video
                                               else ("📎 File" if media_url and _is_file
                                                     else ("🎤 Voice note" if media_url else ""))))
        if not body:
            return {"error": "Write a message first."}
        err = self._rate_ok(email)
        if err:
            return {"error": err}
        self.ensure_profile(email)
        m = self._dm_append(email, peer, body, media_url=media_url)
        if not m:
            return {"error": "Message could not be saved. Try again."}
        try:
            self.d["emit_event"](peer, "community", "Message from @" + str(self.handle_of(email) or "member"), body[:140])
        except Exception:
            pass
        pros = self._profiles_for([email])
        return {"ok": True, "message": self._view(m, email, pros)}

    def moderator_message(self, recipient, body):
        try:
            return self._dm_append(MODERATOR, recipient, body, mod=True)
        except Exception:
            return None

    # ------------------------------------------------------------ reports
    def _reporter_counts(self, reporter):
        """Does this reporter's report count toward the threshold?"""
        try:
            rows = self.store.get("comm_reports", "reporter_email=eq." + reporter + "&limit=200")
        except _StoreError:
            rows = []
        filed = len(rows)
        unfounded = sum(1 for r in rows if r.get("status") == "unfounded")
        if unfounded >= 3 and unfounded * 2 > filed:
            return False, "low_credibility"
        try:
            created = (self.d["load_users"]().get(reporter) or {}).get("created")
        except Exception:
            created = None
        if created and (time.time() - _ts(created)) < MIN_REPORTER_AGE_SEC:
            return False, "new_account"
        return True, ""

    def _fresh_reporters(self, reported):
        rows = self.store.get("comm_reports",
                              "subject_email=eq." + reported + "&status=eq.open&counts=eq.true&limit=500")
        out = []
        for r in rows:
            if r.get("case_id") in (None, "") and r.get("reporter_email") not in out:
                out.append(r.get("reporter_email"))
        return out

    def _authored_message(self, author, mid):
        try:
            mid = int(mid)
        except Exception:
            return None
        row = self.store.one("comm_messages", "id=eq." + str(mid) + "&limit=1")
        if row and row.get("sender_email") == author:
            return row.get("body") or ""
        return None

    def report(self, reporter, username, reason, message_ids=None, number=None):
        self._need()
        reporter = (reporter or "").lower()
        if number:
            row = self.profile_by_number(number)
            reported = row.get("email") if row else None
        else:
            reported = self.email_of(username)
        if not reported:
            return {"error": "No member with that username or OraCool number."}
        if reported == reporter:
            return {"error": "You cannot report yourself."}
        if reported == MODERATOR or self.d["is_admin"](reported):
            return {"error": "Administrators and the moderator cannot be reported here. Use the Help desk."}
        if self.d.get("is_verified") and self.d["is_verified"](reported):
            return {"error": "Verified members (✦) can only be reviewed by an administrator."}
        reason = _clean(reason, REASON_MAX)
        if len(reason) < REASON_MIN:
            return {"error": "Describe what happened (at least %d characters)." % REASON_MIN}
        quotes = []
        for mid in (message_ids or [])[:10]:
            txt = self._authored_message(reported, str(mid))
            if txt:
                quotes.append({"id": str(mid), "text": txt[:500]})
        counts, why = self._reporter_counts(reporter)
        existing = self.store.one("comm_reports",
                                  "reporter_email=eq." + reporter + "&subject_email=eq." + reported +
                                  "&status=eq.open&limit=1")
        if existing:
            self.store.patch("comm_reports", "id=eq." + str(existing.get("id")),
                             {"reason": reason, "quotes": quotes or (existing.get("quotes") or []),
                              "counts": counts, "why": why})
        else:
            self.store.insert("comm_reports",
                              {"reporter_email": reporter, "subject_email": reported,
                               "handle": self.handle_of(reported) or "", "reason": reason, "quotes": quotes,
                               "counts": counts, "why": why, "status": "open"})
        fresh = self._fresh_reporters(reported)
        started, case_id = False, None
        if len(fresh) >= REPORT_THRESHOLD:
            case_id = self._maybe_open_case(reported, fresh)
            started = bool(case_id)
        out = {"ok": True, "reports": len(fresh), "threshold": REPORT_THRESHOLD, "counts": counts,
               "review_started": started, "case_id": case_id,
               "message": ("Report received. The OraCool moderator is reviewing this account now." if started else
                           "Report received (%d of %d needed for a moderator review)." % (min(len(fresh), REPORT_THRESHOLD), REPORT_THRESHOLD))}
        if not counts:
            out["note"] = ("Reports from accounts younger than an hour do not count toward a review." if why == "new_account"
                           else "Several of your earlier reports were found to be unfounded, so this one carries no weight.")
        return out

    def _maybe_open_case(self, reported, reporters):
        try:
            recent = self.store.get("comm_cases", "subject_email=eq." + reported +
                                    "&order=opened_at.desc&limit=5")
        except _StoreError:
            recent = []
        for c in recent:
            if c.get("status") in ("reviewing", "pending_admin"):
                return None
            if (time.time() - _ts(c.get("opened_at"))) < CASE_COOLDOWN_SEC and c.get("status") != "overturned":
                return None
        c = self.store.insert("comm_cases",
                              {"subject_email": reported, "handle": self.handle_of(reported) or "",
                               "reporters": list(reporters), "status": "reviewing",
                               "decided_by": "oracool-ai-moderator"})
        cid = c.get("id") if c else None
        if cid:
            rows = self.store.get("comm_reports",
                                  "subject_email=eq." + reported + "&status=eq.open&counts=eq.true&limit=500")
            for r in rows:
                if r.get("case_id") in (None, "") and r.get("reporter_email") in reporters:
                    self.store.patch("comm_reports", "id=eq." + str(r.get("id")), {"case_id": cid})
        if self.async_reviews and cid:
            threading.Thread(target=self.review_case, args=(str(cid),), daemon=True).start()
        elif cid:
            self.review_case(str(cid))
        return cid

    # ------------------------------------------------------------ room reports & bans
    def report_room(self, reporter, slug, reason):
        self._need()
        reporter = reporter.lower()
        r = self._room(str(slug or ""))
        if not r:
            return {"error": "Unknown room."}
        if r.get("kind") == "dm":
            return {"error": "DM threads cannot be reported — report the member instead."}
        if r.get("banned"):
            return {"error": "This room has already been banned by an administrator."}
        reason = _clean(reason, REASON_MAX)
        if len(reason) < REASON_MIN:
            return {"error": "Describe what is happening in this room (at least %d characters)." % REASON_MIN}
        existing = self.store.one("comm_room_reports",
                                  "room_id=eq." + r["id"] + "&reporter_email=eq." + reporter +
                                  "&status=eq.open&limit=1")
        if existing:
            self.store.patch("comm_room_reports", "id=eq." + str(existing.get("id")), {"reason": reason})
        else:
            self.store.insert("comm_room_reports",
                              {"room_id": r["id"], "reporter_email": reporter, "reason": reason, "status": "open"})
        rows = self.store.get("comm_room_reports", "room_id=eq." + r["id"] + "&status=eq.open&limit=500")
        return {"ok": True, "reports": len(rows),
                "message": "Room report sent to the administrators. They can ban this room from Admin → Moderation."}

    def admin_ban_room(self, slug, banned, reason, admin_email):
        self._need()
        r = self._room(str(slug or ""))
        if not r:
            return {"error": "Unknown room."}
        if r.get("kind") == "dm":
            return {"error": "DM threads cannot be banned."}
        self.store.patch("comm_rooms", "id=eq." + r["id"],
                         {"banned": bool(banned), "ban_reason": _clean(reason, 300) if banned else ""})
        if banned:
            self.store.patch("comm_room_reports", "room_id=eq." + r["id"] + "&status=eq.open", {"status": "banned"})
            self._safe_notify("moderation", "Room banned: #%s" % r.get("name"),
                              "Banned by %s: %s" % (admin_email, _clean(reason, 200) or "no reason given"))
        else:
            self.store.patch("comm_room_reports", "room_id=eq." + r["id"] + "&status=eq.banned", {"status": "dismissed"})
            self._safe_notify("moderation", "Room unbanned: #%s" % r.get("name"), "By " + admin_email)
        rr = self._room(str(slug or ""))
        return {"ok": True, "room": {"id": rr.get("slug"), "name": rr.get("name"), "kind": rr.get("kind"),
                                     "banned": bool(rr.get("banned")), "ban_reason": rr.get("ban_reason", "")}}

    # --------------------------------------- room permissions, delete, reactions, games
    @staticmethod
    def _reactions_count(raw):
        try:
            r = json.loads(raw or "{}")
        except Exception:
            r = {}
        if not isinstance(r, dict):
            return {}
        return {str(k): len(v) for k, v in r.items() if isinstance(v, list) and v}

    def room_set_owner_only(self, email, slug, on):
        self._need()
        email = email.lower()
        r = self._room(str(slug or ""))
        if not r or r.get("kind") not in ("group", "channel"):
            return {"error": "Only your groups and channels support posting settings."}
        if r.get("kind") == "channel":
            return {"error": "Channels are always owner-only — no setting needed."}
        if r.get("owner_email") != email and not self.d["is_admin"](email):
            return {"error": "Only the group creator (or an administrator) can change this."}
        try:
            self.store.patch("comm_rooms", "id=eq." + r["id"], {"owner_only_post": bool(on)})
        except _StoreError:
            return {"error": "Room settings are being set up — run the Patch 18 SQL in Supabase, then try again."}
        rr = self._room(str(slug or "")) or r
        return {"ok": True, "owner_only": bool(rr.get("owner_only_post"))}

    def delete_room(self, email, slug):
        self._need()
        email = email.lower()
        r = self._room(str(slug or ""))
        if not r:
            return {"error": "Unknown room."}
        if r.get("kind") not in ("group", "channel"):
            return {"error": "You can only delete your own groups and channels."}
        if r.get("owner_email") != email and not self.d["is_admin"](email):
            return {"error": "Only the creator (or an administrator) can delete this " + str(r.get("kind")) + "."}
        for t in ("comm_games", "comm_room_reports", "comm_members", "comm_messages"):
            try:
                self.store.delete(t, "room_id=eq." + str(r["id"]))
            except _StoreError:
                pass
        try:
            self.store.delete("comm_rooms", "id=eq." + str(r["id"]))
        except _StoreError as e:
            return {"error": "Could not delete it yet: " + str(e)[:120]}
        self._safe_notify("moderation", str(r.get("kind") or "room") + " deleted: #" + str(r.get("name") or slug),
                          "Deleted by " + email)
        return {"ok": True, "deleted": r.get("name") or slug}

    REACTION_SET = ("\u2764\ufe0f", "\U0001f44d", "\U0001f602", "\U0001f62e", "\U0001f622", "\U0001f64f")

    def react(self, email, message_id, emoji):
        self._need()
        email = email.lower()
        emoji = str(emoji or "")
        if emoji not in self.REACTION_SET:
            return {"error": "Pick one of the quick reactions."}
        try:
            mid = str(int(message_id))
        except Exception:
            return {"error": "Message not found."}
        try:
            m = self.store.one("comm_messages", "id=eq." + mid)
        except _StoreError:
            m = None
        if not m:
            return {"error": "Message not found (it may be from before reactions existed)."}
        try:
            reps = json.loads(m.get("reactions") or "{}")
        except Exception:
            reps = {}
        if not isinstance(reps, dict):
            reps = {}
        users = reps.get(emoji)
        if not isinstance(users, list):
            users = []
        if email in users:
            users.remove(email)
        else:
            users.append(email)
        if users:
            reps[emoji] = users
        else:
            reps.pop(emoji, None)
        try:
            self.store.patch("comm_messages", "id=eq." + mid, {"reactions": json.dumps(reps)})
        except _StoreError:
            return {"error": "Reactions are being set up — run the Patch 18 SQL in Supabase, then try again."}
        return {"ok": True, "reactions": self._reactions_count(json.dumps(reps)), "on": email in users}

    # --------------------------------------------- 2-player chat games (Tic-Tac-Toe)
    _WIN_LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))

    @staticmethod
    def _parse_board(b):
        try:
            rows = json.loads(b or "[]")
            if isinstance(rows, list) and len(rows) == 9:
                return [None if x is None else str(x) for x in rows]
        except Exception:
            pass
        return [None] * 9

    @classmethod
    def _winner_of(cls, board):
        for a, b2, c in cls._WIN_LINES:
            if board[a] is not None and board[a] == board[b2] == board[c]:
                return board[a]
        return None

    def _game_view(self, g, viewer):
        if not g:
            return None
        a, b = g.get("email_a"), g.get("email_b")
        you = "X" if a == viewer else ("O" if b == viewer else None)
        return {"id": g.get("id"), "type": g.get("type") or "tictactoe",
                "board": self._parse_board(g.get("board")), "you": you,
                "turn_is_you": g.get("turn") == viewer,
                "status": g.get("status") or "playing",
                "winner": self.handle_of(g.get("winner") or "") or "",
                "you_won": g.get("winner") == viewer,
                "created": str(g.get("created_at") or "")}

    def _latest_game(self, room_id):
        try:
            rows = self.store.get("comm_games", "room_id=eq." + str(room_id) + "&order=id.desc&limit=1")
            return rows[0] if rows else None
        except _StoreError:
            return None

    def game_start(self, email, username):
        self._need()
        email = email.lower()
        peer = self._resolve_peer(email, username)
        if not peer or peer == MODERATOR:
            return {"error": "No member with that username."}
        self.ensure_profile(email)
        r = self._ensure_dm_room(email, peer)
        g = self._latest_game(r["id"])
        if g and g.get("status") == "playing":
            return {"ok": True, "game": self._game_view(g, email), "resumed": True}
        try:
            g = self.store.insert("comm_games",
                                  {"room_id": r["id"], "email_a": email, "email_b": peer,
                                   "type": "tictactoe", "board": json.dumps([None] * 9),
                                   "turn": email, "status": "playing", "winner": None})
        except _StoreError as e:
            return {"error": "Games are being set up — run the Patch 18 SQL in Supabase, then try again."}
        return {"ok": True, "game": self._game_view(g, email)}

    def game_move(self, email, game_id, idx):
        self._need()
        email = email.lower()
        try:
            gid = str(int(game_id))
            idx = int(idx)
        except Exception:
            return {"error": "Bad move."}
        try:
            g = self.store.one("comm_games", "id=eq." + gid)
        except _StoreError:
            g = None
        if not g:
            return {"error": "Games are being set up — run the Patch 18 SQL in Supabase, then try again."}
        if email not in (g.get("email_a"), g.get("email_b")):
            return {"error": "You are not in this game."}
        if g.get("status") != "playing":
            return {"error": "This game has ended — start a rematch."}
        if g.get("turn") != email:
            return {"error": "Wait — it's not your turn yet."}
        board = self._parse_board(g.get("board"))
        if not (0 <= idx <= 8) or board[idx] is not None:
            return {"error": "That square is taken."}
        me = "X" if g.get("email_a") == email else "O"
        board[idx] = me
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        win = self._winner_of(board)
        if win is not None:
            patch = {"board": json.dumps(board), "status": "won", "winner": email, "updated_at": now}
        elif all(x is not None for x in board):
            patch = {"board": json.dumps(board), "status": "draw", "updated_at": now}
        else:
            other = g.get("email_b") if g.get("email_a") == email else g.get("email_a")
            patch = {"board": json.dumps(board), "turn": other, "updated_at": now}
        try:
            self.store.patch("comm_games", "id=eq." + gid, patch)
        except _StoreError as e:
            return {"error": "Could not save the move: " + str(e)[:120]}
        g2 = self.store.one("comm_games", "id=eq." + gid) or g
        return {"ok": True, "game": self._game_view(g2, email)}

    # ------------------------------------------------------------ the AI review
    def dossier(self, reported, reporters):
        row = self._prof(reported) or {}
        users = {}
        try:
            users = self.d["load_users"]()
        except Exception:
            pass
        urec = users.get(reported) or {}
        try:
            reps = self.store.get("comm_reports", "subject_email=eq." + reported + "&limit=500")
        except _StoreError:
            reps = []
        try:
            cases = [c for c in self.store.get("comm_cases", "subject_email=eq." + reported + "&order=opened_at.desc&limit=20")
                     if c.get("status") != "reviewing"]
        except _StoreError:
            cases = []
        room_msgs = []
        try:
            ms = self.store.get("comm_messages", "sender_email=eq." + reported + "&order=id.desc&limit=80")
            room_msgs = [{"t": m.get("created_at"), "text": (m.get("body") or "")[:500]} for m in reversed(ms)]
        except _StoreError:
            pass
        dm_msgs = []
        for r in reporters:
            try:
                dm = self._ensure_dm_room(reported, r)
                ms = self.store.get("comm_messages", "room_id=eq." + dm["id"] + "&sender_email=eq." + reported +
                                    "&order=id.desc&limit=20")
                dm_msgs += [{"with": "reporter_" + _hid(r)[:6], "t": m.get("created_at"),
                             "text": (m.get("body") or "")[:500]} for m in reversed(ms)]
            except Exception:
                pass
        dm_msgs = dm_msgs[-40:]
        reporter_set = set(reporters)
        return {
            "reported_member": {"username": row.get("username"), "bio": row.get("bio", ""),
                                "joined": str(row.get("created_at") or urec.get("created") or "")[:10],
                                "plan": (self.d["check_tier"](reported) if self.d.get("check_tier") else "unknown"),
                                "previous_cases": [{"verdict": c.get("verdict"), "category": c.get("category"),
                                                    "when": c.get("decided_at")} for c in cases][-5:]},
            "reports": [{"reporter": "reporter_" + _hid(r.get("reporter_email") or "")[:6],
                         "when": r.get("created_at"), "reason": r.get("reason"),
                         "quoted_messages": [q.get("text") for q in (r.get("quotes") or []) if isinstance(q, dict)]}
                        for r in reps if r.get("reporter_email") in reporter_set],
            "reported_member_room_messages": room_msgs,
            "reported_member_messages_to_reporters": dm_msgs,
        }

    @staticmethod
    def parse_verdict(text):
        if not text:
            return None
        t = str(text).strip()
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            return None
        try:
            v = json.loads(m.group(0))
        except Exception:
            return None
        verdict = str(v.get("verdict", "")).strip().lower()
        if verdict not in VERDICTS:
            return None
        try:
            conf = max(0.0, min(1.0, float(v.get("confidence", 0))))
        except Exception:
            conf = 0.0
        cat = str(v.get("category", "none") or "none").strip().lower()
        ev = v.get("evidence") or []
        if not isinstance(ev, list):
            ev = [str(ev)]
        return {"verdict": verdict, "confidence": conf, "category": cat if cat in BLOCKABLE else "none",
                "summary": _clean(v.get("summary", ""), 600), "evidence": [_clean(x, 300) for x in ev][:6]}

    def _case(self, cid):
        try:
            return self.store.one("comm_cases", "id=eq." + str(int(cid)))
        except Exception:
            return self.store.one("comm_cases", "id=eq." + str(cid))

    def review_case(self, cid, apply=True):
        self._need()
        c = self._case(cid)
        if not c:
            return {"error": "Unknown case."}
        reported = c["subject_email"]
        reporters = list(c.get("reporters") or [])
        dossier = self.dossier(reported, reporters)
        n_msgs = len(dossier["reported_member_room_messages"]) + len(dossier["reported_member_messages_to_reporters"]) \
            + sum(len(r["quoted_messages"]) for r in dossier["reports"])
        model_err = None
        if n_msgs == 0:
            verdict = {"verdict": "insufficient_evidence", "confidence": 1.0, "category": "none",
                       "summary": "The reported member has written no messages that can be reviewed.", "evidence": []}
        else:
            text, model_err = None, None
            try:
                text, model_err = self.d["llm_complete"](
                    [{"role": "system", "content": MODERATOR_SYSTEM_PROMPT},
                     {"role": "user", "content": "Dossier (JSON):\n" + json.dumps(dossier, ensure_ascii=False)}],
                    0.0, 700)
            except Exception as e:
                model_err = str(e)
            verdict = self.parse_verdict(text) if text else None
            if verdict is None:
                model_err = model_err or "The moderator model returned no usable verdict."
        return self._decide(str(c.get("id")), verdict, model_err, dossier, apply)

    def _decide(self, cid, verdict, model_err, dossier, apply):
        c = self._case(cid)
        reported = c["subject_email"]
        handle = c.get("handle") or self.handle_of(reported) or "member"
        fields = {"decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "evidence_count": len(dossier["reported_member_room_messages"]) +
                  len(dossier["reported_member_messages_to_reporters"])}
        if verdict is None:
            self.store.patch("comm_cases", "id=eq." + cid,
                             dict(fields, **{"status": "pending_admin", "action": "none",
                                             "error": (model_err or "")[:300]}))
            self._safe_notify("moderation", "Moderator review needs a human: @%s" % handle,
                              "The AI review could not complete (%s). %d members reported this account. Review it in Admin → Moderation." % ((model_err or "unknown error")[:120], len(c.get("reporters") or [])))
            return {"status": "pending_admin"}
        self.store.patch("comm_cases", "id=eq." + cid,
                         dict(fields, **{"verdict": verdict["verdict"], "confidence": verdict["confidence"],
                                         "category": verdict["category"], "summary": verdict["summary"],
                                         "evidence": verdict["evidence"]}))
        if not apply:
            self.store.patch("comm_cases", "id=eq." + cid, {"status": "advisory"})
            return self._case_view(self._case(cid))
        reporters = c.get("reporters") or []
        v = verdict["verdict"]
        if v == "block" and verdict["confidence"] >= BLOCK_CONFIDENCE and verdict["category"] in BLOCKABLE:
            reason = "OraCool moderator (AI review of %d member reports) — %s: %s" % (
                len(reporters), verdict["category"].replace("_", " "),
                verdict["summary"] or "evidence of illegal activity")
            res = self.d["block_user"](reported, True, reason[:400], "oracool-ai-moderator")
            if res.get("ok"):
                self.store.patch("comm_cases", "id=eq." + cid, {"status": "blocked", "action": "suspended"})
                self.store.patch("comm_reports", "case_id=eq." + cid, {"status": "upheld"})
                self._safe_notify("moderation", "Account suspended by the moderator: @%s" % handle,
                                  "%s (confidence %.0f%%). Overturn from Admin → Moderation if this is wrong." % (verdict["summary"][:200], verdict["confidence"] * 100))
                for r in reporters:
                    self._safe_mod_dm(r, "Account suspended: the OraCool moderator reviewed @%s after your report and suspended it for %s. Thank you for keeping the community safe." % (handle, verdict["category"].replace("_", " ")))
                return {"status": "blocked"}
            self.store.patch("comm_cases", "id=eq." + cid,
                             {"status": "pending_admin", "action": "block_failed", "error": str(res.get("error", ""))[:200]})
            self._safe_notify("moderation", "Moderator verdict could not be applied: @%s" % handle,
                              "The suspension write failed (%s). Apply it manually from Admin → Users." % str(res.get("error", ""))[:120])
            return {"status": "pending_admin"}
        if v == "block":     # verdict without enough confidence / category → human decides
            self.store.patch("comm_cases", "id=eq." + cid, {"status": "pending_admin", "action": "escalated"})
            self._safe_notify("moderation", "Moderator recommends suspension, needs your confirmation: @%s" % handle,
                              "%s (confidence %.0f%%). Confirm from Admin → Moderation." % (verdict["summary"][:200], verdict["confidence"] * 100))
            return {"status": "pending_admin"}
        if v == "warn":
            self.store.patch("comm_cases", "id=eq." + cid, {"status": "warned", "action": "warning_sent"})
            self._safe_mod_dm(reported, "Warning from the OraCool moderator: members reported your recent messages and a review found behaviour that breaks the community rules (%s). Further reports can lead to suspension and a reinstatement fine." % (verdict["summary"][:220] or "see community guidelines"))
            for r in reporters:
                self._safe_mod_dm(r, "The OraCool moderator reviewed @%s: a formal warning was issued. Thank you for reporting." % handle)
            self._safe_notify("moderation", "Moderator warned @%s" % handle, verdict["summary"][:200])
            return {"status": "warned"}
        if v == "dismiss":
            self.store.patch("comm_cases", "id=eq." + cid, {"status": "dismissed", "action": "reports_unfounded"})
            self.store.patch("comm_reports", "case_id=eq." + cid, {"status": "unfounded"})
            for r in reporters:
                self._safe_mod_dm(r, "The OraCool moderator reviewed @%s and found no violation. Repeated unfounded reports reduce the weight of your future reports." % handle)
            return {"status": "dismissed"}
        # insufficient_evidence
        self.store.patch("comm_cases", "id=eq." + cid, {"status": "pending_admin", "action": "insufficient_evidence"})
        for r in reporters:
            self._safe_mod_dm(r, "The OraCool moderator could not verify your report about @%s: no messages from that member were available to review. When reporting, tap ⚑ on the exact messages." % handle)
        self._safe_notify("moderation", "Report needs a human look: @%s" % handle,
                          "%d members reported this account but the moderator found no reviewable messages." % len(reporters))
        return {"status": "pending_admin"}

    def _safe_notify(self, etype, title, body):
        try:
            self.d["notify_admins"](etype, title, body)
        except Exception:
            pass

    def _safe_mod_dm(self, recipient, body):
        try:
            self.moderator_message(recipient, body)
        except Exception:
            pass

    # ------------------------------------------------------------ admin
    def _case_view(self, c):
        pros = self._profiles_for([r for r in (c.get("reporters") or []) if r])
        c = dict(c)
        c["reported"] = c.get("subject_email")
        c["opened"] = c.get("opened_at")
        c["reporter_handles"] = [pros.get(r, {}).get("username") or "member" for r in (c.get("reporters") or [])]
        return c

    def admin_overview(self):
        self._need()
        cases = []
        try:
            cases = self.store.get("comm_cases", "order=opened_at.desc&limit=50")
        except _StoreError:
            cases = []
        reps = []
        try:
            reps = self.store.get("comm_reports", "limit=1000")
        except _StoreError:
            reps = []
        by_sub = {}
        for r in reps:
            s = by_sub.setdefault(r.get("subject_email"), {"total": 0, "counting": 0, "last": ""})
            s["total"] += 1
            if r.get("status") == "open" and r.get("counts") and r.get("case_id") in (None, ""):
                s["counting"] += 1
            s["last"] = max(s["last"], str(r.get("created_at") or ""))
        summary = []
        for e, s in by_sub.items():
            if not e or e == MODERATOR:
                continue
            h = self.handle_of(e)
            summary.append({"email": e, "username": h, "handle": h, "total": s["total"],
                            "counting": s["counting"], "last": s["last"],
                            "blocked": bool(self.d["is_blocked"](e))})
        summary.sort(key=lambda x: (-x["counting"], -x["total"]))
        cases = [self._case_view(c) for c in cases]
        stats = {"cases": len(cases),
                 "blocked_by_ai": sum(1 for c in cases if c.get("status") == "blocked"),
                 "pending_admin": sum(1 for c in cases if c.get("status") == "pending_admin"),
                 "warned": sum(1 for c in cases if c.get("status") == "warned"),
                 "dismissed": sum(1 for c in cases if c.get("status") == "dismissed"),
                 "reported_accounts": len(summary), "threshold": REPORT_THRESHOLD}
        room_rows = []
        try:
            groups = self.store.get("comm_rooms", "kind=in.(group,channel)&limit=200")
            for g in groups:
                try:
                    reps = self.store.get("comm_room_reports", "room_id=eq." + str(g.get("id")) + "&limit=500")
                except _StoreError:
                    reps = []
                room_rows.append({"slug": g.get("slug"), "name": g.get("name"), "kind": g.get("kind"),
                                  "owner": self.handle_of(g.get("owner_email") or "") or "",
                                  "reports": sum(1 for x in reps if x.get("status") == "open"),
                                  "total_reports": len(reps), "banned": bool(g.get("banned")),
                                  "ban_reason": g.get("ban_reason", "")})
            room_rows.sort(key=lambda x: (-x["reports"], x["name"]))
        except _StoreError:
            room_rows = []
        return {"cases": cases, "reports": summary[:50], "rooms": room_rows[:100], "stats": stats,
                "note": "Only the moderator AI (after %d member reports) or an administrator can suspend. Reporters never can. Banned rooms are locked for everyone." % REPORT_THRESHOLD}

    def admin_advisory_review(self, email):
        """Human-requested opinion on an account with fewer than three reports: never suspends."""
        self._need()
        email = (email or "").lower()
        if not self.handle_of(email):
            return {"error": "That account has no community profile yet."}
        reps = []
        try:
            reps = [r.get("reporter_email") for r in
                    self.store.get("comm_reports", "subject_email=eq." + email + "&limit=500")
                    if r.get("reporter_email")]
        except _StoreError:
            pass
        c = self.store.insert("comm_cases",
                              {"subject_email": email, "handle": self.handle_of(email) or "",
                               "reporters": reps, "status": "reviewing",
                               "decided_by": "admin-requested advisory"})
        return self.review_case(str(c.get("id")), apply=False) if c else {"error": "Could not open the review."}

    def admin_confirm(self, cid, admin_email):
        """A human administrator applies a pending (escalated / failed) suspension."""
        self._need()
        c = self._case(cid)
        if not c:
            return {"error": "Unknown case."}
        if c.get("status") not in ("pending_admin", "advisory"):
            return {"error": "This case is not waiting for a decision."}
        reason = "Suspended by administrator after moderator review — %s: %s" % (
            (c.get("category") or "rule violation").replace("_", " "), (c.get("summary") or "")[:200])
        res = self.d["block_user"](c["subject_email"], True, reason[:400], admin_email)
        if not res.get("ok"):
            return res
        self.store.patch("comm_cases", "id=eq." + str(c.get("id")),
                         {"status": "blocked", "action": "suspended_by_admin", "decided_by": admin_email,
                          "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        self.store.patch("comm_reports", "case_id=eq." + str(c.get("id")), {"status": "upheld"})
        return {"ok": True, "case": self._case_view(self._case(cid))}

    def admin_overturn(self, cid, admin_email):
        self._need()
        c = self._case(cid)
        if not c:
            return {"error": "Unknown case."}
        res = self.d["block_user"](c["subject_email"], False, "", admin_email)
        if not res.get("ok"):
            return res
        self.store.patch("comm_cases", "id=eq." + str(c.get("id")),
                         {"status": "overturned", "action": "unblocked_by_admin", "decided_by": admin_email,
                          "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        self.store.patch("comm_reports", "case_id=eq." + str(c.get("id")), {"status": "overturned"})
        return {"ok": True, "case": self._case_view(self._case(cid))}

    # ------------------------------------------------------------ chat prompt
    def chat_brief(self, email):
        """Live, READ-ONLY digest of this user's own community — what the main AI
        quotes when the user asks to check their chats, DMs, groups or channels.
        Never posts, never marks read, never exposes other members' private DMs."""
        self._need()
        email = (email or "").lower()
        prof = self._prof(email)
        if not prof:
            return {"error": "This account has no community profile yet (open the Community tab once)."}
        out = {"username": prof.get("username"), "oracool_number": prof.get("oracool_number"),
               "rooms": [], "dm_threads": [], "unread_dm": 0, "games": []}
        # rooms this user is in (or public) with their 3 latest messages
        try:
            rooms = self.store.get("comm_rooms", "kind=in.(room,group,channel)&order=created_at.asc&limit=100")
            mem = self.store.get("comm_members", "email=eq." + email + "&limit=200")
            mine_ids = {m.get("room_id") for m in mem if m.get("room_id")}
        except _StoreError:
            rooms, mine_ids = [], set()
        for r in (rooms or []):
            if r.get("id") not in mine_ids and r.get("kind") == "group":
                continue
            try:
                rows = self.store.get("comm_messages", "room_id=eq." + str(r["id"]) + "&order=id.desc&limit=3")
            except _StoreError:
                rows = []
            msgs = []
            for m in (rows or []):
                if m.get("sender_email") == MODERATOR:
                    who = "moderator"
                else:
                    who = self.handle_of(m.get("sender_email") or "") or "member"
                msgs.append({"who": who, "text": str(m.get("body") or "")[:140],
                             "media": bool(m.get("media_url")), "t": str(m.get("created_at") or "")})
            out["rooms"].append({"name": r.get("name"), "kind": r.get("kind"), "latest": msgs})
        # DM threads (rooms of kind 'dm') — only this user's own conversations
        try:
            dm_rooms = self.store.get("comm_rooms", "kind=eq.dm&order=created_at.desc&limit=50")
            for r in (dm_rooms or []):
                if r.get("owner_email") != email and not self._is_dm_peer(r, email):
                    continue
                try:
                    rows = self.store.get("comm_messages", "room_id=eq." + str(r["id"]) + "&order=id.desc&limit=3")
                except _StoreError:
                    rows = []
                msgs = []
                for m in (rows or []):
                    who = "you" if m.get("sender_email") == email else (self.handle_of(m.get("sender_email") or "") or "member")
                    msgs.append({"who": who, "text": str(m.get("body") or "")[:140],
                                 "media": bool(m.get("media_url")), "t": str(m.get("created_at") or "")})
                if msgs:
                    out["dm_threads"].append({"latest": msgs})
        except _StoreError:
            pass
        # unread DM count (read state vs latest)
        try:
            me = self.store.one("comm_read_state", "email=eq." + email)
            last_read = int(me.get("last_read_id") or 0) if me else 0
            cnt = 0
            for r in (dm_rooms or []):
                try:
                    newest = self.store.get("comm_messages", "room_id=eq." + str(r["id"]) + "&order=id.desc&limit=1")
                    if newest and int(newest[0].get("id") or 0) > last_read:
                        cnt += 1
                except _StoreError:
                    pass
            out["unread_dm"] = cnt
        except _StoreError:
            pass
        # active games
        try:
            games = self.store.get("comm_games", "status=eq.playing&limit=5")
            for g in (games or []):
                if email in (g.get("email_a"), g.get("email_b")):
                    other = g.get("email_b") if g.get("email_a") == email else g.get("email_a")
                    out["games"].append({"type": g.get("type") or "tictactoe",
                                         "with": self.handle_of(other or "") or "member",
                                         "your_turn": g.get("turn") == email})
        except _StoreError:
            pass
        out["note"] = ("READ-ONLY digest of this user's own community. Quote it when asked to check their "
                       "chats; never claim to have sent or posted anything, and never reveal other "
                       "members' DMs.")
        return out

    def _is_dm_peer(self, room, email):
        try:
            mem = self.store.get("comm_members", "room_id=eq." + str(room.get("id")) + "&email=eq." + email + "&limit=1")
            return bool(mem)
        except _StoreError:
            return False

    def brief_for(self, email):
        """One line the main AI gets so it knows this user's community identity."""
        try:
            email = (email or "").lower()
            row = self._prof(email)
            if not row:
                return ""
            line = ("Community identity (server-authoritative): this user's OraCool number is %s and their username "
                    "is @%s. Other members can message them in the Community tab; new friends are added by typing "
                    "this number there. Their community messages are stored and they can scroll back to read them any "
                    "time. If the user asks for their OraCool number or how to add friends, answer from these facts — "
                    "never invent a number." % (row.get("oracool_number"), row.get("username")))
            if self.d.get("is_verified") and self.d["is_verified"](email):
                line += " This user is a VERIFIED member (✦)."
            return line
        except Exception:
            return ""
