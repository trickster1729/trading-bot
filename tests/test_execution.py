"""
Tests for bot/execution/base.py and bot/execution/paper.py

Coverage targets:
- OrderSide.from_direction() mapping
- OrderResult properties: slippage, slippage_bps, filled, to_dict()
- PaperBroker market fills: price, slippage, commission
- PaperBroker limit fills: fills when price at/better than limit, PENDING otherwise
- PaperBroker position tracking: long → short → flat
- PaperBroker cancel_order and cancel_all_orders
- Unsupported order type → REJECTED
"""

from __future__ import annotations

import pytest

from bot.execution.base import OrderSide, OrderStatus
from bot.execution.paper import PaperBroker
from bot.signals.base import Direction
from tests.conftest import make_signal


# ── Helpers ───────────────────────────────────────────────────────────────────

def _broker(slippage_bps: float = 5.0, commission: float = 0.0) -> PaperBroker:
    return PaperBroker(slippage_bps=slippage_bps, commission=commission)


# ═══════════════════════════════════════════════════════════════════════════════
# OrderSide
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrderSide:
    def test_long_maps_to_buy(self):
        assert OrderSide.from_direction(Direction.LONG) == OrderSide.BUY

    def test_short_maps_to_sell(self):
        assert OrderSide.from_direction(Direction.SHORT) == OrderSide.SELL


# ═══════════════════════════════════════════════════════════════════════════════
# OrderResult properties
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrderResult:
    def test_filled_true_when_status_filled(self):
        broker = _broker()
        signal = make_signal(price=100.0)
        result = broker.submit_order(signal, quantity=1.0)
        assert result.filled

    def test_slippage_is_absolute_difference(self):
        # 5 bps slippage on buy: fill = 100 * 1.0005 = 100.05 → slippage = 0.05
        broker = _broker(slippage_bps=5.0)
        signal = make_signal(price=100.0)
        result = broker.submit_order(signal, quantity=1.0)
        expected_slippage = abs(result.fill_price - 100.0)
        assert result.slippage == pytest.approx(expected_slippage, rel=1e-6)

    def test_slippage_bps_calculation(self):
        broker = _broker(slippage_bps=5.0)
        signal = make_signal(price=100.0)
        result = broker.submit_order(signal, quantity=1.0)
        # fill = 100.05, slippage = 0.05, in bps = (0.05/100)*10000 = 5
        assert result.slippage_bps == pytest.approx(5.0, rel=1e-3)

    def test_to_dict_has_all_keys(self):
        broker = _broker()
        result = broker.submit_order(make_signal(), quantity=1.0)
        d = result.to_dict()
        for key in ("order_id", "status", "symbol", "side", "fill_price",
                    "commission", "slippage_bps", "timestamp"):
            assert key in d


# ═══════════════════════════════════════════════════════════════════════════════
# PaperBroker — market orders
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperBrokerMarket:
    def test_market_buy_is_filled(self):
        broker = _broker()
        result = broker.submit_order(make_signal(direction=Direction.LONG), quantity=1.0)
        assert result.status == OrderStatus.FILLED
        assert result.side == OrderSide.BUY

    def test_market_sell_is_filled(self):
        broker = _broker()
        result = broker.submit_order(make_signal(direction=Direction.SHORT), quantity=1.0)
        assert result.status == OrderStatus.FILLED
        assert result.side == OrderSide.SELL

    def test_buy_fill_price_includes_positive_slippage(self):
        # Buy slippage adds to price (adverse for buyer)
        broker = _broker(slippage_bps=10.0)
        signal = make_signal(price=100.0, direction=Direction.LONG)
        result = broker.submit_order(signal, quantity=1.0)
        assert result.fill_price > 100.0
        assert result.fill_price == pytest.approx(100.0 * 1.001, rel=1e-6)

    def test_sell_fill_price_includes_negative_slippage(self):
        # Sell slippage subtracts from price (adverse for seller)
        broker = _broker(slippage_bps=10.0)
        signal = make_signal(price=100.0, direction=Direction.SHORT)
        result = broker.submit_order(signal, quantity=1.0)
        assert result.fill_price < 100.0
        assert result.fill_price == pytest.approx(100.0 * 0.999, rel=1e-6)

    def test_zero_slippage_fills_at_signal_price(self):
        broker = _broker(slippage_bps=0.0)
        signal = make_signal(price=150.0)
        result = broker.submit_order(signal, quantity=1.0)
        assert result.fill_price == pytest.approx(150.0, rel=1e-6)

    def test_filled_qty_matches_requested(self):
        broker = _broker()
        result = broker.submit_order(make_signal(), quantity=3.5)
        assert result.filled_qty == pytest.approx(3.5)
        assert result.requested_qty == pytest.approx(3.5)

    def test_flat_commission_added_to_result(self):
        broker = _broker(commission=1.0)
        result = broker.submit_order(make_signal(price=100.0), quantity=1.0)
        assert result.commission == pytest.approx(1.0)

    def test_pct_commission_calculated_on_trade_value(self):
        # 0.1% commission on $100 trade = $0.10
        broker = PaperBroker(slippage_bps=0.0, commission_pct=0.001)
        signal = make_signal(price=100.0)
        result = broker.submit_order(signal, quantity=1.0)
        assert result.commission == pytest.approx(0.10, rel=1e-4)

    def test_order_id_is_unique_across_orders(self):
        broker = _broker()
        r1 = broker.submit_order(make_signal(), quantity=1.0)
        r2 = broker.submit_order(make_signal(), quantity=1.0)
        assert r1.order_id != r2.order_id

    def test_unsupported_order_type_is_rejected(self):
        broker = _broker()
        result = broker.submit_order(make_signal(), quantity=1.0, order_type="stop_limit_oco")
        assert result.status == OrderStatus.REJECTED
        assert result.filled_qty == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# PaperBroker — limit orders
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperBrokerLimit:
    def test_buy_limit_fills_when_price_at_limit(self):
        broker = _broker()
        signal = make_signal(price=100.0, direction=Direction.LONG)
        result = broker.submit_order(signal, quantity=1.0, order_type="limit", limit_price=100.0)
        assert result.status == OrderStatus.FILLED

    def test_buy_limit_fills_when_price_below_limit(self):
        # Price 95 < limit 100 — favourable for buyer
        broker = _broker()
        signal = make_signal(price=95.0, direction=Direction.LONG)
        result = broker.submit_order(signal, quantity=1.0, order_type="limit", limit_price=100.0)
        assert result.status == OrderStatus.FILLED
        assert result.fill_price == pytest.approx(100.0)

    def test_buy_limit_pending_when_price_above_limit(self):
        broker = _broker()
        signal = make_signal(price=105.0, direction=Direction.LONG)
        result = broker.submit_order(signal, quantity=1.0, order_type="limit", limit_price=100.0)
        assert result.status == OrderStatus.PENDING
        assert result.filled_qty == 0.0

    def test_sell_limit_fills_when_price_at_limit(self):
        broker = _broker()
        signal = make_signal(price=100.0, direction=Direction.SHORT)
        result = broker.submit_order(signal, quantity=1.0, order_type="limit", limit_price=100.0)
        assert result.status == OrderStatus.FILLED

    def test_sell_limit_pending_when_price_below_limit(self):
        broker = _broker()
        signal = make_signal(price=95.0, direction=Direction.SHORT)
        result = broker.submit_order(signal, quantity=1.0, order_type="limit", limit_price=100.0)
        assert result.status == OrderStatus.PENDING

    def test_limit_order_without_price_raises(self):
        broker = _broker()
        with pytest.raises(ValueError, match="limit_price"):
            broker.submit_order(make_signal(), quantity=1.0, order_type="limit")


# ═══════════════════════════════════════════════════════════════════════════════
# PaperBroker — position tracking
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperBrokerPositions:
    def test_buy_adds_to_position(self):
        broker = _broker()
        broker.submit_order(make_signal(symbol="AAPL", direction=Direction.LONG), quantity=5.0)
        positions = broker.get_open_positions()
        assert positions["AAPL"] == pytest.approx(5.0)

    def test_sell_reduces_position(self):
        broker = _broker()
        broker.submit_order(make_signal(symbol="AAPL", direction=Direction.LONG), quantity=5.0)
        broker.submit_order(make_signal(symbol="AAPL", direction=Direction.SHORT), quantity=3.0)
        positions = broker.get_open_positions()
        assert positions["AAPL"] == pytest.approx(2.0)

    def test_full_close_removes_symbol_from_positions(self):
        broker = _broker()
        broker.submit_order(make_signal(symbol="AAPL", direction=Direction.LONG), quantity=5.0)
        broker.submit_order(make_signal(symbol="AAPL", direction=Direction.SHORT), quantity=5.0)
        positions = broker.get_open_positions()
        assert "AAPL" not in positions

    def test_multiple_symbols_tracked_independently(self):
        broker = _broker()
        broker.submit_order(make_signal(symbol="AAPL", direction=Direction.LONG), quantity=3.0)
        broker.submit_order(make_signal(symbol="MSFT", direction=Direction.LONG), quantity=7.0)
        positions = broker.get_open_positions()
        assert positions["AAPL"] == pytest.approx(3.0)
        assert positions["MSFT"] == pytest.approx(7.0)

    def test_pending_order_does_not_affect_positions(self):
        broker = _broker()
        signal = make_signal(price=105.0, direction=Direction.LONG)
        broker.submit_order(signal, quantity=1.0, order_type="limit", limit_price=100.0)
        assert broker.get_open_positions() == {}


# ═══════════════════════════════════════════════════════════════════════════════
# PaperBroker — cancellation
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperBrokerCancellation:
    def test_cancel_pending_order_returns_true(self):
        broker = _broker()
        signal = make_signal(price=105.0, direction=Direction.LONG)
        result = broker.submit_order(signal, quantity=1.0, order_type="limit", limit_price=100.0)
        assert broker.cancel_order(result.order_id)

    def test_cancel_filled_order_returns_false(self):
        broker = _broker()
        result = broker.submit_order(make_signal(), quantity=1.0)
        assert not broker.cancel_order(result.order_id)

    def test_cancel_nonexistent_order_returns_false(self):
        broker = _broker()
        assert not broker.cancel_order("does-not-exist")

    def test_cancel_all_returns_cancelled_ids(self):
        broker = _broker()
        # Create 2 pending limit orders
        s1 = make_signal(symbol="AAPL", price=105.0, direction=Direction.LONG)
        s2 = make_signal(symbol="MSFT", price=105.0, direction=Direction.LONG)
        r1 = broker.submit_order(s1, quantity=1.0, order_type="limit", limit_price=100.0)
        r2 = broker.submit_order(s2, quantity=1.0, order_type="limit", limit_price=100.0)
        cancelled = broker.cancel_all_orders()
        assert r1.order_id in cancelled
        assert r2.order_id in cancelled
        assert len(cancelled) == 2
