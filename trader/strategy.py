"""Entry rules: only buy in an established uptrend, either on a pullback that turns up or on a volume breakout."""

from dataclasses import dataclass

from . import config
from .indicators import atr, rsi, sma


@dataclass
class Signal:
    symbol: str
    asset_class: str
    date: str
    setup: str          # "pullback" or "breakout"
    close: float
    atr: float
    score: float        # higher is better; used to rank same-day signals
    reason: str


def evaluate(symbol, asset_class, bars):
    """Return a Signal if the last bar in `bars` is a buy setup, else None.

    `bars` is a list of dicts with date/open/high/low/close/volume, oldest first.
    """
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

    momentum = (c - closes[i - 20]) / closes[i - 20]
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
                      reason=f"20-day breakout above {prior_high:.4g}{vol_note}, RSI {rsi14[i]:.0f}")
    return None


def trend_broken(bars):
    """Exit rule: close below the 50-day average."""
    closes = [b["close"] for b in bars]
    s = sma(closes, 50)[-1]
    return s is not None and closes[-1] < s


def watch_note(bars):
    """For a symbol in an uptrend with no signal yet, describe what would trigger a buy."""
    if len(bars) < config.MIN_BARS:
        return None
    closes = [b["close"] for b in bars]
    s20, s50 = sma(closes, 20)[-1], sma(closes, 50)[-1]
    if not (closes[-1] > s50 and s20 > s50):
        return None
    r = rsi(closes, 14)[-1]
    trigger = max(b["high"] for b in bars[-20:])
    vol = " on 1.5x volume" if bars[-1].get("volume") else ""
    note = f"breakout needs close > {trigger:.5g} ({trigger / closes[-1] - 1:+.1%}){vol}"
    if r >= 75:
        note += ", but RSI too hot to chase"
    return f"RSI {r:.0f}, {note}; or a dip to RSI <= 40 that turns up"
