from datetime import UTC, datetime
from pathlib import Path

from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    MarketFactEnvelope,
    StrategyActivation,
)
from bybit_workbench.universal_entry.dsl import (
    PlanState,
    evaluate_predicate,
    is_independent_touch,
)
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.r1_strategy import build_r1_cards


ROOT = Path(__file__).resolve().parents[1]


def _r1_entry_plan():
    card = build_r1_cards()[0]
    activation = StrategyActivation(
        activation_id="r1-touch-regression",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True,
        enabled_at=card.approved_at,
        scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
        operator="test",
        source="test",
    )
    entry_plan, _ = materialize_plans(card, activation)
    return card, entry_plan


def test_r1_derived_touch_matches_touch_predicate() -> None:
    card, entry_plan = _r1_entry_plan()
    now = datetime(2026, 10, 5, tzinfo=UTC)
    fact = MarketFactEnvelope(
        fact_id="r1-touch-regression",
        event_kind="R1_TOUCH",
        symbol=card.symbols[0],
        observed_at=now,
        event_at=now,
        received_at=now,
        source_refs=("test",),
        attributes=FrozenPolicy.from_mapping({"price": "1"}),
        direction=next(iter(entry_plan.directions)),
    )
    state = PlanState()
    independent = is_independent_touch(fact, state, entry_plan.touch_policy)
    assert independent is True
    assert evaluate_predicate(
        entry_plan.predicate,
        fact=fact,
        state=state,
        touch_policy=entry_plan.touch_policy,
        independent_touch=independent,
        sensors={},
        allowed_sensor_modes={},
        contexts={},
        allowed_context_modes={},
    ) is True
    state.observe(fact, independent_touch=independent)
    assert state.touch_count == 1
    assert state.last_touch_at == now


def test_unrelated_custom_touch_kind_remains_fail_closed() -> None:
    card, entry_plan = _r1_entry_plan()
    now = datetime(2026, 10, 5, tzinfo=UTC)
    fact = MarketFactEnvelope(
        fact_id="other-touch-regression",
        event_kind="OTHER_TOUCH",
        symbol=card.symbols[0],
        observed_at=now,
        event_at=now,
        received_at=now,
        source_refs=("test",),
        attributes=FrozenPolicy.from_mapping({"price": "1"}),
        direction=next(iter(entry_plan.directions)),
    )
    assert is_independent_touch(fact, PlanState(), entry_plan.touch_policy) is False


def test_real_entry_freshness_policy_is_explicit_in_observer_unit() -> None:
    source = (
        ROOT / "operations/systemd/cripta-universal-entry-observer.service"
    ).read_text(encoding="utf-8")
    assert "Environment=CRIPTA_REAL_ENTRY_ACCOUNT_STATE_MAX_AGE_SECONDS=15" in source
