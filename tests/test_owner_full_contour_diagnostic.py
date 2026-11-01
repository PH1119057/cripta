from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import bybit_workbench.universal_entry.owner_full_contour_control as control
import bybit_workbench.universal_entry.owner_full_contour_diagnostic as diagnostic
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    StrategyActivation,
    TradeDirection,
)
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.readiness import assess_strategy_runtime_readiness
from bybit_workbench.universal_exit.contracts import (
    ExitActionKind,
    ExitEvaluationStatus,
    ExitObservation,
)
from bybit_workbench.universal_exit.engine import UniversalExitEngine

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
INDEX = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
PRIVATE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
ENTRY_CONSUMER = (
    ROOT / "operations/connectivity/universal_entry_consumer.py"
).read_text(encoding="utf-8")
OBSERVER = (
    ROOT / "operations/monitoring/universal_entry_shadow.py"
).read_text(encoding="utf-8")
MIGRATION = (
    ROOT / "operations/sql/20261101_owner_full_contour_diagnostic.sql"
).read_text(encoding="utf-8")
RELEASE_INSTALLER = (
    ROOT / "operations/infrastructure/install_verified_release.sh"
).read_text(encoding="utf-8")


def _main_bundle():
    card = next(
        card
        for card in diagnostic.cards(
            approved_at=datetime(2026, 11, 1, tzinfo=UTC)
        )
        if card.strategy_id == diagnostic.MAIN_ID
    )
    activation = StrategyActivation(
        activation_id=diagnostic.MAIN_ACTIVATION_ID,
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True,
        enabled_at=datetime(2026, 11, 1, tzinfo=UTC),
        scope=FrozenPolicy.from_mapping({"mode": "OWNER_FULL_CONTOUR_DIAGNOSTIC"}),
        operator="owner",
        source="test",
    )
    entry, exit_plan = materialize_plans(card, activation)
    return card, activation, entry, exit_plan


def _position(exit_plan, direction: TradeDirection = TradeDirection.LONG):
    card, activation, entry, _ = _main_bundle()
    return StrategyPosition(
        strategy_position_id="diag-pos-1",
        account_ref="BYBIT:UNIFIED",
        exchange_position_key=f"{diagnostic.MAIN_ACCOUNT_ID}:LTCUSDT:0",
        position_idx=0,
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        strategy_activation_id=activation.activation_id,
        signal_id="diag-signal-1",
        strategy_attempt_id="diag-attempt-1",
        entry_decision_id="diag-decision-1",
        entry_execution_request_id="diag-request-1",
        entry_plan_fingerprint=entry.entry_plan_fingerprint,
        exit_plan_fingerprint=exit_plan.exit_plan_fingerprint,
        entry_command_id="diag-command-1",
        symbol="LTCUSDT",
        direction=direction,
        actual_avg_fill=Decimal("100"),
        actual_qty=Decimal("0.1"),
        fill_at=datetime(2026, 11, 1, tzinfo=UTC),
        strategy_execution_binding_id="binding-owner-full-contour-main-20261101",
        execution_leg="PRIMARY",
        trading_account_id=diagnostic.MAIN_ACCOUNT_ID,
        identity_status="PROVEN",
    )


def _observation(pnl: str) -> ExitObservation:
    now = datetime(2026, 11, 1, tzinfo=UTC)
    return ExitObservation(
        observation_id="diag-observation-" + pnl.replace("-", "m").replace(".", "_"),
        strategy_position_id="diag-pos-1",
        symbol="LTCUSDT",
        event_at=now,
        observed_at=now,
        received_at=now,
        event_kind="OWNER_FULL_CONTOUR_MARK",
        attributes=FrozenPolicy.from_mapping({"directional_pnl_pct": pnl}),
        source_refs=("test:owner-full-contour",),
    )


def test_diagnostic_cards_are_exact_materializable_and_execution_ready() -> None:
    diagnostic_cards = diagnostic.cards(approved_at=datetime(2026, 11, 1, tzinfo=UTC))
    assert {card.strategy_id for card in diagnostic_cards} == {
        "owner_full_contour_diag_main",
        "owner_full_contour_diag_sub",
    }
    for card in diagnostic_cards:
        assert card.strategy_version == diagnostic.VERSION
        assert card.scope.to_dict()["kind"] == "OWNER_FULL_CONTOUR_DIAGNOSTIC"
        entry = card.entry_policy.to_dict()
        assert entry["watch_policy"] == {"enabled": False}
        assert entry["execution_policy"] == {
            "order_type": "MARKET",
            "reference_value_path": "fact.price",
            "max_request_age_seconds": 15,
        }
        readiness = assess_strategy_runtime_readiness(card, observer_ready=True)
        assert readiness.execution_ready, [reason.code for reason in readiness.reasons]


def test_diagnostic_exit_rules_use_exact_half_percent_close() -> None:
    _card, _activation, _entry, exit_plan = _main_bundle()
    engine = UniversalExitEngine()
    tp = engine.evaluate(_position(exit_plan), exit_plan, _observation("0.5"))
    assert tp.status is ExitEvaluationStatus.DECISION_CREATED
    assert tp.decision is not None
    assert tp.decision.rule_id == "OWNER_DIAGNOSTIC_TP_0_5"
    assert tp.decision.action_kind is ExitActionKind.CLOSE
    assert tp.decision.requested_mutation.to_dict() == {
        "quantity": "ALL",
        "order_type": "MARKET",
    }
    sl = engine.evaluate(_position(exit_plan), exit_plan, _observation("-0.5"))
    assert sl.status is ExitEvaluationStatus.DECISION_CREATED
    assert sl.decision is not None
    assert sl.decision.rule_id == "OWNER_DIAGNOSTIC_SL_0_5"
    assert sl.decision.action_kind is ExitActionKind.CLOSE


def test_diagnostic_exchange_validator_is_exact_and_rejects_drift() -> None:
    payload = {
        "source": "universal_entry",
        "strategy_id": diagnostic.MAIN_ID,
        "strategy_version": diagnostic.VERSION,
        "trading_account_id": diagnostic.MAIN_ACCOUNT_ID,
        "capital_reservation_id": "reservation-1",
        "exchange_position_slot_claim_id": "slot-1",
        "strategy_execution_binding_id": "binding-owner-full-contour-main-20261101",
        "entry_plan_fingerprint": "e" * 64,
        "exit_plan_fingerprint": "x" * 64,
        "stake_usdt": "10",
        "price": "100",
        "entry_offset_pct": "0",
        "leverage": 1,
        "margin_mode": "ISOLATED",
        "side": "Buy",
        "initial_protection": {
            "stop_loss_enabled": True,
            "take_profit_enabled": False,
            "stop_loss_pct": str(diagnostic.STOP_LOSS_PCT),
            "take_profit_pct": None,
            "take_profit_price": None,
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
        },
    }
    signed = {
        "retCode": 0,
        "result": {"marginMode": "ISOLATED_MARGIN", "unifiedMarginStatus": 6},
    }
    assert diagnostic.validate_exchange_entry(
        payload=payload,
        symbol="LTCUSDT",
        trading_account_id=diagnostic.MAIN_ACCOUNT_ID,
        signed_account_info=signed,
    )
    drift = dict(payload)
    drift["stake_usdt"] = "100"
    with pytest.raises(ValueError, match="MARKET_ISOLATED_1X"):
        diagnostic.validate_exchange_entry(
            payload=drift,
            symbol="LTCUSDT",
            trading_account_id=diagnostic.MAIN_ACCOUNT_ID,
            signed_account_info=signed,
        )


def test_dashboard_is_control_trigger_only_and_market_observer_excludes_diagnostic() -> None:
    assert 'path == "/api/owner-full-contour-diagnostic"' in APP
    assert "request_owner_full_contour_run(" in APP
    endpoint = APP[APP.index('if path == "/api/owner-full-contour-diagnostic"'):]
    endpoint = endpoint[:endpoint.index('if path in {"/api/live/settings"')]
    for forbidden in (
        "INSERT INTO strategy_entry.strategy_signals",
        "INSERT INTO strategy_entry.entry_decisions",
        "INSERT INTO strategy_entry.execution_requests",
        "INSERT INTO runtime.position_ownership",
    ):
        assert forbidden not in endpoint
    assert "OWNER_FULL_CONTOUR_DIAGNOSTIC" in OBSERVER
    assert "load_active_strategy_bundles" in OBSERVER


def test_execution_smoke_fix_routes_exact_account_and_waits_terminal() -> None:
    assert "error LIKE 'EXCHANGE_MUTATION_BARRIER:%%'" in PRIVATE
    assert "control.account_live_arm_sessions arm" in APP
    assert "arm.release_commit=%s" in APP
    assert '"account_connector_id": test_connector_id' in APP
    assert "attempt<120" in INDEX
    assert "60 секунд" in INDEX


def test_terminal_success_requires_completed_universal_exit_lineage() -> None:
    assert "strategy_exit.exit_decisions" in ENTRY_CONSUMER
    assert "strategy_exit.execution_requests" in ENTRY_CONSUMER
    assert "strategy_exit.execution_dispatches" in ENTRY_CONSUMER
    assert "c.command_type='strategy_exit'" in ENTRY_CONSUMER
    assert "c.state='completed'" in ENTRY_CONSUMER
    assert "POSITION_CLOSED_WITHOUT_COMPLETED_UNIVERSAL_EXIT" in ENTRY_CONSUMER


def test_diagnostic_ui_and_schema_are_bounded() -> None:
    assert "Сквозной LONG" in INDEX
    assert "Сквозной SHORT" in INDEX
    assert "/api/owner-full-contour-diagnostic" in INDEX
    assert "owner_full_contour_one_active_run" in MIGRATION
    assert "'REQUESTED','MATERIALIZED','DISPATCHED','POSITION_OPEN'" in MIGRATION
    assert "OWNER_FULL_CONTOUR_REGISTRATION_REQUIRES_DISARMED_STATE" in MIGRATION
    assert "OWNER_FULL_CONTOUR_PARENT_P90_ACCOUNT_ARM_NOT_ACTIVE" in MIGRATION
    account_insert = MIGRATION[MIGRATION.index(
        "INSERT INTO control.account_live_arm_sessions("
    ):]
    account_insert = account_insert[:account_insert.index("RETURN p_session_id")]
    assert "\n    live_arm_session_id," not in account_insert
    assert "OWNER_FULL_CONTOUR_SYMBOL_ENTRY_MUTATION_PENDING" in MIGRATION
    assert "p_release_commit !~ '^[0-9a-f]{40}$'" in MIGRATION
    assert "p_release_commit !~ '^[0-9a-f]{40}     OR" not in MIGRATION



def test_diagnostic_migration_ends_at_single_transaction_commit() -> None:
    stripped = MIGRATION.rstrip()
    assert stripped.endswith("COMMIT;")
    assert stripped.count("\nCOMMIT;") == 1
    assert "COMMIT;\n\n     OR p_symbol NOT IN" not in stripped


def test_verified_release_registers_and_readbacks_diagnostic_capability() -> None:
    migration = "operations/sql/20261101_owner_full_contour_diagnostic.sql"
    installer = (
        "operations/implementation/install_owner_full_contour_diagnostic.py"
    )
    assert migration in RELEASE_INSTALLER
    migration_pos = RELEASE_INSTALLER.index(migration)
    install_call = RELEASE_INSTALLER.index(
        f"$runtime_release/{installer}\" install"
    )
    check_call = RELEASE_INSTALLER.index(
        f"$runtime_release/{installer}\" check"
    )
    assert migration_pos < install_call < check_call
    assert "runuser -u cripta" in RELEASE_INSTALLER[
        RELEASE_INSTALLER.rfind("runuser -u cripta", 0, install_call):install_call
    ]
    assert "db_mutation_steps=$((db_mutation_steps + 1))" in RELEASE_INSTALLER[
        install_call:check_call
    ]


def test_prestart_recovery_never_replays_stale_execution_smoke_entry() -> None:
    block = PRIVATE[
        PRIVATE.index("def resolve_prestart_entry_commands"):
        PRIVATE.index("def resolve_prestart_non_entry_running_commands")
    ]
    assert "api_post(" not in block
    assert "RESTART_NO_EXACT_EXCHANGE_OUTCOME" in block
    assert "SET state='failed'" in block


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _RetireDictRowConnection:
    def __init__(self):
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement, parameters=()):
        self.calls.append((statement, tuple(parameters)))
        if "SELECT strategy_id,strategy_version,strategy_config_fingerprint" in statement:
            return _Cursor(
                {
                    "strategy_id": diagnostic.MAIN_ID,
                    "strategy_version": diagnostic.VERSION,
                    "strategy_config_fingerprint": "f" * 64,
                }
            )
        if "SELECT count(*) AS active_count" in statement:
            return _Cursor({"active_count": 1})
        if "SELECT control.disarm_owner_full_contour_account_scope" in statement:
            return _Cursor((control.account_session_id("owner-fc-test"),))
        return _Cursor(None)


def test_retire_entry_rights_handles_dict_row_and_disarms_account_scope() -> None:
    connection = _RetireDictRowConnection()
    control.retire_entry_rights(
        connection,
        run_id="owner-fc-test",
        at=datetime(2026, 11, 1, tzinfo=UTC),
        reason="test dispatch",
    )
    sql = "\n".join(statement for statement, _params in connection.calls)
    assert "UPDATE strategy_entry.execution_permissions" in sql
    assert "UPDATE control.live_arm_sessions" in sql
    assert "SELECT control.disarm_owner_full_contour_account_scope" in sql
