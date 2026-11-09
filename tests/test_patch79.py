"""patch79: composer attachment previews (paste stays where you type), plus-sign
attach button, and chat-box market analysis with 7-day forecasts + reasons."""
import os, sys, unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()

UP = [100.0 + i * 0.8 for i in range(90)]                 # steady uptrend
DOWN = [200.0 - i * 0.7 for i in range(90)]               # steady downtrend


def fake_series(kind, sym):
    closes = UP if sym in ("bitcoin", "eurusd", "aapl") else DOWN
    return (closes, [1000.0] * len(closes), "test data")


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count("patch97-easy-link"), 2)


class ForecastEngine(unittest.TestCase):
    def test_uptrend_is_bullish_with_reasons(self):
        with unittest.mock.patch.object(s, "_forecast_series", fake_series):
            r = s.market_forecast("analyze the bitcoin market for me")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["direction"], "Bullish")
        self.assertGreater(r["confidence_pct"], 50)
        lo, hi = r["expected_range"]
        self.assertLess(lo, hi)
        self.assertTrue(lo < r["last_price"] * 1.6)
        self.assertGreaterEqual(len(r["reasons"]), 5)
        self.assertTrue(r["why"].startswith("WHY:"))
        self.assertIn("never a guarantee", r["disclaimer"])
        self.assertEqual(r["forecast_horizon"], "7 days")

    def test_downtrend_is_bearish(self):
        with unittest.mock.patch.object(s, "_forecast_series", fake_series):
            r = s.market_forecast("predict tether market")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["direction"], "Bearish")

    def test_symbol_resolution_fx_stock_unknown(self):
        self.assertEqual(s._fx_symbol("will eurusd go up"), "eurusd")
        self.assertEqual(s._fx_symbol("gold outlook"), "xauusd")
        self.assertEqual(s._fx_symbol("what about USD/NGN"), "usdngn")
        self.assertIsNone(s._fx_symbol("analyze the weather"))
        self.assertEqual(s._stock_symbol("predict $nvda"), "NVDA")
        with unittest.mock.patch.object(s, "_crypto_coin", lambda t: None), \
             unittest.mock.patch.object(s, "_fx_symbol", lambda t: None), \
             unittest.mock.patch.object(s, "_stock_symbol", lambda t: None):
            r = s.market_forecast("analyze my kitchen")
            self.assertIn("error", r)
            self.assertIn("Name a market", r["error"])

    def test_auto_tools_runs_forecast_from_chat(self):
        runs = s.auto_tools("can you predict btc market for me", tier="ultra", email="m@example.com")
        self.assertTrue(any(t["tool"] == "forecast" for t in runs), runs)
        hits = [t for t in runs if t["tool"] == "forecast"]
        self.assertIn("forecast ·", hits[0]["label"])

    def test_trigger_regex_scope(self):
        for yes in ("analyze BTC", "market prediction for eth", "should i buy gold",
                    "where is nasdaq going", "bullish or bearish on eurusd?"):
            self.assertTrue(s.FORECAST_TRIGGER.search(yes), yes)
        self.assertFalse(s.FORECAST_TRIGGER.search("buy me a phone"))


class Composer(unittest.TestCase):
    def test_thumbnails_in_composer_not_chat(self):
        self.assertIn("function renderFileChips(){", APP)
        self.assertIn('width:56px;height:56px;object-fit:cover', APP)
        self.assertIn("attachNote=", APP)
        self.assertIn("👁 I can see it:", APP)          # vision caption AT the composer
        self.assertNotIn("addSystemNote('<img src=\"'+dataURL", APP)   # image note gone from thread
        self.assertNotIn("Pasted", APP)                 # no paste note flies up
        self.assertIn("Type your message and send.", APP)

    def test_plus_button(self):
        m = APP[APP.index('id="attachBtn"') - 60: APP.index('id="attachBtn"') + 180]
        self.assertIn("＋", m)
        self.assertIn("paste a screenshot", m)

    def test_history_and_gallery_intact(self):
        self.assertIn("function renderUserRich(box, content)", APP)   # patch78 survives
        self.assertEqual(APP.count("renderUserRich(b, m.content)"), 2)
        self.assertIn("m.kind==='audio'?`<audio src=", APP)
        self.assertIn("MARKETS & FORECASTS:", APP)
        self.assertIn("the REASONS WHY", APP)


class FreeLaunch(unittest.TestCase):
    def test_free_mode_guard_intact(self):
        with unittest.mock.patch.object(s, "free_mode", lambda: True):
            self.assertIn("FREE LAUNCH", s.identity_prompt_head("a@b.c", "price?"))


if __name__ == "__main__":
    unittest.main()
