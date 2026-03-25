# ADR-005: Fixed Fractional Position Sizing

**Date:** 2026-03-25
**Status:** Accepted

---

## Context

Every trade needs a position size — how many shares or contracts to buy. The choice of sizing method directly affects both returns and drawdown. Several approaches were considered:

- **Fixed lot:** always trade N shares. Simple but ignores account size and volatility.
- **Fixed fractional:** risk a fixed % of account per trade. Scales with account growth, bounded losses.
- **Kelly criterion:** mathematically optimal fraction given win rate and payoff ratio. Maximises geometric growth, but requires accurate win rate estimates and is highly sensitive to parameter errors.
- **Volatility-adjusted (ATR-based):** size positions inversely to recent volatility. More sophisticated, requires more data.
- **Equal weight:** divide capital equally across all open positions. Simple but doesn't account for signal confidence.

## Decision

**Fixed fractional** at **2% of account per trade** as the Phase 1 default.

Formula: `position_size = (account_value × max_position_fraction) / price`

The fraction is configurable via `MAX_POSITION_FRACTION` in `.env`.

## Rationale

- **Simplicity and interpretability:** the rule is trivially understandable. "The bot risked 2% of $10,000 = $200 on this trade" is easy to audit.
- **Bounded maximum loss:** with 2% sizing and a 2% stop-loss, the worst single-trade outcome is a 0.04% account loss. Even 10 consecutive full-stop losses only cost 0.4%.
- **Scales with account growth:** as the account grows from Phase 3 backtesting success, positions automatically grow proportionally without changing configuration.
- **Kelly requires stable statistics:** Kelly's formula needs an accurate estimate of win rate (p) and payoff ratio (b). In Phase 1 we don't have enough live trades to estimate these reliably. Fixed fractional doesn't depend on these estimates.
- **Drawdown control:** combined with the 10% account drawdown kill switch, fixed fractional at 2% provides multiple independent risk controls.

## Consequences

- **Sub-optimal growth rate:** Kelly criterion would produce higher geometric growth if the win rate estimate is accurate. Fixed fractional is more conservative. Accepted for Phase 1–2; revisit in Phase 3 once we have 100+ live trades to estimate win rate.
- **No confidence scaling:** all approved signals get the same size regardless of confidence. A high-confidence RSI signal and a barely-above-threshold signal get identical sizing. Phase 3 enhancement: scale position size by `confidence × max_position_fraction`.
- **Minimum position viability:** at $10,000 with 2% sizing, each position is $200. At $50/share that's 4 shares. For expensive stocks (NVDA at $800+), this produces fractional shares — which Alpaca supports but Binance does not for all pairs.

## Future evolution

When Phase 2 produces 100+ trades with stable win rate estimates, evaluate switching to **half-Kelly** (Kelly fraction ÷ 2) which captures most of the mathematical optimality while being robust to estimation errors.
