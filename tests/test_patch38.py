"""patch38 — plan table (Free: no OSINT, one-time build coins), builder quality pass,
markdown chat rendering, direct answers, preview-on-request, open apps directly."""
import importlib.util, json, subprocess, sys, unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('oracool_test_server_p38', ROOT / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
SRV = open(ROOT / 'server.py', encoding='utf-8').read()
APP = open(ROOT / 'index.html', encoding='utf-8').read()
LAND = open(ROOT / 'landing.html', encoding='utf-8').read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch42-feed"'), 2)
        self.assertNotIn('patch38-direct', SRV)


class _Users:
    """in-memory stand-in for users.json (user_record / touch_user)."""
    def __init__(self, seed=None):
        self.db = dict(seed or {})
    def record(self, email):
        return self.db.get(email)
    def touch(self, email, **kw):
        self.db.setdefault(email, {}).update(kw)


class CoinsFreeOneTime(unittest.TestCase):
    def _state(self, users, tier, week="2026-W39", email="u@x.com"):
        with ExitStack() as st:
            st.enter_context(mock.patch.object(s, "check_tier", lambda e: tier))
            st.enter_context(mock.patch.object(s, "is_admin", lambda e: False))
            st.enter_context(mock.patch.object(s, "user_record", users.record))
            st.enter_context(mock.patch.object(s, "touch_user", users.touch))
            st.enter_context(mock.patch.object(s, "supabase_get_flag", lambda e: {}))
            st.enter_context(mock.patch.object(s, "_iso_week", lambda: week))
            return s.coins_state(email)

    def test_free_grant_is_one_time_and_never_refills(self):
        u = _Users()
        st = self._state(u, "free")
        self.assertEqual(st["balance"], 1_000_000); self.assertTrue(st["one_time"])
        self.assertEqual(st["week"], "lifetime"); self.assertIsNone(st["reset_in_days"])
        # spend most of it
        u.touch("u@x.com", coins={"week": "lifetime", "balance": 20_000})
        self.assertEqual(self._state(u, "free", week="2026-W40")["balance"], 20_000)
        self.assertEqual(self._state(u, "free", week="2027-W03")["balance"], 20_000)

    def test_stale_weekly_record_on_free_is_kept_not_refilled(self):
        u = _Users({"u@x.com": {"coins": {"week": "2026-W30", "balance": 40_000}}})
        st = self._state(u, "free")
        self.assertEqual(st["balance"], 40_000)
        self.assertEqual(u.db["u@x.com"]["coins"], {"week": "lifetime", "balance": 40_000})

    def test_paid_tier_still_refills_weekly(self):
        u = _Users({"u@x.com": {"coins": {"week": "2026-W38", "balance": 5_000}}})
        st = self._state(u, "starter", week="2026-W39")
        self.assertEqual(st["balance"], 3_000_000); self.assertFalse(st["one_time"])
        self.assertIsInstance(st["reset_in_days"], int)

    def test_gate_message_free_vs_paid(self):
        with mock.patch.object(s, "coins_state", lambda e: {"unlimited": False, "balance": 4_000, "grant": 1_000_000,
                                                             "reset_in_days": None, "one_time": True}):
            g = s.coins_gate("u@x.com")
        self.assertIn("one-time", g["error"]); self.assertIn("do not refill", g["error"])
        self.assertNotIn("weekly reset", g["error"]); self.assertEqual(g["upgrade_plan"], "starter")
        with mock.patch.object(s, "coins_state", lambda e: {"unlimited": False, "balance": 4_000, "grant": 3_000_000,
                                                             "reset_in_days": 3, "one_time": False}):
            g2 = s.coins_gate("u@x.com")
        self.assertIn("weekly reset (3 day(s)", g2["error"])


class OsintStarterPlus(unittest.TestCase):
    def _tools(self, text, tier):
        with ExitStack() as st:
            st.enter_context(mock.patch.object(s, "osint_ip", lambda ip: {"ip": ip, "country": "US"}))
            st.enter_context(mock.patch.object(s, "osint_email", lambda e, k: {"email": e, "breaches": []}))
            st.enter_context(mock.patch.object(s, "osint_darkweb", lambda t: {"term": t, "hits": []}))
            st.enter_context(mock.patch.object(s, "osint_username", lambda u: {"user": u}))
            st.enter_context(mock.patch.object(s, "osint_domain", lambda d: {"domain": d}))
            return s.auto_tools(text, tier=tier, email="u@x.com")

    def test_free_gets_locked_notice_instead_of_lookups(self):
        for msg in ("check ip 8.8.8.8", "is john@example.com in any breach", "search the dark web for acme corp",
                    "username lookup of @coolguy"):
            out = self._tools(msg, "free")
            tools = [o["tool"] for o in out]
            self.assertNotIn("ip", tools, msg); self.assertNotIn("email", tools, msg)
            self.assertNotIn("darkweb", tools, msg); self.assertNotIn("username", tools, msg)
            locked = [o for o in out if o["tool"] == "locked"]
            self.assertTrue(locked, msg)
            self.assertEqual(json.loads(locked[0]["result"])["unlocks_on"], "starter")

    def test_starter_runs_the_lookups(self):
        self.assertIn("ip", [o["tool"] for o in self._tools("check ip 8.8.8.8", "starter")])
        self.assertIn("email", [o["tool"] for o in self._tools("is john@example.com in any breach", "starter")])
        self.assertIn("darkweb", [o["tool"] for o in self._tools("search the dark web for acme corp", "starter")])
        self.assertNotIn("locked", [o["tool"] for o in self._tools("check ip 8.8.8.8", "starter")])

    def _post(self, path, body, tier):
        h = object.__new__(s.Handler)
        h.path = path; h.headers = {}; h.client_address = ("127.0.0.1", 1); h.sent = []
        h._read_json = lambda: dict(body)
        h._send_json = lambda obj, status=200: h.sent.append((status, obj))
        with ExitStack() as st:
            st.enter_context(mock.patch.object(s, "request_identity", lambda *a, **k: body.get("email")))
            st.enter_context(mock.patch.object(s, "is_blocked", lambda e: False))
            st.enter_context(mock.patch.object(s, "audit_log", lambda *a, **k: None))
            st.enter_context(mock.patch.object(s, "check_tier", lambda e: tier))
            st.enter_context(mock.patch.object(s.Handler, "_auth", lambda self, b: None))
            st.enter_context(mock.patch.object(s, "osint_ip", lambda ip: {"ip": ip}))
            st.enter_context(mock.patch.object(s, "osint_darkweb", lambda t: {"term": t}))
            st.enter_context(mock.patch.object(s.phone_intel, "lookup", lambda *a, **k: {"phone": "ok"}))
            s.Handler.do_POST(h)
        return h.sent[-1]

    def test_endpoints_require_starter(self):
        for path, body in (("/api/osint/ip", {"ip": "8.8.8.8"}), ("/api/osint/darkweb", {"target": "acme"}),
                           ("/api/osint/phone", {"phone": "+2348012345678"})):
            body = dict(body, email="free@x.com")
            status, obj = self._post(path, body, "free")
            self.assertEqual(status, 402, path); self.assertTrue(obj.get("locked")); self.assertEqual(obj.get("plan"), "starter")
            status2, obj2 = self._post(path, body, "starter")
            self.assertEqual(status2, 200, path); self.assertFalse(obj2.get("locked"))


class BuilderQuality(unittest.TestCase):
    def test_blueprint_matches_industry(self):
        self.assertIn("restaurant", s._brief_blueprint("a landing page for a suya restaurant in Lagos"))
        self.assertIn("fintech", s._brief_blueprint("crypto wallet app for students"))
        self.assertIn("hotel", s._brief_blueprint("Becfom Hotel rooms and booking"))
        self.assertIn("modern business", s._brief_blueprint("something"))

    def test_design_kit_injection_is_idempotent_and_safe(self):
        h = '<!DOCTYPE html><html><head><title>x</title></head><body><section>hi</section></body></html>'
        k = s._inject_design_kit(h)
        self.assertIn('id="ok-kit"', k); self.assertIn('ok-kit-js', k)
        self.assertLess(k.index('id="ok-kit"'), k.index('</head>')); self.assertLess(k.index('ok-kit-js'), k.rindex('</body>'))
        self.assertEqual(s._inject_design_kit(k), k)
        self.assertEqual(s._inject_design_kit("no head here"), "no head here")
        self.assertIn("prefers-reduced-motion", k); self.assertIn("IntersectionObserver", k)

    def test_quality_issues(self):
        thin = '<html><head></head><body><section>x</section></body></html>'
        self.assertGreaterEqual(len(s._site_quality_issues(thin)), 5)
        good = ('<!DOCTYPE html><html><head><meta name="viewport" content="w"><style>@media(max-width:600px){}.a{transition:all .3s}</style></head><body><header><nav></nav></header>'
                + ''.join('<section><h2>%d</h2>%s</section>' % (i, 'x' * 3000) for i in range(6)) + '<form></form><footer></footer></body></html>')
        self.assertEqual(s._site_quality_issues(good), [])
        self.assertTrue(any("lorem" in i for i in s._site_quality_issues(good.replace("x" * 10, "lorem ipsum", 1))))

    def test_builder_prompt_uses_medium_reasoning_and_blueprint(self):
        seen = []
        def fake_fetch(url, method="GET", timeout=0, headers=None, json_body=None, **k):
            seen.append(json_body)
            return 200, json.dumps({"choices": [{"message": {"content": "TEMPLATE: X\nTITLE: Y\nBEGIN index.html\n<!DOCTYPE html><html><head></head><body></body></html>\nEND"}, "finish_reason": "stop"}]}), {}
        with ExitStack() as st:
            st.enter_context(mock.patch.object(s, "key", lambda k: "k" if k == "GROQ_API_KEY" else ""))
            st.enter_context(mock.patch.object(s, "http_fetch", fake_fetch))
            c, prov, fin = s._llm_text("sys", "usr", effort="medium")
        self.assertEqual(seen[0]["reasoning_effort"], "medium")
        self.assertIn('effort="medium"', SRV[SRV.index("def build_site("):SRV.index("def build_site(") + 9000])
        self.assertIn("DESIGN BLUEPRINT", SRV); self.assertIn("between 18KB and 34KB", SRV)
        self.assertIn("DESIGN REVIEW", SRV); self.assertIn("_inject_design_kit(_idx", SRV)


class BuildListPrivacy(unittest.TestCase):
    def test_only_own_builds_unless_admin(self):
        meta = {"a-site": {"owner": "a@x.com", "name": "A", "t": "2026-09-01T00:00:00Z"},
                "b-site": {"owner": "b@x.com", "name": "B", "t": "2026-09-02T00:00:00Z"},
                "b-old": {"owner": "b@x.com", "name": "B old", "t": "2026-08-02T00:00:00Z"}}
        with ExitStack() as st:
            st.enter_context(mock.patch.object(s, "builds_warm", lambda: None))
            st.enter_context(mock.patch.object(s, "_builds_load", lambda: meta))
            st.enter_context(mock.patch.object(s, "is_admin", lambda e: e == "admin@x.com"))
            b = s.build_list("b@x.com")["builds"]
            self.assertEqual([x["slug"] for x in b], ["b-site", "b-old"])
            self.assertEqual(len(s.build_list("admin@x.com")["builds"]), 3)
            self.assertEqual(s.build_list("")["builds"], [])


class ClientMarkdownAndUX(unittest.TestCase):
    def test_md_js_renders_tables_code_lists(self):
        js = r"""
const M=require(process.argv[1]);
const h=M.render("**Title**\n\n| A | B |\n|---|---|\n| 1 | **2** |\n\n1. one\n2. two\n   - sub\n\n```js\nlet x=1<2;\n```\n### Head\n---\n> q\nend <script>x</script>");
const ok=h.includes('<table class="md-table">')&&h.includes('<th>A</th>')&&h.includes('<td><b>2</b></td>')&&h.includes('<ol class="md-list">')&&h.includes('<ul class="md-list"><li>sub</li>')&&h.includes('data-lang="js"><code>let x=1&lt;2;')&&h.includes('<h4 class="md-h">Head</h4>')&&h.includes('<hr class="md-hr">')&&h.includes('<blockquote class="md-q">')&&!h.includes('<script>')&&!h.includes('|');
console.log(ok?'OK':'FAIL '+h);
"""
        r = subprocess.run(["node", "-e", js, str(ROOT / "md.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "OK", r.stdout + r.stderr)

    def test_client_wiring(self):
        self.assertIn('<script src="/md.js?v=41"></script>', APP)
        self.assertIn('elif path == "/md.js":', SRV)
        self.assertIn("if(window.OraMd){ el.classList.add('md'); s = OraMd.render(text||''); }", APP)
        self.assertIn(".bubble .md-table th{", APP); self.assertIn(".bubble .md{white-space:normal;}", APP)

    def test_direct_answers(self):
        self.assertIn("directAnswers:true", APP)
        self.assertIn("DIRECT-ANSWER MODE (on)", APP); self.assertIn('id="sDirect"', APP)
        self.assertIn("decline that part in a single short clause", APP)

    def test_preview_on_request_and_direct_app_open(self):
        self.assertIn("async function showLatestBuild()", APP); self.assertIn("function wantsPreview(q)", APP)
        self.assertIn("never speculate about why a preview", APP)
        self.assertNotIn("Launch card ready, sir", APP)
        self.assertIn("navigator.userActivation", APP); self.assertIn("pre=window.open('about:blank','_blank')", APP)
        self.assertIn("The browser blocked the pop-up, sir", APP)  # card is the fallback only

    def test_plan_copy(self):
        self.assertIn("['Core OSINT (IP·domain·email·username·phone)','❌','✅','✅','✅','✅']", APP)
        self.assertIn("['Phone intel (global)','❌','✅','✅','✅','✅']", APP)
        self.assertIn("['Dark-web index lookups','❌','✅','✅','✅','✅']", APP)
        self.assertIn("'1,000,000 one-time'", APP); self.assertIn("'❌ one-time only'", APP)
        self.assertIn("free forever · one-time coin grant", APP)
        self.assertNotIn("dark-web index lookups are open on EVERY plan", APP)
        self.assertIn("ONE-TIME 1,000,000 coins that never refill", APP)
        self.assertIn("1,000,000 one-time build coins", LAND)
        self.assertIn("OSINT tools (IP · domain · email · username · phone · dark-web)", LAND)
        self.assertNotIn("Basic public intel", LAND)


if __name__ == "__main__":
    unittest.main()
