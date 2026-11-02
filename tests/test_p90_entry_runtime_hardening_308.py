from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = (ROOT / "operations/connectivity/universal_entry_consumer.py").read_text()
PREARM = (ROOT / "src/bybit_workbench/p90l90_real_prearm_evidence.py").read_text()
SERVICE_PROFILE = (ROOT / "src/bybit_workbench/real_service_profiles.py").read_text()
SAFETY = (ROOT / "src/bybit_workbench/release_arm_safety.py").read_text()
LIFECYCLE = (ROOT / "operations/monitoring/lifecycle_supervisor_runtime.py").read_text()
DASH = (ROOT / "operations/dashboard/index.html").read_text()
DASH_APP = (ROOT / "operations/dashboard/app.py").read_text()
SUB_UNIT = (ROOT / "operations/systemd/cripta-universal-entry-consumer@.service").read_text()
INSTALLER = (ROOT / "operations/infrastructure/install_verified_release.sh").read_text()
HEALTH_SQL = (ROOT / "operations/sql/20261031_entry_consumer_health.sql").read_text()
OBSERVER = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text()


def test_consumer_health_schema_is_durable_and_least_privilege() -> None:
    assert "CREATE TABLE IF NOT EXISTS runtime.entry_consumer_health" in HEALTH_SQL
    assert "PRIMARY KEY" in HEALTH_SQL
    assert "REFERENCES identity.trading_accounts" in HEALTH_SQL
    assert "GRANT SELECT, INSERT, UPDATE" in HEALTH_SQL
    assert "REVOKE DELETE, TRUNCATE" in HEALTH_SQL
    assert "20261031_entry_consumer_health.sql" in INSTALLER


def test_prearm_requires_both_entry_consumers_and_fresh_health() -> None:
    assert '"cripta-universal-entry-consumer.service",' in SERVICE_PROFILE
    assert (
        'f"cripta-universal-entry-consumer@{sub_account_id}.service"'
        in SERVICE_PROFILE
    )
    assert "p90_real_v3_service_specs(sub_account_id=_SUB_ACCOUNT)" in PREARM
    assert "FROM runtime.entry_consumer_health" in PREARM
    assert 'str(health_state) == "READY"' in PREARM
    assert "health_checked_at = datetime.now(UTC)" in PREARM
    assert "(health_checked_at - health_observed).total_seconds() <= 8" in PREARM
    assert "(current - health_observed).total_seconds() <= 8" not in PREARM


def test_runtime_guard_runs_without_signal_and_recovers_expired_admission() -> None:
    assert "release_expired_pre_dispatch(" in LIFECYCLE
    assert "require_entry_runtime_health=True" in LIFECYCLE
    assert "entry_source_ready=source_ready" in LIFECYCLE
    assert "if retired_arms:" not in LIFECYCLE[
        LIFECYCLE.index("loaded_commit = ("):
        LIFECYCLE.index("with connection.transaction():\n                delivery")
    ]
    assert "FROM runtime.entry_consumer_health" in SAFETY
    assert "REAL_ENTRY_RUNTIME_NOT_READY" in SAFETY
    assert "UPDATE control.execution_gates" in SAFETY


def test_sub_consumer_can_be_explicitly_persistent_but_installer_does_not_auto_start() -> None:
    assert "[Install]\nWantedBy=multi-user.target" in SUB_UNIT
    assert "Environment=CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND=SUB" in SUB_UNIT
    assert "cripta-universal-entry-consumer@.service" in INSTALLER
    managed = INSTALLER.split("managed_services=(", 1)[1].split("\n)", 1)[0]
    assert "cripta-universal-entry-consumer@.service" not in managed
    assert "discovered_sub_units" in INSTALLER


def test_dashboard_disarm_is_available_even_when_readiness_is_blocked() -> None:
    assert "function p90RealV3OwnerArmPresent()" in DASH
    assert "Number(readiness.live_arm_sessions||0)>0" in DASH
    assert "Number(readiness.account_live_arm_sessions||0)>0" in DASH
    assert (
        "button.disabled=gateChanging||preparing||"
        "(!armed&&(!cohort||inconsistent||knownBlocked));"
        in DASH
    )
    assert "!armed&&" in DASH
    assert "const armed=p90RealV3OwnerArmPresent();" in DASH
    assert '"execution_permissions": int(p90_arm_facts[0])' in DASH_APP
    assert '"live_arm_sessions": int(p90_arm_facts[1])' in DASH_APP
    assert '"account_live_arm_sessions": int(p90_arm_facts[2])' in DASH_APP


def test_consumer_health_does_not_grant_request_update_privilege() -> None:
    assert '("strategy_entry.execution_requests", "UPDATE")' not in CONSUMER
    assert "FOR UPDATE OF r SKIP LOCKED" not in CONSUMER
    assert "FOR UPDATE OF c SKIP LOCKED" in CONSUMER


def test_observer_latest_coin_context_lookup_is_index_bounded() -> None:
    start = OBSERVER.index("def _observer_objective_inputs(")
    end = OBSERVER.index("\ndef _maybe_record_reverse_transition(", start)
    body = OBSERVER[start:end]
    assert "CROSS JOIN LATERAL" in body
    assert "FROM unnest(%s::text[]) AS instrument(linear_instrument_id)" in body
    assert "linear_instrument_id=instrument.linear_instrument_id" in body
    assert "ORDER BY observed_at DESC" in body
    assert "LIMIT 1" in body
    assert "SELECT DISTINCT ON (linear_instrument_id)" not in body


def test_primary_p90_owner_control_is_visible_and_single() -> None:
    assert (
        '<button id="tradeGateButton" class="action danger" type="button" hidden>'
        in DASH
    )
    assert (
        "#tradeGateButton,#tradeOperationStatus,#tradeGateNotice,"
        "#tradeGateReasons,#gatewayReleaseStatus{display:none!important}"
        not in DASH
    )
    assert (
        "const primaryP90Control=cohort||armed||preparing||"
        "operationState==='BLOCKED';"
        in DASH
    )
    assert "tradeGateButton.hidden=!primaryP90Control;" in DASH
    assert "button.hidden=true;" in DASH


def test_owner_diagnostic_plan_ack_is_account_scoped_and_truthful() -> None:
    start = CONSUMER.index("def _acknowledge_owner_diagnostic_entry_plans(")
    end = CONSUMER.index("\ndef _write_consumer_health(", start)
    block = CONSUMER[start:end]
    assert "b.trading_account_id=%s" in block
    assert "b.binding_state='ACTIVE'" in block
    assert "b.allocation_mode='SUBORDINATE_DIAGNOSTIC'" in block
    assert "is_diagnostic_strategy(strategy_id, strategy_version)" in block
    assert "record_plan_consumption(" in block
    assert 'plan_kind="ENTRY"' in block
    assert 'status="LOADED"' in block
    assert '("runtime.plan_consumptions", "INSERT")' in CONSUMER
    assert '("runtime.plan_consumptions", "UPDATE")' in CONSUMER


def test_pre_mutation_reconciliation_requires_complete_no_exchange_evidence() -> None:
    start = CONSUMER.index("def _reconcile_pre_mutation_pending_command_blocks(")
    end = CONSUMER.index("\ndef run_once(", start)
    block = CONSUMER[start:end]
    for required in (
        "started_at_epoch_ms",
        "ENTRY_BLOCKED:NEW_ENTRY_GATE_DISARMED",
        "runtime.executions",
        "runtime.exchange_order_history",
        "runtime.hot_orders",
        "runtime.position_ownership",
        "runtime.hot_positions",
        "PRE_MUTATION_PENDING_COMMAND_RECONCILED_NO_EXCHANGE_MUTATION",
        "REQUEST_TERMINAL",
    ):
        assert required in block
    assert "api_post(" not in block
    assert "state='RELEASED'" in block
    assert "claim_state='RELEASED'" in block
