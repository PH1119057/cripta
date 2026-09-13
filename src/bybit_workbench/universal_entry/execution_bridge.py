from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .contracts import ExecutionRequest, TradeDirection
from .fingerprint import canonical_json, fingerprint


class ExecutionBridgeBlockCode(StrEnum):
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    ACTIVATION_DISABLED = "ACTIVATION_DISABLED"
    POLICY_INCOMPLETE = "POLICY_INCOMPLETE"
    POLICY_UNSUPPORTED = "POLICY_UNSUPPORTED"
    REQUEST_EXPIRED = "REQUEST_EXPIRED"
    REFERENCE_PRICE_MISSING = "REFERENCE_PRICE_MISSING"


class ExecutionBridgeBlocked(ValueError):
    def __init__(self, code: ExecutionBridgeBlockCode, reason: str) -> None:
        super().__init__(f"{code.value}:{reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PreparedRuntimeEntryCommand:
    command_id: str
    execution_request_id: str
    strategy_attempt_id: str
    entry_decision_id: str
    signal_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    entry_plan_fingerprint: str
    exit_plan_fingerprint: str
    strategy_activation_id: str
    symbol: str
    direction: TradeDirection
    requested_at: datetime
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class BridgePolicyBundle:
    strategy_card: Mapping[str, object]
    entry_plan: Mapping[str, object]
    exit_plan: Mapping[str, object]
    activation: Mapping[str, object]


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.POLICY_INCOMPLETE, f"{label} must be an object"
        )
    return value


def _unwrap_policy(value: object, label: str) -> Mapping[str, object]:
    raw = _mapping(value, label)
    if set(raw) == {"payload_json"}:
        payload_json = raw.get("payload_json")
        if not isinstance(payload_json, str):
            raise ExecutionBridgeBlocked(
                ExecutionBridgeBlockCode.POLICY_INCOMPLETE,
                f"{label}.payload_json must be text",
            )
        try:
            decoded = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise ExecutionBridgeBlocked(
                ExecutionBridgeBlockCode.POLICY_INCOMPLETE,
                f"{label}.payload_json is invalid JSON",
            ) from exc
        return _mapping(decoded, label)
    return raw


def _require_text(raw: Mapping[str, object], key: str, label: str) -> str:
    value = raw.get(key)
    rendered = "" if value is None else str(value).strip()
    if not rendered:
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.POLICY_INCOMPLETE,
            f"{label}.{key} is required",
        )
    return rendered


def _decimal(value: object, label: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.POLICY_INCOMPLETE, f"{label} is not decimal"
        ) from None
    if not result.is_finite() or (positive and result <= 0):
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.POLICY_INCOMPLETE, f"{label} is invalid"
        )
    return result


def _integer(value: object, label: str, *, positive: bool = False) -> int:
    try:
        result = int(str(value))
    except (ValueError, TypeError):
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.POLICY_INCOMPLETE, f"{label} is not integer"
        ) from None
    if positive and result <= 0:
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.POLICY_INCOMPLETE, f"{label} must be positive"
        )
    return result


def _require_identity(raw: Mapping[str, object], request: ExecutionRequest, label: str) -> None:
    expected = {
        "strategy_id": request.strategy_id,
        "strategy_version": request.strategy_version,
        "strategy_config_fingerprint": request.strategy_config_fingerprint,
    }
    for key, value in expected.items():
        if str(raw.get(key) or "") != value:
            raise ExecutionBridgeBlocked(
                ExecutionBridgeBlockCode.IDENTITY_MISMATCH,
                f"{label}.{key} does not match ExecutionRequest",
            )


def _resolve_fact_reference(request: ExecutionRequest, path: str) -> Decimal:
    if not path.startswith("fact."):
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.POLICY_UNSUPPORTED,
            "execution reference_value_path must start with fact.",
        )
    payload = request.payload.to_dict()
    signal_fact = _mapping(payload.get("signal_fact"), "ExecutionRequest.signal_fact")
    attrs = _mapping(signal_fact.get("attributes"), "ExecutionRequest.signal_fact.attributes")
    value: object = attrs
    for part in path.removeprefix("fact.").split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise ExecutionBridgeBlocked(
                ExecutionBridgeBlockCode.REFERENCE_PRICE_MISSING,
                f"execution reference path is missing: {path}",
            )
        value = value[part]
    return _decimal(value, f"execution reference {path}", positive=True)


def _validate_policy_identity(
    request: ExecutionRequest,
    bundle: BridgePolicyBundle,
) -> tuple[
    str,
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
]:
    card = _mapping(bundle.strategy_card, "StrategyCard")
    entry = _mapping(bundle.entry_plan, "EntryPlan")
    exit_plan = _mapping(bundle.exit_plan, "ExitPlan")
    activation = _mapping(bundle.activation, "StrategyActivation")
    _require_identity(card, request, "StrategyCard")
    _require_identity(entry, request, "EntryPlan")
    _require_identity(exit_plan, request, "ExitPlan")
    _require_identity(activation, request, "StrategyActivation")
    if str(entry.get("entry_plan_fingerprint") or "") != request.entry_plan_fingerprint:
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.IDENTITY_MISMATCH,
            "EntryPlan fingerprint does not match ExecutionRequest",
        )
    activation_id = _require_text(activation, "activation_id", "StrategyActivation")
    if not bool(activation.get("enabled")):
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.ACTIVATION_DISABLED,
            "exact StrategyActivation is disabled",
        )
    _require_text(exit_plan, "exit_plan_fingerprint", "ExitPlan")
    return activation_id, card, entry, exit_plan, activation


def prepare_runtime_entry_command(
    request: ExecutionRequest,
    bundle: BridgePolicyBundle,
    *,
    now: datetime,
) -> PreparedRuntimeEntryCommand:
    """Translate immutable Universal Entry lineage to the existing Execution command contract.

    This is a pure adapter. It never performs persistence or exchange mutation.
    Missing Strategy-owned live policy is fail-closed; legacy global trading settings are never used
    as fallback defaults.
    """

    current = now.astimezone(UTC)
    activation_id, card, entry_plan, exit_plan, _activation = _validate_policy_identity(
        request, bundle
    )

    entry_policy = _unwrap_policy(card.get("entry_policy"), "StrategyCard.entry_policy")
    execution_policy = _mapping(
        entry_policy.get("execution_policy"), "StrategyCard.entry_policy.execution_policy"
    )
    capital_policy = _unwrap_policy(card.get("capital_policy"), "StrategyCard.capital_policy")
    protection_policy = _unwrap_policy(
        exit_plan.get("protection_policy"), "ExitPlan.protection_policy"
    )

    request_payload = request.payload.to_dict()
    request_capital = _mapping(
        request_payload.get("capital_policy"), "ExecutionRequest.capital_policy"
    )
    if canonical_json(request_capital) != canonical_json(capital_policy):
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.IDENTITY_MISMATCH,
            "ExecutionRequest capital policy differs from immutable StrategyCard",
        )

    card_policy_pairs = (
        ("entry_reference_policy", entry_policy.get("entry_reference_policy")),
        ("local_entry_policy", entry_policy.get("local_entry_policy")),
        (
            "context_feature_policy",
            {"features": entry_policy.get("context_feature_policy", [])},
        ),
        ("context_ranking_policy", entry_policy.get("context_ranking_policy")),
    )
    for field, card_value in card_policy_pairs:
        card_present = field in entry_policy
        plan_present = field in entry_plan
        request_present = field in request_payload
        if not (card_present or plan_present or request_present):
            # Historical immutable Strategy/Plan/Request created before this policy
            # existed.  Absence is accepted only when all three layers agree.
            continue
        if not (card_present and plan_present and request_present):
            raise ExecutionBridgeBlocked(
                ExecutionBridgeBlockCode.IDENTITY_MISMATCH,
                f"{field} presence differs across StrategyCard/EntryPlan/ExecutionRequest",
            )
        request_value = _mapping(request_payload.get(field), f"ExecutionRequest.{field}")
        plan_value = _unwrap_policy(entry_plan.get(field), f"EntryPlan.{field}")
        card_mapping = _mapping(card_value, f"StrategyCard.entry_policy.{field}")
        if canonical_json(request_value) != canonical_json(plan_value):
            raise ExecutionBridgeBlocked(
                ExecutionBridgeBlockCode.IDENTITY_MISMATCH,
                f"ExecutionRequest {field} differs from immutable EntryPlan",
            )
        if canonical_json(plan_value) != canonical_json(card_mapping):
            raise ExecutionBridgeBlocked(
                ExecutionBridgeBlockCode.IDENTITY_MISMATCH,
                f"EntryPlan {field} differs from immutable StrategyCard",
            )

    amount_currency = _require_text(capital_policy, "amount_currency", "capital_policy").upper()
    if amount_currency != "USDT":
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.POLICY_UNSUPPORTED,
            "current Execution adapter supports USDT capital only",
        )
    requested_amount = _decimal(
        capital_policy.get("requested_amount"), "capital_policy.requested_amount", positive=True
    )
    leverage = _integer(capital_policy.get("leverage"), "capital_policy.leverage", positive=True)

    max_age = _integer(
        execution_policy.get("max_request_age_seconds"),
        "execution_policy.max_request_age_seconds",
        positive=True,
    )
    age = (current - request.requested_at.astimezone(UTC)).total_seconds()
    if age < 0 or age > max_age:
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.REQUEST_EXPIRED,
            f"ExecutionRequest age {age:.3f}s exceeds Strategy policy",
        )

    reference_path = _require_text(execution_policy, "reference_value_path", "execution_policy")
    reference_price = _resolve_fact_reference(request, reference_path)
    order_type = _require_text(execution_policy, "order_type", "execution_policy").upper()
    if order_type == "MARKET":
        offset_pct = Decimal("0")
        ttl_seconds: int | None = None
    elif order_type == "LIMIT_OFFSET":
        offset_pct = _decimal(
            execution_policy.get("entry_offset_pct"),
            "execution_policy.entry_offset_pct",
            positive=True,
        )
        ttl_seconds = _integer(
            execution_policy.get("entry_limit_ttl_seconds"),
            "execution_policy.entry_limit_ttl_seconds",
            positive=True,
        )
    else:
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.POLICY_UNSUPPORTED,
            f"unsupported execution_policy.order_type={order_type}",
        )

    initial = _mapping(
        protection_policy.get("initial_protection"), "protection_policy.initial_protection"
    )
    stop_loss = _decimal(
        initial.get("stop_loss_pct"), "initial_protection.stop_loss_pct", positive=True
    )
    take_profit = _decimal(
        initial.get("take_profit_pct"), "initial_protection.take_profit_pct", positive=True
    )
    trigger_by = _require_text(initial, "trigger_by", "initial_protection")
    tpsl_mode = _require_text(initial, "tpsl_mode", "initial_protection")
    if trigger_by != "LastPrice" or tpsl_mode != "Full":
        raise ExecutionBridgeBlocked(
            ExecutionBridgeBlockCode.POLICY_UNSUPPORTED,
            "current Execution supports initial protection only as Full/LastPrice",
        )

    signal_fact = _mapping(request_payload.get("signal_fact"), "ExecutionRequest.signal_fact")
    signal_attributes = _mapping(
        signal_fact.get("attributes"), "ExecutionRequest.signal_fact.attributes"
    )
    side = "Buy" if request.direction is TradeDirection.LONG else "Sell"
    command_id = "ue-" + fingerprint({"execution_request_id": request.execution_request_id})[:32]
    exit_fp = str(exit_plan["exit_plan_fingerprint"])
    protection = {
        "stop_loss_pct": str(stop_loss),
        "take_profit_pct": str(take_profit),
        "trigger_by": trigger_by,
        "tpsl_mode": tpsl_mode,
        "strategy_id": request.strategy_id,
        "strategy_version": request.strategy_version,
        "strategy_config_fingerprint": request.strategy_config_fingerprint,
        "exit_plan_fingerprint": exit_fp,
    }
    payload: dict[str, object] = {
        "source": "universal_entry",
        "execution_request_id": request.execution_request_id,
        "strategy_attempt_id": request.strategy_attempt_id,
        "entry_decision_id": request.entry_decision_id,
        "signal_id": request.signal_id,
        "strategy_id": request.strategy_id,
        "strategy_version": request.strategy_version,
        "strategy_config_fingerprint": request.strategy_config_fingerprint,
        "entry_plan_fingerprint": request.entry_plan_fingerprint,
        "exit_plan_fingerprint": exit_fp,
        "strategy_activation_id": activation_id,
        "calculated_entry_price": signal_attributes.get("calculated_entry_price"),
        "entry_reference_source": signal_attributes.get("entry_reference_source"),
        "strategy_entry_offset_pct_signed": signal_attributes.get("entry_offset_pct_signed"),
        "strategy_context_features": signal_attributes.get("strategy_context_features"),
        "stake_usdt": str(requested_amount),
        "leverage": leverage,
        "side": side,
        "price": str(reference_price),
        "entry_offset_pct": str(offset_pct),
        "entry_limit_ttl_seconds": ttl_seconds,
        "entry_policy": "universal_entry",
        "policy_version": request.strategy_version,
        "bot_instance_id": "universal-entry",
        "initial_protection": protection,
    }
    snapshot_payload_keys = {
        "entry_reference_policy": "strategy_entry_reference_policy",
        "local_entry_policy": "strategy_local_entry_policy",
        "context_feature_policy": "strategy_context_feature_policy",
        "context_ranking_policy": "strategy_context_ranking_policy",
    }
    for request_key, command_key in snapshot_payload_keys.items():
        if request_key in request_payload:
            payload[command_key] = dict(
                _mapping(request_payload.get(request_key), f"ExecutionRequest.{request_key}")
            )
    return PreparedRuntimeEntryCommand(
        command_id=command_id,
        execution_request_id=request.execution_request_id,
        strategy_attempt_id=request.strategy_attempt_id,
        entry_decision_id=request.entry_decision_id,
        signal_id=request.signal_id,
        strategy_id=request.strategy_id,
        strategy_version=request.strategy_version,
        strategy_config_fingerprint=request.strategy_config_fingerprint,
        entry_plan_fingerprint=request.entry_plan_fingerprint,
        exit_plan_fingerprint=exit_fp,
        strategy_activation_id=activation_id,
        symbol=request.symbol,
        direction=request.direction,
        requested_at=request.requested_at.astimezone(UTC),
        payload=payload,
    )
