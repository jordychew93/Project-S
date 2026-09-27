# Project T: swing-trading paper bot

This bot scans US stocks, crypto and FX once a day for swing-trade setups, both **buys and sells (shorts)**. It **paper-trades** them from a $100,000 simulated account. It never places real orders.

There are two strategies. Pick one with `STRATEGY` in `trader/config.py` or `--strategy` on the command line:

| Option | What it trades |
|---|---|
| `sr` (default) | **Support & resistance.** Levels are found automatically from swing highs and lows. |
| `trend` | Pullbacks and volume breakouts inside an established trend. |
| `both` | Looks for both kinds of setup. |

Every rule is written for buying. Sell setups come from running the same rules on the price chart flipped upside down, so buys and sells are exact mirror images. Set `ALLOW_SHORTS = False` for buys only.

## Strategy B: support & resistance (`sr`)

**Finding levels:**
- A swing high is a bar whose high is the highest of the 3 bars on either side; a swing low is the mirror image.
- Swing points within 0.5 ATR of each other merge into a zone, capped at 1 ATR wide.
- A zone needs at least 2 touches to count, and more touches means a stronger level.

**Buy setups:**
- **Support bounce:** price dips into a support zone and closes back above it with a strong candle (a close above the open, in the upper half of the day's range).
- **Breakout retest:** price broke above a resistance zone within the last 10 bars, then came back to test it from above, where old resistance now acts as support, and held.

**Sell setups (mirror images):**
- **Resistance rejection:** price pushes into a resistance zone and closes back below it with a weak candle.
- **Breakdown retest:** price broke below a support zone, then came back up to test it from below and failed.

**Stop, target and filter:**
- The stop goes 0.5 ATR beyond the far side of the zone.
- The target is the first obstacle in the way: the next zone, or the recent 20-day high (for buys) or low (for sells).
- A trade is only taken if the target pays at least **2x the risk**. With no obstacle ahead, the target is 3R.
- Buys are skipped while the 50-day average is falling hard; sells are skipped while it's rising hard.

## Strategy A: trend (`trend`)

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

**Exits (both strategies):**
- The initial stop, or the target if the setup has one.
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
python -m trader --strategy trend run  # choose a strategy: sr (default), trend or both
python -m unittest discover -s tests   # tests
```

Run it once a day after the US close; a cron job works. The account lives in `state/portfolio.json`, and closed trades are appended to `state/trades.csv`.

The free Alpha Vantage key allows 25 requests a day, and the default watchlist uses 16.

## Caveats

- The cached history is short, about 100 daily bars for stocks. A backtest on it is a smoke test, not evidence of an edge.
- Paper-trade for a few months before trusting any of this with real money.
- This is not financial advice.
