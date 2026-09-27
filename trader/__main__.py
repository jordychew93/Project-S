"""Command line entry point.

    python -m trader run            fetch fresh data (needs ALPHAVANTAGE_API_KEY), trade, save state
    python -m trader run --offline  trade using the cached CSVs in data/
    python -m trader status         show open positions, pending orders and equity
    python -m trader backtest       replay the cached history from a fresh $100k account
    python -m trader --strategy trend run    pick a strategy: sr (default), trend or both
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
                    time.sleep(1)   # stay under the free-tier rate limit
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


def cmd_run(args):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    p = Portfolio.load(STATE)
    universe = load_universe(args.offline)
    p.run(universe, start_fresh_at_latest=True)
    if not p.scanned:
        print("No new bars to process.")
    else:
        print(f"Scanned {p.scanned} new bar(s) across {len(universe)} symbols.")
        print("\n".join(p.events) or "No buy or sell setups today. Standing aside.")
        if p.watch:
            print("\nKey levels (nearest support below / resistance above):")
            for sym, note in sorted(p.watch.items()):
                print(f"  {sym:<7} {note}")
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
    sub.add_parser("status")
    sub.add_parser("backtest")
    args = parser.parse_args()
    if args.strategy:
        config.STRATEGY = args.strategy
    {"run": cmd_run, "status": cmd_status, "backtest": cmd_backtest}[args.cmd](args)


if __name__ == "__main__":
    main()
