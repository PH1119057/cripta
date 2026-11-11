"""R06 frozen P90 LEGACY 5m/15m H9, Wilder ATR200 and native trade parity.

Authoritative policies/functions remain causal_entry_state, P90L90CausalEntry,
p90l90_crossing_fact and UniversalEntryEngine. This checks source-only legacy
WS envelopes vs exact immutable PG original provider envelopes, not market
replay or financial execution. Receive/accept timestamps intentionally differ.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.strategies.indicators import wilder_atr
from bybit_workbench.universal_entry.contracts import (
    StrategyActivation,
    TradeDirection,
)
from bybit_workbench.universal_entry.engine import UniversalEntryEngine
from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90CausalEntry,
    P90L90EntryState,
    causal_entry_state,
)
from bybit_workbench.universal_entry.p90l90_crossing_fact import (
    p90l90_crossing_fact,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_scoped_native_codec import (
    original_native_fact,
)
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    OrderedNativeTrade,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards
from bybit_workbench.universal_entry.registry import ActivePlanRegistry
from operations.monitoring.universal_entry_shadow import _trade_cursor, _trade_fact

D = Decimal
START = datetime(2026, 1, 1, tzinfo=UTC)
SYMBOL = "LTCUSDT"


def _closed(interval: int, count: int, *, symbol: str = SYMBOL) -> tuple[Candle, ...]:
    delta = timedelta(minutes=interval)
    # The existing P90 causal regression reference uses the same 9 days
    # and homogeneous 90/110 RAW range. These are tests, NOT live defaults.
    return tuple(
        Candle(
            symbol=symbol, timeframe=str(interval),
            opened_at=START + delta * i,
            closed_at=START + delta * (i + 1),
            open=D("100"), high=D("110"), low=D("90"),
            close=D("100"), volume=D("1"),
        )
        for i in range(count)
    )


def _history() -> tuple[tuple[Candle, ...], tuple[Candle, ...], datetime]:
    boundary = START + timedelta(days=9)
    return _closed(5, 9 * 288), _closed(15, 9 * 96), boundary


def _state_equal_from_both_closed_sources(
    old_fives: tuple[Candle, ...], old_fifteens: tuple[Candle, ...],
    pg_fives: tuple[Candle, ...], pg_fifteens: tuple[Candle, ...],
    *,
    at: datetime,
) -> P90L90EntryState | None:
    # Validate every *source* bar and its OHLCV/UTC boundary rather than
    # assuming two independently obtained lists are automatically equal.
    assert old_fives == pg_fives
    assert old_fifteens == pg_fifteens
    assert wilder_atr(old_fives, 200) == wilder_atr(pg_fives, 200)
    assert wilder_atr(old_fifteens, 200) == wilder_atr(pg_fifteens, 200)
    old = causal_entry_state(old_fives, old_fifteens, effective_at=at)
    pg = causal_entry_state(pg_fives, pg_fifteens, effective_at=at)
    assert old == pg
    return old


def _original_and_legacy(
    *,
    price: str,
    sequence: int,
    exec_id: str,
    when: datetime,
) -> tuple[object, object, object, object]:
    payload = {
        "s": SYMBOL, "p": price, "v": "0.500", "S": "Buy",
        "i": exec_id, "seq": sequence, "T": int(when.timestamp() * 1000),
    }
    ws_received = when + timedelta(milliseconds=45)
    pg_accepted = when + timedelta(milliseconds=115)
    old_fact = _trade_fact(payload, ws_received)
    old_cursor = _trade_cursor(payload)
    new_fact, new_cursor = original_native_fact(OrderedNativeTrade(
        frame_number=sequence, frame_index=0, symbol=SYMBOL,
        exchange_sequence=sequence, exec_id=exec_id,
        event_at=when, price=D(price), quantity=D("0.500"),
        taker_side="Buy", db_accepted_at=pg_accepted,
    ))
    assert old_cursor == new_cursor
    assert old_fact.fact_id == new_fact.fact_id
    assert old_fact.source_refs == new_fact.source_refs
    assert old_fact.event_kind == new_fact.event_kind == "PUBLIC_TRADE"
    assert old_fact.event_at == new_fact.event_at == when
    assert old_fact.symbol == new_fact.symbol == SYMBOL
    # Decimal/string formatting can differ between the independent
    # transport systems. Compare actual economic values, not formatting.
    left = old_fact.attributes.to_dict()
    right = new_fact.attributes.to_dict()
    assert D(str(left["price"])) == D(str(right["price"]))
    assert D(str(left["size"])) == D(str(right["size"]))
    assert left["taker_side"] == right["taker_side"]
    assert old_fact.received_at != new_fact.received_at
    return old_fact, old_cursor, new_fact, new_cursor


def _engine_and_plan():
    embargo = StrategyHardStopEmbargoPolicy.parse({
        "enabled": True, "hardstop_count": 2,
        "rolling_hours": 48, "new_entry_pause_hours": 48,
        "close_existing_positions": False, "domain": "STRATEGY_VERSION",
    })
    cards = new_version_cards(
        approved_at=START, policy_by_strategy_id={
            MAIN_ID: embargo, SUB_ID: embargo,
        },
    )
    card = next(c for c in cards if c.strategy_id == MAIN_ID)
    activation = StrategyActivation(
        activation_id="r06-frozen-parity-test",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True, enabled_at=START,
    )
    registry = ActivePlanRegistry()
    registry.register_card(card)
    plan = registry.activate(activation)
    return UniversalEntryEngine(registry), plan


@pytest.mark.parametrize(
    ("prices", "expect_direction"),
    [
        (("91", "90"), TradeDirection.LONG),
        (("109", "110"), TradeDirection.SHORT),
        (("99", "100"), None),
    ],
)
def test_frozen_h9_atr_and_entry_candidate_parity_both_sides(
    prices: tuple[str, str], expect_direction: TradeDirection | None,
) -> None:
    five, fifteen, at = _history()
    state = _state_equal_from_both_closed_sources(
        five, fifteen, tuple(five), tuple(fifteen), at=at,
    )
    assert state is not None
    assert state.l53_low_raw == D("90")
    assert state.l53_high_raw == D("110")
    assert state.long_depth_pct == state.short_depth_pct == D("0")
    assert state.working_width_pct == state.previous_seven_day_median
    assert wilder_atr(five, 200)[-1] == D("20")
    assert wilder_atr(fifteen, 200)[-1] == D("20")

    old = P90L90CausalEntry()
    new = P90L90CausalEntry()
    old.update_geometry(state)
    new.update_geometry(state)
    old_engine, plan = _engine_and_plan()
    new_engine, _ = _engine_and_plan()
    old_signals = []
    new_signals = []
    for index, price in enumerate(prices):
        when = at + timedelta(seconds=index + 1)
        old_fact, old_cursor, pg_fact, pg_cursor = _original_and_legacy(
            price=price, sequence=index + 1,
            exec_id=f"bybit-provider-{index}", when=when,
        )
        old_crossings = old.on_trade(
            price=D(str(old_fact.attributes.to_dict()["price"])),
            event_at=old_fact.event_at,
        )
        pg_crossings = new.on_trade(
            price=D(str(pg_fact.attributes.to_dict()["price"])),
            event_at=pg_fact.event_at,
        )
        assert old_crossings == pg_crossings
        for old_cross, pg_cross in zip(old_crossings, pg_crossings, strict=True):
            old_candidate = p90l90_crossing_fact(
                entry_plan_fingerprint=plan.entry_plan_fingerprint,
                source_fact=old_fact, crossing=old_cross, geometry=state,
            )
            pg_candidate = p90l90_crossing_fact(
                entry_plan_fingerprint=plan.entry_plan_fingerprint,
                source_fact=pg_fact, crossing=pg_cross, geometry=state,
            )
            assert old_candidate.fact_id == pg_candidate.fact_id
            assert old_candidate.direction == pg_candidate.direction
            assert old_candidate.event_at == pg_candidate.event_at
            assert old_candidate.attributes.to_dict() == pg_candidate.attributes.to_dict()
            old_result = old_engine.process(
                old_candidate, proven_p90_native_cursor=old_cursor,
                allow_new_signals=True, account_ref="BYBIT:UNIFIED",
            )
            pg_result = new_engine.process(
                pg_candidate, proven_p90_native_cursor=pg_cursor,
                allow_new_signals=True, account_ref="BYBIT:UNIFIED",
            )
            assert len(old_result) == len(pg_result)
            for o, p in zip(old_result, pg_result, strict=True):
                assert o.signal.fact_id == p.signal.fact_id
                assert o.signal.direction == p.signal.direction
                assert o.decision is p.decision is None
                assert o.execution_request is p.execution_request is None
                old_signals.append(o.signal.direction)
                new_signals.append(p.signal.direction)
    assert old_signals == new_signals
    assert old_signals == ([] if expect_direction is None else [expect_direction])


def test_h9_raw_exact_equality_failure_blocks_long_on_both_sources() -> None:
    five, fifteen, at = _history()
    last = fifteen[-1]
    changed = replace(last, low=D("89.99"))
    adjusted = fifteen[:-1] + (changed,)
    state = _state_equal_from_both_closed_sources(
        five, adjusted, tuple(five), tuple(adjusted), at=at,
    )
    assert state is not None
    assert state.long_depth_pct is None
    assert state.short_depth_pct == D("0")
    old, pg = P90L90CausalEntry(), P90L90CausalEntry()
    old.update_geometry(state)
    pg.update_geometry(state)
    old.on_trade(price=D("91"), event_at=at + timedelta(seconds=1))
    pg.on_trade(price=D("91"), event_at=at + timedelta(seconds=1))
    assert old.on_trade(price=D("90"), event_at=at + timedelta(seconds=2)) == ()
    assert pg.on_trade(price=D("90"), event_at=at + timedelta(seconds=2)) == ()


@pytest.mark.parametrize("failure", ("gap-5m", "short-atr", "missing-prev7d"))
def test_incomplete_causal_history_never_becomes_positive_entry(failure: str) -> None:
    five, fifteen, at = _history()
    if failure == "gap-5m":
        five = five[:100] + five[101:]
    elif failure == "short-atr":
        fifteen = fifteen[-199:]
    else:
        five = five[2 * 288:]
    state = _state_equal_from_both_closed_sources(
        five, fifteen, tuple(five), tuple(fifteen), at=at,
    )
    assert state is None


def test_independent_closed_history_drift_fails_before_candidate() -> None:
    five, fifteen, at = _history()
    changed = replace(fifteen[-1], high=D("110.01"))
    with pytest.raises(AssertionError):
        _state_equal_from_both_closed_sources(
            five, fifteen, five, fifteen[:-1] + (changed,), at=at,
        )


def test_published_transport_cannot_reenter_or_use_future_geometry() -> None:
    five, fifteen, at = _history()
    state = causal_entry_state(five, fifteen, effective_at=at)
    assert state is not None
    entry = P90L90CausalEntry()
    entry.update_geometry(state)
    assert entry.on_trade(price=D("91"), event_at=at + timedelta(seconds=1)) == ()
    assert len(entry.on_trade(price=D("90"), event_at=at + timedelta(seconds=2))) == 1
    entry.on_trade(price=D("91"), event_at=at + timedelta(seconds=3))
    assert entry.on_trade(price=D("89"), event_at=at + timedelta(seconds=4)) == ()
    assert entry.on_trade(price=D("91"), event_at=at + timedelta(minutes=5)) == ()
