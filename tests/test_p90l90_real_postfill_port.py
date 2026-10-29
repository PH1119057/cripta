"""Postfill -> Universal Exit/Execution owner, no fake PAPER or Bybit API."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_p90l90_real_observation import AT
from test_p90l90_real_postfill_observation import _proof

from bybit_workbench.universal_exit.contracts import (
    ExitActionKind,
    ExitEvaluationStatus,
)
from bybit_workbench.universal_exit.p90l90_real_postfill_port import (
    P90RealPostfillExitPort,
    P90RealPostfillHandoffUnverified,
)


class _DB:
    def __init__(self, *, in_transaction: bool = True):
        self.in_transaction = in_transaction

    @property
    def info(self):
        return SimpleNamespace(
            transaction_status=SimpleNamespace(
                name="INTRANS" if self.in_transaction else "IDLE",
            ),
        )


class _Store:
    def __init__(self):
        self.recorded = []

    def record(self, evaluation, *, position, plan):
        self.recorded.append((evaluation, position, plan))


def _setup(*, short: bool = False, in_transaction: bool = True):
    position, plan, private, coverage = _proof(short=short)
    db = _DB(in_transaction=in_transaction)
    store = _Store()
    commands = []
    def send(evaluation, position, plan):
        commands.append((evaluation, position, plan))
    port = P90RealPostfillExitPort(
        db,
        position_provider=lambda symbol: ((position, plan),),
        exit_store=store,  # type: ignore[arg-type]
        execution_port=send,
    )
    kwargs = dict(
        position=position, plan=plan,
        private=private, coverage=coverage,
        signed_fill_execution_ids=("exact-bybit-fill-1",),
        exact_entry_order_id="exact-order-id",
        received_at=AT + timedelta(milliseconds=5),
    )
    return port, db, store, commands, kwargs


@pytest.mark.parametrize("short", (False, True))
def test_proven_first_fill_queues_only_exchange_native_trailing(
    short: bool,
) -> None:
    port, _db, store, commands, kwargs = _setup(short=short)
    evaluation = port(**kwargs)
    assert evaluation.status is ExitEvaluationStatus.DECISION_CREATED
    assert evaluation.decision is not None
    assert evaluation.decision.action_kind is ExitActionKind.SET_TRAILING
    assert store.recorded == [(evaluation, kwargs["position"], kwargs["plan"])]
    assert commands == [(evaluation, kwargs["position"], kwargs["plan"])]


def test_source_transaction_absence_never_reads_position_or_creates_exit() -> None:
    port, _db, store, commands, kwargs = _setup(in_transaction=False)
    with pytest.raises(
        P90RealPostfillHandoffUnverified,
        match="OWNER_TRANSACTION_NOT_OPEN",
    ):
        port(**kwargs)
    assert store.recorded == []
    assert commands == []


def test_duplicate_or_foreign_durable_owner_blocks_before_any_exit_side_effect() -> None:
    port, _db, store, commands, kwargs = _setup()
    pos, plan = kwargs["position"], kwargs["plan"]
    port._positions = lambda symbol: ((pos, plan), (pos, plan))
    with pytest.raises(
        P90RealPostfillHandoffUnverified,
        match="POSITION_OR_EXIT_PLAN_NOT_DURABLE",
    ):
        port(**kwargs)
    assert store.recorded == []
    assert commands == []


def test_unverified_source_coverage_never_reaches_execution_port() -> None:
    port, _db, store, commands, kwargs = _setup()
    kwargs["coverage"] = replace(
        kwargs["coverage"], complete_native_window=False,
    )
    with pytest.raises(ValueError, match="SOURCE_ACCOUNT_OR_ORDER_UNVERIFIED"):
        port(**kwargs)
    assert store.recorded == []
    assert commands == []
