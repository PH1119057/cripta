"""Issue466: execute real gate writer/composition, no Exchange or service mutation."""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_p90_disarm_exit_continuity import _select

PRIVATE = Path("operations/connectivity/private_runtime.py")
RECONNECT = "private WS reconnect: verification pending"
EXPECTED = frozenset({"M", "S"})


def _writer(*, sub=False, latch=None):
    function = next(n for n in ast.parse(PRIVATE.read_text(encoding="utf-8")).body
                    if isinstance(n, ast.FunctionDef) and n.name == "disarm_new_entries")
    scope = {"psycopg": SimpleNamespace(Connection=object),
             "_is_sub_private_worker": lambda: sub,
             "time": SimpleNamespace(time=lambda: 2),
             "PRIVATE_RECONNECT_GATE_REASON": RECONNECT,
             "TRADING_ACCOUNT_ID": "ta-sub", "ACCOUNT_CONNECTOR_ID": "ac-sub",
             "latch_account_entry_safety": latch}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(PRIVATE), "exec"), scope)
    return scope["disarm_new_entries"]


@pytest.fixture
def gate_db():
    class Connection(sqlite3.Connection):
        def execute(self, sql, params=()):
            return super().execute(sql.replace("%s", "?"), params)

    db = sqlite3.connect(":memory:", factory=Connection)
    db.execute("ATTACH DATABASE ':memory:' AS control")
    db.execute("CREATE TABLE control.execution_gates("
               "mode text,enabled integer,reason text,updated_at_epoch_ms bigint)")
    yield db
    db.close()


@pytest.mark.parametrize("reason", ["OWNER_DISARM", "NOT_READY", "AMBIGUOUS_MUTATION",
                                    "MAINTENANCE", RECONNECT])
def test_reconnect_never_overwrites_already_closed_gate_or_its_timestamp(gate_db, reason):
    gate_db.execute("INSERT INTO control.execution_gates VALUES('mainnet',0,?,1000)", (reason,))
    for _retry in range(3):
        _writer()(gate_db, RECONNECT)
    assert gate_db.execute("SELECT enabled,reason,updated_at_epoch_ms FROM "
                           "control.execution_gates").fetchone() == (0, reason, 1000)


def test_open_gate_is_closed_on_disconnect_and_other_fault_can_replace_reconnect(gate_db):
    gate_db.execute("INSERT INTO control.execution_gates VALUES('mainnet',1,'OWNER_ARM',1000)")
    _writer()(gate_db, RECONNECT)
    assert gate_db.execute("SELECT enabled,reason FROM control.execution_gates").fetchone() == (
        0, RECONNECT)
    _writer()(gate_db, "AMBIGUOUS_MUTATION")
    assert gate_db.execute("SELECT enabled,reason FROM control.execution_gates").fetchone() == (
        0, "AMBIGUOUS_MUTATION")


def test_sub_reconnect_keeps_its_account_latch_without_writing_main_gate(gate_db):
    gate_db.execute("INSERT INTO control.execution_gates VALUES('mainnet',1,'OWNER_ARM',1000)")
    gate_db.commit()
    calls = []
    _writer(sub=True, latch=lambda _db, **kw: calls.append(kw))(gate_db, RECONNECT)
    assert calls == [{"trading_account_id": "ta-sub", "account_connector_id": "ac-sub",
                      "reason": "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN"}]
    assert gate_db.execute("SELECT enabled,reason FROM control.execution_gates").fetchone() == (
        1, "OWNER_ARM")


@pytest.mark.parametrize("exposure", [0, 1, 2, 60])
def test_persisted_reconnect_reason_retains_native_exit_only_composition(monkeypatch, exposure):
    state, db = _select(exposure, monkeypatch, selected=EXPECTED, reason=RECONNECT)
    assert state["p90_real_live"] is True
    assert state["p90_real_degraded"] is True
    assert state["p90_real_staged"] is False
    assert state["p90_real_source_only"] is False
    assert not db.enabled
    assert all("SELECT" in sql and "UPDATE" not in sql for sql in db.queries)


@pytest.mark.parametrize("enabled,selected,reason", [
    (True, EXPECTED, RECONNECT),
    (False, frozenset({"M"}), RECONNECT),
    (False, frozenset({"S"}), RECONNECT),
    (False, EXPECTED, "AMBIGUOUS_MUTATION"),
    (False, EXPECTED, "UNKNOWN_GATE_STATE"),
])
def test_reconnect_does_not_waive_incompatible_gate_permission_or_hard_safety_state(
    monkeypatch, enabled, selected, reason,
):
    with pytest.raises(RuntimeError, match="NATIVE_EXECUTION_OWNER_NOT_CONNECTED"):
        _select(1, monkeypatch, enabled=enabled, selected=selected, reason=reason)


def test_repeated_closed_gate_reconnect_can_continue_composition_without_rearming(
    gate_db, monkeypatch,
):
    gate_db.execute("INSERT INTO control.execution_gates VALUES('mainnet',0,'NOT_READY',1000)")
    for _retry in range(10):
        _writer()(gate_db, RECONNECT)
        enabled, reason = gate_db.execute("SELECT enabled,reason FROM "
                                          "control.execution_gates").fetchone()
        state, _ = _select(1, monkeypatch, enabled=bool(enabled), selected=EXPECTED, reason=reason)
        assert state["p90_real_degraded"] and not enabled
