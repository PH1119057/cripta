"""No-order integration tests for real Observer live epoch bootstrap."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from operations.monitoring import p90l90_real_source_bootstrap as source

AT = datetime.now(UTC)


class FakeMirror:
    def __init__(self):
        self.generation = 1
        self.state = "ACTIVE"

    def snapshot(self):
        return {
            "state": self.state,
            "epoch": self.generation,
            "epoch_started_at": (AT - timedelta(seconds=2)).isoformat(),
            "gaps": 0,
        }


class FakeConnection:
    def __init__(self):
        self.trace = []

    def transaction(self):
        connection = self

        class Tx:
            def __enter__(self):
                connection.trace.append("BEGIN")
                return None

            def __exit__(self, error, *_):
                connection.trace.append("ROLLBACK" if error else "COMMIT")
                return False

        return Tx()

    def execute(self, statement):
        self.trace.append(statement)


def _invoke(monkeypatch, *, mirror=None, db=None, source_func=None):
    import types

    mirror = mirror or FakeMirror()
    db = db or FakeConnection()
    requests = []
    monkeypatch.setattr(
        source, "read_p90_real_account_routes",
        lambda *_args, **_kwargs: types.SimpleNamespace(),
    )
    monkeypatch.setattr(
        source, "read_p90_real_signed_flat_accounts",
        lambda *_args, **_kwargs: ("MAIN", "SUB"),
    )

    def live_start(_conn, **kwargs):
        requests.append(kwargs)
        if source_func:
            return source_func(mirror)
        return "p90-real-epoch-" + "a" * 40

    monkeypatch.setattr(source, "start_p90_real_source_epoch", live_start)
    result = source.start_current_p90_real_observer_epoch(
        db, bundles=(object(), object()), source_commit="b" * 40,
        mirror=mirror,
        subscribed_symbols=frozenset((
            "LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT",
        )),
        subscribed_intervals=frozenset(("5", "15", "60")),
        subscribed_local_at=AT,
        subscribed_server_at=AT,
        epoch_started_at=AT - timedelta(seconds=3),
    )
    return result, db, requests


def test_actual_live_source_epoch_calls_two_private_producers_and_serializable(
    monkeypatch,
):
    epoch, db, calls = _invoke(monkeypatch)
    assert epoch.startswith("p90-real-epoch-")
    assert db.trace == [
        "BEGIN", "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE", "COMMIT",
    ]
    assert len(calls) == 1
    assert calls[0]["signed_accounts"] == ("MAIN", "SUB")
    assert calls[0]["retire_previous"] is True
    assert calls[0]["independently_checked_runtime_commands"] is (
        source.read_p90_real_no_pending_runtime_commands
    )


def test_mirror_restart_inside_source_transaction_rolls_back(monkeypatch):
    mirror = FakeMirror()

    def source_func(current_mirror):
        current_mirror.generation += 1
        return "p90-real-epoch-" + "b" * 40

    db = FakeConnection()
    with pytest.raises(source.P90RealLiveSourceEpochUnverified,
                       match="MIRROR_RESTART"):
        _invoke(monkeypatch, mirror=mirror, db=db, source_func=source_func)
    assert db.trace[-1] == "ROLLBACK"


def test_missing_mirror_or_main_sub_fails_before_mutating(monkeypatch):
    mirror = FakeMirror()
    mirror.state = "GAP"
    db = FakeConnection()
    with pytest.raises(source.P90RealLiveSourceEpochUnverified,
                       match="MIRROR_EPOCH"):
        _invoke(monkeypatch, mirror=mirror, db=db)
    assert db.trace == []


def test_missing_private_account_proof_uses_no_source_transaction(monkeypatch):
    mirror = FakeMirror()
    db = FakeConnection()
    monkeypatch.setattr(source, "read_p90_real_account_routes",
                        lambda *_args, **_kwargs: object())

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("private SUB missing")

    monkeypatch.setattr(source, "read_p90_real_signed_flat_accounts", unavailable)
    with pytest.raises(RuntimeError, match="private SUB missing"):
        source.start_current_p90_real_observer_epoch(
            db, bundles=(object(), object()), source_commit="b" * 40,
            mirror=mirror,
            subscribed_symbols=frozenset((
                "LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT",
            )),
            subscribed_intervals=frozenset(("5", "15")),
            subscribed_local_at=AT,
            subscribed_server_at=AT,
            epoch_started_at=AT - timedelta(seconds=3),
        )
    assert db.trace == []
