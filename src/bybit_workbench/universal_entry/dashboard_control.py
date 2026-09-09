from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast

from .contracts import (
    CandidateCooldown,
    ContextFailureAction,
    ContextMode,
    ContextRequirement,
    CooldownScope,
    DataQuality,
    FrozenPolicy,
    NumericRule,
    SensorRequirement,
    StrategyCard,
    TouchPolicy,
    TradeDirection,
)
from .storage import StrategyEntryStore


class DashboardCursorLike(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...


class DashboardConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> DashboardCursorLike: ...

    def transaction(self) -> AbstractContextManager[object]: ...


class StaleActivationState(RuntimeError):
    """The browser attempted to overwrite a newer StrategyActivation state."""


class UnknownActivation(KeyError):
    """Exact activation_id does not exist."""


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list")
    return list(value)


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"invalid {label}") from None
    if not result.is_finite():
        raise ValueError(f"invalid {label}")
    return result


def _enum_or_none[T](enum_type: type[T], value: object | None, label: str) -> T | None:
    if value is None:
        return None
    try:
        constructor = cast(Callable[[str], T], enum_type)
        return constructor(str(value))
    except ValueError:
        raise ValueError(f"invalid {label}: {value}") from None


def _unwrap_policy(value: object, label: str) -> dict[str, object]:
    raw = _mapping(value, label)
    if "payload_json" not in raw:
        return raw
    payload = raw.get("payload_json")
    if not isinstance(payload, str):
        raise ValueError(f"{label}.payload_json must be a JSON string")
    decoded = json.loads(payload)
    return _mapping(decoded, label)


def _numeric_rule_to_mapping(rule: NumericRule) -> dict[str, object]:
    return {
        "enabled": rule.enabled,
        "value": None if rule.value is None else str(rule.value),
        "unit": rule.unit,
        "scope": rule.scope,
    }


def _numeric_rule_from_mapping(value: object, label: str) -> NumericRule:
    raw = _mapping(value, label)
    if "enabled" not in raw:
        raise ValueError(f"{label}.enabled is required")
    enabled = _bool(raw["enabled"], f"{label}.enabled")
    if not enabled:
        return NumericRule(enabled=False)
    return NumericRule(
        enabled=True,
        value=_decimal(raw.get("value"), f"{label}.value"),
        unit=str(raw.get("unit") or ""),
        scope=None if raw.get("scope") is None else str(raw["scope"]),
    )


def _cooldown_to_mapping(cooldown: CandidateCooldown) -> dict[str, object]:
    return {
        "enabled": cooldown.enabled,
        "duration": None if cooldown.duration is None else str(cooldown.duration),
        "unit": cooldown.unit,
        "scope": None if cooldown.scope is None else cooldown.scope.value,
        "trigger_event": cooldown.trigger_event,
        "anchor": cooldown.anchor,
    }


def _cooldown_from_mapping(value: object) -> CandidateCooldown:
    raw = _mapping(value, "touch_policy.candidate_cooldown")
    if "enabled" not in raw:
        raise ValueError("touch_policy.candidate_cooldown.enabled is required")
    if not _bool(raw["enabled"], "touch_policy.candidate_cooldown.enabled"):
        return CandidateCooldown(enabled=False)
    scope = _enum_or_none(
        CooldownScope,
        raw.get("scope"),
        "touch_policy.candidate_cooldown.scope",
    )
    if scope is None:
        raise ValueError("touch_policy.candidate_cooldown.scope is required")
    return CandidateCooldown(
        enabled=True,
        duration=_decimal(raw.get("duration"), "candidate cooldown duration"),
        unit=str(raw.get("unit") or ""),
        scope=scope,
        trigger_event=str(raw.get("trigger_event") or ""),
        anchor=str(raw.get("anchor") or ""),
    )


def _touch_to_mapping(touch: TouchPolicy) -> dict[str, object]:
    return {
        "accepted_touch_numbers": list(touch.accepted_touch_numbers),
        "accept_touch_from": touch.accept_touch_from,
        "require_exit_from_zone": touch.require_exit_from_zone,
        "minimum_exit_distance": _numeric_rule_to_mapping(touch.minimum_exit_distance),
        "minimum_time_between_touches": _numeric_rule_to_mapping(
            touch.minimum_time_between_touches
        ),
        "maximum_touch_count": touch.maximum_touch_count,
        "reset_on": list(touch.reset_on),
        "candidate_cooldown": _cooldown_to_mapping(touch.candidate_cooldown),
    }


def _touch_from_mapping(value: object) -> TouchPolicy:
    raw = _mapping(value, "touch_policy")
    required = (
        "accepted_touch_numbers",
        "accept_touch_from",
        "require_exit_from_zone",
        "minimum_exit_distance",
        "minimum_time_between_touches",
        "maximum_touch_count",
        "reset_on",
        "candidate_cooldown",
    )
    missing = tuple(name for name in required if name not in raw)
    if missing:
        raise ValueError(f"touch_policy missing required fields: {missing}")
    return TouchPolicy(
        accepted_touch_numbers=tuple(
            int(str(item))
            for item in _list(raw["accepted_touch_numbers"], "accepted_touch_numbers")
        ),
        accept_touch_from=(
            None if raw["accept_touch_from"] is None else int(str(raw["accept_touch_from"]))
        ),
        require_exit_from_zone=_bool(
            raw["require_exit_from_zone"], "touch_policy.require_exit_from_zone"
        ),
        minimum_exit_distance=_numeric_rule_from_mapping(
            raw["minimum_exit_distance"], "touch_policy.minimum_exit_distance"
        ),
        minimum_time_between_touches=_numeric_rule_from_mapping(
            raw["minimum_time_between_touches"],
            "touch_policy.minimum_time_between_touches",
        ),
        maximum_touch_count=(
            None if raw["maximum_touch_count"] is None else int(str(raw["maximum_touch_count"]))
        ),
        reset_on=tuple(str(item) for item in _list(raw["reset_on"], "reset_on")),
        candidate_cooldown=_cooldown_from_mapping(raw["candidate_cooldown"]),
    )


def _sensor_to_mapping(item: SensorRequirement) -> dict[str, object]:
    return {
        "sensor_id": item.sensor_id,
        "mode": item.mode.value,
        "max_age_seconds": item.max_age_seconds,
        "min_quality": None if item.min_quality is None else item.min_quality.value,
        "on_missing": None if item.on_missing is None else item.on_missing.value,
        "on_stale": None if item.on_stale is None else item.on_stale.value,
        "on_partial": None if item.on_partial is None else item.on_partial.value,
    }


def _sensor_from_mapping(value: object) -> SensorRequirement:
    raw = _mapping(value, "market_sensor_policy item")
    try:
        mode = ContextMode(str(raw.get("mode")))
    except ValueError:
        raise ValueError("invalid sensor mode") from None
    return SensorRequirement(
        sensor_id=str(raw.get("sensor_id") or ""),
        mode=mode,
        max_age_seconds=(
            None if raw.get("max_age_seconds") is None else int(str(raw["max_age_seconds"]))
        ),
        min_quality=_enum_or_none(DataQuality, raw.get("min_quality"), "sensor min_quality"),
        on_missing=_enum_or_none(ContextFailureAction, raw.get("on_missing"), "sensor on_missing"),
        on_stale=_enum_or_none(ContextFailureAction, raw.get("on_stale"), "sensor on_stale"),
        on_partial=_enum_or_none(ContextFailureAction, raw.get("on_partial"), "sensor on_partial"),
    )


def _context_to_mapping(item: ContextRequirement) -> dict[str, object]:
    return {
        "context_id": item.context_id,
        "mode": item.mode.value,
        "max_age_seconds": item.max_age_seconds,
        "min_quality": None if item.min_quality is None else item.min_quality.value,
        "on_missing": None if item.on_missing is None else item.on_missing.value,
        "on_stale": None if item.on_stale is None else item.on_stale.value,
        "on_partial": None if item.on_partial is None else item.on_partial.value,
    }


def _context_from_mapping(value: object, label: str) -> ContextRequirement:
    raw = _mapping(value, label)
    try:
        mode = ContextMode(str(raw.get("mode")))
    except ValueError:
        raise ValueError(f"invalid {label} mode") from None
    return ContextRequirement(
        context_id=str(raw.get("context_id") or ""),
        mode=mode,
        max_age_seconds=(
            None if raw.get("max_age_seconds") is None else int(str(raw["max_age_seconds"]))
        ),
        min_quality=_enum_or_none(DataQuality, raw.get("min_quality"), f"{label} min_quality"),
        on_missing=_enum_or_none(
            ContextFailureAction, raw.get("on_missing"), f"{label} on_missing"
        ),
        on_stale=_enum_or_none(ContextFailureAction, raw.get("on_stale"), f"{label} on_stale"),
        on_partial=_enum_or_none(
            ContextFailureAction, raw.get("on_partial"), f"{label} on_partial"
        ),
    )


def card_to_editable(card: StrategyCard) -> dict[str, object]:
    """Return a complete UI-editable policy without inventing defaults."""

    return {
        "strategy_id": card.strategy_id,
        "strategy_version": card.strategy_version,
        "name": card.name,
        "description": card.description,
        "scope": card.scope.to_dict(),
        "symbols": list(card.symbols),
        "direction_policy": [item.value for item in card.direction_policy],
        "entry_policy": card.entry_policy.to_dict(),
        "exit_policy": card.exit_policy.to_dict(),
        "capital_policy": card.capital_policy.to_dict(),
        "protection_policy": card.protection_policy.to_dict(),
        "lifecycle_policy": card.lifecycle_policy.to_dict(),
        "touch_policy": _touch_to_mapping(card.touch_policy),
        "market_sensor_policy": [_sensor_to_mapping(item) for item in card.market_sensor_policy],
        "mayak_context_policy": [_context_to_mapping(item) for item in card.mayak_context_policy],
        "dispatcher_context_policy": [
            _context_to_mapping(item) for item in card.dispatcher_context_policy
        ],
    }


def editable_from_stored_card(card_json: object) -> dict[str, object]:
    raw = _mapping(card_json, "card_json")
    touch = _mapping(raw.get("touch_policy"), "card_json.touch_policy")
    # Stored canonical dataclass JSON already represents touch rules structurally.
    editable_touch = {
        "accepted_touch_numbers": _list(
            touch.get("accepted_touch_numbers"), "accepted_touch_numbers"
        ),
        "accept_touch_from": touch.get("accept_touch_from"),
        "require_exit_from_zone": bool(touch.get("require_exit_from_zone")),
        "minimum_exit_distance": dict(
            _mapping(touch.get("minimum_exit_distance"), "minimum_exit_distance")
        ),
        "minimum_time_between_touches": dict(
            _mapping(touch.get("minimum_time_between_touches"), "minimum_time_between_touches")
        ),
        "maximum_touch_count": touch.get("maximum_touch_count"),
        "reset_on": _list(touch.get("reset_on"), "reset_on"),
        "candidate_cooldown": dict(_mapping(touch.get("candidate_cooldown"), "candidate_cooldown")),
    }
    return {
        "strategy_id": str(raw.get("strategy_id") or ""),
        "strategy_version": str(raw.get("strategy_version") or ""),
        "name": str(raw.get("name") or ""),
        "description": str(raw.get("description") or ""),
        "scope": _unwrap_policy(raw.get("scope"), "scope"),
        "symbols": list(_list(raw.get("symbols"), "symbols")),
        "direction_policy": list(_list(raw.get("direction_policy"), "direction_policy")),
        "entry_policy": _unwrap_policy(raw.get("entry_policy"), "entry_policy"),
        "exit_policy": _unwrap_policy(raw.get("exit_policy"), "exit_policy"),
        "capital_policy": _unwrap_policy(raw.get("capital_policy"), "capital_policy"),
        "protection_policy": _unwrap_policy(raw.get("protection_policy"), "protection_policy"),
        "lifecycle_policy": _unwrap_policy(raw.get("lifecycle_policy"), "lifecycle_policy"),
        "touch_policy": editable_touch,
        "market_sensor_policy": list(
            _list(raw.get("market_sensor_policy"), "market_sensor_policy")
        ),
        "mayak_context_policy": list(
            _list(raw.get("mayak_context_policy"), "mayak_context_policy")
        ),
        "dispatcher_context_policy": list(
            _list(raw.get("dispatcher_context_policy"), "dispatcher_context_policy")
        ),
    }


def card_from_editable(
    payload: Mapping[str, object], *, approved_at: datetime, approved_source: str
) -> StrategyCard:
    raw = _mapping(payload, "StrategyCard payload")
    required = (
        "strategy_id",
        "strategy_version",
        "name",
        "description",
        "scope",
        "symbols",
        "direction_policy",
        "entry_policy",
        "exit_policy",
        "capital_policy",
        "protection_policy",
        "lifecycle_policy",
        "touch_policy",
        "market_sensor_policy",
        "mayak_context_policy",
        "dispatcher_context_policy",
    )
    missing = tuple(name for name in required if name not in raw)
    if missing:
        raise ValueError(f"StrategyCard payload missing required fields: {missing}")
    symbols = tuple(str(item).upper() for item in _list(raw["symbols"], "symbols"))
    directions = tuple(
        TradeDirection(str(item)) for item in _list(raw["direction_policy"], "direction_policy")
    )
    sensors = tuple(
        _sensor_from_mapping(item)
        for item in _list(raw["market_sensor_policy"], "market_sensor_policy")
    )
    mayak_context = tuple(
        _context_from_mapping(item, "mayak_context_policy item")
        for item in _list(raw["mayak_context_policy"], "mayak_context_policy")
    )
    dispatcher_context = tuple(
        _context_from_mapping(item, "dispatcher_context_policy item")
        for item in _list(raw["dispatcher_context_policy"], "dispatcher_context_policy")
    )
    return StrategyCard.build(
        strategy_id=str(raw["strategy_id"]),
        strategy_version=str(raw["strategy_version"]),
        name=str(raw["name"]),
        description=str(raw["description"]),
        scope=FrozenPolicy.from_mapping(_mapping(raw["scope"], "scope")),
        symbols=symbols,
        direction_policy=directions,
        entry_policy=FrozenPolicy.from_mapping(_mapping(raw["entry_policy"], "entry_policy")),
        exit_policy=FrozenPolicy.from_mapping(_mapping(raw["exit_policy"], "exit_policy")),
        capital_policy=FrozenPolicy.from_mapping(_mapping(raw["capital_policy"], "capital_policy")),
        protection_policy=FrozenPolicy.from_mapping(
            _mapping(raw["protection_policy"], "protection_policy")
        ),
        lifecycle_policy=FrozenPolicy.from_mapping(
            _mapping(raw["lifecycle_policy"], "lifecycle_policy")
        ),
        touch_policy=_touch_from_mapping(raw["touch_policy"]),
        market_sensor_policy=sensors,
        mayak_context_policy=mayak_context,
        dispatcher_context_policy=dispatcher_context,
        approved_at=approved_at.astimezone(UTC),
        approved_source=approved_source,
    )


def build_new_strategy_version(
    base: StrategyCard,
    payload: Mapping[str, object],
    *,
    approved_at: datetime,
    approved_source: str,
) -> StrategyCard:
    raw = _mapping(payload, "new StrategyCard")
    if str(raw.get("strategy_id") or "") != base.strategy_id:
        raise ValueError("new Strategy version cannot change strategy_id")
    new_version = str(raw.get("strategy_version") or "")
    if not new_version or new_version == base.strategy_version:
        raise ValueError("new strategy_version must be explicit and different from base")
    return card_from_editable(raw, approved_at=approved_at, approved_source=approved_source)


def _identity(row: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(row.get("strategy_id") or ""),
        str(row.get("strategy_version") or ""),
        str(row.get("strategy_config_fingerprint") or ""),
    )


def _sections(editable: Mapping[str, object]) -> dict[str, object]:
    entry = _mapping(editable.get("entry_policy"), "entry_policy")
    watch = entry.get("watch_policy")
    watch_mapping = dict(watch) if isinstance(watch, Mapping) else None
    touch = _mapping(editable.get("touch_policy"), "touch_policy")
    return {
        "general": {
            "strategy_id": editable.get("strategy_id"),
            "strategy_version": editable.get("strategy_version"),
            "name": editable.get("name"),
            "description": editable.get("description"),
            "scope": editable.get("scope"),
            "symbols": editable.get("symbols"),
            "direction_policy": editable.get("direction_policy"),
        },
        "entry": editable.get("entry_policy"),
        "touch": editable.get("touch_policy"),
        "lifecycle": {
            "lifecycle_policy": editable.get("lifecycle_policy"),
            "candidate_cooldown": touch.get("candidate_cooldown"),
            "reset_on": touch.get("reset_on"),
        },
        "geometry_sensors": {
            "watch_policy": watch_mapping,
            "market_sensor_policy": editable.get("market_sensor_policy"),
        },
        "capital_leverage": editable.get("capital_policy"),
        "protection": editable.get("protection_policy"),
        "exit": editable.get("exit_policy"),
        "mayak_usage": editable.get("mayak_context_policy"),
        "dispatcher_usage": editable.get("dispatcher_context_policy"),
    }


def assemble_strategy_catalog(
    cards: Sequence[Mapping[str, object]],
    activations: Sequence[Mapping[str, object]],
    entry_plans: Sequence[Mapping[str, object]],
    exit_plans: Sequence[Mapping[str, object]],
    activation_events: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for card in cards:
        key = _identity(card)
        editable = editable_from_stored_card(card.get("card_json"))
        card_activations = [dict(row) for row in activations if _identity(row) == key]
        card_entry = [dict(row) for row in entry_plans if _identity(row) == key]
        card_exit = [dict(row) for row in exit_plans if _identity(row) == key]
        activation_ids = {str(row.get("activation_id")) for row in card_activations}
        history = [
            dict(row)
            for row in activation_events
            if str(row.get("activation_id")) in activation_ids
        ]
        if not card_activations:
            activation_state = "NOT SET"
        elif len(card_activations) == 1:
            activation_state = "ENABLED" if bool(card_activations[0].get("enabled")) else "DISABLED"
        else:
            activation_state = "MULTIPLE"
        result.append(
            {
                "strategy_id": key[0],
                "strategy_version": key[1],
                "strategy_config_fingerprint": key[2],
                "name": card.get("name"),
                "description": card.get("description"),
                "direction_policy": editable.get("direction_policy"),
                "scope": editable.get("scope"),
                "symbols": editable.get("symbols"),
                "activation_state": activation_state,
                "activations": card_activations,
                "entry_plan_fingerprints": [
                    str(row.get("entry_plan_fingerprint")) for row in card_entry
                ],
                "exit_plan_fingerprints": [
                    str(row.get("exit_plan_fingerprint")) for row in card_exit
                ],
                "entry_plans": card_entry,
                "exit_plans": card_exit,
                "activation_history": history,
                "approved_at": card.get("approved_at"),
                "approved_source": card.get("approved_source"),
                "editable_card": editable,
                "sections": _sections(editable),
                "read_only": True,
            }
        )
    return result


_CARD_COLUMNS = (
    "strategy_id",
    "strategy_version",
    "strategy_config_fingerprint",
    "name",
    "description",
    "card_json",
    "approved_at",
    "approved_source",
    "created_at",
)
_ACTIVATION_COLUMNS = (
    "activation_id",
    "strategy_id",
    "strategy_version",
    "strategy_config_fingerprint",
    "enabled",
    "enabled_at",
    "disabled_at",
    "scope",
    "operator",
    "source",
    "change_reason",
    "created_at",
    "updated_at",
)
_ENTRY_PLAN_COLUMNS = (
    "entry_plan_fingerprint",
    "strategy_id",
    "strategy_version",
    "strategy_config_fingerprint",
    "entry_plan_version",
    "created_at",
)
_EXIT_PLAN_COLUMNS = (
    "exit_plan_fingerprint",
    "strategy_id",
    "strategy_version",
    "strategy_config_fingerprint",
    "exit_plan_version",
    "created_at",
)
_EVENT_COLUMNS = (
    "activation_event_id",
    "activation_id",
    "occurred_at",
    "enabled",
    "enabled_at",
    "disabled_at",
    "operator",
    "source",
    "reason",
    "scope",
)


def _dict_rows(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> list[dict[str, object]]:
    return [dict(zip(columns, row, strict=True)) for row in rows]


class StrategyDashboardStore:
    """Narrow U6 Strategy read/control store; no Execution or market mutation API."""

    def __init__(self, connection: DashboardConnectionLike) -> None:
        self._connection = connection

    def list_catalog(self) -> list[dict[str, object]]:
        cards = _dict_rows(
            _CARD_COLUMNS,
            self._connection.execute(
                """SELECT strategy_id,strategy_version,strategy_config_fingerprint,
                          name,description,card_json,approved_at,approved_source,created_at
                   FROM strategy_entry.strategy_cards
                   ORDER BY strategy_id,strategy_version,strategy_config_fingerprint"""
            ).fetchall(),
        )
        activations = _dict_rows(
            _ACTIVATION_COLUMNS,
            self._connection.execute(
                """SELECT activation_id,strategy_id,strategy_version,
                          strategy_config_fingerprint,enabled,enabled_at,disabled_at,
                          scope,operator,source,change_reason,created_at,updated_at
                   FROM strategy_entry.strategy_activations
                   ORDER BY strategy_id,strategy_version,created_at,activation_id"""
            ).fetchall(),
        )
        entry_plans = _dict_rows(
            _ENTRY_PLAN_COLUMNS,
            self._connection.execute(
                """SELECT entry_plan_fingerprint,strategy_id,strategy_version,
                          strategy_config_fingerprint,entry_plan_version,created_at
                   FROM strategy_entry.entry_plans
                   ORDER BY strategy_id,strategy_version,entry_plan_version,
                            entry_plan_fingerprint"""
            ).fetchall(),
        )
        exit_plans = _dict_rows(
            _EXIT_PLAN_COLUMNS,
            self._connection.execute(
                """SELECT exit_plan_fingerprint,strategy_id,strategy_version,
                          strategy_config_fingerprint,exit_plan_version,created_at
                   FROM strategy_entry.exit_plans
                   ORDER BY strategy_id,strategy_version,exit_plan_version,
                            exit_plan_fingerprint"""
            ).fetchall(),
        )
        events = _dict_rows(
            _EVENT_COLUMNS,
            self._connection.execute(
                """SELECT activation_event_id,activation_id,occurred_at,enabled,
                          enabled_at,disabled_at,operator,source,reason,scope
                   FROM strategy_entry.strategy_activation_events
                   ORDER BY activation_event_id"""
            ).fetchall(),
        )
        return assemble_strategy_catalog(cards, activations, entry_plans, exit_plans, events)

    def _load_exact_base(
        self, strategy_id: str, strategy_version: str, strategy_config_fingerprint: str
    ) -> StrategyCard:
        row = self._connection.execute(
            """SELECT card_json,approved_at,approved_source
               FROM strategy_entry.strategy_cards
               WHERE strategy_id=%s AND strategy_version=%s
                 AND strategy_config_fingerprint=%s""",
            (strategy_id, strategy_version, strategy_config_fingerprint),
        ).fetchone()
        if row is None:
            raise KeyError("exact base StrategyCard not found")
        editable = editable_from_stored_card(row[0])
        approved_at = row[1]
        if not isinstance(approved_at, datetime):
            approved_at = datetime.fromisoformat(str(approved_at))
        card = card_from_editable(
            editable,
            approved_at=approved_at,
            approved_source=str(row[2]),
        )
        if card.strategy_config_fingerprint != strategy_config_fingerprint:
            raise ValueError("stored StrategyCard fingerprint does not reproduce canonically")
        return card

    def create_new_version(
        self,
        *,
        base_strategy_id: str,
        base_strategy_version: str,
        base_strategy_config_fingerprint: str,
        payload: Mapping[str, object],
        approved_at: datetime,
        operator: str,
    ) -> StrategyCard:
        with self._connection.transaction():
            base = self._load_exact_base(
                base_strategy_id,
                base_strategy_version,
                base_strategy_config_fingerprint,
            )
            created = build_new_strategy_version(
                base,
                payload,
                approved_at=approved_at,
                approved_source=f"dashboard:{operator}",
            )
            StrategyEntryStore(self._connection).insert_strategy_card(created)
        return created

    def set_activation_enabled_cas(
        self,
        *,
        activation_id: str,
        strategy_id: str,
        strategy_version: str,
        strategy_config_fingerprint: str,
        expected_enabled: bool,
        expected_updated_at: datetime,
        enabled: bool,
        changed_at: datetime,
        operator: str,
        source: str,
        reason: str,
    ) -> dict[str, object]:
        with self._connection.transaction():
            row = self._connection.execute(
                """SELECT activation_id,strategy_id,strategy_version,
                          strategy_config_fingerprint,enabled,enabled_at,disabled_at,
                          operator,source,updated_at
                   FROM strategy_entry.strategy_activations
                   WHERE activation_id=%s
                   FOR UPDATE""",
                (activation_id,),
            ).fetchone()
            if row is None:
                raise UnknownActivation(activation_id)
            current_updated_at = row[9]
            if not isinstance(current_updated_at, datetime):
                current_updated_at = datetime.fromisoformat(str(current_updated_at))
            current_identity = (str(row[1]), str(row[2]), str(row[3]))
            expected_identity = (
                strategy_id,
                strategy_version,
                strategy_config_fingerprint,
            )
            if (
                current_identity != expected_identity
                or bool(row[4]) != expected_enabled
                or current_updated_at != expected_updated_at
            ):
                raise StaleActivationState("STALE_ACTIVATION_STATE")
            if bool(row[4]) == enabled:
                return {
                    "status": "NO_CHANGE",
                    "activation_id": activation_id,
                    "enabled": enabled,
                    "updated_at": current_updated_at,
                }
            cursor = self._connection.execute(
                """UPDATE strategy_entry.strategy_activations
                   SET enabled=%s,
                       enabled_at=CASE WHEN %s THEN %s ELSE enabled_at END,
                       disabled_at=CASE WHEN %s THEN NULL ELSE %s END,
                       operator=%s,source=%s,change_reason=%s
                   WHERE activation_id=%s
                     AND strategy_id=%s AND strategy_version=%s
                     AND strategy_config_fingerprint=%s
                     AND enabled=%s AND updated_at=%s
                   RETURNING updated_at""",
                (
                    enabled,
                    enabled,
                    changed_at,
                    enabled,
                    changed_at,
                    operator,
                    source,
                    reason,
                    activation_id,
                    strategy_id,
                    strategy_version,
                    strategy_config_fingerprint,
                    expected_enabled,
                    expected_updated_at,
                ),
            )
            persisted = cursor.fetchone()
            if cursor.rowcount != 1 or persisted is None:
                raise StaleActivationState("STALE_ACTIVATION_STATE")
            persisted_updated_at = persisted[0]
            if not isinstance(persisted_updated_at, datetime):
                persisted_updated_at = datetime.fromisoformat(str(persisted_updated_at))
            return {
                "status": "UPDATED",
                "activation_id": activation_id,
                "enabled": enabled,
                "updated_at": persisted_updated_at,
            }
