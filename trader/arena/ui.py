"""Arena UI: a playful, self-contained HTML page (arena-ui/index.html) plus its data (arena-ui/data.json).

Each strategy is a blob character. Its body size follows its money; its health follows its fall from its
own best-ever balance. Read-only: this module only reads the arena's own JSON files and writes the page.

    python -m trader arena-ui      regenerate arena-ui/data.json and arena-ui/index.html
"""

import json
import os
from datetime import datetime, timezone

from .. import config
from . import runner as rn
from .strategies import KEYS, lineup

UI_DIR = os.path.join(rn.ROOT, "arena-ui")
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_template.html")
PLACEHOLDER = "/*__ARENA_DATA__*/null"

DEAD_DRAWDOWN = rn.MAX_DD        # 20% fall from the book's own peak: the arena's stage 1 kill rule
DEAD_FLOOR = 0.50                # or money down to half the starting $100k


def health_track(equities, start_cash=config.STARTING_CASH, dead_dd=DEAD_DRAWDOWN, dead_floor=DEAD_FLOOR):
    """Health per day for one book, plus the index of the day it died (None if it never did).

    peak     = the best balance so far (never below the starting cash)
    drawdown = 1 - equity / peak
    health   = 100 at a new high, falling in a straight line to 0 at a 20% drawdown
    dead     = drawdown >= 20%, or equity <= 50% of the start. Death is permanent for that run.
    Returns (health list of ints, drawdown list of floats, dead index or None).
    """
    peak, dead = start_cash, None
    health, dds = [], []
    for i, e in enumerate(equities):
        peak = max(peak, e)
        dd = 1 - e / peak if peak > 0 else 1.0
        if dead is None and (dd >= dead_dd - 1e-12 or e <= dead_floor * start_cash):
            dead = i
        h = 0 if dead is not None else max(0, min(100, round(100 * (1 - dd / dead_dd))))
        health.append(h)
        dds.append(round(dd, 5))
    return health, dds, dead


def _run_block(curves, start=None, end=None):
    """{dates, books: {key: {equity, health, drawdown, dead, trades}}} aligned on one shared date axis."""
    dates = sorted({d for c in curves.values() for d, _ in c["curve"]})
    books = {}
    for k in KEYS:
        if k not in curves:
            continue
        by_date = dict((d, e) for d, e in curves[k]["curve"])
        eq, last = [], config.STARTING_CASH
        for d in dates:
            last = by_date.get(d, last)
            eq.append(round(last, 2))
        health, dds, dead = health_track(eq)
        idx = {d: i for i, d in enumerate(dates)}
        trades = [[idx[t["exit_date"]], round(t["pnl"], 2), t.get("symbol", "")]
                  for t in curves[k]["closed"] if t.get("exit_date") in idx]
        books[k] = {"equity": eq, "health": health, "drawdown": dds, "dead": dead, "trades": trades}
    return {"dates": dates, "books": books}


def build_data(curves_path=rn.BACKTEST_CURVES, backtest_path=rn.BACKTEST, state_path=rn.STATE):
    names = {r.key: r.name for r in lineup()}
    blurbs = {r.key: r.blurb for r in lineup()}
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "start_cash": config.STARTING_CASH,
           "rule": {"dead_drawdown": DEAD_DRAWDOWN, "dead_floor": DEAD_FLOOR},
           "strategies": [{"key": k, "name": names[k], "short": rn.SHORT[k], "blurb": blurbs[k]} for k in KEYS],
           "backtest": None, "forward": None}
    if os.path.exists(curves_path):
        with open(curves_path) as f:
            block = _run_block(json.load(f))
        if os.path.exists(backtest_path):
            with open(backtest_path) as f:
                bt = json.load(f)
            block["verdicts"] = bt.get("verdicts")
            block["cards"] = {k: {x: c.get(x) for x in ("total_return", "max_drawdown", "trades", "sharpe")}
                              for k, c in bt.get("cards", {}).items()}
        out["backtest"] = block
    runners, st = rn.load_forward(state_path)
    if st is not None:
        block = _run_block({k: {"curve": r.curve, "closed": r.closed} for k, r in runners.items()})
        block["start"], block["updated"] = st.get("start"), st.get("updated")
        block["open"] = {k: r.open_positions() for k, r in runners.items()}
        out["forward"] = block
    return out


def render(data, template=TEMPLATE):
    with open(template) as f:
        html = f.read()
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    if PLACEHOLDER not in html:
        raise RuntimeError("arena UI template has no data placeholder")
    return html.replace(PLACEHOLDER, blob)


def write_ui(out_dir=UI_DIR, **kw):
    data = build_data(**kw)
    os.makedirs(out_dir, exist_ok=True)
    rn._write_json(os.path.join(out_dir, "data.json"), data)
    path = os.path.join(out_dir, "index.html")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(render(data))
    os.replace(tmp, path)
    return path, data


def cmd_ui(args):
    if not os.path.exists(rn.BACKTEST_CURVES):
        print("arena-ui: no backtest curves yet; run `python -m trader arena-backtest` once (the replay is skipped)")
    path, _ = write_ui()
    if not getattr(args, "quiet", False):
        print(f"arena-ui: wrote {path}")
