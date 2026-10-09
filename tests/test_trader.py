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


# ----- moomoo paper bot (offline: every moomoo call is faked) --------------------------------------------
import os
import tempfile
from zoneinfo import ZoneInfo

from trader import broker_moomoo as bm
from trader import moomoo_bot

REAL_ACC_ID = 286260079670825227


class FakeTradeCtx:
    def __init__(self):
        self.calls = []

    def place_order(self, **kw):
        self.calls.append(("place_order", kw))
        return 0, [{"order_id": "FAKE1"}]

    def modify_order(self, *a, **kw):
        self.calls.append(("modify_order", a, kw))
        return 0, None

    def position_list_query(self, **kw):
        return 0, [{"code": "US.AAPL", "qty": 10.0, "position_side": "LONG"},
                   {"code": "US.XOM", "qty": 5.0, "position_side": "SHORT"},
                   {"code": "US.MSFT", "qty": 0.0, "position_side": "LONG"}]


class PaperGuardTests(unittest.TestCase):
    def test_market_order_goes_to_paper_account(self):
        ctx = FakeTradeCtx()
        self.assertEqual(bm.place_paper_order(ctx, "AAPL", 10, "BUY"), "FAKE1")
        kw = ctx.calls[0][1]
        self.assertEqual((kw["trd_env"], kw["acc_id"], kw["order_type"], kw["code"], kw["qty"]),
                         ("SIMULATE", bm.PAPER_ACC_ID, "MARKET", "US.AAPL", 10))

    def test_limit_day_order(self):
        ctx = FakeTradeCtx()
        bm.place_paper_order(ctx, "AAPL", 3, "SELL", price=250.5)
        kw = ctx.calls[0][1]
        self.assertEqual((kw["order_type"], kw["price"], kw["time_in_force"]), ("NORMAL", 250.5, "DAY"))

    def test_guard_rejects_real(self):
        ctx = FakeTradeCtx()
        with self.assertRaises(bm.NotPaperError):
            bm.place_paper_order(ctx, "AAPL", 10, "BUY", trd_env="REAL")
        with self.assertRaises(bm.NotPaperError):
            bm.place_paper_order(ctx, "AAPL", 10, "BUY", acc_id=REAL_ACC_ID)
        with self.assertRaises(bm.NotPaperError):
            bm.cancel_paper_order(ctx, "FAKE1", trd_env="REAL")
        self.assertEqual(ctx.calls, [])

    def test_bad_quantity_or_side_rejected(self):
        with self.assertRaises(ValueError):
            bm.place_paper_order(FakeTradeCtx(), "AAPL", 0, "BUY")
        with self.assertRaises(ValueError):
            bm.place_paper_order(FakeTradeCtx(), "AAPL", 1, "YOLO")

    def test_place_order_has_a_single_call_site(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trader")
        sites = []
        for name in os.listdir(root):
            if name.endswith(".py"):
                with open(os.path.join(root, name)) as f:
                    src = f.read()
                sites += [name] * src.count(".place_order(")
                self.assertNotIn("unlock_trade", src)
                self.assertNotIn(str(REAL_ACC_ID), src)
        self.assertEqual(sites, ["broker_moomoo.py"])

    def test_positions_are_signed(self):
        self.assertEqual(bm.paper_positions(FakeTradeCtx()), {"AAPL": 10.0, "XOM": -5.0})


class FakeFrame(list):
    def to_dict(self, _):
        return list(self)


class FakeQuoteCtx:
    def __init__(self, pages):
        self.pages = pages

    def request_history_kline(self, code, page_req_key=None, **kw):
        i = page_req_key or 0
        nxt = i + 1 if i + 1 < len(self.pages) else None
        return 0, FakeFrame(self.pages[i]), nxt


class MoomooBarTests(unittest.TestCase):
    rows = [{"time_key": "2026-10-08 00:00:00", "open": 336.815, "high": 341.57, "low": 335.9, "close": 340.42,
             "volume": 35332449.0},
            {"time_key": "2026-10-07 00:00:00", "open": 336.96, "high": 338.67, "low": 332.78, "close": 336.67,
             "volume": 34147860.0}]

    def test_kline_to_bars(self):
        bars = bm.kline_to_bars(self.rows)
        self.assertEqual([b["date"] for b in bars], ["2026-10-07", "2026-10-08"])
        self.assertEqual(set(bars[0]), {"date", "open", "high", "low", "close", "volume"})
        self.assertAlmostEqual(bars[1]["close"], 340.42)

    def test_csv_round_trip(self):
        bars = bm.kline_to_bars(self.rows)
        self.assertEqual(data.parse_csv(bm.bars_to_csv(bars)), [dict(b, open=round(b["open"], 4)) for b in bars])

    def test_fetch_pages_and_caches(self):
        import datetime as dt
        saved = bm.CACHE_DIR
        with tempfile.TemporaryDirectory() as tmp:
            bm.CACHE_DIR = tmp
            try:
                bars = bm.fetch_bars(FakeQuoteCtx([self.rows[:1], self.rows[1:]]), "AAPL", today=dt.date(2026, 10, 9))
                self.assertEqual(len(bars), 2)
                self.assertEqual(bm.load_cached_bars("AAPL"), bars)
            finally:
                bm.CACHE_DIR = saved


class FakeBroker:
    def __init__(self, open_price, when):
        self.open_price, self.when, self.held, self.placed = open_price, when, {}, []

    def snapshots(self, symbols):
        return {s: {"open": self.open_price, "last": self.open_price, "time": self.when} for s in symbols}

    def positions(self):
        return {s: q for s, q in self.held.items() if q}

    def place(self, sym, qty, side, price=None):
        self.placed.append((sym, qty, side))
        self.held[sym] = self.held.get(sym, 0) + (qty if side in ("BUY", "BUY_BACK") else -qty)
        return f"OID{len(self.placed)}"

    def order(self, oid):
        return {"order_status": "FILLED_ALL", "dealt_avg_price": self.open_price}


def bounce_universe():
    bars = range_bars(n=70)[:61]
    bars.append({"date": "2026-12-01", "open": 100.2, "high": 101.8, "low": 99.6, "close": 101.6, "volume": 1000})
    return {"X": ("stock", bars)}


class MoomooFlowTests(unittest.TestCase):
    ET = ZoneInfo("America/New_York")

    def setUp(self):
        self._saved = config.STRATEGY, config.ALLOW_SHORTS
        config.STRATEGY, config.ALLOW_SHORTS = "sr", True

    def tearDown(self):
        config.STRATEGY, config.ALLOW_SHORTS = self._saved

    def test_scan_execute_scan_exit(self):
        p, state, u = Portfolio(), {"exits": []}, bounce_universe()
        exits, cancelled = moomoo_bot.scan(p, state, u)
        self.assertEqual((exits, cancelled), ([], []))
        self.assertIn("X", p.pending)
        self.assertTrue(moomoo_bot.scan_summary(p, exits, cancelled, [], u)[1].startswith("📋"))

        # Execute 10 minutes after the open: one market BUY sized like the engine would at that open.
        broker = FakeBroker(101.7, "2026-12-02 09:40:00")
        expected_units, _ = moomoo_bot.plan_entry(p, "X", 101.7, "2026-12-02")
        lines, rows = moomoo_bot.execute(p, state, broker, datetime(2026, 12, 2, 9, 40, tzinfo=self.ET))
        self.assertEqual(broker.placed, [("X", expected_units, "BUY")])
        self.assertGreater(expected_units, 0)
        self.assertEqual(p.pending["X"]["broker_order_id"], "OID1")
        self.assertIn("agree", lines[-1])
        self.assertEqual(rows[0]["qty"], expected_units)

        # Running execute again sends nothing more.
        again, _ = moomoo_bot.execute(p, state, broker, datetime(2026, 12, 2, 9, 45, tzinfo=self.ET))
        self.assertEqual((len(broker.placed), again), (1, []))

        # Next scan books the fill with exactly the broker quantity.
        bars = u["X"][1]
        bars.append({"date": "2026-12-02", "open": 101.7, "high": 102.5, "low": 101.0, "close": 102.0, "volume": 1000})
        moomoo_bot.scan(p, state, u)
        self.assertEqual(p.positions["X"]["units"], expected_units)

        # The stop is hit: the scan queues an exit and execute sells exactly that quantity.
        bars.append({"date": "2026-12-03", "open": 101.0, "high": 101.2, "low": 95.0, "close": 96.0, "volume": 1000})
        exits, _ = moomoo_bot.scan(p, state, u)
        self.assertEqual([(e["symbol"], e["units"], e["reason"]) for e in exits], [("X", expected_units, "stop hit")])
        broker.when = "2026-12-04 09:40:00"
        lines, _ = moomoo_bot.execute(p, state, broker, datetime(2026, 12, 4, 9, 40, tzinfo=self.ET))
        self.assertEqual(broker.placed[-1], ("X", expected_units, "SELL"))
        self.assertEqual((state["exits"], broker.positions()), ([], {}))
        self.assertIn("agree", lines[-1])

    def test_market_closed_sends_nothing(self):
        p, state, u = Portfolio(), {"exits": []}, bounce_universe()
        moomoo_bot.scan(p, state, u)
        broker = FakeBroker(101.7, "2026-12-02 06:00:00")
        lines, _ = moomoo_bot.execute(p, state, broker, datetime(2026, 12, 2, 6, 0, tzinfo=self.ET))
        self.assertEqual(broker.placed, [])
        self.assertIn("closed", lines[0])

    def test_unsent_order_is_cancelled_at_next_scan(self):
        p, state, u = Portfolio(), {"exits": []}, bounce_universe()
        moomoo_bot.scan(p, state, u)
        u["X"][1].append({"date": "2026-12-02", "open": 101.7, "high": 102.5, "low": 101.0, "close": 102.0,
                          "volume": 1000})
        _, cancelled = moomoo_bot.scan(p, state, u)
        self.assertEqual(cancelled, ["X"])
        self.assertNotIn("X", p.positions)

    def test_rescan_same_day_keeps_pending(self):
        p, state, u = Portfolio(), {"exits": []}, bounce_universe()
        moomoo_bot.scan(p, state, u)
        _, cancelled = moomoo_bot.scan(p, state, u)
        self.assertEqual(cancelled, [])
        self.assertIn("X", p.pending)

    def test_exit_never_opens_a_new_position(self):
        p, state = Portfolio(), {"exits": [{"symbol": "X", "side": 1, "units": 10, "reason": "stop hit", "r": -1}]}
        broker = FakeBroker(100.0, "2026-12-02 09:40:00")
        lines, _ = moomoo_bot.execute(p, state, broker, datetime(2026, 12, 2, 9, 40, tzinfo=self.ET))
        self.assertEqual(broker.placed, [])
        self.assertIn("nothing held", " ".join(lines))

    def test_reconcile_reports_mismatch(self):
        self.assertEqual(moomoo_bot.reconcile({"A": 5, "B": -3}, {"A": 5, "C": 2}), [("B", -3, 0), ("C", 0, 2)])


if __name__ == "__main__":
    unittest.main()
