"""Recovery replay must rebuild causal state without trading callbacks."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    MarketFactEnvelope,
)
from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalEntry
from bybit_workbench.universal_entry.p90l90_causal_fact_codec import (
    encode_p90l90_fact,
)
from bybit_workbench.universal_entry.p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
)
from bybit_workbench.universal_entry.p90l90_durable_paper_processor import (
    P90L90DurablePaperProcessor,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

AT = datetime(2026, 10, 29, 19, 0, tzinfo=UTC)


class FakeAtomic:
    def __init__(self) -> None:
        self.join = P90L90CausalTransportJoin(
            symbol="LTCUSDT",
            closed_5m_at=AT,
            closed_15m_at=AT,
        )
        self.entry = P90L90CausalEntry()

    def deliver(self, event, *, recompute_geometry, apply_paper):
        del recompute_geometry
        released = self.join.accept(event)
        apply_paper(self.entry, released)
        return released


class UnusedBars:
    def recompute_geometry(self, **_kwargs):
        raise AssertionError("native-only recovery must not read candle history")


def _native():
    at = AT + timedelta(seconds=1)
    fact = MarketFactEnvelope(
        fact_id="trade-restart-101",
        symbol="LTCUSDT",
        event_kind="PUBLIC_TRADE",
        event_at=at,
        observed_at=at,
        received_at=at + timedelta(milliseconds=2),
        source_refs=("bybit:publicTrade:LTCUSDT:restart-101",),
        attributes=FrozenPolicy.from_mapping({
            "price": "100.25",
            "size": "0.1",
            "taker_side": "Buy",
        }),
    )
    cursor = TradeCursor(
        symbol="LTCUSDT",
        exec_id="restart-101",
        seq=101,
        traded_at=at,
    )
    return encode_p90l90_fact(fact, cursor=cursor), cursor


def test_recovery_native_advances_entry_and_observation_only():
    normal_actions: list[str] = []
    observed: list[tuple[str, int]] = []
    atomic = FakeAtomic()
    processor = P90L90DurablePaperProcessor(
        object(),
        source_commit="a" * 40,
        atomic_checkpoint_store=atomic,
        bar_ledger=UnusedBars(),
        on_native_exit=lambda *_: normal_actions.append("EXIT"),
        on_closed_candle=lambda *_: normal_actions.append("CANDLE_EXIT"),
        candidate_entry_plan_fingerprint="entry-plan",
        on_proven_candidate=lambda *_: normal_actions.append("ENTRY"),
    )
    event, cursor = _native()
    released = processor.accept_recovery(
        event,
        on_recovered_native=lambda fact, native: observed.append(
            (fact.fact_id, native.seq)
        ),
    )
    assert released == (event,)
    assert observed == [("trade-restart-101", 101)]
    assert normal_actions == []
    assert atomic.entry.previous_at == cursor.traded_at
    assert str(atomic.entry.previous_price) == "100.25"
