from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bybit_workbench.exit_economics import calculate_close_economics
from bybit_workbench.mayak.core.live import LiveMayakEngine


ROOT = Path(__file__).parents[1]
MIGRATION = (ROOT / "operations/sql/20260831_shared_context_geometry_ownership.sql").read_text(
    encoding="utf-8"
)
PRIVATE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
ANALYST = (ROOT / "operations/monitoring/m3_trade_analyst.py").read_text(encoding="utf-8")
SUPERVISOR = (ROOT / "operations/monitoring/position_supervisor.py").read_text(
    encoding="utf-8"
)
ARCHIVE = (ROOT / "operations/dashboard/archive_v2.py").read_text(encoding="utf-8")


def _engine() -> LiveMayakEngine:
    return LiveMayakEngine(symbols=("BTCUSDT", "ETHUSDT"))


def test_shared_context_has_stable_id_for_same_snapshot() -> None:
    engine = _engine()
    now = datetime(2026, 8, 31, tzinfo=UTC)
    assert engine.snapshot(now)["dispatcher_handoff"]["market_context_id"] == engine.snapshot(now)[
        "dispatcher_handoff"
    ]["market_context_id"]


def test_shared_context_declares_no_trading_command() -> None:
    handoff = _engine().snapshot(datetime(2026, 8, 31, tzinfo=UTC))["dispatcher_handoff"]
    assert handoff["provenance"]["trading_command"] is False


def test_shared_context_is_marked_immutable() -> None:
    handoff = _engine().snapshot(datetime(2026, 8, 31, tzinfo=UTC))["dispatcher_handoff"]
    assert handoff["provenance"]["immutable"] is True


def test_shared_context_matches_canonical_schema() -> None:
    schema = json.loads(
        (ROOT / "config/strategy_dispatcher/MAYAK_HANDOFF_SCHEMA_V1.json").read_text(
            encoding="utf-8"
        )
    )
    handoff = _engine().snapshot(datetime(2026, 8, 31, tzinfo=UTC))["dispatcher_handoff"]
    assert set(handoff) == set(schema["properties"])
    assert set(schema["required"]) <= set(handoff)


def test_shared_context_table_is_immutable() -> None:
    assert "shared_market_contexts_immutable" in MIGRATION


def test_geometry_table_is_immutable() -> None:
    assert "entry_geometry_handoffs_immutable" in MIGRATION


def test_future_geometry_is_rejected_by_database() -> None:
    assert "geometry_observed_at <= signal_at" in MIGRATION


def test_geometry_is_bound_to_exact_entry_command() -> None:
    assert "entry_command_id text PRIMARY KEY" in MIGRATION
    assert "geometry_handoff_id text UNIQUE NOT NULL" in MIGRATION


def test_position_ownership_has_stable_multibot_identity() -> None:
    for field in ("position_id", "trade_id", "bot_instance_id", "entry_command_id"):
        assert field in MIGRATION


def test_runtime_does_not_queue_m3_without_geometry() -> None:
    assert "нет причинной неизменяемой геометрии Entry" in PRIVATE


def test_runtime_records_exchange_execution_ids() -> None:
    assert "exchange_order_ids" in PRIVATE
    assert "client_order_ids" in PRIVATE
    assert "execution_ids" in PRIVATE


def test_supervisor_close_command_carries_position_id() -> None:
    assert '"position_id": position.position_id' in SUPERVISOR


def test_analyst_does_not_select_close_by_symbol_and_time() -> None:
    assert "payload_json::jsonb->>'position_id'=%s" in ANALYST
    assert "command_type='close' AND symbol=%s" not in ANALYST


def test_analyst_uses_canonical_trade_id() -> None:
    assert 'ownership["trade_id"]' in ANALYST


def test_unknown_funding_does_not_claim_complete_net() -> None:
    economics = calculate_close_economics(
        side="Buy",
        entry_price=Decimal("100"),
        qty=Decimal("1"),
        executable_close_price=Decimal("101"),
        entry_fee_actual=Decimal("0.02"),
        exit_fee_rate=Decimal("0.00055"),
    )
    assert economics.actual_net_pnl is None
    assert economics.net_completeness == "PARTIAL_NO_FUNDING"


def test_known_funding_produces_complete_net() -> None:
    economics = calculate_close_economics(
        side="Sell",
        entry_price=Decimal("100"),
        qty=Decimal("1"),
        executable_close_price=Decimal("99"),
        entry_fee_actual=Decimal("0.02"),
        exit_fee_rate=Decimal("0.00055"),
        funding_realized=Decimal("-0.01"),
    )
    assert economics.actual_net_pnl is not None
    assert economics.net_completeness == "COMPLETE"


def test_analyst_persists_net_without_funding_separately() -> None:
    assert "actual_net_without_funding" in ANALYST
    assert '"actual_net_pnl": None' in ANALYST


def test_archive_exports_shared_context() -> None:
    assert '"mayak_v2.shared_market_contexts"' in ARCHIVE


def test_archive_exports_geometry_and_ownership() -> None:
    assert '"monitoring.entry_geometry_handoffs"' in ARCHIVE
    assert '"runtime.position_ownership"' in ARCHIVE


def test_analytics_views_share_operational_semantics() -> None:
    assert "analytics.shared_market_context_consumption" in MIGRATION
    assert "analytics.position_lifecycle_identity" in MIGRATION


def test_runtime_role_receives_explicit_new_table_permissions() -> None:
    assert "GRANT SELECT, INSERT ON mayak_v2.shared_market_contexts TO cripta" in MIGRATION
    assert "runtime.position_ownership TO cripta" in MIGRATION


def test_no_live_clean_break_rule_was_invented() -> None:
    assert "P45.1_TWO_COMPLETED_CLOSES" not in SUPERVISOR
    assert "observe_clean_break" not in SUPERVISOR
    assert 'STRUCTURAL_BREAK_RULE = "NOT_PROVEN"' in SUPERVISOR
    assert "STRUCTURAL_EARLY_EXIT_ENABLED = False" in SUPERVISOR
