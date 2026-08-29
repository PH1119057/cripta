from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.position_supervisor import (
    ExchangePosition,
    FeatureEvidence,
    OrderedEventAdapter,
    PositionEvent,
    PositionIdentity,
    PositionSupervisor,
    SupervisorEventEnvelope,
    SupervisorRegistry,
    SupervisorState,
    process_events,
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


def test_confidence_is_bounded_by_mandatory_coverage() -> None:
    engine = PositionSupervisor(identity())
    snapshot = engine.update(
        PositionEvent(T0 + timedelta(minutes=1), Decimal("100"), complete())
    )
    assert snapshot.confidence == Decimal("1")


def test_unknown_optional_structure_is_reported_as_unknown() -> None:
    engine = PositionSupervisor(identity())
    evidence = complete()
    evidence["structure"] = f("unknown", Quality.MISSING)
    snapshot = engine.update(
        PositionEvent(T0 + timedelta(minutes=1), Decimal("100"), evidence)
    )
    assert snapshot.state == SupervisorState.HEALTHY
    assert "структурный слой недоступен" in snapshot.reason
    assert "структура позиции сохраняется" not in snapshot.reason


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
    envelopes = [SupervisorEventEnvelope(i, event) for i, event in enumerate(events, 41)]
    live, replay = PositionSupervisor(identity()), PositionSupervisor(identity())
    live_result = process_events(live, OrderedEventAdapter(envelopes))
    replay_result = process_events(replay, OrderedEventAdapter(list(envelopes)))
    assert [x.audit_dict() for x in live_result] == [x.audit_dict() for x in replay_result]


def test_adapter_rejects_missing_or_duplicate_event_sequence() -> None:
    event = PositionEvent(T0 + timedelta(minutes=1), Decimal("100"), complete())
    broken = [SupervisorEventEnvelope(5, event), SupervisorEventEnvelope(7, event)]
    with pytest.raises(ValueError, match="sequence violation"):
        list(OrderedEventAdapter(broken))


def test_out_of_order_events_fail_closed() -> None:
    engine = PositionSupervisor(identity())
    engine.update(PositionEvent(T0 + timedelta(minutes=2), Decimal("100"), complete()))
    with pytest.raises(ValueError, match="out-of-order"):
        engine.update(PositionEvent(T0 + timedelta(minutes=1), Decimal("100"), complete()))


def test_shadow_engine_has_no_mutation_methods() -> None:
    forbidden = {"place_order", "close", "set_stop", "set_take_profit", "set_leverage"}
    assert forbidden.isdisjoint(dir(PositionSupervisor))


def exchange(position_id: str = "p1", qty: str = "1") -> ExchangePosition:
    return ExchangePosition(
        position_id,
        "XRPUSDT",
        "Buy",
        Decimal("100"),
        Decimal(qty),
        T0,
        Decimal("10"),
        Decimal("100.1"),
    )


def test_registry_exchange_truth_creates_recovers_and_removes() -> None:
    registry = SupervisorRegistry()
    created, removed = registry.reconcile([exchange()])
    assert created == {"p1"} and not removed
    assert registry.ids() == {"p1"}
    created, removed = registry.reconcile([exchange()])
    assert not created and not removed
    created, removed = registry.reconcile([])
    assert not created and removed == {"p1"}


def test_registry_replaces_context_when_exchange_average_changes() -> None:
    registry = SupervisorRegistry()
    registry.reconcile([exchange(qty="1")])
    first = registry.get("p1")
    registry.reconcile([exchange(qty="2")])
    assert registry.get("p1") is not first


def test_restart_restores_mfe_mae_and_state() -> None:
    engine = PositionSupervisor(identity())
    engine.restore_path(
        mfe_pct=Decimal("2.4"),
        mae_pct=Decimal("-0.8"),
        state=SupervisorState.WARNING,
        state_since=T0 + timedelta(minutes=2),
        last_at=T0 + timedelta(minutes=3),
    )
    snapshot = engine.update(PositionEvent(T0 + timedelta(minutes=4), Decimal("101"), complete()))
    assert snapshot.mfe_pct == Decimal("2.4")
    assert snapshot.mae_pct == Decimal("-0.8")
    assert snapshot.previous_state == SupervisorState.WARNING
