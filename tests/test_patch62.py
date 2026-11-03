"""patch62: real payouts — Paystack Transfers send actual naira for bank
withdrawals; crypto/manual queue retained; failed transfers refund the wallet."""
import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

ME = "realpay@example.com"


def _quiet():
    return mock.patch.object(s, "supabase_get_flag", lambda e: None), \
           mock.patch.object(s, "supabase_upsert_flag", lambda r, block_fields=True: True), \
           mock.patch.object(s, "audit_log", lambda *a, **k: None), \
           mock.patch.object(s, "notify_admins", lambda *a, **k: None), \
           mock.patch.object(s, "emit_event", lambda *a, **k: None), \
           mock.patch.object(s, "check_tier", lambda e: "free"), \
           mock.patch.object(s, "supabase_all_flags", lambda: {})


def _cleanup():
    try:
        users = s.load_users()
        if ME in users:
            del users[ME]
            s.save_users(users)
    except Exception:
        pass


class Marker(unittest.TestCase):
    def test_build(self):
        self.assertEqual(SRV.count('"patch63-any-currency"'), 2)

    def test_transfers_code(self):
        for needle in ("def paystack_initiate_transfer", "def paystack_verify_transfer",
                       "def wallet_verify_withdrawal", '"source": "balance"', "transfers_ready"):
            self.assertIn(needle, SRV, needle)

    def test_frontend_real(self):
        self.assertIn("REAL transfer sent via Paystack", APP)
        self.assertIn("data-verify", APP)
        self.assertIn("Verify at Paystack", APP)
        self.assertIn("This is real money, not points", APP)


class RealBank(unittest.TestCase):
    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_full_bank_roundtrip(self):
        g = _quiet()
        rows = []
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6]:
            with mock.patch.object(s, "_paystack_secret", lambda: "sk_test_x"), \
                 mock.patch.object(s, "_wallet_table_insert", lambda row: rows.append(dict(row, id=1)) or True), \
                 mock.patch.object(s, "_wallet_table_rows", lambda e: rows), \
                 mock.patch.object(s, "_wallet_table_open", lambda: rows), \
                 mock.patch.object(s, "_wallet_table_patch", lambda wid, f: rows[0].update(f) or True), \
                 mock.patch.object(s, "paystack_banks", lambda force=False: [
                     {"name": "GTBank", "code": "058"}, {"name": "Opay", "code": "999"}]), \
                 mock.patch.object(s, "paystack_create_recipient"), \
                 mock.patch.object(s, "paystack_initiate_transfer") as minit:
                s.touch_user(ME, wallet_available_cents=1_500)  # $15
                s.paystack_create_recipient.return_value = {"ok": True, "recipient_code": "RCP_x"}
                minit.side_effect = lambda r, k, reason="", currency="NGN": {
                    "ok": True, "transfer_code": "TRF_1", "status": "processing", "amount_ngn": k / 100.0}
                r = s.wallet_withdraw(ME, "bank", "0123456789 GTBank", "Test User")
                self.assertTrue(r["ok"], r)
                self.assertTrue(r["real"])
                self.assertEqual(r["transfer_code"], "TRF_1")
                self.assertEqual(r["ngn_amount"], r["local_amount"])   # live FX rate
                self.assertEqual(s.paystack_create_recipient.call_args[0][2], "058")
                self.assertEqual(minit.call_args[0][1], int(r["local_amount"] * 100))   # live FX
                self.assertEqual(rows[0]["status"], "processing")
                self.assertEqual(s.wallet_state(ME)["available_usd"], 0.0)
                # verify at paystack → success marks paid
                with mock.patch.object(s, "paystack_verify_transfer", lambda c: {"ok": True, "status": "success"}):
                    v = s.wallet_verify_withdrawal(rows[0]["id"])
                    self.assertEqual(v["status"], "success")
                self.assertEqual(rows[0]["status"], "paid")

    def test_failed_transfer_refunds(self):
        g = _quiet()
        rows = [{"id": 3, "email": ME, "amount_cents": 1200, "method": "bank", "destination": "x",
                 "status": "processing", "paystack_transfer_code": "TRF_9", "created_at": "now"}]
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6]:
            with mock.patch.object(s, "_wallet_table_insert", lambda row: True), \
                 mock.patch.object(s, "_wallet_table_rows", lambda e: []), \
                 mock.patch.object(s, "_wallet_table_open", lambda: rows), \
                 mock.patch.object(s, "_wallet_table_patch", lambda wid, f: rows[0].update(f) or True), \
                 mock.patch.object(s, "_paystack_secret", lambda: "sk_test_x"), \
                 mock.patch.object(s, "paystack_verify_transfer", lambda c: {"ok": True, "status": "failed"}):
                v = s.wallet_verify_withdrawal(3)
                self.assertTrue(v["refunded"])
                self.assertEqual(rows[0]["status"], "failed")

    def test_insufficient_paystack_balance_no_deduction(self):
        g = _quiet()
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6]:
            with mock.patch.object(s, "_paystack_secret", lambda: "sk_test_x"), \
                 mock.patch.object(s, "paystack_banks", lambda force=False: [{"name": "GTBank", "code": "058"}]), \
                 mock.patch.object(s, "paystack_create_recipient", lambda n, a, c: {"ok": True, "recipient_code": "R"}), \
                 mock.patch.object(s, "paystack_initiate_transfer",
                                   lambda r, k, reason="", currency="NGN": {"error": "Insufficient balance", "paystack": True}):
                s.touch_user(ME, wallet_available_cents=1_500)
                r = s.wallet_withdraw(ME, "bank", "0123456789 GTBank", "Test User")
                self.assertFalse(r["ok"])
                self.assertIn("Nothing was deducted", r["error"])
                self.assertEqual(s.wallet_state(ME)["available_usd"], 15.0)

    def test_no_key_falls_back_to_manual_queue(self):
        g = _quiet()
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6]:
            with mock.patch.object(s, "_paystack_secret", lambda: None), \
                 mock.patch.object(s, "_wallet_table_insert", lambda row: True), \
                 mock.patch.object(s, "_wallet_table_rows", lambda e: []):
                s.touch_user(ME, wallet_available_cents=1_500)
                r = s.wallet_withdraw(ME, "bank", "0123456789 GTBank")
                self.assertTrue(r["ok"])
                self.assertTrue(r.get("real") is None)

    def test_bank_resolution(self):
        banks = [{"name": "Guaranty Trust Bank", "code": "058"}, {"name": "Opay", "code": "999"}]
        with mock.patch.object(s, "paystack_banks", lambda force=False: banks):
            b, e = s._paystack_resolve_bank("gtbank")
            self.assertEqual(b["code"], "058")
            b, e = s._paystack_resolve_bank("opay")
            self.assertEqual(b["code"], "999")
            b, e = s._paystack_resolve_bank("notarealbank")
            self.assertIsNone(b)
            self.assertIn("Paystack", e)


if __name__ == "__main__":
    unittest.main()
