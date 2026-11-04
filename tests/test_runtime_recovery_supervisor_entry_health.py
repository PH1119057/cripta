"""Read-only MAIN/SUB Entry consumer heartbeat adapter regression tests."""

from __future__ import annotations

import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

PROBE = runpy.run_path(
    "operations/monitoring/runtime_recovery_supervisor.py",
    run_name="recovery_heartbeat_test",
)
HEALTH = PROBE["_entry_consumer_health"]
ROUTES = PROBE["ENTRY_CONSUMER_ROUTES"]
RELEASE = "a" * 40
NOW = datetime(2026, 11, 4, 0, 20, tzinfo=UTC)


def ready_rows() -> list[tuple[object, ...]]:
    return [
        (account, connector, RELEASE, "READY", NOW - timedelta(seconds=2))
        for account, connector in ROUTES.values()
    ]


class ReadOnlyConnection:
    def __init__(self, rows: list[tuple[object, ...]]):
        self.rows = rows
        self.queries: list[tuple[str, object]] = []

    def __enter__(self) -> ReadOnlyConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def execute(
        self, sql: str, params: object = None
    ) -> ReadOnlyConnection:
        self.queries.append((sql, params))
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


def install_rows(
    monkeypatch: pytest.MonkeyPatch, rows: list[tuple[object, ...]]
) -> ReadOnlyConnection:
    connection = ReadOnlyConnection(rows)
    monkeypatch.setattr(
        psycopg,
        "connect",
        lambda *args, **kwargs: connection,
    )
    return connection


def test_both_consumer_heartbeats_ready_exact_scope_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = install_rows(monkeypatch, ready_rows())
    result = HEALTH(release_commit=RELEASE, now=NOW)
    assert result == dict.fromkeys(ROUTES, True)
    assert len(connection.queries) == 2
    assert connection.queries[0][0] == "SET TRANSACTION READ ONLY"
    assert connection.queries[1][0].lstrip().startswith("SELECT ")
    assert "runtime.entry_consumer_health" in connection.queries[1][0]
    assert sorted(connection.queries[1][1][0]) == sorted(
        account for account, _ in ROUTES.values()
    )
    assert not any(
        token in " ".join(sql.upper() for sql, _ in connection.queries)
        for token in ("INSERT INTO", "UPDATE ", "DELETE FROM", "FOR UPDATE")
    )


@pytest.mark.parametrize("age", [9, 30, -1])
def test_stale_or_future_timestamp_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, age: int
) -> None:
    rows = ready_rows()
    rows[0] = (*rows[0][:4], NOW - timedelta(seconds=age))
    install_rows(monkeypatch, rows)
    result = HEALTH(release_commit=RELEASE, now=NOW)
    assert result[list(ROUTES)[0]] is False
    assert result[list(ROUTES)[1]] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (1, "ac-not-registered"),
        (2, "b" * 40),
        (3, "BLOCKED"),
    ],
)
def test_wrong_connector_release_or_state_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, field: int, value: str
) -> None:
    rows = ready_rows()
    entry = list(rows[0])
    entry[field] = value
    rows[0] = tuple(entry)
    install_rows(monkeypatch, rows)
    result = HEALTH(release_commit=RELEASE, now=NOW)
    assert result[list(ROUTES)[0]] is False
    assert result[list(ROUTES)[1]] is True


def test_missing_or_duplicate_account_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = ready_rows()
    install_rows(monkeypatch, rows[1:])
    assert HEALTH(release_commit=RELEASE, now=NOW)[list(ROUTES)[0]] is None
    install_rows(monkeypatch, rows + [rows[0]])
    assert HEALTH(release_commit=RELEASE, now=NOW)[list(ROUTES)[0]] is None


def test_naive_or_non_datetime_observation_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = ready_rows()
    rows[0] = (*rows[0][:4], NOW.replace(tzinfo=None))
    install_rows(monkeypatch, rows)
    assert HEALTH(release_commit=RELEASE, now=NOW)[list(ROUTES)[0]] is None


def test_bad_db_or_release_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise psycopg.OperationalError("database unavailable")

    monkeypatch.setattr(psycopg, "connect", unavailable)
    assert HEALTH(release_commit=RELEASE, now=NOW) == dict.fromkeys(ROUTES)
    assert HEALTH(release_commit="", now=NOW) == dict.fromkeys(ROUTES)


def test_observer_does_not_read_owner_arm_or_dispatch_broker() -> None:
    source = Path(
        "operations/monitoring/runtime_recovery_supervisor.py"
    ).read_text(encoding="utf-8")
    assert "SET TRANSACTION READ ONLY" in source
    assert "execution_enabled=False" in source
    assert "request_recovery(" not in source
    assert "control.live_arm_sessions" not in source
    assert "control.execution_gates" not in source
