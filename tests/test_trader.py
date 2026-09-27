import unittest
from datetime import datetime, timezone

from trader import config, data, strategy
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


if __name__ == "__main__":
    unittest.main()
