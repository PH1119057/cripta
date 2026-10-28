"""Direct P90 native Entry adapter is Watch-fingerprint-identical and fail-closed.

No PostgreSQL, no Bybit and no StrategyActivation: the test injects ONLY a
transaction-shaped atomic gate, whose real PG behavior has separate tests.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    MarketFactEnvelope,
)
from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90CausalEntry,
    P90L90EntryState,
)
from bybit_workbench.universal_entry.p90l90_causal_fact_codec import (
    encode_p90l90_fact,
)
from bybit_workbench.universal_entry.p90l90_durable_paper_processor import (
    P90L90DurablePaperProcessor,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

B = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
SHA = "e" * 40


def _entry() -> P90L90CausalEntry:
    geometry = P90L90EntryState(
        effective_at=B,
        l53_low_raw=Decimal("100"),
        l53_high_raw=Decimal("110"),
        h9_5m_low_raw=Decimal("98"),
        h9_5m_high_raw=Decimal("112"),
        h9_15m_low_raw=Decimal("98"),
        h9_15m_high_raw=Decimal("112"),
        raw_h9_union_span=Decimal("14"),
        working_width_pct=Decimal("1"),
        previous_seven_day_median=Decimal("1"),
        long_depth_pct=Decimal("4"),
        short_depth_pct=None,
    )
    return P90L90CausalEntry(
        state=geometry, previous_price=Decimal("101"),
        previous_at=B+timedelta(seconds=1),
    )


def _native():
    at = B+timedelta(seconds=2)
    fact = MarketFactEnvelope(
        fact_id="trade-LTC-exec-12", event_kind="PUBLIC_TRADE",
        symbol="LTCUSDT", event_at=at, observed_at=at,
        received_at=at+timedelta(seconds=1),
        source_refs=("bybit:publicTrade:LTCUSDT:exec-12",),
        attributes=FrozenPolicy.from_mapping({
            "price": "99", "size": "5", "taker_side": "Sell",
        }),
    )
    return encode_p90l90_fact(
        fact, cursor=TradeCursor("LTCUSDT", "exec-12", 12, at),
    )


class AtomicSimulation:
    def __init__(self):
        self.entry = _entry()
        self.deliveries = 0

    def deliver(self, event, *, recompute_geometry, apply_paper):
        self.deliveries += 1
        apply_paper(self.entry, (event,))
        return (event,)


def test_candidate_projection_uses_exact_source_plan_and_exit_before_entry():
    callbacks = []
    processor = P90L90DurablePaperProcessor(
        SimpleNamespace(), source_commit=SHA,
        on_native_exit=lambda fact, cursor: callbacks.append(("exit", fact.fact_id)),
        on_closed_candle=lambda fact: callbacks.append(("candle", fact.fact_id)),
        candidate_entry_plan_fingerprint="entry-exact-abc",
        on_proven_candidate=lambda fact, cursor: callbacks.append(
            ("candidate", fact, cursor)
        ),
    )
    fake = AtomicSimulation()
    processor._atomic = fake
    processor.accept(_native())
    assert [c[0] for c in callbacks] == ["exit", "candidate"]
    candidate = callbacks[1][1]
    assert candidate.event_kind == "P90L90_RAW_H9_PREV7D_CROSSING"
    assert candidate.attributes.to_dict()["modeled_exchange_fill"] is False
    assert candidate.attributes.to_dict()["trade_fact_id"] == _native().event_id
    assert candidate.attributes.to_dict()["p90l90"]["opposite_raw_l53_target"] == "110"
    assert callbacks[1][2].exec_id == "exec-12"
    assert fake.entry.seen == {"LONG"}


def test_missing_candidate_owner_poisoned_and_never_falls_back():
    processor = P90L90DurablePaperProcessor(
        SimpleNamespace(), source_commit=SHA,
        on_native_exit=lambda fact, cursor: None,
        on_closed_candle=lambda fact: None,
    )
    fake = AtomicSimulation()
    processor._atomic = fake
    with pytest.raises(RuntimeError, match="CANDIDATE_OWNER_NOT_CONNECTED"):
        processor.accept(_native())
    with pytest.raises(RuntimeError, match="poisoned epoch"):
        processor.accept(_native())
    assert fake.deliveries == 1


def test_partial_candidate_owner_configuration_rejected():
    with pytest.raises(ValueError, match="requires both"):
        P90L90DurablePaperProcessor(
            SimpleNamespace(), source_commit=SHA,
            on_native_exit=lambda fact, cursor: None,
                on_closed_candle=lambda fact: None,
            candidate_entry_plan_fingerprint="fp",
        )
