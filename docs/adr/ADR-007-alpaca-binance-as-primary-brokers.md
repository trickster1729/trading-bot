# ADR-007: Alpaca + Binance as Phase 1–3 Primary Brokers

**Date:** 2026-03-25
**Status:** Accepted

---

## Context

The bot needs brokers for stocks/equities and crypto. The broker choice affects:
- API quality and Python SDK availability
- Paper trading / testnet support (critical for Phase 1–2)
- Commission structure
- Asset coverage
- Phase 4 scalability

Brokers evaluated:

| Broker | Assets | Paper | API | Phase fit |
|---|---|---|---|---|
| **Alpaca** | Stocks, Options | Free, built-in | Simple REST/WebSocket | 1–3 |
| **Binance** | Crypto | Testnet free | Well-documented | 1–3 |
| Tradier | Stocks, Options | Sandbox free | Medium complexity | 2–3 |
| Interactive Brokers | All | Paper account | Complex (TWS/IB Gateway) | 3–4 |
| Coinbase Advanced | Crypto | Sandbox | Medium | 2–3 |

## Decision

**Alpaca** for stocks/equities and **Binance** for crypto as Phase 1–3 brokers.

**Interactive Brokers** added in Phase 4 for options volume and margin.
**Coinbase Advanced** added as Binance fallback in Phase 3–4.

## Rationale

### Alpaca
- **Commission-free** for US equities — no per-trade cost erodes returns
- **Built-in paper trading** on `paper-api.alpaca.markets` — identical API to live, no code changes to switch
- **Fractional shares** — allows correct position sizing even for expensive stocks
- **WebSocket feed** for Phase 2 real-time data — same data source as FinRL-Trading reference
- **Python SDK** is mature and well-maintained
- **Weakness:** options support is newer and less liquid than IBKR

### Binance
- **Best crypto liquidity** globally — tight spreads, deep order books
- **Testnet available** at no cost — paper crypto trading without real funds
- **CCXT compatible** — we use CCXT's unified interface which abstracts Binance (and 100+ other exchanges) behind one API
- **Weakness:** US regulatory environment is complex; Coinbase Advanced as fallback in Phase 3

## Consequences

- **Phase 2 dependency:** Alpaca API keys required before Phase 2 starts. Keys are free to obtain at alpaca.markets.
- **Binance US restrictions:** Binance.com has limited access for US users. Phase 1–2 use the testnet (no restriction). Phase 3 will evaluate Binance US vs. Coinbase Advanced for US-based live trading.
- **IBKR deferred:** IBKR's API (TWS/IB Gateway) requires running a desktop application as a gateway process. This is manageable in Phase 4 Docker/ECS deployment but adds operational complexity not worth taking on in Phase 1–3.
- **Broker abstraction isolation:** all broker-specific code is contained in `bot/execution/alpaca.py` and `bot/execution/binance.py`. The engine, strategies, and risk manager have no knowledge of which broker is active.
