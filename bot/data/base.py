"""
DataLoader base class.

Every data source (Yahoo Finance, Alpaca, CCXT) implements this interface.
The backtest engine and live loop only ever talk to a DataLoader — they
don't care where the data comes from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass
class Bar:
    """One OHLCV candlestick."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class DataLoader(ABC):
    """
    Abstract base for all data sources.

    Subclasses must implement `fetch_bars`. Everything else is optional.
    """

    @abstractmethod
    def fetch_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> pd.DataFrame:
        """
        Return a DataFrame with columns [open, high, low, close, volume]
        indexed by a DatetimeIndex (UTC).

        `interval` uses Yahoo Finance notation: 1m, 5m, 1h, 1d, 1wk.
        """

    def latest_bar(self, symbol: str) -> Bar | None:
        """
        Return the most recent bar for a symbol (used in paper/live mode).
        Default: fetch today's daily bar.  Subclasses can override for
        websocket-based sources.
        """
        from datetime import timedelta, timezone
        now = datetime.now(tz=timezone.utc)
        df = self.fetch_bars(symbol, start=now - timedelta(days=5), end=now)
        if df.empty:
            return None
        row = df.iloc[-1]
        return Bar(
            symbol=symbol,
            timestamp=row.name.to_pydatetime(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
