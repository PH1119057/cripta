"""Exercise actual first-mutation/dispatch functions, not a duplicated query."""
import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize("path,account_kw", [
    ("operations/connectivity/universal_entry_consumer.py", True),
    ("operations/connectivity/private_runtime.py", False),
])
def test_every_execution_boundary_uses_the_same_scoped_predicate(path, account_kw):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "_critical_lifecycle_fault_code")
    calls = []

    def classified(db, *, trading_account_id):
        calls.append((db, trading_account_id))
        return "CURRENT_RISK"

    namespace = {"entry_blocking_lifecycle_fault": classified,
                 "TRADING_ACCOUNT_ID": "ta-main"}
    code = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(code), path, "exec"), namespace)
    db = object()
    args = {"trading_account_id": "ta-main"} if account_kw else {}
    assert namespace[function.name](db, **args) == "CURRENT_RISK"
    assert calls == [(db, "ta-main")]
