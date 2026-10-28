"""Synthetic causal 5m REAL Exit uses existing UniversalExitEngine only."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_exit.contracts import (
    ExitActionKind,
    ExitEvaluationStatus,
)
from bybit_workbench.universal_exit.p90l90_real_closed_exit_port import (
    P90RealClosedExitPort,
)
from bybit_workbench.universal_exit.p90l90_real_closed_geometry import (
    P90RealClosedGeometryUnverified,
)
from test_p90l90_real_observation import _scope

AT = datetime(2026, 10, 29, 2, 15, tzinfo=UTC)
D = Decimal


def _source(
    symbol: str,
) -> tuple[MarketFactEnvelope, tuple[Candle, ...]]:
    bars: list[Candle] = []
    for i in range(36):
        end = AT - timedelta(minutes=5 * (35 - i))
        hi = D("112") if i == 12 else D("110")
        lo = D("88") if i == 15 else D("90")
        bars.append(Candle(
            symbol=symbol, timeframe="5",
            opened_at=end - timedelta(minutes=5), closed_at=end,
            open=D("100"), high=hi, low=lo, close=D("100"),
            volume=D("10"),
        ))
    fact = MarketFactEnvelope(
        fact_id="real-shared-bar-215", event_kind="CANDLE_CLOSED",
        symbol=symbol, event_at=AT, observed_at=AT,
        received_at=AT + timedelta(milliseconds=1),
        source_refs=("market-source:BYBIT:5m:exact-bar",),
        attributes=FrozenPolicy.from_mapping({
            "timeframe": "5", "closed_at": AT.isoformat(),
            "open": "100", "high": "110", "low": "90", "close": "100",
        }),
    )
    return fact, tuple(bars)


@pytest.mark.parametrize(("short", "sub", "target"), (
    (False, False, "112"), (True, True, "88"),
))
def test_verified_closed36_and_signed_private_create_dynamic_tp_through_engine(
    short: bool, sub: bool, target: str,
) -> None:
    position, plan, checkpoint, _cursor, private, _price = _scope(
        short=short, sub=sub,
    )
    fact, candles = _source(position.symbol)
    private = replace(private, observed_at=AT + timedelta(milliseconds=1))
    seen: list[object] = []

    class _Store:
        def record(self, evaluation, *, position, plan):
            seen.append((evaluation, position, plan))

    port = P90RealClosedExitPort(
        position_provider=lambda _symbol: ((position, plan),),
        independently_verified_closed_history=lambda _symbol, _at: candles,
        independent_causal_source_proof=lambda _symbol, _at: True,
        independently_signed_private_evidence=lambda _position, _at: private,
        source_commit="a" * 40,
        mfe_ledger=SimpleNamespace(load_latest=lambda **_kw: (
            checkpoint, "native-fact-101", ("bybit:publicTrade:exact:101",),
        )),
        exit_store=_Store(),  # type: ignore[arg-type]
    )
    evaluations = port(fact)
    assert len(evaluations) == 1
    ev = evaluations[0]
    assert ev.status is ExitEvaluationStatus.DECISION_CREATED
    assert ev.decision is not None
    assert ev.decision.action_kind is ExitActionKind.SET_TP
    assert ev.decision.requested_mutation.to_dict()["take_profit_price"] == target
    assert seen == [(ev, position, plan)]
    assert fact.source_refs[0] in ev.observation.source_refs


def test_missing_continuity_denies_reading_prices_or_emitting_exit() -> None:
    position, plan, checkpoint, _cursor, private, _price = _scope()
    fact, candles = _source(position.symbol)
    seen: list[str] = []
    port = P90RealClosedExitPort(
        position_provider=lambda _symbol: ((position, plan),),
        independently_verified_closed_history=lambda _symbol, _at: (
            seen.append("CANDLE"), candles,
        )[1],
        independent_causal_source_proof=lambda _symbol, _at: False,
        independently_signed_private_evidence=lambda _position, _at: private,
        source_commit="a" * 40,
        mfe_ledger=SimpleNamespace(load_latest=lambda **_kw: (
            checkpoint, "native-fact-101", ("native:exact",),
        )),
        exit_store=SimpleNamespace(record=lambda *_args, **_kwargs: seen.append("EXIT")),
    )
    with pytest.raises(P90RealClosedGeometryUnverified, match="SOURCE_OR_CONTINUITY"):
        port(fact)
    assert seen == []


def test_bar_drift_and_incomplete_history_refuse_real_dynamic_tp() -> None:
    position, plan, checkpoint, _cursor, private, _price = _scope()
    fact, candles = _source(position.symbol)
    private = replace(private, observed_at=AT + timedelta(milliseconds=1))
    seen: list[str] = []

    def _port(history):
        return P90RealClosedExitPort(
            position_provider=lambda _symbol: ((position, plan),),
            independently_verified_closed_history=lambda _symbol, _at: history,
            independent_causal_source_proof=lambda _symbol, _at: True,
            independently_signed_private_evidence=lambda _position, _at: private,
            source_commit="a" * 40,
            mfe_ledger=SimpleNamespace(load_latest=lambda **_kw: (
                checkpoint, "native-fact-101", ("native:exact",),
            )),
            exit_store=SimpleNamespace(
                record=lambda *_args, **_kwargs: seen.append("EXIT"),
            ),
        )

    with pytest.raises(P90RealClosedGeometryUnverified):
        _port(candles[:-1])(fact)
    changed = (*candles[:-1], replace(candles[-1], high=D("111")))
    with pytest.raises(P90RealClosedGeometryUnverified, match="EMITTED_BAR_DRIFT"):
        _port(changed)(fact)
    assert seen == []
