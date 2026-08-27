from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.position_supervisor import (
    FeatureEvidence,
    PositionEvent,
    PositionIdentity,
    PositionSupervisor,
    SupervisorState,
)
from bybit_workbench.position_supervisor.models import Quality

T0 = datetime(2026, 8, 27, tzinfo=UTC)


def identity(side: str = "Buy") -> PositionIdentity:
    return PositionIdentity("p1", "XRPUSDT", side, Decimal("100"), Decimal("1"), T0)


def f(state: str, quality: Quality = Quality.FRESH) -> FeatureEvidence:
    return FeatureEvidence(state, T0 + timedelta(minutes=1), quality)


def complete(**overrides: str) -> dict[str, FeatureEvidence]:
    raw = {
        "structure": "hold",
        "price_1m": "neutral",
        "flow": "neutral",
        "absorption": "none",
        "orderbook": "balanced",
        "oi_price": "neutral",
    }
    raw.update(overrides)
    return {key: f(value) for key, value in raw.items()}


def test_path_uses_actual_fill_and_mirrors_long_short() -> None:
    long = PositionSupervisor(identity("Buy"))
    long.update(PositionEvent(T0 + timedelta(minutes=1), Decimal("102"), complete()))
    b = long.update(PositionEvent(T0 + timedelta(minutes=2), Decimal("99"), complete()))
    assert (b.mfe_pct, b.mae_pct, b.giveback_pct) == (
        Decimal("2.00"),
        Decimal("-1.00"),
        Decimal("3.00"),
    )
    short = PositionSupervisor(identity("Sell"))
    s = short.update(PositionEvent(T0 + timedelta(minutes=1), Decimal("98"), complete()))
    assert s.price_move_pct == Decimal("2.00")


def test_missing_and_stale_are_never_neutral() -> None:
    engine = PositionSupervisor(identity())
    assert (
        engine.update(PositionEvent(T0 + timedelta(seconds=1), Decimal("100"), {})).state
        == SupervisorState.WARMUP
    )
    data = complete()
    data["orderbook"] = f("balanced", Quality.STALE)
    assert (
        engine.update(PositionEvent(T0 + timedelta(seconds=2), Decimal("100"), data)).state
        == SupervisorState.BLOCKED
    )


def test_broken_requires_independent_adverse_stack() -> None:
    engine = PositionSupervisor(identity())
    weak = complete(structure="broken")
    assert (
        engine.update(PositionEvent(T0 + timedelta(minutes=1), Decimal("99.4"), weak)).state
        != SupervisorState.BROKEN
    )
    broken = complete(
        structure="broken",
        price_1m="failed_reclaim",
        flow="persistent_adverse",
        absorption="against",
        orderbook="withdrawal",
        oi_price="against",
    )
    snap = engine.update(PositionEvent(T0 + timedelta(minutes=2), Decimal("99.3"), broken))
    assert snap.state == SupervisorState.BROKEN
    assert snap.shadow_action == "КАНДИДАТ НА ВЫХОД"


def test_recovery_and_runner_are_causal() -> None:
    engine = PositionSupervisor(identity())
    recovery = complete(
        structure="reclaim",
        price_1m="recovery",
        flow="favorable_recovery",
        absorption="absorption",
        orderbook="replenishment",
    )
    assert (
        engine.update(PositionEvent(T0 + timedelta(minutes=1), Decimal("99.5"), recovery)).state
        == SupervisorState.RECOVERY
    )
    runner = complete(
        structure="with", price_1m="continuation", flow="favorable", orderbook="replenishment"
    )
    assert (
        engine.update(PositionEvent(T0 + timedelta(minutes=2), Decimal("101.2"), runner)).state
        == SupervisorState.RUNNER
    )


def test_replay_and_live_sequences_are_equivalent() -> None:
    events = [
        PositionEvent(T0 + timedelta(minutes=i), Decimal(str(price)), complete())
        for i, price in [(1, 99.8), (2, 100.4), (3, 101.0)]
    ]
    live, replay = PositionSupervisor(identity()), PositionSupervisor(identity())
    assert [live.update(x).audit_dict() for x in events] == [
        replay.update(x).audit_dict() for x in events
    ]


def test_out_of_order_events_fail_closed() -> None:
    engine = PositionSupervisor(identity())
    engine.update(PositionEvent(T0 + timedelta(minutes=2), Decimal("100"), complete()))
    with pytest.raises(ValueError, match="out-of-order"):
        engine.update(PositionEvent(T0 + timedelta(minutes=1), Decimal("100"), complete()))


def test_shadow_engine_has_no_mutation_methods() -> None:
    forbidden = {"place_order", "close", "set_stop", "set_take_profit", "set_leverage"}
    assert forbidden.isdisjoint(dir(PositionSupervisor))
