"""Entry rules.

Two strategies, picked with config.STRATEGY:
  trend: in an established trend, enter on a pullback that turns or on a volume breakout.
  sr:    trade support and resistance: bounce off a level, or retest a level that just broke.

Every rule is written for buying. Sell (short) setups are found by running the same rules on
the price series flipped upside down (see `flip`), so buys and sells are exact mirror images.
"""

from dataclasses import dataclass, replace

from . import config, levels
from .indicators import atr, rsi, sma


@dataclass
class Signal:
    symbol: str
    asset_class: str
    date: str
    setup: str          # "pullback", "breakout", "support_bounce", "breakout_retest" (sells: mirrored)
    close: float
    atr: float
    score: float        # higher is better; used to rank same-day signals
    reason: str
    side: int = 1       # 1 = buy, -1 = sell short
    stop: float = None  # explicit stop price; None means STOP_ATR x ATR from the fill
    target: float = None


SELL_NAMES = {"pullback": "rally_fade", "breakout": "breakdown", "support_bounce": "resistance_rejection",
              "breakout_retest": "breakdown_retest"}


def flip(bars):
    """Mirror a price series upside down: highs become lows and every price is negated."""
    return [{"date": b["date"], "open": -b["open"], "high": -b["low"], "low": -b["high"],
             "close": -b["close"], "volume": b.get("volume")} for b in bars]


def evaluate(symbol, asset_class, bars):
    """Return the best buy or sell Signal for the last bar in `bars`, or None.

    `bars` is a list of dicts with date/open/high/low/close/volume, oldest first.
    """
    finders = {"trend": [_trend_long], "sr": [_sr_long], "both": [_trend_long, _sr_long]}[config.STRATEGY]
    sides = [1, -1] if config.ALLOW_SHORTS else [1]
    found = []
    for side in sides:
        series = bars if side == 1 else flip(bars)
        for finder in finders:
            sig = finder(symbol, asset_class, series)
            if sig:
                found.append(sig if side == 1 else _unflip(sig))
    return max(found, key=lambda s: s.score) if found else None


def _unflip(sig):
    return replace(sig, side=-1, setup=SELL_NAMES[sig.setup], close=-sig.close,
                   stop=None if sig.stop is None else -sig.stop,
                   target=None if sig.target is None else -sig.target,
                   reason=sig.reason.replace("RSI low", "RSI high").replace("prior high", "prior low")
                                    .replace("uptrend", "downtrend").replace("bounce off level", "rejected at level")
                                    .replace("retest of broken level", "retest of broken support")
                                    .replace("breakout through", "breakdown through"))


def _trend_long(symbol, asset_class, bars):
    """Trend strategy, buy side."""
    if len(bars) < config.MIN_BARS:
        return None
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b.get("volume") or 0.0 for b in bars]

    sma20, sma50 = sma(closes, 20), sma(closes, 50)
    rsi14 = rsi(closes, 14)
    atr14 = atr(highs, lows, closes, config.ATR_PERIOD)

    i = len(bars) - 1
    c, a = closes[i], atr14[i]
    if a is None or sma50[i - 10] is None or a <= 0:
        return None

    # Trend filter: price above a rising 50-day average, with the 20-day above the 50-day.
    uptrend = c > sma50[i] and sma20[i] > sma50[i] and sma50[i] > sma50[i - 10]
    if not uptrend:
        return None
    # Don't chase: skip if price is stretched far above the 20-day average.
    if c > sma20[i] + 3 * a:
        return None

    momentum = (c - closes[i - 20]) / abs(closes[i - 20])
    trend_strength = (c - sma50[i]) / a

    # Setup A: RSI dipped to oversold-for-an-uptrend in the last 5 bars, and today closes above yesterday's high.
    recent_low_rsi = min(r for r in rsi14[i - 5:i + 1] if r is not None)
    if recent_low_rsi <= 40 and c > highs[i - 1] and rsi14[i] > rsi14[i - 1]:
        return Signal(symbol, asset_class, bars[i]["date"], "pullback", c, a,
                      score=2.0 + momentum,
                      reason=f"uptrend pullback: RSI low {recent_low_rsi:.0f}, closed above prior high")

    # Setup B: close above the prior 20-day high, on strong volume where volume exists, and not overbought.
    prior_high = max(highs[i - 20:i])
    avg_vol = sum(volumes[i - 20:i]) / 20
    volume_ok = avg_vol == 0 or volumes[i] >= 1.5 * avg_vol
    if c > prior_high and volume_ok and rsi14[i] < 75:
        vol_note = f", volume {volumes[i] / avg_vol:.1f}x avg" if avg_vol else ""
        return Signal(symbol, asset_class, bars[i]["date"], "breakout", c, a,
                      score=1.0 + momentum + 0.05 * trend_strength,
                      reason=f"20-day breakout through {abs(prior_high):.5g}{vol_note}, RSI {rsi14[i]:.0f}")
    return None


def _sr_long(symbol, asset_class, bars):
    """Support & resistance strategy, buy side.

    support_bounce:  price dips into a support zone and closes back above it with a strong candle.
    breakout_retest: price broke above a resistance zone recently, came back to test it from above
                     (old resistance acting as new support) and held.
    The target is the next zone above; the trade is skipped unless it pays at least MIN_REWARD_RISK.
    """
    if len(bars) < config.MIN_BARS:
        return None
    closes = [b["close"] for b in bars]
    atr14 = atr([b["high"] for b in bars], [b["low"] for b in bars], closes, config.ATR_PERIOD)
    sma50 = sma(closes, 50)
    i = len(bars) - 1
    bar, a = bars[i], atr14[i]
    if a is None or a <= 0 or sma50[i - 10] is None:
        return None
    # Don't buy support while the bigger picture is falling hard.
    if sma50[i] < sma50[i - 10] - 0.5 * a:
        return None
    rng = bar["high"] - bar["low"]
    strong_close = bar["close"] > bar["open"] and rng > 0 and (bar["close"] - bar["low"]) / rng >= 0.5
    if not strong_close:
        return None

    zones = levels.find_zones(bars[:i], a)   # levels as known before today
    recent_high = max(b["high"] for b in bars[i - 20:i])
    touch = config.TOUCH_ATR * a
    best = None
    for z in zones:
        tested = bar["low"] <= z.high + touch and bar["low"] >= z.low - a and bar["close"] > z.high
        if not tested:
            continue
        recent = closes[i - config.RETEST_LOOKBACK:i]
        earlier = closes[max(0, i - 40):i - config.RETEST_LOOKBACK]
        if any(c > z.high + touch for c in recent) and earlier and min(earlier) < z.low:
            setup, what = "breakout_retest", "retest of broken level"
        else:
            setup, what = "support_bounce", "bounce off level"
        stop = z.low - config.SR_STOP_ATR * a
        # The first obstacle overhead is the target: the next zone, or the recent 20-day high, whichever
        # is nearer. If price is already inside a zone, there's no room and the trade is skipped.
        overhead = [y.low for y in zones if y is not z and y.high > bar["close"]]
        overhead += [recent_high] if recent_high > bar["close"] else []
        target = min(overhead) if overhead else bar["close"] + config.DEFAULT_TARGET_R * (bar["close"] - stop)
        rr = (target - bar["close"]) / (bar["close"] - stop)
        if rr < config.MIN_REWARD_RISK:
            continue
        lo, hi = sorted((abs(z.low), abs(z.high)))
        sig = Signal(symbol, asset_class, bar["date"], setup, bar["close"], a,
                     score=3.0 + min(rr, 5) * 0.2 + z.touches * 0.1,
                     reason=f"{what} {lo:.5g}-{hi:.5g} ({z.touches} touches), reward:risk {rr:.1f}",
                     stop=stop, target=target)
        if best is None or sig.score > best.score:
            best = sig
    return best


def trend_broken(bars):
    """Exit rule: close below the 50-day average."""
    closes = [b["close"] for b in bars]
    s = sma(closes, 50)[-1]
    return s is not None and closes[-1] < s


def watch_note(bars):
    """Describe where price sits between its nearest support and resistance levels."""
    if len(bars) < config.MIN_BARS:
        return None
    closes = [b["close"] for b in bars]
    a = atr([b["high"] for b in bars], [b["low"] for b in bars], closes, config.ATR_PERIOD)[-1]
    if not a:
        return None
    c = closes[-1]
    below, above = levels.nearest(levels.find_zones(bars, a), c)
    parts = []
    if below:
        parts.append(f"support {below.low:.5g}-{below.high:.5g} ({(below.high - c) / c:+.1%}, {below.touches}x)")
    if above:
        parts.append(f"resistance {above.low:.5g}-{above.high:.5g} ({(above.low - c) / c:+.1%}, {above.touches}x)")
    return "  |  ".join(parts) or None
