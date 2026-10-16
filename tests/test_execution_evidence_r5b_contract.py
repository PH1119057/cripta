from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/sql/20261016_execution_evidence_r5b.sql"
PRIVATE = ROOT / "operations/connectivity/private_runtime.py"
SCHEMA = ROOT / "operations/connectivity/runtime_schema.py"


def test_r5b_schema_is_additive_and_keeps_legacy_primary_keys() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TABLE runtime.executions" in sql
    assert "ALTER TABLE runtime.exchange_order_history" in sql
    assert sql.count("DEFAULT 'LEGACY_UNKNOWN'") == 2
    upper = sql.upper()
    assert "DROP " not in upper
    assert "DELETE FROM" not in upper
    assert "UPDATE RUNTIME." not in upper
    assert "CREATE INDEX" not in upper


def test_r5b_authenticated_exchange_evidence_is_marked_proven() -> None:
    source = PRIVATE.read_text(encoding="utf-8")
    rest = source[source.index("def refresh_recent_executions"):source.index("def ", source.index("def refresh_recent_executions")+5)]
    history = source[source.index("def upsert_exchange_order_history"):source.index("def upsert_position")]
    private = source[source.index("def handle_private"):source.index("def private_loop")]
    for section in (rest, history, private):
        assert "TRADING_ACCOUNT_ID" in section
        assert "ACCOUNT_CONNECTOR_ID" in section
        assert "'PROVEN'" in section
    assert "ON CONFLICT(exec_id) DO UPDATE SET" in source
    assert "ON CONFLICT(order_id) DO UPDATE SET" in history


def test_r5b_does_not_stamp_strategy_owned_commands_or_positions() -> None:
    source = PRIVATE.read_text(encoding="utf-8")
    assert "runtime.trade_commands" in source
    schema = SCHEMA.read_text(encoding="utf-8")
    assert "EXPECTED_RUNTIME_SCHEMA_VERSION" in schema
    # Strategy-owned durable lineage is intentionally deferred to R6 binding.
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "runtime.trade_commands" not in sql
    assert "runtime.position_ownership" not in sql
    assert "runtime.capital_reservations" not in sql
    assert "runtime.exchange_position_slot_claims" not in sql
