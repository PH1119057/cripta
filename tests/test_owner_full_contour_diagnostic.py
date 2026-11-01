from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import bybit_workbench.universal_entry.owner_full_contour_control as control
import bybit_workbench.universal_entry.owner_full_contour_diagnostic as diagnostic
import bybit_workbench.universal_entry.owner_full_contour_runtime as owner_runtime
from bybit_workbench.strategy_execution_binding_policy import (
    DEDICATED_STRATEGY,
    SUBORDINATE_DIAGNOSTIC,
    real_binding_allocation_allowed,
)
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
MIGRATION_V1 = (
    ROOT / "operations/sql/20261101_owner_full_contour_diagnostic.sql"
).read_text(encoding="utf-8")
MIGRATION = (
    ROOT / "operations/sql/20261101_owner_full_contour_diagnostic_v2.sql"
).read_text(encoding="utf-8")
SUBORDINATE_MIGRATION = (
    ROOT / "operations/sql/20261101_subordinate_diagnostic_binding.sql"
).read_text(encoding="utf-8")
ENTRY_ADMISSION = (ROOT / "src/bybit_workbench/entry_admission.py").read_text(
    encoding="utf-8"
)
ACCOUNT_ARM = (ROOT / "src/bybit_workbench/account_live_arm.py").read_text(
    encoding="utf-8"
)
OWNER_RUNTIME = (
    ROOT / "src/bybit_workbench/universal_entry/owner_full_contour_runtime.py"
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
        strategy_execution_binding_id=diagnostic.MAIN_BINDING_ID,
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


def test_diagnostic_forced_exit_rule_closes_immediately_through_exit_engine() -> None:
    _card, _activation, _entry, exit_plan = _main_bundle()
    position = _position(exit_plan)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    observation = ExitObservation(
        observation_id="diag-forced-exit",
        strategy_position_id=position.strategy_position_id,
        symbol=position.symbol,
        event_at=now,
        observed_at=now,
        received_at=now,
        event_kind=diagnostic.EXIT_TRIGGER_EVENT_KIND,
        attributes=FrozenPolicy.from_mapping({"directional_pnl_pct": "0"}),
        source_refs=("test:owner-full-contour-forced-exit",),
    )
    evaluation = UniversalExitEngine().evaluate(position, exit_plan, observation)
    assert evaluation.status is ExitEvaluationStatus.DECISION_CREATED
    assert evaluation.decision is not None
    assert evaluation.decision.rule_id == "OWNER_DIAGNOSTIC_FORCE_CLOSE"
    assert evaluation.decision.action_kind is ExitActionKind.CLOSE
    assert evaluation.decision.requested_mutation.to_dict() == {
        "quantity": "ALL",
        "order_type": "MARKET",
    }


def test_diagnostic_exchange_validator_is_exact_and_rejects_drift() -> None:
    payload = {
        "source": "universal_entry",
        "strategy_id": diagnostic.MAIN_ID,
        "strategy_version": diagnostic.VERSION,
        "trading_account_id": diagnostic.MAIN_ACCOUNT_ID,
        "capital_reservation_id": "reservation-1",
        "exchange_position_slot_claim_id": "slot-1",
        "strategy_execution_binding_id": diagnostic.MAIN_BINDING_ID,
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
    assert "owner_full_contour_one_active_run" in MIGRATION_V1
    assert "'REQUESTED','MATERIALIZED','DISPATCHED','POSITION_OPEN'" in MIGRATION_V1
    assert "OWNER_FULL_CONTOUR_V2_REGISTRATION_REQUIRES_IDLE_DISARMED_STATE" in MIGRATION
    assert "OWNER_FULL_CONTOUR_PARENT_P90_RIGHTS_MUST_BE_OFF" in MIGRATION
    account_insert = MIGRATION[MIGRATION.index(
        "INSERT INTO control.account_live_arm_sessions("
    ):]
    account_insert = account_insert[:account_insert.index("RETURN p_session_id")]
    assert "\n    live_arm_session_id," not in account_insert
    assert "OWNER_FULL_CONTOUR_SYMBOL_ENTRY_MUTATION_PENDING" in MIGRATION
    assert "p_release_commit !~ '^[0-9a-f]{40}$'" in MIGRATION
    assert "p_release_commit !~ '^[0-9a-f]{40}     OR" not in MIGRATION



def test_diagnostic_migration_ends_at_single_transaction_commit() -> None:
    for migration in (MIGRATION_V1, MIGRATION):
        stripped = migration.rstrip()
        assert stripped.endswith("COMMIT;")
        assert stripped.count("\nCOMMIT;") == 1
        assert "COMMIT;\n\n     OR p_symbol NOT IN" not in stripped


def test_v2_control_owns_bounded_entry_window_without_parent_p90_arm() -> None:
    source = Path(
        ROOT / "src/bybit_workbench/universal_entry/owner_full_contour_control.py"
    ).read_text(encoding="utf-8")
    assert "OWNER_FULL_CONTOUR_MAINNET_GATE_MUST_START_CLOSED" in source
    assert "OWNER_FULL_CONTOUR_PARENT_P90_PERMISSION_MUST_BE_OFF" in source
    assert "OWNER_FULL_CONTOUR_PARENT_P90_ARM_MUST_BE_OFF" in source
    assert "OWNER_FULL_CONTOUR_PARENT_P90_ACCOUNT_ARM_MUST_BE_OFF" in source
    assert "owner full-contour diagnostic Entry window" in source
    assert "SET enabled=1" in source
    assert "SET enabled=0" in source
    assert "global NEW-Entry gate" in (
        ROOT / "docs/TRADING_CONTOUR_RU.md"
    ).read_text(encoding="utf-8")


def test_subordinate_binding_mode_is_exact_diagnostic_exception() -> None:
    assert real_binding_allocation_allowed(
        strategy_id=diagnostic.MAIN_ID,
        strategy_version=diagnostic.VERSION,
        allocation_mode=SUBORDINATE_DIAGNOSTIC,
    )
    assert not real_binding_allocation_allowed(
        strategy_id=diagnostic.MAIN_ID,
        strategy_version=diagnostic.VERSION,
        allocation_mode=DEDICATED_STRATEGY,
    )
    assert real_binding_allocation_allowed(
        strategy_id="ordinary_strategy",
        strategy_version="1",
        allocation_mode=DEDICATED_STRATEGY,
    )
    assert not real_binding_allocation_allowed(
        strategy_id="ordinary_strategy",
        strategy_version="1",
        allocation_mode=SUBORDINATE_DIAGNOSTIC,
    )


def test_subordinate_schema_preserves_dedicated_exclusivity() -> None:
    assert (
        "allocation_mode IN ('DEDICATED_STRATEGY','SUBORDINATE_DIAGNOSTIC')"
        in SUBORDINATE_MIGRATION
    )
    assert (
        "ux_strategy_execution_bindings_subordinate_diagnostic_account"
        in SUBORDINATE_MIGRATION
    )
    assert "allocation_mode='SUBORDINATE_DIAGNOSTIC'" in SUBORDINATE_MIGRATION
    assert "'SUBORDINATE_DIAGNOSTIC','ACTIVE'" in MIGRATION
    assert "OWNER_FULL_CONTOUR_PARENT_P90_BINDING_NOT_ACTIVE" in MIGRATION
    assert "b.allocation_mode='SUBORDINATE_DIAGNOSTIC'" in MIGRATION
    assert "OWNER_FULL_CONTOUR_PARENT_P90_RIGHTS_MUST_BE_OFF" in MIGRATION


def test_subordinate_mode_is_rechecked_in_real_admission_and_dispatch() -> None:
    assert "real_binding_allocation_allowed(" in ENTRY_ADMISSION
    assert "real_binding_allocation_allowed(" in ACCOUNT_ARM
    assert "real_binding_allocation_allowed(" in ENTRY_CONSUMER
    assert "require_parent_p90_diagnostic_scope(" in OWNER_RUNTIME
    assert "require_parent_p90_diagnostic_scope(" in ENTRY_CONSUMER
    assert "PostgresExitShadowStore" in OWNER_RUNTIME
    assert "OWNER_DIAGNOSTIC_FORCE_CLOSE" in OWNER_RUNTIME


def test_verified_release_registers_and_readbacks_diagnostic_capability() -> None:
    migration = "operations/sql/20261101_owner_full_contour_diagnostic.sql"
    subordinate = "operations/sql/20261101_subordinate_diagnostic_binding.sql"
    migration_v2 = "operations/sql/20261101_owner_full_contour_diagnostic_v2.sql"
    installer = (
        "operations/implementation/install_owner_full_contour_diagnostic.py"
    )
    assert migration in RELEASE_INSTALLER
    assert subordinate in RELEASE_INSTALLER
    assert migration_v2 in RELEASE_INSTALLER
    migration_pos = RELEASE_INSTALLER.index(migration)
    subordinate_pos = RELEASE_INSTALLER.index(subordinate)
    migration_v2_pos = RELEASE_INSTALLER.index(migration_v2)
    install_call = RELEASE_INSTALLER.index(
        f"$runtime_release/{installer}\" install"
    )
    check_call = RELEASE_INSTALLER.index(
        f"$runtime_release/{installer}\" check"
    )
    assert migration_pos < subordinate_pos < migration_v2_pos < install_call < check_call
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
    def __init__(self, disarm_session_id: str | None = None):
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.disarm_session_id = (
            disarm_session_id
            if disarm_session_id is not None
            else control.account_session_id("owner-fc-test")
        )

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
            return _Cursor(
                {"account_live_arm_session_id": self.disarm_session_id}
            )
        if "SELECT enabled FROM control.execution_gates" in statement:
            return _Cursor({"enabled": True})
        return _Cursor(None)


def test_retire_entry_rights_rejects_mismatched_dict_row_disarm_result() -> None:
    connection = _RetireDictRowConnection(disarm_session_id="wrong-session")
    with pytest.raises(
        RuntimeError,
        match="OWNER_FULL_CONTOUR_ACCOUNT_DISARM_FAILED",
    ):
        control.retire_entry_rights(
            connection,
            run_id="owner-fc-test",
            at=datetime(2026, 11, 1, tzinfo=UTC),
            reason="test dispatch",
        )


def test_entry_consumer_atomically_retires_diagnostic_rights_after_dispatch() -> None:
    assert "retire_owner_diagnostic_entry_rights" in ENTRY_CONSUMER
    publish = ENTRY_CONSUMER.index("_publish_command(connection, prepared, now=current)")
    finalize = ENTRY_CONSUMER.index(
        "_finalize_owner_diagnostic_dispatch(",
        publish,
    )
    transaction = ENTRY_CONSUMER.rfind(
        "with connection.transaction():",
        0,
        publish,
    )
    assert transaction < publish < finalize
    helper = ENTRY_CONSUMER.index("def _finalize_owner_diagnostic_dispatch(")
    helper_end = ENTRY_CONSUMER.index("def _generation_pre_dispatch_status(", helper)
    helper_body = ENTRY_CONSUMER[helper:helper_end]
    assert 'state="DISPATCHED"' in helper_body
    assert "entry_command_id" in helper_body
    assert "retire_owner_diagnostic_entry_rights(" in helper_body


class _DispatchedDiagnosticGateConnection:
    def __init__(
        self,
        *,
        gate_reason: str = control.DISPATCH_GATE_CLOSE_REASON,
        permission_active: bool = False,
    ):
        self.gate_reason = gate_reason
        self.permission_active = permission_active

    def execute(self, statement, parameters=()):
        if "SELECT run_id" in statement and "entry_command_id=%s" in statement:
            return _Cursor({"run_id": "owner-fc-test"})
        if "AS run_state" in statement and "exact_gate_close_event" in statement:
            return _Cursor(
                {
                    "run_state": "DISPATCHED",
                    "strategy_id": diagnostic.SUB_ID,
                    "strategy_version": diagnostic.VERSION,
                    "run_trading_account_id": diagnostic.SUB_ACCOUNT_ID,
                    "account_connector_id": diagnostic.SUB_CONNECTOR_ID,
                    "release_commit": "a" * 40,
                    "entry_execution_request_id": "request-test",
                    "entry_command_id": "command-test",
                    "command_type": "entry",
                    "command_state": "queued",
                    "command_trading_account_id": diagnostic.SUB_ACCOUNT_ID,
                    "command_identity_status": "PROVEN",
                    "dispatch_state": "DISPATCHED",
                    "gate_enabled": False,
                    "gate_reason": self.gate_reason,
                    "permission_active": self.permission_active,
                    "generic_arm_active": False,
                    "account_arm_active": False,
                    "exact_gate_close_event": True,
                }
            )
        return _Cursor(None)


def test_dispatched_diagnostic_continues_only_after_exact_dispatch_gate_close() -> None:
    assert control.dispatched_entry_command_continuation_allowed(
        _DispatchedDiagnosticGateConnection(),
        command_id="command-test",
        trading_account_id=diagnostic.SUB_ACCOUNT_ID,
        account_connector_id=diagnostic.SUB_CONNECTOR_ID,
        release_commit="a" * 40,
    )
    assert not control.dispatched_entry_command_continuation_allowed(
        _DispatchedDiagnosticGateConnection(gate_reason="safety block"),
        command_id="command-test",
        trading_account_id=diagnostic.SUB_ACCOUNT_ID,
        account_connector_id=diagnostic.SUB_CONNECTOR_ID,
        release_commit="a" * 40,
    )
    assert not control.dispatched_entry_command_continuation_allowed(
        _DispatchedDiagnosticGateConnection(permission_active=True),
        command_id="command-test",
        trading_account_id=diagnostic.SUB_ACCOUNT_ID,
        account_connector_id=diagnostic.SUB_CONNECTOR_ID,
        release_commit="a" * 40,
    )


class _RequestedGateClosedConnection:
    def __init__(self):
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement, parameters=()):
        params = tuple(parameters)
        self.calls.append((statement, params))
        if (
            "FROM control.owner_full_contour_diagnostic_runs" in statement
            and "state=ANY" in statement
        ):
            return _Cursor(
                {
                    "run_id": "owner-fc-test",
                    "state": "REQUESTED",
                    "entry_command_id": None,
                    "entry_execution_request_id": None,
                    "strategy_position_id": None,
                }
            )
        if "SELECT enabled FROM control.execution_gates" in statement:
            return _Cursor({"enabled": 0})
        if "SELECT strategy_id,strategy_version,strategy_config_fingerprint" in statement:
            return _Cursor(
                {
                    "strategy_id": diagnostic.SUB_ID,
                    "strategy_version": diagnostic.VERSION,
                    "strategy_config_fingerprint": "f" * 64,
                }
            )
        if "SELECT count(*) AS active_count" in statement:
            return _Cursor({"active_count": 1})
        if "SELECT control.disarm_owner_full_contour_account_scope" in statement:
            return _Cursor((control.account_session_id("owner-fc-test"),))
        return _Cursor(None)


def test_requested_diagnostic_fails_and_disarms_when_entry_gate_was_closed() -> None:
    connection = _RequestedGateClosedConnection()
    result = owner_runtime.refresh_run_lifecycle(
        connection,
        trading_account_id=diagnostic.SUB_ACCOUNT_ID,
        now=datetime(2026, 11, 1, tzinfo=UTC),
    )
    assert result == (
        "OWNER_DIAGNOSTIC_FAILED:"
        "OWNER_FULL_CONTOUR_ENTRY_GATE_CLOSED_BEFORE_MATERIALIZATION"
    )
    sql = "\n".join(statement for statement, _params in connection.calls)
    assert "UPDATE strategy_entry.execution_permissions" in sql
    assert "UPDATE control.live_arm_sessions" in sql
    assert "SELECT control.disarm_owner_full_contour_account_scope" in sql
    assert "SET state=%s" in sql
    assert any(
        params and params[0] == "FAILED"
        for statement, params in connection.calls
        if "UPDATE control.owner_full_contour_diagnostic_runs" in statement
    )


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
    assert "UPDATE control.execution_gates" in sql
    assert "INSERT INTO control.execution_gate_events" in sql
