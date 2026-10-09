"""Command line entry point.

    python -m trader run            fetch fresh data (needs ALPHAVANTAGE_API_KEY), trade, save state
    python -m trader run --offline  trade using the cached CSVs in data/
    python -m trader status         show open positions, pending orders and equity
    python -m trader backtest       replay the cached history from a fresh $100k account
    python -m trader --strategy trend run    pick a strategy: sr (default), trend or both

moomoo paper bot (needs moomoo OpenD on 127.0.0.1:11111 and the moomoo-api package; see trader/moomoo_bot.py):
    python -m trader moomoo-scan              after the US close: scan, queue next-open orders
    python -m trader moomoo-execute [--dry-run]   after the US open: send them to the moomoo PAPER account
    python -m trader moomoo-status            short summary of the bot book and the paper account
"""

import argparse
import os
import sys
import time

from . import config, data
from .portfolio import Portfolio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "portfolio.json")
TRADES = os.path.join(ROOT, "state", "trades.csv")


def load_universe(offline):
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not offline and not api_key:
        sys.exit("Set ALPHAVANTAGE_API_KEY or pass --offline")
    universe = {}
    for asset_class, symbols in config.WATCHLIST.items():
        for sym in symbols:
            try:
                bars = data.load_cached(asset_class, sym) if offline else data.fetch(asset_class, sym, api_key)
                if not offline:
                    time.sleep(1.5)   # stay under the free-tier rate limit (1 request/second)
            except Exception as e:
                print(f"warning: {sym}: {e}")
                bars = data.load_cached(asset_class, sym)
            if bars:
                universe[sym] = (asset_class, bars)
            else:
                print(f"warning: no data for {sym}")
    return universe


def print_status(p):
    print(f"\nCash ${p.cash:,.2f}   Equity ${p.equity():,.2f}   "
          f"Return {p.equity() / config.STARTING_CASH - 1:+.2%}")
    if p.positions:
        print("\nOpen positions:")
        for sym, pos in sorted(p.positions.items()):
            last = p.last_price.get(sym, pos["entry"])
            side = pos.get("side", 1)
            tgt = f"target {pos['target']:<10.5g}" if pos.get("target") is not None else ""
            print(f"  {'LONG ' if side == 1 else 'SHORT'} {sym:<7} {pos['units']:>12.6g} @ {pos['entry']:<10.5g} "
                  f"last {last:<10.5g} stop {pos['stop']:<10.5g} {tgt} "
                  f"P&L ${side * pos['units'] * (last - pos['entry']):>10,.2f}")
    if p.pending:
        print("\nOrders waiting for the next open:")
        for sym, o in sorted(p.pending.items()):
            word = "BUY " if o.get("side", 1) == 1 else "SELL"
            print(f"  {word} {sym:<7} {o['setup']:<20} signal close {o['close']:.5g}  ({o['reason']})")
    if not p.positions and not p.pending:
        print("\nFlat: no positions and no orders. Waiting for a setup.")


def ranked_watch(p):
    return sorted(p.watch.items(), key=lambda kv: kv[1]["atr_away"])


def stale_symbols(universe):
    """Symbols whose last bar is older than the newest bar in the same asset class."""
    latest = {}
    for ac, bars in universe.values():
        latest[ac] = max(latest.get(ac, ""), bars[-1]["date"])
    return sorted((sym, bars[-1]["date"]) for sym, (ac, bars) in universe.items() if bars[-1]["date"] < latest[ac])


def write_report(path, p, universe):
    """Markdown summary of this run, used for the GitHub issue comment and job summary."""
    orders = [e for e in p.events if "ORDER" in e]
    fills = [e for e in p.events if any(w in e for w in ("BOUGHT", "SHORTED", "SOLD", "COVERED"))]
    dates = sorted(p.last_seen.values())
    headline = (f"🚨 {len(orders)} new order(s)" if orders else "😴 No new buy or sell setups") + \
               (f" · {len(fills)} fill/exit(s)" if fills else "")
    lines = [f"## 📈 T daily scan · data to {dates[-1] if dates else '?'}", "", f"**{headline}**", "",
             f"Equity **${p.equity():,.2f}** ({p.equity() / config.STARTING_CASH - 1:+.2%}) · "
             f"{len(p.positions)} open position(s) · {len(p.pending)} pending order(s) · strategy `{config.STRATEGY}`", ""]
    if p.events:
        lines += ["### Activity", "```", *p.events, "```", ""]
    if p.positions:
        lines += ["### Open positions", "| | Symbol | Entry | Last | Stop | Target | P&L |", "|---|---|---|---|---|---|---|"]
        for sym, pos in sorted(p.positions.items()):
            side, last = pos.get("side", 1), p.last_price.get(sym, pos["entry"])
            tgt = f"{pos['target']:.5g}" if pos.get("target") is not None else "trailing"
            lines.append(f"| {'LONG' if side == 1 else 'SHORT'} | {sym} | {pos['entry']:.5g} | {last:.5g} | "
                         f"{pos['stop']:.5g} | {tgt} | ${side * pos['units'] * (last - pos['entry']):,.2f} |")
        lines.append("")
    if p.watch:
        ranked = ranked_watch(p)
        hot = [f"**{sym}** ({w['atr_away']:.1f} ATR)" for sym, w in ranked if w["atr_away"] <= 0.5]
        if hot:
            lines += ["🎯 **Closest to a setup:** " + " · ".join(hot), ""]
        lines += ["<details><summary>Key levels to watch (closest first)</summary>", "", "```",
                  *[f"{sym:<7} {w['note']}" for sym, w in ranked], "```", "</details>", ""]
    stale = stale_symbols(universe)
    if stale:
        lines += ["⚠️ Stale data (older than its peers, levels may lag): " +
                  ", ".join(f"{s} (last bar {d})" for s, d in stale), ""]
    lines.append("_Paper trading only. Not financial advice._")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def cmd_run(args):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    p = Portfolio.load(STATE)
    universe = load_universe(args.offline)
    p.run(universe, start_fresh_at_latest=True)
    if args.report:
        write_report(args.report, p, universe)
    if not p.scanned:
        print("No new bars to process.")
    else:
        print(f"Scanned {p.scanned} new bar(s) across {len(universe)} symbols.")
        print("\n".join(p.events) or "No buy or sell setups today. Standing aside.")
        if p.watch:
            print("\nKey levels, closest first (distance to the nearest level in ATRs):")
            for sym, w in ranked_watch(p):
                print(f"  {sym:<7} {w['note']}")
        for sym, d in stale_symbols(universe):
            print(f"  warning: {sym} data is stale (last bar {d})")
    p.save(STATE)
    p.append_trades(TRADES)
    print_status(p)


def cmd_status(_):
    print_status(Portfolio.load(STATE))


def cmd_backtest(_):
    p = Portfolio()
    p.run(load_universe(offline=True), start_fresh_at_latest=False)
    trades = p.closed
    wins = [t for t in trades if t["pnl"] > 0]
    print("\n".join(p.events))
    print(f"\nClosed trades: {len(trades)}   Win rate: {len(wins) / len(trades):.0%}" if trades else "\nNo closed trades.")
    if trades:
        print(f"Average R: {sum(t['r_multiple'] for t in trades) / len(trades):+.2f}")
    print_status(p)


def main():
    parser = argparse.ArgumentParser(prog="trader")
    sub = parser.add_subparsers(dest="cmd", required=True)
    parser.add_argument("--strategy", choices=["sr", "trend", "both"],
                        help="override config.STRATEGY (sr = support & resistance)")
    run = sub.add_parser("run")
    run.add_argument("--offline", action="store_true")
    run.add_argument("--report", metavar="FILE", help="also write a markdown summary to FILE")
    sub.add_parser("status")
    sub.add_parser("backtest")
    sub.add_parser("moomoo-scan", help="moomoo paper bot: scan after the US close")
    ex = sub.add_parser("moomoo-execute", help="moomoo paper bot: send queued orders after the US open")
    ex.add_argument("--dry-run", action="store_true", help="show what would be sent, send nothing")
    sub.add_parser("moomoo-status", help="moomoo paper bot: short summary")
    args = parser.parse_args()
    if args.strategy:
        config.STRATEGY = args.strategy
    if args.cmd.startswith("moomoo-"):
        from . import moomoo_bot
        return {"moomoo-scan": moomoo_bot.cmd_scan, "moomoo-execute": moomoo_bot.cmd_execute,
                "moomoo-status": moomoo_bot.cmd_status}[args.cmd](args)
    {"run": cmd_run, "status": cmd_status, "backtest": cmd_backtest}[args.cmd](args)


if __name__ == "__main__":
    main()
