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
        return self.cash + sum(p["units"] * self.last_price.get(s, p["entry"]) for s, p in self.positions.items())

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
            self._log(date, f"BUY ORDER  {sig.symbol:<7} {sig.setup:<9} close {sig.close:.5g}  ({sig.reason}) -> fills next open")

    def _fill_pending(self, sym, asset_class, bar):
        order = self.pending.pop(sym, None)
        if not order:
            return
        price = bar["open"] * (1 + config.SLIPPAGE[asset_class])
        stop = price - config.STOP_ATR * order["atr"]
        risk_per_unit = price - stop
        equity = self.equity()
        units = min(equity * config.RISK_PER_TRADE / risk_per_unit,
                    equity * config.MAX_NOTIONAL_PCT[asset_class] / price,
                    self.cash / price)
        if asset_class == "stock":
            units = float(int(units))
        if units <= 0:
            self._log(bar["date"], f"SKIP       {sym:<7} not enough cash")
            return
        self.cash -= units * price
        self.positions[sym] = {
            "asset_class": asset_class, "setup": order["setup"], "entry_date": bar["date"],
            "entry": price, "units": units, "stop": stop, "initial_stop": stop,
            "atr": order["atr"], "highest": bar["high"], "bars_held": 0,
        }
        self._log(bar["date"], f"BOUGHT     {sym:<7} {units:.6g} @ {price:.5g}  stop {stop:.5g}  "
                               f"(risking ${units * risk_per_unit:,.0f}, {units * price / equity:.0%} of equity)")

    def _manage_position(self, sym, history):
        pos = self.positions.get(sym)
        if not pos:
            return
        bar = history[-1]
        if bar["date"] == pos["entry_date"]:
            exit_price = bar["low"] <= pos["stop"] and pos["stop"]
            if exit_price:
                self._close(sym, bar["date"], exit_price, "stop hit on entry day")
            else:
                pos["highest"] = max(pos["highest"], bar["high"])
            return
        pos["bars_held"] += 1
        slip = config.SLIPPAGE[pos["asset_class"]]
        if bar["open"] <= pos["stop"]:
            return self._close(sym, bar["date"], bar["open"] * (1 - slip), "gapped below stop")
        if bar["low"] <= pos["stop"]:
            return self._close(sym, bar["date"], pos["stop"] * (1 - slip), "stop hit")

        # Update stops using today's bar (they apply from tomorrow).
        pos["highest"] = max(pos["highest"], bar["high"])
        r = pos["entry"] - pos["initial_stop"]
        reached_breakeven = pos["highest"] >= pos["entry"] + config.BREAKEVEN_R * r
        if reached_breakeven:
            pos["stop"] = max(pos["stop"], pos["entry"], pos["highest"] - config.TRAIL_ATR * pos["atr"])

        # The trend exit protects open profit; before breakeven the ATR stop alone defines the risk.
        if reached_breakeven and strategy.trend_broken(history):
            return self._close(sym, bar["date"], bar["close"] * (1 - slip), "closed below 50-day average")
        if pos["bars_held"] >= config.TIME_STOP_BARS and not reached_breakeven:
            return self._close(sym, bar["date"], bar["close"] * (1 - slip), "time stop")

    def _close(self, sym, date, price, why):
        pos = self.positions.pop(sym)
        self.cash += pos["units"] * price
        pnl = pos["units"] * (price - pos["entry"])
        r_mult = (price - pos["entry"]) / (pos["entry"] - pos["initial_stop"])
        self.last_price[sym] = price
        self.closed.append({
            "symbol": sym, "asset_class": pos["asset_class"], "setup": pos["setup"],
            "entry_date": pos["entry_date"], "exit_date": date, "entry": round(pos["entry"], 6),
            "exit": round(price, 6), "units": pos["units"], "pnl": round(pnl, 2),
            "r_multiple": round(r_mult, 2), "exit_reason": why,
        })
        self._log(date, f"SOLD       {sym:<7} @ {price:.5g}  {why}  P&L ${pnl:,.2f} ({r_mult:+.2f}R)")
