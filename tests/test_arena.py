"""Tests for the strategy arena (virtual books only)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from trader import config
from trader.arena import book as bk
from trader.arena import market as mk
from trader.arena import playbook, runner, strategies

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARENA_DIR = os.path.join(ROOT, "trader", "arena")


def trading_dates(n, start="2024-01-01"):
    from datetime import date, timedelta
    d, out = date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def series(closes, dates, spread=1.0):
    return [{"date": d, "open": c, "high": c + spread, "low": c - spread, "close": c, "volume": 1e6}
            for d, c in zip(dates, closes)]


def synthetic_market(n=420):
    import math
    dates = trading_dates(n)
    bars = {}
    for k, s in enumerate(mk.SYMBOLS):
        drift = 0.0004 * (k % 5) - 0.0003
        closes = [100 * math.exp(drift * i) * (1 + 0.08 * math.sin(i / (6 + k))) for i in range(n)]
        bars[s] = series(closes, dates, spread=1.5)
    return mk.Market(bars)


class SafetyTests(unittest.TestCase):
    """The arena is a set of ledgers. It must never be able to place an order."""

    def arena_sources(self):
        for name in sorted(os.listdir(ARENA_DIR)):
            if name.endswith(".py"):
                with open(os.path.join(ARENA_DIR, name)) as f:
                    yield name, f.read()

    def test_arena_never_mentions_order_or_trade_apis(self):
        banned = ("place_order", "place_paper_order", "modify_order", "cancel_paper_order", "unlock_trade",
                  "broker_moomoo", "OpenSecTradeContext", "TrdEnv", "MoomooPaperBroker", "moomoo_bot")
        sources = list(self.arena_sources())
        self.assertGreaterEqual(len(sources), 5)
        for name, src in sources:
            for word in banned:
                self.assertNotIn(word, src, f"{name} mentions {word}")

    def test_running_the_arena_never_imports_the_broker(self):
        code = ("import sys\n"
                "sys.path.insert(0, %r)\n"
                "from tests.test_arena import synthetic_market\n"
                "from trader.arena import runner\n"
                "runner.run_backtest(synthetic_market(300), start='2024-06-01')\n"
                "bad = [m for m in sys.modules if m in ('trader.broker_moomoo', 'trader.moomoo_bot') "
                "or m == 'moomoo' or m.startswith('moomoo.')]\n"
                "print('BAD' if bad else 'CLEAN', bad)\n") % ROOT
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertIn("CLEAN", out.stdout, out.stdout + out.stderr)

    def test_forward_state_is_separate_from_the_bots(self):
        self.assertNotEqual(os.path.basename(runner.STATE), "portfolio.json")
        self.assertFalse(os.path.basename(runner.STATE).startswith("moomoo_"))


class BookTests(unittest.TestCase):
    def test_buy_fills_next_open_and_stop_exits(self):
        b = bk.Book()
        b.orders.append({"action": "buy", "symbol": "X", "pct": 0.2, "risk": 0.01, "stop_dist": 5.0})
        b.fill_orders("d1", {"X": {"date": "d1", "open": 100.0, "high": 101, "low": 99, "close": 100}})
        pos = b.positions["X"]
        self.assertEqual(pos["units"], float(int(min(100_000 * 0.2 / pos["entry"], 100_000 * 0.01 / 5.0))))
        self.assertAlmostEqual(pos["stop"], pos["entry"] - 5.0)
        b.check_stop("X", "d2", {"date": "d2", "open": 98.0, "high": 99, "low": 90, "close": 92})
        self.assertNotIn("X", b.positions)
        self.assertLess(b.closed[0]["pnl"], 0)
        self.assertAlmostEqual(b.closed[0]["r_multiple"], -1.0, delta=0.05)

    def test_sells_fill_before_buys(self):
        b = bk.Book()
        b.positions["A"] = {"units": 990.0, "entry": 100.0, "entry_date": "d0", "bars_held": 3, "stop": None, "risk": None}
        b.cash = 1_000.0
        b.orders = [{"action": "buy", "symbol": "B", "pct": 0.99}, {"action": "sell", "symbol": "A"}]
        bar = {"date": "d1", "open": 100.0, "high": 100, "low": 100, "close": 100}
        b.fill_orders("d1", {"A": bar, "B": bar})
        self.assertIn("B", b.positions)
        self.assertGreater(b.positions["B"]["units"], 900)

    def test_metrics(self):
        curve = [["2024-01-01", 100_000], ["2024-06-01", 120_000], ["2025-01-01", 90_000], ["2025-01-02", 110_000]]
        closed = [{"pnl": 300.0, "r_multiple": 1.5}, {"pnl": -100.0, "r_multiple": -0.5}, {"pnl": -100.0, "r_multiple": None}]
        m = bk.metrics(curve, closed, spy_return=0.05)
        self.assertAlmostEqual(m["total_return"], 0.10)
        self.assertAlmostEqual(m["vs_spy"], 0.05)
        self.assertAlmostEqual(m["max_drawdown"], 0.25)
        self.assertEqual(m["trades"], 3)
        self.assertAlmostEqual(m["win_rate"], 1 / 3)
        self.assertAlmostEqual(m["expectancy"], 100 / 3)
        self.assertAlmostEqual(m["profit_factor"], 1.5)
        self.assertAlmostEqual(m["avg_r"], 0.5)


class StrategyTests(unittest.TestCase):
    def test_breakout_buys_new_high_and_exits_on_low(self):
        dates = trading_dates(120)
        closes = [100.0] * 70 + [100 + 2 * i for i in range(1, 21)] + [140 - 4 * i for i in range(1, 31)]
        mkt = mk.Market({s: series(closes, dates) for s in mk.STOCKS})
        r = strategies.BreakoutRunner()
        for d in dates:
            r.step(d, mkt)
        self.assertTrue(r.closed)
        self.assertTrue(all(t["setup"] == "55-day breakout" for t in r.closed))
        self.assertGreater(r.closed[0]["entry_date"], dates[69])

    def test_meanrev_needs_200ma(self):
        dates = trading_dates(260)
        down = series([300 - i for i in range(260)], dates)         # below its 200-day average throughout
        r = strategies.MeanReversionRunner()
        mkt = mk.Market({s: down for s in mk.STOCKS})
        for d in dates:
            r.step(d, mkt)
        self.assertEqual(r.closed, [])
        self.assertEqual(r.book.positions, {})

    def test_meanrev_buys_dip_in_uptrend_and_sells_bounce(self):
        dates = trading_dates(240)
        closes = [100 + 0.5 * i for i in range(230)] + [210, 200, 195, 205, 215, 220, 222, 224, 226, 228]
        r = strategies.MeanReversionRunner()
        r.symbols = ["AAPL"]
        mkt = mk.Market({"AAPL": series(closes, dates)})
        for d in dates:
            r.step(d, mkt)
        self.assertEqual(len(r.closed), 1)
        self.assertGreater(r.closed[0]["pnl"], 0)
        self.assertEqual(r.closed[0]["exit_reason"], "RSI(2) above 70")

    def test_rotation_holds_leader_or_cash_etf(self):
        dates = trading_dates(200)
        mkt = mk.Market({
            "SPY": series([100 + 0.1 * i for i in range(200)], dates),
            "QQQ": series([100 + 0.3 * i for i in range(200)], dates),
            "GLD": series([100 - 0.1 * i for i in range(200)], dates),
            "TLT": series([100.0] * 200, dates),
            "BIL": series([100.0] * 200, dates),
        })
        r = strategies.RotationRunner()
        for d in dates:
            r.step(d, mkt)
        self.assertEqual(list(r.book.positions), ["QQQ"])
        falling = mk.Market({s: series([200 - 0.3 * i for i in range(200)], dates) for s in mk.ROTATION} |
                            {"BIL": series([100.0] * 200, dates)})
        r2 = strategies.RotationRunner()
        for d in dates:
            r2.step(d, falling)
        self.assertEqual(list(r2.book.positions), ["BIL"])

    def test_buy_hold_tracks_spy(self):
        dates = trading_dates(50)
        mkt = mk.Market({"SPY": series([100 + i for i in range(50)], dates)})
        r = strategies.BuyHoldSPYRunner()
        for d in dates:
            r.step(d, mkt)
        self.assertEqual(list(r.book.positions), ["SPY"])
        self.assertAlmostEqual(r.equity() / 100_000 - 1, 149 / 101 - 1, delta=0.01)


class PlaybookTests(unittest.TestCase):
    def setUp(self):
        self.order = {"setup": "breakout", "atr": 2.0, "side": 1, "date": "2024-01-01"}

    def test_all_flags_off_matches_project_t(self):
        mkt = synthetic_market(380)
        a, b = strategies.ProjectTRunner(), strategies.PlaybookRunner(flags=playbook.ALL_OFF)
        for d in mkt.dates()[100:]:
            a.step(d, mkt)
            b.step(d, mkt)
        self.assertEqual(a.curve, b.curve)
        self.assertEqual([t["pnl"] for t in a.closed], [t["pnl"] for t in b.closed])

    def test_cap_cash_reserve_and_first_tranche(self):
        p = playbook.PlaybookPortfolio(playbook.ALL_ON)
        p.pending["X"] = dict(self.order)
        p._fill_pending("X", "stock", {"date": "2024-01-02", "open": 100.0, "high": 101, "low": 99, "close": 100})
        pos = p.positions["X"]
        planned = int(min(100_000 * 0.01 / 4.0, 100_000 * 0.10 / pos["entry"]))
        self.assertEqual(pos["units"], float(int(planned / 3)))
        self.assertEqual(pos["tranches_left"], 2)

    def test_tranche_add_lowers_average_entry(self):
        p = playbook.PlaybookPortfolio({"tranches": 3})
        p.pending["X"] = dict(self.order)
        p._fill_pending("X", "stock", {"date": "2024-01-02", "open": 100.0, "high": 101, "low": 99, "close": 100})
        first = p.positions["X"]["units"]
        hist = series([100.0] * 59, trading_dates(59)) + [
            {"date": "2024-03-25", "open": 99.5, "high": 99.8, "low": 98.0, "close": 99.0, "volume": 1e6}]
        p._manage_position("X", hist)
        pos = p.positions["X"]
        self.assertEqual(pos["units"], 2 * first)
        self.assertLess(pos["entry"], 100.06)

    def test_half_off_at_two_r(self):
        p = playbook.PlaybookPortfolio({"partial_tp_r": 2.0, "partial_tp_pct": 0.25})
        p.pending["X"] = dict(self.order)
        p._fill_pending("X", "stock", {"date": "2024-01-02", "open": 100.0, "high": 101, "low": 99, "close": 100})
        units = p.positions["X"]["units"]
        hist = series([100.0] * 59, trading_dates(59)) + [
            {"date": "2024-03-25", "open": 101.0, "high": 110.0, "low": 100.5, "close": 109.0, "volume": 1e6}]
        p._manage_position("X", hist)
        pos = p.positions["X"]
        self.assertEqual(pos["units"], units - float(int(units / 2)))
        self.assertTrue(pos["partial_done"])
        self.assertGreater(pos["realized"], 0)

    def test_risk_off_halves_risk_and_trend_gate_blocks(self):
        dates = trading_dates(260)
        falling = series([300 - 0.5 * i for i in range(260)], dates)
        p = playbook.PlaybookPortfolio({"regime_filter": True, "trend_gate_200ma": True})
        p._universe = {"SPY": ("stock", falling), "X": ("stock", falling)}
        self.assertTrue(p.risk_off(dates[-1]))
        from trader.strategy import Signal
        p._queue_orders(dates[-1], [Signal("X", "stock", dates[-1], "breakout", 170.5, 2.0, 1.0, "test")])
        self.assertNotIn("X", p.pending)                      # long below the 200-day: blocked
        p.flags["trend_gate_200ma"] = False
        p._queue_orders(dates[-1], [Signal("X", "stock", dates[-1], "breakout", 170.5, 2.0, 1.0, "test")])
        self.assertEqual(p.pending["X"]["risk_mult"], 0.5)

    def test_earnings_blackout(self):
        p = playbook.PlaybookPortfolio({"earnings_blackout_days": 3}, earnings={"X": ["2024-01-10"]})
        self.assertTrue(p.earnings_soon("X", "2024-01-05"))    # Fri -> Wed: 3 trading days
        self.assertFalse(p.earnings_soon("X", "2024-01-04"))   # 4 trading days
        self.assertFalse(p.earnings_soon("Y", "2024-01-05"))


class ForwardTests(unittest.TestCase):
    def test_update_is_idempotent_and_resumes(self):
        full = synthetic_market(420)
        dates = full.dates()
        cut = mk.Market({s: [b for b in bars if b["date"] <= dates[400]] for s, bars in full.bars.items()})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "arena.json")
            self.assertEqual(runner.update_forward(cut, path), [dates[400]])
            self.assertEqual(runner.update_forward(cut, path), [])          # same data again: nothing happens
            with open(path) as f:
                once = json.load(f)["books"]
            self.assertEqual(runner.update_forward(full, path), dates[401:])
            with open(path) as f:
                st = json.load(f)
            self.assertEqual(st["start"], dates[400])
            self.assertEqual(st["last_date"], dates[-1])
            self.assertEqual(len(st["books"]["spy"]["curve"]), 20)
            self.assertEqual(once["baseline"]["curve"][0], st["books"]["baseline"]["curve"][0])

    def test_forward_matches_one_shot_backtest(self):
        mkt = synthetic_market(380)
        dates = mkt.dates()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "arena.json")
            for i in range(300, 380, 7):          # update in irregular chunks
                part = mk.Market({s: [b for b in bars if b["date"] <= dates[i]] for s, bars in mkt.bars.items()})
                runner.update_forward(part, path)
            runner.update_forward(mkt, path)
            runners, _ = runner.load_forward(path)
        one_shot = runner.run_backtest(mkt, start=dates[300])
        for r in one_shot:
            self.assertEqual(r.curve, runners[r.key].curve, r.key)

    def test_promotion_rule(self):
        def card(trades, exp, dd):
            return {"trades": trades, "expectancy": exp, "max_drawdown": dd}
        bt = {"cards": {"baseline": card(40, 50, 0.15), "breakout": card(35, 80, 0.10), "meanrev": card(50, 90, 0.18)},
              "verdicts": {"breakout": {"survived": True}, "meanrev": {"survived": True}}}
        fwd = {"baseline": card(31, 10, 0.05), "breakout": card(30, 20, 0.04), "meanrev": card(29, 99, 0.01)}
        self.assertEqual(runner.promotion_candidates(bt, fwd), ["breakout"])
        fwd["breakout"] = card(30, 5, 0.04)
        self.assertEqual(runner.promotion_candidates(bt, fwd), [])

    def test_stage1_verdict(self):
        spy = {"total_return": 0.5}
        self.assertEqual(runner.stage1_verdict("x", {"total_return": 0.6, "max_drawdown": 0.1}, spy), (True, []))
        ok, why = runner.stage1_verdict("x", {"total_return": 0.4, "max_drawdown": 0.3}, spy)
        self.assertFalse(ok)
        self.assertEqual(why, ["trailed SPY", "drawdown over 20%"])

    def test_leaderboard_format(self):
        mkt = synthetic_market(330)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "arena.json")
            part = mk.Market({s: bars[:300] for s, bars in mkt.bars.items()})
            runner.update_forward(part, path)
            runner.update_forward(mkt, path)
            runners, st = runner.load_forward(path)
        msg = runner.leaderboard(st, None, runners)
        self.assertIn("virtual", msg.lower())
        self.assertIn("Paper only", msg)
        self.assertNotIn("|", msg)                       # no tables
        sections = msg.split("\n\n")
        self.assertEqual(len(sections[0].splitlines()), 1)   # one-line headline
        for sec in sections[1:]:
            head, *bullets = sec.splitlines()
            self.assertRegex(head, r"^\S+ (\*\*.+\*\*|Paper only)")
            self.assertLessEqual(len(bullets), 4)
        for word in ("green", "red", "yellow", "orange"):
            self.assertNotIn(word, msg.lower())


class UITests(unittest.TestCase):
    def test_health_and_death_rule(self):
        from trader.arena import ui
        start = config.STARTING_CASH
        # new high = 100; 10% off the peak = 50; 20% off the peak = dead, and death is permanent
        eq = [start, start * 1.25, start * 1.125, start * 1.0, start * 1.3, start * 1.4]
        health, dds, dead = ui.health_track(eq)
        self.assertEqual(health[:3], [100, 100, 50])
        self.assertAlmostEqual(dds[2], 0.10, places=4)
        self.assertEqual(dead, 3)                         # 125k -> 100k is exactly a 20% fall
        self.assertEqual(health[3:], [0, 0, 0])           # recovering money does not revive it
        # a fall of 19.9% hurts badly but is survivable
        health, _, dead = ui.health_track([start, start * 0.801, start * 0.9])
        self.assertIsNone(dead)
        self.assertLessEqual(health[1], 1)
        # money down to half the start kills even with a custom, looser drawdown limit
        _, _, dead = ui.health_track([start, start * 0.5], dead_dd=0.9)
        self.assertEqual(dead, 1)
        # the peak never starts below the starting cash: an early loss counts from $100k
        health, _, dead = ui.health_track([start * 0.9])
        self.assertEqual((health, dead), ([50], None))

    def test_ui_page_builds_from_arena_files_only(self):
        from trader.arena import ui
        mkt = synthetic_market(330)
        with tempfile.TemporaryDirectory() as tmp:
            rs = runner.run_backtest(mkt, start="2024-06-01")
            curves = os.path.join(tmp, "curves.json")
            runner.write_curves(rs, curves)
            state = os.path.join(tmp, "arena.json")
            runner.update_forward(mkt, state)
            data = ui.build_data(curves_path=curves, backtest_path=os.path.join(tmp, "none.json"), state_path=state)
            html = ui.render(data)
        self.assertEqual(set(data["backtest"]["books"]), set(strategies.KEYS))
        n = len(data["backtest"]["dates"])
        for b in data["backtest"]["books"].values():
            self.assertEqual(len(b["equity"]), n)
            self.assertEqual(len(b["health"]), n)
        self.assertEqual(len(data["forward"]["dates"]), 1)
        self.assertNotIn(ui.PLACEHOLDER, html)
        self.assertIn("Paper / virtual money", html)


if __name__ == "__main__":
    unittest.main()
