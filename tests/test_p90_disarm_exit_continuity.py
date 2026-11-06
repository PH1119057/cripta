"""Execute the production composition/admission branches without network IO."""
from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

PATH = Path("operations/monitoring/universal_entry_shadow.py")


def _source() -> ast.Module:
    return ast.parse(PATH.read_text(encoding="utf-8"))


def _namespace() -> dict[str, Any]:
    return {
        "os": os, "cast": cast, "Any": Any, "LOADED_COMMIT": "a" * 40,
        "OWNER_ARM_STAGE_GATE_REASON": "STAGE",
        "OWNER_ARM_OPEN_GATE_REASON": "OPEN",
        "OWNER_FULL_CONTOUR_ENTRY_WINDOW_GATE_REASON": "DIAGNOSTIC",
        "REAL_ENTRY_RUNTIME_NOT_READY_REASON": "NOT_READY",
        "PRIVATE_RECONNECT_GATE_REASON": "private WS reconnect: verification pending",
        "P90_REAL_VERSION": "v3", "P90_MAIN_ID": "MAIN", "P90_SUB_ID": "SUB",
    }


class _DB:
    def __init__(self, exposure: int, enabled: bool = False,
                 reason: str = "OWNER_DISARM") -> None:
        self.exposure = exposure
        self.enabled = enabled
        self.reason = reason
        self.queries: list[str] = []

    def execute(self, sql: str, params: object = None) -> SimpleNamespace:
        self.queries.append(sql)
        if "execution_gates" in sql:
            result = (self.enabled, self.reason)
        elif "position_ownership" in sql:
            assert params == ("v3",)
            assert "RECONCILIATION_REQUIRED" in sql
            result = (self.exposure,)
        else:
            pytest.fail("unexpected SQL: " + sql)
        return SimpleNamespace(fetchone=lambda: result)


def _select(exposure: int, monkeypatch: pytest.MonkeyPatch, *, forced: bool = False,
            selected: frozenset[str] = frozenset(), reason: str = "OWNER_DISARM",
            enabled: bool = False):
    monkeypatch.setenv("CRIPTA_P90_REAL_SOURCE_ONLY", "ENABLED" if forced else "DISABLED")
    epoch = next(n for n in _source().body if isinstance(n, ast.FunctionDef)
                 and n.name == "_run_observer_epoch")
    start = next(i for i, n in enumerate(epoch.body) if isinstance(n, ast.Assign)
                 and isinstance(n.targets[0], ast.Name)
                 and n.targets[0].id == "p90_real_force_source_only")
    end = next(i for i, n in enumerate(epoch.body) if isinstance(n, ast.Assign)
               and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "p90_lineage")
    db = _DB(exposure, enabled=enabled, reason=reason)
    namespace = {
        **_namespace(), "connection": db,
        "observer_split": SimpleNamespace(p90_real=tuple(
            SimpleNamespace(activation=SimpleNamespace(activation_id=k)) for k in ("M", "S")
        )),
        "_real_execution_selected_activation_ids": lambda *_: selected,
        "_p90_source_only_gate_compatible": lambda **_: True,
    }
    exec(compile(ast.Module(body=epoch.body[start:end], type_ignores=[]), str(PATH), "exec"),
         namespace)
    return namespace, db


@pytest.mark.parametrize("exposure", [1, 2, 5, 60])
def test_disarm_with_exposure_keeps_real_exit_composition(exposure, monkeypatch):
    state, _ = _select(exposure, monkeypatch)
    assert state["p90_real_live"] is True
    assert state["p90_real_degraded"] is True
    assert state["p90_real_source_only"] is False
    assert state["p90_real_staged"] is False


def test_genuinely_flat_disarm_keeps_no_order_bootstrap(monkeypatch):
    state, db = _select(0, monkeypatch)
    assert state["p90_real_source_only"] is True
    assert state["p90_real_live"] is False
    assert any("position_ownership" in q for q in db.queries)


def test_explicit_diagnostic_cannot_gain_exit_mutation_authority(monkeypatch):
    state, _ = _select(1, monkeypatch, forced=True)
    assert state["p90_real_source_only"] is True
    assert state["p90_real_live"] is False


@pytest.mark.parametrize("selected", [frozenset(), frozenset({"M"}), frozenset({"M", "S"})])
def test_closed_gate_returns_negative_admission_before_missing_permissions(selected):
    epoch = next(n for n in _source().body if isinstance(n, ast.FunctionDef)
                 and n.name == "_run_observer_epoch")
    callback = next(n for n in ast.walk(epoch) if isinstance(n, ast.FunctionDef)
                    and n.name == "p90_admitted_account")
    called: list[str] = []
    db = _DB(1)
    namespace = {
        **_namespace(), "connection": db, "AccountAdmissionContext": Any,
        "datetime": Any, "source_ports": SimpleNamespace(
            _source_transaction=lambda: called.append("source_proof")
        ),
        "observer_split": SimpleNamespace(p90_real=()),
        "active_by_symbol": {"LTCUSDT": "M"},
        "expected_activation_ids": frozenset({"M", "S"}),
        "_real_execution_selected_activation_ids": lambda *_: selected,
        "_blocked_multi_account_admissions": lambda *a, **kw: {
            "M": ("durable-negative-context",)
        },
        "real_candidates": {}, "p90_real_staged": False, "p90_real_degraded": True,
    }
    exec(compile(ast.Module(body=[callback], type_ignores=[]), str(PATH), "exec"), namespace)
    assert namespace["p90_admitted_account"]("LTCUSDT", None) == (
        ("durable-negative-context",), False
    )
    assert called == ["source_proof"]
    # A gate reopening alone cannot re-authorize a retained Exit-only runtime.
    db.enabled = True
    with pytest.raises(RuntimeError, match="PERMISSION_NOT_CURRENT|ENTRY_GATE_DRIFT"):
        namespace["p90_admitted_account"]("LTCUSDT", None)


def _transition(current, latest):
    node = next((n for n in _source().body if isinstance(n, ast.FunctionDef)
                 and n.name == "_p90_disarmed_signature_compatible"), None)
    assert node is not None, "DISARM must retain the running original source/Exit owner"
    namespace = _namespace()
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(PATH), "exec"), namespace)
    return namespace[node.name](current, latest, activation_ids=frozenset({"M", "S"}))


def _signature(enabled, permission):
    return (("MAINNET_GATE", str(enabled)),
            ("M", "MAIN", "v3", "card", "activation-at", "entry", "exit", permission, "at"),
            ("S", "SUB", "v3", "card", "activation-at", "entry", "exit", permission, "at"),
            ("BINDING", "account", "connector"))


def test_disarm_permission_retirement_retains_running_source():
    assert _transition(_signature(True, "True"), _signature(False, "False"))


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 5, 6])
def test_disarm_never_hides_strategy_or_plan_identity_drift(index):
    latest = list(_signature(False, "False"))
    row = list(latest[1])
    row[index] = "changed"
    latest[1] = tuple(row)
    assert not _transition(_signature(True, "True"), tuple(latest))


def test_disarm_never_hides_binding_drift_or_automatic_rearm():
    current = _signature(True, "True")
    latest = (*_signature(False, "False")[:-1], ("BINDING", "other-account", "connector"))
    assert not _transition(current, latest)
    assert not _transition(_signature(False, "False"), current)


def test_disarm_does_not_ignore_ordinary_permission_changes_or_missing_p90_owner():
    ordinary = ("O", "ordinary", "v1", "card", "at", "entry", "exit", "True", "at")
    current = (*_signature(True, "True"), ordinary)
    latest = (*_signature(False, "False"), (*ordinary[:-2], "False", "new-at"))
    assert not _transition(current, latest)
    assert not _transition(current[:2], latest[:2])
