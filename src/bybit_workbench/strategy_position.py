from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from bybit_workbench.universal_entry.contracts import TradeDirection


@dataclass(frozen=True, slots=True)
class StrategyPosition:
    """Exact logical position owned by one immutable Strategy/plan lineage."""

    strategy_position_id: str
    account_ref: str
    exchange_position_key: str
    position_idx: int

    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    strategy_activation_id: str

    signal_id: str
    strategy_attempt_id: str
    entry_decision_id: str
    entry_execution_request_id: str

    entry_plan_fingerprint: str
    exit_plan_fingerprint: str

    entry_command_id: str
    symbol: str
    direction: TradeDirection
    actual_avg_fill: Decimal
    actual_qty: Decimal
    fill_at: datetime
    exchange_position_slot_claim_id: str | None = None
    position_mode_state_ref: str | None = None
    initial_protection_confirmed_at: datetime | None = None

    def __post_init__(self) -> None:
        required_text = (
            "strategy_position_id",
            "account_ref",
            "exchange_position_key",
            "strategy_id",
            "strategy_version",
            "strategy_config_fingerprint",
            "strategy_activation_id",
            "signal_id",
            "strategy_attempt_id",
            "entry_decision_id",
            "entry_execution_request_id",
            "entry_plan_fingerprint",
            "exit_plan_fingerprint",
            "entry_command_id",
            "symbol",
        )
        for field in required_text:
            if not str(getattr(self, field)).strip():
                raise ValueError(f"StrategyPosition requires {field}")
        if self.position_idx < 0:
            raise ValueError("position_idx cannot be negative")
        if self.actual_avg_fill <= 0 or self.actual_qty <= 0:
            raise ValueError("StrategyPosition fill and quantity must be positive")
        if self.fill_at.tzinfo is None:
            raise ValueError("StrategyPosition fill_at must be timezone-aware")

    @property
    def fill_at_utc(self) -> datetime:
        return self.fill_at.astimezone(UTC)
