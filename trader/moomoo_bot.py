"""moomoo paper bot: the normal strategy, with its US-stock trades mirrored in the moomoo PAPER account.

    moomoo-scan     after the US close: fetch bars (stocks from moomoo), run the strategy on a separate
                    state file, queue orders for the next open and list stock exits to send to moomoo.
    moomoo-execute  ~10 min after the US open: send queued stock entries and exits to moomoo paper as
                    market orders, then reconcile the bot's stock book against the paper account.
    moomoo-status   short plain-text summary.

The bot keeps sizing on its own $100k book (config.STARTING_CASH) so results compare with the cloud bot.
Crypto and FX can't trade on moomoo paper, so they stay simulated inside the bot's book. The cloud bot's
state (state/portfolio.json) is never touched.
"""

import copy
import csv
import json
import os
import socket
import time
from datetime import datetime, timezone

from . import broker_moomoo as bm
from . import config, data
from .portfolio import Portfolio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "moomoo_portfolio.json")
TRADES = os.path.join(ROOT, "state", "moomoo_trades.csv")
BROKER_STATE = os.path.join(ROOT, "state", "moomoo_broker.json")
ORDERS_LOG = os.path.join(ROOT, "state", "moomoo_orders.csv")
NAME = "moomoo paper bot"
OFFLINE_LINE = f"⚠️ {NAME}: OpenD not running, skipped."


# ----- formatting (plain text, colour-blind friendly: words, arrows and +/−, never colour alone) ---------
def pct(x):
    return f"{x:+.2%}".replace("-", "−")


def signed(x, fmt="{:,.0f}"):
    return ("+" if x > 0 else "−" if x < 0 else "") + fmt.format(abs(x))


def entry_word(side):
    return "BUY ↑" if side == 1 else "SELL ↓"


def exit_word(side):
    return "SELL" if side == 1 else "BUY BACK"


def setup_name(s):
    return s.replace("_", " ")


# ----- state ---------------------------------------------------------------
def load_broker_state(path=BROKER_STATE):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"exits": []}


def save_broker_state(state, path=BROKER_STATE):
    with open(path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def append_orders(rows, path=ORDERS_LOG):
    if not rows:
        return
    fields = ["time", "symbol", "action", "qty", "order_id", "status", "fill_price", "reason"]
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(rows)


def opend_up(host=bm.HOST, port=bm.PORT, timeout=3):
    try:
        socket.create_connection((host, port), timeout).close()
        return True
    except OSError:
        return False


# ----- data ----------------------------------------------------------------
def load_universe(quote_ctx, warnings):
    """Stocks from moomoo (qfq daily bars); crypto/FX from Alpha Vantage if a key is set, else the cached CSVs."""
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
    universe = {}
    for asset_class, symbols in config.WATCHLIST.items():
        for sym in symbols:
            bars = None
            try:
                if asset_class == "stock":
                    bars = bm.fetch_bars(quote_ctx, sym)
                elif api_key:
                    bars = data.fetch(asset_class, sym, api_key)
                    time.sleep(1.5)
                else:
                    bars = data.load_cached(asset_class, sym)
            except Exception as e:
                warnings.append(f"{sym} fetch failed ({str(e)[:60]}), used cache")
                bars = (bm.load_cached_bars(sym) or data.load_cached(asset_class, sym)) if asset_class == "stock" \
                    else data.load_cached(asset_class, sym)
            if bars:
                universe[sym] = (asset_class, bars)
            else:
                warnings.append(f"no data for {sym}")
    return universe


# ----- scan ----------------------------------------------------------------
def scan(p, broker_state, universe):
    """Run the strategy on the bot's book. Returns (new stock exits for moomoo, symbols cancelled as unsent).

    Before running, stock orders that should already have been sent to moomoo (their next open has
    passed, per the new data) but never were are cancelled, so the bot's book never holds a stock
    position that moomoo doesn't.
    """
    cancelled = []
    for sym, o in list(p.pending.items()):
        if o.get("asset_class") != "stock" or o.get("broker_order_id") or sym not in universe:
            continue
        if universe[sym][1][-1]["date"] > o["date"]:
            p.pending.pop(sym)
            cancelled.append(sym)
            p._log(o["date"], f"CANCELLED  {sym:<7} never sent to moomoo (execute didn't run)")
    p.run(universe, start_fresh_at_latest=True)
    exits = []
    for t in p.closed:
        if t["asset_class"] == "stock":
            exits.append({"symbol": t["symbol"], "side": 1 if t["side"] == "long" else -1, "units": t["units"],
                          "reason": t["exit_reason"], "r": t["r_multiple"], "date": t["exit_date"]})
    broker_state.setdefault("exits", []).extend(exits)
    return exits, cancelled


def scan_summary(p, exits, cancelled, warnings, universe):
    stock_dates = [bars[-1]["date"] for ac, bars in universe.values() if ac == "stock"]
    lines = [f"🤖 {NAME} · scan, data to {max(stock_dates) if stock_dates else '?'}"]
    new = [o for o in p.pending.values() if not o.get("broker_order_id")]
    stock_new = [o for o in new if o["asset_class"] == "stock"]
    sim_new = [o for o in new if o["asset_class"] != "stock"]
    if not p.scanned:
        lines.append("😴 No new bars since the last scan.")
    elif stock_new:
        lines.append("📋 For moomoo at the next open: " + " · ".join(
            f"{entry_word(o.get('side', 1))} {o['symbol']} ({setup_name(o['setup'])}, stop {o['stop']:.5g})"
            if o.get("stop") is not None else f"{entry_word(o.get('side', 1))} {o['symbol']} ({setup_name(o['setup'])})"
            for o in sorted(stock_new, key=lambda o: o["symbol"])))
    else:
        lines.append("😴 No new stock setups. Standing aside.")
    if exits:
        lines.append("🚪 To close on moomoo at the open: " + " · ".join(
            f"{exit_word(e['side'])} {e['symbol']} {e['units']:g} sh ({e['reason']}, {signed(e['r'], '{:.2f}')}R)"
            for e in exits))
    sim_closed = [t for t in p.closed if t["asset_class"] != "stock"]
    if sim_new or sim_closed:
        parts = [f"{entry_word(o.get('side', 1))} {o['symbol']} next open" for o in sim_new]
        parts += [f"closed {t['symbol']} {signed(t['pnl'])} USD ({t['exit_reason']})" for t in sim_closed]
        lines.append("🔁 Simulated crypto/FX: " + " · ".join(parts))
    n_stock = sum(1 for pos in p.positions.values() if pos["asset_class"] == "stock")
    lines.append(f"💼 Bot book ${p.equity():,.0f} ({pct(p.equity() / config.STARTING_CASH - 1)}) · "
                 f"{len(p.positions)} open ({n_stock} on moomoo, {len(p.positions) - n_stock} simulated) · "
                 f"{len(p.pending)} pending")
    hot = [(s, w) for s, w in sorted(p.watch.items(), key=lambda kv: kv[1]["atr_away"]) if w["atr_away"] <= 0.5][:3]
    if hot:
        lines.append("🎯 Closest to a setup: " + " · ".join(
            f"{s} inside a level" if w["atr_away"] == 0 else f"{s} {w['atr_away']:.1f} ATR away" for s, w in hot))
    warn = list(warnings) + [f"{s} order cancelled, never sent to moomoo" for s in cancelled]
    if warn:
        lines.append("⚠️ " + " · ".join(warn[:4]) + (f" (+{len(warn) - 4} more)" if len(warn) > 4 else ""))
    return lines


def cmd_scan(args):
    if not opend_up():
        print(OFFLINE_LINE)
        return
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    p, broker_state, warnings = Portfolio.load(STATE), load_broker_state(), []
    quote = bm.open_quote()
    try:
        universe = load_universe(quote, warnings)
    finally:
        quote.close()
    exits, cancelled = scan(p, broker_state, universe)
    p.save(STATE)
    p.append_trades(TRADES)
    broker_state["last_scan"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_broker_state(broker_state)
    print("\n".join(scan_summary(p, exits, cancelled, warnings, universe)))


# ----- execute -------------------------------------------------------------
def us_now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


def market_open(now_et):
    minutes = now_et.hour * 60 + now_et.minute
    return now_et.weekday() < 5 and 9 * 60 + 30 <= minutes < 16 * 60


def plan_entry(p, sym, open_price, date):
    """Size a pending stock order exactly as the engine will at this open. Returns (units, skip_reason)."""
    q = copy.deepcopy(p)
    q.events = []
    q._fill_pending(sym, "stock", {"date": date, "open": open_price, "high": open_price,
                                   "low": open_price, "close": open_price})
    if sym in q.positions:
        return int(q.positions[sym]["units"]), None
    why = q.events[-1].split(sym, 1)[-1].strip() if q.events else "not filled"
    return 0, why


def expected_positions(p, broker_state):
    """Stock quantities moomoo should hold: the bot's open stock positions, entries already sent but not
    yet booked by a scan, and exits still waiting to be sent."""
    exp = {}
    for sym, pos in p.positions.items():
        if pos["asset_class"] == "stock":
            exp[sym] = exp.get(sym, 0) + pos.get("side", 1) * pos["units"]
    for sym, o in p.pending.items():
        if o.get("broker_order_id"):
            exp[sym] = exp.get(sym, 0) + o.get("side", 1) * o["broker_units"]
    for e in broker_state.get("exits", []):
        exp[e["symbol"]] = exp.get(e["symbol"], 0) + e["side"] * e["units"]
    return {s: q for s, q in exp.items() if q}


def reconcile(expected, actual):
    """[(symbol, bot qty, moomoo qty)] wherever they differ."""
    return [(s, expected.get(s, 0), actual.get(s, 0)) for s in sorted(set(expected) | set(actual))
            if abs(expected.get(s, 0) - actual.get(s, 0)) > 1e-9]


def _fill_note(info):
    status = info.get("order_status", "?")
    if status == "FILLED_ALL":
        return f"filled @ {float(info['dealt_avg_price']):.2f}"
    return f"placed ({status.lower().replace('_', ' ')})"


def execute(p, broker_state, broker, now_et, dry_run=False):
    """Send queued exits and entries to moomoo paper. Returns (summary lines, order log rows)."""
    today = now_et.strftime("%Y-%m-%d")
    stamp = now_et.isoformat(timespec="seconds")
    exits = broker_state.get("exits", [])
    entries = {s: o for s, o in p.pending.items() if o["asset_class"] == "stock" and not o.get("broker_order_id")}
    if not exits and not entries:
        mism = reconcile(expected_positions(p, broker_state), broker.positions())
        return (_mismatch_lines(mism) if mism else []), []
    if not market_open(now_et):
        return [f"⏸️ {NAME}: US market closed, {len(exits) + len(entries)} order(s) waiting for the open."], []

    done, skipped, failed, rows = [], [], [], []
    held = broker.positions()
    for e in list(exits):
        sym, side = e["symbol"], e["side"]
        have = held.get(sym, 0) * side          # >0 if moomoo holds the same direction
        qty = int(min(e["units"], max(have, 0)))
        if qty <= 0:
            skipped.append(f"{sym} exit: nothing held on moomoo")
            exits.remove(e)
            continue
        action = "SELL" if side == 1 else "BUY_BACK"
        if dry_run:
            done.append(f"[dry run] {exit_word(side)} {sym} {qty} sh ({e['reason']})")
            continue
        try:
            oid = broker.place(sym, qty, action)
            info = broker.order(oid)
            done.append(f"🚪 {exit_word(side)} {sym} {qty} sh {_fill_note(info)} ({e['reason']})")
            rows.append({"time": stamp, "symbol": sym, "action": action, "qty": qty, "order_id": oid,
                         "status": info.get("order_status"), "fill_price": info.get("dealt_avg_price"),
                         "reason": e["reason"]})
            exits.remove(e)
        except Exception as ex:
            failed.append(f"{sym} exit: {str(ex)[:80]}")

    snaps = broker.snapshots(sorted(entries)) if entries else {}
    for sym, o in sorted(entries.items()):
        side = o.get("side", 1)
        snap = snaps.get(sym)
        if not snap or not snap["time"].startswith(today) or snap["open"] <= 0:
            skipped.append(f"{sym}: no opening price yet, will retry")
            continue
        units, why = plan_entry(p, sym, snap["open"], today)
        if not units:
            skipped.append(f"{sym}: {why}")
            if not dry_run:
                p.pending.pop(sym)
                p._log(today, f"SKIP       {sym:<7} {why} (moomoo order not sent)")
            continue
        action = "BUY" if side == 1 else "SELL_SHORT"
        if dry_run:
            done.append(f"[dry run] {entry_word(side)} {sym} {units} sh ({setup_name(o['setup'])}, open {snap['open']:.2f})")
            continue
        try:
            oid = broker.place(sym, units, action)
        except Exception as ex:
            failed.append(f"{sym}: {str(ex)[:80]}")
            p.pending.pop(sym)
            p._log(today, f"SKIP       {sym:<7} moomoo rejected the order")
            continue
        o.update(broker_order_id=oid, broker_units=units, broker_sent=stamp)
        info = broker.order(oid)
        done.append(f"✅ {entry_word(side)} {sym} {units} sh {_fill_note(info)} ({setup_name(o['setup'])})")
        rows.append({"time": stamp, "symbol": sym, "action": action, "qty": units, "order_id": oid,
                     "status": info.get("order_status"), "fill_price": info.get("dealt_avg_price"),
                     "reason": o["setup"]})

    lines = [f"🤖 {NAME} · orders at the US open{' (dry run, nothing sent)' if dry_run else ''}"]
    lines += done
    if skipped:
        lines.append("⏭️ Skipped: " + " · ".join(skipped))
    if failed:
        lines.append("❗ Failed: " + " · ".join(failed))
    if not dry_run:
        mism = reconcile(expected_positions(p, broker_state), broker.positions())
        n = len(expected_positions(p, broker_state))
        lines += _mismatch_lines(mism) if mism else [f"⚖️ Bot and moomoo agree: {n} stock position(s)"]
    return lines, rows


def _mismatch_lines(mism):
    parts = [f"{s} bot {signed(b, '{:g}') or '0'} vs moomoo {signed(m, '{:g}') or '0'}" for s, b, m in mism]
    return [f"⚠️ {NAME} mismatch: " + " · ".join(parts)]


def cmd_execute(args):
    if not opend_up():
        print(OFFLINE_LINE)
        return
    p, broker_state = Portfolio.load(STATE), load_broker_state()
    broker = bm.MoomooPaperBroker()
    try:
        lines, rows = execute(p, broker_state, broker, us_now(), dry_run=args.dry_run)
    finally:
        broker.close()
    if not args.dry_run:
        p.save(STATE)
        broker_state["last_execute"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_broker_state(broker_state)
        append_orders(rows)
    if lines:
        print("\n".join(lines))


# ----- status --------------------------------------------------------------
def cmd_status(_):
    p, broker_state = Portfolio.load(STATE), load_broker_state()
    lines = [f"🤖 {NAME} · bot book ${p.equity():,.0f} ({pct(p.equity() / config.STARTING_CASH - 1)}), "
             f"cash ${p.cash:,.0f}"]
    if p.positions:
        lines.append("📂 Open: " + " · ".join(
            f"{'LONG' if pos.get('side', 1) == 1 else 'SHORT'} {s} {pos['units']:g} @ {pos['entry']:.5g} "
            f"({'moomoo' if pos['asset_class'] == 'stock' else 'sim'}, P&L "
            f"{signed(pos.get('side', 1) * pos['units'] * (p.last_price.get(s, pos['entry']) - pos['entry']))})"
            for s, pos in sorted(p.positions.items())))
    else:
        lines.append("📂 No open positions.")
    if p.pending:
        lines.append("📋 Pending: " + " · ".join(
            f"{entry_word(o.get('side', 1))} {s}" + (" (sent)" if o.get("broker_order_id") else "")
            for s, o in sorted(p.pending.items())))
    if broker_state.get("exits"):
        lines.append("🚪 Exits waiting: " + " · ".join(f"{exit_word(e['side'])} {e['symbol']} {e['units']:g}"
                                                     for e in broker_state["exits"]))
    if opend_up():
        broker = bm.MoomooPaperBroker()
        try:
            acct, held = broker.account(), broker.positions()
            lines.append(f"🏦 moomoo paper: cash ${acct['cash']:,.0f}, total ${acct['total_assets']:,.0f}, "
                         f"{len(held)} position(s), {len(broker.open_orders())} open order(s)")
            mism = reconcile(expected_positions(p, broker_state), held)
            lines += _mismatch_lines(mism) if mism else ["⚖️ Bot and moomoo agree."]
        finally:
            broker.close()
    else:
        lines.append("⚠️ OpenD not running: moomoo side not checked.")
    lines.append(f"🕒 Last scan {broker_state.get('last_scan', 'never')} · last execute "
                 f"{broker_state.get('last_execute', 'never')} (UTC)")
    print("\n".join(lines))
