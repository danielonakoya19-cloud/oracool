"""patch60: power ecosystem — personal API keys (OpenAI-compatible /v1),
AMA hosting and build showcase. Devices tab slimmed down."""
import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402
import community as c  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
SQL = open(os.path.join(ROOT, "supabase_patch60_power_ecosystem.sql"), encoding="utf-8").read()

_ME = "keyuser@example.com"


def _no_cloud():
    return mock.patch.object(s, "_apikey_table_insert", lambda row: True), \
           mock.patch.object(s, "_apikey_table_get", lambda h: None), \
           mock.patch.object(s, "_apikey_table_rows", lambda e: []), \
           mock.patch.object(s, "_apikey_table_patch", lambda h, f: True), \
           mock.patch.object(s, "audit_log", lambda *a, **k: None), \
           mock.patch.object(s, "check_tier", lambda e: "free")


class Marker(unittest.TestCase):
    def test_build_bumped(self):
        self.assertEqual(SRV.count('"patch92-community-write"'), 2)

    def test_openai_compat_routes(self):
        self.assertIn('path == "/v1/chat/completions"', SRV)
        self.assertIn('path == "/v1/models"', SRV)
        self.assertIn('"/api/account/apikeys"', SRV)

    def test_feature_matrix_rows(self):
        self.assertIn("Personal API keys — build on OraCool from any OpenAI-compatible app (/v1)", SRV)
        self.assertIn("Community power features — host AMAs · Showcase your builds", SRV)

    def test_frontend(self):
        self.assertIn("renderApiKeys", APP)
        self.assertIn("sk-ora-", APP)
        self.assertNotIn("Nearby Devices Radar", APP)
        self.assertNotIn("Home Assistant", APP)  # section removed from Devices
        self.assertIn("renderCommAma", APP)
        self.assertIn("renderCommShowcase", APP)
        self.assertIn("ama/create", APP)

    def test_sql(self):
        self.assertIn("create table if not exists public.api_keys", SQL)
        self.assertIn("create table if not exists public.comm_amas", SQL)
        self.assertIn("create table if not exists public.comm_showcase", SQL)


class ApiKeys(unittest.TestCase):
    def setUp(self):
        s._APIKEY_LIMIT_CACHE.clear()

    def test_create_shape(self):
        with _no_cloud()[0], _no_cloud()[1], _no_cloud()[2], _no_cloud()[3], _no_cloud()[4], _no_cloud()[5]:
            r = s.api_key_create(_ME, "my-project")
            self.assertTrue(r["key"].startswith("sk-ora-"))
            self.assertEqual(len(r["key"]), len("sk-ora-") + 40)

    def test_auth_and_chat_roundtrip(self):
        raw = "sk-ora-" + "ab" * 20
        row = {"email": _ME, "name": "t", "key_hash": s._apikey_hash(raw), "prefix": raw[:12],
               "last4": raw[-4:], "revoked": False, "requests": 0}
        with mock.patch.object(s, "_apikey_table_get", lambda h: row if h == row["key_hash"] else None), \
             mock.patch.object(s, "_apikey_table_patch", lambda h, f: True), \
             mock.patch.object(s, "check_tier", lambda e: "free"), \
             mock.patch.object(s, "llm_complete", lambda msgs, temperature=0.4, max_tokens=800: ("Hello from OraCool!", None)):
            bad = s.openai_compat_chat({"messages": [{"role": "user", "content": "hi"}]}, "Bearer wrong")
            self.assertEqual(bad["status"], 401)
            ok = s.openai_compat_chat({"messages": [{"role": "user", "content": "hi"}]}, "Bearer " + raw)
            self.assertEqual(ok["status"], 200)
            self.assertEqual(ok["obj"]["choices"][0]["message"]["content"], "Hello from OraCool!")
            st = s.openai_compat_chat({"messages": [{"role": "user", "content": "hi"}], "stream": True}, "Bearer " + raw)
            self.assertEqual(st["status"], 200)
            gen = st["stream"]
            chunks = list(gen() if callable(gen) else gen)
            self.assertTrue(chunks[-1].endswith(b"data: [DONE]\n\n"))

    def test_rate_limit_free_tier(self):
        raw = "sk-ora-" + "cd" * 20
        row = {"email": _ME, "name": "t", "key_hash": s._apikey_hash(raw), "revoked": False, "requests": 0}
        with mock.patch.object(s, "_apikey_table_get", lambda h: row), \
             mock.patch.object(s, "_apikey_table_patch", lambda h, f: True), \
             mock.patch.object(s, "check_tier", lambda e: "free"), \
             mock.patch.object(s, "llm_complete", lambda msgs, temperature=0.4, max_tokens=800: ("x", None)):
            s.APIKEY_DAILY["free"] = 2
            try:
                self.assertEqual(s.openai_compat_chat({"messages": [{"role": "user", "content": "1"}]}, "Bearer " + raw)["status"], 200)
                self.assertEqual(s.openai_compat_chat({"messages": [{"role": "user", "content": "2"}]}, "Bearer " + raw)["status"], 200)
                self.assertEqual(s.openai_compat_chat({"messages": [{"role": "user", "content": "3"}]}, "Bearer " + raw)["status"], 429)
            finally:
                s.APIKEY_DAILY["free"] = 100

    def test_revoke_and_list(self):
        rows = [{"email": _ME, "name": "k1", "key_hash": "H1", "prefix": "sk-ora-aaaa", "last4": "bbbb",
                 "created_at": "now", "requests": 3, "revoked": False}]
        with mock.patch.object(s, "_apikey_table_rows", lambda e: rows if e == _ME else []), \
             mock.patch.object(s, "_apikey_table_patch", lambda h, f: True):
            lst = s.api_key_list(_ME)
            self.assertEqual(lst["keys"][0]["id"], "H1")
            self.assertIn("/v1", lst["base_url"])
            rv = s.api_key_revoke(_ME, "H1")
            self.assertTrue(rv["ok"])
            self.assertEqual(s.api_key_revoke(_ME, "missing")["status"] if "status" in s.api_key_revoke(_ME, "missing") else s.api_key_revoke(_ME, "missing").get("error"), "That key is not on your account.")

    def test_chat_router_mentions_keys(self):
        with mock.patch.object(s, "key", lambda k: None):
            out = s.auto_tools("how do I create an api key?", tier="free", email=_ME)
            self.assertTrue(any(x["tool"] == "apikeys" for x in out), out)


class _FakeStore:
    def __init__(self):
        self.tables = {}

    def _t(self, n):
        return self.tables.setdefault(n, [])

    def ready(self):
        return True

    def get(self, table, query=""):
        return list(self._t(table))

    def one(self, table, query=""):
        rows = self.get(table)
        return rows[0] if rows else None

    def insert(self, table, row, returning=True, on_conflict=None):
        if isinstance(row, dict):
            row = dict(row, id=len(self._t(table)) + 1)
            self._t(table).append(row)
            return row
        return None

    def patch(self, table, query, fields):
        for r in self._t(table):
            r.update(fields)
        return []

    def delete(self, table, query=""):
        self._t(table).clear()


def _svc():
    st = _FakeStore()
    deps = {"is_admin": lambda e: False, "check_tier": lambda e: "pro",
            "load_users": lambda: {}, "is_blocked": lambda e: False,
            "block_user": lambda *a, **k: {}, "is_verified": lambda e: False,
            "llm_complete": lambda *a, **k: ("ok", None),
            "notify_admins": lambda *a, **k: None, "emit_event": lambda *a, **k: None}
    svc = c.Service(None, deps)
    svc.store = st
    return svc


class AmaShowcase(unittest.TestCase):
    def test_host_gate_and_flow(self):
        svc = _svc()
        pro = _svc()
        free = _svc()
        free.d["check_tier"] = lambda e: "free"
        free.d["load_users"] = lambda: {}
        self.assertTrue(pro.can_host("pro@example.com"))
        self.assertFalse(free.can_host("free@example.com"))
        r = pro.ama_create("pro@example.com", "I built 40 sites — ask me anything", "builder")
        self.assertTrue(r["ok"])
        ama_id = r["ama"]["id"]
        q = pro.ama_ask("fan@example.com", ama_id, "What is your build routine?")
        self.assertTrue(q["ok"])
        qid = q["question"]["id"]
        v = pro.ama_vote("other@example.com", qid)
        self.assertEqual(v["votes"], 1)
        self.assertTrue(pro.ama_answer("pro@example.com", qid, "Every morning, one build.")["ok"])
        self.assertTrue(pro.ama_end("pro@example.com", ama_id)["ok"])
        self.assertIn("error", free.ama_create("free@example.com", "not allowed", ""))

    def test_showcase_share_like(self):
        svc = _svc()
        r = svc.showcase_share("a@example.com", "My shop", "mysite.oracoolai.com", "sneaker store")
        self.assertTrue(r["ok"])
        self.assertTrue(r["item"]["url"].startswith("https://"))
        bad = svc.showcase_share("a@example.com", "x", "not a url", "")
        self.assertEqual(bad.get("error"), "Paste the link to your published build.")
        lst = svc.showcase("b@example.com")
        self.assertEqual(len(lst["items"]), 1)
        like = svc.showcase_like("b@example.com", lst["items"][0]["id"])
        self.assertEqual(like["likes"], 1)
        again = svc.showcase_like("b@example.com", lst["items"][0]["id"])
        self.assertTrue(again.get("already"))


if __name__ == "__main__":
    unittest.main()
