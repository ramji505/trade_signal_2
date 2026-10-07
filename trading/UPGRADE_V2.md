# Trading Project — Upgrade V2

This version implements the highest-impact controls from the assessment.

## Implemented

- Real Groww token exchange in live mode; no fabricated production token path.
- Historical 1-minute seed + live Groww quote polling in `feed.py` when `MARKET_DATA_MODE=GROWW`.
- Execution quality threshold unified to `MIN_EXECUTION_SCORE=80`.
- Daily realized-P&L hard stop and scheduled high-risk event veto.
- Backtest profit factor is `null` when there are no losing trades; results under 30 completed trades are marked non-actionable.
- `.env.example` contains configuration only; real secrets are intentionally excluded.

## Live configuration

Copy `.env.example` to `.env`, supply real Groww credentials, set `MARKET_DATA_MODE=GROWW`, and start in paper/simulation mode first. Keep real trading disabled until historical and out-of-sample validation is sufficiently large.
