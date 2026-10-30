"""Retired private writer may be inspected, but never started as a runtime."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "research/server/connectivity/private_runtime.py"
ACTIVE = ROOT / "operations/connectivity/private_runtime.py"


def test_historical_unscoped_private_writer_fails_before_side_effects() -> None:
    source = LEGACY.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(LEGACY))
    main = next(
        item for item in module.body
        if isinstance(item, ast.FunctionDef) and item.name == "main"
    )
    statements = [
        item for item in main.body if not isinstance(item, ast.Global)
    ]
    assert statements
    assert isinstance(statements[0], ast.Raise)
    raise_code = ast.get_source_segment(source, statements[0]) or ""
    assert "RETIRED_GLOBAL_PRIVATE_RUNTIME" in raise_code
    assert "operations/connectivity/private_runtime.py" in raise_code

    # Historical routines are retained for provenance, not restored as writers.
    assert "DELETE FROM runtime.hot_positions WHERE symbol=%s" in source
    assert "ON CONFLICT(exec_id)" in source
    assert ACTIVE.is_file()
    assert "trading_account_id" in ACTIVE.read_text(encoding="utf-8")
