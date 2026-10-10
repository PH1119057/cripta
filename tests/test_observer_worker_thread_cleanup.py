from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path("operations/monitoring/universal_entry_shadow.py")


def test_worker_cleanup_only_joins_started_threads() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_run_observer_epoch"
    ]
    assert len(functions) == 1
    function_text = ast.get_source_segment(source, functions[0])
    assert function_text is not None
    assert "if oi_thread.ident is not None:" in function_text
    assert "if trade_mirror_thread.ident is not None:" in function_text
    assert "oi_thread.join(timeout=2.0)" in function_text
    assert "trade_mirror_thread.join(timeout=2.0)" in function_text


def test_thread_that_never_started_is_safe_to_skip() -> None:
    from threading import Thread

    never_started = Thread(target=lambda: None)
    assert never_started.ident is None
    if never_started.ident is not None:
        never_started.join(timeout=0.01)
