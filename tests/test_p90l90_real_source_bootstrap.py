"""No-order integration tests for real Observer live epoch bootstrap."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from operations.monitoring import p90l90_real_source_bootstrap as source


class FakeMirror:
    def __init__(self):
        self.generation = 1
        self.state = "ACTIVE"
        self.ready_at = datetime.now(UTC) - timedelta(seconds=2)

    def snapshot(self):
        return {
            "state": self.state,
            "epoch": self.generation,
            "epoch_started_at": self.ready_at.isoformat(),
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
    now = datetime.now(UTC)
    requests = []
    monkeypatch.setattr(
        source, "read_p90_real_account_routes",
        lambda *_args, **_kwargs: types.SimpleNamespace(),
    )
    monkeypatch.setattr(
        source, "read_p90_real_signed_flat_accounts",
        lambda *_args, **_kwargs: ("MAIN", "SUB"),
    )
    monkeypatch.setattr(
        source, "_wait_for_fresh_flat_generations",
        lambda *_args, **_kwargs: None,
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
        subscribed_local_at=now,
        subscribed_server_at=now,
        epoch_started_at=now - timedelta(seconds=3),
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
    monkeypatch.setattr(
        source, "_wait_for_fresh_flat_generations",
        lambda *_args, **_kwargs: None,
    )

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("private SUB missing")

    monkeypatch.setattr(source, "read_p90_real_signed_flat_accounts", unavailable)
    now = datetime.now(UTC)
    with pytest.raises(RuntimeError, match="private SUB missing"):
        source.start_current_p90_real_observer_epoch(
            db, bundles=(object(), object()), source_commit="b" * 40,
            mirror=mirror,
            subscribed_symbols=frozenset((
                "LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT",
            )),
            subscribed_intervals=frozenset(("5", "15")),
            subscribed_local_at=now,
            subscribed_server_at=now,
            epoch_started_at=now - timedelta(seconds=3),
        )
    assert db.trace == []



class _OneRow:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _GenerationDB:
    def __init__(self, *, current_time: datetime, fresh_after_sleep: bool):
        self.current_time = current_time
        self.fresh_after_sleep = fresh_after_sleep
        self.sleep_count = 0
        self.calls: list[tuple[str, str]] = []

    def execute(self, _statement, args):
        account, connector = args
        self.calls.append((account, connector))
        completed = (
            self.current_time - timedelta(seconds=1)
            if self.sleep_count and self.fresh_after_sleep
            else self.current_time - timedelta(seconds=9)
        )
        return _OneRow(
            (
                "generation-" + account,
                "COMPLETE",
                completed,
                0,
                0,
                "PROVEN",
            )
        )


def _routes():
    import types

    return types.SimpleNamespace(
        main=types.SimpleNamespace(
            trading_account_id="ta-main",
            account_connector_id="connector-main",
        ),
        sub=types.SimpleNamespace(
            trading_account_id="ta-sub",
            account_connector_id="connector-sub",
        ),
    )


def test_stale_generation_waits_for_next_fresh_complete_without_widening_ttl():
    now = datetime.now(UTC)
    db = _GenerationDB(current_time=now, fresh_after_sleep=True)

    def sleep(seconds: float) -> None:
        assert 0 < seconds <= source._ACCOUNT_GENERATION_BARRIER_POLL_SECONDS
        db.sleep_count += 1
        db.current_time += timedelta(seconds=seconds)

    source._wait_for_fresh_flat_generations(
        db,
        routes=_routes(),
        subscribed_local_at=now,
        clock=lambda: db.current_time,
        sleeper=sleep,
    )
    assert db.sleep_count == 1
    assert ("ta-main", "connector-main") in db.calls
    assert ("ta-sub", "connector-sub") in db.calls
    assert source._ACCOUNT_GENERATION_BARRIER_MAX_AGE_SECONDS < 15


def test_generation_wait_times_out_fail_closed_before_source_mutation():
    now = datetime.now(UTC)
    db = _GenerationDB(current_time=now, fresh_after_sleep=False)

    def sleep(seconds: float) -> None:
        db.sleep_count += 1
        db.current_time += timedelta(seconds=seconds)

    with pytest.raises(
        source.P90RealLiveSourceEpochUnverified,
        match="GENERATION_WAIT_TIMEOUT",
    ):
        source._wait_for_fresh_flat_generations(
            db,
            routes=_routes(),
            subscribed_local_at=now,
            clock=lambda: db.current_time,
            sleeper=sleep,
        )
    assert db.sleep_count > 0


def test_private_proof_missing_gets_one_bounded_retry(monkeypatch):
    import types

    now = datetime.now(UTC)
    mirror = FakeMirror()
    db = FakeConnection()
    routes = types.SimpleNamespace()
    calls = {"proof": 0, "barrier": 0}

    monkeypatch.setattr(
        source,
        "read_p90_real_account_routes",
        lambda *_args, **_kwargs: routes,
    )

    def barrier(*_args, **_kwargs):
        calls["barrier"] += 1

    monkeypatch.setattr(source, "_wait_for_fresh_flat_generations", barrier)

    def proof(_routes, **_kwargs):
        calls["proof"] += 1
        if calls["proof"] == 1:
            raise source.P90RealAccountRoutesUnverified(
                "P90_REAL_FLAT_ACCOUNT_PRIVATE_PROOF_MISSING"
            )
        return ("MAIN", "SUB")

    monkeypatch.setattr(source, "read_p90_real_signed_flat_accounts", proof)
    monkeypatch.setattr(source.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        source,
        "start_p90_real_source_epoch",
        lambda *_args, **_kwargs: "p90-real-epoch-" + "c" * 40,
    )
    result = source.start_current_p90_real_observer_epoch(
        db,
        bundles=(object(), object()),
        source_commit="b" * 40,
        mirror=mirror,
        subscribed_symbols=frozenset(
            ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")
        ),
        subscribed_intervals=frozenset(("5", "15")),
        subscribed_local_at=now,
        subscribed_server_at=now,
        epoch_started_at=now - timedelta(seconds=3),
    )
    assert result.startswith("p90-real-epoch-")
    assert calls == {"proof": 2, "barrier": 2}


def test_identity_scope_failure_is_never_retried(monkeypatch):
    import types

    now = datetime.now(UTC)
    mirror = FakeMirror()
    db = FakeConnection()
    monkeypatch.setattr(
        source,
        "read_p90_real_account_routes",
        lambda *_args, **_kwargs: types.SimpleNamespace(),
    )
    monkeypatch.setattr(
        source,
        "_wait_for_fresh_flat_generations",
        lambda *_args, **_kwargs: None,
    )
    calls = 0

    def proof(_routes, **_kwargs):
        nonlocal calls
        calls += 1
        raise source.P90RealAccountRoutesUnverified(
            "P90_REAL_FLAT_ACCOUNT_SIGNED_SCOPE_MISMATCH"
        )

    monkeypatch.setattr(source, "read_p90_real_signed_flat_accounts", proof)
    with pytest.raises(
        source.P90RealAccountRoutesUnverified,
        match="SIGNED_SCOPE_MISMATCH",
    ):
        source.start_current_p90_real_observer_epoch(
            db,
            bundles=(object(), object()),
            source_commit="b" * 40,
            mirror=mirror,
            subscribed_symbols=frozenset(
                ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")
            ),
            subscribed_intervals=frozenset(("5", "15")),
            subscribed_local_at=now,
            subscribed_server_at=now,
            epoch_started_at=now - timedelta(seconds=3),
        )
    assert calls == 1


def test_pre_ws_private_preflight_allows_valid_ten_second_proof(monkeypatch):
    import types

    now = datetime.now(UTC)
    current = [now]
    monkeypatch.setattr(
        source,
        "_wait_for_fresh_flat_generations",
        lambda *_args, **_kwargs: None,
    )
    observed_timeouts: list[float] = []

    def clock():
        return current[0]

    def valid_long_proof(_routes, *, timeout_seconds: float):
        observed_timeouts.append(timeout_seconds)
        current[0] += timedelta(seconds=10.5)
        return object(), object()

    result = source._read_signed_accounts_bounded(
        object(),
        routes=types.SimpleNamespace(),
        started_at=now,
        clock=clock,
        sleeper=lambda _seconds: None,
        reader=valid_long_proof,
        retry_budget_seconds=source._PRIVATE_PREFLIGHT_RETRY_BUDGET_SECONDS,
        attempt_seconds=source._PRIVATE_PREFLIGHT_ATTEMPT_SECONDS,
        generation_wait_seconds=source._PRIVATE_PREFLIGHT_GENERATION_WAIT_SECONDS,
        generation_max_age_seconds=(
            source._PRIVATE_PREFLIGHT_GENERATION_MAX_AGE_SECONDS
        ),
    )

    assert len(result) == 2
    assert observed_timeouts == [source.P90_ACCOUNT_PROOF_TIMEOUT_SECONDS]
    assert source._PRIVATE_PROOF_RETRY_BUDGET_SECONDS == 8.0
    assert source._PRIVATE_PROOF_ATTEMPT_SECONDS == 2.0
    assert (current[0] - now).total_seconds() == 10.5


def test_private_proof_retry_budget_expires_fail_closed(monkeypatch):
    import types

    now = datetime.now(UTC)
    current = [now]
    calls: list[float] = []
    monkeypatch.setattr(
        source,
        "_wait_for_fresh_flat_generations",
        lambda *_args, **_kwargs: None,
    )

    def clock():
        return current[0]

    def sleeper(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    def unavailable(_routes, *, timeout_seconds: float):
        calls.append(timeout_seconds)
        current[0] += timedelta(seconds=timeout_seconds)
        raise source.P90RealAccountRoutesUnverified(
            "P90_REAL_FLAT_ACCOUNT_PRIVATE_PROOF_MISSING"
        )

    with pytest.raises(
        source.P90RealLiveSourceEpochUnverified,
        match="PRIVATE_PROOF_RETRY_BUDGET_EXPIRED",
    ):
        source._read_signed_accounts_bounded(
            object(),
            routes=types.SimpleNamespace(),
            started_at=now,
            clock=clock,
            sleeper=sleeper,
            reader=unavailable,
        )
    assert calls
    assert all(
        0 < timeout <= source._PRIVATE_PROOF_ATTEMPT_SECONDS
        for timeout in calls
    )
    assert (
        current[0] - now
    ).total_seconds() <= source._PRIVATE_PROOF_RETRY_BUDGET_SECONDS + 0.001
