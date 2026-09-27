"""Paper portfolio: bar-by-bar engine that fills orders, manages stops and records trades.

Signals are generated on a bar's close and filled at the next bar's open, so nothing
trades on information it couldn't have had.
"""

import csv
import json
import os
from dataclasses import asdict

from . import config, strategy


class Portfolio:
    def __init__(self, cash=config.STARTING_CASH):
        self.cash = cash
        self.positions = {}       # symbol -> position dict
        self.pending = {}         # symbol -> order dict, filled at next open
        self.last_seen = {}       # symbol -> last processed bar date
        self.last_price = {}      # symbol -> last close
        self.closed = []          # closed trade records
        self.events = []          # human-readable log of this run
        self.watch = {}           # symbol -> note on what would trigger a buy (latest bar only)
        self.scanned = 0          # bars processed this run

    # ----- persistence -------------------------------------------------
    @classmethod
    def load(cls, path):
        p = cls()
        if os.path.exists(path):
            with open(path) as f:
                state = json.load(f)
            p.cash = state["cash"]
            p.positions = state["positions"]
            p.pending = state["pending"]
            p.last_seen = state["last_seen"]
            p.last_price = state["last_price"]
        return p

    def save(self, path):
        state = {k: getattr(self, k) for k in ("cash", "positions", "pending", "last_seen", "last_price")}
        state["equity"] = round(self.equity(), 2)
        with open(path, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)

    def append_trades(self, path):
        if not self.closed:
            return
        new_file = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(self.closed[0].keys()))
            if new_file:
                w.writeheader()
            w.writerows(self.closed)

    # ----- accounting --------------------------------------------------
    def equity(self):
        return self.cash + sum(p["units"] * (p["entry"] + p.get("side", 1) * (self.last_price.get(s, p["entry"]) - p["entry"]))
                               for s, p in self.positions.items())

    def _log(self, date, msg):
        self.events.append(f"{date}  {msg}")

    # ----- engine ------------------------------------------------------
    def run(self, universe, start_fresh_at_latest=True):
        """Process every unseen bar for every symbol in date order.

        `universe` maps symbol -> (asset_class, bars oldest-first). A symbol seen for the
        first time starts at its latest bar when `start_fresh_at_latest` (live mode), or
        at its first bar otherwise (backtest mode).
        """
        index = {}
        for sym, (_, bars) in universe.items():
            if sym not in self.last_seen:
                self.last_seen[sym] = bars[-2]["date"] if start_fresh_at_latest and len(bars) > 1 else ""
            index[sym] = {b["date"]: i for i, b in enumerate(bars)}
        dates = sorted({b["date"] for _, bars in universe.values() for b in bars})

        for date in dates:
            signals = []
            for sym, (asset_class, bars) in universe.items():
                i = index[sym].get(date)
                if i is None or date <= self.last_seen[sym]:
                    continue
                history = bars[:i + 1]
                self._fill_pending(sym, asset_class, bars[i])
                self._manage_position(sym, history)
                self.last_price[sym] = bars[i]["close"]
                self.last_seen[sym] = date
                self.scanned += 1
                if sym not in self.positions and sym not in self.pending:
                    sig = strategy.evaluate(sym, asset_class, history)
                    if sig:
                        signals.append(sig)
                    elif i == len(bars) - 1:
                        note = strategy.watch_note(history)
                        if note:
                            self.watch[sym] = note
            self._queue_orders(date, signals)

    def _queue_orders(self, date, signals):
        slots = config.MAX_OPEN_POSITIONS - len(self.positions) - len(self.pending)
        for sig in sorted(signals, key=lambda s: s.score, reverse=True)[:max(slots, 0)]:
            self.pending[sig.symbol] = asdict(sig)
            word = "BUY ORDER " if sig.side == 1 else "SELL ORDER"
            levels = "".join(f"  {k} {v:.5g}" for k, v in (("stop", sig.stop), ("target", sig.target)) if v is not None)
            self._log(date, f"{word} {sig.symbol:<7} {sig.setup:<20} close {sig.close:.5g}{levels}  ({sig.reason}) -> fills next open")

    # All position maths below runs in "side space": prices are multiplied by the side (+1 long,
    # -1 short) so a short is handled exactly like a long on an upside-down chart.

    def _fill_pending(self, sym, asset_class, bar):
        order = self.pending.pop(sym, None)
        if not order:
            return
        side = order.get("side", 1)
        f = lambda x: side * x
        bar_f = bar if side == 1 else strategy.flip([bar])[0]
        entry_f = bar_f["open"] + abs(bar_f["open"]) * config.SLIPPAGE[asset_class]
        if order.get("stop") is not None:
            stop_f = f(order["stop"])
            if bar_f["open"] <= stop_f:
                self._log(bar["date"], f"SKIP       {sym:<7} opened beyond the stop")
                return
        else:
            stop_f = entry_f - config.STOP_ATR * order["atr"]
        target_f = f(order["target"]) if order.get("target") is not None else None
        if target_f is not None and bar_f["open"] >= target_f:
            self._log(bar["date"], f"SKIP       {sym:<7} opened beyond the target")
            return
        entry, risk_per_unit = abs(entry_f), entry_f - stop_f
        equity = self.equity()
        units = min(equity * config.RISK_PER_TRADE / risk_per_unit,
                    equity * config.MAX_NOTIONAL_PCT[asset_class] / entry,
                    self.cash / entry)
        if asset_class == "stock":
            units = float(int(units))
        if units <= 0:
            self._log(bar["date"], f"SKIP       {sym:<7} not enough cash")
            return
        self.cash -= units * entry
        self.positions[sym] = {
            "asset_class": asset_class, "setup": order["setup"], "side": side, "entry_date": bar["date"],
            "entry": entry, "units": units, "stop": f(stop_f), "initial_stop": f(stop_f),
            "target": order.get("target"), "atr": order["atr"], "extreme": f(bar_f["high"]), "bars_held": 0,
        }
        word = "BOUGHT " if side == 1 else "SHORTED"
        tgt = f"  target {order['target']:.5g}" if order.get("target") is not None else ""
        self._log(bar["date"], f"{word}    {sym:<7} {units:.6g} @ {entry:.5g}  stop {f(stop_f):.5g}{tgt}  "
                               f"(risking ${units * risk_per_unit:,.0f}, {units * entry / equity:.0%} of equity)")

    def _manage_position(self, sym, history):
        pos = self.positions.get(sym)
        if not pos:
            return
        side = pos.get("side", 1)
        f = lambda x: side * x
        if side == -1:
            history = strategy.flip(history[-60:])
        bar = history[-1]
        entry_f, stop_f, init_f = f(pos["entry"]), f(pos["stop"]), f(pos["initial_stop"])
        target_f = f(pos["target"]) if pos.get("target") is not None else None
        slip = config.SLIPPAGE[pos["asset_class"]]
        worse = lambda x: x - abs(x) * slip

        if bar["date"] == pos["entry_date"]:
            if bar["low"] <= stop_f:
                return self._close(sym, bar["date"], f(worse(stop_f)), "stop hit on entry day")
            if target_f is not None and bar["high"] >= target_f:
                return self._close(sym, bar["date"], f(target_f), "target hit on entry day")
            pos["extreme"] = f(max(f(pos["extreme"]), bar["high"]))
            return
        pos["bars_held"] += 1
        if bar["open"] <= stop_f:
            return self._close(sym, bar["date"], f(worse(bar["open"])), "gapped through stop")
        if bar["low"] <= stop_f:
            return self._close(sym, bar["date"], f(worse(stop_f)), "stop hit")
        if target_f is not None and bar["open"] >= target_f:
            return self._close(sym, bar["date"], f(worse(bar["open"])), "gapped through target")
        if target_f is not None and bar["high"] >= target_f:
            return self._close(sym, bar["date"], f(target_f), "target hit")

        # Update stops using today's bar (they apply from tomorrow).
        extreme_f = max(f(pos["extreme"]), bar["high"])
        pos["extreme"] = f(extreme_f)
        reached_breakeven = extreme_f >= entry_f + config.BREAKEVEN_R * (entry_f - init_f)
        if reached_breakeven:
            pos["stop"] = f(max(stop_f, entry_f, extreme_f - config.TRAIL_ATR * pos["atr"]))

        # The trend exit protects open profit; before breakeven the stop alone defines the risk.
        if reached_breakeven and strategy.trend_broken(history):
            return self._close(sym, bar["date"], f(worse(bar["close"])), "trend broke (50-day average)")
        if pos["bars_held"] >= config.TIME_STOP_BARS and not reached_breakeven:
            return self._close(sym, bar["date"], f(worse(bar["close"])), "time stop")

    def _close(self, sym, date, price, why):
        pos = self.positions.pop(sym)
        side = pos.get("side", 1)
        pnl = side * pos["units"] * (price - pos["entry"])
        self.cash += pos["units"] * pos["entry"] + pnl
        r_mult = side * (price - pos["entry"]) / abs(pos["entry"] - pos["initial_stop"])
        self.last_price[sym] = price
        self.closed.append({
            "symbol": sym, "asset_class": pos["asset_class"], "setup": pos["setup"],
            "side": "long" if side == 1 else "short",
            "entry_date": pos["entry_date"], "exit_date": date, "entry": round(pos["entry"], 6),
            "exit": round(price, 6), "units": pos["units"], "pnl": round(pnl, 2),
            "r_multiple": round(r_mult, 2), "exit_reason": why,
        })
        word = "SOLD   " if side == 1 else "COVERED"
        self._log(date, f"{word}    {sym:<7} @ {price:.5g}  {why}  P&L ${pnl:,.2f} ({r_mult:+.2f}R)")
