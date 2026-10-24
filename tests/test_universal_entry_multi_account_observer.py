from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bybit_workbench.strategy_position_binding import exchange_position_key
from bybit_workbench.universal_entry import (
    ActivePlanRegistry,
    MarketFactEnvelope,
    StrategyExecutionBindingRef,
    UniversalEntryEngine,
)
from bybit_workbench.universal_entry.engine import AccountAdmissionContext
from operations.monitoring.universal_entry_shadow import (
    _blocked_multi_account_admissions,
    _unarmed_multi_account_candidate_bindings,
)

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


class Rows:
    def __init__(self, rows: list[tuple[str, str, str, str]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[str, str, str, str]]:
        return self.rows


class CandidateDB:
    def __init__(self, rows: list[tuple[str, str, str, str]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> Rows:
        self.queries.append((sql, params))
        return Rows(self.rows)


class Activation:
    def __init__(self, activation_id: str) -> None:
        self.activation_id = activation_id


class Bundle:
    def __init__(self, activation_id: str) -> None:
        self.activation = Activation(activation_id)


def binding(number: int, activation: str = "act-one") -> StrategyExecutionBindingRef:
    return StrategyExecutionBindingRef(
        f"binding-{number}",
        activation,
        f"LEG_{number}",
        f"trading-account-{number}",
    )


def test_live_observer_sees_every_active_account_without_creating_authority() -> None:
    one, two, three = binding(1), binding(2), binding(3, "act-single")
    rows = [
        (item.strategy_execution_binding_id, item.strategy_activation_id,
         item.execution_leg, item.trading_account_id)
        for item in (one, two, three)
    ]
    db = CandidateDB(rows)
    result = _unarmed_multi_account_candidate_bindings(
        db, (Bundle("act-one"), Bundle("act-single")),
        observed_at=NOW,
    )
    assert result == {"act-one": (one, two)}
    assert len(db.queries) == 1
    query, params = db.queries[0]
    assert "strategy_entry.strategy_execution_bindings" in query
    assert "effective_from<=%s" in query
    assert "binding_state='ACTIVE'" in query
    assert "execution_leg='PRIMARY'" not in query
    assert "trading_account_id=%s" not in query
    assert params == (NOW, NOW)
    assert query.lstrip().startswith("SELECT")


def test_unarmed_multi_account_bindings_are_blocked_per_leg_no_admission_port() -> None:
    one, two = binding(1), binding(2)
    candidates = {"act-one": (one, two)}
    prepared = _blocked_multi_account_admissions(
        candidates, symbol="BTCUSDT", observed_at=NOW
    )
    assert set(prepared) == {"act-one"}
    assert len(prepared["act-one"]) == 2
    for proof, ref in zip(prepared["act-one"], (one, two), strict=True):
        assert isinstance(proof, AccountAdmissionContext)
        assert proof.binding == ref
        assert proof.entry_admission_port is None
        assert proof.capacity is None
        assert not proof.technical_readiness.ready
        assert proof.technical_readiness.reason == "MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN"
        assert proof.exchange_position_key == exchange_position_key(
            ref.trading_account_id, "BTCUSDT", 0
        )
    assert len({x.exchange_position_key for x in prepared["act-one"]}) == 2


def test_empty_or_single_binding_never_enters_multi_account_route() -> None:
    only = binding(1)
    db = CandidateDB([
        (
            only.strategy_execution_binding_id,
            only.strategy_activation_id,
            only.execution_leg,
            only.trading_account_id,
        )
    ])
    assert _unarmed_multi_account_candidate_bindings(
        db, (Bundle("act-one"),), observed_at=NOW
    ) == {}


def test_legacy_and_fanout_preflight_allow_only_disjoint_activations() -> None:
    one, two = binding(1), binding(2)
    ctx = _blocked_multi_account_admissions(
        {"act-one": (one, two)}, symbol="BTCUSDT", observed_at=NOW
    )
    engine = UniversalEntryEngine(ActivePlanRegistry())
    fact = MarketFactEnvelope(
        fact_id="sample",
        event_kind="TOUCH",
        symbol="BTCUSDT",
        observed_at=NOW,
        event_at=NOW,
        received_at=NOW,
        source_refs=("test:isolated",),
    )
    # Unregistered strategy plans cannot cause an Entry in either path.
    assert engine.process(
        fact,
        account_admissions=ctx,
        execution_bindings={"act-other": binding(3, "act-other")},
        real_admission_required_for=frozenset({"act-one", "act-other"}),
    ) == ()
    with pytest.raises(ValueError, match="REAL_FANOUT_AND_LEGACY_BINDING_ACTIVATION_OVERLAP"):
        engine.process(
            fact,
            account_admissions=ctx,
            execution_bindings={"act-one": one},
        )


def test_unarmed_candidate_requires_aware_snapshot() -> None:
    with pytest.raises(ValueError, match="aware timestamp"):
        _unarmed_multi_account_candidate_bindings(
            CandidateDB([]), (Bundle("act-one"),),
            observed_at=datetime(2026, 10, 24),
        )
