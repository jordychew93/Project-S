"""Arena orchestration: stage 1 backtest, stage 2 forward update, results file and weekly leaderboard.

State lives in state/arena.json (forward books) and state/arena_backtest.json (stage 1 scorecards).
The baseline bot's files (state/portfolio.json, state/moomoo_*) are never read or written here.
Promotion is only ever REPORTED; nothing here changes what trades live.
"""

import json
import os
from datetime import date as _date, datetime, timedelta, timezone

from .. import config
from . import market as mk
from .book import metrics
from .playbook import ALL_OFF, ALL_ON, FLAG_GROUPS
from .strategies import KEYS, PlaybookRunner, lineup

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE = os.path.join(ROOT, "state", "arena.json")
BACKTEST = os.path.join(ROOT, "state", "arena_backtest.json")
BACKTEST_CURVES = os.path.join(ROOT, "state", "arena_backtest_curves.json")   # daily equity + closed trades (UI)
RESULTS_MD = os.path.join(ROOT, "ARENA_RESULTS.md")
EARNINGS = os.path.join(mk.CACHE_DIR, "earnings.json")

BACKTEST_START = "2022-01-03"
MAX_DD = 0.20
MIN_TRADES = 30
SHORT = {"baseline": "Project T as is", "playbook": "Project T + playbook", "breakout": "Breakout",
         "meanrev": "Dip buying", "rotation": "Monthly rotation", "spy": "SPY buy & hold"}
PLAIN_REASON = {"trailed SPY": "made less than SPY", f"drawdown over {MAX_DD:.0%}": f"fell over {MAX_DD:.0%} from a peak"}


def load_earnings(path=EARNINGS):
    """{symbol: [report dates]} if an earnings file exists, else {} (the blackout rule is then inactive)."""
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


# ----- stage 1: backtest -------------------------------------------------------------------------
def run_backtest(market, start=BACKTEST_START, end=None, runners=None):
    runners = runners if runners is not None else lineup(load_earnings())
    for d in market.dates():
        if d < start or (end and d > end):
            continue
        for r in runners:
            r.step(d, market)
    return runners


def scorecards(runners):
    by_key = {r.key: r for r in runners}
    spy = by_key.get("spy")
    spy_ret = metrics(spy.curve, spy.closed)["total_return"] if spy else None
    return {r.key: metrics(r.curve, r.closed, spy_return=spy_ret) for r in runners}


def stage1_verdict(key, m, spy_m):
    """(survived, reasons). A strategy is dropped if it trails SPY or its max drawdown exceeds 20%."""
    if key == "spy":
        return True, ["benchmark"]
    reasons = []
    if m["total_return"] < spy_m["total_return"]:
        reasons.append("trailed SPY")
    if m["max_drawdown"] > MAX_DD:
        reasons.append(f"drawdown over {MAX_DD:.0%}")
    return not reasons, reasons


def ablation(market, start=BACKTEST_START, earnings=None):
    """Project T with each playbook flag group on its own, then all on (for the results file)."""
    variants = [("all flags off (= Project T)", ALL_OFF)]
    variants += [(name, {**ALL_OFF, **flags}) for name, flags in FLAG_GROUPS.items()]
    variants += [("all flags on", ALL_ON)]
    runners = [PlaybookRunner(flags=f, earnings=earnings, key=f"v{i}", name=n) for i, (n, f) in enumerate(variants)]
    run_backtest(market, start, runners=runners)
    return [(r.name, metrics(r.curve, r.closed)) for r in runners]


# ----- formatting helpers --------------------------------------------------------------------------
def pct(x, digits=1):
    return "n/a" if x is None else f"{x:+.{digits}%}".replace("-", "−")


def money(x):
    return "n/a" if x is None else ("−" if x < 0 else "") + f"${abs(x):,.0f}"


def pts(x):
    return "n/a" if x is None else f"{100 * x:+.1f} pts".replace("-", "−")


def num(x, fmt="{:.2f}"):
    return "n/a" if x is None else fmt.format(x).replace("-", "−")


def results_markdown(cards, verdicts, start, end, ablation_rows=None, earnings_active=False):
    names = {r.key: r.name for r in lineup()}
    lines = [
        "# Strategy arena: stage 1 backtest", "",
        f"Daily bars from moomoo (front-adjusted), **{start} to {end}**. Each strategy runs its own **virtual "
        f"$100,000 book**. Signals use a day's close and fill at the next open with {config.SLIPPAGE['stock']:.2%} "
        "slippage. No commissions. Paper/virtual only: nothing here places orders.", "",
        f"Stage 1 rule: a strategy is **dropped** if it trails buy-and-hold SPY **or** its max drawdown exceeds "
        f"{MAX_DD:.0%}. Win rate is shown but not used to rank.", "",
        "| Strategy | Total return | vs SPY (% points) | CAGR | Max drawdown | Sharpe | Trades | Win rate | Expectancy ($/trade) "
        "| Avg R | Profit factor | Stage 1 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for k in KEYS:
        if k not in cards:
            continue
        m = cards[k]
        ok, why = verdicts[k]
        verdict = "benchmark" if k == "spy" else ("**survived**" if ok else "dropped: " + ", ".join(why))
        wr = "n/a" if m["win_rate"] is None else f"{m['win_rate']:.0%}"
        lines.append(
            f"| {names[k]} | {pct(m['total_return'])} | {pts(m['vs_spy']) if k != 'spy' else '—'} | {pct(m['cagr'])} | "
            f"{pct(-m['max_drawdown'])} | {num(m['sharpe'])} | {m['trades']} | {wr} | {money(m['expectancy'])} | "
            f"{num(m['avg_r'], '{:+.2f}')} | {num(m['profit_factor'])} | {verdict} |")
    survivors = [names[k] for k in KEYS if k in verdicts and k != "spy" and verdicts[k][0]]
    lines += ["", f"**Survivors:** {', '.join(survivors) if survivors else 'none'}.", ""]
    lines += [
        "## Notes", "",
        "- Universe: Project T's US stocks (" + ", ".join(mk.STOCKS) + "). Rotation uses SPY, QQQ, GLD, TLT and "
        "BIL as cash. Project T's crypto/FX legs are left out: moomoo has no long history for them.",
        "- Project T and the playbook variant see the same ~276-bar window the live moomoo bot sees each day.",
        "- Expectancy is the average dollar P&L per closed trade; Avg R only exists for strategies with a stop. "
        "Open positions at the end count in the return, not in the trade stats. SPY buy & hold has no closed trades.",
        "- Sharpe uses daily returns, annualised, risk-free rate 0.",
        "- Playbook earnings blackout: " + ("active (earnings dates from data/arena/earnings.json)." if earnings_active
                                            else "**inactive**: no earnings-date source is configured "
                                                 "(moomoo's quote API has none and no Alpha Vantage key is set)."),
        "- Scaling in is simplified: 1/3 at the open, then limit adds 1/3 and 2/3 of the way to the stop.",
        "",
    ]
    if ablation_rows:
        lines += ["## Playbook rules one at a time (Project T + one flag)", "",
                  "| Variant | Total return | Max drawdown | Sharpe | Trades | Win rate | Expectancy ($/trade) | Avg R |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for name, m in ablation_rows:
            wr = "n/a" if m["win_rate"] is None else f"{m['win_rate']:.0%}"
            lines.append(f"| {name} | {pct(m['total_return'])} | {pct(-m['max_drawdown'])} | {num(m['sharpe'])} | "
                         f"{m['trades']} | {wr} | {money(m['expectancy'])} | {num(m['avg_r'], '{:+.2f}')} |")
        lines.append("")
    lines += ["Promotion rule (reported only, never switched automatically): at least 30 closed trades, and beats "
              "Project T on expectancy and on max drawdown in both the backtest and the forward paper run.", ""]
    return "\n".join(lines)


def write_curves(runners, path=BACKTEST_CURVES):
    """Daily equity curve and closed trades per strategy, for the arena UI replay."""
    _write_json(path, {r.key: {"curve": r.curve, "closed": r.closed} for r in runners})


def cmd_backtest(args):
    mkt = mk.Market(mk.load_market("hist", refresh=args.refresh or not os.path.exists(mk.cache_path("hist", "SPY"))))
    missing = [s for s in mk.SYMBOLS if s not in mkt.bars]
    if missing:
        raise SystemExit(f"arena-backtest: no data for {', '.join(missing)}")
    earnings = load_earnings()
    runners = run_backtest(mkt, args.start)
    write_curves(runners)
    cards = scorecards(runners)
    verdicts = {k: stage1_verdict(k, m, cards["spy"]) for k, m in cards.items()}
    abl = ablation(mkt, args.start, earnings) if args.ablation else None
    start, end = cards["spy"]["start"], cards["spy"]["end"]
    _write_json(BACKTEST, {"start": start, "end": end, "cards": cards,
                           "verdicts": {k: {"survived": v[0], "reasons": v[1]} for k, v in verdicts.items()},
                           "ablation": abl, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    md = results_markdown(cards, verdicts, start, end, abl, earnings_active=bool(earnings))
    with open(RESULTS_MD, "w") as f:
        f.write(md)
    print(md)


# ----- stage 2: forward virtual books --------------------------------------------------------------
def load_forward(path=STATE, earnings=None):
    runners = {r.key: r for r in lineup(earnings)}
    if not os.path.exists(path):
        return runners, None
    with open(path) as f:
        st = json.load(f)
    for k, book in st["books"].items():
        if k in runners:
            runners[k].load_state(book)
    return runners, st


def update_forward(market, path=STATE, earnings=None, now=None):
    """Advance every forward book through each new trading day. Idempotent: days already done are skipped.

    Returns the list of dates processed. A fresh state starts all books at the latest close.
    """
    runners, st = load_forward(path, earnings)
    latest = market.latest()
    if latest is None:
        raise RuntimeError("no SPY bars")
    if st is None:
        st = {"version": 1, "start": latest, "last_date": None}
        dates = [latest]
    else:
        dates = [d for d in market.dates() if d > st["last_date"]]
    for d in dates:
        for r in runners.values():
            r.step(d, market)
    if dates:
        st["last_date"] = dates[-1]
    st["updated"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    st["books"] = {k: r.to_state() for k, r in runners.items()}
    _write_json(path, st)
    return dates


def cmd_update(args):
    mkt = mk.Market(mk.load_market("live", refresh=not args.offline))
    missing = [s for s in mk.SYMBOLS if s not in mkt.bars]
    if missing:
        raise SystemExit(f"arena-update: no data for {', '.join(missing)}")
    dates = update_forward(mkt, earnings=load_earnings())
    print(f"arena-update: {len(dates)} new day(s){' to ' + dates[-1] if dates else ''}")


# ----- promotion + leaderboard ---------------------------------------------------------------------
def beats(m, base):
    exp_ok = m["expectancy"] is not None and (base["expectancy"] is None or m["expectancy"] > base["expectancy"])
    return exp_ok and m["max_drawdown"] < base["max_drawdown"]


def promotion_candidates(bt, fwd):
    """Keys that satisfy the promotion rule. Reporting only: nothing is switched."""
    out = []
    for k in KEYS:
        if k in ("baseline", "spy") or k not in bt.get("cards", {}) or k not in fwd:
            continue
        b, f = bt["cards"][k], fwd[k]
        if not bt["verdicts"][k]["survived"]:
            continue
        if b["trades"] >= MIN_TRADES and f["trades"] >= MIN_TRADES and beats(b, bt["cards"]["baseline"]) \
                and beats(f, fwd["baseline"]):
            out.append(k)
    return out


def _nice_date(d):
    x = _date.fromisoformat(d)
    return f"{x.day} {x.strftime('%b')}"


def _week_change(curve, today):
    if not curve:
        return 0.0
    cutoff = (_date.fromisoformat(today) - timedelta(days=7)).isoformat()
    before = [e for d, e in curve if d <= cutoff]
    ref = before[-1] if before else config.STARTING_CASH
    return curve[-1][1] / ref - 1


def leaderboard(st, bt, runners):
    """Plain-text Telegram message for Jordy."""
    if st is None:
        return "🏁 Strategy arena: forward paper test hasn't started yet (run arena-update first).\n\n" \
               "🔒 Paper only: virtual money, no orders placed."
    today, start = st["last_date"], st["start"]
    spy_ret = metrics(runners["spy"].curve, runners["spy"].closed)["total_return"]
    fwd = {k: metrics(r.curve, r.closed, spy_return=spy_ret) for k, r in runners.items()}
    ranked = sorted(KEYS, key=lambda k: fwd[k]["equity"], reverse=True)
    leader = ranked[0]
    week = {k: _week_change(runners[k].curve, today) for k in KEYS}
    days = len(runners["spy"].curve)

    moved = days > 1 and len({fwd[k]["equity"] for k in KEYS}) > 1
    if moved:
        head = (f"🏁 Strategy arena, week to {_nice_date(today)}: {SHORT[leader]} leads at "
                f"{pct(fwd[leader]['total_return'])} since {_nice_date(start)} (virtual money)")
    else:
        head = f"🏁 Strategy arena: forward paper test started {_nice_date(start)}, no results yet (virtual money)"
    lines = [head, ""]
    lines.append(f"📊 **Standings since {_nice_date(start)}** ({days} trading day{'s' if days != 1 else ''})")
    for i, k in enumerate(ranked[:3], 1):
        lines.append(f"• {i}. {SHORT[k]}: {money(fwd[k]['equity'])} ({pct(fwd[k]['total_return'])})")
    lines += ["", "🐢 **Further back**"]
    for i, k in enumerate(ranked[3:], 4):
        lines.append(f"• {i}. {SHORT[k]}: {money(fwd[k]['equity'])} ({pct(fwd[k]['total_return'])})")

    week_ago = (_date.fromisoformat(today) - timedelta(days=7)).isoformat()
    week_trades = sum(1 for r in runners.values() for t in r.closed if t["exit_date"] > week_ago)
    open_now = sum(len(runners[k].open_positions()) for k in KEYS if k != "spy")
    lines += ["", "📅 **This week**",
              f"• {week_trades} trade{'s' if week_trades != 1 else ''} closed across all books"]
    if moved:
        best = max(KEYS, key=lambda k: week[k])
        worst = min(KEYS, key=lambda k: week[k])
        lines += [f"• Best week: {SHORT[best]} ({pct(week[best])})",
                  f"• Weakest week: {SHORT[worst]} ({pct(week[worst])})"]
    else:
        lines.append("• Every book still holds its starting $100,000")
    lines.append(f"• {open_now} open position{'s' if open_now != 1 else ''} across the five strategies")

    if bt:
        v = bt["verdicts"]
        passed = [SHORT[k] for k in KEYS if k != "spy" and k in v and v[k]["survived"]]
        dropped = [f"{SHORT[k]} ({' and '.join(PLAIN_REASON.get(x, x) for x in v[k]['reasons'])})" for k in KEYS
                   if k != "spy" and k in v and not v[k]["survived"]]
        y0, y1 = bt["start"][:4], bt["end"][:4]
        lines += ["", f"🧪 **Backtest check ({y0} to {y1})**",
                  f"• SPY made {pct(bt['cards']['spy']['total_return'], 0)} over that stretch; "
                  f"a strategy had to beat that without falling over {MAX_DD:.0%}",
                  "• Passed: " + (", ".join(passed) if passed else "none")]
        if dropped:
            lines.append("• Dropped: " + "; ".join(dropped[:3]) + (f" (+{len(dropped) - 3} more)" if len(dropped) > 3 else ""))

    cands = promotion_candidates(bt, fwd) if bt else []
    lines += ["", "🎓 **Promotion check**"]
    if cands:
        lines.append("• Candidate: " + ", ".join(SHORT[k] for k in cands) +
                     ": beat Project T on average profit per trade and on worst drop, in both tests")
        lines.append("• Nothing switches automatically: your call")
    else:
        most = max((k for k in KEYS if k not in ("baseline", "spy")), key=lambda k: fwd[k]["trades"])
        so_far = (f"most so far is {SHORT[most]} with {fwd[most]['trades']}" if fwd[most]["trades"]
                  else "none has closed one yet")
        lines.append(f"• No candidate yet. A strategy needs {MIN_TRADES}+ closed paper trades; {so_far}")
        lines.append("• It must also beat Project T on profit per trade and on worst drop")
    lines += ["", "🔒 Paper only: each strategy is a virtual $100k book and places no orders. "
                  "Only the original bot trades the moomoo paper account."]
    return "\n".join(lines)


def cmd_leaderboard(_args):
    runners, st = load_forward()
    bt = None
    if os.path.exists(BACKTEST):
        with open(BACKTEST) as f:
            bt = json.load(f)
    print(leaderboard(st, bt, runners))
