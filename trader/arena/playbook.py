"""Project T with the investing-playbook rules, each behind a flag (arena strategy 2).

Rules (from the Obsidian note "Project T - refinements from investing playbook"):
  trend_gate_200ma        buys only above the 200-day average, shorts only below it
  tranches                scale in: 1/N of the planned size at the open, the rest as limit adds at
                          entry - k/N of the way to the stop (the stop stays where it was)
  max_position_pct        at most this fraction of equity in one name
  min_cash_pct            keep this fraction of equity in cash (entries/adds are shrunk or skipped)
  partial_tp_r / _pct     take half off at +2R or +25%, whichever comes first, and trail the rest
  earnings_blackout_days  no new entries this many trading days before a report (needs earnings dates)
  regime_filter           halve risk per trade while SPY closes below its 200-day average

With every flag off this behaves exactly like the normal Portfolio. Virtual ledger only: no broker.
"""

from datetime import date as _date, timedelta

from .. import config, strategy
from ..portfolio import Portfolio

ALL_ON = {"trend_gate_200ma": True, "tranches": 3, "max_position_pct": 0.10, "min_cash_pct": 0.15,
          "partial_tp_r": 2.0, "partial_tp_pct": 0.25, "earnings_blackout_days": 3, "regime_filter": True}
ALL_OFF = {"trend_gate_200ma": False, "tranches": 1, "max_position_pct": None, "min_cash_pct": None,
           "partial_tp_r": None, "partial_tp_pct": None, "earnings_blackout_days": None, "regime_filter": False}

# One flag group at a time, for the ablation table.
FLAG_GROUPS = {
    "200-day trend gate": {"trend_gate_200ma": True},
    "scale in (3 tranches)": {"tranches": 3},
    "10% cap + 15% cash": {"max_position_pct": 0.10, "min_cash_pct": 0.15},
    "half off at +2R/+25%": {"partial_tp_r": 2.0, "partial_tp_pct": 0.25},
    "earnings blackout": {"earnings_blackout_days": 3},
    "risk-off halves risk": {"regime_filter": True},
}


def trading_days_between(d1, d2):
    """Weekdays in (d1, d2]."""
    a, b = _date.fromisoformat(d1), _date.fromisoformat(d2)
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


class PlaybookPortfolio(Portfolio):
    def __init__(self, flags=None, earnings=None, cash=config.STARTING_CASH):
        super().__init__(cash)
        self.flags = {**ALL_OFF, **(flags or {})}
        self.earnings = earnings or {}      # symbol -> list of report dates (YYYY-MM-DD)
        self._universe = {}

    def run(self, universe, start_fresh_at_latest=True):
        self._universe = universe
        super().run(universe, start_fresh_at_latest)

    # ----- helpers -----------------------------------------------------------------------------
    def _history(self, sym, date):
        if sym not in self._universe:
            return []
        bars = self._universe[sym][1]
        for i in range(len(bars) - 1, -1, -1):
            if bars[i]["date"] <= date:
                return bars[:i + 1]
        return []

    def _ma200(self, sym, date):
        h = self._history(sym, date)
        if len(h) < 200:
            return None, None
        closes = [b["close"] for b in h[-200:]]
        return closes[-1], sum(closes) / 200

    def risk_off(self, date):
        close, ma = self._ma200("SPY", date)
        return ma is not None and close < ma

    def earnings_soon(self, sym, date):
        n = self.flags["earnings_blackout_days"]
        return bool(n) and any(e >= date and trading_days_between(date, e) <= n for e in self.earnings.get(sym, []))

    # ----- entries -----------------------------------------------------------------------------
    def _queue_orders(self, date, signals):
        keep = []
        for sig in signals:
            if self.flags["trend_gate_200ma"]:
                close, ma = self._ma200(sig.symbol, date)
                if ma is None or (sig.side == 1 and close <= ma) or (sig.side == -1 and close >= ma):
                    continue
            if self.earnings_soon(sig.symbol, date):
                self._log(date, f"SKIP       {sig.symbol:<7} earnings within {self.flags['earnings_blackout_days']} days")
                continue
            keep.append(sig)
        before = set(self.pending)
        super()._queue_orders(date, keep)
        mult = 0.5 if self.flags["regime_filter"] and self.risk_off(date) else 1.0
        for s in set(self.pending) - before:
            self.pending[s]["risk_mult"] = mult

    def _cash_room(self, price, equity):
        room = self.cash / price
        if self.flags["min_cash_pct"]:
            room = min(room, (self.cash - self.flags["min_cash_pct"] * equity) / price)
        return room

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
        cap = config.MAX_NOTIONAL_PCT[asset_class]
        if self.flags["max_position_pct"]:
            cap = min(cap, self.flags["max_position_pct"])
        units = min(equity * config.RISK_PER_TRADE * order.get("risk_mult", 1.0) / risk_per_unit,
                    equity * cap / entry, self._cash_room(entry, equity))
        whole = asset_class == "stock"
        if whole:
            units = float(int(units))
        if units <= 0:
            self._log(bar["date"], f"SKIP       {sym:<7} not enough cash (or cash reserve)")
            return
        n = max(int(self.flags["tranches"] or 1), 1)
        first = units / n
        if whole:
            first = float(int(first))
        if first <= 0:
            first, n = units, 1
        self.cash -= first * entry
        self.positions[sym] = {
            "asset_class": asset_class, "setup": order["setup"], "side": side, "entry_date": bar["date"],
            "entry": entry, "units": first, "stop": f(stop_f), "initial_stop": f(stop_f),
            "target": order.get("target"), "atr": order["atr"], "extreme": f(bar_f["high"]), "bars_held": 0,
            "first_entry": entry, "tranche_units": first, "tranche_k": 1, "tranches_left": n - 1,
            "tranche_step": (entry_f - stop_f) / n, "risk_dollars": first * risk_per_unit,
            "realized": 0.0, "partial_done": False,
        }
        word = "BOUGHT " if side == 1 else "SHORTED"
        self._log(bar["date"], f"{word}    {sym:<7} {first:.6g} @ {entry:.5g}  stop {f(stop_f):.5g}  "
                               f"(tranche 1/{n}, risk x{order.get('risk_mult', 1.0):g})")

    # ----- position management -----------------------------------------------------------------
    def _manage_position(self, sym, history):
        pos = self.positions.get(sym)
        if not pos:
            return
        bar = history[-1]
        if pos.get("tranches_left") and bar["date"] != pos["entry_date"]:
            self._add_tranches(sym, pos, bar)
        super()._manage_position(sym, history)
        pos = self.positions.get(sym)
        if pos and not pos.get("partial_done", True) and bar["date"] != pos["entry_date"] and \
                (self.flags["partial_tp_r"] or self.flags["partial_tp_pct"]):
            self._take_partial(sym, pos, bar)

    def _add_tranches(self, sym, pos, bar):
        side = pos.get("side", 1)
        f = lambda x: side * x
        bar_f = bar if side == 1 else strategy.flip([bar])[0]
        slip = config.SLIPPAGE[pos["asset_class"]]
        while pos["tranches_left"] > 0:
            level_f = f(pos["first_entry"]) - pos["tranche_k"] * pos["tranche_step"]
            if bar_f["low"] > level_f:
                return
            fill_f = min(bar_f["open"], level_f)
            price = abs(fill_f + abs(fill_f) * slip)
            add = min(pos["tranche_units"], self._cash_room(price, self.equity()))
            if pos["asset_class"] == "stock":
                add = float(int(add))
            pos["tranche_k"] += 1
            pos["tranches_left"] -= 1
            if add <= 0:
                continue
            total = pos["units"] + add
            pos["entry"] = (pos["units"] * pos["entry"] + add * price) / total
            pos["units"] = total
            pos["risk_dollars"] += add * abs(price - pos["initial_stop"])
            self.cash -= add * price
            self._log(bar["date"], f"ADDED      {sym:<7} {add:.6g} @ {price:.5g}  (tranche {pos['tranche_k'] - 1})")

    def _take_partial(self, sym, pos, bar):
        side = pos.get("side", 1)
        f = lambda x: side * x
        bar_f = bar if side == 1 else strategy.flip([bar])[0]
        entry_f = f(pos["entry"])
        risk_f = entry_f - f(pos["initial_stop"])
        levels = []
        if self.flags["partial_tp_r"] and risk_f > 0:
            levels.append(entry_f + self.flags["partial_tp_r"] * risk_f)
        if self.flags["partial_tp_pct"]:
            levels.append(entry_f + self.flags["partial_tp_pct"] * abs(entry_f))
        if not levels or bar_f["high"] < min(levels):
            return
        level_f = min(levels)
        slip = config.SLIPPAGE[pos["asset_class"]]
        price_f = bar_f["open"] - abs(bar_f["open"]) * slip if bar_f["open"] >= level_f else level_f
        half = pos["units"] / 2
        if pos["asset_class"] == "stock":
            half = float(int(half))
        pos["partial_done"] = True
        if half <= 0:
            return
        price = f(price_f)
        pnl = side * half * (price - pos["entry"])
        self.cash += half * pos["entry"] + pnl
        pos["units"] -= half
        pos["realized"] += pnl
        self._log(bar["date"], f"TOOK HALF  {sym:<7} {half:.6g} @ {price:.5g}  P&L ${pnl:,.2f}")

    def _close(self, sym, date, price, why):
        pos = self.positions.pop(sym)
        side = pos.get("side", 1)
        pnl = side * pos["units"] * (price - pos["entry"])
        self.cash += pos["units"] * pos["entry"] + pnl
        total = pnl + pos.get("realized", 0.0)
        risk = pos.get("risk_dollars") or pos["units"] * abs(pos["entry"] - pos["initial_stop"])
        self.last_price[sym] = price
        self.closed.append({
            "symbol": sym, "asset_class": pos["asset_class"], "setup": pos["setup"],
            "side": "long" if side == 1 else "short", "entry_date": pos["entry_date"], "exit_date": date,
            "entry": round(pos["entry"], 6), "exit": round(price, 6), "units": pos["units"],
            "pnl": round(total, 2), "r_multiple": round(total / risk, 2) if risk else None, "exit_reason": why,
        })
        self._log(date, f"{'SOLD   ' if side == 1 else 'COVERED'}    {sym:<7} @ {price:.5g}  {why}  P&L ${total:,.2f}")
