from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from .contracts import (
    CandidateCooldown,
    ContextFailureAction,
    ContextMode,
    ContextRequirement,
    CooldownScope,
    DataQuality,
    EntryPlan,
    ExitPlan,
    FrozenPolicy,
    NumericRule,
    SensorRequirement,
    StrategyActivation,
    StrategyCard,
    TouchPolicy,
    TradeDirection,
)
from .materializer import materialize_plans


class RuntimeCursorLike(Protocol):
    def fetchall(self) -> list[tuple[object, ...]]: ...


class RuntimeConnectionLike(Protocol):
    def execute(
        self, statement: str, parameters: Sequence[object] = ()
    ) -> RuntimeCursorLike: ...


@dataclass(frozen=True, slots=True)
class ActiveStrategyBundle:
    card: StrategyCard
    activation: StrategyActivation
    entry_plan: EntryPlan
    exit_plan: ExitPlan
    activation_updated_at: datetime

    @property
    def exact_identity(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.activation.activation_id,
            self.card.strategy_id,
            self.card.strategy_version,
            self.card.strategy_config_fingerprint,
            self.entry_plan.entry_plan_fingerprint,
            self.exit_plan.exit_plan_fingerprint,
        )


def _mapping(value: object, label: str) -> dict[str, object]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list")
    return list(value)


def _unwrap_policy(value: object, label: str) -> dict[str, object]:
    raw = _mapping(value, label)
    if set(raw) == {"payload_json"}:
        payload = raw.get("payload_json")
        if not isinstance(payload, str):
            raise ValueError(f"{label}.payload_json must be text")
        decoded = json.loads(payload)
        return _mapping(decoded, label)
    return raw


def _aware(value: object, label: str) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if result.tzinfo is None:
        raise ValueError(f"{label} must include timezone")
    return result.astimezone(UTC)


def _enum[T](enum_type: type[T], value: object | None, label: str) -> T | None:
    if value is None:
        return None
    try:
        return enum_type(str(value))  # type: ignore[call-arg]
    except ValueError:
        raise ValueError(f"invalid {label}: {value}") from None


def _numeric(value: object, label: str) -> NumericRule:
    raw = _mapping(value, label)
    enabled = bool(raw.get("enabled", False))
    if not enabled:
        return NumericRule(enabled=False)
    return NumericRule(
        enabled=True,
        value=Decimal(str(raw.get("value"))),
        unit=str(raw.get("unit") or ""),
        scope=None if raw.get("scope") is None else str(raw.get("scope")),
    )


def _cooldown(value: object) -> CandidateCooldown:
    raw = _mapping(value, "candidate_cooldown")
    if not bool(raw.get("enabled", False)):
        return CandidateCooldown(enabled=False)
    scope = _enum(CooldownScope, raw.get("scope"), "candidate_cooldown.scope")
    if scope is None:
        raise ValueError("candidate_cooldown.scope is required")
    return CandidateCooldown(
        enabled=True,
        duration=Decimal(str(raw.get("duration"))),
        unit=str(raw.get("unit") or ""),
        scope=scope,
        trigger_event=str(raw.get("trigger_event") or ""),
        anchor=str(raw.get("anchor") or ""),
    )


def _touch(value: object) -> TouchPolicy:
    raw = _mapping(value, "touch_policy")
    return TouchPolicy(
        accepted_touch_numbers=tuple(
            int(str(item)) for item in _list(raw.get("accepted_touch_numbers"), "touch numbers")
        ),
        accept_touch_from=(
            None if raw.get("accept_touch_from") is None else int(str(raw["accept_touch_from"]))
        ),
        require_exit_from_zone=bool(raw.get("require_exit_from_zone", False)),
        minimum_exit_distance=_numeric(
            raw.get("minimum_exit_distance"), "minimum_exit_distance"
        ),
        minimum_time_between_touches=_numeric(
            raw.get("minimum_time_between_touches"), "minimum_time_between_touches"
        ),
        maximum_touch_count=(
            None
            if raw.get("maximum_touch_count") is None
            else int(str(raw["maximum_touch_count"]))
        ),
        reset_on=tuple(str(item) for item in _list(raw.get("reset_on"), "reset_on")),
        candidate_cooldown=_cooldown(raw.get("candidate_cooldown")),
    )


def _sensor(value: object) -> SensorRequirement:
    raw = _mapping(value, "sensor requirement")
    return SensorRequirement(
        sensor_id=str(raw.get("sensor_id") or ""),
        mode=ContextMode(str(raw.get("mode"))),
        max_age_seconds=(
            None if raw.get("max_age_seconds") is None else int(str(raw["max_age_seconds"]))
        ),
        min_quality=_enum(DataQuality, raw.get("min_quality"), "sensor min_quality"),
        on_missing=_enum(ContextFailureAction, raw.get("on_missing"), "sensor on_missing"),
        on_stale=_enum(ContextFailureAction, raw.get("on_stale"), "sensor on_stale"),
        on_partial=_enum(ContextFailureAction, raw.get("on_partial"), "sensor on_partial"),
    )


def _context(value: object) -> ContextRequirement:
    raw = _mapping(value, "context requirement")
    return ContextRequirement(
        context_id=str(raw.get("context_id") or ""),
        mode=ContextMode(str(raw.get("mode"))),
        max_age_seconds=(
            None if raw.get("max_age_seconds") is None else int(str(raw["max_age_seconds"]))
        ),
        min_quality=_enum(DataQuality, raw.get("min_quality"), "context min_quality"),
        on_missing=_enum(ContextFailureAction, raw.get("on_missing"), "context on_missing"),
        on_stale=_enum(ContextFailureAction, raw.get("on_stale"), "context on_stale"),
        on_partial=_enum(ContextFailureAction, raw.get("on_partial"), "context on_partial"),
    )


def strategy_card_from_storage(
    card_json: object,
    *,
    approved_at: object,
    approved_source: object,
) -> StrategyCard:
    raw = _mapping(card_json, "strategy_cards.card_json")
    card = StrategyCard.build(
        strategy_id=str(raw.get("strategy_id") or ""),
        strategy_version=str(raw.get("strategy_version") or ""),
        name=str(raw.get("name") or ""),
        description=str(raw.get("description") or ""),
        scope=FrozenPolicy.from_mapping(_unwrap_policy(raw.get("scope"), "scope")),
        symbols=tuple(str(item) for item in _list(raw.get("symbols"), "symbols")),
        direction_policy=tuple(
            TradeDirection(str(item))
            for item in _list(raw.get("direction_policy"), "direction_policy")
        ),
        entry_policy=FrozenPolicy.from_mapping(
            _unwrap_policy(raw.get("entry_policy"), "entry_policy")
        ),
        exit_policy=FrozenPolicy.from_mapping(
            _unwrap_policy(raw.get("exit_policy"), "exit_policy")
        ),
        capital_policy=FrozenPolicy.from_mapping(
            _unwrap_policy(raw.get("capital_policy"), "capital_policy")
        ),
        protection_policy=FrozenPolicy.from_mapping(
            _unwrap_policy(raw.get("protection_policy"), "protection_policy")
        ),
        lifecycle_policy=FrozenPolicy.from_mapping(
            _unwrap_policy(raw.get("lifecycle_policy"), "lifecycle_policy")
        ),
        touch_policy=_touch(raw.get("touch_policy")),
        market_sensor_policy=tuple(
            _sensor(item)
            for item in _list(raw.get("market_sensor_policy"), "market_sensor_policy")
        ),
        mayak_context_policy=tuple(
            _context(item)
            for item in _list(raw.get("mayak_context_policy"), "mayak_context_policy")
        ),
        dispatcher_context_policy=tuple(
            _context(item)
            for item in _list(
                raw.get("dispatcher_context_policy"), "dispatcher_context_policy"
            )
        ),
        approved_at=_aware(approved_at, "approved_at"),
        approved_source=str(approved_source),
    )
    stored_fp = str(raw.get("strategy_config_fingerprint") or "")
    if stored_fp and stored_fp != card.strategy_config_fingerprint:
        raise ValueError("stored StrategyCard fingerprint does not reproduce")
    return card


def load_active_strategy_bundles(
    connection: RuntimeConnectionLike,
) -> tuple[ActiveStrategyBundle, ...]:
    rows = connection.execute(
        """SELECT
               a.activation_id,a.strategy_id,a.strategy_version,
               a.strategy_config_fingerprint,a.enabled,a.enabled_at,a.disabled_at,
               a.scope,a.operator,a.source,a.updated_at,
               c.card_json,c.approved_at,c.approved_source,
               ep.entry_plan_fingerprint,xp.exit_plan_fingerprint
           FROM strategy_entry.strategy_activations a
           JOIN strategy_entry.strategy_cards c
             ON c.strategy_id=a.strategy_id
            AND c.strategy_version=a.strategy_version
            AND c.strategy_config_fingerprint=a.strategy_config_fingerprint
           JOIN strategy_entry.entry_plans ep
             ON ep.strategy_id=a.strategy_id
            AND ep.strategy_version=a.strategy_version
            AND ep.strategy_config_fingerprint=a.strategy_config_fingerprint
           JOIN strategy_entry.exit_plans xp
             ON xp.strategy_id=a.strategy_id
            AND xp.strategy_version=a.strategy_version
            AND xp.strategy_config_fingerprint=a.strategy_config_fingerprint
           WHERE a.enabled=true
           ORDER BY a.activation_id,ep.entry_plan_fingerprint,xp.exit_plan_fingerprint"""
    ).fetchall()
    by_activation: dict[str, list[tuple[object, ...]]] = {}
    for row in rows:
        by_activation.setdefault(str(row[0]), []).append(row)
    bundles: list[ActiveStrategyBundle] = []
    for activation_id, matches in sorted(by_activation.items()):
        if len(matches) != 1:
            raise RuntimeError(
                f"active Strategy {activation_id} must have exactly one EntryPlan and ExitPlan"
            )
        row = matches[0]
        card = strategy_card_from_storage(
            row[11], approved_at=row[12], approved_source=row[13]
        )
        activation = StrategyActivation(
            activation_id=activation_id,
            strategy_id=str(row[1]),
            strategy_version=str(row[2]),
            strategy_config_fingerprint=str(row[3]),
            enabled=bool(row[4]),
            enabled_at=_aware(row[5], "enabled_at"),
            disabled_at=None if row[6] is None else _aware(row[6], "disabled_at"),
            scope=FrozenPolicy.from_mapping(_mapping(row[7], "activation.scope")),
            operator=str(row[8]),
            source=str(row[9]),
        )
        if not activation.enabled:
            raise RuntimeError("disabled activation leaked into active query")
        entry_plan, exit_plan = materialize_plans(card, activation)
        if entry_plan.entry_plan_fingerprint != str(row[14]):
            raise RuntimeError(
                f"active Strategy {activation_id} EntryPlan fingerprint mismatch"
            )
        if exit_plan.exit_plan_fingerprint != str(row[15]):
            raise RuntimeError(
                f"active Strategy {activation_id} ExitPlan fingerprint mismatch"
            )
        bundles.append(
            ActiveStrategyBundle(
                card,
                activation,
                entry_plan,
                exit_plan,
                _aware(row[10], "activation.updated_at"),
            )
        )
    return tuple(bundles)


def active_bundle_signature(
    bundles: Sequence[ActiveStrategyBundle],
) -> tuple[tuple[str, str, str, str, str, str, str], ...]:
    return tuple(
        sorted(
            bundle.exact_identity + (bundle.activation_updated_at.isoformat(),)
            for bundle in bundles
        )
    )
