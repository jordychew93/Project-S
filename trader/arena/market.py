"""Daily prices for the arena: read-only moomoo quotes, cached as CSV under data/arena/.

Uses only moomoo's OpenQuoteContext (market data). It never opens a trade context.
"""

import datetime as dt
import logging
import os

from .. import config, data

CACHE_DIR = os.path.join(data.DATA_DIR, "arena")
HOST, PORT = "127.0.0.1", 11111

STOCKS = list(config.WATCHLIST["stock"])          # same US-stock universe as Project T
ROTATION = ["SPY", "QQQ", "GLD", "TLT"]
CASH_ETF = "BIL"
SYMBOLS = sorted(set(STOCKS + ROTATION + [CASH_ETF]))

HISTORY_START = "2020-10-01"      # backtest history (stage 1)
LIVE_DAYS = 420                   # calendar days fetched for forward updates (stage 2)


def open_quote():
    """moomoo quote context (prices only), with the SDK's console logging silenced."""
    from moomoo.common.ft_logger import logger
    logger.console_level = logging.CRITICAL
    import moomoo
    return moomoo.OpenQuoteContext(host=HOST, port=PORT)


def fetch(quote_ctx, symbol, start, end):
    """Front-adjusted (qfq) daily bars for a US symbol, oldest first."""
    rows, page_key = [], None
    while True:
        ret, df, page_key = quote_ctx.request_history_kline(
            f"US.{symbol}", start=start, end=end, ktype="K_DAY", autype="qfq", max_count=1000,
            page_req_key=page_key)
        if ret != 0:
            raise RuntimeError(f"{symbol}: moomoo kline error: {df}")
        rows += df.to_dict("records")
        if page_key is None:
            break
    bars = [{"date": str(r["time_key"])[:10], "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "volume": float(r.get("volume") or 0.0)}
            for r in rows]
    bars.sort(key=lambda b: b["date"])
    if not bars:
        raise RuntimeError(f"{symbol}: moomoo returned no bars")
    return data.drop_incomplete(bars, "stock")


def cache_path(kind, symbol):
    return os.path.join(CACHE_DIR, f"{kind}_{symbol}.csv")


def save(kind, symbol, bars):
    os.makedirs(CACHE_DIR, exist_ok=True)
    lines = ["timestamp,open,high,low,close,volume"]
    for b in sorted(bars, key=lambda b: b["date"], reverse=True):
        lines.append(f"{b['date']},{b['open']:.4f},{b['high']:.4f},{b['low']:.4f},{b['close']:.4f},{int(b['volume'])}")
    with open(cache_path(kind, symbol), "w") as f:
        f.write("\n".join(lines) + "\n")


def load(kind, symbol):
    path = cache_path(kind, symbol)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return data.parse_csv(f.read())


def load_market(kind, refresh, symbols=SYMBOLS, today=None):
    """{symbol: bars}. kind "hist" = long backtest history, "live" = recent bars for forward updates.

    With refresh, bars are fetched from moomoo (raises if OpenD is down); otherwise the cache is used.
    """
    today = today or dt.date.today()
    start = HISTORY_START if kind == "hist" else (today - dt.timedelta(days=LIVE_DAYS)).isoformat()
    out = {}
    if refresh:
        q = open_quote()
        try:
            for s in symbols:
                out[s] = fetch(q, s, start, today.isoformat())
                save(kind, s, out[s])
        finally:
            q.close()
    else:
        for s in symbols:
            bars = load(kind, s)
            if bars:
                out[s] = bars
    return out


class Market:
    """Bars for many symbols with fast date lookups. Strategies only ever see bars up to `date`."""

    def __init__(self, bars_by_symbol):
        self.bars = bars_by_symbol
        self.index = {s: {b["date"]: i for i, b in enumerate(bars)} for s, bars in bars_by_symbol.items()}

    def dates(self, calendar="SPY"):
        if calendar in self.bars:
            return [b["date"] for b in self.bars[calendar]]
        return sorted({b["date"] for bars in self.bars.values() for b in bars})

    def bar(self, symbol, date):
        i = self.index.get(symbol, {}).get(date)
        return None if i is None else self.bars[symbol][i]

    def history(self, symbol, date, n=None):
        """Bars up to and including `date` (the last n of them); [] if the symbol has no bar that day."""
        i = self.index.get(symbol, {}).get(date)
        if i is None:
            return []
        return self.bars[symbol][max(0, i + 1 - n) if n else 0:i + 1]

    def latest(self, calendar="SPY"):
        d = self.dates(calendar)
        return d[-1] if d else None
