"""#470 R11 Stage 4: bounded publication backlog/restart semantics."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from bybit_workbench.market_identity import bybit_instrument_id

import operations.monitoring.r11_mayak_pg_observation as observation
from bybit_workbench.mayak.seven_publication_observation_candidate import (
    R11SevenMayakReplayCheckpoint,
)
from bybit_workbench.mayak.seven_publication_replay_runtime import (
    R11MayakPublicationReplay,
)

SESSION = "a" * 32
RELEASE = "b" * 40
AT = datetime(2026, 11, 15, 20, 0, tzinfo=UTC)


class Result:
    def __init__(
        self,
        one: tuple[object, ...] | None = None,
        many: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.one = one
        self.many = [] if many is None else many

    def fetchone(self) -> tuple[object, ...] | None:
        return self.one

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.many


class DB:
    def __init__(
        self,
        *,
        backlog: list[tuple[object, ...]],
        latest: tuple[object, ...],
        bootstrap: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.backlog = backlog
        self.latest = latest
        self.bootstrap = [] if bootstrap is None else bootstrap

    def execute(self, sql: str, _params: object = None) -> Result:
        if sql == "SHOW transaction_isolation":
            return Result(("repeatable read",))
        if sql == "SHOW transaction_read_only":
            return Result(("on",))
        if "rest_field_coverage" in sql:
            return Result(many=self.bootstrap)
        if "ORDER BY p.as_of_second,p.publication_id" in sql:
            return Result(many=self.backlog)
        if "ORDER BY p.as_of_second DESC,p.publication_id DESC" in sql:
            return Result(self.latest)
        raise AssertionError(sql)


def complete_bootstrap_row(
    publication_id: int, at: datetime,
) -> tuple[object, ...]:
    rest: dict[str, dict[str, dict[str, str]]] = {}
    for symbol in observation._SEVEN:
        rest[bybit_instrument_id("spot", symbol)] = {
            "INSTRUMENT_INFO": {"status": "OBSERVED"},
        }
        rest[bybit_instrument_id("linear", symbol)] = {
            "INSTRUMENT_INFO": {"status": "OBSERVED"},
            "ACCOUNT_RATIO": {"status": "OBSERVED"},
        }
    refs = {"SPOT": {"source_frame_number": 1},
            "LINEAR": {"source_frame_number": 2}}
    coverage = {"SPOT": "PHYSICAL_FRAME_REF_ONLY",
                "LINEAR": "PHYSICAL_FRAME_REF_ONLY"}
    return publication_id, at, rest, refs, coverage


def replay_at(
    publication_id: int = 100,
    *,
    at: datetime = AT,
) -> R11MayakPublicationReplay:
    checkpoint = R11SevenMayakReplayCheckpoint(
        publication_id=publication_id,
        source_session_id=SESSION,
        release_commit=RELEASE,
        as_of_second=at,
        optional_cursors=(),
        native_cursors=(),
    )
    return R11MayakPublicationReplay(checkpoint=checkpoint)


def test_backlog_reads_exact_next_publication_not_latest() -> None:
    db = DB(
        backlog=[
            (101, AT + timedelta(seconds=1)),
            (102, AT + timedelta(seconds=2)),
            (103, AT + timedelta(seconds=3)),
        ],
        latest=(103, AT + timedelta(seconds=3), SESSION, RELEASE),
    )
    target = observation._next_publication(
        db, replay=replay_at(), now=AT + timedelta(seconds=10),
    )
    assert target == (101, AT + timedelta(seconds=1))


def test_observed_65_publication_restart_lag_is_replayable_in_order() -> None:
    rows = [
        (101 + index, AT + timedelta(seconds=index + 1))
        for index in range(65)
    ]
    db = DB(
        backlog=rows,
        latest=(rows[-1][0], rows[-1][1], SESSION, RELEASE),
    )
    assert observation._MAX_BACKLOG_PUBLICATIONS == 600
    assert observation._next_publication(
        db, replay=replay_at(), now=AT + timedelta(minutes=2),
    ) == rows[0]


def test_backlog_ceiling_fails_closed_before_skipping() -> None:
    rows = [
        (101 + index, AT + timedelta(seconds=index + 1))
        for index in range(observation._MAX_BACKLOG_PUBLICATIONS + 1)
    ]
    db = DB(
        backlog=rows,
        latest=(rows[-1][0], rows[-1][1], SESSION, RELEASE),
    )
    with pytest.raises(
        observation.R11MayakPGSourceUnavailable,
        match="PUBLICATION_BACKLOG_EXCEEDS_BOUND",
    ):
        observation._next_publication(
            db, replay=replay_at(), now=AT + timedelta(minutes=2),
        )


def test_head_is_checked_for_wall_clock_staleness_only_after_backlog() -> None:
    db = DB(
        backlog=[],
        latest=(100, AT, SESSION, RELEASE),
    )
    with pytest.raises(
        observation.R11MayakPGSourceUnavailable,
        match="PUBLICATION_STREAM_STALE",
    ):
        observation._next_publication(
            db,
            replay=replay_at(),
            now=AT + timedelta(seconds=31),
        )


def test_bootstrap_uses_first_complete_publication_while_head_is_fresh() -> None:
    incomplete = list(complete_bootstrap_row(6, AT - timedelta(seconds=1)))
    incomplete[2] = {}
    db = DB(
        backlog=[],
        latest=(9, AT + timedelta(seconds=2), SESSION, RELEASE),
        bootstrap=[tuple(incomplete), complete_bootstrap_row(7, AT),
                   complete_bootstrap_row(8, AT + timedelta(seconds=1))],
    )
    replay = R11MayakPublicationReplay()
    assert observation._next_publication(
        db, replay=replay, now=AT + timedelta(seconds=3),
    ) == (7, AT)


def test_bootstrap_waits_until_full_rest_and_optional_scope_exists() -> None:
    incomplete = list(complete_bootstrap_row(7, AT))
    incomplete[4] = {"SPOT": "PHYSICAL_FRAME_REF_ONLY"}
    db = DB(
        backlog=[], latest=(7, AT, SESSION, RELEASE),
        bootstrap=[tuple(incomplete)],
    )
    with pytest.raises(
        observation.R11MayakPGSourceUnavailable,
        match="BOOTSTRAP_PUBLICATION_NOT_READY",
    ):
        observation._next_publication(
            db, replay=R11MayakPublicationReplay(),
            now=AT + timedelta(seconds=1),
        )


def test_bootstrap_still_requires_fresh_current_head() -> None:
    db = DB(
        backlog=[], latest=(7, AT, SESSION, RELEASE),
        bootstrap=[complete_bootstrap_row(7, AT)],
    )
    with pytest.raises(
        observation.R11MayakPGSourceUnavailable,
        match="PUBLICATION_STREAM_STALE",
    ):
        observation._next_publication(
            db, replay=R11MayakPublicationReplay(),
            now=AT + timedelta(seconds=31),
        )


def test_backlog_replay_uses_publication_causal_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_at = AT + timedelta(seconds=1)
    db = DB(
        backlog=[(101, target_at)],
        latest=(101, target_at, SESSION, RELEASE),
    )
    observed: list[datetime] = []
    applied: list[datetime] = []
    carried = {("LINEAR", "TICKER", "BTCUSDT"): AT}
    received_starts: list[object] = []

    candidate = SimpleNamespace(as_of_second=target_at)

    def collect(*_args: object, **kwargs: object) -> object:
        observed.append(cast(datetime, kwargs["observed_at"]))
        received_starts.append(kwargs["previous_continuity_starts"])
        return candidate

    class FakeReplay:
        checkpoint = replay_at().checkpoint
        continuity_starts = carried

        def apply(self, _candidate: object, *, now: datetime) -> dict[str, Any]:
            applied.append(now)
            return {
                "r11_market_publication": {
                    "consumer_ready": False,
                    "mayak_ws_retirement_allowed": False,
                }
            }

    monkeypatch.setattr(
        observation, "collect_r11_seven_mayak_candidate", collect,
    )
    wall_now = AT + timedelta(seconds=20)
    snapshot = observation._read_one(
        db, replay=cast(Any, FakeReplay()), now=wall_now,
    )
    assert snapshot is not None
    assert observed == [target_at]
    assert received_starts == [carried]
    assert applied == [target_at]


def test_durable_cursor_at_head_returns_none_when_stream_is_fresh() -> None:
    db = DB(
        backlog=[],
        latest=(100, AT, SESSION, RELEASE),
    )
    assert observation._next_publication(
        db,
        replay=replay_at(),
        now=AT + timedelta(seconds=20),
    ) is None


def test_source_epoch_change_is_not_misread_as_empty_backlog() -> None:
    db = DB(
        backlog=[],
        latest=(200, AT + timedelta(seconds=1), "c" * 32, RELEASE),
    )
    with pytest.raises(
        observation.R11MayakPGSourceUnavailable,
        match="SOURCE_EPOCH_CHANGED",
    ):
        observation._next_publication(
            db, replay=replay_at(), now=AT + timedelta(seconds=2),
        )
