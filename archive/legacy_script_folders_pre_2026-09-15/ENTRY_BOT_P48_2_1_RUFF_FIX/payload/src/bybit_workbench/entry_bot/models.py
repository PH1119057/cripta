from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

Direction = Literal["Long", "Short"]

WORKING_SYMBOLS: tuple[str, ...] = (
    "UNIUSDT",
    "LINKUSDT",
    "XRPUSDT",
    "SOLUSDT",
    "ADAUSDT",
    "BNBUSDT",
    "AVAXUSDT",
    "SUIUSDT",
    "AAVEUSDT",
    "LTCUSDT",
)
REFERENCE_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")


class AssetScanStatus(StrEnum):
    WARMUP = "WARMUP"
    NO_CALIBRATION = "NO CALIBRATION"
    WAITING = "WAITING"
    WATCH = "WATCH"
    APPROACH = "APPROACH"
    COOLDOWN = "COOLDOWN"
    BLOCKED = "BLOCKED"
    SIGNAL = "SIGNAL"
    ERROR = "ERROR"


class ScannerState(StrEnum):
    STOPPED = "STOPPED"
    STOPPING = "STOPPING"
    WARMUP = "WARMUP"
    CONNECTING = "CONNECTING"
    RUNNING = "RUNNING"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class EntryBotCalibration:
    symbol: str
    high_oi_change_60m_pct: Decimal
    low_oi_acceleration_5_vs_60: Decimal
    source_period: str
    source_summary_sha256: str


@dataclass(frozen=True, slots=True)
class EntryZone:
    timeframe: str
    observed_at: datetime
    range_high: Decimal
    range_low: Decimal
    atr: Decimal
    resistance_top: Decimal
    resistance_bottom: Decimal
    support_top: Decimal
    support_bottom: Decimal
    effective_lookback: int
    regime_reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class OiFeatures:
    change_5m_pct: Decimal
    change_60m_pct: Decimal
    acceleration_5_vs_60: Decimal
    anchor_at: datetime


@dataclass(frozen=True, slots=True)
class FlowFeatures:
    pressure_directional_delta_pct: Decimal
    reversal_directional_delta_pct: Decimal
    pressure_total_notional: Decimal
    reversal_total_notional: Decimal
    state: str


@dataclass(frozen=True, slots=True)
class ArmedCandidate:
    symbol: str
    bar_opened_at: datetime
    bar_reference_price: Decimal
    long_entry: Decimal | None
    short_entry: Decimal | None
    long_gap_pct: Decimal | None
    short_gap_pct: Decimal | None
    oi_features: OiFeatures | None


@dataclass(frozen=True, slots=True)
class EntrySignalEvent:
    signal_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    direction: Direction
    candidate_bar_at: datetime
    touch_at: datetime
    entry_price: Decimal
    flow: FlowFeatures
    oi: OiFeatures
    zone_gap_pct: Decimal
    first_touch_live_convention: bool = True


@dataclass(frozen=True, slots=True)
class EntryBotAuditEvent:
    event_id: str
    occurred_at: datetime
    symbol: str
    event_type: str
    status: str
    candidate_id: str | None = None
    direction: Direction | None = None
    candidate_bar_at: datetime | None = None
    entry_price: Decimal | None = None
    last_price: Decimal | None = None
    distance_pct: Decimal | None = None
    flow_state: str = "—"
    oi_state: str = "—"
    reason: str = ""
    payload_json: str = "{}"


@dataclass(frozen=True, slots=True)
class EntryBotAssetSnapshot:
    symbol: str
    status: AssetScanStatus
    side: Direction | None = None
    last_price: Decimal | None = None
    entry_price: Decimal | None = None
    distance_pct: Decimal | None = None
    flow_state: str = "—"
    oi_state: str = "—"
    updated_at: datetime | None = None
    detail: str = ""
    last_signal_id: str | None = None


@dataclass(frozen=True, slots=True)
class EntryBotSnapshot:
    state: ScannerState
    running: bool
    detail: str
    execution_mode: str
    assets: tuple[EntryBotAssetSnapshot, ...] = field(default_factory=tuple)
    updated_at: datetime | None = None
    audit_event_count: int = 0


@dataclass(frozen=True, slots=True)
class PositionHandoff:
    handoff_id: str
    source_signal_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    side: Direction
    quantity: Decimal
    average_entry: Decimal
    initial_stop: Decimal
    entry_order_id: str
    client_order_id: str
    protection_order_id: str | None
    filled_at: datetime
    payload_json: str = "{}"


@dataclass(frozen=True, slots=True)
class ClaimedPositionHandoff:
    handoff: PositionHandoff
    claimed_by: str
    claimed_at: datetime
