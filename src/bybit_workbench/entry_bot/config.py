from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import REFERENCE_SYMBOLS, WORKING_SYMBOLS


@dataclass(frozen=True, slots=True)
class EntryBotConfig:
    working_symbols: tuple[str, ...] = WORKING_SYMBOLS
    reference_symbols: tuple[str, ...] = REFERENCE_SYMBOLS
    history_limit: int = 1000
    five_minute_lookback: int = 130
    fifteen_minute_lookback: int = 130
    hourly_lookback: int = 130
    atr_period: int = 200
    zone_half_width_atr: Decimal = Decimal("0.5")
    confluence_max_gap_percent: Decimal = Decimal("0.25")
    candidate_cooldown_minutes: int = 30
    failure_embargo_minutes: int = 60
    shock_atr_period: int = 20
    shock_atr_multiple: Decimal = Decimal("3.0")
    embargo_minutes_after_shock: int = 60
    hourly_swing_pause_percent: Decimal = Decimal("10.0")
    approach_display_percent: Decimal = Decimal("0.25")
    watch_display_percent: Decimal = Decimal("0.60")
    public_trade_flow_warmup_minutes: int = 5
    candidate_outcome_horizon_minutes: int = 360
    monitoring_only_expanded_universe: bool = False
    require_oi_calibration: bool = True

    def __post_init__(self) -> None:
        if not self.monitoring_only_expanded_universe and len(self.working_symbols) != 10:
            raise ValueError("Entry Bot requires exactly ten working symbols")
        if len(set(self.working_symbols)) != len(self.working_symbols):
            raise ValueError("working symbols must be unique")
        if set(self.working_symbols) & set(self.reference_symbols):
            raise ValueError("working and reference symbols must be disjoint")
        if not self.monitoring_only_expanded_universe and {"BTCUSDT", "ETHUSDT"} != set(self.reference_symbols):
            raise ValueError("BTCUSDT and ETHUSDT are frozen market reference symbols")
        if not self.monitoring_only_expanded_universe and {"1000PEPEUSDT", "DOGEUSDT"} & set(self.working_symbols):
            raise ValueError("meme assets are excluded from the ten-symbol bot universe")
        if self.history_limit < self.atr_period:
            raise ValueError("history_limit must cover ATR history")
        if self.zone_half_width_atr <= 0:
            raise ValueError("zone_half_width_atr must be positive")
        if self.confluence_max_gap_percent < 0:
            raise ValueError("confluence_max_gap_percent cannot be negative")
        if self.candidate_cooldown_minutes < 0:
            raise ValueError("candidate cooldown cannot be negative")
        if self.failure_embargo_minutes <= 0:
            raise ValueError("failure embargo must be positive")
        if self.hourly_swing_pause_percent <= 0:
            raise ValueError("hourly swing pause percent must be positive")
        if self.public_trade_flow_warmup_minutes < 5:
            raise ValueError("flow warmup must cover the frozen 4+1 minute windows")

    @property
    def all_market_symbols(self) -> tuple[str, ...]:
        return self.working_symbols + self.reference_symbols
