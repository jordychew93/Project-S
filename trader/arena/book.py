"""A virtual long-only ledger for the simple arena strategies, plus the shared scorecard maths.

Orders decided on a day's close fill at the next day's open (with Project T's stock slippage), so no
strategy trades on prices it couldn't have had. Nothing here talks to a broker.
"""

import math
from datetime import date as _date

from .. import config

SLIP = config.SLIPPAGE["stock"]


class Book:
    def __init__(self, cash=config.STARTING_CASH):
        self.cash = cash
        self.positions = {}     # symbol -> {units, entry, entry_date, stop, risk, bars_held}
        self.orders = []        # [{action: buy|sell, symbol, ...}] to fill at the next open
        self.closed = []        # closed trades
        self.curve = []         # [[date, equity after the close]]
        self.last_price = {}

    STATE_KEYS = ("cash", "positions", "orders", "closed", "curve", "last_price")

    def to_state(self):
        return {k: getattr(self, k) for k in self.STATE_KEYS}

    @classmethod
    def from_state(cls, state):
        b = cls()
        for k in cls.STATE_KEYS:
            setattr(b, k, state[k])
        return b

    def equity(self):
        return self.cash + sum(p["units"] * self.last_price.get(s, p["entry"]) for s, p in self.positions.items())

    # ----- the day's cycle: fill at the open, stops intraday, mark at the close -------------------
    def fill_orders(self, date, today):
        """`today` maps symbol -> today's bar. Sells fill before buys so freed cash can be reused."""
        keep = []
        for o in sorted(self.orders, key=lambda o: o["action"] != "sell"):
            bar = today.get(o["symbol"])
            if bar is None:
                keep.append(o)                       # no bar for this symbol today: try at its next open
                continue
            if o["action"] == "sell":
                if o["symbol"] in self.positions:
                    self.close(o["symbol"], date, bar["open"] * (1 - SLIP), o.get("reason", "exit"))
            else:
                self._buy(o, date, bar)
        self.orders = keep

    def _buy(self, o, date, bar):
        sym = o["symbol"]
        if sym in self.positions:
            return
        price = bar["open"] * (1 + SLIP)
        eq = self.equity()
        units = min(eq * o.get("pct", 1.0) / price, self.cash / price)
        stop_dist = o.get("stop_dist")
        if stop_dist:
            units = min(units, eq * o["risk"] / stop_dist)
        units = float(int(units))
        if units <= 0:
            return
        self.cash -= units * price
        self.positions[sym] = {"units": units, "entry": price, "entry_date": date, "bars_held": 0,
                               "stop": price - stop_dist if stop_dist else None,
                               "risk": units * stop_dist if stop_dist else None, "setup": o.get("setup", "")}

    def check_stop(self, sym, date, bar):
        pos = self.positions.get(sym)
        if pos and pos.get("stop") is not None and bar["low"] <= pos["stop"]:
            self.close(sym, date, min(bar["open"], pos["stop"]) * (1 - SLIP), "stop hit")

    def close(self, sym, date, price, why):
        pos = self.positions.pop(sym)
        pnl = pos["units"] * (price - pos["entry"])
        self.cash += pos["units"] * price
        self.last_price[sym] = price
        self.closed.append({"symbol": sym, "setup": pos.get("setup", ""), "entry_date": pos["entry_date"],
                            "exit_date": date, "entry": round(pos["entry"], 4), "exit": round(price, 4),
                            "units": pos["units"], "pnl": round(pnl, 2),
                            "r_multiple": round(pnl / pos["risk"], 2) if pos.get("risk") else None,
                            "exit_reason": why})

    def mark(self, date, today):
        for s, bar in today.items():
            self.last_price[s] = bar["close"]
        for pos in self.positions.values():
            if pos["entry_date"] != date:
                pos["bars_held"] += 1
        self.curve.append([date, round(self.equity(), 2)])


# ----- scorecard ---------------------------------------------------------------------------------
def max_drawdown(equities):
    peak, worst = -math.inf, 0.0
    for e in equities:
        peak = max(peak, e)
        worst = min(worst, e / peak - 1)
    return -worst


def metrics(curve, closed, start_cash=config.STARTING_CASH, spy_return=None):
    """Scorecard for one book. `curve` is [[date, equity]], `closed` the closed trades."""
    eq = [e for _, e in curve]
    out = {"days": len(eq), "start": curve[0][0] if curve else None, "end": curve[-1][0] if curve else None}
    if not eq:
        eq = [start_cash]
    series = [start_cash] + eq
    out["equity"] = round(eq[-1], 2)
    out["total_return"] = eq[-1] / start_cash - 1
    if curve and len(curve) > 1:
        years = (_date.fromisoformat(curve[-1][0]) - _date.fromisoformat(curve[0][0])).days / 365.25
        out["cagr"] = (eq[-1] / start_cash) ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else None
    else:
        out["cagr"] = None
    out["max_drawdown"] = max_drawdown(series)
    rets = [series[i] / series[i - 1] - 1 for i in range(1, len(series))]
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
        out["sharpe"] = mean / sd * math.sqrt(252) if sd > 0 else None
    else:
        out["sharpe"] = None
    pnls = [t["pnl"] for t in closed]
    rs = [t["r_multiple"] for t in closed if t.get("r_multiple") is not None]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p <= 0]
    out["trades"] = len(pnls)
    out["win_rate"] = len(wins) / len(pnls) if pnls else None
    out["expectancy"] = sum(pnls) / len(pnls) if pnls else None          # average $ per closed trade
    out["avg_r"] = sum(rs) / len(rs) if rs else None
    out["profit_factor"] = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None
    out["vs_spy"] = out["total_return"] - spy_return if spy_return is not None else None
    return out
