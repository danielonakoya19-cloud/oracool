"""patch61: OraCool Wallet — $1 per referral that unlocks when the friend uses
OraCool, withdrawals from $10, referral-code box on signup."""
import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
SQL = open(os.path.join(ROOT, "supabase_patch61_wallet.sql"), encoding="utf-8").read()

REF = "advocate61@example.com"
FRIEND = "friend61@example.com"


def _quiet():
    return mock.patch.object(s, "supabase_all_flags", lambda: {}), \
           mock.patch.object(s, "supabase_get_flag", lambda e: None), \
           mock.patch.object(s, "supabase_upsert_flag", lambda r, block_fields=True: True), \
           mock.patch.object(s, "_referrals_log", lambda row: None), \
           mock.patch.object(s, "audit_log", lambda *a, **k: None), \
           mock.patch.object(s, "notify_admins", lambda *a, **k: None), \
           mock.patch.object(s, "emit_event", lambda *a, **k: None), \
           mock.patch.object(s, "check_tier", lambda e: "free")


def _cleanup():
    try:
        users = s.load_users()
        changed = False
        for em in list(users):
            if em in (REF, FRIEND):
                del users[em]
                changed = True
        if changed:
            s.save_users(users)
    except Exception:
        pass
    s._WALLET_LOCAL["pending"].clear()
    s._WALLET_LOCAL["earned"].clear()
    s._WALLET_USED_MEMO.clear()


class Marker(unittest.TestCase):
    def test_build_bumped(self):
        self.assertEqual(SRV.count('"patch63-any-currency"'), 2)

    def test_frontend(self):
        self.assertIn('id="gRef"', APP)          # signup gate referral box
        self.assertIn('id="aRef"', APP)          # account-panel referral box
        self.assertIn("OraCool Wallet", APP)
        self.assertIn("/api/wallet", APP)
        self.assertIn("Wallet payouts", APP)     # admin queue
        self.assertIn("data-paid", APP)

    def test_sql(self):
        self.assertIn("wallet_available_cents", SQL)
        self.assertIn("create table if not exists public.wallet_withdrawals", SQL)
        self.assertIn("add column if not exists status text default 'pending'", SQL)

    def test_matrix_row(self):
        self.assertIn("OraCool Wallet — $1 per active referral", SRV)


class WalletFlow(unittest.TestCase):
    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def _local_only(self):
        m = _quiet()
        return m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7]

    def test_pending_on_signup_then_unlock_on_use(self):
        g = self._local_only()
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6], g[7]:
            s.touch_user(REF, ref_code="WALLETC1")
            r = s.referral_credit("WALLETC1", FRIEND)
            self.assertTrue(r["ok"])
            w = s.wallet_state(REF)
            self.assertEqual(w["pending_usd"], 1.0)
            self.assertEqual(w["available_usd"], 0.0)
            # friend uses OraCool → their first chat
            out = s.referral_mark_used(FRIEND)
            self.assertTrue(out and out["referrer"] == REF)
            w = s.wallet_state(REF)
            self.assertEqual(w["available_usd"], 1.0)
            self.assertEqual(w["pending_usd"], 0.0)
            # runs once per account — no double credit
            s.referral_mark_used(FRIEND)
            w = s.wallet_state(REF)
            self.assertEqual(w["available_usd"], 1.0)

    def test_withdraw_minimum(self):
        g = self._local_only()
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6], g[7]:
            with mock.patch.object(s, "_wallet_table_insert", lambda row: True), \
                 mock.patch.object(s, "_wallet_table_rows", lambda e: []):
                s.touch_user(REF, wallet_available_cents=500)  # $5
                r = s.wallet_withdraw(REF, "bank", "0123456789 GTBank")
                self.assertIn("$10.00", r["error"])
                s.touch_user(REF, wallet_available_cents=1_500)  # $15
                r2 = s.wallet_withdraw(REF, "bank", "0123456789 GTBank")
                self.assertTrue(r2["ok"])
                self.assertEqual(r2["amount_usd"], 15.0)
                w = s.wallet_state(REF)
                self.assertEqual(w["available_usd"], 0.0)
                bad = s.wallet_withdraw(REF, "paypal", "x")
                self.assertIn("bank or crypto", bad["error"])

    def test_mark_paid(self):
        g = self._local_only()
        fake = {"id": 7, "email": REF, "amount_cents": 1500, "method": "bank",
                "destination": "0123456789 GTBank", "status": "requested", "created_at": "now"}
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6], g[7]:
            with mock.patch.object(s, "_wallet_table_open", lambda: [fake]), \
                 mock.patch.object(s, "_wallet_table_patch", lambda wid, f: True):
                r = s.wallet_mark_paid(7, "admin")
                self.assertTrue(r["ok"])
                self.assertEqual(r["amount_usd"], 15.0)
                fake["status"] = "paid"   # production filters open list by status
                self.assertEqual(s.wallet_mark_paid(7, "admin").get("error"),
                                 "That withdrawal request is not open (already paid?).")

    def test_chat_router_wallet(self):
        g = self._local_only()
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6], g[7]:
            out = s.auto_tools("how much money is in my wallet? can I withdraw?",
                               tier="free", email=REF)
            self.assertTrue(any(x["tool"] == "wallet" for x in out), out)

    def test_signup_signature_still_has_ref(self):
        import inspect
        self.assertIn("ref", inspect.signature(s.auth_signup).parameters)


if __name__ == "__main__":
    unittest.main()
