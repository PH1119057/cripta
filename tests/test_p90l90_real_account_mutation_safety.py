"""Account-scoped signed Exchange mutation reattestation: no orders."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_p90l90_real_observation import AT, _scope

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.p90l90_real_mfe import P90RealMfeCheckpoint
from bybit_workbench.universal_exit.p90l90_real_observation import (
    build_real_native_exit_observation,
)
from bybit_workbench.universal_exit.p90l90_real_private_router import (
    P90RealPrivateEvidenceRouter,
)
from operations.monitoring.p90l90_real_account_mutation_safety import (
    P90RealIndependentMutationSafety,
)
from operations.monitoring.p90l90_real_live_source_ports import (
    P90RealLiveSourcePorts,
)
MAIN = "ta-" + "a" * 32
SUB = "ta-" + "b" * 32


class FakeSource(P90RealLiveSourcePorts):
    def __init__(self) -> None:
        self.calls = 0

    def _source_transaction(self) -> None:
        self.calls += 1


class FakeSignedPrivate(P90RealPrivateEvidenceRouter):
    def __init__(self, evidence):
        self.evidence = evidence
        self.reads = 0

    def for_closed_boundary(self, position, event_at):
        self.reads += 1
        assert event_at >= position.fill_at
        return self.evidence


class FakeDB:
    def __init__(self, position, *, pending=0, wrong_binding=False):
        self.position = position
        self.pending = pending
        self.wrong_binding = wrong_binding
        self.statements = []

    def execute(self, statement, params=()):
        self.statements.append((statement, params))
        if "FROM runtime.trade_commands" in statement:
            return SimpleNamespace(fetchone=lambda: (self.pending,))
        if "FROM strategy_entry.strategy_execution_bindings" in statement:
            return SimpleNamespace(fetchall=lambda: (
                [] if self.wrong_binding else
                [(self.position.strategy_execution_binding_id,
                  self.position.trading_account_id)]
            ))
        raise AssertionError(statement)


def _case(*, sub: bool = False):
    position, plan, _state, cursor, private, price = _scope(sub=sub)
    account = SUB if sub else MAIN
    position = replace(
        position, trading_account_id=account,
        exchange_position_key=exchange_position_key(
            account, position.symbol, position.position_idx,
        ),
    )
    private = replace(
        private, trading_account_id=account,
        exchange_position_key=position.exchange_position_key,
        observed_at=cursor.traded_at + timedelta(milliseconds=3),
    )
    checkpoint = P90RealMfeCheckpoint.begin(
        position=position, source_commit="a" * 40,
        first_cursor=cursor, first_price=price,
        verified_fill_to_first_trade_coverage=True,
    )
    observed = build_real_native_exit_observation(
        position=position, checkpoint=checkpoint, cursor=cursor, price=price,
        native_fact_id="real-native-event",
        native_source_refs=("bybit:publicTrade:exact-real-trade",),
        private=private, received_at=private.observed_at,
    )
    evaluated = UniversalExitEngine().evaluate(position, plan, observed)
    assert evaluated.decision is not None
    db = FakeDB(position)
    source = FakeSource()
    signed = FakeSignedPrivate(private)
    check = P90RealIndependentMutationSafety(
        db, source=source, signed_private=signed,
        account_by_strategy={MAIN_ID: MAIN, SUB_ID: SUB},
        connector_by_strategy={MAIN_ID: "connector-main", SUB_ID: "connector-sub"},
    )
    now = private.observed_at + timedelta(milliseconds=1)
    return check, db, source, signed, position, evaluated, now


@pytest.mark.parametrize("sub", (False, True))
def test_exact_current_signed_main_sub_protection_allows_a_real_exit_request(
    sub: bool,
) -> None:
    checker, db, source, signed, position, evaluated, now = _case(sub=sub)
    assert checker(position, evaluated, now) is True
    assert source.calls == 1 and signed.reads == 1
    assert len(db.statements) == 2


@pytest.mark.parametrize("bad", (
    "changed-stop", "changed-tp", "changed-trailing",
    "late-activation", "other-connector", "other-account",
    "pending-command", "wrong-binding", "stale-signed", "stale-decision",
))
def test_exit_mutation_never_trusts_stale_or_foreign_signed_protection(
    bad: str,
) -> None:
    checker, db, source, signed, position, evaluated, now = _case()
    raw = dict(signed.evidence.position)
    if bad == "changed-stop":
        raw["stopLoss"] = "89"
    elif bad == "changed-tp":
        raw["takeProfit"] = "109"
    elif bad == "changed-trailing":
        raw["trailingStop"] = "0.35"
    elif bad == "late-activation":
        raw["markPrice"] = "100.6"
    elif bad == "other-connector":
        signed.evidence = replace(signed.evidence, connector_id="wrong")
    elif bad == "other-account":
        signed.evidence = replace(signed.evidence, trading_account_id=SUB)
    elif bad == "pending-command":
        db.pending = 1
    elif bad == "wrong-binding":
        db.wrong_binding = True
    elif bad == "stale-signed":
        signed.evidence = replace(
            signed.evidence, observed_at=AT - timedelta(seconds=30),
        )
    elif bad == "stale-decision":
        now = now + timedelta(seconds=6)
    signed.evidence = replace(signed.evidence, position=raw)
    assert checker(position, evaluated, now) is False


def test_current_signed_source_or_account_transport_failure_denies_exit(
    monkeypatch,
) -> None:
    checker, _db, source, _signed, position, evaluated, now = _case()
    def reject():
        raise RuntimeError("SOURCE_EPOCH_FORKED")
    monkeypatch.setattr(source, "_source_transaction", reject)
    assert checker(position, evaluated, now) is False
