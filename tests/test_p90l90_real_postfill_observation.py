"""First REAL fill handoff to Universal Exit, without inventing a native tick.

Tests use synthetic independently supplied source facts only. They do not
submit Bybit orders, arm MAIN/SUB or attest a running MarketSource.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from test_p90l90_real_observation import AT, D, _scope

from bybit_workbench.universal_exit.contracts import (
    ExitActionKind,
    ExitEvaluationStatus,
)
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.p90l90_real_observation import (
    P90RealObservationUnverified,
    P90RealPostfillSourceWindow,
    build_real_postfill_exit_observation,
)


def _proof(*, short: bool = False, sub: bool = False):
    position, plan, _checkpoint, _cursor, private, _price = _scope(
        short=short, sub=sub,
    )
    private = replace(
        private,
        source_ref="bybit:signed-position-list:exact-account",
    )
    source = P90RealPostfillSourceWindow(
        position_id=position.strategy_position_id,
        symbol=position.symbol,
        fill_at=position.fill_at,
        covered_through=AT + timedelta(milliseconds=3),
        source_commit="a" * 40,
        last_native_price=D("99.8") if short else D("100.2"),
        best_favorable_price=D("99.8") if short else D("100.2"),
        source_refs=(f"bybit:publicTrade:{position.symbol}:source-epoch",),
        complete_native_window=True,
    )
    return position, plan, private, source


@pytest.mark.parametrize("short,sub", ((False, False), (True, True)))
def test_actual_postfill_handoff_arms_exchange_trail_without_next_native_tick(
    short: bool, sub: bool,
) -> None:
    position, plan, private, source = _proof(short=short, sub=sub)
    obs = build_real_postfill_exit_observation(
        position=position,
        private=private,
        coverage=source,
        signed_fill_execution_ids=("first-fill-execution",),
        exact_entry_order_id="signed-entry-order",
        received_at=AT + timedelta(milliseconds=5),
    )
    assert obs.event_kind == "P90L90_REAL_POSTFILL"
    assert obs.attributes.to_dict()["p90l90_real"]["fill_to_postfill_source_verified"]
    assert any(ref.startswith("bybit:execution:list:exact-order:")
               for ref in obs.source_refs)
    result = UniversalExitEngine().evaluate(position, plan, obs)
    assert result.status is ExitEvaluationStatus.DECISION_CREATED
    assert result.decision is not None
    assert result.decision.action_kind is ExitActionKind.SET_TRAILING
    assert result.decision.requested_mutation.to_dict()["distance"] == "0.35"


@pytest.mark.parametrize("failure", (
    "gap", "wrong-symbol", "wrong-position", "wrong-account",
    "signed-private-not-exact", "missing-fill-exec", "future-time",
    "stale-source", "bad-signed-trailing",
))
def test_postfill_builder_denies_unproven_real_execution_or_native_window(
    failure: str,
) -> None:
    position, plan, private, source = _proof()
    ids = ("first-fill-execution",)
    received_at = AT + timedelta(milliseconds=5)
    if failure == "gap":
        source = replace(source, complete_native_window=False)
    elif failure == "wrong-symbol":
        source = replace(source, symbol="DOTUSDT")
    elif failure == "wrong-position":
        source = replace(source, position_id="foreign")
    elif failure == "wrong-account":
        private = replace(private, trading_account_id="ta-foreign")
    elif failure == "signed-private-not-exact":
        private = replace(private, source_ref="bybit:unsigned:test")
    elif failure == "missing-fill-exec":
        ids = ()
    elif failure == "future-time":
        source = replace(
            source, covered_through=AT + timedelta(seconds=1),
        )
    elif failure == "stale-source":
        received_at = AT + timedelta(seconds=7)
    elif failure == "bad-signed-trailing":
        private = replace(
            private, position={**private.position, "trailingStop": "NaN"},
        )
    with pytest.raises(P90RealObservationUnverified):
        build_real_postfill_exit_observation(
            position=position, private=private, coverage=source,
            signed_fill_execution_ids=ids,
            exact_entry_order_id="signed-entry-order",
            received_at=received_at,
        )
