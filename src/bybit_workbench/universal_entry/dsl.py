from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .contracts import (
    ContextMode,
    FrozenPolicy,
    MarketFactEnvelope,
    ObjectiveContext,
    SensorObservation,
    TouchPolicy,
)


class DslOperator(StrEnum):
    COMPARE = "COMPARE"
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    TOUCH = "TOUCH"
    BREAK = "BREAK"
    RETEST = "RETEST"
    RECLAIM = "RECLAIM"
    COUNT = "COUNT"
    NTH_EVENT = "NTH_EVENT"
    SEQUENCE = "SEQUENCE"
    WINDOW = "WINDOW"
    TIMER = "TIMER"
    RESET = "RESET"


class UnsupportedOperatorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PredicateNode:
    operator: DslOperator
    params: FrozenPolicy = FrozenPolicy("{}")
    children: tuple[PredicateNode, ...] = ()


@dataclass(slots=True)
class PlanState:
    event_counts: dict[str, int] = field(default_factory=dict)
    recent_events: list[tuple[str, datetime]] = field(default_factory=list)
    touch_count: int = 0
    last_touch_at: datetime | None = None
    exited_zone_since_touch: bool = True
    cooldown_until: datetime | None = None

    def reset(self) -> None:
        self.event_counts.clear()
        self.recent_events.clear()
        self.touch_count = 0
        self.last_touch_at = None
        self.exited_zone_since_touch = True
        self.cooldown_until = None

    def observe(self, fact: MarketFactEnvelope, *, independent_touch: bool = False) -> None:
        kind = fact.event_kind.upper()
        self.event_counts[kind] = self.event_counts.get(kind, 0) + 1
        self.recent_events.append((kind, fact.observed_at))
        if kind == "TOUCH" and independent_touch:
            self.touch_count += 1
            self.last_touch_at = fact.observed_at
            self.exited_zone_since_touch = False
        elif kind == "EXIT_ZONE":
            self.exited_zone_since_touch = True


def _require_keys(
    operator: DslOperator,
    params: Mapping[str, object],
    required: tuple[str, ...],
) -> None:
    missing = tuple(key for key in required if key not in params)
    if missing:
        raise ValueError(f"{operator.value} requires explicit parameters: {missing}")


def _validate_predicate_shape(
    operator: DslOperator,
    params: Mapping[str, object],
    children: tuple[PredicateNode, ...],
) -> None:
    if operator is DslOperator.COMPARE:
        _require_keys(operator, params, ("path", "comparator", "value"))
    elif operator is DslOperator.COUNT:
        _require_keys(operator, params, ("event_kind", "comparator", "value"))
    elif operator is DslOperator.NTH_EVENT:
        _require_keys(operator, params, ("event_kind", "number"))
    elif operator is DslOperator.SEQUENCE:
        _require_keys(operator, params, ("events",))
        events = params["events"]
        if not isinstance(events, Sequence) or isinstance(events, (str, bytes)) or not events:
            raise ValueError("SEQUENCE requires a non-empty events list")
    elif operator is DslOperator.WINDOW:
        _require_keys(operator, params, ("event_kind", "seconds"))
    elif operator is DslOperator.TIMER:
        _require_keys(operator, params, ("anchor_event_kind", "seconds", "mode"))
    if operator in {DslOperator.AND, DslOperator.OR} and not children:
        raise ValueError(f"{operator.value} requires at least one child")
    if operator is DslOperator.NOT and len(children) != 1:
        raise ValueError("NOT requires exactly one child")


def predicate_from_mapping(raw: Mapping[str, object]) -> PredicateNode:
    op_raw = raw.get("op")
    try:
        operator = DslOperator(str(op_raw))
    except ValueError as exc:
        raise UnsupportedOperatorError(f"UNSUPPORTED_OPERATOR:{op_raw}") from exc
    params_raw = raw.get("params")
    params = params_raw if isinstance(params_raw, Mapping) else {}
    children_raw = raw.get("children")
    children: list[PredicateNode] = []
    if isinstance(children_raw, Sequence) and not isinstance(children_raw, (str, bytes)):
        for item in children_raw:
            if not isinstance(item, Mapping):
                raise ValueError("predicate child must be an object")
            children.append(predicate_from_mapping(item))
    node = PredicateNode(operator, FrozenPolicy.from_mapping(params), tuple(children))
    _validate_predicate_shape(operator, params, node.children)
    return node


def _path_value(
    path: str,
    fact: MarketFactEnvelope,
    sensors: Mapping[str, SensorObservation],
    allowed_sensor_modes: Mapping[str, ContextMode],
    contexts: Mapping[str, ObjectiveContext],
    allowed_context_modes: Mapping[str, ContextMode],
) -> object | None:
    if path == "event_kind":
        return fact.event_kind
    if path == "symbol":
        return fact.symbol
    if path == "direction":
        return None if fact.direction is None else fact.direction.value
    parts = path.split(".")
    if parts[0] == "fact":
        value: object = fact.attributes.to_dict()
        parts = parts[1:]
    elif path.startswith("sensor."):
        remainder = path.removeprefix("sensor.")
        sensor_id = next(
            (
                candidate
                for candidate in sorted(allowed_sensor_modes, key=len, reverse=True)
                if remainder == candidate or remainder.startswith(candidate + ".")
            ),
            None,
        )
        if sensor_id is None or allowed_sensor_modes.get(sensor_id) not in {
            ContextMode.CONDITION,
            ContextMode.RANKING,
        }:
            return None
        sensor = sensors.get(sensor_id)
        if sensor is None:
            return None
        value = sensor.payload.to_dict()
        suffix = remainder[len(sensor_id) :].removeprefix(".")
        parts = [] if not suffix else suffix.split(".")
    elif path.startswith("context."):
        remainder = path.removeprefix("context.")
        context_id = next(
            (
                candidate
                for candidate in sorted(allowed_context_modes, key=len, reverse=True)
                if remainder == candidate or remainder.startswith(candidate + ".")
            ),
            None,
        )
        if context_id is None or allowed_context_modes.get(context_id) not in {
            ContextMode.CONDITION,
            ContextMode.RANKING,
        }:
            return None
        context = contexts.get(context_id)
        if context is None:
            return None
        value = context.payload.to_dict()
        suffix = remainder[len(context_id) :].removeprefix(".")
        parts = [] if not suffix else suffix.split(".")
    else:
        value = fact.attributes.to_dict()
    for part in parts:
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
        if value is None:
            return None
    return value


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _compare(left: object, comparator: str, right: object) -> bool:
    op = comparator.upper()
    if op == "EQ":
        return left == right or str(left) == str(right)
    if op == "NE":
        return not _compare(left, "EQ", right)
    if op in {"GT", "GTE", "LT", "LTE"}:
        a = _decimal(left)
        b = _decimal(right)
        if a is None or b is None:
            return False
        if op == "GT":
            return a > b
        if op == "GTE":
            return a >= b
        if op == "LT":
            return a < b
        return a <= b
    if op in {"IN", "NOT_IN"}:
        if not isinstance(right, Sequence) or isinstance(right, (str, bytes)):
            return False
        present = left in right
        return present if op == "IN" else not present
    raise UnsupportedOperatorError(f"UNSUPPORTED_COMPARATOR:{comparator}")


def _duration_seconds(value: Decimal, unit: str | None) -> Decimal:
    factors = {
        "seconds": Decimal("1"),
        "minutes": Decimal("60"),
        "hours": Decimal("3600"),
    }
    if unit not in factors:
        raise ValueError(f"unsupported duration unit: {unit}")
    return value * factors[unit]


def is_independent_touch(
    fact: MarketFactEnvelope,
    state: PlanState,
    policy: TouchPolicy,
) -> bool:
    if fact.event_kind.upper() != "TOUCH":
        return False
    next_touch_number = state.touch_count + 1
    if policy.maximum_touch_count is not None and next_touch_number > policy.maximum_touch_count:
        return False
    if (
        policy.require_exit_from_zone
        and state.touch_count > 0
        and not state.exited_zone_since_touch
    ):
        return False
    time_rule = policy.minimum_time_between_touches
    if time_rule.enabled and state.last_touch_at is not None:
        assert time_rule.value is not None
        elapsed = Decimal(str((fact.observed_at - state.last_touch_at).total_seconds()))
        if elapsed < _duration_seconds(time_rule.value, time_rule.unit):
            return False
    distance_rule = policy.minimum_exit_distance
    if distance_rule.enabled and state.touch_count > 0:
        attributes = fact.attributes.to_dict()
        distance = _decimal(attributes.get("exit_distance"))
        observed_unit = attributes.get("exit_distance_unit")
        assert distance_rule.value is not None
        if observed_unit != distance_rule.unit:
            return False
        if distance is None or distance < distance_rule.value:
            return False
    return True


def _touch_matches(state: PlanState, policy: TouchPolicy, independent_touch: bool) -> bool:
    if not independent_touch:
        return False
    return policy.accepts(state.touch_count + 1)


def evaluate_predicate(
    node: PredicateNode,
    *,
    fact: MarketFactEnvelope,
    state: PlanState,
    touch_policy: TouchPolicy,
    independent_touch: bool,
    sensors: Mapping[str, SensorObservation],
    allowed_sensor_modes: Mapping[str, ContextMode],
    contexts: Mapping[str, ObjectiveContext],
    allowed_context_modes: Mapping[str, ContextMode],
) -> bool:
    op = node.operator
    params = node.params.to_dict()

    def evaluate_child(child: PredicateNode) -> bool:
        return evaluate_predicate(
            child,
            fact=fact,
            state=state,
            touch_policy=touch_policy,
            independent_touch=independent_touch,
            sensors=sensors,
            allowed_sensor_modes=allowed_sensor_modes,
            contexts=contexts,
            allowed_context_modes=allowed_context_modes,
        )

    if op is DslOperator.AND:
        return all(evaluate_child(child) for child in node.children)
    if op is DslOperator.OR:
        return any(evaluate_child(child) for child in node.children)
    if op is DslOperator.NOT:
        if len(node.children) != 1:
            raise ValueError("NOT requires exactly one child")
        return not evaluate_child(node.children[0])
    if op is DslOperator.COMPARE:
        path = str(params.get("path") or "")
        comparator = str(params["comparator"])
        left = _path_value(
            path,
            fact,
            sensors,
            allowed_sensor_modes,
            contexts,
            allowed_context_modes,
        )
        if left is None:
            return False
        return _compare(left, comparator, params.get("value"))
    if op is DslOperator.TOUCH:
        return _touch_matches(state, touch_policy, independent_touch)
    if op in {DslOperator.BREAK, DslOperator.RETEST, DslOperator.RECLAIM, DslOperator.RESET}:
        return fact.event_kind.upper() == op.value
    if op is DslOperator.COUNT:
        event_kind = str(params.get("event_kind") or "").upper()
        projected = state.event_counts.get(event_kind, 0) + (
            1 if fact.event_kind.upper() == event_kind else 0
        )
        return _compare(projected, str(params["comparator"]), params["value"])
    if op is DslOperator.NTH_EVENT:
        event_kind = str(params["event_kind"]).upper()
        projected = state.event_counts.get(event_kind, 0) + (
            1 if fact.event_kind.upper() == event_kind else 0
        )
        return projected == int(str(params["number"]))
    if op is DslOperator.SEQUENCE:
        expected_raw = params.get("events")
        if not isinstance(expected_raw, Sequence) or isinstance(expected_raw, (str, bytes)):
            return False
        expected = tuple(str(value).upper() for value in expected_raw)
        seen = [kind for kind, _ in state.recent_events] + [fact.event_kind.upper()]
        if len(seen) < len(expected) or tuple(seen[-len(expected) :]) != expected:
            return False
        within = params.get("within_seconds")
        if within is None or len(expected) <= 1:
            return True
        start_index = len(state.recent_events) - len(expected) + 1
        if start_index < 0:
            return False
        start_at = state.recent_events[start_index][1]
        return fact.observed_at - start_at <= timedelta(seconds=int(str(within)))
    if op is DslOperator.WINDOW:
        event_kind = str(params["event_kind"]).upper()
        seconds = int(str(params["seconds"]))
        return any(
            kind == event_kind and fact.observed_at - timestamp <= timedelta(seconds=seconds)
            for kind, timestamp in state.recent_events
        )
    if op is DslOperator.TIMER:
        anchor = str(params["anchor_event_kind"]).upper()
        seconds = int(str(params["seconds"]))
        mode = str(params["mode"]).upper()
        timestamps = [timestamp for kind, timestamp in state.recent_events if kind == anchor]
        if not timestamps:
            return False
        elapsed = fact.observed_at - timestamps[-1]
        if mode == "ELAPSED":
            return elapsed >= timedelta(seconds=seconds)
        if mode == "ACTIVE":
            return elapsed < timedelta(seconds=seconds)
        raise UnsupportedOperatorError(f"UNSUPPORTED_TIMER_MODE:{mode}")
    raise UnsupportedOperatorError(f"UNSUPPORTED_OPERATOR:{op}")
