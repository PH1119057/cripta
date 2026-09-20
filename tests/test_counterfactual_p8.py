from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.counterfactual import (
    CounterfactualEconomicsStatus,
    CounterfactualOutcome,
    CounterfactualOutcomeStatus,
    build_insufficient_funds_candidate,
)
from bybit_workbench.universal_entry.contracts import (
    DataQuality,
    EntryDecisionCode,
    ExitPlan,
    TradingCapacitySnapshot,
)
from tests.test_universal_entry_architecture import (
    NOW,
    AcceptingAdmissionPort,
    RejectingCapitalAdmissionPort,
    capacity_policy,
    fact,
    make_card,
    real_evaluate,
    setup_engine,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src/bybit_workbench/counterfactual.py").read_text(encoding="utf-8")
OBSERVER = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")


def _insufficient_evaluation():
    card = make_card(
        "p8-insufficient",
        capital={**capacity_policy("50"), "amount_currency": "USDT"},
        execution={"max_request_age_seconds": 30},
    )
    registry, engine = setup_engine(card)
    capacity = TradingCapacitySnapshot(
        "p8-capacity",
        NOW,
        Decimal("20"),
        DataQuality.HIGH,
        "exchange:test",
    )
    evaluation = real_evaluate(
        engine,
        fact(1),
        capacity=capacity,
        admission_port=RejectingCapitalAdmissionPort(),
    )[0]
    entry_plan, exit_plan = registry.exact_plan_pair("act-0")
    return evaluation, entry_plan, exit_plan


def test_insufficient_capital_creates_exact_counterfactual_candidate() -> None:
    evaluation, entry_plan, exit_plan = _insufficient_evaluation()
    assert evaluation.decision.code is EntryDecisionCode.INSUFFICIENT_AVAILABLE_FUNDS
    assert evaluation.execution_request is None
    assert evaluation.decision.capital_reservation_id is None

    candidate = build_insufficient_funds_candidate(
        evaluation,
        entry_plan=entry_plan,
        exit_plan=exit_plan,
        captured_at=NOW,
    )
    assert candidate is not None
    assert candidate.strategy_activation_id == entry_plan.strategy_activation_id
    assert candidate.strategy_id == entry_plan.strategy_id
    assert candidate.strategy_version == entry_plan.strategy_version
    assert candidate.strategy_config_fingerprint == entry_plan.strategy_config_fingerprint
    assert candidate.entry_plan_fingerprint == entry_plan.entry_plan_fingerprint
    assert candidate.exit_plan_fingerprint == exit_plan.exit_plan_fingerprint
    assert candidate.signal_id == evaluation.signal.signal_id
    assert candidate.strategy_attempt_id == evaluation.attempt.strategy_attempt_id
    assert candidate.entry_decision_id == evaluation.decision.entry_decision_id
    assert candidate.requested_amount == Decimal("50")
    assert candidate.reported_available_amount == Decimal("20")
    assert candidate.amount_currency == "USDT"


def test_atomic_reservation_race_also_creates_counterfactual_candidate() -> None:
    card = make_card(
        "p8-race",
        capital={**capacity_policy("10"), "amount_currency": "USDT"},
        execution={"max_request_age_seconds": 30},
    )
    registry, engine = setup_engine(card)
    capacity = TradingCapacitySnapshot(
        "p8-race-capacity",
        NOW,
        Decimal("10"),
        DataQuality.HIGH,
        "exchange:test",
    )
    evaluation = real_evaluate(
        engine,
        fact(2),
        capacity=capacity,
        admission_port=RejectingCapitalAdmissionPort(),
    )[0]
    entry_plan, exit_plan = registry.exact_plan_pair("act-0")
    candidate = build_insufficient_funds_candidate(
        evaluation,
        entry_plan=entry_plan,
        exit_plan=exit_plan,
        captured_at=NOW,
    )
    assert candidate is not None
    assert candidate.reported_available_amount == Decimal("10")
    assert "atomic capital reservation failed" in candidate.decision_reason


def test_non_counterfactual_accepted_decision_never_becomes_counterfactual() -> None:
    card = make_card(
        "p8-accepted",
        capital={**capacity_policy("10"), "amount_currency": "USDT"},
        execution={"max_request_age_seconds": 30},
    )
    registry, engine = setup_engine(card)
    evaluation = real_evaluate(
        engine,
        fact(3),
        capacity=TradingCapacitySnapshot(
            "p8-accepted-capacity",
            NOW,
            Decimal("20"),
            DataQuality.HIGH,
            "exchange:test",
        ),
        admission_port=AcceptingAdmissionPort(),
    )[0]
    entry_plan, exit_plan = registry.exact_plan_pair("act-0")
    assert evaluation.decision is not None
    assert evaluation.decision.code is EntryDecisionCode.ACCEPTED
    assert (
        build_insufficient_funds_candidate(
            evaluation,
            entry_plan=entry_plan,
            exit_plan=exit_plan,
            captured_at=NOW,
        )
        is None
    )


def test_counterfactual_fails_closed_if_illegal_execution_or_reservation_exists() -> None:
    evaluation, entry_plan, exit_plan = _insufficient_evaluation()

    accepted_card = make_card(
        "p8-request-source",
        capital={**capacity_policy("10"), "amount_currency": "USDT"},
        execution={"max_request_age_seconds": 30},
    )
    _, accepted_engine = setup_engine(accepted_card)
    accepted = real_evaluate(
        accepted_engine,
        fact(4),
        capacity=TradingCapacitySnapshot(
            "p8-request-source-capacity",
            NOW,
            Decimal("20"),
            DataQuality.HIGH,
            "exchange:test",
        ),
        admission_port=AcceptingAdmissionPort(),
    )[0]
    assert accepted.execution_request is not None
    illegal_request = replace(
        evaluation,
        execution_request=accepted.execution_request,
    )
    with pytest.raises(
        RuntimeError,
        match="cannot have ExecutionRequest",
    ):
        build_insufficient_funds_candidate(
            illegal_request,
            entry_plan=entry_plan,
            exit_plan=exit_plan,
            captured_at=NOW,
        )

    illegal_reservation = replace(
        evaluation,
        decision=replace(
            evaluation.decision,
            capital_reservation_id="forbidden-reservation",
        ),
    )
    with pytest.raises(RuntimeError) as caught:
        build_insufficient_funds_candidate(
            illegal_reservation,
            entry_plan=entry_plan,
            exit_plan=exit_plan,
            captured_at=NOW,
        )
    assert "cannot keep capital reservation" in str(caught.value)


def test_counterfactual_cross_exit_plan_lineage_is_fail_closed() -> None:
    evaluation, entry_plan, exit_plan = _insufficient_evaluation()
    wrong_exit = ExitPlan(
        strategy_id="other-strategy",
        strategy_version=exit_plan.strategy_version,
        strategy_config_fingerprint=exit_plan.strategy_config_fingerprint,
        strategy_activation_id=exit_plan.strategy_activation_id,
        exit_plan_version=exit_plan.exit_plan_version,
        exit_plan_fingerprint="wrong-exit-plan",
        exit_policy=exit_plan.exit_policy,
        protection_policy=exit_plan.protection_policy,
    )
    with pytest.raises(RuntimeError, match="ExitPlan lineage mismatch"):
        build_insufficient_funds_candidate(
            evaluation,
            entry_plan=entry_plan,
            exit_plan=wrong_exit,
            captured_at=NOW,
        )


def test_counterfactual_outcome_economics_is_explicit_and_separate() -> None:
    outcome = CounterfactualOutcome.build(
        counterfactual_id="cf-1",
        status=CounterfactualOutcomeStatus.CLOSED,
        evaluated_at=NOW + timedelta(minutes=10),
        opened_at=NOW,
        entry_price=Decimal("10"),
        closed_at=NOW + timedelta(minutes=9),
        exit_price=Decimal("10.2"),
        exit_reason="EXACT_EXIT_PLAN_RULE",
        gross_pnl=Decimal("0.4"),
        fees=Decimal("0.02"),
        funding=None,
        slippage=Decimal("0.01"),
        net_pnl_after_fees=Decimal("0.38"),
        economics_status=CounterfactualEconomicsStatus.PARTIAL_NO_FUNDING,
    )
    assert outcome.status is CounterfactualOutcomeStatus.CLOSED
    assert outcome.net_pnl_after_fees == Decimal("0.38")
    assert outcome.funding is None
    assert outcome.economics_status is CounterfactualEconomicsStatus.PARTIAL_NO_FUNDING


def test_analyst_counterfactual_source_has_no_trading_path() -> None:
    for forbidden in (
        "execution_bridge",
        "from bybit_workbench.capital_reservation",
        "runtime.trade_commands",
        "strategy_entry.execution_requests",
        "/v5/order/create",
        "/v5/position/trading-stop",
        "api_post(",
        "reserve(",
    ):
        assert forbidden not in SOURCE


def test_observer_captures_counterfactual_only_for_real_execution_activation() -> None:
    start = OBSERVER.index("for evaluation in evaluations:")
    block = OBSERVER[start : start + 2600]
    assert "build_insufficient_funds_candidate(" in block
    assert "counterfactual_store.record_candidate(candidate)" in block
    assert "evaluation.signal.strategy_activation_id" in block
    assert "real_admission_required_for" in block
    assert block.index("real_admission_required_for") < block.index(
        "build_insufficient_funds_candidate("
    )
    assert "paper.create_order(" in block
    assert "if evaluation.execution_request is not None:" not in block
