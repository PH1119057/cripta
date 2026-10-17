from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
BRIDGE = (ROOT / "src/bybit_workbench/exchange/bybit/protection_supervisor_bridge.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "operations/monitoring/lifecycle_supervisor_runtime.py").read_text(encoding="utf-8")

def test_protection_command_lineage_can_be_exact_account_scoped() -> None:
    assert "trading_account_id: str | None = None" in BRIDGE
    assert "identity_status='PROVEN' AND (%s IS NULL OR trading_account_id=%s)" in BRIDGE
    assert "LEGACY_UNKNOWN" not in BRIDGE

def test_production_lifecycle_passes_exact_trading_account() -> None:
    assert "trading_account_id=TRADING_ACCOUNT_ID" in RUNTIME