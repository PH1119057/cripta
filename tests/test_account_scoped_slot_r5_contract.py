from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_new_exchange_position_key_is_trading_account_scoped() -> None:
    identity_source = (ROOT / "src/bybit_workbench/exchange_position_identity.py").read_text(encoding="utf-8")
    lineage_source = (ROOT / "src/bybit_workbench/strategy_position_binding.py").read_text(encoding="utf-8")
    assert "trading_account_id: str" in identity_source
    assert 'return f"BYBIT:{account}:LINEAR:USDT:{normalized}:{position_idx}"' in identity_source
    assert "lineage.trading_account_id" in lineage_source


def test_real_entry_uses_exact_account_generation_and_slot_key() -> None:
    observer = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")
    consumer = (ROOT / "operations/connectivity/universal_entry_consumer.py").read_text(encoding="utf-8")
    admission = (ROOT / "src/bybit_workbench/entry_admission.py").read_text(encoding="utf-8")
    assert "exchange_position_key(TRADING_ACCOUNT_ID, symbol, 0)" in observer
    assert "trading_account_id=TRADING_ACCOUNT_ID" in observer
    assert "request.trading_account_id" in consumer
    assert "trading_account_id=request.trading_account_id" in admission


def test_reverse_open_cannot_bypass_binding_contract() -> None:
    source = (ROOT / "operations/connectivity/r1_reverse_worker.py").read_text(encoding="utf-8")
    assert "_active_primary_binding(" in source
    assert "exchange_position_key(" in source
    assert "strategy_execution_binding_id=binding_id" in source
    assert "trading_account_id=trading_account_id" in source
