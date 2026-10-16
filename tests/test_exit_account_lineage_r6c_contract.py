from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path


from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import FrozenPolicy, TradeDirection
from bybit_workbench.universal_exit.contracts import ExitActionKind, ExitDecision, ExitExecutionRequest
from bybit_workbench.universal_exit.execution_bridge import prepare_runtime_exit_command

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def proven_position() -> StrategyPosition:
    return StrategyPosition(
        strategy_position_id="sp-r6c",
        account_ref="BYBIT:UNIFIED",
        exchange_position_key="BYBIT:ta-r6c:LINEAR:USDT:BTCUSDT:0",
        position_idx=0,
        strategy_id="s",
        strategy_version="1",
        strategy_config_fingerprint="s-fp",
        strategy_activation_id="a",
        signal_id="sig",
        strategy_attempt_id="attempt",
        entry_decision_id="ed",
        entry_execution_request_id="er",
        entry_plan_fingerprint="ep",
        exit_plan_fingerprint="xp",
        entry_command_id="cmd",
        symbol="BTCUSDT",
        direction=TradeDirection.LONG,
        actual_avg_fill=Decimal("100"),
        actual_qty=Decimal("1"),
        fill_at=NOW,
        strategy_execution_binding_id="seb-r6c",
        execution_leg="PRIMARY",
        trading_account_id="ta-r6c",
        identity_status="PROVEN",
    )


def request_for(position: StrategyPosition) -> ExitExecutionRequest:
    decision = ExitDecision(
        exit_decision_id="xd",
        strategy_position_id=position.strategy_position_id,
        strategy_id=position.strategy_id,
        strategy_version=position.strategy_version,
        strategy_config_fingerprint=position.strategy_config_fingerprint,
        exit_plan_fingerprint=position.exit_plan_fingerprint,
        rule_id="r",
        action_kind=ExitActionKind.CLOSE,
        requested_mutation=FrozenPolicy.from_mapping(
            {"quantity": "ALL", "order_type": "MARKET"}
        ),
        source_refs=("fact",),
        decided_at=NOW,
    )
    return ExitExecutionRequest.from_decision(
        execution_request_id="xr",
        decision=decision,
        position=position,
        requested_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )


def test_exit_request_copies_exact_proven_account_lineage() -> None:
    request = request_for(proven_position())
    assert request.strategy_execution_binding_id == "seb-r6c"
    assert request.execution_leg == "PRIMARY"
    assert request.trading_account_id == "ta-r6c"
    assert request.identity_status == "PROVEN"
    prepared = prepare_runtime_exit_command(request, proven_position(), now=NOW)
    assert prepared.payload["trading_account_id"] == "ta-r6c"
    assert prepared.payload["strategy_execution_binding_id"] == "seb-r6c"


def test_legacy_position_stays_explicitly_unknown() -> None:
    legacy = StrategyPosition(
        strategy_position_id="sp-legacy",
        account_ref="BYBIT:UNIFIED",
        exchange_position_key="BYBIT:UNIFIED:LINEAR:USDT:BTCUSDT:0",
        position_idx=0,
        strategy_id="s",
        strategy_version="1",
        strategy_config_fingerprint="s-fp",
        strategy_activation_id="a",
        signal_id="sig",
        strategy_attempt_id="attempt",
        entry_decision_id="ed",
        entry_execution_request_id="er",
        entry_plan_fingerprint="ep",
        exit_plan_fingerprint="xp",
        entry_command_id="cmd",
        symbol="BTCUSDT",
        direction=TradeDirection.LONG,
        actual_avg_fill=Decimal("100"),
        actual_qty=Decimal("1"),
        fill_at=NOW,
    )
    assert legacy.identity_status == "LEGACY_UNKNOWN"
    assert legacy.trading_account_id is None


def test_partial_proven_identity_is_rejected() -> None:
    try:
        StrategyPosition(
            strategy_position_id="sp-bad",
            account_ref="BYBIT:UNIFIED",
            exchange_position_key="BYBIT:ta:LINEAR:USDT:BTCUSDT:0",
            position_idx=0,
            strategy_id="s",
            strategy_version="1",
            strategy_config_fingerprint="s-fp",
            strategy_activation_id="a",
            signal_id="sig",
            strategy_attempt_id="attempt",
            entry_decision_id="ed",
            entry_execution_request_id="er",
            entry_plan_fingerprint="ep",
            exit_plan_fingerprint="xp",
            entry_command_id="cmd",
            symbol="BTCUSDT",
            direction=TradeDirection.LONG,
            actual_avg_fill=Decimal("100"),
            actual_qty=Decimal("1"),
            fill_at=NOW,
            trading_account_id="ta",
            identity_status="PROVEN",
        )
    except ValueError as exc:
        assert "requires binding/account lineage" in str(exc)
    else:
        raise AssertionError("partial PROVEN identity was accepted")


def test_r6c_storage_migration_is_additive_and_identity_checked() -> None:
    store = (ROOT / "src/bybit_workbench/universal_exit/execution_store.py").read_text(encoding="utf-8")
    migration = (ROOT / "operations/sql/20261016_exit_account_lineage_r6c.sql").read_text(encoding="utf-8")
    assert "strategy_execution_binding_id,execution_leg,trading_account_id" in store
    assert "exit_execution_requests_identity_check" in migration
    assert "DROP " not in migration.upper()
    assert "DELETE FROM" not in migration.upper()