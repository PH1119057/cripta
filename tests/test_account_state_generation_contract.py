from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_account_generation_migration_is_atomic_and_defers_attempt_fk() -> None:
    sql = (
        ROOT / "operations/sql/20261006_account_state_generation_v1.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS runtime.account_state_generations" in sql
    assert "state IN ('COLLECTING','COMPLETE','FAILED')" in sql
    assert "GRANT SELECT,INSERT,UPDATE ON runtime.account_state_generations TO cripta" in sql
    assert "exchange_position_slot_claims_strategy_attempt_id_fkey" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql


def test_real_admission_validates_exact_generation_and_generation_mode_ref() -> None:
    source = (ROOT / "src/bybit_workbench/entry_admission.py").read_text(encoding="utf-8")
    assert "current_complete_account_state_generation" in source
    assert "request.account_state_generation_id" in source
    assert "generation.position_mode_refs.get(request.symbol" in source
    assert "generation.available_balance != request.capacity_available" in source


def test_reverse_worker_uses_generation_capacity_not_dispatcher_age() -> None:
    source = (ROOT / "operations/connectivity/r1_reverse_worker.py").read_text(
        encoding="utf-8"
    )
    capacity = source[source.index("def _capacity("):source.index("def _create_open_request(")]
    assert "current_complete_account_state_generation" in capacity
    assert "dispatcher_v2.trading_capacity_snapshots" not in capacity
    assert "capacity_max_age_seconds" not in capacity

def test_account_generation_can_fail_closed_on_exact_trading_account() -> None:
    source = (ROOT / "src/bybit_workbench/account_state_generation.py").read_text(encoding="utf-8")
    assert "trading_account_id: str | None = None" in source
    assert "AND trading_account_id=%s" in source
    assert "AND identity_status='PROVEN'" in source


def test_reverse_worker_requires_exact_strategy_execution_binding() -> None:
    source = (ROOT / "operations/connectivity/r1_reverse_worker.py").read_text(encoding="utf-8")
    assert "def _active_primary_binding(" in source
    assert "b.execution_role='PRIMARY'" in source
    assert "b.allocation_mode='DEDICATED_STRATEGY'" in source
    assert "strategy_execution_binding_id=binding_id" in source
    assert "identity_status" in source
