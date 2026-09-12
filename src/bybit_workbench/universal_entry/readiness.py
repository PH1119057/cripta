from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .contracts import ContextMode, StrategyCard


@dataclass(frozen=True, slots=True)
class RuntimeReadinessReason:
    code: str
    contour: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "contour": self.contour, "message": self.message}


@dataclass(frozen=True, slots=True)
class StrategyRuntimeReadiness:
    policy_ready: bool
    observer_ready: bool
    execution_ready: bool
    active_ready: bool
    reasons: tuple[RuntimeReadinessReason, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_ready": self.policy_ready,
            "observer_ready": self.observer_ready,
            "execution_ready": self.execution_ready,
            "active_ready": self.active_ready,
            "reasons": [item.as_dict() for item in self.reasons],
        }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()


def _enabled(value: object) -> bool:
    return bool(_mapping(value).get("enabled", False))


def _positive_decimal(value: object) -> bool:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return number.is_finite() and number > 0


def _positive_int(value: object) -> bool:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return False
    return number > 0


def _non_off_features(value: object) -> tuple[Mapping[str, object], ...]:
    rows: list[Mapping[str, object]] = []
    for item in _sequence(value):
        raw = _mapping(item)
        if str(raw.get("mode") or "OFF") != ContextMode.OFF.value:
            rows.append(raw)
    return tuple(rows)


def assess_strategy_runtime_readiness(
    card: StrategyCard,
    *,
    observer_ready: bool,
) -> StrategyRuntimeReadiness:
    """Fail-closed proof that no enabled Strategy field is silently ignored.

    `observer_ready` is supplied by the installed runtime, never inferred from a card.
    A policy can be structurally valid while still being forbidden from Activation.
    """

    policy_reasons: list[RuntimeReadinessReason] = []
    execution_reasons: list[RuntimeReadinessReason] = []
    observer_reasons: list[RuntimeReadinessReason] = []

    entry = card.entry_policy.to_dict()
    watch = _mapping(entry.get("watch_policy"))
    if not bool(watch.get("enabled", False)):
        policy_reasons.append(
            RuntimeReadinessReason(
                "ENTRY_WATCH_DISABLED",
                "ENTRY",
                "Entry Watch выключен; активная торговая Strategy должна явно включить Entry.",
            )
        )

    reference = _mapping(entry.get("entry_reference_policy"))
    if bool(reference.get("enabled", False)):
        policy_reasons.append(
            RuntimeReadinessReason(
                "ENTRY_REFERENCE_OFFSET_NOT_CONSUMED",
                "ENTRY",
                (
                    "Signed offset относительно CALCULATED_ENTRY сохраняется, "
                    "но Universal Entry пока его не применяет."
                ),
            )
        )

    local = _mapping(entry.get("local_entry_policy"))
    if bool(local.get("enabled", False)):
        policy_reasons.append(
            RuntimeReadinessReason(
                "LOCAL_ENTRY_POLICY_NOT_IMPLEMENTED",
                "ENTRY",
                (
                    "Алгоритм локальной точки Entry не утверждён и не реализован; "
                    "activation запрещена."
                ),
            )
        )

    if _non_off_features(entry.get("context_feature_policy")):
        policy_reasons.append(
            RuntimeReadinessReason(
                "ENTRY_CONTEXT_FEATURE_POLICY_NOT_COMPILED",
                "ENTRY",
                (
                    "Выбранные MAYAK/Dispatcher feature rules пока не компилируются "
                    "в EntryPlan и не могут влиять на решение."
                ),
            )
        )

    typed_contexts = card.mayak_context_policy + card.dispatcher_context_policy
    if any(item.mode in {ContextMode.CONDITION, ContextMode.RANKING} for item in typed_contexts):
        policy_reasons.append(
            RuntimeReadinessReason(
                "TYPED_CONTEXT_FAILURE_ACTIONS_NOT_ENFORCED",
                "ENTRY",
                (
                    "Core связывает context freshness, но ещё не исполняет разные "
                    "on_missing/on_stale/on_partial actions полностью."
                ),
            )
        )

    exit_policy = card.exit_policy.to_dict()
    for field, code, label in (
        ("break_even", "EXIT_BREAK_EVEN_NOT_WIRED", "безубыточность"),
        ("trailing", "EXIT_TRAILING_NOT_WIRED", "trailing"),
        ("local_zone_exit", "EXIT_LOCAL_ZONE_NOT_WIRED", "выход по локальной зоне"),
        ("time_exit", "EXIT_TIME_NOT_WIRED", "выход по времени"),
    ):
        if _enabled(exit_policy.get(field)):
            policy_reasons.append(
                RuntimeReadinessReason(
                    code,
                    "EXIT",
                    f"Strategy-specific {label} пока не исполняется Exit runtime.",
                )
            )
    if _non_off_features(exit_policy.get("context_feature_policy")):
        policy_reasons.append(
            RuntimeReadinessReason(
                "EXIT_CONTEXT_FEATURE_POLICY_NOT_WIRED",
                "EXIT",
                (
                    "Strategy-specific MAYAK/Dispatcher правила выхода пока не исполняются "
                    "Exit runtime."
                ),
            )
        )

    lifecycle = card.lifecycle_policy.to_dict()
    if _enabled(lifecycle.get("hedge_policy")):
        policy_reasons.append(
            RuntimeReadinessReason(
                "HEDGE_POLICY_NOT_IMPLEMENTED",
                "EXIT",
                (
                    "Hedge policy сохраняется в StrategyCard, но runtime открытия/сопровождения "
                    "hedge ещё не реализован."
                ),
            )
        )

    capital = card.capital_policy.to_dict()
    if not bool(capital.get("require_capacity", False)):
        execution_reasons.append(
            RuntimeReadinessReason(
                "EXECUTION_CAPITAL_NOT_CONFIGURED",
                "EXECUTION",
                "Для реального Execution нужна явная ставка и проверка доступного капитала.",
            )
        )
    if str(capital.get("amount_currency") or "").upper() != "USDT":
        execution_reasons.append(
            RuntimeReadinessReason(
                "EXECUTION_CAPITAL_CURRENCY_UNSUPPORTED",
                "EXECUTION",
                "Текущий Execution adapter принимает только USDT amount.",
            )
        )
    if not _positive_decimal(capital.get("requested_amount")):
        execution_reasons.append(
            RuntimeReadinessReason(
                "EXECUTION_AMOUNT_NOT_SET",
                "EXECUTION",
                "requested_amount должен быть положительным.",
            )
        )
    if not _positive_int(capital.get("leverage")):
        execution_reasons.append(
            RuntimeReadinessReason(
                "EXECUTION_LEVERAGE_NOT_SET",
                "EXECUTION",
                "leverage должен быть положительным целым.",
            )
        )

    execution = _mapping(entry.get("execution_policy"))
    order_type = str(execution.get("order_type") or "").upper()
    if order_type not in {"MARKET", "LIMIT_OFFSET"}:
        execution_reasons.append(
            RuntimeReadinessReason(
                "EXECUTION_ORDER_TYPE_NOT_SET",
                "EXECUTION",
                "Нужно явно выбрать MARKET или LIMIT_OFFSET.",
            )
        )
    if not _positive_int(execution.get("max_request_age_seconds")):
        execution_reasons.append(
            RuntimeReadinessReason(
                "EXECUTION_REQUEST_MAX_AGE_NOT_SET",
                "EXECUTION",
                "max_request_age_seconds должен быть положительным.",
            )
        )
    reference_path = str(execution.get("reference_value_path") or "")
    if not reference_path.startswith("fact."):
        execution_reasons.append(
            RuntimeReadinessReason(
                "EXECUTION_REFERENCE_PATH_UNSUPPORTED",
                "EXECUTION",
                "Execution reference_value_path должен быть причинным fact.* path.",
            )
        )
    if order_type == "LIMIT_OFFSET":
        if not _positive_decimal(execution.get("entry_offset_pct")):
            execution_reasons.append(
                RuntimeReadinessReason(
                    "EXECUTION_LIMIT_OFFSET_NOT_SET",
                    "EXECUTION",
                    "LIMIT_OFFSET требует отдельный положительный execution entry_offset_pct.",
                )
            )
        if not _positive_int(execution.get("entry_limit_ttl_seconds")):
            execution_reasons.append(
                RuntimeReadinessReason(
                    "EXECUTION_LIMIT_TTL_NOT_SET",
                    "EXECUTION",
                    "LIMIT_OFFSET требует положительный TTL лимитной заявки.",
                )
            )

    protection = card.protection_policy.to_dict()
    initial = _mapping(protection.get("initial_protection"))
    if not _positive_decimal(initial.get("stop_loss_pct")):
        execution_reasons.append(
            RuntimeReadinessReason(
                "INITIAL_STOP_NOT_SET",
                "EXECUTION",
                "Существующий Execution требует положительный initial stop_loss_pct.",
            )
        )
    if not _positive_decimal(initial.get("take_profit_pct")):
        execution_reasons.append(
            RuntimeReadinessReason(
                "INITIAL_TAKE_PROFIT_NOT_SET",
                "EXECUTION",
                "Существующий Execution требует положительный initial take_profit_pct.",
            )
        )
    if str(initial.get("trigger_by") or "") != "LastPrice":
        execution_reasons.append(
            RuntimeReadinessReason(
                "INITIAL_TRIGGER_BY_UNSUPPORTED",
                "EXECUTION",
                "Текущий Execution adapter поддерживает initial protection только по LastPrice.",
            )
        )
    if str(initial.get("tpsl_mode") or "") != "Full":
        execution_reasons.append(
            RuntimeReadinessReason(
                "INITIAL_TPSL_MODE_UNSUPPORTED",
                "EXECUTION",
                "Текущий Execution adapter поддерживает initial protection только в Full mode.",
            )
        )

    if not observer_ready:
        observer_reasons.append(
            RuntimeReadinessReason(
                "MULTI_STRATEGY_OBSERVER_NOT_INSTALLED",
                "OBSERVER",
                (
                    "Production observer, загружающий enabled StrategyActivation из PostgreSQL, "
                    "ещё не установлен."
                ),
            )
        )

    reasons = tuple(policy_reasons + execution_reasons + observer_reasons)
    policy_ready = not policy_reasons
    execution_ready = not execution_reasons
    return StrategyRuntimeReadiness(
        policy_ready=policy_ready,
        observer_ready=observer_ready,
        execution_ready=execution_ready,
        active_ready=policy_ready and observer_ready and execution_ready,
        reasons=reasons,
    )
