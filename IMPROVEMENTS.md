# Project T: improvement log

Ideas to build **only when the user asks**. The daily scan never changes the code on its own.

Rules for these improvements:
- **Keep them small.** Each one should be reviewable in a minute and never mix two ideas.
- **Test before shipping.** The test suite must pass, and new behaviour gets a new test.
- **Don't curve-fit.** Never tighten or loosen strategy rules or risk settings just because of recent results. A strategy change needs a backtest across all the cached history, and the report has to show the before and after numbers.
- **Never touch the safety rails** without the user asking: risk per trade, maximum positions, no leverage, paper trading only.
- **Prefer clarity and correctness.** Better reports, data checks and bug fixes come before new trading rules.

## Backlog (top = next)
- [ ] Show each symbol's daily % change in the report
- [ ] Log near-misses: setups that failed only one rule, and which rule
- [ ] Equity curve chart saved to docs/img after each run
- [ ] Performance stats by setup type (win rate, average R) in `status`
- [ ] Friday weekly summary: best and worst movers, trades, equity change
- [ ] Correlation guard: at most 2 open positions in one group (SPY, QQQ and big tech move together)
- [ ] Earnings filter: skip stocks with earnings in the next 2 days (costs 1 data request)
- [ ] Walk-forward backtest report, as the cached history grows
- [ ] Carry the previous day's levels into the report, to show which levels broke overnight

## Inbox: ideas to review

- 2026-10-07 [@deanwperkins on X](https://x.com/deanwperkins/status/2100194304035549602): a post about a 23-minute tutorial on building a Claude trading bot, inspired by a Chinese trader. In the final minute the creator admits the demo isn't finished and that they trade better by hand. Taken from a search snippet; I haven't seen the video. Jordy linked it to "obsidian". Ideas for T, pending Jordy's OK:
  1. Scorecard: win rate, average R, expectancy and max drawdown for T's signals, compared with buying and holding SPY and with Jordy's own moomoo trades.
  2. Trade journal: one Obsidian-style markdown note per signal (setup, levels, chart, outcome, lesson), tagged and linked, so it can be opened as a vault.
  3. Rule: no real-money size increase until about 30 closed paper trades show positive expectancy.

## Changelog
- **2026-09-30:** The watchlist is ranked by distance to the nearest level, measured in ATR. The report opens with a 🎯 "Closest to a setup" line, flags when price is *inside* a level, and warns when a symbol's data is stale.
