"""P90 first-fill proof preparation: bounded, typed and never an Exit waiver.

No PostgreSQL, Bybit credentials, socket or trade mutation in these tests.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_exit.p90l90_real_signed_position import (
    P90RealSignedPositionUnverified,
)
from operations.connectivity.private_runtime import (
    _p90_bounded_first_fill_signed_proof,
)

AT = datetime(2026, 11, 9, 4, 7, 38, tzinfo=UTC)


def _position(*, age_seconds: int = 8, protected: bool = True) -> StrategyPosition:
    filled = AT - timedelta(seconds=age_seconds)
    return cast(StrategyPosition, SimpleNamespace(
        fill_at=filled,
        initial_protection_confirmed_at=(
            filled + timedelta(seconds=3) if protected else None
        ),
    ))


def _clock():
    time = [0.0]
    sleeps: list[float] = []

    def monotonic() -> float:
        return time[0]

    def sleeper(seconds: float) -> None:
        assert 0 < seconds <= 0.2 + 1e-9
        sleeps.append(seconds)
        time[0] += seconds

    return time, sleeps, monotonic, sleeper


def test_new_complete_generation_within_bound_is_read_afresh_and_proven() -> None:
    elapsed, sleeps, monotonic, sleeper = _clock()
    calls: list[int] = []

    def signed():
        calls.append(len(calls))
        # Simulate FAILED/in-flight first account state, then a new COMPLETE
        # generation available 2.4s later. Only signed proof can accept it.
        if elapsed[0] < 2.4:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_GENERATION_UNAVAILABLE"
            )
        return object()

    evidence = _p90_bounded_first_fill_signed_proof(
        signed,
        position=_position(),
        event_kind="TRADE",
        utc_now=lambda: AT,
        monotonic=monotonic,
        sleeper=sleeper,
    )
    assert evidence is not None
    assert len(calls) > 1
    assert 2.4 <= elapsed[0] <= 3.0
    assert sleeps


def test_generation_never_completes_is_still_denied_with_finite_budget() -> None:
    elapsed, sleeps, monotonic, sleeper = _clock()
    calls = []

    def unavailable():
        calls.append(1)
        raise P90RealSignedPositionUnverified(
            "P90_REAL_SIGNED_GENERATION_UNAVAILABLE"
        )

    with pytest.raises(
        P90RealSignedPositionUnverified,
        match="P90_REAL_SIGNED_GENERATION_UNAVAILABLE",
    ):
        _p90_bounded_first_fill_signed_proof(
            unavailable, position=_position(), event_kind="TRADE",
            utc_now=lambda: AT, monotonic=monotonic, sleeper=sleeper,
        )
    assert len(calls) >= 2
    assert 3.0 <= elapsed[0] <= 3.0 + 1e-9
    assert sum(sleeps) <= 3.0 + 1e-9


@pytest.mark.parametrize("hard_error", [
    "P90_REAL_SIGNED_GENERATION_STALE_OR_WRONG_ACCOUNT",
    "P90_REAL_PRIVATE_WS_CONTINUITY_UNVERIFIED",
    "P90_REAL_SIGNED_POSITION_FILL_OR_SIDE_MISMATCH",
    "P90_REAL_SIGNED_CONNECTOR_UID_UNVERIFIED",
    "P90_REAL_PENDING_EXIT_COMMAND_UNVERIFIED",
])
def test_non_generation_proof_failure_never_retries(hard_error: str) -> None:
    elapsed, sleeps, monotonic, sleeper = _clock()
    calls = []

    def denied():
        calls.append(1)
        raise P90RealSignedPositionUnverified(hard_error)

    with pytest.raises(P90RealSignedPositionUnverified, match=hard_error):
        _p90_bounded_first_fill_signed_proof(
            denied, position=_position(), event_kind="TRADE",
            utc_now=lambda: AT, monotonic=monotonic, sleeper=sleeper,
        )
    assert calls == [1]
    assert elapsed == [0.0]
    assert sleeps == []


@pytest.mark.parametrize(("event_kind", "age", "protected"), [
    ("CLOSED", 8, True),
    ("TRADE", 30, True),
    ("TRADE", 8, False),
])
def test_not_fresh_protected_first_native_fill_never_retries(
    event_kind: str, age: int, protected: bool,
) -> None:
    elapsed, sleeps, monotonic, sleeper = _clock()
    calls = []

    def unavailable():
        calls.append(1)
        raise P90RealSignedPositionUnverified(
            "P90_REAL_SIGNED_GENERATION_UNAVAILABLE"
        )

    with pytest.raises(P90RealSignedPositionUnverified):
        _p90_bounded_first_fill_signed_proof(
            unavailable, position=_position(age_seconds=age, protected=protected),
            event_kind=event_kind, utc_now=lambda: AT,
            monotonic=monotonic, sleeper=sleeper,
        )
    assert calls == [1]
    assert elapsed == [0.0]
    assert sleeps == []


def test_unexpected_private_exception_is_never_retried_or_unwrapped() -> None:
    elapsed, sleeps, monotonic, sleeper = _clock()
    calls = []

    def unknown():
        calls.append(1)
        raise RuntimeError("private details remain in producer scope")

    with pytest.raises(RuntimeError, match="private details remain"):
        _p90_bounded_first_fill_signed_proof(
            unknown, position=_position(), event_kind="TRADE",
            utc_now=lambda: AT, monotonic=monotonic, sleeper=sleeper,
        )
    assert calls == [1]
    assert elapsed == [0.0]
    assert sleeps == []


def test_already_valid_first_fill_proof_is_not_delayed_or_duplicated() -> None:
    elapsed, sleeps, monotonic, sleeper = _clock()
    received: list[object] = []
    original = object()

    def valid():
        received.append(original)
        return original

    result = _p90_bounded_first_fill_signed_proof(
        valid, position=_position(), event_kind="TRADE",
        utc_now=lambda: AT, monotonic=monotonic, sleeper=sleeper,
    )
    assert result is original
    assert received == [original]
    assert elapsed == [0.0]
    assert sleeps == []
