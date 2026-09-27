"""Daily OHLCV data from Alpha Vantage, cached as CSV files under data/."""

import csv
import io
import os
from datetime import datetime, timezone

import requests

API_URL = "https://www.alphavantage.co/query"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def cache_path(asset_class, symbol):
    return os.path.join(DATA_DIR, f"{asset_class}_{symbol}.csv")


def parse_csv(text):
    """Parse Alpha Vantage CSV (newest first) into bars sorted oldest first."""
    bars = []
    for row in csv.DictReader(io.StringIO(text.strip())):
        bars.append({
            "date": row["timestamp"][:10],
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row.get("volume") or 0.0),
        })
    bars.sort(key=lambda b: b["date"])
    return bars


def drop_incomplete(bars, asset_class, now=None):
    """Drop today's bar if it is still forming. US stocks are treated as closed after 21:00 UTC."""
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    if bars and bars[-1]["date"] >= today and (asset_class != "stock" or now.hour < 21):
        return bars[:-1]
    return bars


def load_cached(asset_class, symbol):
    path = cache_path(asset_class, symbol)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return drop_incomplete(parse_csv(f.read()), asset_class)


def fetch(asset_class, symbol, api_key):
    """Download recent daily bars and refresh the cache."""
    if asset_class == "stock":
        params = {"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "compact"}
    elif asset_class == "crypto":
        params = {"function": "DIGITAL_CURRENCY_DAILY", "symbol": symbol, "market": "USD"}
    elif asset_class == "fx":
        params = {"function": "FX_DAILY", "from_symbol": symbol[:3], "to_symbol": symbol[3:], "outputsize": "compact"}
    else:
        raise ValueError(f"unknown asset class {asset_class}")
    params.update(apikey=api_key, datatype="csv")
    resp = requests.get(API_URL, params=params, timeout=30)
    resp.raise_for_status()
    text = resp.text
    if not text.startswith("timestamp"):
        raise RuntimeError(f"{symbol}: unexpected response: {text[:200]}")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(cache_path(asset_class, symbol), "w") as f:
        f.write(text.replace("\r\n", "\n"))
    return drop_incomplete(parse_csv(text), asset_class)
