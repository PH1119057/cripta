from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .models import (
    FeatureEvidence,
    PositionEvent,
    PositionIdentity,
    PositionSnapshot,
    Quality,
    SupervisorState,
)

MANDATORY = ("structure", "price_1m", "flow", "absorption", "orderbook", "oi_price")


def _directional_move(identity: PositionIdentity, price: Decimal) -> Decimal:
    raw = (price / identity.actual_avg_fill - Decimal("1")) * Decimal("100")
    return raw if identity.side == "Buy" else -raw


class PositionSupervisor:
    """Pure causal state machine. It exposes no order or execution interface."""

    def __init__(self, identity: PositionIdentity) -> None:
        self.identity = identity
        self._mfe = Decimal("0")
        self._mae = Decimal("0")
        self._state: SupervisorState | None = None
        self._state_since = identity.fill_time
        self._last_at = identity.fill_time

    def update(self, event: PositionEvent) -> PositionSnapshot:
        if event.observed_at < self._last_at:
            raise ValueError("out-of-order event")
        if event.mark_price <= 0:
            raise ValueError("mark price must be positive")
        self._last_at = event.observed_at
        move = _directional_move(self.identity, event.mark_price)
        self._mfe = max(self._mfe, move)
        self._mae = min(self._mae, move)
        state, reason, action, confidence = self._classify(event.features)
        previous = self._state
        if state != previous:
            self._state_since = event.observed_at
        self._state = state
        return PositionSnapshot(
            identity=self.identity,
            observed_at=event.observed_at,
            mark_price=event.mark_price,
            price_move_pct=move,
            mfe_pct=self._mfe,
            mae_pct=self._mae,
            giveback_pct=max(Decimal("0"), self._mfe - move),
            recovery_from_mae_pct=max(Decimal("0"), move - self._mae),
            state=state,
            previous_state=previous,
            state_since=self._state_since,
            reason=reason,
            shadow_action=action,
            confidence=confidence,
            features=dict(event.features),
        )

    def restore_path(
        self,
        *,
        mfe_pct: Decimal,
        mae_pct: Decimal,
        state: SupervisorState,
        state_since: datetime,
        last_at: datetime,
    ) -> None:
        """Restore causal values persisted earlier for this exact position."""
        if last_at < self.identity.fill_time or state_since < self.identity.fill_time:
            raise ValueError("restored timestamps predate the position")
        if mfe_pct < 0 or mae_pct > 0:
            raise ValueError("invalid restored path extrema")
        self._mfe = mfe_pct
        self._mae = mae_pct
        self._state = state
        self._state_since = state_since
        self._last_at = last_at

    def _classify(
        self, features: dict[str, FeatureEvidence] | object
    ) -> tuple[SupervisorState, str, str, Decimal]:
        evidence: dict[str, FeatureEvidence] = dict(features)  # type: ignore[arg-type]
        absent = [name for name in MANDATORY if name not in evidence]
        if absent:
            return (
                SupervisorState.WARMUP,
                "не прогреты: " + ", ".join(absent),
                "ЖДАТЬ",
                Decimal("0"),
            )
        invalid = [
            name for name in MANDATORY if evidence[name].quality in {Quality.STALE, Quality.MISSING}
        ]
        if invalid:
            return (
                SupervisorState.BLOCKED,
                "устарели или отсутствуют: " + ", ".join(invalid),
                "НЕТ РЕКОМЕНДАЦИИ",
                Decimal("0"),
            )
        states = {name: item.state for name, item in evidence.items()}
        adverse = sum(
            states.get(name)
            in {"against", "broken", "persistent_adverse", "withdrawal", "failed_reclaim"}
            for name in MANDATORY
        )
        recovery = sum(
            states.get(name)
            in {"reclaim", "recovery", "absorption", "replenishment", "favorable_recovery"}
            for name in MANDATORY
        )
        favorable = sum(
            states.get(name) in {"with", "continuation", "favorable", "replenishment"}
            for name in MANDATORY
        )
        confidence = Decimal(
            sum(item.quality == Quality.FRESH for item in evidence.values())
        ) / Decimal(len(MANDATORY))
        if (
            states.get("structure") == "broken"
            and states.get("price_1m") == "failed_reclaim"
            and adverse >= 4
        ):
            return (
                SupervisorState.BROKEN,
                "структура сломана, возврат не удался и подтверждено устойчивое давление",
                "КАНДИДАТ НА ВЫХОД",
                confidence,
            )
        if recovery >= 3 and states.get("structure") in {"hold", "reclaim"}:
            return (
                SupervisorState.RECOVERY,
                "структура удержана и восстановление подтверждено независимыми признаками",
                "УДЕРЖИВАТЬ / ВОССТАНОВЛЕНИЕ",
                confidence,
            )
        if self._mfe >= Decimal("1.10") and favorable >= 3 and states.get("structure") == "with":
            return (
                SupervisorState.RUNNER,
                "движение доказано структурой, ценой и текущим потоком",
                "ДАТЬ ПРОСТРАНСТВО",
                confidence,
            )
        if self._mfe >= Decimal("0.50") and favorable >= 2:
            return (
                SupervisorState.PROVEN,
                "позиция показала движение и сохраняет подтверждение",
                "УДЕРЖИВАТЬ",
                confidence,
            )
        if adverse >= 2:
            return (
                SupervisorState.WARNING,
                "есть ухудшение, но разрушение не подтверждено полным стеком",
                "НАБЛЮДАТЬ",
                confidence,
            )
        return (
            SupervisorState.HEALTHY,
            "структура позиции сохраняется, подтверждённого неблагоприятного стека нет",
            "УДЕРЖИВАТЬ",
            confidence,
        )
