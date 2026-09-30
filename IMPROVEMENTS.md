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

## Changelog
- **2026-09-30:** The watchlist is ranked by distance to the nearest level, measured in ATR. The report opens with a 🎯 "Closest to a setup" line, flags when price is *inside* a level, and warns when a symbol's data is stale.
