"""The arena line-up. Every runner owns one virtual $100k book and advances one trading day at a time.

    baseline   Project T exactly as it runs now (same engine, same rules, same ~400-day data window)
    playbook   Project T + the investing-playbook rules (all flags on, see playbook.py)
    breakout   Turtle-style: buy a close above the 55-day high, exit below the 20-day low, 2 ATR stop
    meanrev    RSI(2) below 10 on stocks above their 200-day average; exit RSI(2) above 70 or after 10 days
    rotation   monthly: hold the strongest of SPY/QQQ/GLD/TLT by 3- and 6-month return, BIL if all negative
    spy        buy and hold SPY (the benchmark)

`step(date, market)` uses only bars up to `date`; orders fill at the next open. Nothing here can
place a real or paper order: these are ledgers.
"""

from .. import config
from ..indicators import atr, rsi
from ..portfolio import Portfolio
from . import market as mk
from .book import Book
from .playbook import ALL_ON, PlaybookPortfolio

PROJECT_T_WINDOW = 276   # bars the live moomoo bot sees (it fetches 400 calendar days)


class Runner:
    key = name = blurb = ""
    symbols = mk.STOCKS

    def step(self, date, market):
        raise NotImplementedError

    def equity(self):
        raise NotImplementedError

    @property
    def curve(self):
        raise NotImplementedError

    @property
    def closed(self):
        raise NotImplementedError

    def open_positions(self):
        raise NotImplementedError

    def to_state(self):
        raise NotImplementedError

    def load_state(self, state):
        raise NotImplementedError


# ----- Project T (baseline and playbook) -----------------------------------------------------------
class ProjectTRunner(Runner):
    key, name = "baseline", "Project T (as is)"
    blurb = "the current bot: support & resistance swing trades, 1% risk each"

    def __init__(self):
        self.p = self.make_portfolio()
        self._curve = []

    def make_portfolio(self):
        return Portfolio()

    def step(self, date, market):
        window = {}
        for s in self.symbols:
            h = market.history(s, date, PROJECT_T_WINDOW)
            if h:
                window[s] = ("stock", h)
        if not window:
            return
        self.p.events = []
        self.p.run(window, start_fresh_at_latest=True)   # only bars after last_seen are processed
        self._curve.append([date, round(self.p.equity(), 2)])

    def equity(self):
        return self.p.equity()

    @property
    def curve(self):
        return self._curve

    @property
    def closed(self):
        return self.p.closed

    def open_positions(self):
        return sorted(self.p.positions)

    def to_state(self):
        st = {k: getattr(self.p, k) for k in ("cash", "positions", "pending", "last_seen", "last_price", "closed")}
        st["curve"] = self._curve
        return st

    def load_state(self, st):
        for k in ("cash", "positions", "pending", "last_seen", "last_price", "closed"):
            setattr(self.p, k, st[k])
        self._curve = st["curve"]


class PlaybookRunner(ProjectTRunner):
    key, name = "playbook", "Project T + playbook rules"
    blurb = "same bot plus 200-day trend gate, scaling in, 10% cap, 15% cash, half off at +2R, risk-off halving"

    def __init__(self, flags=None, earnings=None, key=None, name=None):
        self.flags = dict(ALL_ON if flags is None else flags)
        self.earnings = earnings or {}
        if key:
            self.key = key
        if name:
            self.name = name
        super().__init__()

    def make_portfolio(self):
        return PlaybookPortfolio(self.flags, self.earnings)


# ----- simple strategies on a Book -----------------------------------------------------------------
class BookRunner(Runner):
    def __init__(self):
        self.book = Book()
        self.extra = {}

    def step(self, date, market):
        today = {s: b for s in self.symbols if (b := market.bar(s, date))}
        if not today:
            return
        self.book.fill_orders(date, today)
        for s, bar in today.items():
            if s in self.book.positions:
                self.book.check_stop(s, date, bar)
        self.book.mark(date, today)
        self.book.orders += self.decide(date, market)

    def decide(self, date, market):
        return []

    def pending_symbols(self):
        return {o["symbol"] for o in self.book.orders}

    def equity(self):
        return self.book.equity()

    @property
    def curve(self):
        return self.book.curve

    @property
    def closed(self):
        return self.book.closed

    def open_positions(self):
        return sorted(self.book.positions)

    def to_state(self):
        return {**self.book.to_state(), "extra": self.extra}

    def load_state(self, st):
        self.book = Book.from_state(st)
        self.extra = st.get("extra", {})


class BreakoutRunner(BookRunner):
    key, name = "breakout", "Breakout (Turtle-style)"
    blurb = "buys a new 55-day high, sells on a 20-day low, sized by volatility"
    ENTRY, EXIT, N_PERIOD, STOP_N, RISK, CAP, MAX_POS = 55, 20, 20, 2.0, 0.01, 0.20, config.MAX_OPEN_POSITIONS

    def decide(self, date, market):
        orders, candidates = [], []
        pending = self.pending_symbols()
        for s in self.symbols:
            h = market.history(s, date, self.ENTRY + 25)
            if len(h) < self.ENTRY + 1:
                continue
            c = h[-1]["close"]
            if s in self.book.positions:
                if c < min(b["low"] for b in h[-self.EXIT - 1:-1]):
                    orders.append({"action": "sell", "symbol": s, "reason": "20-day low"})
            elif s not in pending:
                prior_high = max(b["high"] for b in h[-self.ENTRY - 1:-1])
                n = atr([b["high"] for b in h], [b["low"] for b in h], [b["close"] for b in h], self.N_PERIOD)[-1]
                if c > prior_high and n:
                    candidates.append((c / prior_high - 1, s, n))
        slots = self.MAX_POS - len(self.book.positions) + len(orders) - \
            sum(1 for o in self.book.orders if o["action"] == "buy")
        for _, s, n in sorted(candidates, reverse=True)[:max(slots, 0)]:
            orders.append({"action": "buy", "symbol": s, "pct": self.CAP, "risk": self.RISK,
                           "stop_dist": self.STOP_N * n, "setup": "55-day breakout"})
        return orders


class MeanReversionRunner(BookRunner):
    key, name = "meanrev", "Mean reversion (RSI-2)"
    blurb = "buys sharp 1-2 day dips in stocks that are above their 200-day average, sells the bounce"
    BUY_BELOW, SELL_ABOVE, MAX_DAYS, PCT, MAX_POS = 10, 70, 10, 0.15, 6

    def decide(self, date, market):
        orders, candidates = [], []
        pending = self.pending_symbols()
        for s in self.symbols:
            h = market.history(s, date, 260)
            if len(h) < 201:
                continue
            closes = [b["close"] for b in h]
            r2 = rsi(closes, 2)[-1]
            if s in self.book.positions:
                if r2 > self.SELL_ABOVE:
                    orders.append({"action": "sell", "symbol": s, "reason": "RSI(2) above 70"})
                elif self.book.positions[s]["bars_held"] >= self.MAX_DAYS:
                    orders.append({"action": "sell", "symbol": s, "reason": f"{self.MAX_DAYS}-day time exit"})
            elif s not in pending and closes[-1] > sum(closes[-200:]) / 200 and r2 < self.BUY_BELOW:
                candidates.append((r2, s))
        slots = self.MAX_POS - len(self.book.positions) + len(orders) - \
            sum(1 for o in self.book.orders if o["action"] == "buy")
        for _, s in sorted(candidates)[:max(slots, 0)]:
            orders.append({"action": "buy", "symbol": s, "pct": self.PCT, "setup": "RSI(2) dip"})
        return orders


class RotationRunner(BookRunner):
    key, name = "rotation", "Monthly momentum rotation"
    blurb = "once a month holds whichever of SPY, QQQ, gold (GLD) or long bonds (TLT) is strongest"
    symbols = mk.ROTATION + [mk.CASH_ETF]
    PCT = 0.995

    def decide(self, date, market):
        month = date[:7]
        if self.extra.get("month") == month:
            return []
        scores = {}
        for s in mk.ROTATION:
            h = market.history(s, date, 127)
            if len(h) == 127:
                c = [b["close"] for b in h]
                scores[s] = (c[-1] / c[-64] - 1 + c[-1] / c[-127] - 1) / 2
        if not scores:
            return []
        self.extra["month"] = month
        best = max(scores, key=scores.get)
        target = best if scores[best] > 0 else (mk.CASH_ETF if market.bar(mk.CASH_ETF, date) else None)
        self.extra["last_pick"] = target or "cash"
        held = list(self.book.positions)
        if held == ([target] if target else []):
            return []
        orders = [{"action": "sell", "symbol": s, "reason": "monthly rotation"} for s in held]
        if target:
            orders.append({"action": "buy", "symbol": target, "pct": self.PCT, "setup": "monthly leader"})
        return orders


class BuyHoldSPYRunner(BookRunner):
    key, name = "spy", "Buy & hold SPY (benchmark)"
    blurb = "just buys the S&P 500 fund and holds it"
    symbols = ["SPY"]

    def decide(self, date, market):
        if not self.book.positions and not self.book.orders:
            return [{"action": "buy", "symbol": "SPY", "pct": 0.999, "setup": "buy and hold"}]
        return []


def lineup(earnings=None):
    return [ProjectTRunner(), PlaybookRunner(earnings=earnings), BreakoutRunner(), MeanReversionRunner(),
            RotationRunner(), BuyHoldSPYRunner()]


KEYS = ["baseline", "playbook", "breakout", "meanrev", "rotation", "spy"]
