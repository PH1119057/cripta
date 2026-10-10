from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path("operations/monitoring/universal_entry_shadow.py")


def test_worker_cleanup_only_joins_started_threads() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    functions = [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for name in ("_run_observer_epoch",):
        matches = [n for n in functions if n.name == name]
        assert len(matches) == 1
        function_text = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), matches[0])
        assert function_text is not None
        assert (
            "if oi_thread.ident is not None:\n            oi_thread.join(timeout=2.0)"
            in function_text
        )
        assert (
            "if trade_mirror_thread.ident is not None:\n            trade_mirror_thread.join(timeout=2.0)"
            in function_text
        )


def test_thread_that_never_started_is_safe_to_skip() -> None:
    from threading import Thread

    never_started = Thread(target=lambda: None)
    assert never_started.ident is None
    if never_started.ident is not None:
        never_started.join(timeout=0.01)
