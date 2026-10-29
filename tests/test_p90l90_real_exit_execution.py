"""REAL Exit dispatch: synthetic typed bridge/DB checkpoint, NO Exchange orders."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_p90l90_real_observation import _observation, _scope

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_exit import p90l90_real_exit_execution as mod
from bybit_workbench.universal_exit.contracts import ExitEvaluation, ExitEvaluationStatus
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.execution_bridge import ExitExecutionBridgeBlocked
from bybit_workbench.universal_exit.p90l90_real_exit_port import P90RealExitNativePort
from bybit_workbench.universal_exit.p90l90_real_mfe import P90RealMfeUnverified

ACCOUNT_MAIN = "ta-" + "1" * 32
ACCOUNT_SUB = "ta-" + "2" * 32


class _DB:
    def __init__(self):
        self.events: list[object] = []
        self.open_transactions = 0
        self.outer_source_transaction_active = True

    @property
    def info(self):
        return SimpleNamespace(
            transaction_status=SimpleNamespace(
                name="INTRANS" if self.outer_source_transaction_active else "IDLE"
            ),
        )

    @contextmanager
    def transaction(self):
        self.open_transactions += 1
        try:
            yield
        finally:
            self.open_transactions -= 1

    def execute(self, sql, params=()):
        assert self.open_transactions > 0
        assert "pg_advisory_xact_lock" in sql
        self.events.append(("account-lock", params))
        return None


def _valid():
    position, plan, checkpoint, cursor, private, price = _scope()
    observation = _observation(position, checkpoint, cursor, private, price)
    evaluation = UniversalExitEngine().evaluate(position, plan, observation)
    assert evaluation.status is ExitEvaluationStatus.DECISION_CREATED
    assert evaluation.decision is not None

    position = replace(
        position,
        trading_account_id=ACCOUNT_MAIN,
        exchange_position_key=exchange_position_key(
            ACCOUNT_MAIN, position.symbol, 0
        ),
    )
    details = observation.attributes.to_dict()
    details["p90l90_real"].update({
        "trading_account_id": ACCOUNT_MAIN,
        "exchange_position_key": position.exchange_position_key,
    })
    observation = replace(
        observation,
        attributes=FrozenPolicy.from_mapping(details),
        source_refs=(
            *observation.source_refs,
            "bybit:signed-position-list:exact-account:account=" + ACCOUNT_MAIN,
        ),
    )
    return position, plan, replace(evaluation, observation=observation)


def _sink(monkeypatch, *, safety=True, missing_exit_ttl=False, age_ms=10):
    db = _DB()
    position, plan, evaluation = _valid()
    assert plan.exit_policy.to_dict()["execution_policy"] == {
        "max_request_age_seconds": 5
    }
    if missing_exit_ttl:
        # Negative-only fixture: a broken/unapproved ExitPlan must still
        # fail closed, but the REAL Strategy contract is never modified.
        policy = plan.exit_policy.to_dict()
        del policy["execution_policy"]
        plan = replace(plan, exit_policy=FrozenPolicy.from_mapping(policy))
    steps: list[str] = []

    def lookup(_db, *, strategy_position_id):
        steps.append("load")
        assert strategy_position_id == position.strategy_position_id
        return position, plan

    def saved(_db, request):
        steps.append("request")
        assert request.trading_account_id == ACCOUNT_MAIN
        assert request.exit_decision_id == evaluation.decision.exit_decision_id

    def queued(_db, prepared, *, now):
        steps.append("queue")
        assert prepared.payload["trading_account_id"] == ACCOUNT_MAIN
        assert prepared.payload["source"] == "universal_exit"
        assert prepared.command_id.startswith("ux-")

    def guard(pos, ev, now):
        steps.append("independent-safety")
        assert pos == position
        assert ev == evaluation
        return safety

    monkeypatch.setattr(mod, "load_exit_binding", lookup)
    monkeypatch.setattr(mod, "persist_exit_execution_request", saved)
    monkeypatch.setattr(mod, "publish_runtime_exit_command", queued)
    assert evaluation.decision is not None
    sink = mod.P90RealExitExecutionPort(
        db,
        independently_verified_execution_safety=guard,
        account_by_strategy={MAIN_ID: ACCOUNT_MAIN, SUB_ID: ACCOUNT_SUB},
        clock=lambda: evaluation.decision.decided_at + timedelta(milliseconds=age_ms),
    )
    return sink, db, steps, position, plan, evaluation


def test_real_native_decision_persists_request_then_single_account_command(
    monkeypatch,
) -> None:
    sink, db, steps, position, plan, evaluation = _sink(monkeypatch)
    prepared = sink(evaluation, position, plan)
    assert prepared is not None
    assert steps == ["load", "independent-safety", "request", "queue"]
    assert db.events == [("account-lock", (ACCOUNT_MAIN,))]
    assert db.open_transactions == 0


def test_missing_exit_ttl_still_fails_closed_without_dispatch(monkeypatch) -> None:
    sink, db, steps, position, plan, evaluation = _sink(
        monkeypatch, missing_exit_ttl=True,
    )
    with pytest.raises(
        ExitExecutionBridgeBlocked, match="POLICY_INCOMPLETE"
    ):
        sink(evaluation, position, plan)
    assert steps == ["load", "independent-safety"]
    assert db.open_transactions == 0


@pytest.mark.parametrize(("age_ms", "expired"), ((4999, False), (5000, True), (5001, True)))
def test_real_exit_request_expires_at_exact_five_second_boundary(
    monkeypatch, age_ms: int, expired: bool,
) -> None:
    sink, db, steps, position, plan, evaluation = _sink(
        monkeypatch, age_ms=age_ms,
    )
    if expired:
        with pytest.raises(ExitExecutionBridgeBlocked, match="REQUEST_EXPIRED"):
            sink(evaluation, position, plan)
        assert steps == ["load", "independent-safety"]
    else:
        assert sink(evaluation, position, plan) is not None
        assert steps == ["load", "independent-safety", "request", "queue"]
    assert db.open_transactions == 0


def test_real_exit_command_cannot_leave_source_checkpoint_transaction(
    monkeypatch,
) -> None:
    sink, db, steps, position, plan, evaluation = _sink(monkeypatch)
    db.outer_source_transaction_active = False
    with pytest.raises(
        mod.P90RealExitExecutionUnverified, match="SOURCE_TRANSACTION_NOT_OPEN"
    ):
        sink(evaluation, position, plan)
    assert steps == []
    assert db.events == []


def test_unknown_account_mutation_safety_never_materializes_command(
    monkeypatch,
) -> None:
    sink, db, steps, position, plan, evaluation = _sink(
        monkeypatch, safety=False,
    )
    with pytest.raises(
        mod.P90RealExitExecutionUnverified, match="MUTATION_SAFETY_UNVERIFIED"
    ):
        sink(evaluation, position, plan)
    assert steps == ["load", "independent-safety"]
    assert db.open_transactions == 0


@pytest.mark.parametrize("field", (
    "trading_account_id", "identity_status", "source_refs",
    "event_kind", "signed_source", "protection",
))
def test_unsafe_exit_cannot_queue_even_when_independent_guard_true(
    monkeypatch, field: str,
) -> None:
    sink, db, steps, position, plan, evaluation = _sink(monkeypatch)
    observation = evaluation.observation
    if field == "trading_account_id":
        position = replace(
            position, trading_account_id=ACCOUNT_SUB,
            exchange_position_key=exchange_position_key(
                ACCOUNT_SUB, position.symbol, 0,
            ),
        )
    elif field == "identity_status":
        position = replace(position, identity_status="LEGACY_UNKNOWN",
                           trading_account_id=None,
                           strategy_execution_binding_id=None,
                           execution_leg=None)
    elif field == "source_refs":
        observation = replace(
            observation, source_refs=("untrusted:paper",),
        )
    elif field == "event_kind":
        observation = replace(observation, event_kind="PAPER_PROXY")
    elif field == "signed_source":
        observation = replace(
            observation, source_refs=("bybit:claimed-signed-but-unverified",),
        )
    else:
        details = observation.attributes.to_dict()
        details["p90l90_real"]["protection_exchange_confirmed"] = False
        observation = replace(
            observation, attributes=FrozenPolicy.from_mapping(details),
        )
    evaluation = replace(evaluation, observation=observation)
    with pytest.raises(mod.P90RealExitExecutionUnverified):
        sink(evaluation, position, plan)
    assert steps == []
    assert db.events == []


def test_missing_independent_execution_safety_is_constructor_hard_stop() -> None:
    db = _DB()
    with pytest.raises(
        mod.P90RealExitExecutionUnverified, match="SAFETY_PROVIDER_MISSING"
    ):
        mod.P90RealExitExecutionPort(
            db, independently_verified_execution_safety=None,
            account_by_strategy={MAIN_ID: ACCOUNT_MAIN, SUB_ID: ACCOUNT_SUB},
        )


def test_unmaterializable_non_decision_is_not_a_command(monkeypatch) -> None:
    sink, db, steps, position, plan, evaluation = _sink(monkeypatch)
    quiet = ExitEvaluation(
        evaluation_id="quiet-test", observation=evaluation.observation,
        status=ExitEvaluationStatus.NO_MATCH, reason="NO_MATCH",
    )
    assert sink(quiet, position, plan) is None
    assert steps == []
    assert db.events == []


def test_real_native_trade_requires_execution_sink_before_decision_persistence() -> None:
    position, plan, checkpoint, cursor, private, price = _scope()
    trade = MarketFactEnvelope(
        fact_id="native-test-verified-101",
        event_kind="PUBLIC_TRADE", symbol=cursor.symbol,
        event_at=cursor.traded_at, observed_at=cursor.traded_at,
        received_at=cursor.traded_at + timedelta(milliseconds=2),
        attributes=FrozenPolicy.from_mapping({"price": str(price)}),
        source_refs=("bybit:publicTrade:exact-native-101",),
    )
    observed: list[str] = []
    common = dict(
        position_provider=lambda _symbol: ((position, plan),),
        independently_verified_source_coverage=lambda _position, _cursor: (
            True, True,
        ),
        independently_signed_private_evidence=lambda _position, _cursor: private,
        source_commit="a" * 40,
        mfe_ledger=SimpleNamespace(on_trade=lambda **_kwargs: checkpoint),
        exit_store=SimpleNamespace(
            record=lambda *_a, **_k: observed.append("record"),
        ),
    )
    port = P90RealExitNativePort(**common)
    with pytest.raises(
        P90RealMfeUnverified, match="EXECUTION_OWNER_MISSING"
    ):
        port(trade, cursor)
    assert observed == []
    port = P90RealExitNativePort(
        **common,
        verified_execution_port=lambda _ev, _pos, _plan: observed.append(
            "dispatch"
        ),
    )
    result = port(trade, cursor)
    assert len(result) == 1
    assert result[0].status is ExitEvaluationStatus.DECISION_CREATED
    assert observed == ["record", "dispatch"]
