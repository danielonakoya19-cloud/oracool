# -*- coding: utf-8 -*-
"""patch81 — LIVE price feed for trade signals across every market.
Short horizons get real 1m/5m candles (CoinGecko ticks / Yahoo intraday),
timestamped output, honest coarse fallback, no invented prices."""
import os
import re
import time
import unittest
import unittest.mock

import server


class TestPatch81LiveFeed(unittest.TestCase):
    UP = [100 * (1.004 ** i) for i in range(90)]
    DOWN = [100 * (0.996 ** i) for i in range(90)]
    FLAT = [100 + ((i * 7919) % 5 - 2) * 0.02 for i in range(90)]
    TS = [int(time.time() * 1000) - (89 - i) * 60000 for i in range(90)]

    def setUp(self):
        self._sv = server._signal_series
        self._fi = server._intraday_series
        self._ff = server._forecast_series

    def tearDown(self):
        server._signal_series = self._sv
        server._intraday_series = self._fi
        server._forecast_series = self._ff

    def _live(self, series):
        server._signal_series = lambda k, sym, mins: (series, [100.0] * 90, self.TS, "test live feed")

    def test_marker_patch81(self):
        src = open("server.py", encoding="utf-8").read()
        assert src.count("patch99-viz-clean") == 2
        assert "def _intraday_series(" in src and "def _signal_series(" in src
        assert "asof" in src and "Yahoo uses epoch seconds" in src

    def test_live_uptrend_buy_with_timestamp(self):
        self._live(self.UP)
        r = server.market_signal("in the next 4 mins should i buy or sell gold")
        assert r["ok"] and r["signal"] == "BUY"
        assert r["live"] is True and r["bar"] == "1-minute bars"
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$", r["asof"])
        assert r["stop"] < r["entry"] < r["target"]

    def test_live_downtrend_sell(self):
        self._live(self.DOWN)
        r = server.market_signal("should i sell bitcoin for the next hour")
        assert r["ok"] and r["signal"] == "SELL" and r["stop"] > r["entry"] > r["target"]

    def test_flat_waits_honestly(self):
        self._live(self.FLAT)
        r = server.market_signal("eurusd signal for tomorrow")
        assert r["ok"] and r["signal"] == "WAIT" and r["stop"] is None
        assert "probability" in r["disclaimer"] and "WAIT" in r["disclaimer"]

    def test_series_selection_short_vs_long_horizon(self):
        calls = []
        def fake_intra(k, sym, mins):
            calls.append(("intra", mins)); return (self.UP, [], self.TS, "Yahoo live (1m candles)")
        def fake_daily(k, sym):
            calls.append(("daily", None)); return (self.UP, [], "CoinGecko (90d daily)")
        server._intraday_series, server._forecast_series = fake_intra, fake_daily
        got = server._signal_series("fx", "xauusd", 5)
        assert got[3].startswith("Yahoo live") and calls == [("intra", 5)]
        calls.clear()
        got = server._signal_series("fx", "xauusd", 10080)
        assert calls == [("daily", None)] and got[3] == "CoinGecko (90d daily)"

    def test_daily_fallback_is_labelled_coarse(self):
        def boom(k, sym, mins):
            raise ValueError("no intraday bars")
        server._intraday_series = boom
        server._forecast_series = lambda k, sym: (self.UP, [], "Yahoo Finance (90d daily)")
        r = server.market_signal("buy or sell gold in 30 mins")
        assert r["ok"] and r["live"] is False and r["bar"] == "daily bars"
        assert "coarser" in r["reasons"][0]["reading"]

    def test_refuses_to_guess_without_data(self):
        def boom(k, sym, mins):
            raise ValueError("No history returned for that symbol.")
        server._signal_series = boom
        r = server.market_signal("should i buy or sell litecoin in 30 mins")
        assert r["ok"] is False and r["saw_symbol"] is True
        assert "guess" in r.get("disclaimer", "")

    def test_levels_scale_with_horizon(self):
        self._live(self.UP)
        r5 = server.market_signal("buy or sell gold in 5 mins")
        r8h = server.market_signal("buy or sell gold today")
        d5 = r5["entry"] - r5["stop"]
        d8h = r8h["entry"] - r8h["stop"]
        assert d8h > d5 * 3, (d5, d8h)

    def test_yahoo_sparse_pairs_and_epoch_seconds(self):
        import json
        now_s = int(time.time())
        ts_raw = [now_s - (99 - i) * 300 for i in range(100)]      # epoch SECONDS, 5m grid
        c_raw = [None] * 100
        v_raw = [None] * 100
        for i in range(30, 100):                                    # only 70 real bars
            c_raw[i] = 100.0 + i
            v_raw[i] = 5.0
        payload = {"chart": {"result": [{"timestamp": ts_raw,
                                         "indicators": {"quote": [{"close": c_raw, "volume": v_raw}]}}]}}
        def fake_fetch(url, headers=None, timeout=None):
            return (200, json.dumps(payload).encode(), {})
        with unittest.mock.patch.object(server, "http_fetch", fake_fetch):
            closes, vols, ts, src = server._intraday_series("fx", "usdngn", 480)
        assert len(closes) == 70 and len(ts) == 70 and all(t > 10 ** 12 for t in ts)
        assert closes[0] == 130.0 and "5m candles" in src

    def test_auto_tools_live_label_and_asof(self):
        self._live(self.UP)
        runs = server.auto_tools("in the next 4 mins should i buy or sell gold",
                                 tier="ultra", email="probe@example.com")
        sig = [x for x in runs if x["tool"] == "signal"]
        assert sig and "live" in sig[0]["label"]
        assert "asof" in sig[0]["result"]

    def test_prompt_forbids_denying_the_feed(self):
        h = open("index.html", encoding="utf-8").read()
        assert "NEVER claim you lack access to real-time or live data" in h
        assert "LIVE, timestamped price feed for EVERY market" in h
        assert "never say you lack market access" in h

    def test_free_launch_guard_unchanged(self):
        with unittest.mock.patch.dict(os.environ, {"GROWTH_FREE_MODE": "1"}):
            assert server.free_mode() is True
            assert server.check_tier("x@y.z") == "ultra"
            assert server.coins_gate("image") is None


if __name__ == "__main__":
    unittest.main()
