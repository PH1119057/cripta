from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from bybit_workbench.domain.models import Candle, Execution, InstrumentRules, Order
from bybit_workbench.domain.types import AppMode, AppState, ExecutionMode
from bybit_workbench.exchange.bybit.health import BybitHealthSnapshot, ChannelHealth
from bybit_workbench.exchange.bybit.models import (
    AccountSnapshot,
    BybitPositionSnapshot,
    BybitReadSnapshot,
    ClosedPnlRecord,
    MainnetConnectionTestReport,
    TickerSnapshot,
)
from bybit_workbench.risk.models import RiskCheck, RiskDecision

if TYPE_CHECKING:
    from bybit_workbench.exchange.bybit.streams import (
        BybitStreamProcessor,
        BybitStreamSnapshot,
    )


@dataclass(frozen=True, slots=True)
class ConnectionIndicator:
    connected: bool = False
    fresh: bool = False
    last_message_at: datetime | None = None
    detail: str = "Нет подключения"

    @classmethod
    def from_health(cls, health: ChannelHealth) -> ConnectionIndicator:
        if health.last_error:
            detail = health.last_error
        elif health.fresh:
            detail = "Подключено, данные актуальны"
        elif health.connected:
            detail = "Подключено, данные устарели"
        else:
            detail = "Нет подключения"
        return cls(
            connected=health.connected,
            fresh=health.fresh,
            last_message_at=health.last_message_at,
            detail=detail,
        )


@dataclass(frozen=True, slots=True)
class UserFacingError:
    what_happened: str
    automatic_action: str
    user_action: str

    def __post_init__(self) -> None:
        if not all(
            value.strip() for value in (self.what_happened, self.automatic_action, self.user_action)
        ):
            raise ValueError("all user-facing error fields are required")

    @property
    def text(self) -> str:
        return (
            f"Что произошло: {self.what_happened}\n"
            f"Что сделала система: {self.automatic_action}\n"
            f"Что сделать вам: {self.user_action}"
        )


@dataclass(frozen=True, slots=True)
class ProtectionView:
    planned_stop: Decimal | None = None
    requested_stop: Decimal | None = None
    confirmed_stop: Decimal | None = None
    planned_take_profit: Decimal | None = None
    requested_take_profit: Decimal | None = None
    confirmed_take_profit: Decimal | None = None


@dataclass(frozen=True, slots=True)
class WorkbenchViewState:
    mode: AppMode
    execution_mode: ExecutionMode = ExecutionMode.SHADOW
    execution_phase: str = "DISARMED"
    execution_detail: str = "Mutating requests are blocked"
    arming_ticket_expires_at: datetime | None = None
    endpoint: str | None = None
    api_account_scope: str | None = None
    api_access: str | None = None
    api_ip_binding: str | None = None
    api_expiry: datetime | None = None
    api_deadline_day: int | None = None
    api_permissions: tuple[str, ...] = ()
    api_permission_warnings: tuple[str, ...] = ()
    arming_blockers: tuple[str, ...] = ()
    clock_offset_ms: int | None = None
    engine_state: AppState = AppState.DISCONNECTED
    public: ConnectionIndicator = ConnectionIndicator()
    private: ConnectionIndicator = ConnectionIndicator()
    rest: ConnectionIndicator = ConnectionIndicator()
    symbol: str = "BTCUSDT"
    timeframe: str = "60"
    strategy: str = "Manual protected trade"
    risk_profile: str = "Default"
    instrument: InstrumentRules | None = None
    equity: Decimal | None = None
    available_balance: Decimal | None = None
    wallet_balance: Decimal | None = None
    account_margin_mode: str | None = None
    account_leverage: Decimal | None = None
    maker_fee_rate: Decimal | None = None
    taker_fee_rate: Decimal | None = None
    funding_rate: Decimal | None = None
    daily_realized_pnl: Decimal | None = None
    last_price: Decimal | None = None
    mark_price: Decimal | None = None
    position_side: str = "Flat"
    position_quantity: Decimal = Decimal("0")
    position_average_price: Decimal | None = None
    position_break_even_price: Decimal | None = None
    liquidation_price: Decimal | None = None
    unrealized_pnl: Decimal = Decimal("0")
    signal: str = "Нет сигнала"
    signal_reason: str = "Ожидание закрытой свечи"
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    stop_distance: Decimal | None = None
    proposed_quantity: Decimal | None = None
    risk_budget: Decimal | None = None
    risk_amount: Decimal | None = None
    estimated_fees: Decimal | None = None
    estimated_slippage: Decimal | None = None
    estimated_funding: Decimal | None = None
    minimum_viable_quantity: Decimal | None = None
    minimum_viable_loss_at_stop: Decimal | None = None
    minimum_viable_risk_percent: Decimal | None = None
    protection: ProtectionView = ProtectionView()
    candles: tuple[Candle, ...] = ()
    orders: tuple[Order, ...] = ()
    executions: tuple[Execution, ...] = ()
    closed_trades: tuple[ClosedPnlRecord, ...] = ()
    risk_checks: tuple[RiskCheck, ...] = ()
    strategy_decisions: tuple[str, ...] = ()
    risk_events: tuple[str, ...] = ()
    system_log: tuple[str, ...] = ()
    error: UserFacingError | None = None

    @property
    def connection_safe_for_entries(self) -> bool:
        if self.mode is AppMode.REPLAY:
            return True
        return self.public.fresh and self.private.fresh and self.rest.fresh


class WorkbenchViewModel:
    def __init__(
        self,
        mode: AppMode,
        *,
        symbol: str = "BTCUSDT",
        timeframe: str = "60",
        max_candles: int = 500,
        max_log_entries: int = 500,
    ) -> None:
        if max_candles <= 0 or max_log_entries <= 0:
            raise ValueError("view history limits must be positive")
        self._state = WorkbenchViewState(mode=mode, symbol=symbol, timeframe=timeframe)
        self._max_candles = max_candles
        self._max_log_entries = max_log_entries

    @property
    def state(self) -> WorkbenchViewState:
        return self._state

    def set_engine_state(self, state: AppState) -> None:
        self._state = replace(self._state, engine_state=state)

    def set_execution_status(
        self,
        mode: ExecutionMode,
        phase: str,
        detail: str,
        ticket_expires_at: datetime | None = None,
    ) -> None:
        if not phase.strip() or not detail.strip():
            raise ValueError("execution phase and detail are required")
        if ticket_expires_at is not None and ticket_expires_at.tzinfo is None:
            raise ValueError("ticket expiry must be timezone-aware")
        self._state = replace(
            self._state,
            execution_mode=mode,
            execution_phase=phase.strip(),
            execution_detail=detail.strip(),
            arming_ticket_expires_at=ticket_expires_at,
        )

    def set_market(self, symbol: str, timeframe: str) -> None:
        if not symbol.strip() or not timeframe.strip():
            raise ValueError("symbol and timeframe are required")
        self._state = replace(
            self._state,
            symbol=symbol.strip().upper(),
            timeframe=timeframe.strip(),
        )

    def set_risk_profile(self, profile: str) -> None:
        if not profile.strip():
            raise ValueError("risk profile label is required")
        self._state = replace(self._state, risk_profile=profile.strip())

    def set_clock_offset(self, offset_ms: int | None) -> None:
        self._state = replace(self._state, clock_offset_ms=offset_ms)

    def set_closed_trades(self, records: tuple[ClosedPnlRecord, ...]) -> None:
        self._state = replace(self._state, closed_trades=tuple(records[:500]))

    def apply_health(self, health: BybitHealthSnapshot) -> None:
        self._state = replace(
            self._state,
            public=ConnectionIndicator.from_health(health.public),
            private=ConnectionIndicator.from_health(health.private),
            rest=ConnectionIndicator.from_health(health.rest),
        )

    def health_snapshot(self) -> BybitHealthSnapshot:
        state = self._state
        return BybitHealthSnapshot(
            ChannelHealth(
                state.public.connected,
                state.public.fresh,
                state.public.last_message_at,
                None if state.public.fresh else state.public.detail,
            ),
            ChannelHealth(
                state.private.connected,
                state.private.fresh,
                state.private.last_message_at,
                None if state.private.fresh else state.private.detail,
            ),
            ChannelHealth(
                state.rest.connected,
                state.rest.fresh,
                state.rest.last_message_at,
                None if state.rest.fresh else state.rest.detail,
            ),
        )

    def apply_read_snapshot(self, snapshot: BybitReadSnapshot) -> None:
        self._apply_account(snapshot.account)
        self._apply_position(snapshot.position)
        self._state = replace(
            self._state,
            symbol=snapshot.instrument.symbol,
            instrument=snapshot.instrument,
            orders=tuple(snapshot.open_orders),
        )

    def apply_connection_test(self, report: MainnetConnectionTestReport) -> None:
        key = report.api_key
        if key is None:
            self._state = replace(
                self._state,
                endpoint=report.endpoint,
                clock_offset_ms=report.clock_offset_ms,
                api_account_scope="unknown",
                api_access="metadata unavailable",
                api_ip_binding="unknown",
                api_expiry=None,
                api_deadline_day=None,
                api_permissions=(),
                api_permission_warnings=(
                    "Bybit /v5/user/query-api metadata is temporarily unavailable",
                ),
                arming_blockers=(
                    "API key metadata unavailable; Mainnet execution arming is blocked",
                ),
            )
            return
        audit = key.permissions
        permission_rows = (
            *(f"ContractTrade:{value}" for value in audit.contract_trade),
            *(f"Spot:{value}" for value in audit.spot),
            *(f"Wallet:{value}" for value in audit.wallet),
            *(f"Options:{value}" for value in audit.options),
            *(
                f"{name}:{value}"
                for name, values in audit.other
                for value in values
            ),
        )
        blockers = list(audit.blocking_reasons)
        if key.read_only:
            blockers.append("API key is read-only")
        if not key.is_master or key.parent_uid is not None:
            blockers.append("dedicated main account is required; subaccount is blocked")
        if not key.unified_account:
            blockers.append("Unified Trading Account permission is required")
        if key.key_type != 1:
            blockers.append("personal transaction API key type=1 is required")
        if audit.spot:
            blockers.append("excess Spot permissions must be removed")
        if audit.options:
            blockers.append("excess Options/USDC permissions must be removed")
        forbidden_other = tuple(
            (name, values)
            for name, values in audit.other
            if name != "Derivatives" or any(value != "DerivativesTrade" for value in values)
        )
        if forbidden_other:
            blockers.append(
                "unrecognized permissions must be removed: "
                + ", ".join(name for name, _values in forbidden_other)
            )
        self._state = replace(
            self._state,
            endpoint=report.endpoint,
            clock_offset_ms=report.clock_offset_ms,
            api_account_scope="master" if key.is_master else "subaccount",
            api_access="Read only" if key.read_only else "Read/Write",
            api_ip_binding=", ".join(key.ip_bindings) if key.ip_bindings else "not bound",
            api_expiry=key.expired_at,
            api_deadline_day=key.deadline_day,
            api_permissions=tuple(permission_rows),
            api_permission_warnings=audit.warnings,
            arming_blockers=tuple(blockers),
        )

    def apply_stream_state(self, processor: BybitStreamProcessor) -> None:
        self.apply_stream_snapshot(processor.snapshot())

    def apply_stream_snapshot(self, snapshot: BybitStreamSnapshot) -> None:
        if snapshot.ticker is not None:
            self.apply_ticker(snapshot.ticker)
        if snapshot.latest_candle is not None:
            self.apply_candle(snapshot.latest_candle)
        if snapshot.account is not None:
            self._apply_account(snapshot.account)
        if snapshot.position is not None:
            self._apply_position(snapshot.position)
        self._state = replace(
            self._state,
            orders=snapshot.orders,
        )
        self.merge_executions(snapshot.executions)

    def merge_executions(self, executions: Iterable[Execution]) -> None:
        by_id = {item.execution_id: item for item in self._state.executions}
        for execution in executions:
            existing = by_id.get(execution.execution_id)
            if existing is not None and existing != execution:
                raise ValueError("execution id was reused with different data")
            by_id[execution.execution_id] = execution
        ordered = tuple(
            sorted(
                by_id.values(),
                key=lambda item: (item.executed_at, item.execution_id, item.order_id),
                reverse=True,
            )[:500]
        )
        self._state = replace(self._state, executions=ordered)

    def apply_ticker(self, ticker: TickerSnapshot) -> None:
        if ticker.symbol != self._state.symbol:
            return
        self._state = replace(
            self._state,
            last_price=ticker.last_price,
            mark_price=ticker.mark_price,
            funding_rate=ticker.funding_rate,
        )

    def apply_candle(self, candle: Candle) -> None:
        if candle.symbol != self._state.symbol:
            return
        candles = [
            item
            for item in self._state.candles
            if not (
                item.symbol == candle.symbol
                and item.timeframe == candle.timeframe
                and item.opened_at == candle.opened_at
            )
        ]
        candles.append(candle)
        candles.sort(key=lambda item: item.opened_at)
        self._state = replace(
            self._state,
            timeframe=candle.timeframe,
            last_price=candle.close,
            candles=tuple(candles[-self._max_candles :]),
        )

    def apply_risk_decision(self, decision: RiskDecision) -> None:
        order = decision.normalized_order
        estimated_fees = decision.estimated_fees
        quantity = order.quantity if order is not None else decision.candidate_quantity
        entry = (
            order.price
            if order is not None and order.price is not None
            else decision.normalized_entry
        )
        estimated_funding = None
        if entry is not None and quantity is not None:
            notional = entry * quantity
            if self._state.funding_rate is not None:
                estimated_funding = notional * self._state.funding_rate
        self._state = replace(
            self._state,
            risk_checks=decision.checks,
            proposed_quantity=quantity,
            entry_price=entry,
            stop_price=decision.normalized_stop,
            risk_budget=decision.risk_budget,
            risk_amount=decision.estimated_loss_at_stop,
            estimated_fees=estimated_fees,
            estimated_slippage=decision.estimated_slippage,
            estimated_funding=estimated_funding,
            minimum_viable_quantity=decision.minimum_viable_quantity,
            minimum_viable_loss_at_stop=decision.minimum_viable_loss_at_stop,
            minimum_viable_risk_percent=decision.minimum_viable_risk_percent,
            signal="Разрешён" if decision.approved else "Отклонён",
            signal_reason=(
                "Все risk-gates пройдены"
                if decision.approved
                else ", ".join(decision.rejection_codes)
            ),
        )

    def set_protection(self, protection: ProtectionView) -> None:
        self._state = replace(self._state, protection=protection)

    def set_error(self, error: UserFacingError) -> None:
        self._state = replace(self._state, error=error)
        self.append_system_log(f"ERROR: {error.what_happened}")

    def clear_error(self) -> None:
        self._state = replace(self._state, error=None)

    def append_strategy_decision(self, text: str) -> None:
        self._state = replace(
            self._state,
            strategy_decisions=self._append(self._state.strategy_decisions, text),
        )

    def append_risk_event(self, text: str) -> None:
        self._state = replace(
            self._state,
            risk_events=self._append(self._state.risk_events, text),
        )

    def append_system_log(self, text: str) -> None:
        self._state = replace(
            self._state,
            system_log=self._append(self._state.system_log, text),
        )

    def _apply_account(self, account: AccountSnapshot) -> None:
        self._state = replace(
            self._state,
            equity=account.equity,
            available_balance=account.available_balance,
            wallet_balance=account.wallet_balance,
            daily_realized_pnl=account.daily_realized_pnl,
            account_margin_mode=account.margin_mode,
            maker_fee_rate=account.maker_fee_rate,
            taker_fee_rate=account.taker_fee_rate,
        )

    def _apply_position(self, position: BybitPositionSnapshot) -> None:
        value = position.position
        self._state = replace(
            self._state,
            position_side=value.side.value,
            position_quantity=value.quantity,
            position_average_price=value.average_price,
            position_break_even_price=position.break_even_price,
            mark_price=position.mark_price or self._state.mark_price,
            liquidation_price=position.liquidation_price,
            unrealized_pnl=position.unrealized_pnl,
            account_leverage=position.leverage,
            protection=replace(
                self._state.protection,
                confirmed_stop=position.stop_loss,
                confirmed_take_profit=position.take_profit,
            ),
        )

    def _append(self, current: Iterable[str], text: str) -> tuple[str, ...]:
        cleaned = text.strip()
        if not cleaned:
            return tuple(current)
        return (*tuple(current), cleaned)[-self._max_log_entries :]
