"""
Yahoo Finance data loader (stocks, ETFs).

Uses yfinance under the hood. Free, no API key required.
Good for Phase 1 historical replay. For live data, swap to AlpacaLoader.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import yfinance as yf

from bot.data.base import DataLoader
from bot.monitoring.logger import get_logger

log = get_logger(__name__)


class YahooLoader(DataLoader):
    """Downloads OHLCV data from Yahoo Finance."""

    def fetch_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> pd.DataFrame:
        log.debug(
            "fetching_bars",
            source="yahoo",
            symbol=symbol,
            start=start.date().isoformat(),
            end=end.date().isoformat(),
            interval=interval,
        )

        ticker = yf.Ticker(symbol)
        df = ticker.history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval=interval,
            auto_adjust=True,
        )

        if df.empty:
            log.warning("no_data_returned", source="yahoo", symbol=symbol)
            return df

        # Normalise column names to lowercase
        df.columns = [c.lower() for c in df.columns]

        # Keep only the columns the rest of the system expects
        df = df[["open", "high", "low", "close", "volume"]].copy()

        # Ensure UTC-aware index
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        log.debug("bars_fetched", source="yahoo", symbol=symbol, rows=len(df))
        return df
