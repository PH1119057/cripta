from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from .contracts import EntryPlan, ExitPlan, StrategyActivation, StrategyCard
from .materializer import materialize_plans


class ActivePlanRegistry:
    def __init__(self) -> None:
        self._cards: dict[tuple[str, str, str], StrategyCard] = {}
        self._activations: dict[str, StrategyActivation] = {}
        self._entry_plans: dict[str, EntryPlan] = {}
        self._exit_plans: dict[str, ExitPlan] = {}

    def register_card(self, card: StrategyCard) -> None:
        key = (card.strategy_id, card.strategy_version, card.strategy_config_fingerprint)
        existing = self._cards.get(key)
        if existing is not None and existing != card:
            raise ValueError("immutable StrategyCard identity collision")
        self._cards[key] = card

    def activate(self, activation: StrategyActivation) -> EntryPlan:
        key = (
            activation.strategy_id,
            activation.strategy_version,
            activation.strategy_config_fingerprint,
        )
        card = self._cards.get(key)
        if card is None:
            raise KeyError("activation references unknown StrategyCard")
        self._activations[activation.activation_id] = activation
        entry, exit_plan = materialize_plans(card, activation)
        if not activation.enabled:
            self._entry_plans.pop(activation.activation_id, None)
            self._exit_plans.pop(activation.activation_id, None)
            return entry
        self._entry_plans[activation.activation_id] = entry
        self._exit_plans[activation.activation_id] = exit_plan
        return entry

    def set_enabled(
        self,
        activation_id: str,
        enabled: bool,
        *,
        changed_at: datetime | None = None,
    ) -> StrategyActivation:
        current = self._activations[activation_id]
        at = (changed_at or datetime.now(UTC)).astimezone(UTC)
        updated = replace(
            current,
            enabled=enabled,
            enabled_at=at if enabled else current.enabled_at,
            disabled_at=None if enabled else at,
        )
        self.activate(updated)
        return updated

    def active_entry_plans(self) -> tuple[EntryPlan, ...]:
        return tuple(
            sorted(self._entry_plans.values(), key=lambda item: item.entry_plan_fingerprint)
        )

    def plans_for(self, symbol: str) -> tuple[EntryPlan, ...]:
        selected = symbol.upper()
        return tuple(plan for plan in self.active_entry_plans() if selected in plan.symbols)

    def card(self, strategy_id: str, strategy_version: str, fingerprint_value: str) -> StrategyCard:
        return self._cards[(strategy_id, strategy_version, fingerprint_value)]
