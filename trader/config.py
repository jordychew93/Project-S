"""Watchlist and risk settings. Edit these to change what and how the bot trades."""

WATCHLIST = {
    "stock": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM"],
    "crypto": ["BTC", "ETH", "SOL"],
    # FX pairs must be quoted in USD so P&L is in dollars.
    "fx": ["EURUSD", "GBPUSD", "AUDUSD"],
}

STARTING_CASH = 100_000.0

# Risk per trade as a fraction of equity (distance from entry to initial stop).
RISK_PER_TRADE = 0.01
MAX_OPEN_POSITIONS = 8

# Largest position allowed, as a fraction of equity (no leverage is ever used).
MAX_NOTIONAL_PCT = {"stock": 0.20, "crypto": 0.10, "fx": 0.25}

# Simulated slippage applied to every fill, as a fraction of price.
SLIPPAGE = {"stock": 0.0005, "crypto": 0.0010, "fx": 0.0002}

# Strategy parameters.
ATR_PERIOD = 14
STOP_ATR = 2.0          # initial stop distance in ATRs
TRAIL_ATR = 3.0         # trailing stop distance below highest high since entry
BREAKEVEN_R = 1.0       # move stop to entry once price has gained this many R
TIME_STOP_BARS = 30     # exit if the trade hasn't reached BREAKEVEN_R by then
MIN_BARS = 60
