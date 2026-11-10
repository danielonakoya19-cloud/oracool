# -*- coding: utf-8 -*-
"""patch80 — trade signals from the chat box: BUY / SELL / WAIT + confidence %.
market_signal wraps the live-data forecast engine: any market, any horizon
("in the next 4 mins"), entry/stop/target, reasons, honest WAIT."""
import os
import re
import unittest
import unittest.mock

import server


class TestPatch80TradeSignals(unittest.TestCase):
    UP = [100 * (1.004 ** i) for i in range(90)]
    DOWN = [100 * (0.996 ** i) for i in range(90)]
    FLAT = [100 + ((i * 7919) % 5 - 2) * 0.02 for i in range(90)]

    def setUp(self):
        self._sv = server._signal_series

    def tearDown(self):
        server._signal_series = self._sv

    def _feed(self, series):
        server._signal_series = lambda k, sym, mins: (series, [100.0] * 90, None, "test feed")

    def test_marker_patch80(self):
        src = open("server.py", encoding="utf-8").read()
        assert src.count("patch102-wa-bot") == 2
        assert "def market_signal(" in src and "SIGNAL_TRIGGER" in src

    def test_uptrend_gives_buy_with_confidence(self):
        self._feed(self.UP)
        r = server.market_signal("in the next 4 mins should i buy or sell gold")
        assert r["ok"] and r["signal"] == "BUY"
        assert isinstance(r["confidence_pct"], int) and 50 <= r["confidence_pct"] <= 92
        assert r["horizon_minutes"] == 4 and r["stop"] < r["entry"] < r["target"]
        assert r["expected_move_pct"] > 0

    def test_downtrend_gives_sell_with_levels(self):
        self._feed(self.DOWN)
        r = server.market_signal("should i sell bitcoin for the next hour")
        assert r["ok"] and r["signal"] == "SELL"
        assert r["horizon_minutes"] == 60 and r["stop"] > r["entry"] > r["target"]

    def test_flat_market_waits_honestly(self):
        self._feed(self.FLAT)
        r = server.market_signal("eurusd signal for tomorrow")
        assert r["ok"] and r["signal"] == "WAIT"
        assert r["stop"] is None and r["target"] is None
        assert "probability" in r["disclaimer"] and "WAIT" in r["disclaimer"]

    def test_horizon_parsing(self):
        for q, exp in (("gold signal", 240), ("signal for eurusd in 15 mins", 15),
                       ("should i buy gold today", 480), ("btc scalp", 5),
                       ("buy or sell eth in the next few minutes", 3),
                       ("gold in the next hour", 60), ("nasdaq in the next couple of hours", 120)):
            mins, lab = server._signal_horizon(q)
            assert mins == exp, (q, mins, exp)
            assert lab

    def test_no_market_is_an_honest_error(self):
        r = server.market_signal("should i buy the thingamajig")
        assert r["ok"] is False and r["saw_symbol"] is False
        assert "market" in r["error"].lower()

    def test_data_error_refuses_to_guess(self):
        def boom(k, sym, mins):
            raise ValueError("No history returned for that symbol.")
        server._signal_series = boom
        r = server.market_signal("should i buy or sell litecoin in 30 mins")
        assert r["ok"] is False and r["saw_symbol"] is True
        assert "guess" in r.get("disclaimer", "")

    def test_auto_tools_routes_signal_not_forecast(self):
        self._feed(self.UP)
        runs = server.auto_tools("in the next 4 mins should i buy or sell gold",
                                 tier="ultra", email="probe@example.com")
        tools = [x["tool"] for x in runs]
        assert "signal" in tools and "forecast" not in tools
        assert any("signal" in x["label"] for x in runs)

    def test_forecast_still_routes_analyze(self):
        self._feed(self.UP)
        runs = server.auto_tools("analyze the bitcoin market", tier="ultra", email="probe@example.com")
        tools = [x["tool"] for x in runs]
        assert "forecast" in tools and "signal" not in tools

    def test_no_phantom_market_on_unrelated_text(self):
        self._feed(self.UP)
        runs = server.auto_tools("should i buy a phone", tier="ultra", email="probe@example.com")
        assert not any(x["tool"] == "signal" for x in runs)

    def test_why_and_reasons_present(self):
        self._feed(self.UP)
        r = server.market_signal("give me a trade idea for bitcoin")
        assert r["ok"] and "WHY THIS SIGNAL:" in r["why"]
        assert r["reasons"][0]["factor"] == "Horizon"
        assert any(x.get("factor") for x in r["reasons"])
        assert "trader" in r["trader_info_note"].lower()

    def test_free_launch_guard_unchanged(self):
        with unittest.mock.patch.dict(os.environ, {"GROWTH_FREE_MODE": "1"}):
            assert server.free_mode() is True
            assert server.check_tier("x@y.z") == "ultra"
            assert server.coins_gate("image") is None
        with unittest.mock.patch.object(server, "free_mode", lambda: True):
            assert "FREE LAUNCH" in server.identity_prompt_head("a@b.c", "price?")


if __name__ == "__main__":
    unittest.main()
