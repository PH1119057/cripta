import json
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain import Candle, OrderRequest
from bybit_workbench.domain.types import (
    FillReason,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from bybit_workbench.replay import ProtectionPlan, ReplayConfig, ReplayEngine

START = datetime(2026, 1, 1, tzinfo=UTC)


def candle(
    index: int,
    open_price: str,
    high: str,
    low: str,
    close: str,
    *,
    is_closed: bool = True,
) -> Candle:
    opened_at = START + timedelta(minutes=index)
    return Candle(
        symbol="BTCUSDT",
        timeframe="1m",
        opened_at=opened_at,
        closed_at=opened_at + timedelta(minutes=1),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("10"),
        is_closed=is_closed,
    )


def request(
    *,
    quantity: str = "1",
    order_type: OrderType = OrderType.MARKET,
    side: OrderSide = OrderSide.BUY,
    price: str | None = None,
    client_order_id: str = "replay-entry-1",
) -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        symbol="BTCUSDT",
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        price=None if price is None else Decimal(price),
    )


class ReplayEngineTests(unittest.TestCase):
    def test_execution_delay_skips_configured_number_of_bars(self) -> None:
        engine = ReplayEngine("BTCUSDT", ReplayConfig(execution_delay_bars=1))
        engine.submit_entry(
            request(
                client_order_id="delayed",
                order_type=OrderType.LIMIT,
                price="100",
            ),
            ProtectionPlan(Decimal("90")),
        )
        first = candle(0, open_price="100", high="101", low="99", close="100")
        second = candle(1, open_price="100", high="101", low="99", close="100")
        self.assertEqual(engine.on_candle(first), ())
        self.assertTrue(engine.on_candle(second))

    def test_market_order_waits_for_next_bar_and_uses_open_with_slippage(self) -> None:
        engine = ReplayEngine(
            "BTCUSDT",
            ReplayConfig(fee_rate=Decimal("0"), slippage_percent=Decimal("1")),
        )
        engine.submit_entry(request(), ProtectionPlan(Decimal("90"), Decimal("120")))
        self.assertEqual(engine.position.side, PositionSide.FLAT)
        fills = engine.on_candle(candle(0, "100", "105", "95", "102"))
        self.assertEqual(fills[0].reason, FillReason.ENTRY)
        self.assertEqual(fills[0].price, Decimal("101"))
        self.assertEqual(engine.position.side, PositionSide.LONG)

    def test_modelled_slippage_is_attributed_per_fill(self) -> None:
        engine = ReplayEngine(
            "BTCUSDT",
            ReplayConfig(fee_rate=Decimal("0"), slippage_percent=Decimal("1")),
        )
        engine.submit_entry(request(), ProtectionPlan(Decimal("90"), Decimal("110")))
        entry_fill = engine.on_candle(candle(0, "100", "105", "95", "102"))[0]
        self.assertEqual(entry_fill.slippage_cost, Decimal("1"))
        exit_fill = engine.on_candle(candle(1, "105", "112", "101", "110"))[-1]
        self.assertEqual(exit_fill.reason, FillReason.TAKE_PROFIT)
        self.assertEqual(exit_fill.slippage_cost, Decimal("1.10"))

    def test_limit_order_fills_only_when_crossed(self) -> None:
        engine = ReplayEngine("BTCUSDT", ReplayConfig(slippage_percent=Decimal("0")))
        order = engine.submit_entry(
            request(order_type=OrderType.LIMIT, price="95"),
            ProtectionPlan(Decimal("90")),
        )
        self.assertEqual(engine.on_candle(candle(0, "100", "105", "96", "102")), ())
        self.assertEqual(order.status, OrderStatus.ACCEPTED)
        fills = engine.on_candle(candle(1, "97", "100", "94", "96"))
        self.assertEqual(fills[0].price, Decimal("95"))
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_partial_fills_resize_protected_quantity(self) -> None:
        engine = ReplayEngine(
            "BTCUSDT",
            ReplayConfig(
                fee_rate=Decimal("0"),
                slippage_percent=Decimal("0"),
                max_fill_quantity_per_bar=Decimal("1"),
            ),
        )
        order = engine.submit_entry(
            request(quantity="2"),
            ProtectionPlan(Decimal("90"), Decimal("120")),
        )
        engine.on_candle(candle(0, "100", "105", "95", "102"))
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(engine.protected_quantity, Decimal("1"))
        engine.on_candle(candle(1, "102", "108", "96", "105"))
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(engine.protected_quantity, Decimal("2"))

    def test_gap_through_stop_fills_at_first_available_price(self) -> None:
        engine = ReplayEngine(
            "BTCUSDT",
            ReplayConfig(fee_rate=Decimal("0"), slippage_percent=Decimal("0")),
        )
        engine.submit_entry(request(), ProtectionPlan(Decimal("90"), Decimal("120")))
        engine.on_candle(candle(0, "100", "105", "95", "102"))
        fills = engine.on_candle(candle(1, "80", "85", "75", "82"))
        self.assertEqual(fills[0].reason, FillReason.STOP_LOSS)
        self.assertEqual(fills[0].price, Decimal("80"))
        self.assertEqual(engine.position.side, PositionSide.FLAT)

    def test_short_gap_through_stop_uses_first_available_price(self) -> None:
        engine = ReplayEngine(
            "BTCUSDT",
            ReplayConfig(fee_rate=Decimal("0"), slippage_percent=Decimal("0")),
        )
        engine.submit_entry(
            request(side=OrderSide.SELL),
            ProtectionPlan(Decimal("110"), Decimal("80")),
        )
        engine.on_candle(candle(0, "100", "105", "95", "98"))
        fills = engine.on_candle(candle(1, "120", "125", "115", "122"))
        self.assertEqual(fills[0].reason, FillReason.STOP_LOSS)
        self.assertEqual(fills[0].price, Decimal("120"))
        self.assertEqual(engine.position.side, PositionSide.FLAT)

    def test_ambiguous_stop_and_take_profit_uses_conservative_stop(self) -> None:
        engine = ReplayEngine(
            "BTCUSDT",
            ReplayConfig(fee_rate=Decimal("0"), slippage_percent=Decimal("0")),
        )
        engine.submit_entry(request(), ProtectionPlan(Decimal("90"), Decimal("120")))
        fills = engine.on_candle(candle(0, "100", "125", "85", "105"))
        self.assertEqual(fills[-1].reason, FillReason.STOP_LOSS)
        self.assertTrue(fills[-1].ambiguous_bar)
        self.assertTrue(engine.completed_trades[-1].ambiguous_bar)

    def test_fees_and_funding_are_reported_separately(self) -> None:
        engine = ReplayEngine(
            "BTCUSDT",
            ReplayConfig(fee_rate=Decimal("0.001"), slippage_percent=Decimal("0")),
        )
        engine.submit_entry(request(), ProtectionPlan(Decimal("90"), Decimal("110")))
        engine.on_candle(candle(0, "100", "105", "95", "102"))
        engine.apply_funding(Decimal("0.2"))
        engine.on_candle(candle(1, "105", "111", "101", "110"))
        result = engine.completed_trades[-1]
        self.assertEqual(result.gross_pnl, Decimal("10"))
        self.assertEqual(result.fees, Decimal("0.210"))
        self.assertEqual(result.funding, Decimal("0.2"))
        self.assertEqual(result.net_pnl, Decimal("9.590"))

    def test_limit_entry_uses_maker_fee_and_protection_exit_uses_taker_fee(self) -> None:
        engine = ReplayEngine(
            "BTCUSDT",
            ReplayConfig(
                fee_rate=Decimal("0"),
                maker_fee_rate=Decimal("0.001"),
                taker_fee_rate=Decimal("0.002"),
                slippage_percent=Decimal("0"),
            ),
        )
        engine.submit_entry(
            request(order_type=OrderType.LIMIT, price="100"),
            ProtectionPlan(Decimal("90")),
        )
        engine.on_candle(candle(0, "100", "105", "95", "102"))
        engine.on_candle(candle(1, "90", "95", "85", "88"))
        self.assertEqual(engine.completed_trades[-1].fees, Decimal("0.280"))

    def test_mark_price_can_trigger_stop_when_trade_candle_does_not(self) -> None:
        engine = ReplayEngine(
            "BTCUSDT",
            ReplayConfig(fee_rate=Decimal("0"), slippage_percent=Decimal("0")),
        )
        engine.submit_entry(request(), ProtectionPlan(Decimal("90")))
        engine.on_candle(candle(0, "100", "105", "95", "102"))
        trade = candle(1, "100", "103", "95", "99")
        mark = candle(1, "100", "102", "89", "96")
        fills = engine.on_candle(trade, mark)
        self.assertEqual(fills[-1].reason, FillReason.STOP_LOSS)
        self.assertEqual(fills[-1].price, Decimal("90"))

    def test_unclosed_and_out_of_order_candles_are_rejected(self) -> None:
        engine = ReplayEngine("BTCUSDT")
        with self.assertRaises(ValueError):
            engine.on_candle(candle(0, "100", "105", "95", "102", is_closed=False))
        engine.on_candle(candle(1, "100", "105", "95", "102"))
        with self.assertRaises(ValueError):
            engine.on_candle(candle(0, "100", "105", "95", "102"))

    def test_emergency_stop_is_idempotent_and_cancels_remaining_entry(self) -> None:
        engine = ReplayEngine(
            "BTCUSDT",
            ReplayConfig(
                fee_rate=Decimal("0"),
                slippage_percent=Decimal("0"),
                max_fill_quantity_per_bar=Decimal("1"),
            ),
        )
        order = engine.submit_entry(
            request(quantity="2"),
            ProtectionPlan(Decimal("90"), Decimal("120")),
        )
        engine.on_candle(candle(0, "100", "105", "95", "102"))
        first = engine.emergency_stop("emergency-1", Decimal("99"), START + timedelta(minutes=2))
        second = engine.emergency_stop("emergency-1", Decimal("50"), START + timedelta(minutes=3))
        self.assertEqual(first, second)
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.assertEqual(engine.position.side, PositionSide.FLAT)
        emergency_fills = [
            fill for fill in engine.fills if fill.reason is FillReason.EMERGENCY_FLATTEN
        ]
        self.assertEqual(len(emergency_fills), 1)

    def test_snapshot_is_json_safe_and_restores_open_position(self) -> None:
        config = ReplayConfig(fee_rate=Decimal("0"), slippage_percent=Decimal("0"))
        original = ReplayEngine("BTCUSDT", config)
        original.submit_entry(request(), ProtectionPlan(Decimal("90"), Decimal("110")))
        original.on_candle(candle(0, "100", "105", "95", "102"))
        snapshot = json.loads(json.dumps(original.snapshot()))
        restored = ReplayEngine.restore(snapshot)
        next_bar = candle(1, "103", "111", "101", "109")
        original.on_candle(next_bar)
        restored.on_candle(next_bar)
        self.assertEqual(restored.position, original.position)
        self.assertEqual(restored.fills, original.fills)
        self.assertEqual(restored.completed_trades, original.completed_trades)


if __name__ == "__main__":
    unittest.main()
