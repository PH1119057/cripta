from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from operations.dashboard import app

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 6, 16, 50, tzinfo=UTC)


class RowsCursor:
    def __init__(self, *, rows=None, row=None):
        self._rows = [] if rows is None else rows
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class ReadConnection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, parameters=()):
        self.calls.append((statement, tuple(parameters)))
        return RowsCursor(rows=self.rows)


class ResolveConnection:
    def __init__(self, resolved=True):
        self.resolved = resolved
        self.calls = []

    def execute(self, statement, parameters=()):
        self.calls.append((statement, tuple(parameters)))
        return RowsCursor(row=("observer-fault-1",) if self.resolved else None)


def test_observer_fault_read_model_exposes_unresolved_durable_evidence(monkeypatch) -> None:
    rows = [
        (
            "observer-fault-1",
            "ERROR",
            NOW,
            {
                "service": "cripta-universal-entry-observer.service",
                "source_commit": "a" * 40,
            },
            {
                "first_seen_at": "2026-10-06T16:40:00+00:00",
                "last_seen_at": "2026-10-06T16:49:00+00:00",
                "occurrence_count": 7,
                "error_type": "ValueError",
                "error_message": "forced dashboard test",
                "last_observer_epoch_id": "epoch-7",
                "owner_resolution_required": True,
            },
        )
    ]
    connection = ReadConnection(rows)
    monkeypatch.setattr(app.psycopg, "connect", lambda *_a, **_kw: connection)

    state = app.observer_runtime_fault_state()

    assert state["state"] == "red"
    assert state["count"] == 1
    item = state["items"][0]
    assert item["fault_id"] == "observer-fault-1"
    assert item["occurrence_count"] == 7
    assert item["error_type"] == "ValueError"
    assert item["last_observer_epoch_id"] == "epoch-7"
    assert item["source_commit"] == "a" * 40
    assert connection.calls[0][1] == (app.OBSERVER_RUNTIME_FAULT_CODE,)


def test_observer_fault_escalates_dashboard_health_to_red() -> None:
    merged = app.merge_observer_fault_health(
        {"state": "green", "issues": []},
        {"state": "red", "count": 2, "items": [{}, {}], "error": None},
    )
    assert merged["state"] == "red"
    assert merged["issues"][-1]["code"] == app.OBSERVER_RUNTIME_FAULT_CODE
    assert "2" in merged["issues"][-1]["message"]

    unavailable = app.merge_observer_fault_health(
        {"state": "green", "issues": []},
        {"state": "unavailable", "count": 0, "items": [], "error": "db down"},
    )
    assert unavailable["state"] == "red"
    assert unavailable["issues"][-1]["code"] == "OBSERVER_DURABLE_FAULT_READ_UNAVAILABLE"


def test_explicit_resolve_only_closes_open_observer_fault_and_records_operator() -> None:
    connection = ResolveConnection(resolved=True)

    assert app.resolve_observer_runtime_fault(
        connection,
        fault_id="observer-fault-1",
        reason="проверено владельцем",
        operator="alex",
        resolved_at=NOW,
    )

    statement, params = connection.calls[0]
    assert "state='RESOLVED'" in statement
    assert "fault_code=%s" in statement
    assert "state='OPEN'" in statement
    assert "last_resolution_reason" in statement
    assert "last_resolved_by" in statement
    assert params[1] == "проверено владельцем"
    assert params[2] == "alex"
    assert params[-2] == "observer-fault-1"
    assert params[-1] == app.OBSERVER_RUNTIME_FAULT_CODE


def test_explicit_resolve_requires_reason() -> None:
    with pytest.raises(ValueError, match="причина resolution обязательна"):
        app.resolve_observer_runtime_fault(
            ResolveConnection(),
            fault_id="observer-fault-1",
            reason="   ",
            operator="alex",
            resolved_at=NOW,
        )


def test_dashboard_global_red_banner_and_resolve_ui_are_non_trading() -> None:
    html = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    backend = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")

    assert html.index('id="observerFaultBanner"') < html.index('id="infra"')
    assert "RED · ошибка Universal Entry observer" in html
    assert "function renderObserverFaults" in html
    assert "async function resolveObserverFault" in html
    assert "Причина явного resolution durable observer fault" in html
    assert "renderObserverFaults(d.observer_faults)" in html
    assert "/api/observer-faults/resolve" in html
    resolve_scope = html[
        html.index("async function resolveObserverFault"):
        html.index("function renderObserverFaults")
    ]
    assert "/api/live/" not in resolve_scope
    assert "tradeCommand(" not in resolve_scope

    assert 'if path == "/api/observer-faults/resolve":' in backend
    endpoint_scope = backend[
        backend.index('if path == "/api/observer-faults/resolve":'):
        backend.index('if path == "/api/lifecycle/fault-delivery/ack":')
    ]
    assert "resolve_observer_runtime_fault(" in endpoint_scope
    assert "runtime.trade_commands" not in endpoint_scope
    assert "execution_permissions" not in endpoint_scope
