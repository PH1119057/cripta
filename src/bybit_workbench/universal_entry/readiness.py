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
    paper_ready: bool
    monitor_ready: bool
    execution_ready: bool
    active_ready: bool
    reasons: tuple[RuntimeReadinessReason, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_ready": self.policy_ready,
            "observer_ready": self.observer_ready,
            "paper_ready": self.paper_ready,
            "monitor_ready": self.monitor_ready,
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
    """Prove monitoring and live-execution readiness independently.

    ACTIVE means real-market monitoring + paper lifecycle only.  It must never be
    coupled to permission for real exchange mutation.  execution_ready is a
    separate proof consumed by Strategy ExecutionPermission.
    """

    policy_reasons: list[RuntimeReadinessReason] = []
    paper_reasons: list[RuntimeReadinessReason] = []
    execution_reasons: list[RuntimeReadinessReason] = []
    observer_reasons: list[RuntimeReadinessReason] = []

    entry = card.entry_policy.to_dict()
    watch = _mapping(entry.get("watch_policy"))
    if not bool(watch.get("enabled", False)):
        policy_reasons.append(
            RuntimeReadinessReason(
                "ENTRY_WATCH_DISABLED",
                "ENTRY",
                "Entry Watch выключен; ACTIVE Strategy должна явно включить Entry.",
            )
        )

    # Entry reference/local/context policies are materialized and consumed by the
    # Universal Entry engine.  Authoring validation owns their field completeness.
    typed_contexts = card.mayak_context_policy + card.dispatcher_context_policy
    for item in typed_contexts:
        if item.mode in {ContextMode.CONDITION, ContextMode.RANKING} and (
            item.max_age_seconds is None or item.min_quality is None
        ):
            policy_reasons.append(
                RuntimeReadinessReason(
                    "TYPED_CONTEXT_POLICY_INCOMPLETE",
                    "ENTRY",
                    "Decision-affecting typed context требует freshness и quality.",
                )
            )
            break

    exit_policy = card.exit_policy.to_dict()
    # Paper lifecycle supports deterministic price/time/context rules.  Local-zone
    # actions remain blocked until the Strategy explicitly defines an executable
    # zone operator rather than three ambiguous booleans.
    if _enabled(exit_policy.get("local_zone_exit")):
        zone = _mapping(exit_policy.get("local_zone_exit"))
        if not str(zone.get("operator") or ""):
            paper_reasons.append(
                RuntimeReadinessReason(
                    "EXIT_LOCAL_ZONE_OPERATOR_NOT_SET",
                    "PAPER_EXIT",
                    "Локальный Exit включён, но Strategy не задаёт точный оператор зоны.",
                )
            )

    capital = card.capital_policy.to_dict()
    require_capacity = bool(capital.get("require_capacity", False))
    if str(capital.get("amount_currency") or "").upper() != "USDT":
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_CAPITAL_CURRENCY_UNSUPPORTED",
                "PAPER",
                "Текущий paper/runtime contract использует USDT amount.",
            )
        )
    if not _positive_decimal(capital.get("requested_amount")):
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_AMOUNT_NOT_SET",
                "PAPER",
                "requested_amount должен быть положительным.",
            )
        )
    if not _positive_int(capital.get("leverage")):
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_LEVERAGE_NOT_SET",
                "PAPER",
                "leverage должен быть положительным целым.",
            )
        )

    execution = _mapping(entry.get("execution_policy"))
    order_type = str(execution.get("order_type") or "").upper()
    if order_type not in {"MARKET", "LIMIT_OFFSET"}:
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_ORDER_TYPE_NOT_SET",
                "PAPER",
                "Нужно явно выбрать MARKET или LIMIT_OFFSET.",
            )
        )
    if not _positive_int(execution.get("max_request_age_seconds")):
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_REQUEST_MAX_AGE_NOT_SET",
                "PAPER",
                "max_request_age_seconds должен быть положительным.",
            )
        )
    reference_path = str(execution.get("reference_value_path") or "")
    if not reference_path.startswith("fact."):
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_REFERENCE_PATH_UNSUPPORTED",
                "PAPER",
                "Execution reference_value_path должен быть причинным fact.* path.",
            )
        )
    if order_type == "LIMIT_OFFSET":
        if not _positive_decimal(execution.get("entry_offset_pct")):
            paper_reasons.append(
                RuntimeReadinessReason(
                    "PAPER_LIMIT_OFFSET_NOT_SET",
                    "PAPER",
                    "LIMIT_OFFSET требует положительный execution entry_offset_pct.",
                )
            )
        if not _positive_int(execution.get("entry_limit_ttl_seconds")):
            paper_reasons.append(
                RuntimeReadinessReason(
                    "PAPER_LIMIT_TTL_NOT_SET",
                    "PAPER",
                    "LIMIT_OFFSET требует положительный TTL псевдозаявки.",
                )
            )

    protection = card.protection_policy.to_dict()
    initial = _mapping(protection.get("initial_protection"))
    if not _positive_decimal(initial.get("stop_loss_pct")):
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_INITIAL_STOP_NOT_SET",
                "PAPER",
                "Псевдосделка требует положительный initial stop_loss_pct.",
            )
        )
    if not _positive_decimal(initial.get("take_profit_pct")):
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_INITIAL_TAKE_PROFIT_NOT_SET",
                "PAPER",
                "Псевдосделка требует положительный initial take_profit_pct.",
            )
        )

    # Live Execution requires a fresh account-capacity contract even though PAPER
    # monitoring is allowed to simulate a fixed Strategy amount without tying the
    # experiment to current wallet availability.
    execution_reasons.extend(paper_reasons)
    if not require_capacity:
        execution_reasons.append(
            RuntimeReadinessReason(
                "LIVE_CAPACITY_POLICY_REQUIRED",
                "EXECUTION",
                (
                    "Execution ON требует явную проверку доступного капитала; "
                    "PAPER может работать без неё."
                ),
            )
        )
    elif not _positive_int(capital.get("capacity_max_age_seconds")) or not str(
        capital.get("capacity_min_quality") or ""
    ):
        execution_reasons.append(
            RuntimeReadinessReason(
                "LIVE_CAPACITY_POLICY_INCOMPLETE",
                "EXECUTION",
                "Execution ON требует freshness и quality TradingCapacitySnapshot.",
            )
        )
    if str(initial.get("trigger_by") or "") != "LastPrice":
        execution_reasons.append(
            RuntimeReadinessReason(
                "INITIAL_TRIGGER_BY_UNSUPPORTED",
                "EXECUTION",
                "Текущий live Execution поддерживает initial protection только по LastPrice.",
            )
        )
    if str(initial.get("tpsl_mode") or "") != "Full":
        execution_reasons.append(
            RuntimeReadinessReason(
                "INITIAL_TPSL_MODE_UNSUPPORTED",
                "EXECUTION",
                "Текущий live Execution поддерживает initial protection только в Full mode.",
            )
        )
    for field, code, label in (
        ("break_even", "LIVE_EXIT_BREAK_EVEN_NOT_WIRED", "безубыточность"),
        ("trailing", "LIVE_EXIT_TRAILING_NOT_WIRED", "trailing"),
        ("local_zone_exit", "LIVE_EXIT_LOCAL_ZONE_NOT_WIRED", "выход по локальной зоне"),
        ("time_exit", "LIVE_EXIT_TIME_NOT_WIRED", "выход по времени"),
    ):
        if _enabled(exit_policy.get(field)):
            execution_reasons.append(
                RuntimeReadinessReason(
                    code,
                    "EXECUTION",
                    f"Strategy-specific {label} пока не исполняется live Exit runtime.",
                )
            )
    if _non_off_features(exit_policy.get("context_feature_policy")):
        execution_reasons.append(
            RuntimeReadinessReason(
                "LIVE_EXIT_CONTEXT_NOT_WIRED",
                "EXECUTION",
                "Strategy-specific context Exit пока не подключён к live Exit runtime.",
            )
        )
    lifecycle = card.lifecycle_policy.to_dict()
    if _enabled(lifecycle.get("hedge_policy")):
        execution_reasons.append(
            RuntimeReadinessReason(
                "LIVE_HEDGE_EXECUTION_NOT_WIRED",
                "EXECUTION",
                "Paper hedge поддерживается отдельно; live Execution hedge ещё не разрешён.",
            )
        )

    if not observer_ready:
        observer_reasons.append(
            RuntimeReadinessReason(
                "MULTI_STRATEGY_OBSERVER_NOT_READY",
                "OBSERVER",
                "Multi-Strategy observer не подтверждён как доступный.",
            )
        )

    policy_ready = not policy_reasons
    paper_ready = not paper_reasons
    monitor_ready = policy_ready and paper_ready and observer_ready
    execution_ready = policy_ready and paper_ready and not execution_reasons
    reasons = tuple(policy_reasons + paper_reasons + observer_reasons + execution_reasons)
    return StrategyRuntimeReadiness(
        policy_ready=policy_ready,
        observer_ready=observer_ready,
        paper_ready=paper_ready,
        monitor_ready=monitor_ready,
        execution_ready=execution_ready,
        active_ready=monitor_ready,
        reasons=reasons,
    )
