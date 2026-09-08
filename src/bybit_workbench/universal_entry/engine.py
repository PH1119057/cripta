from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from .contracts import (
    CandidateCooldown,
    ContextLink,
    ContextMode,
    CooldownScope,
    DataQuality,
    EntryDecision,
    EntryDecisionCode,
    EntryEvaluation,
    EntryPlan,
    ExecutionRequest,
    FrozenPolicy,
    MarketFactEnvelope,
    NotificationEvent,
    NotificationKind,
    ObjectiveContext,
    SensorLink,
    SensorObservation,
    StrategyAttempt,
    StrategySignal,
    TechnicalReadiness,
    TradeDirection,
    TradingCapacitySnapshot,
)
from .dsl import PlanState, PredicateNode, evaluate_predicate, is_independent_touch
from .fingerprint import fingerprint
from .registry import ActivePlanRegistry

_QUALITY_RANK = {
    DataQuality.UNKNOWN: 0,
    DataQuality.INSUFFICIENT: 1,
    DataQuality.LOW: 2,
    DataQuality.MEDIUM: 3,
    DataQuality.HIGH: 4,
}
_DECISION_MODES = {ContextMode.CONDITION, ContextMode.RANKING}


class UniversalEntryEngine:
    def __init__(self, registry: ActivePlanRegistry) -> None:
        self._registry = registry
        self._states: dict[tuple[str, str], PlanState] = {}
        self._strategy_cooldowns: dict[str, datetime] = {}
        self._account_cooldowns: dict[tuple[str, str], datetime] = {}

    def process(
        self,
        fact: MarketFactEnvelope,
        *,
        sensors: Mapping[str, SensorObservation] | None = None,
        contexts: Mapping[str, ObjectiveContext] | None = None,
        capacity: TradingCapacitySnapshot | None = None,
        technical_readiness: TechnicalReadiness | None = None,
        account_ref: str | None = None,
    ) -> tuple[EntryEvaluation, ...]:
        available_sensors = dict(sensors or {})
        available_contexts = dict(contexts or {})
        output: list[EntryEvaluation] = []
        for plan in self._registry.plans_for(fact.symbol):
            if fact.direction is not None and fact.direction not in plan.directions:
                continue
            direction = self._resolve_direction(plan, fact)
            if direction is None:
                continue
            state = self._state_for(plan, fact.symbol)
            if fact.event_kind.upper() in {value.upper() for value in plan.touch_policy.reset_on}:
                self._reset_plan_state(plan, state, account_ref)
            if self._cooldown_active(plan, state, fact.observed_at, account_ref):
                state.observe(fact, independent_touch=False)
                continue

            independent_touch = is_independent_touch(fact, state, plan.touch_policy)
            sensor_links, consumed_sensors, sensors_ready = self._sensor_links(
                plan,
                available_sensors,
                fact.observed_at,
            )
            context_links, consumed_contexts, contexts_ready = self._context_links(
                plan,
                available_contexts,
                fact.observed_at,
            )
            predicate = plan.predicate
            if not isinstance(predicate, PredicateNode):
                raise TypeError("EntryPlan predicate is not a PredicateNode")
            matched = False
            if sensors_ready and contexts_ready:
                matched = evaluate_predicate(
                    predicate,
                    fact=fact,
                    state=state,
                    touch_policy=plan.touch_policy,
                    independent_touch=independent_touch,
                    sensors=consumed_sensors,
                    allowed_sensor_modes={item.sensor_id: item.mode for item in plan.sensor_policy},
                    contexts=consumed_contexts,
                    allowed_context_modes={
                        item.context_id: item.mode for item in plan.context_policy
                    },
                )

            state.observe(fact, independent_touch=independent_touch)
            cooldown = plan.touch_policy.candidate_cooldown
            if independent_touch and cooldown.enabled and cooldown.start_on == "TOUCH":
                self._start_cooldown(cooldown, plan, state, fact.observed_at, account_ref)
            if not matched:
                continue

            signal = self._signal(plan, fact, direction)
            if cooldown.enabled and cooldown.start_on == "SIGNAL":
                self._start_cooldown(cooldown, plan, state, fact.observed_at, account_ref)
            attempt = self._attempt(plan, signal, fact.observed_at)
            if cooldown.enabled and cooldown.start_on == "ATTEMPT":
                self._start_cooldown(cooldown, plan, state, fact.observed_at, account_ref)
            decision = self._decision(
                plan,
                signal,
                attempt,
                fact.observed_at,
                capacity=capacity,
                technical_readiness=technical_readiness,
            )
            request = self._execution_request(plan, signal, attempt, decision, fact.observed_at)
            notifications = self._notifications(signal, attempt, decision, plan, capacity=capacity)
            output.append(
                EntryEvaluation(
                    signal,
                    attempt,
                    decision,
                    request,
                    sensor_links,
                    context_links,
                    notifications,
                )
            )
        return tuple(output)

    def _state_for(self, plan: EntryPlan, symbol: str) -> PlanState:
        return self._states.setdefault((plan.entry_plan_fingerprint, symbol), PlanState())

    @staticmethod
    def _resolve_direction(plan: EntryPlan, fact: MarketFactEnvelope) -> TradeDirection | None:
        if fact.direction is not None:
            return fact.direction if fact.direction in plan.directions else None
        if len(plan.directions) == 1:
            return plan.directions[0]
        return None

    def _reset_plan_state(
        self,
        plan: EntryPlan,
        state: PlanState,
        account_ref: str | None,
    ) -> None:
        state.reset()
        self._strategy_cooldowns.pop(plan.strategy_activation_id, None)
        if account_ref is not None:
            self._account_cooldowns.pop((plan.strategy_activation_id, account_ref), None)

    def _cooldown_active(
        self,
        plan: EntryPlan,
        state: PlanState,
        observed_at: datetime,
        account_ref: str | None,
    ) -> bool:
        cooldown = plan.touch_policy.candidate_cooldown
        if not cooldown.enabled:
            return False
        if cooldown.scope is CooldownScope.PER_SYMBOL:
            return state.cooldown_until is not None and observed_at < state.cooldown_until
        if cooldown.scope is CooldownScope.PER_STRATEGY:
            until = self._strategy_cooldowns.get(plan.strategy_activation_id)
            return until is not None and observed_at < until
        if cooldown.scope is CooldownScope.PER_ACCOUNT:
            if not account_ref:
                raise ValueError("PER_ACCOUNT cooldown requires account_ref")
            until = self._account_cooldowns.get((plan.strategy_activation_id, account_ref))
            return until is not None and observed_at < until
        raise ValueError(f"unsupported cooldown scope: {cooldown.scope}")

    def _start_cooldown(
        self,
        cooldown: CandidateCooldown,
        plan: EntryPlan,
        state: PlanState,
        observed_at: datetime,
        account_ref: str | None,
    ) -> None:
        if not cooldown.enabled:
            return
        until = observed_at + timedelta(seconds=float(cooldown.as_seconds()))
        if cooldown.scope is CooldownScope.PER_SYMBOL:
            state.cooldown_until = until
            return
        if cooldown.scope is CooldownScope.PER_STRATEGY:
            self._strategy_cooldowns[plan.strategy_activation_id] = until
            return
        if cooldown.scope is CooldownScope.PER_ACCOUNT:
            if not account_ref:
                raise ValueError("PER_ACCOUNT cooldown requires account_ref")
            self._account_cooldowns[(plan.strategy_activation_id, account_ref)] = until
            return
        raise ValueError(f"unsupported cooldown scope: {cooldown.scope}")

    @staticmethod
    def _observation_status(
        *,
        observed_at: datetime,
        now: datetime,
        quality: DataQuality,
        completeness: str,
        max_age_seconds: int | None,
        min_quality: DataQuality | None,
    ) -> tuple[str, float]:
        age = (now - observed_at).total_seconds()
        status = "FRESH"
        if age < 0:
            status = "FUTURE"
        elif max_age_seconds is not None and age > max_age_seconds:
            status = "STALE"
        if completeness.upper() != "COMPLETE":
            status = "PARTIAL"
        if min_quality is not None and _QUALITY_RANK[quality] < _QUALITY_RANK[min_quality]:
            status = "PARTIAL"
        return status, age

    @classmethod
    def _sensor_links(
        cls,
        plan: EntryPlan,
        sensors: Mapping[str, SensorObservation],
        observed_at: datetime,
    ) -> tuple[tuple[SensorLink, ...], dict[str, SensorObservation], bool]:
        links: list[SensorLink] = []
        consumed: dict[str, SensorObservation] = {}
        ready = True
        for requirement in plan.sensor_policy:
            if requirement.mode is ContextMode.OFF:
                continue
            sensor = sensors.get(requirement.sensor_id)
            if sensor is None:
                links.append(
                    SensorLink(
                        requirement.sensor_id,
                        False,
                        False,
                        None,
                        None,
                        None,
                        "MISSING",
                    )
                )
                if requirement.mode in _DECISION_MODES:
                    ready = False
                continue
            status, age = cls._observation_status(
                observed_at=sensor.observed_at,
                now=observed_at,
                quality=sensor.quality,
                completeness=sensor.completeness,
                max_age_seconds=requirement.max_age_seconds,
                min_quality=requirement.min_quality,
            )
            is_consumed = requirement.mode in _DECISION_MODES and status == "FRESH"
            links.append(
                SensorLink(
                    sensor.sensor_id,
                    True,
                    is_consumed,
                    sensor.observed_at,
                    age,
                    sensor.quality,
                    status,
                    sensor.source_refs,
                )
            )
            if requirement.mode in _DECISION_MODES:
                if status != "FRESH":
                    ready = False
                else:
                    consumed[requirement.sensor_id] = sensor
        return tuple(links), consumed, ready

    @classmethod
    def _context_links(
        cls,
        plan: EntryPlan,
        contexts: Mapping[str, ObjectiveContext],
        observed_at: datetime,
    ) -> tuple[tuple[ContextLink, ...], dict[str, ObjectiveContext], bool]:
        links: list[ContextLink] = []
        consumed: dict[str, ObjectiveContext] = {}
        ready = True
        for requirement in plan.context_policy:
            if requirement.mode is ContextMode.OFF:
                continue
            context = contexts.get(requirement.context_id)
            if context is None:
                links.append(
                    ContextLink(
                        requirement.context_id,
                        None,
                        None,
                        requirement.mode,
                        False,
                        False,
                        None,
                        None,
                        None,
                        "MISSING",
                    )
                )
                if requirement.mode in _DECISION_MODES:
                    ready = False
                continue
            status, age = cls._observation_status(
                observed_at=context.observed_at,
                now=observed_at,
                quality=context.quality,
                completeness=context.completeness,
                max_age_seconds=requirement.max_age_seconds,
                min_quality=requirement.min_quality,
            )
            is_consumed = requirement.mode in _DECISION_MODES and status == "FRESH"
            links.append(
                ContextLink(
                    requirement.context_id,
                    context.context_id,
                    context.context_type,
                    requirement.mode,
                    True,
                    is_consumed,
                    context.observed_at,
                    age,
                    context.quality,
                    status,
                    context.source_refs,
                )
            )
            if requirement.mode in _DECISION_MODES:
                if status != "FRESH":
                    ready = False
                else:
                    consumed[requirement.context_id] = context
        return tuple(links), consumed, ready

    @staticmethod
    def _signal(
        plan: EntryPlan,
        fact: MarketFactEnvelope,
        direction: TradeDirection,
    ) -> StrategySignal:
        signal_id = (
            "sig-"
            + fingerprint(
                {
                    "plan": plan.entry_plan_fingerprint,
                    "activation": plan.strategy_activation_id,
                    "fact": fact.fact_id,
                    "source_refs": fact.source_refs,
                    "symbol": fact.symbol,
                    "direction": direction,
                }
            )[:32]
        )
        return StrategySignal(
            signal_id=signal_id,
            strategy_id=plan.strategy_id,
            strategy_version=plan.strategy_version,
            strategy_config_fingerprint=plan.strategy_config_fingerprint,
            entry_plan_fingerprint=plan.entry_plan_fingerprint,
            strategy_activation_id=plan.strategy_activation_id,
            symbol=fact.symbol,
            direction=direction,
            detected_at=fact.observed_at.astimezone(UTC),
            source_refs=fact.source_refs,
            fact_id=fact.fact_id,
        )

    @staticmethod
    def _attempt(plan: EntryPlan, signal: StrategySignal, now: datetime) -> StrategyAttempt:
        attempt_id = (
            "attempt-"
            + fingerprint(
                {"signal_id": signal.signal_id, "activation": plan.strategy_activation_id}
            )[:32]
        )
        return StrategyAttempt(
            strategy_attempt_id=attempt_id,
            signal_id=signal.signal_id,
            strategy_id=plan.strategy_id,
            strategy_version=plan.strategy_version,
            strategy_config_fingerprint=plan.strategy_config_fingerprint,
            entry_plan_fingerprint=plan.entry_plan_fingerprint,
            strategy_activation_id=plan.strategy_activation_id,
            created_at=now.astimezone(UTC),
        )

    @staticmethod
    def _capital_settings(plan: EntryPlan) -> tuple[Decimal | None, int | None, DataQuality | None]:
        payload = plan.capital_policy.to_dict()
        if not bool(payload.get("require_capacity", False)):
            return None, None, None
        raw_amount = payload.get("requested_amount")
        raw_age = payload.get("capacity_max_age_seconds")
        raw_quality = payload.get("capacity_min_quality")
        try:
            amount = Decimal(str(raw_amount))
            max_age = int(str(raw_age))
            min_quality = DataQuality(str(raw_quality))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("capital policy is not materialized correctly") from None
        return amount, max_age, min_quality

    def _decision(
        self,
        plan: EntryPlan,
        signal: StrategySignal,
        attempt: StrategyAttempt,
        now: datetime,
        *,
        capacity: TradingCapacitySnapshot | None,
        technical_readiness: TechnicalReadiness | None,
    ) -> EntryDecision:
        code = EntryDecisionCode.ACCEPTED
        reason = "strategy conditions matched"
        capacity_id: str | None = None
        if technical_readiness is None:
            code = EntryDecisionCode.STALE_OR_UNKNOWN_REQUIRED_STATE
            reason = "mandatory technical readiness is unknown"
        elif not technical_readiness.ready:
            code = EntryDecisionCode.OPERATIONAL_SAFETY_BLOCKED
            reason = technical_readiness.reason

        requested, max_age, min_quality = self._capital_settings(plan)
        if code is EntryDecisionCode.ACCEPTED and requested is not None:
            if capacity is None or capacity.available_for_new_trading is None:
                code = EntryDecisionCode.STALE_OR_UNKNOWN_REQUIRED_STATE
                reason = "required trading capacity is unknown"
            else:
                capacity_id = capacity.capacity_snapshot_id
                age = (now - capacity.observed_at).total_seconds()
                if age < 0 or (max_age is not None and age > max_age):
                    code = EntryDecisionCode.STALE_OR_UNKNOWN_REQUIRED_STATE
                    reason = "required trading capacity is stale or from the future"
                elif min_quality is not None and (
                    _QUALITY_RANK[capacity.quality] < _QUALITY_RANK[min_quality]
                ):
                    code = EntryDecisionCode.STALE_OR_UNKNOWN_REQUIRED_STATE
                    reason = "required trading capacity quality is insufficient"
                elif capacity.available_for_new_trading < requested:
                    code = EntryDecisionCode.INSUFFICIENT_AVAILABLE_FUNDS
                    reason = "available trading capacity is below Strategy request"
        decision_id = (
            "decision-"
            + fingerprint({"attempt": attempt.strategy_attempt_id, "code": code, "reason": reason})[
                :32
            ]
        )
        return EntryDecision(
            decision_id,
            attempt.strategy_attempt_id,
            signal.signal_id,
            code,
            reason,
            now.astimezone(UTC),
            capacity_id,
        )

    def _execution_request(
        self,
        plan: EntryPlan,
        signal: StrategySignal,
        attempt: StrategyAttempt,
        decision: EntryDecision,
        now: datetime,
    ) -> ExecutionRequest | None:
        if decision.code is not EntryDecisionCode.ACCEPTED:
            return None
        payload = FrozenPolicy.from_mapping(
            {
                "capital_policy": plan.capital_policy.to_dict(),
                "entry_plan_fingerprint": plan.entry_plan_fingerprint,
            }
        )
        request_id = (
            "request-"
            + fingerprint({"decision": decision.entry_decision_id, "signal": signal.signal_id})[:32]
        )
        return ExecutionRequest(
            request_id,
            attempt.strategy_attempt_id,
            decision.entry_decision_id,
            signal.signal_id,
            signal.strategy_id,
            signal.strategy_version,
            signal.strategy_config_fingerprint,
            signal.entry_plan_fingerprint,
            signal.symbol,
            signal.direction,
            now.astimezone(UTC),
            payload,
        )

    def _notifications(
        self,
        signal: StrategySignal,
        attempt: StrategyAttempt,
        decision: EntryDecision,
        plan: EntryPlan,
        *,
        capacity: TradingCapacitySnapshot | None,
    ) -> tuple[NotificationEvent, ...]:
        kind_map = {
            EntryDecisionCode.INSUFFICIENT_AVAILABLE_FUNDS: (
                NotificationKind.INSUFFICIENT_AVAILABLE_FUNDS
            ),
            EntryDecisionCode.OPERATIONAL_SAFETY_BLOCKED: (
                NotificationKind.OPERATIONAL_SAFETY_BLOCKED
            ),
            EntryDecisionCode.STALE_OR_UNKNOWN_REQUIRED_STATE: (
                NotificationKind.STALE_OR_UNKNOWN_REQUIRED_STATE
            ),
        }
        kind = kind_map.get(decision.code)
        if kind is None:
            return ()
        requested, _, _ = self._capital_settings(plan)
        available = None if capacity is None else capacity.available_for_new_trading
        notification_id = (
            "notice-"
            + fingerprint(
                {
                    "attempt": attempt.strategy_attempt_id,
                    "kind": kind,
                    "decision": decision.entry_decision_id,
                }
            )[:32]
        )
        return (
            NotificationEvent(
                notification_id,
                kind,
                signal.strategy_id,
                signal.strategy_version,
                signal.signal_id,
                attempt.strategy_attempt_id,
                signal.symbol,
                signal.direction,
                decision.decided_at,
                decision.reason,
                requested,
                available,
            ),
        )
