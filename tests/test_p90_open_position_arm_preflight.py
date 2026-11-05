"""Execute the actual Observer startup branch; no market/account mutations."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry import p90l90_real_open_restart as restart
from operations.monitoring import p90l90_real_source_bootstrap as bootstrap


@pytest.mark.parametrize("mode,has_open,expected", [
    ("staged", True, ["inspect"]),
    ("staged", False, ["inspect", "flat", "candles"]),
    ("live", True, ["inspect"]),
    ("live", False, ["inspect", "candles"]),
    ("source_only", False, ["flat", "candles"]),
    ("staged", "corrupt", ["inspect"]),
    ("live", "corrupt", ["inspect"]),
])
def test_signed_flat_preflight_is_only_for_a_fresh_source(
    monkeypatch, mode, has_open, expected,
):
    path = Path("operations/monitoring/universal_entry_shadow.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    epoch = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name == "_run_observer_epoch")
    startup = next(n for n in epoch.body if isinstance(n, ast.Try)
                   and "trade_mirror_thread.start()" in ast.unparse(n.body[0]))
    end = next(i for i, n in enumerate(startup.body) if isinstance(n, ast.Assign)
               and "_connect_and_subscribe" in ast.unparse(n.value))
    calls = []

    def inspect(*_, **__):
        calls.append("inspect")
        if has_open == "corrupt":
            raise RuntimeError("P90_REAL_RESTART_SOURCE_CHECKPOINT_CORRUPT")
        return "p90-real-epoch-" + "b" * 40 if has_open else None

    def flat(*_, **__):
        assert not has_open, "an OPEN position is not an invalid flat-account proof"
        calls.append("flat")

    monkeypatch.setattr(restart, "inspect_p90_real_open_restart_epoch", inspect)
    monkeypatch.setattr(bootstrap, "preflight_p90_real_private_flat_source", flat)
    def noop(*_):
        pass
    namespace = {
        "trade_mirror_thread": SimpleNamespace(start=noop),
        "trade_mirror_ready": SimpleNamespace(wait=lambda _: True),
        "trade_mirror": SimpleNamespace(snapshot=lambda: {"state": "ACTIVE"}),
        "oi_thread": SimpleNamespace(start=noop),
        "p90_real_staged": mode == "staged", "p90_real_source_only": mode == "source_only",
        "p90_real_live": mode != "source_only", "connection": object(),
        "observer_split": SimpleNamespace(p90_real=("main", "sub")),
        "p90l90_symbols": {"LTCUSDT"}, "LOADED_COMMIT": "a" * 40,
        "MIRROR_READY_TIMEOUT_SECONDS": 10, "ContinuityNotProvable": RuntimeError,
        "preparation_progress": noop, "_fetch_history": noop,
        "cold_preparation_wait": lambda exc: exc,
        "prepare_p90_closed_tails": lambda *a, **kw: calls.append("candles"),
    }
    code = compile(ast.Module(body=startup.body[:end], type_ignores=[]), str(path), "exec")
    if has_open == "corrupt":
        with pytest.raises(RuntimeError, match="SOURCE_CHECKPOINT_CORRUPT"):
            exec(code, namespace)
    else:
        exec(code, namespace)
    assert calls == expected
