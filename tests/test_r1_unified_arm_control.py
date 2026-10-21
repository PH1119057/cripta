from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bybit_workbench import r1_unified_arm_control as rail
from bybit_workbench.live_arm_readiness import LiveArmContext
from bybit_workbench.universal_entry.r1_strategy import build_r1_unified_card

NOW = datetime(2026, 10, 21, 17, 30, tzinfo=UTC)
RELEASE = "a" * 40


class Rows:
    def __init__(self, values):
        self.values = values

    def fetchone(self):
        return self.values[0] if self.values else None

    def fetchall(self):
        return list(self.values)


class FakeDB:
    def __init__(self):
        self.writes = []
        self.session_ids = {}
        self.gate = 0
        self.permissions = []
        self.sessions = []
        self.nested_rollbacks = 0

    @contextmanager
    def transaction(self):
        prev_writes = list(self.writes)
        prev_sessions = dict(self.session_ids)
        try:
            yield
        except Exception:
            self.writes = prev_writes
            self.session_ids = prev_sessions
            self.nested_rollbacks += 1
            raise

    def execute(self, statement, args=()):
        upper = statement.strip().upper()
        if upper.startswith("INSERT") or upper.startswith("UPDATE"):
            self.writes.append((statement, args))
            if "INSERT INTO control.live_arm_sessions" in statement:
                self.session_ids[args[5]] = args[0]
            return Rows([])
        if "FROM control.execution_gates" in statement:
            return Rows([(self.gate,)])
        if "FROM strategy_entry.strategy_execution_bindings" in statement:
            return Rows([("binding-one",)])
        if "FROM strategy_entry.lock_strategy_execution_binding" in statement:
            return Rows([(
                "one-activation", "PRIMARY", "one-account", "PRIMARY",
                "DEDICATED_STRATEGY", "ACTIVE", "ACTIVE", "MAINNET", True,
            )])
        if "FROM strategy_entry.execution_permissions" in statement:
            return Rows(self.permissions)
        if "FROM control.live_arm_sessions" in statement:
            return Rows(self.sessions)
        raise AssertionError("Unrecognized SQL: " + statement)


def _contexts():
    card = build_r1_unified_card()
    return tuple(
        LiveArmContext(
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            strategy_activation_id="one-activation",
            symbol=symbol,
            release_commit=RELEASE,
        )
        for symbol in card.symbols
    )


def _patch(monkeypatch, db, *, fail_symbol=None, fail_owner=False):
    contexts = _contexts()
    monkeypatch.setattr(
        rail, "unified_r1_contexts",
        lambda *a, **k: (contexts, "one-account"),
    )

    def _proof(connection, *, context, now, require_owner_approval):
        failed = (
            context.symbol == fail_symbol
            and (require_owner_approval if fail_owner else not require_owner_approval)
        )
        return SimpleNamespace(
            ready=not failed,
            failed_codes=("CRITICAL_FAULT_DELIVERY",) if failed else (),
        )

    monkeypatch.setattr(rail, "evaluate_live_arm", _proof)
    monkeypatch.setattr(
        rail, "active_live_arm_session",
        lambda connection, *, context: db.session_ids.get(context.symbol),
    )
    permissions = []

    class Store:
        def __init__(self, connection):
            assert connection is db

        def set_execution_permission(self, **kwargs):
            permissions.append(kwargs)
            return {"enabled": kwargs["enabled"]}

    monkeypatch.setattr(rail, "StrategyDashboardStore", Store)
    return permissions


def _arm(db, *, confirmed=True, baseline_ready=True, observer_ready=True):
    return rail.arm_unified_r1(
        db,
        release_commit=RELEASE,
        now=NOW,
        operator="owner",
        request_id="owner-request-unique-123",
        confirmed=confirmed,
        observer_ready=observer_ready,
        baseline_ready=baseline_ready,
    )


def test_unified_arm_requires_explicit_confirmed_owner_intent():
    db = FakeDB()
    with pytest.raises(ValueError, match="NOT_EXPLICITLY_CONFIRMED"):
        _arm(db, confirmed=False)
    assert db.writes == []


@pytest.mark.parametrize(
    ("reason", "values"),
    [
        ("OPERATIONAL_BASELINE_BLOCKED", {"baseline_ready": False}),
        ("OBSERVER_NOT_READY", {"observer_ready": False}),
    ],
)
def test_unified_arm_refuses_missing_baseline_or_observer(reason, values):
    db = FakeDB()
    with pytest.raises(ValueError, match=reason):
        _arm(db, **values)
    assert db.writes == []


def test_unified_arm_refuses_incomplete_nonwaived_critical_delivery(monkeypatch):
    db = FakeDB()
    _patch(monkeypatch, db, fail_symbol="APTUSDT")
    with pytest.raises(ValueError, match="CRITICAL_FAULT_DELIVERY"):
        _arm(db)
    assert db.writes == []
    assert db.nested_rollbacks == 1


def test_unified_arm_is_one_owner_action_one_permission_five_scopes(monkeypatch):
    db = FakeDB()
    permissions = _patch(monkeypatch, db)
    result = _arm(db)
    assert result["status"] == "ARMED"
    assert result["strategies"] == 1
    assert result["instrument_scopes"] == 5
    assert result["aggregate_cap_usdt"] == "50"
    assert len(result["session_ids"]) == 5
    assert len(permissions) == 1
    assert permissions[0]["strategy_id"] == "r1_five_symbols"
    assert permissions[0]["enabled"] is True
    evidence = [sql for sql, _ in db.writes if "live_arm_evidence" in sql]
    sessions = [sql for sql, _ in db.writes if "live_arm_sessions" in sql]
    gates = [sql for sql, _ in db.writes if "UPDATE control.execution_gates" in sql]
    gate_events = [sql for sql, _ in db.writes if "execution_gate_events" in sql]
    assert len(evidence) == 5
    assert len(sessions) == 5
    assert len(gates) == 1
    assert len(gate_events) == 1
    assert len(db.session_ids) == 5
    # Five technical witnesses share ONE operator request ID / owner decision.
    request_ids = set()
    for sql, args in db.writes:
        if "live_arm_evidence" in sql:
            witness = json.loads(args[-1])
            request_ids.add(witness["owner_action_request_id"])
            assert witness["aggregate_committed_usdt_max"] == "50"
    assert request_ids == {"owner-request-unique-123"}


def test_unified_arm_final_evidence_failure_rolls_back_all_sessions(monkeypatch):
    db = FakeDB()
    _patch(monkeypatch, db, fail_symbol="DOTUSDT", fail_owner=True)
    # Failure after partial evidence inserts must rollback all witnesses.
    with pytest.raises(ValueError, match="CRITICAL_FAULT_DELIVERY"):
        _arm(db)
    assert db.writes == []
    assert db.session_ids == {}


def test_unified_arm_refuses_open_gate_before_other_changes(monkeypatch):
    db = FakeDB()
    db.gate = 1
    _patch(monkeypatch, db)
    with pytest.raises(ValueError, match="MAINNET_GATE_NOT_CLOSED"):
        _arm(db)
    assert db.writes == []


def test_disarm_only_unified_strategy_and_closes_global_gate():
    db = FakeDB()
    db.gate = 1
    result = rail.disarm_unified_r1(
        db,
        now=NOW,
        operator="owner",
        request_id="owner-disarm-123",
    )
    assert result["status"] == "DISARMED"
    assert result["strategies"] == 1
    statements = [sql for sql, _ in db.writes]
    assert len(statements) == 4
    assert all(
        "r1_five_symbols" in str(args) or "control.execution_gates" in sql
        or "execution_gate_events" in sql
        for sql, args in db.writes
    )


def test_unified_route_requires_confirmed_owner_and_protects_generic_gate():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "operations/dashboard/app.py")
    text = source.read_text(encoding="utf-8")
    assert 'R1_UNIFIED_MICRO_LIVE_PATH = "/api/r1/unified/micro-live"' in text
    assert "_u6_r1_unified_micro_live(handler, request)" in text
    assert "request.get(\"confirmed\") is not True" in text
    assert "arm_unified_r1(" in text
    assert "disarm_unified_r1(" in text
    assert "UNIFIED_R1_MUST_USE_DEDICATED_OWNER_ARM_ENDPOINT" in text
    assert "R1_UNIFIED_MICRO_LIVE_PATH," in text.split(
        "U6_STRATEGY_POST_PATHS", 1
    )[1].split("}", 1)[0]
