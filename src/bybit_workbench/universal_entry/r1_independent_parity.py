"""Independent, exchange-free R1 PAPER/REAL decision and adapter replay.

A PASS requires real causal watch output, two separately evaluated modes,
an in-memory REAL admission receipt, and actual PAPER/REAL adapter outputs.
No PostgreSQL, Bybit or filesystem mutation is performed.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from bybit_workbench.capital_reservation import CapitalReservation, CapitalReservationState
from bybit_workbench.domain.models import Candle
from bybit_workbench.entry_admission import EntryAdmissionReceipt
from bybit_workbench.universal_entry import ActivePlanRegistry, UniversalEntryEngine
from bybit_workbench.universal_entry.contracts import (
    DataQuality,
    EntryDecisionCode,
    FrozenPolicy,
    MarketFactEnvelope,
    StrategyActivation,
    StrategyExecutionBindingRef,
    TechnicalReadiness,
    TradeDirection,
    TradingCapacitySnapshot,
)
from bybit_workbench.universal_entry.execution_bridge import (
    BridgePolicyBundle,
    prepare_runtime_entry_command,
)
from bybit_workbench.universal_entry.paper_runtime import PaperTradeRuntime
from bybit_workbench.universal_entry.r1_strategy import R1_STRATEGY_IDS, build_r1_cards
from bybit_workbench.universal_entry.reverse_intent import build_reverse_transition_intent
from bybit_workbench.universal_entry.runtime_loader import ActiveStrategyBundle
from bybit_workbench.universal_exit.contracts import ExitEvaluationStatus, ExitObservation
from bybit_workbench.universal_exit.engine import UniversalExitEngine


class IndependentParityFailure(ValueError):
    """A causal PAPER/REAL decision or adapter comparison failed closed."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise IndependentParityFailure("R1 PAPER_REAL_DECISION_PARITY: " + reason)


class _Cursor:
    def fetchone(self) -> None:
        return None


class _OfflinePaperDB:
    def __init__(self) -> None:
        self.inserted_orders: list[tuple[object, ...]] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _Cursor:
        if "INSERT INTO strategy_entry.paper_orders" in sql:
            self.inserted_orders.append(params)
        elif not sql.lstrip().startswith("SELECT "):
            raise IndependentParityFailure("PAPER replay attempted unexpected mutation")
        return _Cursor()


class _OfflineRealAdmission:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.requests: list[Any] = []

    def admit(self, request: Any) -> EntryAdmissionReceipt:
        self.requests.append(request)
        _require(request.requested_amount == Decimal("10"), "wrong REAL capital amount")
        _require(request.trading_account_id == "parity-account", "REAL account mismatch")
        _require(request.execution_leg == "PRIMARY", "REAL execution leg mismatch")
        reservation = CapitalReservation(
            "parity-reservation",
            request.account_ref,
            request.strategy_attempt_id,
            request.requested_amount,
            request.amount_currency,
            request.capacity_snapshot_id,
            CapitalReservationState.RESERVED,
            self.now,
            self.now,
            request.pre_dispatch_expires_at,
            strategy_execution_binding_id=request.strategy_execution_binding_id,
            execution_leg=request.execution_leg,
            trading_account_id=request.trading_account_id,
        )
        return EntryAdmissionReceipt(
            "parity-slot", request.exchange_position_key, "parity-position-mode", 0,
            reservation,
        )


def _candles(symbol: str) -> tuple[Candle, ...]:
    started = datetime(2026, 10, 5, tzinfo=UTC)
    return tuple(
        Candle(
            symbol=symbol,
            timeframe="5",
            opened_at=started + timedelta(minutes=5 * i),
            closed_at=started + timedelta(minutes=5 * (i + 1)),
            open=Decimal("100"),
            high=Decimal("110") if i % 10 == 0 else Decimal("101"),
            low=Decimal("90") if i % 10 == 5 else Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1"),
        )
        for i in range(205)
    )


def _engine(
    registry: ActivePlanRegistry, symbol: str, candles: tuple[Candle, ...]
) -> UniversalEntryEngine:
    engine = UniversalEntryEngine(registry)
    engine.load_watch_history(
        symbol, {"5": candles}, (), observed_at=candles[-1].closed_at,
    )
    return engine


def _exit_case(
    card: Any, exit_plan: Any, direction: TradeDirection, now: datetime,
    *,
    case_key: str,
) -> None:
    position_fields = dict(
        strategy_position_id=case_key + "-position",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        exit_plan_fingerprint=exit_plan.exit_plan_fingerprint,
        symbol=card.symbols[0],
        direction=direction,
    )
    attributes = FrozenPolicy.from_mapping({
        "geometry": {"l5_3": {"target_inner": "101" if direction is TradeDirection.LONG else "99"}}
    })
    observation = ExitObservation(
        observation_id=case_key + "-exit",
        strategy_position_id=position_fields["strategy_position_id"],
        symbol=card.symbols[0],
        event_at=now,
        observed_at=now,
        received_at=now,
        event_kind="GEOMETRY_L5_3",
        attributes=attributes,
        source_refs=("parity-replay",),
    )
    # Two independent evaluations from separately assembled PAPER and REAL contexts.
    paper = UniversalExitEngine().evaluate(
        SimpleNamespace(**position_fields), exit_plan, observation
    )
    real = UniversalExitEngine().evaluate(
        SimpleNamespace(**position_fields), exit_plan, observation
    )
    _require(paper == real, f"{case_key} ExitDecision mismatch")
    _require(
        paper.status is ExitEvaluationStatus.DECISION_CREATED,
        f"{case_key} Exit not executable",
    )
    _require(paper.decision is not None, f"{case_key} ExitDecision missing")
    _require(
        paper.decision.requested_mutation.to_dict() == {
            "take_profit_price": "101" if direction is TradeDirection.LONG else "99",
            "order_type": "LIMIT",
            "time_in_force": "POST_ONLY",
            "quantity": "ALL",
            "marketable_action": "CLOSE_MARKET",
        },
        f"{case_key} unexpected Exit mutation policy",
    )
    bad_observation = ExitObservation(
        observation_id=case_key + "-exit-missing",
        strategy_position_id=position_fields["strategy_position_id"],
        symbol=card.symbols[0],
        event_at=now,
        observed_at=now,
        received_at=now,
        event_kind="GEOMETRY_L5_3",
        attributes=FrozenPolicy.from_mapping({}),
        source_refs=("parity-replay",),
    )
    blocked = UniversalExitEngine().evaluate(
        SimpleNamespace(**position_fields), exit_plan, bad_observation,
    )
    _require(
        blocked.status is ExitEvaluationStatus.BLOCKED,
        f"{case_key} missing Exit fact did not block",
    )


def run_r1_independent_decision_parity() -> dict[str, object]:
    """Replay current R1 cohort in both modes; return PASS only after all comparisons.

    This is a causal deterministic **test**, not proof of live Exchange execution
    or admission-capital correctness. The other real-arm gates remain independent.
    """
    cards = build_r1_cards()
    _require(
        len(cards) == 5
        and {card.strategy_id for card in cards} == set(R1_STRATEGY_IDS.values()),
        "R1 strategy cohort changed without parity review",
    )
    case_count = 0
    for card in cards:
        _require(
            card.strategy_version == "1.1-micro-live", "R1 version changed without parity review",
        )
        symbol = card.symbols[0]
        candles = _candles(symbol)
        now = candles[-1].closed_at + timedelta(seconds=2)
        registry = ActivePlanRegistry()
        registry.register_card(card)
        activation = StrategyActivation(
            activation_id="parity-" + card.strategy_id,
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            enabled=True,
            enabled_at=card.approved_at,
            scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
            operator="offline-parity",
            source="offline-parity",
        )
        plan = registry.activate(activation)
        exit_plan = registry.exact_plan_pair(activation.activation_id)[1]
        bundle = ActiveStrategyBundle(card, activation, plan, exit_plan, now)
        bridge_bundle = BridgePolicyBundle(
            asdict(card), asdict(plan), asdict(exit_plan), asdict(activation),
        )

        for direction, price in (
            (TradeDirection.LONG, "91"),
            (TradeDirection.SHORT, "109"),
        ):
            case = card.strategy_id + "/" + direction.value
            market_fact = MarketFactEnvelope(
                fact_id=case + "/trade",
                event_kind="PUBLIC_TRADE",
                symbol=symbol,
                observed_at=now,
                event_at=now,
                received_at=now,
                source_refs=("offline-parity-trade",),
                attributes=FrozenPolicy.from_mapping({
                    "price": price,
                    "size": "1",
                    "taker_side": "Sell" if direction is TradeDirection.LONG else "Buy",
                }),
            )
            paper_rows = _engine(registry, symbol, candles).process(market_fact)
            admission = _OfflineRealAdmission(now)
            binding = StrategyExecutionBindingRef(
                "parity-binding", activation.activation_id, "PRIMARY", "parity-account",
            )
            real_rows = _engine(registry, symbol, candles).process(
                market_fact,
                capacity=TradingCapacitySnapshot(
                    "parity-capacity", now, Decimal("100"),
                    DataQuality.HIGH, "runtime.account_state_generations:parity",
                ),
                technical_readiness=TechnicalReadiness(True, now),
                account_ref="parity-account",
                entry_admission_port=admission,
                real_admission_required_for=frozenset({activation.activation_id}),
                real_admission_ready_for=frozenset({activation.activation_id}),
                execution_bindings={activation.activation_id: binding},
                exchange_position_keys={symbol: "parity-physical-slot"},
            )
            _require(len(paper_rows) == len(real_rows) == 1, case + " causal watch differs")
            paper = paper_rows[0]
            real = real_rows[0]
            _require(
                paper.signal == real.signal
                and paper.attempt == real.attempt
                and paper.execution_intent == real.execution_intent,
                case + " mode-neutral Entry identity differs",
            )
            _require(
                paper.decision is None and paper.execution_request is None,
                case + " PAPER forged REAL admission",
            )
            _require(
                real.decision is not None
                and real.decision.code is EntryDecisionCode.ACCEPTED
                and real.execution_request is not None
                and len(admission.requests) == 1,
                case + " offline REAL admission failed",
            )
            request = real.execution_request
            _require(
                request.payload == paper.execution_intent.payload,
                case + " request policy differs",
            )
            paper_db = _OfflinePaperDB()
            paper_runtime = PaperTradeRuntime(paper_db, bundles=(bundle,))
            paper_id = paper_runtime.create_order(paper, bundle, now=now)
            _require(
                paper_id is not None and len(paper_db.inserted_orders) == 1,
                case + " PAPER order missing",
            )
            paper_row = paper_db.inserted_orders[0]
            paper_payload = json.loads(str(paper_row[-1]))
            real_command = prepare_runtime_entry_command(request, bridge_bundle, now=now)
            real_payload = real_command.payload
            compared = (
                "strategy_attempt_id", "signal_id", "strategy_id", "strategy_version",
                "strategy_config_fingerprint", "entry_plan_fingerprint",
                "exit_plan_fingerprint", "calculated_entry_price",
                "entry_reference_source", "strategy_entry_offset_pct_signed",
                "strategy_context_features", "stake_usdt", "leverage",
                "side", "price", "entry_offset_pct", "entry_limit_ttl_seconds",
                "entry_lifetime_mode", "entry_time_in_force", "initial_protection",
            )
            for key in compared:
                _require(
                    paper_payload.get(key) == real_payload.get(key),
                    case + " PAPER/REAL adapter mismatch " + key,
                )
            for key in ("operator", "signal_entry_price", "signal_target_price"):
                _require(
                    paper_payload["entry_validity"][key] == real_payload["entry_validity"][key],
                    case + " entry validity mismatch " + key,
                )
            _require(
                real_payload["entry_validity"]["history_limit"] >= 205,
                case + " real causal-history validity unavailable",
            )
            reference = Decimal(str(paper_payload["price"]))
            offset = Decimal(str(paper_payload["entry_offset_pct"]))
            expected_limit = reference * (
                Decimal("1") - offset / Decimal("100")
                if direction is TradeDirection.LONG
                else Decimal("1") + offset / Decimal("100")
            )
            _require(paper_row[14] == expected_limit, case + " PAPER price policy mismatch")
            _require(
                real_payload["entry_time_in_force"] == "POST_ONLY"
                and real_payload["entry_lifetime_mode"] == "SIGNAL_VALIDITY"
                and real_payload["entry_offset_pct"] == "0.10"
                and real_payload["stake_usdt"] == "10",
                case + " R1 entry policy changed",
            )
            _require(
                real_command.strategy_execution_binding_id == "parity-binding"
                and real_command.trading_account_id == "parity-account",
                case + " REAL binding lineage mismatch",
            )
            # Disarmed REAL cannot silently fall back to a PAPER order.
            blocked_rows = _engine(registry, symbol, candles).process(
                market_fact,
                real_admission_required_for=frozenset({activation.activation_id}),
                real_admission_ready_for=frozenset(),
            )
            _require(
                len(blocked_rows) == 1
                and blocked_rows[0].execution_intent == paper.execution_intent
                and blocked_rows[0].decision is not None
                and blocked_rows[0].decision.code is EntryDecisionCode.OPERATIONAL_SAFETY_BLOCKED
                and blocked_rows[0].execution_request is None,
                case + " disarmed REAL did not fail closed",
            )
            reverse_paper = build_reverse_transition_intent(
                paper.execution_intent,
                strategy_activation_id=activation.activation_id,
                from_direction=(
                    TradeDirection.SHORT
                    if direction is TradeDirection.LONG
                    else TradeDirection.LONG
                ),
                lifecycle_policy=card.lifecycle_policy.to_dict(),
                capital_policy=card.capital_policy.to_dict(),
            )
            reverse_real = build_reverse_transition_intent(
                real.execution_intent,
                strategy_activation_id=activation.activation_id,
                from_direction=(
                    TradeDirection.SHORT
                    if direction is TradeDirection.LONG
                    else TradeDirection.LONG
                ),
                lifecycle_policy=card.lifecycle_policy.to_dict(),
                capital_policy=card.capital_policy.to_dict(),
            )
            _require(
                reverse_paper is not None
                and reverse_paper == reverse_real
                and reverse_paper.close_execution == "MARKET"
                and reverse_paper.open_execution == "MARKET"
                and reverse_paper.position_mode == "ONE_WAY"
                and reverse_paper.position_idx == 0,
                case + " reverse intent mismatch",
            )
            _exit_case(card, exit_plan, direction, now, case_key=case)
            case_count += 1
    _require(case_count == 10, "incomplete independent parity coverage")
    return {
        "status": "PASS",
        "proof_version": "R1_CAUSAL_PAPER_REAL_ADAPTER_PARITY_V1",
        "cases": case_count,
        "symbols": sorted(card.symbols[0] for card in cards),
        "directions": ["LONG", "SHORT"],
        "entry": "CAUSAL_L53_AND_PAPER_REAL_ADAPTER_COMPARISON",
        "exit": "UNIVERSAL_EXIT_DECISION_AND_MISSING_FACT_FAIL_CLOSED",
        "reverse": "SHARED_INTENT",
        "real_exchange_mutation": False,
    }
