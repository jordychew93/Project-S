import unittest
from datetime import datetime, timezone

from trader import config, data, levels, strategy
from trader.indicators import atr, rsi, sma
from trader.portfolio import Portfolio


def make_bars(closes, start_day=1, spread=1.0, volume=1000.0):
    bars = []
    for i, c in enumerate(closes):
        day = start_day + i
        bars.append({"date": f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}", "open": c, "high": c + spread,
                     "low": c - spread, "close": c, "volume": volume})
    return bars


class IndicatorTests(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])

    def test_rsi_extremes(self):
        self.assertEqual(rsi(list(range(1, 30)), 14)[-1], 100.0)
        self.assertLess(rsi(list(range(30, 1, -1)), 14)[-1], 1.0)

    def test_atr_constant_range(self):
        bars = make_bars([100.0] * 30, spread=1.0)
        out = atr([b["high"] for b in bars], [b["low"] for b in bars], [b["close"] for b in bars], 14)
        self.assertAlmostEqual(out[-1], 2.0)


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self._saved = config.STRATEGY, config.ALLOW_SHORTS
        config.STRATEGY, config.ALLOW_SHORTS = "trend", False

    def tearDown(self):
        config.STRATEGY, config.ALLOW_SHORTS = self._saved

    def test_no_signal_in_downtrend(self):
        self.assertIsNone(strategy.evaluate("X", "stock", make_bars([200 - i for i in range(80)])))

    def test_breakout_in_uptrend(self):
        closes = [100 + i * 0.5 for i in range(60)] + [127.0, 130.0] * 9 + [127.0, 132.0]
        bars = make_bars(closes)
        bars[-1]["volume"] = 5000.0
        sig = strategy.evaluate("X", "stock", bars)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.setup, "breakout")

    def test_breakout_needs_volume(self):
        closes = [100 + i * 0.5 for i in range(60)] + [127.0, 130.0] * 9 + [127.0, 132.0]
        self.assertIsNone(strategy.evaluate("X", "stock", make_bars(closes)))


def range_bars(n=70, lo=100.0, hi=110.0, period=10):
    """Price swinging between a floor near `lo` and a ceiling near `hi`."""
    import math
    closes = [lo + (hi - lo) * (0.5 - 0.5 * math.cos(2 * math.pi * i / period)) for i in range(n)]
    return make_bars(closes, spread=0.5)


class LevelTests(unittest.TestCase):
    def test_finds_floor_and_ceiling(self):
        bars = range_bars()
        zones = levels.find_zones(bars, atr_value=2.0)
        self.assertEqual(len(zones), 2)
        self.assertAlmostEqual(zones[0].low, 99.5)
        self.assertAlmostEqual(zones[1].high, 110.5)
        self.assertGreaterEqual(zones[0].touches, 5)

    def test_nearest(self):
        zones = [levels.Zone(99, 100, 3, 0), levels.Zone(109, 110, 3, 0)]
        below, above = levels.nearest(zones, 105)
        self.assertEqual((below.low, above.low), (99, 109))


class SupportResistanceTests(unittest.TestCase):
    def setUp(self):
        self._saved = config.STRATEGY, config.ALLOW_SHORTS
        config.STRATEGY, config.ALLOW_SHORTS = "sr", True

    def tearDown(self):
        config.STRATEGY, config.ALLOW_SHORTS = self._saved

    def test_support_bounce_buy(self):
        bars = range_bars(n=70)
        bars = bars[:61]
        bars.append({"date": "2026-12-01", "open": 100.2, "high": 101.8, "low": 99.6, "close": 101.6, "volume": 1000})
        sig = strategy.evaluate("X", "stock", bars)
        self.assertIsNotNone(sig)
        self.assertEqual((sig.side, sig.setup), (1, "support_bounce"))
        self.assertLess(sig.stop, 99.5)
        self.assertLessEqual(sig.target, 110.5)
        self.assertGreaterEqual((sig.target - sig.close) / (sig.close - sig.stop), config.MIN_REWARD_RISK)

    def test_resistance_rejection_sell_mirrors_buy(self):
        bars = range_bars(n=70)[:66]
        bars.append({"date": "2026-12-01", "open": 109.8, "high": 110.4, "low": 108.2, "close": 108.4, "volume": 1000})
        sig = strategy.evaluate("X", "stock", bars)
        self.assertIsNotNone(sig)
        self.assertEqual((sig.side, sig.setup), (-1, "resistance_rejection"))
        self.assertGreater(sig.stop, 110.5)
        self.assertGreaterEqual(sig.target, 99.5)


class WatchlistTests(unittest.TestCase):
    def test_watch_note_ranks_by_atr_distance(self):
        w = strategy.watch_note(range_bars(n=70)[:67])
        self.assertIn("resistance", w["note"])
        self.assertGreaterEqual(w["atr_away"], 0)

    def test_stale_symbols(self):
        from trader.__main__ import stale_symbols
        u = {"A": ("fx", [{"date": "2026-09-29"}]), "B": ("fx", [{"date": "2026-09-25"}]),
             "C": ("stock", [{"date": "2026-09-25"}])}
        self.assertEqual(stale_symbols(u), [("B", "2026-09-25")])


class DataTests(unittest.TestCase):
    def test_parse_sorts_oldest_first(self):
        text = "timestamp,open,high,low,close\r\n2026-01-02,1,2,0.5,1.5\r\n2026-01-01,1,2,0.5,1.2\r\n"
        bars = data.parse_csv(text)
        self.assertEqual([b["date"] for b in bars], ["2026-01-01", "2026-01-02"])
        self.assertEqual(bars[0]["volume"], 0.0)

    def test_drop_incomplete(self):
        bars = [{"date": "2026-09-26"}, {"date": "2026-09-27"}]
        morning = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
        evening = datetime(2026, 9, 27, 22, tzinfo=timezone.utc)
        self.assertEqual(len(data.drop_incomplete(bars, "crypto", morning)), 1)
        self.assertEqual(len(data.drop_incomplete(bars, "stock", morning)), 1)
        self.assertEqual(len(data.drop_incomplete(bars, "stock", evening)), 2)


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.p = Portfolio(cash=100_000.0)
        self.p.pending["X"] = {"setup": "breakout", "atr": 2.0}

    def test_fill_sizes_by_risk_and_cap(self):
        self.p._fill_pending("X", "stock", {"date": "d1", "open": 100.0, "high": 101, "low": 99, "close": 100})
        pos = self.p.positions["X"]
        risk_units = 100_000 * config.RISK_PER_TRADE / (config.STOP_ATR * 2.0)
        cap_units = 100_000 * config.MAX_NOTIONAL_PCT["stock"] / pos["entry"]
        self.assertEqual(pos["units"], float(int(min(risk_units, cap_units))))
        self.assertAlmostEqual(pos["stop"], pos["entry"] - 4.0)

    def test_stop_exit_records_loss(self):
        self.p._fill_pending("X", "stock", {"date": "d1", "open": 100.0, "high": 101, "low": 99, "close": 100})
        bars = make_bars([100.0] * 60)
        bars[-1].update(open=99.0, low=90.0)
        self.p._manage_position("X", bars)
        self.assertNotIn("X", self.p.positions)
        self.assertLess(self.p.closed[0]["pnl"], 0)
        self.assertEqual(self.p.closed[0]["exit_reason"], "stop hit")


class ShortPositionTests(unittest.TestCase):
    def test_short_profit_and_cash(self):
        p = Portfolio(cash=100_000.0)
        p.pending["X"] = {"setup": "resistance_rejection", "atr": 2.0, "side": -1, "stop": 105.0, "target": 90.0}
        p._fill_pending("X", "stock", {"date": "d1", "open": 100.0, "high": 101, "low": 99, "close": 100})
        pos = p.positions["X"]
        self.assertEqual(pos["side"], -1)
        self.assertEqual(pos["stop"], 105.0)
        bars = make_bars([100.0] * 60)
        bars[-1].update(open=95.0, high=96.0, low=89.0, close=90.0)
        p._manage_position("X", bars)
        trade = p.closed[0]
        self.assertEqual(trade["exit_reason"], "target hit")
        self.assertGreater(trade["pnl"], 0)
        self.assertAlmostEqual(p.cash, 100_000.0 + trade["pnl"])


if __name__ == "__main__":
    unittest.main()
