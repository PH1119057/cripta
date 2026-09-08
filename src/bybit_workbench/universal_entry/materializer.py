from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from .contracts import (
    CooldownScope,
    DataQuality,
    EntryEmbargoPolicy,
    EntryPlan,
    ExitPlan,
    FrozenPolicy,
    PostSignalOutcomePolicy,
    StrategyActivation,
    StrategyCard,
)
from .dsl import predicate_from_mapping
from .fingerprint import fingerprint


def _decimal_value(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"invalid {name}") from None
    if not result.is_finite():
        raise ValueError(f"invalid {name}")
    return result


def _post_signal_policy(card: StrategyCard) -> PostSignalOutcomePolicy:
    lifecycle = card.lifecycle_policy.to_dict()
    raw = lifecycle.get("post_signal_outcome_policy")
    if not isinstance(raw, Mapping) or "enabled" not in raw:
        raise ValueError("lifecycle_policy requires explicit post_signal_outcome_policy.enabled")
    enabled = bool(raw["enabled"])
    if not enabled:
        return PostSignalOutcomePolicy(enabled=False)
    embargo_raw = raw.get("optional_embargo")
    if not isinstance(embargo_raw, Mapping) or "enabled" not in embargo_raw:
        raise ValueError("enabled post-signal policy requires explicit optional_embargo.enabled")
    embargo_enabled = bool(embargo_raw["enabled"])
    embargo = EntryEmbargoPolicy(enabled=False)
    if embargo_enabled:
        try:
            scope = CooldownScope(str(embargo_raw.get("scope")))
        except ValueError:
            raise ValueError("invalid post-signal embargo scope") from None
        embargo = EntryEmbargoPolicy(
            enabled=True,
            on_resolution=str(embargo_raw.get("on_resolution") or ""),
            duration=_decimal_value(embargo_raw.get("duration"), "embargo duration"),
            unit=str(embargo_raw.get("unit") or ""),
            scope=scope,
            anchor=str(embargo_raw.get("anchor") or ""),
        )
    return PostSignalOutcomePolicy(
        enabled=True,
        observation_event_kind=str(raw.get("observation_event_kind") or ""),
        reference_value_path=str(raw.get("reference_value_path") or ""),
        observation_value_path=str(raw.get("observation_value_path") or ""),
        metric=str(raw.get("metric") or ""),
        favorable_threshold=_decimal_value(raw.get("favorable_threshold"), "favorable threshold"),
        adverse_threshold=_decimal_value(raw.get("adverse_threshold"), "adverse threshold"),
        horizon=_decimal_value(raw.get("horizon"), "post-signal horizon"),
        horizon_unit=str(raw.get("horizon_unit") or ""),
        resolution_semantics=str(raw.get("resolution_semantics") or ""),
        favorable_resulting_entry_state=str(raw.get("favorable_resulting_entry_state") or ""),
        adverse_resulting_entry_state=str(raw.get("adverse_resulting_entry_state") or ""),
        optional_embargo=embargo,
    )


def _validate_capital_policy(card: StrategyCard) -> None:
    payload = card.capital_policy.to_dict()
    if not bool(payload.get("require_capacity", False)):
        return
    required = (
        "requested_amount",
        "capacity_max_age_seconds",
        "capacity_min_quality",
    )
    missing = tuple(field for field in required if payload.get(field) is None)
    if missing:
        raise ValueError(f"capacity policy missing required fields: {missing}")
    try:
        requested = Decimal(str(payload["requested_amount"]))
        max_age = int(str(payload["capacity_max_age_seconds"]))
        DataQuality(str(payload["capacity_min_quality"]))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("capacity policy contains invalid values") from None
    if requested <= 0:
        raise ValueError("requested_amount must be positive")
    if max_age < 0:
        raise ValueError("capacity_max_age_seconds cannot be negative")


def materialize_plans(
    card: StrategyCard, activation: StrategyActivation
) -> tuple[EntryPlan, ExitPlan]:
    if (
        activation.strategy_id != card.strategy_id
        or activation.strategy_version != card.strategy_version
        or activation.strategy_config_fingerprint != card.strategy_config_fingerprint
    ):
        raise ValueError("activation does not bind the exact StrategyCard")
    _validate_capital_policy(card)
    entry_raw = card.entry_policy.to_dict()
    version_raw = entry_raw.get("entry_plan_version")
    predicate_raw = entry_raw.get("predicate")
    watch_raw = entry_raw.get("watch_policy")
    if not version_raw:
        raise ValueError("entry_policy requires entry_plan_version")
    if not isinstance(predicate_raw, Mapping):
        raise ValueError("entry_policy requires predicate object")
    if not isinstance(watch_raw, Mapping) or "enabled" not in watch_raw:
        raise ValueError("entry_policy requires explicit watch_policy.enabled")
    predicate = predicate_from_mapping(predicate_raw)
    watch_policy = FrozenPolicy.from_mapping(watch_raw)
    post_signal_policy = _post_signal_policy(card)
    context_policy = card.mayak_context_policy + card.dispatcher_context_policy
    entry_payload = {
        "strategy_fingerprint": card.strategy_config_fingerprint,
        "entry_plan_version": str(version_raw),
        "symbols": card.symbols,
        "directions": card.direction_policy,
        "predicate": predicate,
        "watch_policy": watch_policy,
        "touch_policy": card.touch_policy,
        "sensor_policy": card.market_sensor_policy,
        "context_policy": context_policy,
        "capital_policy": card.capital_policy,
        "lifecycle_policy": card.lifecycle_policy,
        "post_signal_outcome_policy": post_signal_policy,
    }
    entry_plan = EntryPlan(
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        strategy_activation_id=activation.activation_id,
        entry_plan_version=str(version_raw),
        entry_plan_fingerprint=fingerprint(entry_payload),
        symbols=card.symbols,
        directions=card.direction_policy,
        predicate=predicate,
        watch_policy=watch_policy,
        touch_policy=card.touch_policy,
        sensor_policy=card.market_sensor_policy,
        context_policy=context_policy,
        capital_policy=card.capital_policy,
        lifecycle_policy=card.lifecycle_policy,
        post_signal_outcome_policy=post_signal_policy,
    )
    exit_raw = card.exit_policy.to_dict()
    exit_version = str(exit_raw.get("exit_plan_version") or card.strategy_version)
    exit_payload = {
        "strategy_fingerprint": card.strategy_config_fingerprint,
        "exit_plan_version": exit_version,
        "exit_policy": card.exit_policy,
        "protection_policy": card.protection_policy,
    }
    exit_plan = ExitPlan(
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        strategy_activation_id=activation.activation_id,
        exit_plan_version=exit_version,
        exit_plan_fingerprint=fingerprint(exit_payload),
        exit_policy=card.exit_policy,
        protection_policy=card.protection_policy,
    )
    return entry_plan, exit_plan
