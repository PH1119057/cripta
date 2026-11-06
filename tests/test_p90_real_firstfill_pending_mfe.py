"""P90 REAL: the source cursor cannot outrun the private first-fill handoff.

Runs the method AST from the ACTUAL checkout. No canned fallback in CI.
No Exchange I/O and no production DB access.
"""
from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/bybit_workbench/universal_exit/p90l90_real_exit_port.py"
SYMBOL = "SYNTHETICUSDT"
AT = datetime(2026, 11, 6, 8, 6, 22, tzinfo=UTC)


class NativeError(ValueError):
    pass


class Cursor:
    def __init__(self, symbol, seq, exec_id, traded_at):
        self.symbol, self.seq, self.exec_id, self.traded_at = symbol, seq, exec_id, traded_at


class Ledger:
    def __init__(self):
        self.calls = []

    def on_trade(self, **kwargs):
        self.calls.append(kwargs["cursor"].exec_id)
        return SimpleNamespace(last_native_price=kwargs["price"])


class Store:
    def __init__(self):
        self.saved = []

    def record(self, evaluation, *, position, plan):
        self.saved.append(evaluation)


def subject(*, pending=True):
    assert SOURCE.is_file(), "Actual source checkout required"
    module = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef)
               and n.name == "P90RealExitNativePort")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "__call__")
    scope = {
        "Decimal": Decimal, "InvalidOperation": InvalidOperation,
        "ALLOWED_SYMBOLS": {"synth": (SYMBOL,)}, "VERSION": "v1",
        "P90RealMfeUnverified": NativeError, "TradeCursor": Cursor,
        "pending_private_evaluation": lambda p, f: SimpleNamespace(
            status="BLOCKED", decision=None, reason="P90_REAL_PENDING_EXIT_COMMAND_UNVERIFIED"),
        "build_real_native_exit_observation": lambda **kw: SimpleNamespace(
            source_refs=kw["native_source_refs"]),
        "ExitEvaluationStatus": SimpleNamespace(BLOCKED="BLOCKED", DECISION_CREATED="DECISION_CREATED"),
    }
    wrapper = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(wrapper), str(SOURCE), "exec"), scope)
    cursor = Cursor(SYMBOL, 102, "current", AT + timedelta(milliseconds=30))
    fact = SimpleNamespace(
        event_kind="PUBLIC_TRADE", symbol=SYMBOL, event_at=cursor.traded_at,
        observed_at=cursor.traded_at, received_at=cursor.traded_at + timedelta(milliseconds=1),
        fact_id="native-current", source_refs=("synthetic:original:trade",),
        attributes=SimpleNamespace(to_dict=lambda: {"price": "100.4"}),
    )
    position = SimpleNamespace(
        symbol=SYMBOL, strategy_id="synth", strategy_version="v1",
        exit_plan_fingerprint="fp", initial_protection_confirmed_at=AT,
        strategy_position_id="synthetic-position")
    plan = SimpleNamespace(strategy_id="synth", strategy_version="v1",
                           exit_plan_fingerprint="fp")
    prior = [SimpleNamespace(symbol=SYMBOL, seq=100+i, exec_id=f"prior-{i}",
             traded_at=AT+timedelta(milliseconds=10+i), price="100.2",
             received_at=AT+timedelta(milliseconds=13+i)) for i in range(2)]
    state = {"pending": pending, "prefix": 0, "coverage": 0, "private": 0, "exec": 0}
    ledger, store = Ledger(), Store()

    def prefix(_p, _c):
        state["prefix"] += 1
        return prior

    def coverage(_p, _c):
        state["coverage"] += 1
        return True, True

    def private(_p, _c):
        state["private"] += 1
        return SimpleNamespace(observed_at=fact.received_at)

    obj = SimpleNamespace(
        _positions=lambda symbol: ((position, plan),),
        _pending=lambda pos: state["pending"], _prefix=prefix, _coverage=coverage,
        _mfe=ledger, _private=private, _source_commit="a"*40, _exit_store=store,
        _engine=SimpleNamespace(evaluate=lambda *_: SimpleNamespace(status="NO_DECISION")),
        _execution_port=lambda *_: state.__setitem__("exec", state["exec"] + 1),
    )
    return scope["__call__"], obj, fact, cursor, ledger, store, state


def test_pending_first_fill_does_not_touch_mfe_prefix_or_private_worker():
    call, obj, fact, cursor, ledger, store, t = subject()
    out = call(obj, fact, cursor)
    assert len(out) == 1 and out[0].status == "BLOCKED"
    assert ledger.calls == [] and t["prefix"] == t["coverage"] == t["private"] == t["exec"] == 0
    assert store.saved == list(out)


def test_completed_first_fill_replays_original_prefix_in_exact_order():
    call, obj, fact, cursor, ledger, _store, t = subject(pending=False)
    out = call(obj, fact, cursor)
    assert len(out) == 1 and out[0].status == "NO_DECISION"
    assert ledger.calls == ["prior-0", "prior-1", "current"]
    assert (t["prefix"], t["coverage"], t["private"], t["exec"]) == (1, 3, 1, 0)


def test_pending_to_completed_does_not_skip_buffered_native_exec_ids():
    call, obj, fact, cursor, ledger, _store, t = subject()
    call(obj, fact, cursor)
    assert ledger.calls == []
    t["pending"] = False
    call(obj, fact, cursor)
    assert ledger.calls == ["prior-0", "prior-1", "current"]


def test_unverified_native_prefix_still_fails_closed():
    call, obj, fact, cursor, ledger, _store, t = subject(pending=False)
    def broken(_p, _c):
        raise NativeError("P90_REAL_LIVE_SOURCE_NATIVE_INTERVAL_UNPROVEN")
    obj._prefix = broken
    with pytest.raises(NativeError, match="NATIVE_INTERVAL_UNPROVEN"):
        call(obj, fact, cursor)
    assert ledger.calls == [] and t["exec"] == 0


def test_invalid_envelope_rejected_before_pending_check():
    call, obj, fact, cursor, ledger, _store, t = subject()
    fact.received_at = AT - timedelta(seconds=1)
    with pytest.raises(NativeError, match="ENVELOPE_UNPROVEN"):
        call(obj, fact, cursor)
    assert ledger.calls == [] and t["prefix"] == 0
