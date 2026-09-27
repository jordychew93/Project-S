# Project-S: swing-trading paper bot

This bot scans US stocks, crypto and FX once a day for swing-trade setups. It **paper-trades** them from a $100,000 simulated account. It never places real orders.

## How it trades

**Only buys in an uptrend.** The close must be above the 50-day average, the 20-day average must be above the 50-day, and the 50-day must be rising.

**Two entry setups:**
- **Pullback:** RSI(14) dipped to 40 or below within the last 5 days, and today closes above yesterday's high.
- **Breakout:** the close clears the prior 20-day high on at least 1.5x average volume (the volume check is skipped for FX), with RSI below 75.

It also won't chase: no entry if price is more than 3 ATR above the 20-day average.

**Fills:** a signal forms on a daily close and fills at the **next open**, with slippage. The bot never trades on data it couldn't have had yet.

**Risk:**
- Each trade risks 1% of equity.
- The initial stop is 2 ATR below entry.
- Positions are capped at 20% of equity for a stock, 10% for crypto and 25% for FX.
- At most 8 positions are open at once.
- No leverage.

**Exits:**
- The initial stop.
- Once a trade is up 1R, the stop moves to breakeven and then trails 3 ATR below the highest high.
- After breakeven, a close below the 50-day average also exits.
- A time stop exits after 30 days if the trade never reached 1R.

All of these numbers are in `trader/config.py`.

## Usage

```bash
pip install -r requirements.txt
export ALPHAVANTAGE_API_KEY=...        # free key: https://www.alphavantage.co/support/#api-key

python -m trader run                   # fetch fresh data, manage positions, look for new buys
python -m trader run --offline         # same, using the cached CSVs in data/
python -m trader status                # positions, pending orders, equity
python -m trader backtest              # replay cached history from a fresh account
python -m unittest discover -s tests   # tests
```

Run it once a day after the US close; a cron job works. The account lives in `state/portfolio.json`, and closed trades are appended to `state/trades.csv`.

The free Alpha Vantage key allows 25 requests a day, and the default watchlist uses 16.

## Caveats

- The cached history is short, about 100 daily bars for stocks. A backtest on it is a smoke test, not evidence of an edge.
- Paper-trade for a few months before trusting any of this with real money.
- This is not financial advice.
