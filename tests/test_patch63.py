"""patch63: payouts in any currency — live FX, Paystack NGN/GHS/ZAR rails,
international transfer queue for 160+ others."""
import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

ME = "globalpay@example.com"
RATES = {"USD": 1.0, "NGN": 1600.0, "GHS": 15.5, "ZAR": 18.0, "KES": 129.0, "EUR": 0.9, "JPY": 150.0}


def _quiet():
    return mock.patch.object(s, "supabase_get_flag", lambda e: None), \
           mock.patch.object(s, "supabase_upsert_flag", lambda r, block_fields=True: True), \
           mock.patch.object(s, "audit_log", lambda *a, **k: None), \
           mock.patch.object(s, "notify_admins", lambda *a, **k: None), \
           mock.patch.object(s, "emit_event", lambda *a, **k: None), \
           mock.patch.object(s, "check_tier", lambda e: "free"), \
           mock.patch.object(s, "supabase_all_flags", lambda: {})


def _fx():
    s._FX_CACHE["rows"] = dict(RATES)
    s._FX_CACHE["at"] = __import__("time").time()


def _cleanup():
    try:
        users = s.load_users()
        if ME in users:
            del users[ME]
            s.save_users(users)
    except Exception:
        pass
    s._FX_CACHE["rows"] = {}
    s._FX_CACHE["at"] = 0.0


class Marker(unittest.TestCase):
    def test_build(self):
        self.assertEqual(SRV.count('"patch63-any-currency"'), 2)

    def test_fx_engine(self):
        for needle in ("open.er-api.com/v6/latest/USD", "def fx_convert", "FX_ZERO_DECIMAL", "paystack_ghs_recipient"):
            self.assertIn(needle, SRV, needle)

    def test_frontend_currency(self):
        self.assertIn("Your currency for the payout", APP)
        self.assertIn("Mobile-money network + number", APP)
        self.assertIn("any currency", APP)


class Fx(unittest.TestCase):
    def test_convert(self):
        _fx()
        major, rate = s.fx_convert(10.0, "NGN")
        self.assertEqual((major, rate), (16000.0, 1600.0))
        major, rate = s.fx_convert(10.0, "jpy")   # zero-decimal
        self.assertEqual(major, 1500)
        self.assertIsNone(s.fx_convert(10, "ZZZ")[0])

    def test_live_fetch(self):
        fake = {"rates": {"NGN": 1500.0, "GHS": 15.0}}
        with mock.patch.object(s, "http_fetch", return_value=(200, __import__("json").dumps(fake), None)):
            s._FX_CACHE["rows"] = {}
            s._FX_CACHE["at"] = 0.0
            rows = s.fx_rates()
            self.assertEqual(rows["NGN"], 1500.0)


class Rails(unittest.TestCase):
    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def _paystack(self):
        return mock.patch.object(s, "_paystack_secret", lambda: "sk_x"), \
               mock.patch.object(s, "paystack_banks", lambda force=False: [{"name": "GTBank", "code": "058"}]), \
               mock.patch.object(s, "paystack_create_recipient", lambda n, a, c: {"ok": True, "recipient_code": "R"})  # noqa: E501

    def test_ngn_instant(self):
        g = _quiet()
        _fx()
        rows = []
        p = self._paystack()
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6]:
            with p[0], p[1], p[2], \
                 mock.patch.object(s, "_wallet_table_insert", lambda row: rows.append(dict(row, id=1)) or True), \
                 mock.patch.object(s, "_wallet_table_rows", lambda e: rows), \
                 mock.patch.object(s, "paystack_initiate_transfer",
                                   lambda r, k, reason="", currency="NGN": {"ok": True, "transfer_code": "T1",
                                                                            "status": "processing", "amount_ngn": k / 100.0}):
                s.touch_user(ME, wallet_available_cents=1_000)
                r = s.wallet_withdraw(ME, "bank", "0123456789 GTBank", "Test", "NGN")
                self.assertTrue(r["ok"], r)
                self.assertEqual(r["currency"], "NGN")
                self.assertEqual(r["local_amount"], 16000.0)
                self.assertEqual(r["fx_rate"], 1600.0)

    def test_ghs_momo(self):
        g = _quiet()
        _fx()
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6]:
            with mock.patch.object(s, "_paystack_secret", lambda: "sk_x"), \
                 mock.patch.object(s, "paystack_ghs_recipient") as gr, \
                 mock.patch.object(s, "_wallet_table_insert", lambda row: True), \
                 mock.patch.object(s, "_wallet_table_rows", lambda e: []), \
                 mock.patch.object(s, "paystack_initiate_transfer",
                                   lambda r, k, reason="", currency="GHS": {"ok": True, "transfer_code": "T2",
                                                                            "status": "processing", "amount_ngn": k / 100.0}):
                s.touch_user(ME, wallet_available_cents=2_000)
                gr.return_value = {"ok": True, "recipient_code": "RG"}
                r = s.wallet_withdraw(ME, "bank", "MTN 0241234567", "Kofi", "GHS")
                self.assertTrue(r["ok"], r)
                self.assertEqual(r["local_amount"], 310.0)   # 20 USD × 15.5
                self.assertEqual(gr.call_args[0][1].lower(), "mtn")

    def test_international_manual(self):
        g = _quiet()
        _fx()
        rows = []
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6]:
            with mock.patch.object(s, "_paystack_secret", lambda: "sk_x"), \
                 mock.patch.object(s, "_wallet_table_insert", lambda row: rows.append(dict(row, id=9)) or True), \
                 mock.patch.object(s, "_wallet_table_rows", lambda e: rows):
                s.touch_user(ME, wallet_available_cents=1_500)
                r = s.wallet_withdraw(ME, "bank", "IBAN DE89 3704 0044 · Deutsche Bank", "Anna", "EUR")
                self.assertTrue(r["ok"], r)
                self.assertEqual(r["currency"], "EUR")
                self.assertEqual(r["local_amount"], 13.5)
                self.assertEqual(rows[0]["method"], "bank (international)")
                self.assertIn("48 hours", r["note"])

    def test_unknown_currency_rate_guard(self):
        g = _quiet()
        with g[0], g[1], g[2], g[3], g[4], g[5], g[6]:
            with mock.patch.object(s, "_paystack_secret", lambda: "sk_x"), \
                 mock.patch.object(s, "fx_rates", lambda: {"NGN": 1600.0}):
                s.touch_user(ME, wallet_available_cents=1_500)
                r = s.wallet_withdraw(ME, "bank", "somewhere", "Bo", "XYZ")
                self.assertIn("exchange rate", r["error"])
                self.assertEqual(s.wallet_state(ME)["available_usd"], 15.0)


if __name__ == "__main__":
    unittest.main()
