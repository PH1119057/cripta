from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/sql/20261016_binding_lineage_r6b1.sql"
ENGINE = ROOT / "src/bybit_workbench/universal_entry/engine.py"
ADMISSION = ROOT / "src/bybit_workbench/entry_admission.py"
CAPITAL = ROOT / "src/bybit_workbench/capital_reservation.py"
STORAGE = ROOT / "src/bybit_workbench/universal_entry/storage.py"
OBSERVER = ROOT / "operations/monitoring/universal_entry_shadow.py"


def test_r6b1_schema_adds_binding_lineage_without_key_rewrite() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in (
        "runtime.capital_reservations",
        "runtime.exchange_position_slot_claims",
        "strategy_entry.entry_decisions",
        "strategy_entry.execution_requests",
    ):
        assert f"ALTER TABLE {table}" in sql
    assert sql.count("DEFAULT 'LEGACY_UNKNOWN'") == 4
    upper = sql.upper()
    assert "DROP " not in upper
    assert "DELETE FROM" not in upper
    assert "UPDATE RUNTIME." not in upper
    assert "UPDATE STRATEGY_ENTRY." not in upper


def test_r6b1_atomic_admission_rechecks_binding_before_reservation() -> None:
    source = ADMISSION.read_text(encoding="utf-8")
    transaction = source[source.index("def admit("):source.index("def _capital_request")]
    binding_lock = transaction.index("FROM strategy_entry.lock_strategy_execution_binding(%s)")
    capital_reservation = transaction.index("self._capital.reserve")
    assert binding_lock < capital_reservation
    assert "StrategyExecutionBindingUnavailable" in transaction
    assert "strategy execution binding disappeared before admission" in transaction
    assert "strategy execution binding is not active for admission" in transaction
    assert 'str(binding[3]) != "PRIMARY"' in transaction
    assert "real_binding_allocation_allowed(" in transaction
    assert "allocation_mode=str(binding[4])" in transaction
    assert "binding[10] is not True" in transaction
    assert "account state generation TradingAccount mismatch" in transaction
    assert "trading_account_id=%s" in transaction
    assert "identity_status='PROVEN'" in transaction


def test_r6b1_engine_and_observer_propagate_exact_binding() -> None:
    engine = ENGINE.read_text(encoding="utf-8")
    observer = OBSERVER.read_text(encoding="utf-8")
    assert "execution_bindings: Mapping[str, StrategyExecutionBindingRef]" in engine
    assert "execution_binding=execution_binding" in engine
    assert "strategy_execution_binding_id=decision.strategy_execution_binding_id" in engine
    assert "def _real_execution_bindings(" in observer
    assert "execution_bindings=execution_bindings" in observer


def test_r6b1_reservation_slot_decision_request_store_lineage() -> None:
    capital = CAPITAL.read_text(encoding="utf-8")
    admission = ADMISSION.read_text(encoding="utf-8")
    storage = STORAGE.read_text(encoding="utf-8")
    for field in ("strategy_execution_binding_id","execution_leg","trading_account_id"):
        assert field in capital
        assert field in admission
        assert field in storage
    assert "'PROVEN'" in admission
    assert "'PROVEN'" in storage
    assert "lock_identity = request.trading_account_id" in capital
    assert "R10 capital reservation requires PROVEN StrategyExecutionBinding lineage" in capital
    assert "LEGACY_UNKNOWN" not in capital
