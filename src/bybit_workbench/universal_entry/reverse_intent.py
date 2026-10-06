from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .contracts import EntryExecutionIntent, FrozenPolicy, TradeDirection
from .fingerprint import fingerprint


class ReverseIntentContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReverseTransitionIntent:
    reverse_intent_id: str
    strategy_attempt_id: str
    signal_id: str
    strategy_activation_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    entry_plan_fingerprint: str
    exit_plan_fingerprint: str
    symbol: str
    from_direction: TradeDirection
    to_direction: TradeDirection
    reason: str
    close_execution: str
    open_execution: str
    position_mode: str
    position_idx: int
    execution_override: str
    entry_request_payload: FrozenPolicy
    capital_policy: FrozenPolicy
    lifecycle_policy: FrozenPolicy

    def transition_payload(self) -> FrozenPolicy:
        return FrozenPolicy.from_mapping(
            {
                "source": "universal_entry",
                "reverse_intent_id": self.reverse_intent_id,
                "reason": self.reason,
                "from_direction": self.from_direction.value,
                "to_direction": self.to_direction.value,
                "close_execution": self.close_execution,
                "open_execution": self.open_execution,
                "position_mode": self.position_mode,
                "position_idx": self.position_idx,
                "execution_override": self.execution_override,
                "entry_request_payload": self.entry_request_payload.to_dict(),
                "capital_policy": self.capital_policy.to_dict(),
                "lifecycle_policy": self.lifecycle_policy.to_dict(),
            }
        )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReverseIntentContractError(f"{label} must be an object")
    return value


def build_reverse_transition_intent(
    entry_intent: EntryExecutionIntent,
    *,
    strategy_activation_id: str,
    from_direction: TradeDirection,
    lifecycle_policy: Mapping[str, object],
    capital_policy: Mapping[str, object],
) -> ReverseTransitionIntent | None:
    reverse_raw = lifecycle_policy.get("reverse_on_opposite_signal")
    if not isinstance(reverse_raw, Mapping) or not bool(reverse_raw.get("enabled", False)):
        return None
    if from_direction is entry_intent.direction:
        return None

    reverse = _mapping(reverse_raw, "reverse_on_opposite_signal")
    if not bool(reverse.get("same_strategy_only", False)):
        raise ReverseIntentContractError(
            "current reverse contract requires same_strategy_only=true"
        )
    close_execution = str(reverse.get("close_execution") or "").upper()
    open_execution = str(reverse.get("open_execution") or "").upper()
    position_mode = str(reverse.get("position_mode") or "")
    try:
        position_idx = int(str(reverse.get("position_idx")))
    except (TypeError, ValueError) as exc:
        raise ReverseIntentContractError("reverse position_idx must be integer") from exc
    if (
        close_execution != "MARKET"
        or open_execution != "MARKET"
        or position_mode != "ONE_WAY"
        or position_idx != 0
    ):
        raise ReverseIntentContractError(
            "current reverse contract requires MARKET/MARKET ONE_WAY position_idx=0"
        )

    request_payload = entry_intent.payload.to_dict()
    request_payload["execution_override"] = "OPPOSITE_FLIP_TAKER"
    reverse_intent_id = "reverse-intent-" + fingerprint(
        {
            "strategy_attempt_id": entry_intent.strategy_attempt_id,
            "signal_id": entry_intent.signal_id,
            "strategy_activation_id": strategy_activation_id,
            "strategy_id": entry_intent.strategy_id,
            "strategy_version": entry_intent.strategy_version,
            "strategy_config_fingerprint": entry_intent.strategy_config_fingerprint,
            "entry_plan_fingerprint": entry_intent.entry_plan_fingerprint,
            "exit_plan_fingerprint": entry_intent.exit_plan_fingerprint,
            "symbol": entry_intent.symbol,
            "from_direction": from_direction.value,
            "to_direction": entry_intent.direction.value,
        }
    )[:32]
    return ReverseTransitionIntent(
        reverse_intent_id=reverse_intent_id,
        strategy_attempt_id=entry_intent.strategy_attempt_id,
        signal_id=entry_intent.signal_id,
        strategy_activation_id=strategy_activation_id,
        strategy_id=entry_intent.strategy_id,
        strategy_version=entry_intent.strategy_version,
        strategy_config_fingerprint=entry_intent.strategy_config_fingerprint,
        entry_plan_fingerprint=entry_intent.entry_plan_fingerprint,
        exit_plan_fingerprint=entry_intent.exit_plan_fingerprint,
        symbol=entry_intent.symbol,
        from_direction=from_direction,
        to_direction=entry_intent.direction,
        reason="OPPOSITE_ENTRY_FORCED_FLIP",
        close_execution=close_execution,
        open_execution=open_execution,
        position_mode=position_mode,
        position_idx=position_idx,
        execution_override="OPPOSITE_FLIP_TAKER",
        entry_request_payload=FrozenPolicy.from_mapping(request_payload),
        capital_policy=FrozenPolicy.from_mapping(dict(capital_policy)),
        lifecycle_policy=FrozenPolicy.from_mapping(dict(lifecycle_policy)),
    )
