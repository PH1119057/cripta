from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "operations/sql/20260918_trade_lifecycle_v1.sql").read_text(encoding="utf-8")


def test_trade_lifecycle_schema_declares_required_support_entities() -> None:
    required = (
        "runtime.plan_consumptions",
        "runtime.capital_reservations",
        "strategy_exit.exit_observations",
        "strategy_exit.shadow_evaluations",
        "strategy_exit.exit_decisions",
        "strategy_exit.execution_requests",
        "strategy_exit.execution_dispatches",
        "runtime.trade_lifecycle_events",
        "runtime.lifecycle_faults",
    )
    for name in required:
        assert name in SQL


def test_universal_position_lineage_is_fail_closed() -> None:
    assert "position_ownership_universal_lineage_check" in SQL
    for field in (
        "account_ref IS NOT NULL",
        "strategy_config_fingerprint IS NOT NULL",
        "strategy_activation_id IS NOT NULL",
        "strategy_attempt_id IS NOT NULL",
        "entry_decision_id IS NOT NULL",
        "entry_execution_request_id IS NOT NULL",
        "entry_plan_fingerprint IS NOT NULL",
        "exit_plan_fingerprint IS NOT NULL",
        "exchange_position_key IS NOT NULL",
    ):
        assert field in SQL


def test_open_exchange_slot_has_single_logical_owner() -> None:
    assert "ux_position_ownership_active_exchange_slot" in SQL
    assert "state IN ('OPEN','RECONCILIATION_REQUIRED')" in SQL


def test_capital_reservation_unknown_states_remain_reserved() -> None:
    assert "'PENDING_EXCHANGE_REFLECTION'" in SQL
    assert "'RECONCILIATION_REQUIRED'" in SQL
    assert "ix_capital_reservations_account_active" in SQL


def test_capital_reservation_has_strategy_owned_pre_dispatch_lease() -> None:
    assert "pre_dispatch_expires_at timestamptz NOT NULL" in SQL
    assert "state_reason text" in SQL
    assert "ix_capital_reservations_reserved_expiry" in SQL


def test_p5_exit_shadow_storage_preserves_causal_observation_and_rule_identity() -> None:
    for token in (
        "event_at timestamptz NOT NULL",
        "observed_at timestamptz NOT NULL",
        "received_at timestamptz NOT NULL",
        "observation_id text NOT NULL",
        "rule_priority integer NOT NULL",
        "repeat_policy text NOT NULL",
        "NO_EXECUTABLE_EXIT_RULES",
        "DECISION_CREATED",
    ):
        assert token in SQL


def test_p5_shadow_evidence_is_immutable_and_separate_from_execution_requests() -> None:
    assert "exit_observations_immutable" in SQL
    assert "exit_shadow_evaluations_immutable" in SQL
    assert "CREATE TABLE IF NOT EXISTS strategy_exit.shadow_evaluations" in SQL
    assert "CREATE TABLE IF NOT EXISTS strategy_exit.execution_requests" in SQL


def test_exit_storage_is_separate_from_entry_execution_storage() -> None:
    assert "CREATE SCHEMA IF NOT EXISTS strategy_exit" in SQL
    assert "CREATE TABLE IF NOT EXISTS strategy_exit.execution_requests" in SQL
    assert "ALTER TABLE strategy_entry.execution_requests" in SQL


def test_lifecycle_supervisor_tokens_are_storage_contract() -> None:
    for token in (
        "POSITION_WITHOUT_EXIT_OWNER",
        "EXCHANGE_POSITION_OWNERSHIP_CONFLICT",
        "CAPITAL_RESERVATION_STUCK",
        "EXIT_EXECUTION_AMBIGUOUS",
    ):
        assert f"'{token}'" in SQL


def test_runtime_role_cannot_delete_lifecycle_evidence() -> None:
    assert "REVOKE DELETE ON runtime.plan_consumptions" in SQL
    immutable_revoke = SQL[
        SQL.index("REVOKE UPDATE,DELETE ON strategy_exit.exit_observations")
        : SQL.index("REVOKE DELETE ON runtime.plan_consumptions")
    ]
    for table in (
        "strategy_exit.exit_observations",
        "strategy_exit.shadow_evaluations",
        "strategy_exit.exit_decisions",
        "strategy_exit.execution_requests",
        "strategy_exit.execution_dispatches",
    ):
        assert table in immutable_revoke
