from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/sql/20261016_identity_bridge_r3_r4.sql"
PRIVATE = ROOT / "operations/connectivity/private_runtime.py"
DISPATCHER = ROOT / "operations/monitoring/dispatcher_v2.py"
PRIVATE_UNIT = ROOT / "research/server/connectivity/cripta-private-runtime.service"
DISPATCHER_UNIT = ROOT / "operations/dispatcher_v2/cripta-dispatcher-v2.service"


def test_r3_r4_marks_legacy_without_historical_update() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert sql.count("DEFAULT 'LEGACY_UNKNOWN'") == 3
    assert sql.count("'PROVEN','LEGACY_UNKNOWN'") == 3
    assert sql.count("NOT VALID") == 3
    upper = sql.upper()
    assert "UPDATE RUNTIME." not in upper
    assert "UPDATE DISPATCHER_V2." not in upper
    assert "DELETE FROM" not in upper


def test_private_runtime_writes_proven_identity_and_validates_registry() -> None:
    source = PRIVATE.read_text(encoding="utf-8")
    assert 'TRADING_ACCOUNT_ID = os.environ.get("CRIPTA_TRADING_ACCOUNT_ID", "").strip()' in source
    assert 'ACCOUNT_CONNECTOR_ID = os.environ.get("CRIPTA_ACCOUNT_CONNECTOR_ID", "").strip()' in source
    assert "validate_runtime_identity_contract(bootstrap)" in source
    assert "TRADING_ACCOUNT_IDENTITY_NOT_REGISTERED" in source
    assert "identity.account_connectors" in source
    assert source.count("'PROVEN'") >= 2


def test_dispatcher_capacity_is_bound_to_exact_account_identity() -> None:
    source = DISPATCHER.read_text(encoding="utf-8")
    assert "_validate_runtime_identity(identity_connection)" in source
    assert "trading_account_id,account_connector_id,identity_status" in source
    assert "TRADING_ACCOUNT_ID" in source
    assert "ACCOUNT_CONNECTOR_ID" in source
    assert "'PROVEN'" in source


def test_units_bind_same_exact_identity_without_secret_material() -> None:
    private_unit = PRIVATE_UNIT.read_text(encoding="utf-8")
    dispatcher_unit = DISPATCHER_UNIT.read_text(encoding="utf-8")
    for unit in (private_unit, dispatcher_unit):
        assert "CRIPTA_TRADING_ACCOUNT_ID=ta-2122c81ecff14f3b85b102db605212f7" in unit
        assert "CRIPTA_ACCOUNT_CONNECTOR_ID=ac-ed73de20410548d6841aeafa5f196f70" in unit
        assert "api_secret" not in unit.lower()
