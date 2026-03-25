"""
Tests for bot/data/ccxt_loader.py

Strategy: mock the CCXT exchange object so tests run offline and instantly.
We test the loader's own logic — pagination, trimming, deduplication,
interval mapping, error handling — not CCXT itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from bot.data.ccxt_loader import CcxtLoader, ccxt_symbol


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _ts(dt: datetime) -> int:
    """datetime → millisecond timestamp."""
    return int(dt.timestamp() * 1000)


def _make_bar(dt: datetime, close: float = 100.0) -> list:
    """Make a fake CCXT OHLCV row."""
    return [_ts(dt), close - 1, close + 1, close - 2, close, 1000.0]


def _make_loader(exchange_mock: MagicMock) -> CcxtLoader:
    """Return a CcxtLoader whose exchange is replaced by exchange_mock."""
    with patch("ccxt.binance", return_value=exchange_mock):
        loader = CcxtLoader(exchange_id="binance")
    loader._exchange = exchange_mock
    return loader


@pytest.fixture()
def exchange():
    m = MagicMock()
    m.has = {"fetchOHLCV": True}
    return m


@pytest.fixture()
def loader(exchange):
    return _make_loader(exchange)


# ── ccxt_symbol helper ────────────────────────────────────────────────────────


class TestCcxtSymbol:
    def test_converts_base_to_slash_format(self):
        assert ccxt_symbol("BTC") == "BTC/USDT"

    def test_custom_quote(self):
        assert ccxt_symbol("ETH", "BTC") == "ETH/BTC"

    def test_passthrough_if_already_slash_format(self):
        assert ccxt_symbol("SOL/USDT") == "SOL/USDT"

    def test_lowercased_input_uppercased(self):
        assert ccxt_symbol("btc", "usdt") == "BTC/USDT"


# ── Basic fetch ───────────────────────────────────────────────────────────────


class TestFetchBars:
    def test_returns_dataframe_with_correct_columns(self, loader, exchange):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end   = datetime(2024, 1, 5, tzinfo=timezone.utc)

        exchange.fetch_ohlcv.return_value = [
            _make_bar(datetime(2024, 1, i, tzinfo=timezone.utc))
            for i in range(1, 4)
        ]

        df = loader.fetch_bars("BTC/USDT", start, end)
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert df.index.tzinfo is not None  # UTC-aware

    def test_empty_exchange_response_returns_empty_df(self, loader, exchange):
        exchange.fetch_ohlcv.return_value = []
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end   = datetime(2024, 1, 5, tzinfo=timezone.utc)
        df = loader.fetch_bars("BTC/USDT", start, end)
        assert df.empty

    def test_bars_trimmed_to_requested_range(self, loader, exchange):
        # Return one bar BEFORE start and one AFTER end
        start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        end   = datetime(2024, 1, 4, tzinfo=timezone.utc)

        exchange.fetch_ohlcv.return_value = [
            _make_bar(datetime(2024, 1, 1, tzinfo=timezone.utc)),  # before start
            _make_bar(datetime(2024, 1, 2, tzinfo=timezone.utc)),  # in range
            _make_bar(datetime(2024, 1, 3, tzinfo=timezone.utc)),  # in range
            _make_bar(datetime(2024, 1, 4, tzinfo=timezone.utc)),  # == end, excluded
        ]
        df = loader.fetch_bars("BTC/USDT", start, end)
        assert len(df) == 2

    def test_interval_mapping_passed_to_exchange(self, loader, exchange):
        exchange.fetch_ohlcv.return_value = []
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end   = datetime(2024, 1, 2, tzinfo=timezone.utc)

        loader.fetch_bars("BTC/USDT", start, end, interval="1h")
        call_kwargs = exchange.fetch_ohlcv.call_args
        assert call_kwargs.kwargs.get("timeframe") == "1h" or \
               call_kwargs.args[1] == "1h"

    def test_weekly_interval_mapping(self, loader, exchange):
        exchange.fetch_ohlcv.return_value = []
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end   = datetime(2024, 6, 1, tzinfo=timezone.utc)
        loader.fetch_bars("BTC/USDT", start, end, interval="1wk")
        call_kwargs = exchange.fetch_ohlcv.call_args
        timeframe = call_kwargs.kwargs.get("timeframe") or call_kwargs.args[1]
        assert timeframe == "1w"

    def test_unsupported_interval_raises(self, loader, exchange):
        with pytest.raises(ValueError, match="Unsupported interval"):
            loader.fetch_bars(
                "BTC/USDT",
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                datetime(2024, 1, 2, tzinfo=timezone.utc),
                interval="3d",  # not in the map
            )

    def test_exchange_without_fetch_ohlcv_raises(self, loader, exchange):
        exchange.has = {"fetchOHLCV": False}
        with pytest.raises(RuntimeError, match="does not support fetchOHLCV"):
            loader.fetch_bars(
                "BTC/USDT",
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                datetime(2024, 1, 2, tzinfo=timezone.utc),
            )


# ── Pagination ────────────────────────────────────────────────────────────────


class TestPagination:
    def test_two_pages_combined(self, exchange):
        """Loader should make two requests when first batch is full (page_limit)."""
        loader = _make_loader(exchange)
        loader.page_limit = 3  # small limit to force pagination

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end   = datetime(2024, 1, 8, tzinfo=timezone.utc)

        page1 = [_make_bar(datetime(2024, 1, i, tzinfo=timezone.utc)) for i in range(1, 4)]
        page2 = [_make_bar(datetime(2024, 1, i, tzinfo=timezone.utc)) for i in range(4, 7)]

        exchange.fetch_ohlcv.side_effect = [page1, page2, []]

        df = loader.fetch_bars("BTC/USDT", start, end)
        assert exchange.fetch_ohlcv.call_count == 3
        assert len(df) == 6

    def test_stops_when_last_ts_reaches_end(self, exchange):
        """No second request when first batch already covers the full range."""
        loader = _make_loader(exchange)
        loader.page_limit = 10

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end   = datetime(2024, 1, 4, tzinfo=timezone.utc)

        page1 = [_make_bar(datetime(2024, 1, i, tzinfo=timezone.utc)) for i in range(1, 4)]
        exchange.fetch_ohlcv.return_value = page1  # only one call needed

        loader.fetch_bars("BTC/USDT", start, end)
        assert exchange.fetch_ohlcv.call_count == 1

    def test_duplicate_bars_on_page_boundary_removed(self, exchange):
        """Duplicate timestamps across pages should be deduplicated."""
        loader = _make_loader(exchange)
        loader.page_limit = 2

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end   = datetime(2024, 1, 5, tzinfo=timezone.utc)

        bar_jan2 = _make_bar(datetime(2024, 1, 2, tzinfo=timezone.utc))
        bar_jan3 = _make_bar(datetime(2024, 1, 3, tzinfo=timezone.utc))
        bar_jan4 = _make_bar(datetime(2024, 1, 4, tzinfo=timezone.utc))

        page1 = [bar_jan2, bar_jan3]
        page2 = [bar_jan3, bar_jan4]  # jan3 duplicated

        exchange.fetch_ohlcv.side_effect = [page1, page2, []]

        df = loader.fetch_bars("BTC/USDT", start, end)
        assert df.index.duplicated().sum() == 0
        assert len(df) == 3


# ── CcxtLoader init ───────────────────────────────────────────────────────────


class TestCcxtLoaderInit:
    def test_invalid_exchange_raises(self):
        with pytest.raises(ValueError, match="Unknown CCXT exchange"):
            CcxtLoader(exchange_id="notarealexchange")

    def test_default_exchange_is_binance(self):
        with patch("ccxt.binance") as mock_cls:
            mock_cls.return_value = MagicMock(has={"fetchOHLCV": True})
            loader = CcxtLoader()
        assert loader.exchange_id == "binance"
