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

## Running by itself on GitHub

`.github/workflows/daily-scan.yml` runs the bot every weekday at 21:22 UTC, after the New York close. Each run:
1. fetches fresh prices,
2. paper-trades any setups,
3. commits `data/` and `state/`,
4. posts the report as a comment on the **"📈 T daily reports"** issue. GitHub then notifies you by email and in the mobile app.

One-time setup:
1. Get a free Alpha Vantage key, then add it as a repository secret named `ALPHAVANTAGE_API_KEY` (**Settings → Secrets and variables → Actions → New repository secret**).
2. GitHub only runs scheduled workflows from the **default branch**. Either make the branch holding this code the default (**Settings → General → Default branch**) or merge it into the default branch.
3. Optional: go to **Actions → T daily scan → Run workflow** to test it straight away.
4. Optional phone alerts: add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets to also get a Telegram message.

## moomoo paper bot

A second, local way to run the same strategy: US-stock trades are mirrored as orders in Jordy's **moomoo paper (simulated) account** through moomoo OpenD on `127.0.0.1:11111`. The cloud bot above is unchanged; this bot keeps its own book in `state/moomoo_portfolio.json`, `state/moomoo_trades.csv`, `state/moomoo_broker.json` and `state/moomoo_orders.csv` (all git-ignored).

```bash
python -m trader moomoo-scan                # after the US close (~06:00 MYT): scan, queue next-open orders
python -m trader moomoo-execute [--dry-run] # ~10 min after the US open: send them to moomoo paper, reconcile
python -m trader moomoo-status              # bot book, paper account, and whether they agree
```

- **Paper only.** Every order goes through `place_paper_order` in `trader/broker_moomoo.py`, which refuses anything but `TrdEnv.SIMULATE` and paper account 4405511. Trading is never unlocked. A test checks it is the only `place_order` call.
- **Same rules, same $100k sizing**, so results compare with the cloud bot, even though the paper account holds $1M.
- **Stocks** use moomoo daily bars (front-adjusted), cached in `data/moomoo/`. **Crypto and FX** can't trade on moomoo paper, so they stay simulated in the bot's book (Alpha Vantage if `ALPHAVANTAGE_API_KEY` is set, otherwise the cached CSVs).
- Entries are market orders sized at that day's open exactly as the simulator would. Exits (stop, target, trend, time) are spotted at the scan and sent at the next execute, so moomoo exits a day after the bot's book does.
- A stock order that never reached moomoo (execute didn't run) is cancelled at the next scan, so the book never holds what moomoo doesn't. Execute ends by comparing the two and reports any mismatch.
- Cron wrappers: `~/.hermes/scripts/moomoo/paper_scan.sh` and `paper_execute.sh` (stdout is the Telegram summary; moomoo logs go to `paper_bot.log`). If OpenD isn't running they print one warning line.

## Strategy arena (virtual books)

Several strategies run side by side, each with its own **virtual $100,000 book** fed the same daily moomoo prices. The arena never places orders (a test checks it can't even import the trading adapter); only the moomoo paper bot above trades the paper account.

| Book | Rules |
|---|---|
| Project T (as is) | this bot, stocks only, same ~400-day data window as the moomoo bot |
| Project T + playbook | plus 200-day trend gate, 3 tranches, 10% cap per name, 15% cash, half off at +2R/+25%, earnings blackout (needs `data/arena/earnings.json`), half risk while SPY is below its 200-day average |
| Breakout | buy a close above the 55-day high, exit below the 20-day low, 2 ATR stop, 1% risk |
| Dip buying | RSI(2) under 10 above the 200-day average; exit RSI(2) over 70 or after 10 days |
| Monthly rotation | hold the strongest of SPY/QQQ/GLD/TLT by 3- and 6-month return, BIL if all are negative |
| SPY buy & hold | the benchmark |

```bash
python -m trader arena-backtest [--refresh] [--ablation]   # stage 1: 2022 onwards, writes ARENA_RESULTS.md
python -m trader arena-update [--offline]                  # stage 2: advance the forward books (idempotent)
python -m trader arena-leaderboard                         # weekly Telegram-style leaderboard
```

- **Stage 1** drops a strategy that trails buy-and-hold SPY or falls more than 20% from a peak.
- **Stage 2** forward books live in `state/arena.json` (git-ignored); stage 1 scorecards in `state/arena_backtest.json`.
- **Promotion** is only reported: 30+ closed trades, and beats Project T on expectancy ($/trade) and max drawdown in both stages.
- Cron wrappers: `~/.hermes/scripts/moomoo/arena_update.sh` (silent unless it fails) and `arena_leaderboard.sh`, run from the `~/Documents/Project-T-arena` worktree; logs go to `arena.log`.

## Caveats

- The cached history is short, about 100 daily bars for stocks. A backtest on it is a smoke test, not evidence of an edge.
- Paper-trade for a few months before trusting any of this with real money.
- This is not financial advice.
