from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "operations/sql/20261029_p90l90_real_account_arm_control.sql"
DASH = ROOT / "operations/dashboard/app.py"
OBSERVER = ROOT / "operations/monitoring/universal_entry_shadow.py"
CONTROL = ROOT / "src/bybit_workbench/p90l90_real_arm_control.py"
SUB_PRIVATE = ROOT / "operations/systemd/cripta-private-runtime@.service"


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
    assert 'P90_REAL_V3_ARM_PATH = "/api/p90l90/real-v3/arm"' in source
    assert "P90_REAL_V3_USE_DEDICATED_PREARM_CONTROL" in source
    assert "P90_REAL_V3_USE_DEDICATED_OWNER_ARM_CONTROL" in source
    assert "_u6_p90_real_v3_prearm" in source
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


def test_p90_sub_private_runtime_publishes_fresh_position_mode_evidence() -> None:
    source = SUB_PRIVATE.read_text()
    assert "Environment=CRIPTA_POSITION_MODE_REFRESH_SECONDS=30" in source
    assert "Environment=CRIPTA_POSITION_MODE_FRESHNESS_SECONDS=90" in source
