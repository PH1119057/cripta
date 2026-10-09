"""The deployed mainnet gate stores smallint 0/1, never PostgreSQL boolean."""
from pathlib import Path


def test_release_guard_uses_smallint_gate_predicates():
    src = Path("src/bybit_workbench/release_arm_safety.py").read_text()
    assert "SET enabled=0,reason=%s" in src
    assert "WHERE mode='mainnet' AND enabled=1" in src
    assert "SET enabled=false" not in src
    assert "AND enabled=true" not in src


def test_incoming_release_preinhibit_uses_smallint():
    src = Path("operations/infrastructure/cripta-apply-incoming").read_text()
    assert "was_open smallint;" in src
    assert "IF was_open = 1 THEN" in src
    assert "SET enabled=0" in src


def test_disposable_postgres_fixture_matches_production_column_type():
    src = Path("tests/test_release_arm_postgres.py").read_text()
    assert "enabled smallint not null" in src
    assert "== (0,)" in src
