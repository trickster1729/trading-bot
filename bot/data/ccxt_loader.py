"""
CCXT data loader — crypto OHLCV via any CCXT-supported exchange.

Default exchange: Binance (best historical depth, most liquid pairs).
Swap to any other exchange by passing `exchange_id` to the constructor.

CCXT symbol format
------------------
CCXT uses "BASE/QUOTE" notation: "BTC/USDT", "ETH/USDT", "SOL/USDT".
Yahoo uses "BTCUSDT" or "BTC-USD". This loader expects CCXT notation.
A helper `ccxt_symbol()` converts common shorthand ("BTC") to "BTC/USDT".

Interval mapping
----------------
This loader accepts Yahoo-style interval strings and maps them to CCXT:

    Yahoo  →  CCXT
    -------- ------
    1m     →  1m
    5m     →  5m
    15m    →  15m
    1h     →  1h
    4h     →  4h
    1d     →  1d
    1wk    →  1w

Pagination
----------
CCXT exchanges return at most `fetch_ohlcv_limit` bars per call (usually
500–1000). For multi-year backtests we paginate automatically: each request
fetches `page_limit` bars starting from where the previous one stopped.

Rate limiting
-------------
CCXT has a built-in rate limiter (`enableRateLimit=True`). We respect it.
For sandbox/test usage, pass `sandbox=True` to use the exchange test API.
"""

from __future__ import annotations

from datetime import datetime

import ccxt
import pandas as pd

from bot.data.base import DataLoader
from bot.monitoring.logger import get_logger

log = get_logger(__name__)

# Yahoo-style → CCXT timeframe
_INTERVAL_MAP: dict[str, str] = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "1h":  "1h",
    "4h":  "4h",
    "1d":  "1d",
    "1wk": "1w",
}

# Default bars per request — Binance allows 1000, use 500 to be safe
_DEFAULT_PAGE_LIMIT = 500


def ccxt_symbol(base: str, quote: str = "USDT") -> str:
    """Convert 'BTC' → 'BTC/USDT'. Pass through if already in CCXT format."""
    if "/" in base:
        return base
    return f"{base.upper()}/{quote.upper()}"


class CcxtLoader(DataLoader):
    """
    Downloads OHLCV data from a CCXT exchange.

    Args:
        exchange_id  : CCXT exchange identifier (default "binance")
        sandbox      : use exchange sandbox / testnet (default False)
        page_limit   : bars per request for pagination (default 500)
        api_key      : optional — only needed for private endpoints
        api_secret   : optional — only needed for private endpoints
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        sandbox: bool = False,
        page_limit: int = _DEFAULT_PAGE_LIMIT,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        self.exchange_id = exchange_id
        self.page_limit = page_limit

        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Unknown CCXT exchange: {exchange_id!r}")

        config: dict = {"enableRateLimit": True}
        if api_key:
            config["apiKey"] = api_key
        if api_secret:
            config["secret"] = api_secret

        self._exchange: ccxt.Exchange = exchange_class(config)

        if sandbox:
            self._exchange.set_sandbox_mode(True)

    # ── DataLoader interface ──────────────────────────────────────────────────

    def fetch_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> pd.DataFrame:
        """
        Return OHLCV bars as a UTC-indexed DataFrame.

        `symbol` must be in CCXT format ("BTC/USDT"). Use `ccxt_symbol()`
        to convert shorthand if needed.
        """
        timeframe = _INTERVAL_MAP.get(interval)
        if timeframe is None:
            raise ValueError(
                f"Unsupported interval {interval!r}. "
                f"Supported: {list(_INTERVAL_MAP)}"
            )

        if not self._exchange.has.get("fetchOHLCV"):
            raise RuntimeError(
                f"Exchange {self.exchange_id!r} does not support fetchOHLCV"
            )

        log.debug(
            "fetching_bars",
            source=self.exchange_id,
            symbol=symbol,
            start=start.date().isoformat(),
            end=end.date().isoformat(),
            interval=interval,
        )

        since_ms = int(start.timestamp() * 1000)
        end_ms   = int(end.timestamp() * 1000)

        all_rows: list[list] = []
        while True:
            batch = self._exchange.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                since=since_ms,
                limit=self.page_limit,
            )
            if not batch:
                break

            all_rows.extend(batch)

            last_ts = batch[-1][0]
            if last_ts >= end_ms or len(batch) < self.page_limit:
                break

            # Advance past the last returned bar
            since_ms = last_ts + 1

        if not all_rows:
            log.warning("no_data_returned", source=self.exchange_id, symbol=symbol)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = self._to_dataframe(all_rows)

        # Trim to requested range (CCXT may return a bar just before `start`)
        # Use tz_localize only for naive datetimes; tz-aware ones compare directly.
        def _ts(dt: datetime) -> pd.Timestamp:
            t = pd.Timestamp(dt)
            return t if t.tzinfo else t.tz_localize("UTC")

        df = df[(df.index >= _ts(start)) & (df.index < _ts(end))]

        log.debug("bars_fetched", source=self.exchange_id, symbol=symbol, rows=len(df))
        return df

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _to_dataframe(self, rows: list[list]) -> pd.DataFrame:
        """Convert raw CCXT OHLCV list to a normalised UTC DataFrame."""
        df = pd.DataFrame(
            rows,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        df = df[["open", "high", "low", "close", "volume"]]

        # Drop duplicates (some exchanges return overlapping bars on pagination)
        df = df[~df.index.duplicated(keep="last")]
        df = df.sort_index()
        return df

    def markets(self) -> list[str]:
        """Return list of available trading pairs on this exchange."""
        markets = self._exchange.load_markets()
        return list(markets.keys())
