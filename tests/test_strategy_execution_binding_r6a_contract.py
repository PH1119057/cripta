from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/sql/20261016_strategy_execution_binding_r6a.sql"
OBSERVER = ROOT / "operations/monitoring/universal_entry_shadow.py"
UNIT = ROOT / "operations/systemd/cripta-universal-entry-observer.service"
SCHEMA = ROOT / "operations/connectivity/runtime_schema.py"


def test_r6a_binding_registry_is_empty_additive_control_plane() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS strategy_entry.strategy_execution_bindings" in sql
    assert "strategy_activation_id text NOT NULL" in sql
    assert "trading_account_id text NOT NULL" in sql
    assert "allocation_mode text NOT NULL DEFAULT 'DEDICATED_STRATEGY'" in sql
    assert "binding_state IN ('ACTIVE','INACTIVE','RETIRED')" in sql
    assert "ux_strategy_execution_bindings_active_leg" in sql
    assert "ux_strategy_execution_bindings_dedicated_account" in sql
    assert "GRANT SELECT ON strategy_entry.strategy_execution_bindings TO cripta" in sql
    upper = sql.upper()
    assert "INSERT INTO STRATEGY_ENTRY.STRATEGY_EXECUTION_BINDINGS" not in upper
    assert "UPDATE STRATEGY_ENTRY.STRATEGY_EXECUTION_BINDINGS" not in upper
    assert "DELETE FROM STRATEGY_ENTRY.STRATEGY_EXECUTION_BINDINGS" not in upper


def test_r6a_real_entry_requires_release_arm_and_exact_account_binding() -> None:
    source = OBSERVER.read_text(encoding="utf-8")
    assert "def _real_execution_binding_activation_ids(" in source
    assert "b.execution_leg='PRIMARY'" in source
    assert "b.execution_role='PRIMARY'" in source
    assert "b.allocation_mode='DEDICATED_STRATEGY'" in source
    assert "b.trading_account_id=%s" in source
    assert "release_ready_for = _real_execution_activation_ids(connection, bundles)" in source
    assert "execution_bindings = _real_execution_bindings(" in source
    assert "binding_ready_for = frozenset(execution_bindings)" in source
    assert "real_admission_ready_for = release_ready_for.intersection(binding_ready_for)" in source
    assert "FROM strategy_entry.strategy_execution_bindings" in source
    assert '("BINDING",' in source


def test_r6a_observer_is_pinned_to_current_private_account_identity() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    assert "CRIPTA_TRADING_ACCOUNT_ID=ta-2122c81ecff14f3b85b102db605212f7" in unit
    source = OBSERVER.read_text(encoding="utf-8")
    assert 'TRADING_ACCOUNT_ID = os.environ.get("CRIPTA_TRADING_ACCOUNT_ID", "").strip()' in source


def test_r6a_does_not_retrofit_open_positions_or_enable_live() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in (
        "runtime.position_ownership",
        "runtime.capital_reservations",
        "runtime.exchange_position_slot_claims",
        "runtime.trade_commands",
    ):
        assert table not in sql
    assert "control.execution_gates" not in sql
    assert "control.live_arm_sessions" not in sql
    schema = SCHEMA.read_text(encoding="utf-8")
    assert "EXPECTED_RUNTIME_SCHEMA_VERSION" in schema
