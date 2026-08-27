from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass

from bybit_workbench.domain.models import Candle, InstrumentRules, Order, Position

_DTO_CONFIG = ConfigDict(strict=True, extra="forbid", arbitrary_types_allowed=True)


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class AccountSnapshot:
    account_type: str
    equity: Decimal
    available_balance: Decimal
    wallet_balance: Decimal
    unrealized_pnl: Decimal
    observed_at: datetime
    margin_mode: str | None = None
    unified_margin_status: int | None = None
    maker_fee_rate: Decimal | None = None
    taker_fee_rate: Decimal | None = None
    daily_realized_pnl: Decimal | None = None


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class BybitPositionSnapshot:
    position: Position
    position_idx: int
    leverage: Decimal | None
    mark_price: Decimal | None
    liquidation_price: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    trailing_stop_distance: Decimal | None
    unrealized_pnl: Decimal
    sequence: int | None
    observed_at: datetime
    # Exchange-provided economic break-even for this exact position. Keep it
    # separate from average entry; Bybit may include position-specific costs.
    break_even_price: Decimal | None = None


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class TickerSnapshot:
    symbol: str
    last_price: Decimal
    mark_price: Decimal | None
    index_price: Decimal | None
    funding_rate: Decimal | None
    next_funding_at: datetime | None
    observed_at: datetime


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class BybitReadSnapshot:
    instrument: InstrumentRules
    account: AccountSnapshot
    position: BybitPositionSnapshot
    open_orders: tuple[Order, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class MainnetAccountWideSnapshot:
    """Complete contract state used only by the Mainnet safety provider."""

    instrument: InstrumentRules
    account: AccountSnapshot
    position: BybitPositionSnapshot
    other_positions: tuple[BybitPositionSnapshot, ...]
    open_orders: tuple[Order, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class PublicMarketState:
    ticker: TickerSnapshot | None
    latest_candle: Candle | None


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class ApiKeyPermissionAudit:
    contract_trade: tuple[str, ...]
    spot: tuple[str, ...]
    wallet: tuple[str, ...]
    options: tuple[str, ...]
    other: tuple[tuple[str, tuple[str, ...]], ...]
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def execution_eligible(self) -> bool:
        return not self.blocking_reasons


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class ApiKeyInfo:
    note: str
    read_only: bool
    ip_bindings: tuple[str, ...]
    deadline_day: int | None
    expired_at: datetime | None
    created_at: datetime | None
    is_master: bool
    parent_uid: str | None
    unified_account: bool
    key_type: int | None
    permissions: ApiKeyPermissionAudit
    key_id: str | None = None


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class MainnetConnectionTestReport:
    endpoint: str
    server_time: datetime
    clock_offset_ms: int
    api_key: ApiKeyInfo | None
    wallet: AccountSnapshot
    position: BybitPositionSnapshot
    open_order_count: int
    completed_steps: tuple[str, ...]

    @property
    def arming_blocked(self) -> bool:
        return self.api_key is None or not self.api_key.permissions.execution_eligible


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class ClosedPnlRecord:
    symbol: str
    order_id: str
    side: str
    quantity: Decimal
    closed_size: Decimal
    average_entry_price: Decimal
    average_exit_price: Decimal
    closed_pnl: Decimal
    open_fee: Decimal | None
    close_fee: Decimal | None
    order_type: str
    leverage: Decimal | None
    created_at: datetime
    updated_at: datetime
