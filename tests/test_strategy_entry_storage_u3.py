from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.capital_reservation import CapitalReservation, CapitalReservationState
from bybit_workbench.entry_admission import EntryAdmissionReceipt, EntryAdmissionRequest
from bybit_workbench.universal_entry import (
    ActivePlanRegistry,
    ContextFailureAction,
    ContextMode,
    ContextRequirement,
    DataQuality,
    FrozenPolicy,
    MarketFactEnvelope,
    ObjectiveContext,
    SensorObservation,
    SensorRequirement,
    StrategyActivation,
    StrategyCard,
    TechnicalReadiness,
    TouchPolicy,
    TradeDirection,
    TradingCapacitySnapshot,
    UniversalEntryEngine,
    StrategyExecutionBindingRef,
)
from bybit_workbench.universal_entry.contracts import EntryDecisionCode
from bybit_workbench.universal_entry.engine import AccountAdmissionContext
from bybit_workbench.universal_entry.storage import StrategyEntryStore
from operations.dashboard import archive_v2

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "operations/sql/20260908_strategy_entry_u3.sql"
NOW = datetime(2026, 9, 8, 13, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, rowcount: int = 1) -> None:
        self._rowcount = rowcount

    @property
    def rowcount(self) -> int:
        return self._rowcount


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.transactions = 0

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> FakeCursor:
        self.statements.append((statement, parameters))
        return FakeCursor()

    @contextmanager
    def transaction(self) -> Iterator[object]:
        self.transactions += 1
        yield object()


class AcceptingAdmissionPort:
    def admit(self, request: EntryAdmissionRequest) -> EntryAdmissionReceipt:
        account = request.trading_account_id
        reservation = CapitalReservation(
            reservation_id=("cap-storage" if account == "ta-storage" else f"cap-{account}"),
            account_ref=request.account_ref,
            strategy_attempt_id=request.strategy_attempt_id,
            requested_amount=request.requested_amount,
            amount_currency=request.amount_currency,
            capacity_snapshot_id=request.capacity_snapshot_id,
            state=CapitalReservationState.RESERVED,
            created_at=request.requested_at,
            updated_at=request.requested_at,
            pre_dispatch_expires_at=request.pre_dispatch_expires_at,
            state_reason="TEST",
        )
        return EntryAdmissionReceipt(
            exchange_position_slot_claim_id=(
                "slot-storage" if account == "ta-storage" else f"slot-{account}"
            ),
            exchange_position_key=request.exchange_position_key,
            position_mode_state_ref=(
                "pmode-storage" if account == "ta-storage" else f"pmode-{account}"
            ),
            position_idx=0,
            capital_reservation=reservation,
        )


def policy(payload: dict[str, object] | None = None) -> FrozenPolicy:
    return FrozenPolicy.from_mapping(payload or {})


def strategy_card() -> StrategyCard:
    requirement = ContextRequirement(
        "dispatcher.coin",
        ContextMode.CONDITION,
        max_age_seconds=30,
        min_quality=DataQuality.MEDIUM,
        on_missing=ContextFailureAction.REJECT_SIGNAL,
        on_stale=ContextFailureAction.REJECT_SIGNAL,
        on_partial=ContextFailureAction.REJECT_SIGNAL,
    )
    sensor = SensorRequirement(
        "flow.delta",
        ContextMode.CONDITION,
        max_age_seconds=30,
        min_quality=DataQuality.MEDIUM,
        on_missing=ContextFailureAction.REJECT_SIGNAL,
        on_stale=ContextFailureAction.REJECT_SIGNAL,
        on_partial=ContextFailureAction.REJECT_SIGNAL,
    )
    return StrategyCard.build(
        strategy_id="storage-test",
        strategy_version="1.0.0",
        name="storage test",
        description="storage lineage test",
        scope=policy({"symbols": ["XRPUSDT"]}),
        symbols=("XRPUSDT",),
        direction_policy=(TradeDirection.LONG,),
        entry_policy=policy(
            {
                "entry_plan_version": "1",
                "predicate": {
                    "op": "AND",
                    "children": [
                        {"op": "TOUCH"},
                        {
                            "op": "COMPARE",
                            "params": {
                                "path": "context.dispatcher.coin.score",
                                "comparator": "GTE",
                                "value": 1,
                            },
                        },
                        {
                            "op": "COMPARE",
                            "params": {
                                "path": "sensor.flow.delta.value",
                                "comparator": "GT",
                                "value": 0,
                            },
                        },
                    ],
                },
                "watch_policy": {"enabled": False},
                "execution_policy": {
                    "order_type": "MARKET",
                    "reference_value_path": "fact.entry_price",
                    "max_request_age_seconds": 30,
                },
            }
        ),
        exit_policy=policy({"exit_plan_version": "1"}),
        capital_policy=policy(
            {
                "require_capacity": True,
                "requested_amount": "10",
                "amount_currency": "USDT",
                "leverage": 1,
                "capacity_max_age_seconds": 30,
                "capacity_min_quality": "MEDIUM",
            }
        ),
        protection_policy=policy({}),
        lifecycle_policy=policy({"post_signal_outcome_policy": {"enabled": False}}),
        touch_policy=TouchPolicy(accepted_touch_numbers=(1,)),
        market_sensor_policy=(sensor,),
        dispatcher_context_policy=(requirement,),
        approved_at=NOW,
    )


def build_evaluation(
    *, real: bool = False, fanout: bool = False,
    partial: bool = False, all_unarmed: bool = False,
):
    card = strategy_card()
    activation = StrategyActivation(
        activation_id="act-storage",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True,
        enabled_at=NOW,
    )
    registry = ActivePlanRegistry()
    registry.register_card(card)
    entry_plan = registry.activate(activation)
    fact = MarketFactEnvelope(
        fact_id="market-fact-1",
        event_kind="TOUCH",
        symbol="XRPUSDT",
        observed_at=NOW,
        event_at=NOW,
        received_at=NOW,
        source_refs=("trade:exact:1", "zone:exact:1"),
    )
    sensor = SensorObservation(
        sensor_id="flow.delta",
        observed_at=NOW,
        quality=DataQuality.HIGH,
        completeness="COMPLETE",
        payload=policy({"value": "2"}),
        source_refs=("trade-window:exact:1",),
    )
    context = ObjectiveContext(
        context_id="DCMC-exact-snapshot-1",
        context_type="DISPATCHER_COIN_CONTEXT",
        observed_at=NOW,
        quality=DataQuality.HIGH,
        completeness="COMPLETE",
        payload=policy({"score": 2}),
        source_refs=("dispatcher-row:exact:1",),
    )
    engine = UniversalEntryEngine(registry)
    kwargs: dict[str, object] = {
        "sensors": {"flow.delta": sensor},
        "contexts": {"dispatcher.coin": context},
        "technical_readiness": TechnicalReadiness(True, NOW),
    }
    if real:
        kwargs.update(
            {
                "capacity": TradingCapacitySnapshot(
                    "cap-storage-snapshot",
                    NOW,
                    Decimal("100"),
                    DataQuality.HIGH,
                    "exchange:test",
                ),
                "account_ref": "BYBIT:UNIFIED",
                "entry_admission_port": AcceptingAdmissionPort(),
                "real_admission_required_for": frozenset({"act-storage"}),
                "execution_bindings": {"act-storage": StrategyExecutionBindingRef("binding-storage", "act-storage", "PRIMARY", "ta-storage")},
                "exchange_position_keys": {
                    "XRPUSDT": "BYBIT:UNIFIED:LINEAR:USDT:XRPUSDT:0"
                },
            }
        )
    if fanout:
        kwargs.pop("execution_bindings", None)
        kwargs.pop("exchange_position_keys", None)
        kwargs.pop("entry_admission_port", None)
        kwargs["account_admissions"] = {
            "act-storage": (
                AccountAdmissionContext(
                    binding=StrategyExecutionBindingRef(
                        "binding-one", "act-storage", "LEG_A", "ta-one"
                    ),
                    capacity=TradingCapacitySnapshot(
                        "snapshot-one", NOW, Decimal("100"), DataQuality.HIGH,
                        "runtime.account_state_generations:g-one",
                    ),
                    technical_readiness=TechnicalReadiness(
                        not all_unarmed, NOW,
                        "MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN" if all_unarmed else "",
                    ),
                    account_ref="BYBIT:UNIFIED",
                    exchange_position_key="ta-one:LINEAR:XRPUSDT:0",
                    entry_admission_port=(
                        None if all_unarmed else AcceptingAdmissionPort()
                    ),
                ),
                AccountAdmissionContext(
                    binding=StrategyExecutionBindingRef(
                        "binding-two", "act-storage", "LEG_B", "ta-two"
                    ),
                    capacity=TradingCapacitySnapshot(
                        "snapshot-two", NOW, Decimal("100"), DataQuality.HIGH,
                        "runtime.account_state_generations:g-two",
                    ),
                    technical_readiness=TechnicalReadiness(
                        not partial and not all_unarmed,
                        NOW,
                        (
                            "MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN"
                            if all_unarmed
                            else ("SECOND_ACCOUNT_NOT_ARM_READY" if partial else "")
                        ),
                    ),
                    account_ref="BYBIT:UNIFIED",
                    exchange_position_key="ta-two:LINEAR:XRPUSDT:0",
                    entry_admission_port=(
                        None if all_unarmed else AcceptingAdmissionPort()
                    ),
                ),
            )
        }
    result = engine.process(fact, **kwargs)
    assert len(result) == 1
    return card, activation, entry_plan, result[0]


def test_u3_sql_declares_separate_schema_and_required_tables() -> None:
    source = SQL.read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS strategy_entry AUTHORIZATION postgres" in source
    required = {
        "strategy_cards",
        "strategy_activations",
        "strategy_activation_events",
        "entry_plans",
        "exit_plans",
        "strategy_signals",
        "strategy_attempts",
        "entry_decisions",
        "context_links",
        "sensor_links",
        "execution_requests",
        "notifications",
        "shadow_parity_runs",
        "shadow_parity_events",
    }
    for table in required:
        assert f"CREATE TABLE IF NOT EXISTS strategy_entry.{table}" in source


def test_u3_sql_makes_facts_immutable_and_activation_narrowly_mutable() -> None:
    source = SQL.read_text(encoding="utf-8")
    assert "strategy_entry.reject_immutable_change()" in source
    assert "StrategyActivation identity/scope is immutable" in source
    assert "GRANT SELECT, INSERT, UPDATE ON strategy_entry.strategy_activations TO cripta" in source
    assert "REVOKE DELETE ON ALL TABLES IN SCHEMA strategy_entry FROM cripta" in source
    assert "REVOKE UPDATE ON" in source
    assert "strategy_entry.strategy_signals" in source
    assert "strategy_entry.entry_decisions" in source


def test_u3_context_schema_distinguishes_observed_and_consumed() -> None:
    source = SQL.read_text(encoding="utf-8")
    assert "'OBSERVED_CONTEXT'" in source
    assert "'CONSUMED_CONTEXT'" in source
    assert "requirement_id text NOT NULL" in source
    assert "context_id text" in source
    assert "CONSUMED_CONTEXT' AND strategy_attempt_id IS NOT NULL" not in source
    assert "AND context_id IS NOT NULL" in source


def test_u3_exact_attempt_foreign_key_carries_strategy_lineage() -> None:
    source = SQL.read_text(encoding="utf-8")
    expected = """FOREIGN KEY (
        signal_id, strategy_id, strategy_version, strategy_config_fingerprint,
        entry_plan_fingerprint, strategy_activation_id
    ) REFERENCES strategy_entry.strategy_signals("""
    assert expected in source
    assert "FOREIGN KEY (strategy_attempt_id, signal_id)" in source


def test_u3_signal_foreign_keys_prevent_cross_strategy_plan_or_activation_mix() -> None:
    source = SQL.read_text(encoding="utf-8")
    assert (
        """FOREIGN KEY (
        entry_plan_fingerprint, strategy_id, strategy_version,
        strategy_config_fingerprint
    ) REFERENCES strategy_entry.entry_plans("""
        in source
    )
    assert (
        """FOREIGN KEY (
        strategy_activation_id, strategy_id, strategy_version,
        strategy_config_fingerprint
    ) REFERENCES strategy_entry.strategy_activations("""
        in source
    )
    assert (
        "UNIQUE NULLS NOT DISTINCT (signal_id, strategy_attempt_id, link_type, sensor_id)" in source
    )


def test_plan_storage_json_is_policy_only_and_excludes_activation_identity() -> None:
    card, activation, entry_plan, _ = build_evaluation()
    connection = FakeConnection()
    store = StrategyEntryStore(connection)
    store.insert_strategy_card(card)
    store.insert_activation(activation)
    store.insert_entry_plan(entry_plan)
    plan_insert = next(
        params
        for statement, params in connection.statements
        if "INSERT INTO strategy_entry.entry_plans" in statement
    )
    payload = str(plan_insert[5])
    assert '"entry_plan_fingerprint"' in payload
    assert '"strategy_activation_id"' not in payload
    assert activation.activation_id not in payload


def test_store_persists_shadow_signal_attempt_and_dual_context_links_without_real_decision(
) -> None:
    card, activation, entry_plan, evaluation = build_evaluation()
    connection = FakeConnection()
    store = StrategyEntryStore(connection)
    store.insert_strategy_card(card)
    store.insert_activation(activation)
    store.insert_entry_plan(entry_plan)
    store.record_evaluation(evaluation, provenance=policy({"test": "u3-shadow"}))

    statements = "\n".join(statement for statement, _ in connection.statements)
    assert "strategy_entry.strategy_cards" in statements
    assert "strategy_entry.strategy_activations" in statements
    assert "strategy_entry.entry_plans" in statements
    assert "strategy_entry.strategy_signals" in statements
    assert "strategy_entry.strategy_attempts" in statements
    assert "strategy_entry.entry_decisions" not in statements
    assert "strategy_entry.execution_requests" not in statements
    assert statements.count("strategy_entry.context_links") == 2
    assert statements.count("strategy_entry.sensor_links") == 2
    assert "CONSUMED_CONTEXT" in statements
    assert "OBSERVED_CONTEXT" in statements
    assert connection.transactions == 1
    assert evaluation.decision is None
    assert evaluation.execution_request is None

    signal_insert = next(
        params
        for statement, params in connection.statements
        if "INSERT INTO strategy_entry.strategy_signals" in statement
    )
    assert signal_insert[0] == evaluation.signal.signal_id
    assert signal_insert[4] == evaluation.signal.entry_plan_fingerprint
    assert signal_insert[5] == evaluation.signal.strategy_activation_id

    attempt_insert = next(
        params
        for statement, params in connection.statements
        if "INSERT INTO strategy_entry.strategy_attempts" in statement
    )
    assert attempt_insert[0] == evaluation.attempt.strategy_attempt_id
    assert attempt_insert[1] == evaluation.signal.signal_id


def test_store_persists_real_decision_request_and_admission_lineage() -> None:
    card, activation, entry_plan, evaluation = build_evaluation(real=True)
    assert evaluation.decision is not None
    assert evaluation.execution_request is not None
    connection = FakeConnection()
    store = StrategyEntryStore(connection)
    store.insert_strategy_card(card)
    store.insert_activation(activation)
    store.insert_entry_plan(entry_plan)
    store.record_evaluation(evaluation, provenance=policy({"test": "u3-real"}))

    statements = "\n".join(statement for statement, _ in connection.statements)
    assert "strategy_entry.entry_decisions" in statements
    assert "strategy_entry.execution_requests" in statements
    assert "strategy_entry.execution_request_state_events" in statements

    decision_insert = next(
        params
        for statement, params in connection.statements
        if "INSERT INTO strategy_entry.entry_decisions" in statement
    )
    assert "cap-storage" in decision_insert
    assert "slot-storage" in decision_insert
    assert "pmode-storage" in decision_insert

    request_insert = next(
        params
        for statement, params in connection.statements
        if "INSERT INTO strategy_entry.execution_requests" in statement
    )
    assert "cap-storage" in request_insert
    assert "slot-storage" in request_insert
    assert "pmode-storage" in request_insert



def test_same_causal_fact_produces_identical_paper_real_entry_intent() -> None:
    """Compare actual engine evaluations, not a source-code string or SHA alone."""
    paper_card, paper_activation, paper_plan, paper_eval = build_evaluation(real=False)
    real_card, real_activation, real_plan, real_eval = build_evaluation(real=True)

    assert paper_card.strategy_config_fingerprint == real_card.strategy_config_fingerprint
    assert paper_activation.strategy_id == real_activation.strategy_id
    assert paper_plan.entry_plan_fingerprint == real_plan.entry_plan_fingerprint

    paper = paper_eval.execution_intent
    real = real_eval.execution_intent
    for attribute in (
        "strategy_id",
        "strategy_version",
        "strategy_config_fingerprint",
        "entry_plan_fingerprint",
        "exit_plan_fingerprint",
        "symbol",
        "direction",
        "observed_at",
        "payload",
    ):
        assert getattr(paper, attribute) == getattr(real, attribute), attribute
    assert paper_eval.signal.direction == real_eval.signal.direction
    assert paper_eval.signal.strategy_id == real_eval.signal.strategy_id
    assert paper_eval.signal.strategy_config_fingerprint == real_eval.signal.strategy_config_fingerprint
    assert paper_eval.execution_request is None
    assert paper_eval.decision is None
    assert real_eval.execution_request is not None
    assert real_eval.decision is not None
    assert real_eval.execution_request.strategy_execution_binding_id == "binding-storage"
    assert real_eval.execution_request.trading_account_id == "ta-storage"



def test_store_keeps_context_requirement_key_separate_from_snapshot_id() -> None:
    _, _, _, evaluation = build_evaluation()
    link = evaluation.context_links[0]
    assert link.requirement_id == "dispatcher.coin"
    assert link.context_id == "DCMC-exact-snapshot-1"
    assert link.source_refs == ("dispatcher-row:exact:1",)


def test_store_observed_context_api_requires_exact_signal_and_snapshot_ids() -> None:
    connection = FakeConnection()
    store = StrategyEntryStore(connection)
    context = ObjectiveContext(
        context_id="DGC-exact-2",
        context_type="DISPATCHER_GLOBAL_CONTEXT",
        observed_at=NOW,
        quality=DataQuality.HIGH,
        completeness="COMPLETE",
        payload=policy({}),
        source_refs=("dispatcher-global-row:2",),
    )
    link_id = store.record_observed_context(
        signal_id="sig-exact-2",
        requirement_id="dispatcher.global",
        context=context,
        linked_at=NOW,
        age_seconds=0.0,
        status="FRESH",
        provenance=policy({"correlator": "exact-id-only"}),
    )
    assert link_id.startswith("ctx-")
    statement, params = connection.statements[-1]
    assert "OBSERVED_CONTEXT" in statement
    assert params[1] == "sig-exact-2"
    assert params[2] == "dispatcher.global"
    assert params[4] == "DGC-exact-2"


def test_compact_archive_exports_every_strategy_entry_table() -> None:
    tables = {item.table for item in archive_v2.TABLE_EXPORTS}
    assert {
        "strategy_entry.strategy_cards",
        "strategy_entry.strategy_activations",
        "strategy_entry.strategy_activation_events",
        "strategy_entry.entry_plans",
        "strategy_entry.exit_plans",
        "strategy_entry.strategy_signals",
        "strategy_entry.strategy_attempts",
        "strategy_entry.entry_decisions",
        "strategy_entry.context_links",
        "strategy_entry.sensor_links",
        "strategy_entry.execution_requests",
        "strategy_entry.notifications",
        "strategy_entry.shadow_parity_runs",
        "strategy_entry.shadow_parity_events",
    } <= tables


def test_archive_manifest_marks_strategy_entry_u3_schema_version() -> None:
    assert archive_v2.DATABASE_SCHEMA_VERSION.endswith("/strategy-entry-u3")
    legacy = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
    assert "strategy-entry-u3" in legacy
    assert '"strategy_entry.strategy_signals"' in legacy
    assert '"strategy_entry.context_links"' in legacy


def test_storage_source_has_no_nearest_time_or_exchange_mutation() -> None:
    source = (ROOT / "src/bybit_workbench/universal_entry/storage.py").read_text(encoding="utf-8")
    forbidden = (
        "ORDER BY ABS",
        "nearest",
        "symbol + time",
        "place_order",
        "cancel_order",
        "runtime.trade_commands",
        "bybit",
    )
    for token in forbidden:
        assert token not in source


def test_fanout_one_market_signal_two_account_admissions_persist_once() -> None:
    card, activation, entry_plan, evaluation = build_evaluation(real=True, fanout=True)
    assert evaluation.decision is None
    assert evaluation.execution_request is None
    assert len(evaluation.account_outcomes) == 2
    one, two = evaluation.account_outcomes
    assert one.decision.code is two.decision.code is EntryDecisionCode.ACCEPTED
    assert one.binding.trading_account_id != two.binding.trading_account_id
    assert one.decision.entry_decision_id != two.decision.entry_decision_id
    assert one.execution_request is not None
    assert two.execution_request is not None
    assert one.execution_request.execution_request_id != two.execution_request.execution_request_id
    assert one.execution_request.strategy_attempt_id == two.execution_request.strategy_attempt_id
    assert one.execution_request.signal_id == two.execution_request.signal_id
    assert (
        one.execution_request.capital_reservation_id
        != two.execution_request.capital_reservation_id
    )
    assert (
        one.execution_request.exchange_position_slot_claim_id
        != two.execution_request.exchange_position_slot_claim_id
    )
    connection = FakeConnection()
    store = StrategyEntryStore(connection)
    store.insert_strategy_card(card)
    store.insert_activation(activation)
    store.insert_entry_plan(entry_plan)
    connection.statements.clear()
    store.record_evaluation(evaluation)
    sql = [statement for statement, _ in connection.statements]
    assert sum("INSERT INTO strategy_entry.strategy_signals" in item for item in sql) == 1
    assert sum("INSERT INTO strategy_entry.strategy_attempts" in item for item in sql) == 1
    assert sum("INSERT INTO strategy_entry.entry_decisions" in item for item in sql) == 2
    assert sum("INSERT INTO strategy_entry.execution_requests" in item for item in sql) == 2
    assert sum(
        "INSERT INTO strategy_entry.execution_request_state_events" in item for item in sql
    ) == 2
    assert sum("INSERT INTO strategy_entry.notifications" in item for item in sql) == 0
    assert connection.transactions == 1


def test_fanout_one_account_not_ready_does_not_erase_other_account() -> None:
    card, activation, entry_plan, evaluation = build_evaluation(
        real=True, fanout=True, partial=True
    )
    one, two = evaluation.account_outcomes
    assert one.decision.code is EntryDecisionCode.ACCEPTED
    assert one.execution_request is not None
    assert two.decision.code is EntryDecisionCode.OPERATIONAL_SAFETY_BLOCKED
    assert "SECOND_ACCOUNT_NOT_ARM_READY" in two.decision.reason
    assert two.execution_request is None
    assert one.decision.entry_decision_id != two.decision.entry_decision_id
    connection = FakeConnection()
    store = StrategyEntryStore(connection)
    store.insert_strategy_card(card)
    store.insert_activation(activation)
    store.insert_entry_plan(entry_plan)
    connection.statements.clear()
    store.record_evaluation(evaluation)
    sql = [stmt for stmt, _params in connection.statements]
    assert sum("INSERT INTO strategy_entry.strategy_signals" in item for item in sql) == 1
    assert sum("INSERT INTO strategy_entry.entry_decisions" in item for item in sql) == 2
    assert sum("INSERT INTO strategy_entry.execution_requests" in item for item in sql) == 1


def test_fanout_rejects_cross_account_request_before_any_storage_write() -> None:
    _card, _activation, _plan, evaluation = build_evaluation(real=True, fanout=True)
    one, two = evaluation.account_outcomes
    assert one.execution_request is not None
    forged = replace(
        two,
        execution_request=replace(
            one.execution_request, entry_decision_id=two.decision.entry_decision_id
        ),
    )
    connection = FakeConnection()
    with pytest.raises(ValueError, match="REAL_FANOUT_REQUEST_ATTRIBUTION_MISMATCH"):
        StrategyEntryStore(connection).record_evaluation(
            replace(evaluation, account_outcomes=(one, forged))
        )
    assert connection.statements == []
    assert connection.transactions == 0


def test_fanout_rejects_duplicate_account_before_any_storage_write() -> None:
    _card, _activation, _plan, evaluation = build_evaluation(real=True, fanout=True)
    one, _two = evaluation.account_outcomes
    connection = FakeConnection()
    with pytest.raises(ValueError, match="REAL_FANOUT_DUPLICATED_ACCOUNT_OUTCOME"):
        StrategyEntryStore(connection).record_evaluation(
            replace(evaluation, account_outcomes=(one, one))
        )
    assert not connection.statements



def test_unarmed_real_fanout_records_two_denials_and_never_creates_requests() -> None:
    _card, _activation, _entry_plan, evaluation = build_evaluation(
        real=True, fanout=True, all_unarmed=True
    )
    assert evaluation.decision is None
    assert evaluation.execution_request is None
    assert len(evaluation.account_outcomes) == 2
    first, second = evaluation.account_outcomes
    assert first.decision.entry_decision_id != second.decision.entry_decision_id
    assert all(
        outcome.decision.code is EntryDecisionCode.OPERATIONAL_SAFETY_BLOCKED
        and outcome.decision.reason == "MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN"
        and outcome.execution_request is None
        and len(outcome.notifications) == 1
        for outcome in evaluation.account_outcomes
    )
    assert first.notifications[0].notification_id != second.notifications[0].notification_id
    connection = FakeConnection()
    StrategyEntryStore(connection).record_evaluation(evaluation)
    sql = [statement for statement, _ in connection.statements]
    assert sum("INSERT INTO strategy_entry.strategy_signals" in stmt for stmt in sql) == 1
    assert sum("INSERT INTO strategy_entry.strategy_attempts" in stmt for stmt in sql) == 1
    assert sum("INSERT INTO strategy_entry.entry_decisions" in stmt for stmt in sql) == 2
    assert sum("INSERT INTO strategy_entry.notifications" in stmt for stmt in sql) == 2
    assert not any("INSERT INTO strategy_entry.execution_requests" in stmt for stmt in sql)
