from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/sql/20261016_command_position_lineage_r6b2.sql"
BRIDGE = ROOT / "src/bybit_workbench/universal_entry/execution_bridge.py"
CONSUMER = ROOT / "operations/connectivity/universal_entry_consumer.py"
POSITION = ROOT / "src/bybit_workbench/strategy_position_binding.py"
UNIT = ROOT / "operations/systemd/cripta-universal-entry-consumer.service"


def test_r6b2_schema_is_additive_and_legacy_safe() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TABLE runtime.trade_commands" in sql
    assert "ALTER TABLE runtime.position_ownership" in sql
    assert sql.count("DEFAULT 'LEGACY_UNKNOWN'") == 2
    upper = sql.upper()
    assert "DROP " not in upper
    assert "DELETE FROM" not in upper
    assert "UPDATE RUNTIME." not in upper


def test_r6b2_bridge_requires_and_freezes_binding_lineage() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    assert "real EntryExecutionRequest requires Strategy execution binding lineage" in source
    for field in ("strategy_execution_binding_id","execution_leg","trading_account_id"):
        assert field in source
    assert '"strategy_execution_binding_id": request.strategy_execution_binding_id' in source


def test_r6b2_consumer_rechecks_binding_before_command_and_blocks_revocation() -> None:
    source = CONSUMER.read_text(encoding="utf-8")
    status = source[source.index("def _admission_pre_dispatch_status"):source.index("def _release_pre_exchange_admission")]
    publish = source[source.index("def _publish_command"):source.index("def _admission_pre_dispatch_status")]
    assert "STRATEGY_EXECUTION_BINDING_NOT_ACTIVE" in status
    assert "CAPITAL_BINDING_LINEAGE_MISMATCH" in status
    assert "ENTRY_DECISION_BINDING_LINEAGE_MISMATCH" in status
    assert 'str(row.get("identity_status") or "") != "PROVEN"' in status
    assert "POSITION_MODE_TRADING_ACCOUNT_MISMATCH" in status
    assert "BINDING_INVALID" in source
    assert "PRE_EXCHANGE_BLOCK:STRATEGY_EXECUTION_BINDING_NOT_READY" in source
    assert "identity_status" in publish
    assert "'PROVEN'" in publish


def test_r6b2_position_requires_equal_proven_lineage_across_durable_chain() -> None:
    source = POSITION.read_text(encoding="utf-8")
    assert "Universal Entry durable binding lineage mismatch" in source
    assert "Universal Entry durable binding lineage is not PROVEN" in source
    assert "strategy_execution_binding_id" in source
    assert "identity_status" in source
    assert "persisted StrategyPosition admission/binding lineage mismatch" in source


def test_r6b2_consumer_is_pinned_to_current_trading_account() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    assert "CRIPTA_TRADING_ACCOUNT_ID=ta-2122c81ecff14f3b85b102db605212f7" in unit
