from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "operations/sql/20261029_p90l90_real_account_arm_control.sql"
DASH = ROOT / "operations/dashboard/app.py"
OBSERVER = ROOT / "operations/monitoring/universal_entry_shadow.py"
PRIVATE_RUNTIME = ROOT / "operations/connectivity/private_runtime.py"
CONTROL = ROOT / "src/bybit_workbench/p90l90_real_arm_control.py"
SUB_PRIVATE = ROOT / "operations/systemd/cripta-private-runtime@.service"
MAIN_PRIVATE_DROPIN = (
    ROOT / "operations/systemd/cripta-private-runtime.service.d/10-pythonpath.conf"
)
PREARM_SCRIPT = ROOT / "operations/implementation/control_p90l90_real_v3_prearm.py"
PREARM_EVIDENCE = ROOT / "src/bybit_workbench/p90l90_real_prearm_evidence.py"
INSTALLER = ROOT / "operations/infrastructure/install_verified_release.sh"
P90_PORTAL_WAIVER_SQL = (
    ROOT / "operations/sql/20261030_p90_portal_micro_live_waiver.sql"
)


def test_p90_control_db_rail_is_narrow_and_never_opens_mainnet() -> None:
    sql = SQL.read_text()
    assert "CREATE ROLE cripta_p90l90_real_v3_control NOLOGIN" in sql
    assert "pg_has_role('cripta','cripta_p90l90_real_v3_control','MEMBER')" in sql
    assert "strategy_entry.prearm_p90l90_real_v3" in sql
    assert "strategy_entry.unprearm_p90l90_real_v3" in sql
    assert "control.arm_p90l90_real_v3_account_scope" in sql
    assert "control.disarm_p90l90_real_v3_account_scopes" in sql
    assert "OWNER TO cripta_p90l90_real_v3_control" in sql
    assert "GRANT EXECUTE ON FUNCTION" in sql
    assert "has_table_privilege(" in sql
    assert "'cripta','control.account_live_arm_sessions','INSERT'" in sql
    assert "UPDATE control.execution_gates" not in sql
    assert "INSERT INTO control.execution_gate_events" not in sql


def test_p90_owner_control_orders_account_arm_before_permissions_before_gate() -> None:
    source = CONTROL.read_text()
    account = source.index("control.arm_p90l90_real_v3_account_scope")
    readback = source.index("account_live_arm_ready", account)
    permissions = source.index("store.set_execution_permission", readback)
    gate = source.index("UPDATE control.execution_gates", permissions)
    assert account < readback < permissions < gate
    assert '"positions_closed": False' in source
    assert "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE" in source


def test_p90_dashboard_blocks_generic_partial_controls() -> None:
    source = DASH.read_text()
    assert 'P90_REAL_V3_PREARM_PATH = "/api/p90l90/real-v3/prearm"' in source
    assert 'P90_REAL_V3_PREARM_EVIDENCE_PATH = "/api/p90l90/real-v3/prearm-evidence"' in source
    assert 'P90_REAL_V3_ARM_PATH = "/api/p90l90/real-v3/arm"' in source
    assert "P90_REAL_V3_USE_DEDICATED_PREARM_CONTROL" in source
    assert "P90_REAL_V3_USE_DEDICATED_OWNER_ARM_CONTROL" in source
    assert "_u6_p90_real_v3_prearm" in source
    assert "_u6_p90_real_v3_prearm_evidence" in source
    assert "_u6_p90_real_v3_arm" in source


def test_p90_observer_has_three_state_prearm_real_failclosed_switch() -> None:
    source = OBSERVER.read_text()
    prearm = source.index("elif not gate_enabled and not selected:")
    real = source.index("elif gate_enabled and selected == expected:", prearm)
    partial = source.index(
        'raise RuntimeError("P90L90_REAL_NATIVE_EXECUTION_OWNER_NOT_CONNECTED")',
        real,
    )
    assert prearm < real < partial
    assert "p90_real_source_only = True" in source[prearm:real]
    assert "p90_real_live = True" in source[real:partial]


def test_p90_private_runtimes_publish_fresh_mode_and_signed_ipc() -> None:
    main_source = MAIN_PRIVATE_DROPIN.read_text()
    sub_source = SUB_PRIVATE.read_text()
    assert "Environment=CRIPTA_P90_REAL_PRIVATE_IPC=ENABLED" in main_source
    assert "Environment=CRIPTA_P90_REAL_PRIVATE_IPC=ENABLED" in sub_source
    assert "Environment=CRIPTA_POSITION_MODE_REFRESH_SECONDS=30" in main_source
    assert "Environment=CRIPTA_POSITION_MODE_FRESHNESS_SECONDS=90" in main_source
    assert "Environment=CRIPTA_POSITION_MODE_REFRESH_SECONDS=30" in sub_source
    assert "Environment=CRIPTA_POSITION_MODE_FRESHNESS_SECONDS=90" in sub_source


def test_p90_private_flat_failure_publishes_only_stable_local_reason() -> None:
    source = PRIVATE_RUNTIME.read_text()
    start = source.index("def _p90_private_ipc_flat_handler(")
    end = source.index("def _p90_private_ipc_recovery_handler(", start)
    block = source[start:end]
    assert "except P90RealFlatAccountUnverified as exc:" in block
    assert '"p90_private_ipc"' in block
    assert '"state": "BLOCKED"' in block
    assert '"flat_reason": str(exc)' in block
    assert '"flat_reason": "UNEXPECTED_" + type(exc).__name__' in block
    assert '"state": "VERIFIED"' in block
    assert "return result" in block


def test_p90_technical_evidence_and_owner_arm_are_separate_actions() -> None:
    source = DASH.read_text()
    evidence_handler = source.index("def _u6_p90_real_v3_prearm_evidence(")
    arm_handler = source.index("def _u6_p90_real_v3_arm(", evidence_handler)
    catalog_handler = source.index("def _u6_financial_account_catalog", arm_handler)
    evidence_block = source[evidence_handler:arm_handler]
    arm_block = source[arm_handler:catalog_handler]
    assert "prepare_p90l90_real_v3_prearm_evidence(" in evidence_block
    assert "arm_p90l90_real_v3(" not in evidence_block
    assert "prepare_p90l90_real_v3_prearm_evidence(" not in arm_block
    assert "arm_p90l90_real_v3(" in arm_block
    assert "OWNER ARM requires explicit confirmation" in arm_block


def test_p90_prearm_operator_script_cannot_owner_arm_or_open_gate() -> None:
    source = PREARM_SCRIPT.read_text()
    assert "prearm_p90l90_real_v3" in source
    assert "prepare_p90l90_real_v3_prearm_evidence" in source
    forbidden_import = (
        "from bybit_workbench.p90l90_real_arm_control import (\\n"
        "    arm_p90l90_real_v3,"
    )
    assert forbidden_import not in source
    assert "\n        arm_p90l90_real_v3(" not in source
    assert "set_execution_permission" not in source
    assert "UPDATE control.execution_gates" not in source
    assert "EXCHANGE_MUTATION: none" in source
    assert "LIVE_ARM / execution permission / mainnet gate mutation: forbidden here." in source

def test_p90_technical_evidence_producer_has_no_owner_arm_capability() -> None:
    source = PREARM_EVIDENCE.read_text()
    assert "INSERT INTO control.live_arm_evidence" in source
    assert "require_owner_approval=False" in source
    assert 'put("MAINNET_GATE_EXPLICIT_OWNER_APPROVAL"' not in source
    assert "arm_p90l90_real_v3(" not in source
    assert "set_execution_permission" not in source
    assert "UPDATE control.execution_gates" not in source


def test_p90_prearm_atomically_retires_exact_historical_r1_binding() -> None:
    source = SQL.read_text()
    prearm = source.index(
        "CREATE OR REPLACE FUNCTION strategy_entry.prearm_p90l90_real_v3"
    )
    unprearm = source.index(
        "CREATE OR REPLACE FUNCTION strategy_entry.unprearm_p90l90_real_v3",
        prearm,
    )
    block = source[prearm:unprearm]
    assert "activation-be482d99252b4055b020a84fef8e2054" in block
    assert "binding-r1-unified-2c51489dae0a38fc" in block
    retire = block.index("R1 RETIRED; replaced by P90 REAL v3 SOURCE_ONLY PREARM")
    activate = block.index("SET binding_state='ACTIVE'", retire)
    assert retire < activate
    assert "effective_to=p_at" in block
    assert "P90_REAL_PREARM_UNEXPECTED_ACTIVE_ACCOUNT_OWNER" in block
    assert "P90_REAL_PREARM_RETIRED_R1_BINDING_NOT_EXACT" in block

    unprearm_block = source[unprearm:]
    assert "binding-r1-unified-2c51489dae0a38fc" not in unprearm_block


def test_p90_prearm_live_freshness_uses_post_read_clock() -> None:
    source = PREARM_EVIDENCE.read_text()
    assert "observer_checked_at = datetime.now(UTC)" in source
    assert "proof_checked_at = datetime.now(UTC)" in source
    assert "generation_checked_at = datetime.now(UTC)" in source
    assert (
        "reconciliation_checked_ms = int(datetime.now(UTC).timestamp() * 1000)"
        in source
    )
    assert "position_mode_checked_at = datetime.now(UTC)" in source
    assert "(current - observer_at.astimezone(UTC)).total_seconds()" not in source
    assert "(current - proof.observed_at).total_seconds()" not in source
    assert "(current - completed).total_seconds()" not in source
    assert "now_ms - int(reconciliation[1])" not in source
    assert 'position mode") >= current' not in source


def test_p90_portal_micro_live_waiver_is_scoped_and_release_migrated() -> None:
    evidence = PREARM_EVIDENCE.read_text()
    dashboard = DASH.read_text()
    migration = P90_PORTAL_WAIVER_SQL.read_text()
    installer = INSTALLER.read_text()

    assert '"cripta-dashboard.service"' in evidence
    assert "critical_pending == 0" in evidence
    assert "OWNER_WAIVED_FOR_P90_PORTAL_MICRO_LIVE" in evidence
    assert '"p90_portal_micro_live_waiver"' in evidence
    assert '"owner_webhook_deferred"' in evidence
    assert "FROM runtime.lifecycle_faults" in dashboard
    assert "state='OPEN' AND severity='CRITICAL'" in dashboard

    assert "OWNER_WAIVED_FOR_R1_MICRO_LIVE" in migration
    assert "OWNER_WAIVED_FOR_P90_PORTAL_MICRO_LIVE" in migration
    assert "UPDATE control.execution_gates" not in migration
    assert "execution_permissions" not in migration
    assert "20261030_p90_portal_micro_live_waiver.sql" in installer


def test_p90_prearm_uses_latest_completed_proven_account_generation() -> None:
    source = PREARM_EVIDENCE.read_text()
    assert "AND state='COMPLETE'" in source
    assert "AND identity_status='PROVEN'" in source
    assert "AND completed_at IS NOT NULL" in source
    assert "ORDER BY completed_at DESC LIMIT 1" in source
    assert "ORDER BY started_at DESC LIMIT 1" not in source


def test_p90_prearm_evidence_deduplicates_shared_strategy_scope() -> None:
    source = PREARM_EVIDENCE.read_text()
    assert "written_scopes: dict[tuple[str, str, str], str] = {}" in source
    assert "scope = (code, scope_type, scope_key)" in source
    assert "previous = written_scopes.get(scope)" in source
    assert '_require(previous == payload, "conflicting evidence for shared scope")' in source
    assert "written_scopes[scope] = payload" in source
    assert source.index("if previous is not None:") < source.index("evidence_id =")
    # The five P90 symbols share two distinct strategy scopes.
    main = ("LTCUSDT", "INJUSDT")
    sub = ("DOTUSDT", "ARBUSDT", "APTUSDT")
    scopes = [("MAIN", symbol) for symbol in main] + [
        ("SUB", symbol) for symbol in sub
    ]
    assert len(scopes) == 5
    assert len({strategy for strategy, _ in scopes}) == 2
    assert len(set(scopes)) == 5
    # Same Strategy check across symbols must map to a single scope,
    # while symbol-specific checks remain independent for all five symbols.
    strategy_scope_keys = [(strategy, "ENTRY_PLAN_EXECUTABLE") for strategy, _ in scopes]
    symbol_scope_keys = [(strategy, symbol, "POSITION_MODE_FRESH") for strategy, symbol in scopes]
    assert len(set(strategy_scope_keys)) == 2
    assert len(set(symbol_scope_keys)) == 5
