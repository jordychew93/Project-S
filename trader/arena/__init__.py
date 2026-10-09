"""Strategy arena: several strategies trade side by side as VIRTUAL $100k books.

PAPER / VIRTUAL ONLY. Nothing in this package places, modifies or cancels an order anywhere, and it
never imports the moomoo trading adapter or the moomoo bot. It only reads daily prices
(moomoo quote API) and keeps its own ledgers in state/arena.json. A unit test enforces this.

    python -m trader arena-backtest       stage 1: replay >= 2 years of daily bars, write ARENA_RESULTS.md
    python -m trader arena-update         stage 2: advance the forward virtual books with the latest closes
    python -m trader arena-leaderboard    weekly leaderboard (plain-text Telegram message)
"""
