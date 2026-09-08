from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from .contracts import DataQuality, EntryPlan, ExitPlan, StrategyActivation, StrategyCard
from .dsl import predicate_from_mapping
from .fingerprint import fingerprint


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
    if not version_raw:
        raise ValueError("entry_policy requires entry_plan_version")
    if not isinstance(predicate_raw, Mapping):
        raise ValueError("entry_policy requires predicate object")
    predicate = predicate_from_mapping(predicate_raw)
    context_policy = card.mayak_context_policy + card.dispatcher_context_policy
    entry_payload = {
        "strategy_fingerprint": card.strategy_config_fingerprint,
        "entry_plan_version": str(version_raw),
        "symbols": card.symbols,
        "directions": card.direction_policy,
        "predicate": predicate,
        "touch_policy": card.touch_policy,
        "sensor_policy": card.market_sensor_policy,
        "context_policy": context_policy,
        "capital_policy": card.capital_policy,
        "lifecycle_policy": card.lifecycle_policy,
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
        touch_policy=card.touch_policy,
        sensor_policy=card.market_sensor_policy,
        context_policy=context_policy,
        capital_policy=card.capital_policy,
        lifecycle_policy=card.lifecycle_policy,
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
