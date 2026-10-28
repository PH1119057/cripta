"""P90 source bridge: old seed remains evidence, no second live candle writer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    MarketFactEnvelope,
)
from bybit_workbench.universal_entry.p90l90_causal_fact_codec import (
    encode_p90l90_fact,
)
from bybit_workbench.universal_entry.p90l90_causal_transport_join import (
    P90TransportEvent,
)
from bybit_workbench.universal_entry.p90l90_shared_candle_bridge import (
    P90SharedCandleLedger,
)

T = datetime(2026, 10, 28, 12, 30, tzinfo=UTC)


def _closed_event() -> P90TransportEvent:
    fact = MarketFactEnvelope(
        fact_id="p90-test-closed",
        event_kind="CANDLE_CLOSED",
        symbol="LTCUSDT",
        event_at=T, observed_at=T,
        received_at=T+timedelta(seconds=1),
        source_refs=("bybit:kline.5:LTCUSDT:test",),
        attributes=FrozenPolicy.from_mapping({
            "timeframe": "5",
            "opened_at": (T-timedelta(minutes=5)).isoformat(),
            "closed_at": T.isoformat(),
            "open": "100", "high": "101", "low": "99",
            "close": "100.5", "volume": "10",
        }),
    )
    return encode_p90l90_fact(fact)


def test_p90_new_closed_candle_is_verify_only_never_legacy_writer() -> None:
    event = _closed_event()
    adapter = object.__new__(P90SharedCandleLedger)
    adapter._seed = {("LTCUSDT", "5"): T-timedelta(minutes=5)}
    adapter._emitted = {("LTCUSDT", "5"): T-timedelta(minutes=5)}
    exact = Candle(
        "LTCUSDT", "5", T-timedelta(minutes=5), T,
        Decimal("100"), Decimal("101"), Decimal("99"),
        Decimal("100.5"), Decimal("10"),
    )
    adapter._canonical = lambda *_args, **_kwargs: (exact,)
    # No database connection or legacy table write is even possible in
    # this fixture; match only checks independently committed OHLC.
    assert adapter.append(event) is False


def test_p90_conflicting_or_earlier_candle_never_rewrites_seed() -> None:
    event = _closed_event()
    adapter = object.__new__(P90SharedCandleLedger)
    adapter._seed = {("LTCUSDT", "5"): T}
    adapter._emitted = {("LTCUSDT", "5"): T}
    with pytest.raises(RuntimeError, match="CANNOT_REWRITE_ORIGINAL"):
        adapter.append(event)
    adapter._seed = {("LTCUSDT", "5"): T-timedelta(minutes=5)}
    adapter._emitted = {("LTCUSDT", "5"): T-timedelta(minutes=5)}
    wrong = Candle(
        "LTCUSDT", "5", T-timedelta(minutes=5), T,
        Decimal("100"), Decimal("101"), Decimal("99"),
        Decimal("100.6"), Decimal("10"),
    )
    adapter._canonical = lambda *_args, **_kwargs: (wrong,)
    with pytest.raises(RuntimeError, match="NOT_EQUAL_TO_MARKET_SOURCE"):
        adapter.append(event)


def test_p90_original_checkpoint_uses_only_frozen_legacy_archive() -> None:
    adapter = object.__new__(P90SharedCandleLedger)
    adapter._seed = {("LTCUSDT", "5"): T}
    adapter._emitted = {("LTCUSDT", "5"): T}
    class FrozenArchive:
        def __init__(self) -> None:
            self.called = False

        def history(self, **kwargs):
            self.called = True
            return ("original-frozen-seed",)

    adapter._legacy = FrozenArchive()
    adapter._canonical = lambda *_args, **_kwargs: pytest.fail(
        "cannot replace old seed with newer provider version"
    )
    assert adapter.history(
        symbol="LTCUSDT", timeframe="5", through=T, limit=2600,
    ) == ("original-frozen-seed",)
    assert adapter._legacy.called


def test_missing_shared_new_history_is_not_silently_reset() -> None:
    adapter = object.__new__(P90SharedCandleLedger)
    adapter._seed = {("LTCUSDT", "5"): T-timedelta(minutes=5)}
    adapter._emitted = {("LTCUSDT", "5"): T-timedelta(minutes=5)}
    def missing(*_args, **_kwargs):
        raise RuntimeError("CANDLE_WARMUP_INCOMPLETE")
    adapter._canonical = missing
    with pytest.raises(RuntimeError, match="WARMUP_INCOMPLETE"):
        adapter.history(
            symbol="LTCUSDT", timeframe="5", through=T, limit=2600,
        )


def test_gap_plan_is_exact_and_5m_first_at_same_close() -> None:
    adapter = object.__new__(P90SharedCandleLedger)
    adapter._seed = {
        ("LTCUSDT", "5"): T,
        ("LTCUSDT", "15"): T,
    }
    def read(symbol: str, tf: str, *, through: datetime, count: int):
        assert symbol == "LTCUSDT"
        step = timedelta(minutes=int(tf))
        return tuple(
            Candle(
                symbol, tf, through-step*(count-i),
                through-step*(count-i-1),
                Decimal("100"), Decimal("101"),
                Decimal("99"), Decimal("100"),
                Decimal("1"),
            ) for i in range(count)
        )

    adapter._canonical = read
    plan = adapter.closed_gap_plan(
        symbol="LTCUSDT",
        through_5m=T+timedelta(minutes=15),
        through_15m=T+timedelta(minutes=15),
    )
    assert [(tf, bar.closed_at) for tf, bar in plan] == [
        ("5", T+timedelta(minutes=5)),
        ("5", T+timedelta(minutes=10)),
        ("5", T+timedelta(minutes=15)),
        ("15", T+timedelta(minutes=15)),
    ]


def test_gap_plan_refuses_any_missing_source_bar() -> None:
    adapter = object.__new__(P90SharedCandleLedger)
    adapter._seed = {
        ("LTCUSDT", "5"): T,
        ("LTCUSDT", "15"): T,
    }
    def missing(*_args, **_kwargs):
        raise RuntimeError("CANDLE_HISTORY_GAP_OR_DUPLICATE")
    adapter._canonical = missing
    with pytest.raises(RuntimeError, match="GAP_OR_DUPLICATE"):
        adapter.closed_gap_plan(
            symbol="LTCUSDT",
            through_5m=T+timedelta(minutes=10),
            through_15m=T,
        )


def test_gap_plan_never_invents_public_trades() -> None:
    adapter = object.__new__(P90SharedCandleLedger)
    adapter._seed = {
        ("LTCUSDT", "5"): T,
        ("LTCUSDT", "15"): T,
    }
    adapter._canonical = lambda *_args, **_kwargs: (
        Candle(
            "LTCUSDT", "5", T,
            T+timedelta(minutes=5),
            Decimal("100"), Decimal("101"),
            Decimal("99"), Decimal("100"), Decimal("1"),
        ),
    )
    plan = adapter.closed_gap_plan(
        symbol="LTCUSDT",
        through_5m=T+timedelta(minutes=5),
        through_15m=T,
    )
    assert len(plan) == 1
    assert plan[0][0] == "5"
    # A real native event has execId/seq; no such field is created here.
    assert all(isinstance(event, Candle) for _, event in plan)


def test_shared_market_source_future_cached_15m_is_not_causal_15m() -> None:
    adapter = object.__new__(P90SharedCandleLedger)
    adapter._seed = {
        ("LTCUSDT", "5"): T,
        ("LTCUSDT", "15"): T,
    }
    adapter._emitted = {
        ("LTCUSDT", "5"): T,
        ("LTCUSDT", "15"): T,
    }
    # A global cache may already contain a future 15m close. It must not
    # advance geometry before the exact causal join emits that 15m bar.
    adapter._canonical = lambda *_args, **_kwargs: pytest.fail(
        "preloaded future bar cannot be a causal watermark"
    )
    assert adapter.closed_at_or_before(
        symbol="LTCUSDT", timeframe="15",
        through=T+timedelta(minutes=5),
    ) == T
    assert adapter.closed_at_or_before(
        symbol="LTCUSDT", timeframe="5",
        through=T+timedelta(minutes=5),
    ) == T
