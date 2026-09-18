from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import ExitPlan
from bybit_workbench.universal_entry.fingerprint import fingerprint

from .contracts import ExitActionKind, ExitDecision, ExitExecutionRequest


class ExitExecutionBridgeBlockCode(StrEnum):
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    POLICY_INCOMPLETE = "POLICY_INCOMPLETE"
    POLICY_UNSUPPORTED = "POLICY_UNSUPPORTED"
    REQUEST_EXPIRED = "REQUEST_EXPIRED"
    POSITION_NOT_OPEN = "POSITION_NOT_OPEN"


class ExitExecutionBridgeBlocked(ValueError):
    def __init__(self, code: ExitExecutionBridgeBlockCode, reason: str) -> None:
        super().__init__(f"{code.value}:{reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PreparedRuntimeExitCommand:
    command_id: str
    execution_request: ExitExecutionRequest
    payload: dict[str, object]


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_INCOMPLETE,
            f"{label} must be an object",
        )
    return value


def _require_text(raw: Mapping[str, object], key: str, label: str) -> str:
    value = str(raw.get(key) or "").strip()
    if not value:
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_INCOMPLETE,
            f"{label}.{key} is required",
        )
    return value


def _positive_decimal(raw: Mapping[str, object], key: str, label: str) -> Decimal:
    try:
        value = Decimal(str(raw.get(key)))
    except (InvalidOperation, TypeError, ValueError):
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_INCOMPLETE,
            f"{label}.{key} must be decimal",
        ) from None
    if not value.is_finite() or value <= 0:
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_INCOMPLETE,
            f"{label}.{key} must be positive",
        )
    return value


def _strict_keys(
    mutation: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> None:
    actual = frozenset(str(key) for key in mutation)
    missing = required - actual
    unknown = actual - required - optional
    if missing:
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_INCOMPLETE,
            f"{label} missing fields: {','.join(sorted(missing))}",
        )
    if unknown:
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_UNSUPPORTED,
            f"{label} unsupported fields: {','.join(sorted(unknown))}",
        )


def _require_market(value: str, label: str) -> None:
    if value.upper() != "MARKET":
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_UNSUPPORTED,
            f"{label} currently supports MARKET only",
        )


def _require_full(value: str, label: str) -> None:
    if value != "Full":
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_UNSUPPORTED,
            f"{label} currently supports Full only",
        )


def _validate_trigger_by(value: str, label: str) -> None:
    if value not in {"LastPrice", "MarkPrice", "IndexPrice"}:
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_UNSUPPORTED,
            f"{label} unsupported trigger source {value}",
        )


def validate_exit_mutation(
    action_kind: ExitActionKind,
    mutation: Mapping[str, object],
) -> dict[str, object]:
    """Validate without adding trading policy or silently ignoring fields."""

    result: dict[str, object] = dict(mutation)
    label = f"{action_kind.value}.mutation"

    if action_kind is ExitActionKind.SET_STOP:
        _strict_keys(
            mutation,
            required=frozenset({"stop_price", "trigger_by", "tpsl_mode", "order_type"}),
            label=label,
        )
        result["stop_price"] = str(_positive_decimal(mutation, "stop_price", label))
        trigger = _require_text(mutation, "trigger_by", label)
        mode = _require_text(mutation, "tpsl_mode", label)
        order_type = _require_text(mutation, "order_type", label)
        _validate_trigger_by(trigger, label)
        _require_full(mode, label)
        _require_market(order_type, label)

    elif action_kind is ExitActionKind.SET_TP:
        _strict_keys(
            mutation,
            required=frozenset({"take_profit_price", "trigger_by", "tpsl_mode", "order_type"}),
            label=label,
        )
        result["take_profit_price"] = str(_positive_decimal(mutation, "take_profit_price", label))
        trigger = _require_text(mutation, "trigger_by", label)
        mode = _require_text(mutation, "tpsl_mode", label)
        order_type = _require_text(mutation, "order_type", label)
        _validate_trigger_by(trigger, label)
        _require_full(mode, label)
        _require_market(order_type, label)

    elif action_kind is ExitActionKind.SET_PROTECTION:
        _strict_keys(
            mutation,
            required=frozenset(
                {
                    "stop_price",
                    "take_profit_price",
                    "sl_trigger_by",
                    "tp_trigger_by",
                    "tpsl_mode",
                    "sl_order_type",
                    "tp_order_type",
                }
            ),
            label=label,
        )
        result["stop_price"] = str(_positive_decimal(mutation, "stop_price", label))
        result["take_profit_price"] = str(_positive_decimal(mutation, "take_profit_price", label))
        sl_trigger = _require_text(mutation, "sl_trigger_by", label)
        tp_trigger = _require_text(mutation, "tp_trigger_by", label)
        mode = _require_text(mutation, "tpsl_mode", label)
        sl_order = _require_text(mutation, "sl_order_type", label)
        tp_order = _require_text(mutation, "tp_order_type", label)
        _validate_trigger_by(sl_trigger, label)
        _validate_trigger_by(tp_trigger, label)
        _require_full(mode, label)
        _require_market(sl_order, label)
        _require_market(tp_order, label)

    elif action_kind is ExitActionKind.SET_TRAILING:
        _strict_keys(
            mutation,
            required=frozenset({"distance", "tpsl_mode"}),
            optional=frozenset({"active_price"}),
            label=label,
        )
        result["distance"] = str(_positive_decimal(mutation, "distance", label))
        mode = _require_text(mutation, "tpsl_mode", label)
        _require_full(mode, label)
        if "active_price" in mutation:
            result["active_price"] = str(_positive_decimal(mutation, "active_price", label))

    elif action_kind is ExitActionKind.REDUCE:
        _strict_keys(
            mutation,
            required=frozenset({"quantity", "order_type"}),
            label=label,
        )
        result["quantity"] = str(_positive_decimal(mutation, "quantity", label))
        _require_market(_require_text(mutation, "order_type", label), label)

    elif action_kind is ExitActionKind.CLOSE:
        _strict_keys(
            mutation,
            required=frozenset({"quantity", "order_type"}),
            label=label,
        )
        if _require_text(mutation, "quantity", label).upper() != "ALL":
            raise ExitExecutionBridgeBlocked(
                ExitExecutionBridgeBlockCode.POLICY_UNSUPPORTED,
                "CLOSE currently supports quantity=ALL only; use REDUCE for partial close",
            )
        _require_market(_require_text(mutation, "order_type", label), label)
        result["quantity"] = "ALL"

    else:
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_UNSUPPORTED,
            f"unsupported action {action_kind}",
        )
    return result


def _exact_identity(
    decision: ExitDecision,
    position: StrategyPosition,
    plan: ExitPlan,
) -> None:
    position_identity = (
        position.strategy_id,
        position.strategy_version,
        position.strategy_config_fingerprint,
        position.exit_plan_fingerprint,
    )
    decision_identity = (
        decision.strategy_id,
        decision.strategy_version,
        decision.strategy_config_fingerprint,
        decision.exit_plan_fingerprint,
    )
    plan_identity = (
        plan.strategy_id,
        plan.strategy_version,
        plan.strategy_config_fingerprint,
        plan.exit_plan_fingerprint,
    )
    if decision.strategy_position_id != position.strategy_position_id:
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.IDENTITY_MISMATCH,
            "ExitDecision StrategyPosition differs from exact position",
        )
    if decision_identity != position_identity or plan_identity != position_identity:
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.IDENTITY_MISMATCH,
            "ExitDecision/ExitPlan/StrategyPosition lineage mismatch",
        )


def _max_request_age_seconds(plan: ExitPlan) -> int:
    policy = plan.exit_policy.to_dict()
    execution_policy = _mapping(
        policy.get("execution_policy"),
        "ExitPlan.exit_policy.execution_policy",
    )
    raw = execution_policy.get("max_request_age_seconds")
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_INCOMPLETE,
            "ExitPlan execution_policy.max_request_age_seconds must be integer",
        ) from None
    if value <= 0:
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.POLICY_INCOMPLETE,
            "ExitPlan execution_policy.max_request_age_seconds must be positive",
        )
    return value


def prepare_exit_execution_request(
    decision: ExitDecision,
    position: StrategyPosition,
    plan: ExitPlan,
    *,
    now: datetime,
) -> ExitExecutionRequest:
    _exact_identity(decision, position, plan)
    current = now.astimezone(UTC)
    decided_at = decision.decided_at.astimezone(UTC)
    if current < decided_at:
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.IDENTITY_MISMATCH,
            "ExitDecision timestamp is in the future",
        )
    max_age = _max_request_age_seconds(plan)
    expires_at = decided_at + timedelta(seconds=max_age)
    request_id = (
        "exit-request-"
        + fingerprint(
            {
                "exit_decision_id": decision.exit_decision_id,
                "strategy_position_id": position.strategy_position_id,
                "exit_plan_fingerprint": plan.exit_plan_fingerprint,
            }
        )[:32]
    )
    return ExitExecutionRequest.from_decision(
        execution_request_id=request_id,
        decision=decision,
        position=position,
        requested_at=decided_at,
        expires_at=expires_at,
    )


def prepare_runtime_exit_command(
    request: ExitExecutionRequest,
    position: StrategyPosition,
    *,
    now: datetime,
) -> PreparedRuntimeExitCommand:
    current = now.astimezone(UTC)
    if current >= request.expires_at.astimezone(UTC):
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.REQUEST_EXPIRED,
            "ExitExecutionRequest expired before dispatch",
        )
    expected = (
        position.strategy_position_id,
        position.strategy_id,
        position.strategy_version,
        position.strategy_config_fingerprint,
        position.exit_plan_fingerprint,
        position.account_ref,
        position.exchange_position_key,
        position.position_idx,
        position.symbol,
        position.direction,
    )
    actual = (
        request.strategy_position_id,
        request.strategy_id,
        request.strategy_version,
        request.strategy_config_fingerprint,
        request.exit_plan_fingerprint,
        request.account_ref,
        request.exchange_position_key,
        request.position_idx,
        request.symbol,
        request.direction,
    )
    if actual != expected:
        raise ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.IDENTITY_MISMATCH,
            "ExitExecutionRequest differs from exact StrategyPosition",
        )

    mutation = validate_exit_mutation(
        request.action_kind,
        request.requested_mutation.to_dict(),
    )
    command_id = (
        "ux-" + fingerprint({"exit_execution_request_id": request.execution_request_id})[:32]
    )
    payload: dict[str, object] = {
        "source": "universal_exit",
        "exit_execution_request_id": request.execution_request_id,
        "exit_decision_id": request.exit_decision_id,
        "strategy_position_id": request.strategy_position_id,
        "strategy_id": request.strategy_id,
        "strategy_version": request.strategy_version,
        "strategy_config_fingerprint": request.strategy_config_fingerprint,
        "exit_plan_fingerprint": request.exit_plan_fingerprint,
        "account_ref": request.account_ref,
        "exchange_position_key": request.exchange_position_key,
        "position_idx": request.position_idx,
        "symbol": request.symbol,
        "direction": request.direction.value,
        "action_kind": request.action_kind.value,
        "requested_mutation": mutation,
        "expires_at": request.expires_at.astimezone(UTC).isoformat(),
    }
    return PreparedRuntimeExitCommand(
        command_id=command_id,
        execution_request=request,
        payload=payload,
    )
