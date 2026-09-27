"""Draws the explainer charts used in the strategy write-up. Run: python docs/make_charts.py (needs matplotlib)."""
import sys, datetime as dt
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from trader import data
from trader.indicators import sma, rsi, atr

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "img")
SURF, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
BLUE, ORANGE, VIOLET = "#2a78d6", "#eb6834", "#4a3aa7"
GOOD, CRIT = "#0ca30c", "#d03b3b"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": GRID,
    "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "axes.facecolor": SURF,
    "figure.facecolor": SURF, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 2})

def load(ac, s):
    b = data.load_cached(ac, s)
    return b, [dt.date.fromisoformat(x["date"]) for x in b]

def title(fig, t, sub):
    fig.text(0.06, 0.965, t, fontsize=16, weight="bold", color=INK, va="top")
    fig.text(0.06, 0.915, sub, fontsize=11, color=INK2, va="top")

def fmt(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0, interval=2))

def endlabel(ax, x, y, text, color):
    ax.annotate(text, (x, y), xytext=(6, 0), textcoords="offset points", color=INK, fontsize=10,
                va="center", bbox=dict(boxstyle="round,pad=0.2", fc=SURF, ec=color, lw=1.5))

# 1. Trend filter on SPY
b, d = load("stock", "SPY"); c = [x["close"] for x in b]
s20, s50 = sma(c, 20), sma(c, 50)
fig, ax = plt.subplots(figsize=(10, 5.2)); fig.subplots_adjust(top=0.82, left=0.07, right=0.88, bottom=0.1)
title(fig, "Step 1 · Only trade with the trend", "SPY: green shading = all three uptrend rules pass (price > 50-day, 20-day > 50-day, 50-day rising)")
mask = [i >= 60 and c[i] > s50[i] and s20[i] > s50[i] and s50[i] > s50[i - 10] for i in range(len(c))]
ax.fill_between(d, 0, 1, where=mask, step="mid", color=GOOD, alpha=0.13, lw=0, transform=ax.get_xaxis_transform(), label="Uptrend: OK to buy")
ax.plot(d, c, color=BLUE, label="Price"); ax.plot(d, s20, color=ORANGE, label="20-day avg")
ax.plot(d, s50, color=VIOLET, label="50-day avg")
for s, lab, col in [(c, "Price", BLUE), (s20, "20-day", ORANGE), (s50, "50-day", VIOLET)]:
    endlabel(ax, d[-1], s[-1], lab, col)
ax.legend(loc="lower left", frameon=False, ncol=4); fmt(ax)
fig.savefig(f"{OUT}/1_trend.png", dpi=150); plt.close(fig)

# 2. Pullback on AMZN (signal 21 Sep)
b, d = load("stock", "AMZN"); c = [x["close"] for x in b]; hi = [x["high"] for x in b]
r = rsi(c); s50 = sma(c, 50); k = next(i for i, x in enumerate(b) if x["date"] == "2026-09-21"); lo = k - 55
fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
fig.subplots_adjust(top=0.84, left=0.08, right=0.97, bottom=0.08, hspace=0.12)
title(fig, "Trigger A · The pullback bounce", "AMZN, 21 Sep 2026: RSI dipped below 40, then price closed above the prior day's high")
a1.plot(d[lo:], c[lo:], color=BLUE, label="Price"); a1.plot(d[lo:], s50[lo:], color=VIOLET, label="50-day avg")
a1.scatter([d[k]], [c[k]], s=90, color=GOOD, zorder=5, edgecolor=SURF, linewidth=2)
a1.annotate("BUY signal\ncloses above\nyesterday's high", (d[k], c[k]), xytext=(-110, 30), textcoords="offset points",
            fontsize=10, color=INK, arrowprops=dict(arrowstyle="->", color=INK2))
a1.legend(loc="upper left", frameon=False, ncol=2); a1.set_ylabel("Price $")
a2.plot(d[lo:], r[lo:], color=ORANGE); a2.axhline(40, color=INK2, lw=1, ls="--")
a2.fill_between(d[lo:], r[lo:], 40, where=[x <= 40 for x in r[lo:]], color=ORANGE, alpha=0.25, lw=0)
a2.text(d[lo], 41.5, "40 = 'oversold for an uptrend'", fontsize=9, color=INK2)
m = min(range(k - 5, k + 1), key=lambda j: r[j])
a2.scatter([d[m]], [r[m]], s=50, color=ORANGE, zorder=5, edgecolor=SURF, linewidth=1.5)
a2.annotate(f"dip to {r[m]:.0f}", (d[m], r[m]), xytext=(-70, -12), textcoords="offset points", fontsize=9.5, color=INK,
            arrowprops=dict(arrowstyle="->", color=INK2)); a2.set_ylabel("RSI"); a2.set_ylim(20, 80)
fmt(a2); fig.savefig(f"{OUT}/2_pullback.png", dpi=150); plt.close(fig)

# 3. Breakout on NVDA (signal 27 Aug)
b, d = load("stock", "NVDA"); c = [x["close"] for x in b]; hi = [x["high"] for x in b]; v = [x["volume"] / 1e6 for x in b]
k = next(i for i, x in enumerate(b) if x["date"] == "2026-08-27"); lo = k - 40; hiend = k + 8
ceil = [max(hi[i - 20:i]) for i in range(lo, hiend)]; avgv = [1.5 * sum(v[i - 20:i]) / 20 for i in range(lo, hiend)]
fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
fig.subplots_adjust(top=0.84, left=0.08, right=0.97, bottom=0.08, hspace=0.12)
title(fig, "Trigger B · The volume breakout", "NVDA, 27 Aug 2026: closed above its 20-day high on 2.6x normal volume")
a1.plot(d[lo:hiend], c[lo:hiend], color=BLUE, label="Price")
a1.step(d[lo:hiend], ceil, where="mid", color=INK2, lw=1.5, ls="--", label="Prior 20-day high (the 'ceiling')")
a1.scatter([d[k]], [c[k]], s=90, color=GOOD, zorder=5, edgecolor=SURF, linewidth=2)
a1.annotate("BUY signal\nbreaks the ceiling", (d[k], c[k]), xytext=(-130, 25), textcoords="offset points",
            fontsize=10, color=INK, arrowprops=dict(arrowstyle="->", color=INK2))
a1.legend(loc="lower right", frameon=False); a1.set_ylabel("Price $")
a2.bar(d[lo:hiend], v[lo:hiend], width=0.7, color=[BLUE if i != k else GOOD for i in range(lo, hiend)])
a2.plot(d[lo:hiend], avgv, color=INK2, lw=1.2, ls="--"); a2.text(d[lo], avgv[0] * 1.08, "1.5x average volume", fontsize=9, color=INK2)
a2.set_ylabel("Volume (M)"); fmt(a2); fig.savefig(f"{OUT}/3_breakout.png", dpi=150); plt.close(fig)

# 4. Trade lifecycle (illustrative)
import math
days = list(range(0, 36)); entry, A = 100.0, 2.5
path = [100, 99, 98, 99.5, 101, 102, 101.5, 103, 104.5, 104, 105.5, 107, 106, 108, 109.5, 109, 111, 112, 111, 113,
        114.5, 113.5, 115, 116, 114, 113, 112.5, 113, 111, 110.5, 109.8, 109, 108.5, 108.2, 108, 107.5]
stop, hh, stops, exit_day = entry - 2 * A, path[0], [], None
for i, p in enumerate(path):
    if exit_day is None and i > 0 and p <= stop:
        exit_day = i
    stops.append(stop)
    hh = max(hh, p + 0.8)
    if hh >= entry + 2 * A:
        stop = max(stop, entry, hh - 3 * A)
end = exit_day + 1
fig, ax = plt.subplots(figsize=(10, 5.4)); fig.subplots_adjust(top=0.82, left=0.07, right=0.95, bottom=0.1)
title(fig, "Life of a trade · how the stop protects you", "Illustration with ATR = $2.50. The red stop line only ever moves up.")
ax.plot(days[:end], path[:end], color=BLUE, label="Price")
ax.step(days[:end], stops[:end], where="post", color=CRIT, label="Stop-loss")
ax.axhline(entry, color=INK2, lw=1, ls=":"); ax.axhline(entry + 2 * A, color=INK2, lw=1, ls=":")
ax.text(0.3, entry + 0.4, "Entry $100", fontsize=9, color=INK2)
ax.text(0.3, entry + 2 * A + 0.4, "+1R = $105 (profit equal to the risk)", fontsize=9, color=INK2)
notes = [(0, 95, "① Buy at $100\nstop 2 ATR below = $95\n(risk: $5 per share)", (8, -38)),
         (11, 100, "② Hits +1R → stop jumps\nto breakeven. Can't lose now.", (8, -42)),
         (23, stops[23], "③ Stop trails 3 ATR\nbelow the highest high", (10, -75)),
         (exit_day, path[exit_day], "④ Price falls into the stop\n→ sell, locking in profit", (-40, 70))]
for x, y, t, off in notes:
    ax.annotate(t, (x, y), xytext=off, textcoords="offset points", fontsize=9.5, color=INK,
                arrowprops=dict(arrowstyle="->", color=INK2), bbox=dict(boxstyle="round,pad=0.3", fc=SURF, ec=GRID))
ax.set_xlabel("Trading days after entry"); ax.set_ylabel("Price $"); ax.legend(loc="upper left", frameon=False)
ax.set_ylim(90, 122); ax.set_xlim(-1, 34); fig.savefig(f"{OUT}/4_lifecycle.png", dpi=150); plt.close(fig)

# 5. Watchlist distance to trigger
rows = [("QQQ", .5), ("SPY", .5), ("MSFT", .6), ("AAPL", 1.3), ("SOL", 1.3), ("BTC", 3.5), ("META", 3.7),
        ("ETH", 4.1), ("NVDA", 4.3), ("XOM", 5.6)][::-1]
fig, ax = plt.subplots(figsize=(10, 5.4)); fig.subplots_adjust(top=0.82, left=0.1, right=0.95, bottom=0.1)
title(fig, "Who's closest to a buy?", "How far each uptrend asset must rise to trigger a breakout (as of 25 Sep 2026 close)")
ax.barh([r[0] for r in rows], [r[1] for r in rows], height=0.6, color=BLUE)
for i, (n, x) in enumerate(rows):
    ax.text(x + 0.08, i, f"+{x}%", va="center", fontsize=10, color=INK)
ax.grid(axis="y", visible=False); ax.set_xlabel("Rise needed to break the 20-day high (%)"); ax.set_xlim(0, 6.5)
fig.savefig(f"{OUT}/5_watchlist.png", dpi=150); plt.close(fig)
print("ok")

# 6. Support & resistance levels found automatically, small multiples
from trader import levels
picks = [("stock", "SPY"), ("stock", "QQQ"), ("stock", "NVDA"), ("crypto", "BTC"), ("fx", "EURUSD"), ("fx", "AUDUSD")]
fig, axes = plt.subplots(3, 2, figsize=(11, 10.5))
fig.subplots_adjust(top=0.865, left=0.07, right=0.97, bottom=0.05, hspace=0.38, wspace=0.18)
title(fig, "Support & resistance · levels the bot found by itself",
      "Green = support below price · red = resistance above · grey = price is inside the level · darker = more touches")
for ax, (ac, sym) in zip(axes.flat, picks):
    full, _ = load(ac, sym)
    fc = [x["close"] for x in full]
    a = atr([x["high"] for x in full], [x["low"] for x in full], fc, 14)[-1]
    zones = levels.find_zones(full, a)          # same levels the bot uses
    b, d = load(ac, sym); b, d = b[-90:], d[-90:]
    c = [x["close"] for x in b]
    lo, hi = min(x["low"] for x in b), max(x["high"] for x in b)
    for z in [z for z in zones if z.high >= lo - a and z.low <= hi + a]:
        col = GOOD if z.high < c[-1] else CRIT if z.low > c[-1] else INK2
        ax.axhspan(z.low - a * 0.05, z.high + a * 0.05, color=col, alpha=min(0.12 + 0.05 * z.touches, 0.45), lw=0)
    ax.plot(d, c, color=BLUE, lw=1.6)
    ax.scatter([d[-1]], [c[-1]], s=30, color=BLUE, zorder=5)
    ax.set_title(sym, loc="left", fontsize=12, weight="bold", color=INK)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b")); ax.xaxis.set_major_locator(mdates.MonthLocator())
fig.savefig(f"{OUT}/6_levels.png", dpi=150); plt.close(fig)
print("levels chart ok")
