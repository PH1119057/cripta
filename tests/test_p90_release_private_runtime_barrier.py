from datetime import UTC, datetime, timedelta

import pytest

from operations.implementation import wait_p90_private_runtime_ready as barrier


class _Rows:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _DB:
    def __init__(self, row):
        self.row = row

    def execute(self, _sql, _args=()):
        return _Rows(self.row)


def test_generation_must_be_post_start_fresh_proven_and_order_free():
    now = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
    not_before = now - timedelta(seconds=5)
    good = (
        "gen-1",
        "COMPLETE",
        now - timedelta(seconds=2),
        0,
        0,
        "PROVEN",
    )
    assert barrier._generation_ready(
        _DB(good),
        account_id="ta-" + "1" * 32,
        connector_id="ac-main",
        not_before=not_before,
        now=now,
    )
    stale_prestart = (
        "gen-old",
        "COMPLETE",
        not_before - timedelta(seconds=1),
        0,
        0,
        "PROVEN",
    )
    assert not barrier._generation_ready(
        _DB(stale_prestart),
        account_id="ta-" + "1" * 32,
        connector_id="ac-main",
        not_before=not_before,
        now=now,
    )
    nonflat = ("gen-open", "COMPLETE", now, 1, 0, "PROVEN")
    assert barrier._generation_ready(
        _DB(nonflat),
        account_id="ta-" + "1" * 32,
        connector_id="ac-main",
        not_before=not_before,
        now=now,
    )


def test_invalid_not_before_fails_closed(monkeypatch):
    monkeypatch.delenv(
        "CRIPTA_RELEASE_PRIVATE_RUNTIME_NOT_BEFORE",
        raising=False,
    )
    with pytest.raises(
        barrier.P90ReleasePrivateRuntimeNotReady,
        match="P90_RELEASE_PRIVATE_NOT_BEFORE_MISSING",
    ):
        barrier._not_before_from_env()


def test_barrier_constants_are_bounded():
    assert barrier.WAIT_SECONDS == 120.0
    assert barrier.POLL_SECONDS == 0.5
    assert barrier.MAX_GENERATION_AGE_SECONDS == 15.0
    assert barrier.IPC_ATTEMPT_SECONDS == 8.0
