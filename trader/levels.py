"""Support and resistance levels, found from swing highs and lows.

A swing high is a bar whose high is the highest of the bars around it (a swing low is the
mirror image). Swing points that sit close together are merged into one zone; the more times
price has turned at a zone, the stronger it is.
"""

from dataclasses import dataclass

from . import config


@dataclass
class Zone:
    low: float
    high: float
    touches: int
    last_index: int     # bar index of the most recent swing point in the zone

    @property
    def mid(self):
        return (self.low + self.high) / 2


def swing_points(bars, n=None):
    """Return (index, price) for every confirmed swing high and swing low.

    A swing point needs `n` bars on each side, so the last `n` bars can't produce one yet.
    """
    n = n or config.PIVOT_BARS
    points = []
    for i in range(n, len(bars) - n):
        window = bars[i - n:i + n + 1]
        if bars[i]["high"] == max(b["high"] for b in window):
            points.append((i, bars[i]["high"]))
        if bars[i]["low"] == min(b["low"] for b in window):
            points.append((i, bars[i]["low"]))
    return points


def find_zones(bars, atr_value, min_touches=None):
    """Cluster swing points lying within ZONE_MERGE_ATR of each other into zones, sorted low to high."""
    min_touches = min_touches or config.MIN_TOUCHES
    points = sorted(swing_points(bars), key=lambda p: p[1])
    tolerance = config.ZONE_MERGE_ATR * atr_value
    clusters = []
    for idx, price in points:
        if clusters and price - clusters[-1][-1][1] <= tolerance and price - clusters[-1][0][1] <= 2 * tolerance:
            clusters[-1].append((idx, price))
        else:
            clusters.append([(idx, price)])
    zones = [Zone(min(p for _, p in c), max(p for _, p in c), len(c), max(i for i, _ in c)) for c in clusters]
    return [z for z in zones if z.touches >= min_touches]


def nearest(zones, price):
    """Return (closest zone below price, closest zone above price); either may be None."""
    below = [z for z in zones if z.high < price]
    above = [z for z in zones if z.low > price]
    return (below[-1] if below else None, above[0] if above else None)
