"""patch64: BotFather-style community bots + money-gifting wallet removed +
custom-domain attach retained."""
import hashlib
import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402
import community as c  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
SQL = open(os.path.join(ROOT, "supabase_patch64_bots.sql"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_build(self):
        self.assertEqual(SRV.count('"patch88-movers-faq-logo"'), 2)

    def test_wallet_gone(self):
        for needle in ("/api/wallet", "wallet_withdraw", "referral_mark_used",
                       "WALLET_REFERRAL_CENTS", "paystack_initiate_transfer", "fx_rates"):
            self.assertNotIn(needle, SRV, needle)
        for needle in ("OraCool Wallet", "/api/wallet", "Wallet payouts", "$1.00"):
            self.assertNotIn(needle, APP, needle)
        sqls = [f for f in os.listdir(ROOT) if f.endswith(".sql")]
        self.assertNotIn("supabase_patch61_wallet.sql", sqls)

    def test_referrals_kept_coins_only(self):
        self.assertIn("REF_REWARD_COINS", SRV)
        self.assertIn("Refer &amp; earn", APP)
        self.assertNotIn("plus <b>$1.00</b>", APP)

    def test_custom_domain_intact(self):
        self.assertIn("def site_domain_set", SRV)
        self.assertIn("pubDomOpen", APP)  # patch70: domain portal button

    def test_bots_present(self):
        self.assertIn('path.startswith("/api/bot/v1/")', SRV)
        self.assertIn("renderCommBots", APP)
        self.assertIn("create table if not exists public.comm_bots", SQL)
        self.assertIn("Create community bots (BotFather-style)", SRV)


class _FakeStore:
    def __init__(self):
        self.tables = {}
        self.seq = {}

    def _t(self, n):
        return self.tables.setdefault(n, [])

    def ready(self):
        return True

    def get(self, table, query=""):
        return list(self._t(table))

    def one(self, table, query=""):
        for pair in (query or "").split("&"):
            if "=eq." in pair:
                k, v = pair.split("=eq.", 1)
                for r in self._t(table):
                    if str(r.get(k)) == v:
                        return r
        return None

    def insert(self, table, row, returning=True, on_conflict=None):
        self.seq[table] = self.seq.get(table, 0) + 1
        row = dict(row, id=self.seq[table])
        self._t(table).append(row)
        return row

    def patch(self, table, query, fields):
        for r in self._t(table):
            r.update(fields)
        return []

    def delete(self, table, query=""):
        self._t(table).clear()


def _svc():
    deps = {"is_admin": lambda e: False, "check_tier": lambda e: "free",
            "load_users": lambda: {}, "is_blocked": lambda e: False,
            "block_user": lambda *a, **k: {}, "is_verified": lambda e: False,
            "llm_complete": lambda msgs, t=0.0, mx=700: ("Hi, I am your bot!", None),
            "notify_admins": lambda *a, **k: None, "emit_event": lambda *a, **k: None}
    svc = c.Service(None, deps)
    svc.store = _FakeStore()
    svc.store.insert("comm_rooms", {"slug": "lounge", "name": "Lounge", "kind": "room", "id": "1",
                                    "owner_email": "fan@example.com"})
    return svc


class Bots(unittest.TestCase):
    def test_username_rule(self):
        svc = _svc()
        ok, _ = svc._bot_uok("studyhelper_bot")
        self.assertTrue(ok)
        ok, _ = svc._bot_uok("helper")          # doesn't end in bot
        self.assertFalse(ok)
        ok, _ = svc._bot_uok("botfather")
        self.assertFalse(ok)

    def test_create_token_once_and_hash(self):
        svc = _svc()
        r = svc.bot_create("dev@example.com", "Study Helper", "studyhelper_bot",
                           "You help students revise.", "Hi!")
        self.assertTrue(r["ok"], r)
        token = r["token"]
        self.assertIn(":", token)
        row = svc.store._t("comm_bots")[0]
        self.assertNotEqual(row["token_hash"], token)          # never stored in clear
        self.assertEqual(row["token_hash"], hashlib.sha256(token.encode()).hexdigest())
        dup = svc.bot_create("dev2@example.com", "Copycat", "studyhelper_bot", "", "")
        self.assertIn("taken", dup["error"])
        found = svc.bot_by_token("Bearer " + token)
        self.assertTrue(found and found["username"] == "studyhelper_bot")
        self.assertIsNone(svc.bot_by_token("Bearer bogus"))

    def test_mention_triggers_bot_reply(self):
        svc = _svc()
        svc.bot_create("dev@example.com", "Study Helper", "studyhelper_bot",
                       "You help students revise.", "")
        room = svc.store._t("comm_rooms")[0]
        r = svc.room_send("fan@example.com", "lounge", "hey @studyhelper_bot, quiz me on algebra")
        self.assertTrue(r["ok"], r)
        svc._bots_on_message(room, "hey @studyhelper_bot, quiz me on algebra")
        msgs = svc.store._t("comm_messages")
        self.assertTrue(any(str(m.get("sender_email")) == "bot:studyhelper_bot" for m in msgs), msgs)
        v = svc._view(next(m for m in msgs if str(m.get("sender_email")).startswith("bot:")),
                      "fan@example.com")
        self.assertTrue(v["username"].endswith("\U0001f916"))
        self.assertTrue(v["bot"])

    def test_bot_send_api(self):
        svc = _svc()
        svc.bot_create("dev@example.com", "Study Helper", "studyhelper_bot", "", "")
        bot = svc.store._t("comm_bots")[0]
        r = svc.bot_send(bot, "lounge", "Scheduled study reminder!")
        self.assertTrue(r["ok"])
        self.assertEqual(r["message"]["username"], "studyhelper_bot \U0001f916")

    def test_update_reset_delete(self):
        svc = _svc()
        made = svc.bot_create("dev@example.com", "B1", "brainy_one_bot", "", "")
        bid = made["bot"]["id"]
        self.assertTrue(svc.bot_update("dev@example.com", bid, brain="new brain")["ok"])
        self.assertIn("error", svc.bot_update("intruder@example.com", bid, brain="x"))
        old_hash = svc.store._t("comm_bots")[0]["token_hash"]
        rt = svc.bot_reset_token("dev@example.com", bid)
        self.assertTrue(rt["ok"])
        self.assertNotEqual(svc.store._t("comm_bots")[0]["token_hash"], old_hash)
        self.assertTrue(svc.bot_delete("dev@example.com", bid)["ok"])
        self.assertFalse(svc.store._t("comm_bots"))


if __name__ == "__main__":
    unittest.main()


class BotProgrammer(unittest.TestCase):
    def test_program_by_token(self):
        svc = _svc()
        made = svc.bot_create("dev@example.com", "Tutor", "waec_tutor_bot", "", "")
        r = svc.bot_program("dev@example.com", made["token"], None,
                            "a patient WAEC study tutor that quizzes me daily on maths and never gives answers directly")
        self.assertTrue(r["ok"], r)
        row = svc.store._t("comm_bots")[0]
        self.assertTrue(len(row["brain"]) > 20)
        self.assertTrue(row["greeting"])
        bad = svc.bot_program("dev@example.com", "wrong-token", None, "do stuff please")
        self.assertIn("token", bad["error"])

    def test_program_by_owner_bot_id(self):
        svc = _svc()
        made = svc.bot_create("dev@example.com", "Tutor", "waec_tutor_bot", "", "")
        r = svc.bot_program("dev@example.com", "", made["bot"]["id"], "a friendly weather bot for Lagos fishers")
        self.assertTrue(r["ok"], r)
        other = svc.bot_program("stranger@example.com", "", made["bot"]["id"], "a friendly weather bot")
        self.assertIn("error", other)


class Timestamps(unittest.TestCase):
    def test_isots_valid_for_pg(self):
        import re as _re
        v = c._isots()
        self.assertRegex(v, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertNotEqual(v, "0.0")
        # bot_create uses _isots for created_at
        svc = _svc()
        svc.bot_create("dev@example.com", "Timer Bot", "time_check_bot", "", "")
        rows = svc.store._t("comm_bots")
        self.assertTrue(rows, "bot row missing")
        self.assertRegex(str(rows[0]["created_at"]), r"^\d{4}-\d{2}-\d{2}T")
