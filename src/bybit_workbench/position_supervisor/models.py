from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class Quality(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    PARTIAL = "partial"


class SupervisorState(StrEnum):
    WARMUP = "WARMUP"
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    RECOVERY = "RECOVERY"
    PROVEN = "PROVEN"
    RUNNER = "RUNNER"
    BROKEN = "BROKEN"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class FeatureEvidence:
    state: str
    observed_at: datetime
    quality: Quality
    measurements: Mapping[str, Decimal | str | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionIdentity:
    position_id: str
    symbol: str
    side: str
    actual_avg_fill: Decimal
    qty: Decimal
    fill_time: datetime
    leverage: Decimal = Decimal("1")
    break_even_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.side not in {"Buy", "Sell"}:
            raise ValueError("side must be Buy or Sell")
        if self.actual_avg_fill <= 0 or self.qty <= 0:
            raise ValueError("actual fill and quantity must be positive")


@dataclass(frozen=True)
class PositionEvent:
    observed_at: datetime
    mark_price: Decimal
    features: Mapping[str, FeatureEvidence] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionSnapshot:
    identity: PositionIdentity
    observed_at: datetime
    mark_price: Decimal
    price_move_pct: Decimal
    mfe_pct: Decimal
    mae_pct: Decimal
    giveback_pct: Decimal
    recovery_from_mae_pct: Decimal
    state: SupervisorState
    previous_state: SupervisorState | None
    state_since: datetime
    reason: str
    shadow_action: str
    confidence: Decimal
    features: Mapping[str, FeatureEvidence]
    engine_version: str = "position-supervisor-v1"

    def audit_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.observed_at.astimezone(UTC).isoformat(),
            "position_id": self.identity.position_id,
            "symbol": self.identity.symbol,
            "side": self.identity.side,
            "avg_fill": str(self.identity.actual_avg_fill),
            "qty": str(self.identity.qty),
            "price": str(self.mark_price),
            "price_move_pct": str(self.price_move_pct),
            "mfe_pct": str(self.mfe_pct),
            "mae_pct": str(self.mae_pct),
            "giveback_pct": str(self.giveback_pct),
            "old_state": None if self.previous_state is None else self.previous_state.value,
            "new_state": self.state.value,
            "reason": self.reason,
            "shadow_action": self.shadow_action,
            "confidence": str(self.confidence),
            "engine_version": self.engine_version,
            "features": {
                name: {
                    "state": value.state,
                    "quality": value.quality.value,
                    "observed_at": value.observed_at.astimezone(UTC).isoformat(),
                    "measurements": {
                        k: str(v) if isinstance(v, Decimal) else v
                        for k, v in value.measurements.items()
                    },
                }
                for name, value in self.features.items()
            },
        }
