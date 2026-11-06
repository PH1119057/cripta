"""The existing explicit preparation owns registration, never status polling."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_p90_real_source_release_compatibility import EPOCH, LOADED, ORIGIN, bundles

from bybit_workbench.universal_entry import p90l90_real_source_compatibility as compat
from bybit_workbench.universal_entry import runtime_loader

APP = Path("operations/dashboard/app.py")


@pytest.mark.parametrize("epoch,exposure,registrations", [
    ([], 1, 0), ([(EPOCH, LOADED)], 1, 0),
    ([(EPOCH, ORIGIN)], 0, 0), ([(EPOCH, ORIGIN)], 1, 1),
])
def test_preparation_registers_only_prior_origin_with_current_exposure(
    monkeypatch, tmp_path, epoch, exposure, registrations,
):
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_prepare_p90_source_compatibility_for_owner_request")
    calls = []
    monkeypatch.setattr(compat, "register_real_source_compatibility",
                        lambda db, **kw: calls.append(kw))
    monkeypatch.setattr(runtime_loader, "load_active_strategy_bundles", lambda db: bundles())

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=()):
            if "count(*)" in sql:
                return SimpleNamespace(fetchone=lambda: (exposure,))
            if "p90l90_real_source_epochs" in sql:
                return SimpleNamespace(fetchall=lambda: epoch)
            assert sql == "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
            return None

    ns = {"psycopg": SimpleNamespace(connect=lambda _: DB()),
          "LOADED_RELEASE_COMMIT": LOADED,
          "P90_REAL_V3_VERSION": bundles()[0].card.strategy_version,
          "Path": lambda _: tmp_path / LOADED}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(APP), "exec"), ns)
    ns[fn.name]()
    assert len(calls) == registrations
    if calls:
        assert calls[0]["epoch_id"] == EPOCH
        assert calls[0]["loaded_commit"] == LOADED
        assert calls[0]["origin_root"].name == ORIGIN


def test_registration_is_only_in_explicit_owner_request_after_disarmed_control_check():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    name = "_prepare_p90_source_compatibility_for_owner_request"
    callers = [fn for fn in tree.body if isinstance(fn, ast.FunctionDef)
               and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                       and n.func.id == name for n in ast.walk(fn))]
    assert [x.name for x in callers] == ["_u6_prepare_p90_real_v3_readiness"]
    body = ast.get_source_segment(APP.read_text(encoding="utf-8"), callers[0])
    assert body is not None
    assert body.index("begin_real_readiness_operation(") < body.index(name + "()")
    assert body.index("PREPARATION_REQUIRES_DISARMED_NEW_ENTRY") < body.index(name + "()")
    assert body.index("SERVICE_START_TIMEOUT") < body.index(name + "()")
    assert body.index(name + "()") < body.index("_wait_p90_real_v3_technical_evidence(")
