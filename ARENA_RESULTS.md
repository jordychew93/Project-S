# Strategy arena: stage 1 backtest

Daily bars from moomoo (front-adjusted), **2022-01-03 to 2026-10-08**. Each strategy runs its own **virtual $100,000 book**. Signals use a day's close and fill at the next open with 0.05% slippage. No commissions. Paper/virtual only: nothing here places orders.

Stage 1 rule: a strategy is **dropped** if it trails buy-and-hold SPY **or** its max drawdown exceeds 20%. Win rate is shown but not used to rank.

| Strategy | Total return | vs SPY (% points) | CAGR | Max drawdown | Sharpe | Trades | Win rate | Expectancy ($/trade) | Avg R | Profit factor | Stage 1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Project T (as is) | −7.3% | −78.9 pts | −1.6% | −11.6% | −0.17 | 212 | 29% | −$33 | +0.09 | 0.90 | dropped: trailed SPY |
| Project T + playbook rules | −1.7% | −73.3 pts | −0.4% | −3.2% | −0.18 | 196 | 35% | −$8 | +0.22 | 0.90 | dropped: trailed SPY |
| Breakout (Turtle-style) | +73.6% | +2.0 pts | +12.3% | −13.7% | 0.94 | 107 | 43% | $666 | +0.86 | 2.07 | **survived** |
| Mean reversion (RSI-2) | +51.8% | −19.8 pts | +9.2% | −10.6% | 1.19 | 336 | 71% | $153 | n/a | 1.99 | dropped: trailed SPY |
| Monthly momentum rotation | +97.8% | +26.2 pts | +15.4% | −19.1% | 0.83 | 17 | 59% | $5,817 | n/a | 4.12 | **survived** |
| Buy & hold SPY (benchmark) | +71.6% | — | +12.0% | −24.6% | 0.75 | 0 | n/a | n/a | n/a | n/a | benchmark |

**Survivors:** Breakout (Turtle-style), Monthly momentum rotation.

## Notes

- Universe: Project T's US stocks (SPY, QQQ, AAPL, MSFT, NVDA, AMZN, GOOGL, META, JPM, XOM). Rotation uses SPY, QQQ, GLD, TLT and BIL as cash. Project T's crypto/FX legs are left out: moomoo has no long history for them.
- Project T and the playbook variant see the same ~276-bar window the live moomoo bot sees each day.
- Expectancy is the average dollar P&L per closed trade; Avg R only exists for strategies with a stop. Open positions at the end count in the return, not in the trade stats. SPY buy & hold has no closed trades.
- Sharpe uses daily returns, annualised, risk-free rate 0.
- Playbook earnings blackout: **inactive**: no earnings-date source is configured (moomoo's quote API has none and no Alpha Vantage key is set).
- Scaling in is simplified: 1/3 at the open, then limit adds 1/3 and 2/3 of the way to the stop.

## Playbook rules one at a time (Project T + one flag)

| Variant | Total return | Max drawdown | Sharpe | Trades | Win rate | Expectancy ($/trade) | Avg R |
|---|---:|---:|---:|---:|---:|---:|---:|
| all flags off (= Project T) | −7.3% | −11.6% | −0.17 | 212 | 29% | −$33 | +0.09 |
| 200-day trend gate | −10.0% | −14.3% | −0.27 | 191 | 30% | −$51 | +0.07 |
| scale in (3 tranches) | −3.9% | −7.8% | −0.20 | 217 | 33% | −$17 | +0.26 |
| 10% cap + 15% cash | −5.1% | −6.7% | −0.23 | 212 | 29% | −$23 | +0.09 |
| half off at +2R/+25% | −7.7% | −12.4% | −0.21 | 212 | 32% | −$35 | +0.09 |
| earnings blackout | −7.3% | −11.6% | −0.17 | 212 | 29% | −$33 | +0.09 |
| risk-off halves risk | −2.5% | −11.4% | −0.05 | 212 | 29% | −$10 | +0.09 |
| all flags on | −1.7% | −3.2% | −0.18 | 196 | 35% | −$8 | +0.22 |

Promotion rule (reported only, never switched automatically): at least 30 closed trades, and beats Project T on expectancy and on max drawdown in both the backtest and the forward paper run.
