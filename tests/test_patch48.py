"""patch48: public creator profile + feature matrix (landing, plans, AI), password reset, admin delete/block truth,
friends saved under your own names, calls that actually carry media."""
import os, sys, io, json, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
LAND = open(os.path.join(ROOT, "landing.html"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch55-kali-look"'), 2)
        self.assertNotIn("patch48-people", SRV)


class Creator(unittest.TestCase):
    def test_profile_and_brief(self):
        c = s.creator_profile()
        self.assertEqual(c["name"], "Daniel Onakoya Adebayo"); self.assertIn("Lagos", c["location"])
        self.assertTrue(any(l["url"].startswith("https://github.com/") for l in c["links"]))
        self.assertNotIn("2009", json.dumps(c)); self.assertNotIn("birth", json.dumps(c).lower())  # never the birthday
        b = s.creator_brief(); self.assertIn("Daniel Onakoya Adebayo", b); self.assertIn("private", b)

    def test_ai_knows_creator_and_features_on_demand(self):
        head = s.identity_prompt_head("someone@example.invalid", "who created you?")
        self.assertIn("Daniel Onakoya Adebayo", head); self.assertIn("Starter $29", head)
        plain = s.identity_prompt_head("someone@example.invalid", "what's the weather?")
        self.assertIn("Daniel Onakoya Adebayo", plain); self.assertNotIn("Starter $29", plain)
        self.assertIn("created by Daniel Onakoya Adebayo", s._IDENTITY_LOCK)
        self.assertIn('_IDENTITY_LOCK + "\\n\\n" + (_ident_head', SRV)  # the creator/feature sheet actually reaches the model
        self.assertEqual(s._pii_scrub("Daniel Onakoya Adebayo built it", "x@y.z"), "Daniel Onakoya Adebayo built it")
        self.assertIn("[private]", s._pii_scrub("mail thinkglobal1000@gmail.com", "x@y.z"))

    def test_landing_renders_matrix_and_card(self):
        self.assertIn("<!--FEATURE_MATRIX-->", LAND); self.assertIn("<!--CREATOR_CARD-->", LAND)
        html_ = s.feature_matrix_html(); self.assertIn("Live step-by-step build feed", html_); self.assertIn('class="fmatrix"', html_)
        self.assertIn('_lh.replace("<!--FEATURE_MATRIX-->", feature_matrix_html()).replace("<!--CREATOR_CARD-->", _card)', SRV)
        self.assertIn("type=recovery", LAND)

    def test_matrix_shape(self):
        m = s.feature_matrix_public()
        self.assertEqual(m["tiers"], ["Free", "Starter", "Pro", "Professional", "Enterprise"])
        for sec in m["sections"]:
            for r in sec["rows"]:
                self.assertEqual(len(r), 6, r)
        names = " ".join(r[0] for sec in m["sections"] for r in sec["rows"])
        for must in ("Charts", "chess", "Code runner", "Photo understanding", "video calls", "Chat modes", "OSINT", "Build coins"):
            self.assertIn(must, names)


class PasswordReset(unittest.TestCase):
    def test_recover_never_reveals(self):
        calls = []
        with mock.patch.object(s, "supabase_auth", lambda path, method="GET", json_body=None, access_token=None: calls.append((path, json_body)) or {"status": 200, "data": {}}):
            r = s.auth_recover("Someone@Example.com", "https://oracool-ai.onrender.com/app")
        self.assertTrue(r["ok"]); self.assertIn("If that address", r["message"])
        self.assertEqual(calls[0][0], "/auth/v1/recover"); self.assertEqual(calls[0][1]["email"], "someone@example.com")
        self.assertEqual(calls[0][1]["redirect_to"], "https://oracool-ai.onrender.com/app")
        self.assertIn("error", s.auth_recover("nope"))

    def test_set_password_rules_and_call(self):
        self.assertIn("error", s.auth_set_password("tok", "short"))
        self.assertIn("error", s.auth_set_password("", "GoodPass1x"))
        seen = {}
        def fake(path, method="GET", json_body=None, access_token=None):
            seen.update(path=path, method=method, body=json_body, tok=access_token); return {"status": 200, "data": {"email": "u@x.y"}}
        with mock.patch.object(s, "supabase_auth", fake):
            r = s.auth_set_password("tok123", "GoodPass1x")
        self.assertTrue(r["ok"]); self.assertEqual(seen["path"], "/auth/v1/user"); self.assertEqual(seen["method"], "PUT"); self.assertEqual(seen["tok"], "tok123")

    def test_routes_and_client(self):
        for needle in ('path == "/api/auth/recover"', 'path == "/api/auth/recovery/verify"', 'path == "/api/auth/password"'):
            self.assertIn(needle, SRV, needle)
        for needle in ('id="gForgot"', "post('/api/auth/recover'", "let gateReset=null", "hp.get('type')==='recovery'", "post('/api/auth/password'", 'id="rPass2"'):
            self.assertIn(needle, APP, needle)


class Admin(unittest.TestCase):
    def test_block_truth_and_delete_cleanup(self):
        self.assertIn('elif "blocked" in f:  # patch48', SRV)
        for needle in ('removed.append("user record")', 'removed.append("community profile")', 'removed.append("conversations")', "_svc.store.delete(tbl, q)"):
            self.assertIn(needle, SRV, needle)

    def test_admin_via_supabase_session(self):
        h = s.Handler.__new__(s.Handler); h.headers = {}; h._auth = lambda body: None
        sent = []
        h._send_json = lambda obj, status=200: sent.append(status)
        with mock.patch.object(s, "request_identity", lambda handler, body, require_supabase=False: "boss@example.invalid"), \
             mock.patch.object(s, "is_admin", lambda e: e == "boss@example.invalid"):
            p = s._require_admin(h, {"access_token": "t"})
        self.assertEqual(p["sub"], "boss@example.invalid"); self.assertTrue(p["admin"]); self.assertFalse(sent)
        with mock.patch.object(s, "request_identity", lambda handler, body, require_supabase=False: "user@example.invalid"), \
             mock.patch.object(s, "is_admin", lambda e: False):
            self.assertIsNone(s._require_admin(h, {"access_token": "t"}))
        self.assertEqual(sent, [403])


class Friends(unittest.TestCase):
    def test_nicknames_by_number(self):
        with mock.patch.object(s, "_contact_names_file", lambda: "/tmp/test48_contacts.json"):
            try:
                os.remove("/tmp/test48_contacts.json")
            except Exception:
                pass
            r = s.contact_name_set("me@x.y", "2000123456", "  Mum  "); self.assertEqual(r["name"], "Mum")
            self.assertEqual(s.contact_names("me@x.y"), {"2000123456": "Mum"})
            out = s._friends_with_names("me@x.y", {"friends": [{"number": 2000123456, "username": "ade"}, {"number": "1", "username": "x"}]})
            self.assertEqual(out["friends"][0]["nickname"], "Mum"); self.assertEqual(out["friends"][1]["nickname"], "")
            s.contact_name_set("me@x.y", "2000123456", ""); self.assertEqual(s.contact_names("me@x.y"), {})
        for needle in ('if action == "friend/name":', 'id="friendName"', "data-fname=", "post('/api/community/friend/name'"):
            self.assertIn(needle, SRV + APP, needle)


class Calls(unittest.TestCase):
    def test_client_media_wiring(self):
        self.assertNotIn("rv.srcObject=CALL.pc", APP)
        for needle in ("pc.ontrack=e=>{", "function callAttachRemote()", "id='remoteAud'", "CALL.pendingIce.push(cd)", "await callFlushIce();", "post('/api/community/call/servers'"):
            self.assertIn(needle, APP, needle)

    def test_ice_servers_fallback_and_operator_turn(self):
        s._ICE_CACHE.clear()
        with mock.patch.object(s, "key", lambda n, d="": ""):
            srv = s._ice_servers()
        self.assertTrue(any("turn:" in u for e in srv for u in (e["urls"] if isinstance(e["urls"], list) else [e["urls"]])))
        s._ICE_CACHE.clear()
        with mock.patch.object(s, "key", lambda n, d="": {"TURN_URLS": "turn:relay.example.com:3478,turns:relay.example.com:5349", "TURN_USERNAME": "u", "TURN_CREDENTIAL": "c"}.get(n, "")):
            srv = s._ice_servers()
        self.assertEqual(srv[0]["urls"], ["turn:relay.example.com:3478", "turns:relay.example.com:5349"]); self.assertEqual(srv[0]["username"], "u")
        s._ICE_CACHE.clear()


class Composer(unittest.TestCase):
    def test_no_focus_glow(self):
        self.assertIn(".gpt-input.big:focus-within{border-color:var(--line) !important", APP)


if __name__ == "__main__":
    unittest.main()


class RightfulCreator(unittest.TestCase):
    """patch48b: the creator is an explicit identity, not 'whichever admin the env lists first'."""
    def test_creator_email_is_explicit(self):
        with mock.patch.object(s, "admin_emails", lambda: ["someone.else@example.com", "danielonakoya19@gmail.com"]), \
             mock.patch.object(s, "key", lambda n, d="": ""):
            self.assertEqual(s._creator_email(), "danielonakoya19@gmail.com")
            self.assertTrue(s.is_admin("danielonakoya19@gmail.com"))
            self.assertTrue(s._is_creator_session("DanielOnakoya19@gmail.com"))
            self.assertFalse(s._is_creator_session("someone.else@example.com"))
        with mock.patch.object(s, "key", lambda n, d="": "owner@custom.tld" if n == "CREATOR_EMAIL" else ""):
            self.assertEqual(s._creator_email(), "owner@custom.tld")
            self.assertTrue(s.is_admin("owner@custom.tld"))

    def test_owner_bond_only_for_the_creator(self):
        with mock.patch.object(s, "admin_emails", lambda: ["other.admin@example.com", "danielonakoya19@gmail.com"]), \
             mock.patch.object(s, "key", lambda n, d="": ""):
            mine = s.identity_prompt_head("danielonakoya19@gmail.com", "hello")
            self.assertIn("RIGHTFUL CREATOR", mine); self.assertIn("Daniel Onakoya Adebayo", mine); self.assertIn("never question", mine)
            other = s.identity_prompt_head("other.admin@example.com", "hello")
            self.assertNotIn("RIGHTFUL CREATOR", other); self.assertIn("never granted to an account by typing", other)

    def test_session_flag_and_badge(self):
        self.assertIn('r["creator"] = _is_creator_session(uemail)', SRV)
        self.assertIn('"creator": _is_creator_session(email)', SRV)
        self.assertIn("✦ Creator", APP); self.assertIn("session.creator=!!r.creator", APP)
