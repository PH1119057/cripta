import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bybit_workbench.domain import (
    Candle,
    EnterIntent,
    InstrumentRules,
    Order,
    OrderRequest,
    Position,
)
from bybit_workbench.domain.types import (
    FillReason,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from bybit_workbench.execution import (
    ExecutionCommandKind,
    ExecutionCommandStatus,
)
from bybit_workbench.historical import (
    HistoricalAcceptancePolicy,
    HistoricalDataset,
    HistoricalEligibilityQuery,
    build_validation_report,
)
from bybit_workbench.persistence import EventJournal, TradingJournal, canonical_json
from bybit_workbench.replay import ReplayFill
from bybit_workbench.risk import RiskCheck, RiskDecision

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def intent() -> EnterIntent:
    return EnterIntent(
        intent_id="intent-audit-1",
        symbol="BTCUSDT",
        direction=PositionSide.LONG,
        order_type=OrderType.LIMIT,
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        leverage=Decimal("2"),
        reason="audit test",
        take_profit=Decimal("120"),
    )


def order(status: OrderStatus = OrderStatus.ACCEPTED) -> Order:
    request = OrderRequest(
        client_order_id="intent-audit-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
    )
    return Order(
        order_id="order-audit-1",
        request=request,
        status=status,
        filled_quantity=Decimal("0"),
        created_at=NOW,
        updated_at=NOW,
    )


class TradingJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "audit.db"
        self.journal = TradingJournal(self.path)

    def tearDown(self) -> None:
        self.journal.close()
        self.temp.cleanup()

    def start_chain(self) -> tuple[EnterIntent, Order]:
        trade_intent = intent()
        tracked_order = order()
        self.journal.start_strategy_run(
            "run-1",
            strategy_id="manual_protected_trade",
            strategy_version="1.0",
            code_version="test",
            mode="replay",
            symbol="BTCUSDT",
            parameters={"risk": "test"},
            started_at=NOW,
        )
        self.journal.record_strategy_decision(
            "decision-1",
            "run-1",
            inputs={"close": Decimal("100")},
            decision={"action": "enter"},
            candle_at=NOW,
            created_at=NOW,
        )
        self.journal.record_trade_intent(
            trade_intent,
            "run-1",
            decision_id="decision-1",
            created_at=NOW,
        )
        risk = RiskDecision(
            True,
            (RiskCheck("approved", True, "ok"),),
            normalized_order=tracked_order.request,
            normalized_stop=Decimal("90"),
            risk_budget=Decimal("10"),
            estimated_loss_at_stop=Decimal("10"),
        )
        self.journal.record_risk_decision(
            "risk-1",
            trade_intent.intent_id,
            risk,
            created_at=NOW,
        )
        self.journal.upsert_order(
            tracked_order,
            intent_id=trade_intent.intent_id,
            event_id="order-event-1",
            raw_payload={"status": "accepted"},
        )
        return trade_intent, tracked_order

    def test_schema_contains_required_audit_tables(self) -> None:
        expected = {
            "strategy_runs",
            "strategy_decisions",
            "trade_intents",
            "risk_decisions",
            "orders",
            "order_state_history",
            "executions",
            "position_snapshots",
            "stop_updates",
            "reconciliation_runs",
            "settings_versions",
            "engine_snapshots",
            "execution_commands",
            "historical_validation_reports",
        }
        connection = sqlite3.connect(self.path)
        try:
            actual = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertTrue(expected <= actual)

    def test_settings_are_redacted_canonical_and_deduplicated(self) -> None:
        settings = {
            "symbols": {"ETHUSDT", "BTCUSDT"},
            "api_key": "must-never-be-stored",
            "nested": {"Authorization": "Bearer secret-value"},
        }
        first = self.journal.save_settings_version("testnet", settings, created_at=NOW)
        second = self.journal.save_settings_version("testnet", settings, created_at=NOW)
        self.assertEqual(first, second)
        self.assertEqual(self.journal.table_count("settings_versions"), 1)
        row = self.journal._connection.execute(  # noqa: SLF001 - persistence verification
            "SELECT settings_json FROM settings_versions WHERE id=?", (first,)
        ).fetchone()
        stored = row["settings_json"]
        self.assertNotIn("must-never-be-stored", stored)
        self.assertNotIn("secret-value", stored)
        self.assertEqual(json.loads(stored)["api_key"], "***REDACTED***")

    def test_complete_trace_and_duplicate_suppression(self) -> None:
        trade_intent, tracked_order = self.start_chain()
        self.journal.upsert_order(
            tracked_order,
            intent_id=trade_intent.intent_id,
            event_id="order-event-1",
            raw_payload={"api_secret": "not-stored"},
        )
        fill = ReplayFill(
            execution_id="exec-audit-1",
            client_order_id=trade_intent.intent_id,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
            fee=Decimal("0.06"),
            reason=FillReason.ENTRY,
            occurred_at=NOW,
        )
        self.assertTrue(
            self.journal.record_replay_fill(
                fill,
                order_id=tracked_order.order_id,
                symbol="BTCUSDT",
            )
        )
        self.assertFalse(
            self.journal.record_replay_fill(
                fill,
                order_id=tracked_order.order_id,
                symbol="BTCUSDT",
            )
        )
        conflicting_fill = ReplayFill(
            execution_id=fill.execution_id,
            client_order_id=fill.client_order_id,
            side=fill.side,
            quantity=fill.quantity,
            price=Decimal("101"),
            fee=fill.fee,
            reason=fill.reason,
            occurred_at=fill.occurred_at,
        )
        with self.assertRaises(ValueError):
            self.journal.record_replay_fill(
                conflicting_fill,
                order_id=tracked_order.order_id,
                symbol="BTCUSDT",
            )
        self.journal.record_stop_update(
            run_id="run-1",
            intent_id=trade_intent.intent_id,
            order_id=tracked_order.order_id,
            symbol="BTCUSDT",
            status="confirmed",
            price=Decimal("90"),
            protected_quantity=Decimal("1"),
            reason="entry filled",
            occurred_at=NOW,
        )
        trace = self.journal.trace_for_intent(trade_intent.intent_id)
        self.assertEqual(trace["run"]["run_id"], "run-1")
        self.assertEqual(trace["decision"]["decision_id"], "decision-1")
        self.assertEqual(len(trace["risk_decisions"]), 1)
        self.assertEqual(len(trace["orders"]), 1)
        self.assertEqual(len(trace["executions"]), 1)
        self.assertEqual(len(trace["position_snapshots"]), 0)
        self.assertEqual(len(trace["stop_updates"]), 1)
        self.assertEqual(self.journal.table_count("order_state_history"), 1)

    def test_projection_and_current_snapshot_restore(self) -> None:
        _, tracked_order = self.start_chain()
        position = Position(
            "BTCUSDT",
            PositionSide.LONG,
            Decimal("1"),
            Decimal("100"),
        )
        self.journal.record_position_snapshot(
            position,
            source="replay",
            run_id="run-1",
            observed_at=NOW,
        )
        self.journal.save_engine_snapshot("run-1", "replay", {"sequence": 1})
        self.journal.save_engine_snapshot("run-1", "replay", {"sequence": 2})
        projection = self.journal.load_projection("BTCUSDT", run_id="run-1")
        self.assertEqual(projection.position, position)
        self.assertEqual(projection.active_orders, (tracked_order,))
        self.assertEqual(projection.replay_snapshot, {"sequence": 2})
        self.assertEqual(self.journal.table_count("engine_snapshots"), 2)
        current_count = self.journal._connection.execute(  # noqa: SLF001
            "SELECT COUNT(1) FROM engine_snapshots WHERE is_current=1"
        ).fetchone()[0]
        self.assertEqual(current_count, 1)

    def test_order_identity_cannot_be_silently_reused(self) -> None:
        _, tracked_order = self.start_chain()
        conflicting = Order(
            order_id=tracked_order.order_id,
            request=OrderRequest(
                client_order_id="different-client-id",
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=Decimal("1"),
                price=Decimal("100"),
            ),
            status=OrderStatus.ACCEPTED,
            created_at=NOW,
            updated_at=NOW,
        )
        with self.assertRaises(ValueError):
            self.journal.upsert_order(conflicting)

    def test_foreign_key_prevents_orphan_risk_decision(self) -> None:
        risk = RiskDecision(False, (RiskCheck("blocked", False, "test"),))
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.record_risk_decision("risk-orphan", "missing-intent", risk)

    def test_canonical_json_is_stable_for_sets(self) -> None:
        left = canonical_json({"symbols": {"ETHUSDT", "BTCUSDT"}})
        right = canonical_json({"symbols": {"BTCUSDT", "ETHUSDT"}})
        self.assertEqual(left, right)

    def test_execution_command_is_durable_and_idempotent(self) -> None:
        first = self.journal.create_execution_command(
            "command-1",
            ExecutionCommandKind.ENTRY,
            "entry:intent-audit-1",
            "BTCUSDT",
            {"qty": Decimal("1"), "api_secret": "never-store"},
            created_at=NOW,
        )
        duplicate = self.journal.create_execution_command(
            "ignored-duplicate-id",
            ExecutionCommandKind.ENTRY,
            "entry:intent-audit-1",
            "BTCUSDT",
            {"qty": Decimal("1"), "api_secret": "different-secret-is-redacted"},
            created_at=NOW,
        )
        self.assertEqual(first.command_id, duplicate.command_id)
        self.assertEqual(first.status, ExecutionCommandStatus.PLANNED)
        requested = self.journal.update_execution_command(
            first.command_id,
            ExecutionCommandStatus.REQUESTED,
            updated_at=NOW,
        )
        acknowledged = self.journal.update_execution_command(
            first.command_id,
            ExecutionCommandStatus.ACKNOWLEDGED,
            exchange_order_id="exchange-1",
            response={"api_key": "never-store", "retCode": 0},
            updated_at=NOW,
        )
        confirmed = self.journal.update_execution_command(
            first.command_id,
            ExecutionCommandStatus.CONFIRMED,
            updated_at=NOW,
        )
        self.assertEqual(requested.status, ExecutionCommandStatus.REQUESTED)
        self.assertEqual(acknowledged.exchange_order_id, "exchange-1")
        self.assertEqual(confirmed.status, ExecutionCommandStatus.CONFIRMED)
        row = self.journal._connection.execute(  # noqa: SLF001
            "SELECT request_json,response_json FROM execution_commands WHERE command_id=?",
            (first.command_id,),
        ).fetchone()
        self.assertNotIn("never-store", row["request_json"])
        self.assertNotIn("never-store", row["response_json"])

    def test_execution_idempotency_key_cannot_change_command_identity(self) -> None:
        self.journal.create_execution_command(
            "command-1",
            ExecutionCommandKind.ENTRY,
            "stable-key",
            "BTCUSDT",
            {"qty": "1"},
            created_at=NOW,
        )
        with self.assertRaises(ValueError):
            self.journal.create_execution_command(
                "command-2",
                ExecutionCommandKind.ENTRY,
                "stable-key",
                "BTCUSDT",
                {"qty": "2"},
                created_at=NOW,
            )

    def test_execution_command_state_cannot_regress(self) -> None:
        command = self.journal.create_execution_command(
            "command-1",
            ExecutionCommandKind.ENTRY,
            "entry-key",
            "BTCUSDT",
            {"qty": "1"},
            created_at=NOW,
        )
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.REQUESTED,
            updated_at=NOW,
        )
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.ACKNOWLEDGED,
            updated_at=NOW,
        )
        with self.assertRaises(ValueError):
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.REQUESTED,
                updated_at=NOW,
            )

    def test_historical_eligibility_is_exactly_bound_to_market_and_execution(self) -> None:
        candles = tuple(
            Candle(
                "BTCUSDT",
                "60",
                NOW + timedelta(hours=index),
                NOW + timedelta(hours=index + 1),
                Decimal("100"),
                Decimal("101"),
                Decimal("99"),
                Decimal("100"),
                Decimal("1"),
            )
            for index in range(4)
        )
        rules = InstrumentRules(
            "BTCUSDT",
            Decimal("0.1"),
            Decimal("0.001"),
            Decimal("0.001"),
            Decimal("5"),
            Decimal("1000"),
        )
        report = build_validation_report(
            strategy_id="algorithm-1",
            strategy_version="1.0",
            code_version="code-pass4",
            parameters={"period": 20},
            train_dataset=HistoricalDataset(candles[:2]),
            out_of_sample_dataset=HistoricalDataset(candles[2:]),
            in_sample_trades=(),
            out_of_sample_trades=(),
            policy=HistoricalAcceptancePolicy(
                minimum_out_of_sample_trades=1,
                require_positive_out_of_sample_pnl=False,
                require_production_data=True,
            ),
            fee_rate=Decimal("0"),
            maker_fee_rate=Decimal("0.0002"),
            taker_fee_rate=Decimal("0.00055"),
            slippage_percent=Decimal("0.1"),
            seed=0,
            generated_at=NOW,
            mark_price_complete=True,
            funding_complete=True,
            production_equivalent=True,
            price_trigger="MarkPrice",
            instrument_rules=rules,
        )
        self.journal.save_historical_validation_report("exact-report", report)
        exact = HistoricalEligibilityQuery.from_instrument(
            symbol="BTCUSDT",
            timeframe="60",
            code_version="code-pass4",
            instrument_rules=rules,
            maker_fee_rate=Decimal("0.0002"),
            taker_fee_rate=Decimal("0.00055"),
            slippage_percent=Decimal("0.1"),
        )
        stored = self.journal.latest_historical_eligibility(
            report.strategy_id, report.strategy_version, report.parameters_fingerprint, exact
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertTrue(stored.production_equivalent)

        mismatches = (
            HistoricalEligibilityQuery.from_instrument(
                symbol="BTCUSDT", timeframe="240", code_version="code-pass4",
                instrument_rules=rules, maker_fee_rate=Decimal("0.0002"),
                taker_fee_rate=Decimal("0.00055"), slippage_percent=Decimal("0.1"),
            ),
            HistoricalEligibilityQuery.from_instrument(
                symbol="BTCUSDT", timeframe="60", code_version="different-code",
                instrument_rules=rules, maker_fee_rate=Decimal("0.0002"),
                taker_fee_rate=Decimal("0.00055"), slippage_percent=Decimal("0.1"),
            ),
            HistoricalEligibilityQuery.from_instrument(
                symbol="BTCUSDT", timeframe="60", code_version="code-pass4",
                instrument_rules=rules, maker_fee_rate=Decimal("0.0003"),
                taker_fee_rate=Decimal("0.00055"), slippage_percent=Decimal("0.1"),
            ),
            HistoricalEligibilityQuery.from_instrument(
                symbol="UNIUSDT", timeframe="60", code_version="code-pass4",
                instrument_rules=InstrumentRules(
                    "UNIUSDT", Decimal("0.0001"), Decimal("0.1"), Decimal("0.1"),
                    Decimal("5"), Decimal("100000"),
                ),
                maker_fee_rate=Decimal("0.0002"),
                taker_fee_rate=Decimal("0.00055"), slippage_percent=Decimal("0.1"),
            ),
        )
        for query in mismatches:
            self.assertIsNone(
                self.journal.latest_historical_eligibility(
                    report.strategy_id,
                    report.strategy_version,
                    report.parameters_fingerprint,
                    query,
                )
            )

    def test_historical_eligibility_report_is_durable(self) -> None:
        candles = tuple(
            Candle(
                "BTCUSDT",
                "1",
                NOW + timedelta(minutes=index),
                NOW + timedelta(minutes=index + 1),
                Decimal("100"),
                Decimal("101"),
                Decimal("99"),
                Decimal("100"),
                Decimal("1"),
            )
            for index in range(4)
        )
        train = HistoricalDataset(candles[:2])
        test = HistoricalDataset(candles[2:])
        report = build_validation_report(
            strategy_id="algorithm-1",
            strategy_version="1.0",
            code_version="test-commit",
            parameters={"period": 20},
            train_dataset=train,
            out_of_sample_dataset=test,
            in_sample_trades=(),
            out_of_sample_trades=(),
            policy=HistoricalAcceptancePolicy(minimum_out_of_sample_trades=1),
            fee_rate=Decimal("0.0006"),
            slippage_percent=Decimal("0.1"),
            seed=0,
            generated_at=NOW,
        )
        self.journal.save_historical_validation_report("report-1", report)
        query = HistoricalEligibilityQuery.from_instrument(
            symbol="BTCUSDT",
            timeframe="1",
            code_version="test-commit",
            instrument_rules=InstrumentRules(
                "BTCUSDT",
                Decimal("0.1"),
                Decimal("0.001"),
                Decimal("0.001"),
                Decimal("5"),
                Decimal("1000"),
            ),
            maker_fee_rate=Decimal("0.0002"),
            taker_fee_rate=Decimal("0.00055"),
            slippage_percent=Decimal("0.1"),
        )
        self.assertIsNone(
            self.journal.latest_historical_eligibility(
                report.strategy_id,
                report.strategy_version,
                report.parameters_fingerprint,
                query,
            )
        )
        self.assertEqual(self.journal.table_count("historical_validation_reports"), 1)


class MigrationCompatibilityTests(unittest.TestCase):
    def test_existing_event_database_is_migrated_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.db"
            event_journal = EventJournal(path)
            event_journal.append("legacy.event", "preserve me")
            event_journal.close()
            trading = TradingJournal(path)
            try:
                self.assertEqual(trading.table_count("system_events"), 1)
                self.assertEqual(trading.table_count("strategy_runs"), 0)
            finally:
                trading.close()

    def test_newer_database_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "future.db"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)"
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (999, 'future')"
            )
            connection.commit()
            connection.close()
            with self.assertRaises(RuntimeError):
                TradingJournal(path)


if __name__ == "__main__":
    unittest.main()
