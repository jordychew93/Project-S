"""Thin adapter for moomoo OpenD, used only by the moomoo paper bot.

PAPER ONLY. Every order goes through `place_paper_order`, which refuses anything that isn't the
simulated environment and the paper account. Nothing here unlocks trading, and nothing here may
call `place_order` except that one function (a unit test checks this).

The `moomoo` package is imported lazily so the cloud / Alpha Vantage path and the tests don't need it.
moomoo's enum values are plain strings ("SIMULATE", "BUY", "MARKET"...), which is what this module uses.
"""

import logging
import os
import time

from . import data

HOST, PORT = "127.0.0.1", 11111
PAPER_ACC_ID = 4405511            # moomoo paper (simulated) account. Never put a real account id here.
SIMULATE = "SIMULATE"             # moomoo TrdEnv.SIMULATE
RET_OK = 0
SIDES = ("BUY", "SELL", "SELL_SHORT", "BUY_BACK")
CACHE_DIR = os.path.join(data.DATA_DIR, "moomoo")


class NotPaperError(PermissionError):
    """Raised when anything tries to trade outside the moomoo paper account."""


def _assert_paper(trd_env, acc_id):
    if trd_env != SIMULATE:
        raise NotPaperError(f"refusing to trade: trd_env must be SIMULATE, got {trd_env!r}")
    if acc_id != PAPER_ACC_ID:
        raise NotPaperError(f"refusing to trade: acc_id must be the paper account {PAPER_ACC_ID}, got {acc_id!r}")


# ----- connection -------------------------------------------------------
def _sdk():
    """Import moomoo with its console logging silenced (it logs INFO lines to stdout by default)."""
    from moomoo.common.ft_logger import logger
    logger.console_level = logging.CRITICAL
    import moomoo
    assert moomoo.TrdEnv.SIMULATE == SIMULATE
    return moomoo


def open_quote():
    m = _sdk()
    return m.OpenQuoteContext(host=HOST, port=PORT)


def open_trade():
    m = _sdk()
    return m.OpenSecTradeContext(filter_trdmarket=m.TrdMarket.US, host=HOST, port=PORT,
                                 security_firm=m.SecurityFirm.FUTUMY)


def code_for(symbol):
    return f"US.{symbol}"


def symbol_for(code):
    return code.split(".", 1)[1] if "." in code else code


# ----- daily bars -------------------------------------------------------
def kline_to_bars(rows):
    """moomoo K_DAY rows (dicts with time_key/open/high/low/close/volume) -> data.py bar dicts, oldest first."""
    bars = [{"date": str(r["time_key"])[:10], "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "volume": float(r.get("volume") or 0.0)}
            for r in rows]
    bars.sort(key=lambda b: b["date"])
    return bars


def bars_to_csv(bars):
    """Same CSV layout as the Alpha Vantage cache (newest first), so data.parse_csv reads it back."""
    lines = ["timestamp,open,high,low,close,volume"]
    for b in sorted(bars, key=lambda b: b["date"], reverse=True):
        lines.append(f"{b['date']},{b['open']:.4f},{b['high']:.4f},{b['low']:.4f},{b['close']:.4f},{int(b['volume'])}")
    return "\n".join(lines) + "\n"


def cache_path(symbol):
    return os.path.join(CACHE_DIR, f"stock_{symbol}.csv")


def load_cached_bars(symbol):
    path = cache_path(symbol)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return data.drop_incomplete(data.parse_csv(f.read()), "stock")


def fetch_bars(quote_ctx, symbol, days=400, today=None):
    """Daily bars (front-adjusted, qfq) for a US symbol from moomoo, cached under data/moomoo/."""
    import datetime as dt
    today = today or dt.date.today()
    start = (today - dt.timedelta(days=days)).isoformat()
    rows, page_key = [], None
    while True:
        ret, df, page_key = quote_ctx.request_history_kline(
            code_for(symbol), start=start, end=today.isoformat(), ktype="K_DAY", autype="qfq",
            max_count=1000, page_req_key=page_key)
        if ret != RET_OK:
            raise RuntimeError(f"{symbol}: moomoo kline error: {df}")
        rows += df.to_dict("records")
        if page_key is None:
            break
    bars = kline_to_bars(rows)
    if not bars:
        raise RuntimeError(f"{symbol}: moomoo returned no bars")
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path(symbol), "w") as f:
        f.write(bars_to_csv(bars))
    return data.drop_incomplete(bars, "stock")


def snapshots(quote_ctx, symbols):
    """{symbol: {"open", "last", "time"}} from get_market_snapshot (time is US Eastern)."""
    ret, df = quote_ctx.get_market_snapshot([code_for(s) for s in symbols])
    if ret != RET_OK:
        raise RuntimeError(f"moomoo snapshot error: {df}")
    return {symbol_for(r["code"]): {"open": float(r["open_price"]), "last": float(r["last_price"]),
                                     "time": str(r["update_time"])}
            for r in df.to_dict("records")}


# ----- paper trading ----------------------------------------------------
def place_paper_order(trd_ctx, symbol, qty, side, price=None, trd_env=SIMULATE, acc_id=PAPER_ACC_ID, remark=""):
    """THE ONLY ORDER PATH. Market order (regular hours) or, with `price`, a LIMIT DAY order.

    Refuses unless trd_env is SIMULATE and acc_id is the paper account. Returns the moomoo order id.
    """
    _assert_paper(trd_env, acc_id)
    if side not in SIDES:
        raise ValueError(f"unknown side {side!r}")
    qty = int(qty)
    if qty <= 0:
        raise ValueError(f"quantity must be positive, got {qty}")
    order_type = "MARKET" if price is None else "NORMAL"     # NORMAL = limit order in moomoo
    ret, df = trd_ctx.place_order(price=0 if price is None else float(price), qty=qty, code=code_for(symbol),
                                  trd_side=side, order_type=order_type, trd_env=SIMULATE, acc_id=PAPER_ACC_ID,
                                  time_in_force="DAY", remark=(remark or "moomoo paper bot")[:64])
    if ret != RET_OK:
        raise RuntimeError(f"{symbol}: paper order rejected: {df}")
    return str(df.iloc[0]["order_id"]) if hasattr(df, "iloc") else str(df[0]["order_id"])


def cancel_paper_order(trd_ctx, order_id, trd_env=SIMULATE, acc_id=PAPER_ACC_ID):
    _assert_paper(trd_env, acc_id)
    ret, df = trd_ctx.modify_order("CANCEL", order_id, 0, 0, trd_env=SIMULATE, acc_id=PAPER_ACC_ID)
    if ret != RET_OK:
        raise RuntimeError(f"cancel {order_id} failed: {df}")


def _records(df):
    return df.to_dict("records") if hasattr(df, "to_dict") else list(df)


def paper_positions(trd_ctx):
    """{symbol: signed quantity} in the paper account (+ long, - short)."""
    ret, df = trd_ctx.position_list_query(trd_env=SIMULATE, acc_id=PAPER_ACC_ID, refresh_cache=True)
    if ret != RET_OK:
        raise RuntimeError(f"paper positions query failed: {df}")
    out = {}
    for r in _records(df):
        qty = abs(float(r["qty"]))
        if qty:
            out[symbol_for(r["code"])] = -qty if r.get("position_side") == "SHORT" else qty
    return out


def paper_cash(trd_ctx):
    ret, df = trd_ctx.accinfo_query(trd_env=SIMULATE, acc_id=PAPER_ACC_ID, refresh_cache=True)
    if ret != RET_OK:
        raise RuntimeError(f"paper account query failed: {df}")
    r = _records(df)[0]
    return {"cash": float(r["cash"]), "total_assets": float(r["total_assets"]), "market_val": float(r["market_val"])}


def paper_orders(trd_ctx, order_id=""):
    ret, df = trd_ctx.order_list_query(order_id=order_id, trd_env=SIMULATE, acc_id=PAPER_ACC_ID, refresh_cache=True)
    if ret != RET_OK:
        raise RuntimeError(f"paper orders query failed: {df}")
    return _records(df)


class MoomooPaperBroker:
    """What the paper bot needs from moomoo, behind one small interface (tests use a fake)."""

    def __init__(self):
        self.quote = open_quote()
        self.trade = open_trade()

    def close(self):
        for ctx in (self.quote, self.trade):
            try:
                ctx.close()
            except Exception:
                pass

    def snapshots(self, symbols):
        return snapshots(self.quote, symbols)

    def positions(self):
        return paper_positions(self.trade)

    def account(self):
        return paper_cash(self.trade)

    def open_orders(self):
        live = ("SUBMITTING", "SUBMITTED", "WAITING_SUBMIT", "FILLED_PART")
        return [o for o in paper_orders(self.trade) if o.get("order_status") in live]

    def place(self, symbol, qty, side, price=None):
        return place_paper_order(self.trade, symbol, qty, side, price=price)

    def order(self, order_id, wait=3.0):
        time.sleep(wait)
        rows = paper_orders(self.trade, order_id)
        return rows[0] if rows else {}
