from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4

from bybit_workbench import __version__
from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.live_readiness import LiveReadinessGate, LiveReadinessInput
from bybit_workbench.domain.models import InstrumentRules, Order
from bybit_workbench.domain.types import (
    AppMode,
    ExecutionMode,
    OrderSide,
    OrderType,
    PositionSide,
)
from bybit_workbench.exchange.bybit.models import (
    AccountSnapshot,
    ApiKeyInfo,
    BybitPositionSnapshot,
)
from bybit_workbench.exchange.bybit.write_transport import BybitWriteTransport
from bybit_workbench.historical import (
    HistoricalEligibilityQuery,
    eligibility_binding_fingerprint,
    instrument_rules_fingerprint,
)

_TICKET_SEAL = object()


class MutationKind(StrEnum):
    ENTRY = "entry"
    CANCEL = "cancel"
    REDUCE_ONLY = "reduce_only"
    PROTECTION = "protection"
    ACCOUNT_CONFIGURATION = "account_configuration"


class MutationBlocked(PermissionError):
    pass


class UnprotectedPositionEmergency(RuntimeError):
    pass


class HistoricalGateView(Protocol):
    @property
    def allowed(self) -> bool: ...

    @property
    def parameters_fingerprint(self) -> str: ...

    @property
    def report_id(self) -> str | None: ...

    @property
    def dataset_fingerprint(self) -> str | None: ...

    @property
    def binding_fingerprint(self) -> str | None: ...

    @property
    def query(self) -> HistoricalEligibilityQuery | None: ...


class ArmedStrategyView(Protocol):
    @property
    def strategy_id(self) -> str: ...

    @property
    def strategy_version(self) -> str: ...

    @property
    def historical_gate(self) -> HistoricalGateView: ...

    @property
    def requires_historical_validation(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class MicroLiveLimits:
    allowed_symbols: frozenset[str]
    max_order_notional: Decimal
    max_total_exposure: Decimal
    max_daily_loss: Decimal
    max_orders_per_interval: int
    order_interval: timedelta
    cooldown: timedelta
    required_leverage: Decimal = Decimal("1")
    require_isolated_margin: bool = True

    def __post_init__(self) -> None:
        if len(self.allowed_symbols) != 1:
            raise ValueError("Micro-Live requires exactly one allowlisted symbol by default")
        normalized = frozenset(symbol.strip().upper() for symbol in self.allowed_symbols)
        if normalized != self.allowed_symbols or "" in normalized:
            raise ValueError("Micro-Live symbols must be non-empty uppercase values")
        numeric_limits = (
            self.max_order_notional,
            self.max_total_exposure,
            self.max_daily_loss,
            self.required_leverage,
        )
        if any(not value.is_finite() or value <= 0 for value in numeric_limits):
            raise ValueError("Micro-Live numeric limits must be finite and positive")
        if self.max_order_notional > self.max_total_exposure:
            raise ValueError("max order notional cannot exceed max total exposure")
        if self.max_orders_per_interval < 1 or self.order_interval <= timedelta(0):
            raise ValueError("invalid Micro-Live order interval")
        if self.cooldown < timedelta(0):
            raise ValueError("cooldown cannot be negative")


@dataclass(frozen=True, slots=True)
class MicroLiveEntryPlan:
    """Exact normalized entry facts sealed into a short-lived Micro-Live ticket."""

    symbol: str
    client_order_id: str
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None
    risk_percent: Decimal
    risk_budget: Decimal
    estimated_loss_at_stop: Decimal
    order_type: OrderType = OrderType.LIMIT

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.strip().upper():
            raise ValueError("Micro-Live entry-plan symbol must be non-empty uppercase")
        if not self.client_order_id or len(self.client_order_id) > 36:
            raise ValueError("Micro-Live client_order_id must contain 1..36 characters")
        for name in (
            "quantity",
            "limit_price",
            "stop_loss",
            "risk_percent",
            "risk_budget",
            "estimated_loss_at_stop",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.risk_percent > Decimal("10"):
            raise ValueError("Micro-Live risk percent cannot exceed 10%")
        if self.estimated_loss_at_stop > self.risk_budget:
            raise ValueError("estimated loss cannot exceed the sealed risk budget")
        if self.side is OrderSide.BUY and self.stop_loss >= self.limit_price:
            raise ValueError("long Micro-Live stop must be below the limit price")
        if self.side is OrderSide.SELL and self.stop_loss <= self.limit_price:
            raise ValueError("short Micro-Live stop must be above the limit price")
        if self.take_profit is not None:
            if not self.take_profit.is_finite() or self.take_profit <= 0:
                raise ValueError("take_profit must be finite and positive")
            if self.side is OrderSide.BUY and self.take_profit <= self.limit_price:
                raise ValueError("long Micro-Live take profit must be above the limit price")
            if self.side is OrderSide.SELL and self.take_profit >= self.limit_price:
                raise ValueError("short Micro-Live take profit must be below the limit price")


@dataclass(frozen=True, slots=True)
class MainnetMutation:
    """Untrusted request intent; all financial facts are derived inside the gateway."""

    endpoint: str
    params: Mapping[str, Any]
    kind: MutationKind
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.endpoint.startswith("/v5/"):
            raise ValueError("a V5 endpoint is required")
        if not self.idempotency_key or len(self.idempotency_key) > 36:
            raise ValueError("idempotency_key must contain 1..36 characters")
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True, slots=True)
class MainnetSafetySnapshot:
    """Fresh exchange truth used by the write gateway, never supplied by a strategy."""

    endpoint: str
    api_key: ApiKeyInfo
    instrument: InstrumentRules
    account: AccountSnapshot
    position: BybitPositionSnapshot
    other_positions: tuple[BybitPositionSnapshot, ...]
    open_orders: tuple[Order, ...]
    public_observed_at: datetime
    private_observed_at: datetime
    rest_observed_at: datetime
    reconciliation_complete: bool
    positions_complete: bool
    open_orders_complete: bool

    def __post_init__(self) -> None:
        if not self.endpoint.startswith("https://"):
            raise ValueError("Mainnet safety snapshot endpoint must use https://")
        for name in ("public_observed_at", "private_observed_at", "rest_observed_at"):
            if getattr(self, name).tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")


class MainnetSafetyStateProvider(Protocol):
    async def snapshot(self, symbol: str) -> MainnetSafetySnapshot: ...


@dataclass(frozen=True, slots=True)
class ApiKeyIdentity:
    note: str
    created_at: datetime | None
    key_type: int | None
    is_master: bool
    parent_uid: str | None
    unified_account: bool
    key_id: str | None


@dataclass(frozen=True, slots=True)
class MainnetArmingTicket:
    """Short-lived in-memory capability bound to one account context and strategy."""

    ticket_id: str
    mode: ExecutionMode
    endpoint: str
    credential_profile_name: str
    key_identity: ApiKeyIdentity
    symbol: str
    strategy_id: str
    strategy_version: str
    parameters_fingerprint: str
    historical_report_id: str | None
    historical_dataset_fingerprint: str | None
    historical_binding_fingerprint: str | None
    limits: MicroLiveLimits
    entry_plan: MicroLiveEntryPlan
    issued_at: datetime
    expires_at: datetime
    max_state_age: timedelta
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.mode is not ExecutionMode.MICRO_LIVE:
            raise ValueError("only MICRO_LIVE tickets are implemented")
        if self._seal is not _TICKET_SEAL:
            raise ValueError("Mainnet arming tickets must be issued by the safety gate")
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("ticket timestamps must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("ticket expiry must be after issue time")
        if self.max_state_age <= timedelta(0):
            raise ValueError("max_state_age must be positive")

    def is_valid_at(self, now: datetime) -> bool:
        return self.issued_at <= now < self.expires_at


class IdempotencyStore(Protocol):
    def claim_before_send(self, key: str) -> bool:
        """Atomically reserve a key durably before an ambiguous network call."""
        ...


class MemoryIdempotencyStore:
    def __init__(self) -> None:
        self._keys: set[str] = set()

    def claim_before_send(self, key: str) -> bool:
        if key in self._keys:
            return False
        self._keys.add(key)
        return True


def issue_micro_live_ticket(
    settings: AppSettings,
    snapshot: MainnetSafetySnapshot,
    limits: MicroLiveLimits,
    armed_strategy: ArmedStrategyView,
    entry_plan: MicroLiveEntryPlan,
    *,
    now: datetime | None = None,
    ttl: timedelta = timedelta(minutes=2),
    max_state_age: timedelta = timedelta(seconds=10),
) -> MainnetArmingTicket:
    """Issue a capability only from current, reconciled, least-privilege exchange truth."""

    issued_at = _aware_now(now)
    if ttl <= timedelta(0) or ttl > timedelta(minutes=5):
        raise MutationBlocked("Micro-Live ticket TTL must be within five minutes")
    if max_state_age <= timedelta(0) or max_state_age > timedelta(seconds=30):
        raise MutationBlocked("Mainnet state freshness budget must be within thirty seconds")
    if settings.mode is not AppMode.LIVE or not settings.allow_live_trading:
        raise MutationBlocked("external Mainnet live switch is disabled")
    endpoint = settings.endpoint_profile.rest_url
    if endpoint is None or _normalized_endpoint(snapshot.endpoint) != endpoint:
        raise MutationBlocked("arming snapshot endpoint does not match selected settings")
    symbol = next(iter(limits.allowed_symbols))
    if entry_plan.symbol != symbol:
        raise MutationBlocked("sealed entry plan symbol does not match Micro-Live allowlist")
    if entry_plan.limit_price * entry_plan.quantity > limits.max_order_notional:
        raise MutationBlocked("sealed entry plan exceeds Micro-Live order-notional cap")
    if not armed_strategy.historical_gate.allowed:
        raise MutationBlocked("BackTest historical gate has not passed")
    gate = armed_strategy.historical_gate
    fingerprint = gate.parameters_fingerprint.strip()
    if not fingerprint:
        raise MutationBlocked("historical gate parameters fingerprint is missing")
    report_id = gate.report_id
    dataset_fingerprint = gate.dataset_fingerprint
    binding_fingerprint = gate.binding_fingerprint
    if armed_strategy.requires_historical_validation:
        if not report_id:
            raise MutationBlocked("exact historical report id is missing")
        if dataset_fingerprint is None or len(dataset_fingerprint) != 64:
            raise MutationBlocked("historical dataset fingerprint is missing or invalid")
        if binding_fingerprint is None or len(binding_fingerprint) != 64:
            raise MutationBlocked(
                "historical eligibility binding fingerprint is missing or invalid"
            )
        query = gate.query
        if query is None:
            raise MutationBlocked("historical eligibility query binding is missing")
        expected_binding = eligibility_binding_fingerprint(
            strategy_id=armed_strategy.strategy_id,
            strategy_version=armed_strategy.strategy_version,
            parameters_fingerprint=fingerprint,
            query=query,
            dataset_fingerprint=dataset_fingerprint,
        )
        if binding_fingerprint != expected_binding:
            raise MutationBlocked("historical eligibility binding fingerprint is invalid")
        if query.symbol != symbol:
            raise MutationBlocked("historical report symbol does not match Micro-Live symbol")
        if query.timeframe not in {"60", "240"}:
            raise MutationBlocked("historical report timeframe is not approved for Micro-Live")
        if query.code_version != __version__:
            raise MutationBlocked("historical report code version differs from running code")
        if query.execution_mode != "closed-candle-limit-retest":
            raise MutationBlocked("historical execution model differs from Mainnet")
        if query.price_trigger != "MarkPrice":
            raise MutationBlocked("historical trigger must use MarkPrice")
        if query.instrument_rules_fingerprint != instrument_rules_fingerprint(snapshot.instrument):
            raise MutationBlocked("historical instrument rules differ from current Bybit rules")
        if snapshot.account.maker_fee_rate is None or snapshot.account.taker_fee_rate is None:
            raise MutationBlocked("current Mainnet maker/taker fee rates are unavailable")
        if query.maker_fee_rate != snapshot.account.maker_fee_rate:
            raise MutationBlocked("historical maker fee differs from current Mainnet fee")
        if query.taker_fee_rate != snapshot.account.taker_fee_rate:
            raise MutationBlocked("historical taker fee differs from current Mainnet fee")
    _validate_snapshot(
        snapshot,
        endpoint=endpoint,
        symbol=symbol,
        limits=limits,
        expected_key=None,
        now=issued_at,
        max_state_age=max_state_age,
        require_entry_configuration=True,
        require_daily_pnl=True,
        require_protected_position=True,
    )
    readiness = LiveReadinessGate().evaluate(
        settings,
        LiveReadinessInput(
            confirmation_word="LIVE",
            symbol=symbol,
            position_cap=limits.max_total_exposure,
            daily_loss_cap=limits.max_daily_loss,
            first_trade_notional=limits.max_order_notional,
            fresh_public=_is_fresh(snapshot.public_observed_at, issued_at, max_state_age),
            fresh_private=_is_fresh(snapshot.private_observed_at, issued_at, max_state_age),
            fresh_rest=_is_fresh(snapshot.rest_observed_at, issued_at, max_state_age),
            reconciliation_complete=snapshot.reconciliation_complete,
            withdrawal_permission_absent=not snapshot.api_key.permissions.wallet,
        ),
    )
    if not readiness.ready:
        failed = ", ".join(check.code for check in readiness.checks if not check.passed)
        raise MutationBlocked(f"Mainnet readiness failed: {failed}")
    return MainnetArmingTicket(
        ticket_id=uuid4().hex,
        mode=ExecutionMode.MICRO_LIVE,
        endpoint=endpoint,
        credential_profile_name=settings.credential_profile_name,
        key_identity=_key_identity(snapshot.api_key),
        symbol=symbol,
        strategy_id=armed_strategy.strategy_id,
        strategy_version=armed_strategy.strategy_version,
        parameters_fingerprint=fingerprint,
        historical_report_id=report_id,
        historical_dataset_fingerprint=dataset_fingerprint,
        historical_binding_fingerprint=binding_fingerprint,
        limits=limits,
        entry_plan=entry_plan,
        issued_at=issued_at,
        expires_at=issued_at + ttl,
        max_state_age=max_state_age,
        _seal=_TICKET_SEAL,
    )


class ExecutionArmingController:
    """Memory-only capability. New instances always start disarmed in SHADOW."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._mode = ExecutionMode.SHADOW
        self._ticket: MainnetArmingTicket | None = None
        self._kill_switch = False
        self._clock = clock or _utc_now

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch

    @property
    def limits(self) -> MicroLiveLimits | None:
        return None if self._ticket is None else self._ticket.limits

    @property
    def ticket(self) -> MainnetArmingTicket | None:
        return self._ticket

    def arm_micro_live(self, confirmation: str, ticket: MainnetArmingTicket) -> None:
        if self._kill_switch:
            raise MutationBlocked("kill switch must be explicitly reset before arming")
        if confirmation != "ARM MICRO_LIVE":
            raise MutationBlocked("arming requires exact confirmation: ARM MICRO_LIVE")
        now = _aware_now(self._clock())
        if not ticket.is_valid_at(now):
            raise MutationBlocked("Mainnet arming ticket is not currently valid")
        self._ticket = ticket
        self._mode = ExecutionMode.MICRO_LIVE

    def arm_live(self, *_args: object, **_kwargs: object) -> None:
        raise MutationBlocked("full LIVE arming is intentionally unavailable in this release")

    def disarm(self) -> None:
        self._mode = ExecutionMode.SHADOW
        self._ticket = None

    def activate_kill_switch(self) -> None:
        self._kill_switch = True

    def reset_kill_switch(self, confirmation: str) -> None:
        if confirmation != "RESET KILL SWITCH":
            raise MutationBlocked("kill switch reset requires explicit confirmation")
        self._kill_switch = False
        self.disarm()

    def require_ticket(self, *, allow_expired_exit: bool = False) -> MainnetArmingTicket:
        if self._mode is ExecutionMode.SHADOW or self._ticket is None:
            raise MutationBlocked("SHADOW transport blocks every mutating request")
        now = _aware_now(self._clock())
        if not self._ticket.is_valid_at(now) and not allow_expired_exit:
            self.disarm()
            raise MutationBlocked("Mainnet arming ticket expired; execution returned to SHADOW")
        return self._ticket


class _UnavailableSafetyStateProvider:
    async def snapshot(self, symbol: str) -> MainnetSafetySnapshot:
        del symbol
        raise MutationBlocked("fresh Mainnet safety state provider is not connected")


class MainnetMutationGateway:
    """Only route to Mainnet writes; strategies cannot declare their own risk facts."""

    _ALLOWED_ENDPOINTS = frozenset(
        {
            "/v5/order/create",
            "/v5/order/cancel",
            "/v5/position/trading-stop",
        }
    )
    _ENTRY_FIELDS = frozenset(
        {
            "category",
            "symbol",
            "side",
            "orderType",
            "qty",
            "price",
            "timeInForce",
            "positionIdx",
            "orderLinkId",
            "tpslMode",
            "takeProfit",
            "stopLoss",
            "tpTriggerBy",
            "slTriggerBy",
            "tpOrderType",
            "slOrderType",
        }
    )
    _REDUCE_ONLY_FIELDS = frozenset(
        {
            "category",
            "symbol",
            "side",
            "orderType",
            "qty",
            "price",
            "timeInForce",
            "positionIdx",
            "orderLinkId",
            "reduceOnly",
            "closeOnTrigger",
        }
    )
    _CANCEL_FIELDS = frozenset({"category", "symbol", "orderId", "orderLinkId"})
    _PROTECTION_FIELDS = frozenset(
        {
            "category",
            "symbol",
            "positionIdx",
            "tpslMode",
            "takeProfit",
            "stopLoss",
            "tpTriggerBy",
            "slTriggerBy",
            "tpOrderType",
            "slOrderType",
            "trailingStop",
            "activePrice",
        }
    )

    def __init__(
        self,
        delegate: BybitWriteTransport,
        arming: ExecutionArmingController,
        idempotency: IdempotencyStore,
        state_provider: MainnetSafetyStateProvider | None = None,
        *,
        endpoint: str = "https://api.bybit.com",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._delegate = delegate
        self._arming = arming
        self._idempotency = idempotency
        self._state_provider = state_provider or _UnavailableSafetyStateProvider()
        self._endpoint = _normalized_endpoint(endpoint)
        self._clock = clock or _utc_now
        self._submit_lock = asyncio.Lock()
        self._entry_times: deque[datetime] = deque()
        self._last_entry_at: datetime | None = None

    async def submit(self, mutation: MainnetMutation) -> Mapping[str, Any]:
        async with self._submit_lock:
            return await self._submit_locked(mutation)

    async def _submit_locked(self, mutation: MainnetMutation) -> Mapping[str, Any]:
        kind = self._validate_endpoint_semantics(mutation)
        emergency_exit = kind in {MutationKind.CANCEL, MutationKind.REDUCE_ONLY}
        ticket = self._arming.require_ticket(allow_expired_exit=emergency_exit)
        if self._arming.kill_switch_active and not emergency_exit:
            raise MutationBlocked("kill switch permits only cancel and reduce-only actions")
        if ticket.endpoint != self._endpoint:
            raise MutationBlocked("armed ticket endpoint does not match write transport")
        try:
            snapshot = await self._state_provider.snapshot(ticket.symbol)
        except MutationBlocked:
            raise
        except Exception as exc:
            raise MutationBlocked("fresh Mainnet safety snapshot is unavailable") from exc
        now = _aware_now(self._clock())
        _validate_snapshot(
            snapshot,
            endpoint=ticket.endpoint,
            symbol=ticket.symbol,
            limits=ticket.limits,
            expected_key=ticket.key_identity,
            now=now,
            max_state_age=ticket.max_state_age,
            require_entry_configuration=kind is MutationKind.ENTRY,
            require_daily_pnl=kind is MutationKind.ENTRY,
            require_protected_position=kind is MutationKind.ENTRY,
        )
        self._validate_common_payload(mutation.params, ticket.symbol)
        if kind is MutationKind.ENTRY:
            self._authorize_entry(mutation, snapshot, ticket, now)
        elif kind is MutationKind.REDUCE_ONLY:
            self._authorize_reduce_only(mutation, snapshot)
        elif kind is MutationKind.CANCEL:
            self._authorize_cancel(mutation.params)
        elif kind is MutationKind.PROTECTION:
            self._authorize_protection(mutation.params, snapshot)
        if not self._idempotency.claim_before_send(mutation.idempotency_key):
            raise MutationBlocked("duplicate mutation blocked by durable idempotency key")
        response = await self._delegate.post(mutation.endpoint, mutation.params)
        if kind is MutationKind.ENTRY:
            self._entry_times.append(now)
            self._last_entry_at = now
        return response

    def _validate_endpoint_semantics(self, mutation: MainnetMutation) -> MutationKind:
        if self._arming.mode is ExecutionMode.SHADOW:
            raise MutationBlocked("SHADOW transport blocks every mutating request")
        if mutation.endpoint not in self._ALLOWED_ENDPOINTS:
            raise MutationBlocked(f"unsupported Mainnet mutation endpoint: {mutation.endpoint}")
        expected_kind = {
            "/v5/order/cancel": MutationKind.CANCEL,
            "/v5/position/trading-stop": MutationKind.PROTECTION,
        }.get(mutation.endpoint)
        if mutation.endpoint == "/v5/order/create":
            expected_kind = (
                MutationKind.REDUCE_ONLY
                if mutation.params.get("reduceOnly") is True
                else MutationKind.ENTRY
            )
        if mutation.kind is not expected_kind:
            raise MutationBlocked("mutation kind does not match endpoint/payload semantics")
        return mutation.kind

    @staticmethod
    def _validate_common_payload(params: Mapping[str, Any], symbol: str) -> None:
        if params.get("category") != "linear":
            raise MutationBlocked("Mainnet safety gateway supports category=linear only")
        if params.get("symbol") != symbol:
            raise MutationBlocked("payload symbol does not match the armed symbol")
        if params.get("positionIdx", 0) != 0:
            raise MutationBlocked("Micro-Live requires one-way positionIdx=0")

    def _authorize_entry(
        self,
        mutation: MainnetMutation,
        snapshot: MainnetSafetySnapshot,
        ticket: MainnetArmingTicket,
        now: datetime,
    ) -> None:
        params = mutation.params
        _require_allowed_params(params, self._ENTRY_FIELDS, "entry")
        raw_order_type = str(params.get("orderType") or "")
        try:
            order_type = OrderType(raw_order_type)
        except ValueError as exc:
            raise MutationBlocked("Micro-Live entry orderType must be Limit or Market") from exc
        side = _order_side(params)
        quantity = _positive_decimal(params, "qty")
        rules = snapshot.instrument
        _require_step(quantity, rules.qty_step, "qty")
        max_quantity = rules.max_order_qty
        if order_type is OrderType.MARKET and rules.max_market_order_qty is not None:
            max_quantity = rules.max_market_order_qty
        if quantity < rules.min_order_qty or quantity > max_quantity:
            raise MutationBlocked("entry quantity is outside current instrument limits")
        mark_price = _required_mark_price(snapshot.position)
        price: Decimal | None
        if order_type is OrderType.LIMIT:
            if params.get("timeInForce") not in {"GTC", "PostOnly"}:
                raise MutationBlocked("Limit Micro-Live entry requires explicit GTC or PostOnly")
            price = _positive_decimal(params, "price")
            _require_step(price, rules.tick_size, "price")
            reference_price = price
        else:
            if params.get("timeInForce") not in (None, "", "IOC"):
                raise MutationBlocked("Market Micro-Live entry cannot use non-IOC timeInForce")
            if params.get("price") not in (None, ""):
                raise MutationBlocked("Market Micro-Live entry must not send price")
            price = None
            reference_price = mark_price
        self._require_link_id(mutation)
        # The hard protection must travel with the entry itself.  The gateway does
        # not merely trust the sealed ticket here: it verifies that the actual
        # /v5/order/create payload contains the exact sealed stop/TP values and the
        # required server-side MarkPrice/Market execution policy.
        if params.get("tpslMode") != "Full":
            raise MutationBlocked("Micro-Live entry requires tpslMode=Full")
        if params.get("slTriggerBy") != "MarkPrice":
            raise MutationBlocked("Micro-Live entry stop must trigger by MarkPrice")
        if params.get("slOrderType") != "Market":
            raise MutationBlocked("Micro-Live entry stop must execute as Market")
        stop = _positive_decimal(params, "stopLoss")
        take_profit: Decimal | None = None
        raw_take_profit = params.get("takeProfit")
        if raw_take_profit not in (None, "", 0, "0", Decimal("0")):
            take_profit = _positive_decimal(params, "takeProfit")
            if params.get("tpTriggerBy") != "MarkPrice":
                raise MutationBlocked("Micro-Live entry take-profit must trigger by MarkPrice")
            if params.get("tpOrderType") != "Market":
                raise MutationBlocked("Micro-Live entry take-profit must execute as Market")
        elif (
            params.get("tpTriggerBy") not in (None, "")
            or params.get("tpOrderType") not in (None, "")
        ):
            raise MutationBlocked("take-profit trigger/order fields require takeProfit")
        _require_step(stop, rules.tick_size, "stopLoss")
        _require_protective_direction(side, reference_price, stop, "stopLoss")
        if take_profit is not None:
            _require_step(take_profit, rules.tick_size, "takeProfit")
            _require_profit_direction(side, reference_price, take_profit)
        _require_exact_entry_plan(
            ticket.entry_plan,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop=stop,
            take_profit=take_profit,
            client_order_id=str(params.get("orderLinkId") or ""),
        )
        reference_notional = reference_price * quantity
        if reference_notional < rules.min_notional:
            raise MutationBlocked("entry is below the current exchange min notional")
        limits = ticket.limits
        if order_type is OrderType.MARKET:
            order_notional = reference_notional
        else:
            assert price is not None
            # A Buy limit cannot execute above its limit price. A marketable Sell limit,
            # however, may execute at a better (higher) current price, so keep the
            # conservative mark-price bound only for that side.
            order_notional = (
                reference_notional
                if side is OrderSide.BUY
                else max(price, mark_price) * quantity
            )
        if order_notional > limits.max_order_notional:
            raise MutationBlocked("max order notional exceeded")
        exposure = _current_gross_exposure(snapshot, mark_price) + order_notional
        if exposure > limits.max_total_exposure:
            raise MutationBlocked("max total exposure exceeded")
        required_margin = exposure / limits.required_leverage
        if required_margin > snapshot.account.available_balance:
            raise MutationBlocked(
                "insufficient fresh available balance for requested Micro-Live leverage"
            )
        if _derived_daily_loss(snapshot.account) >= limits.max_daily_loss:
            raise MutationBlocked("max daily loss reached")
        cutoff = now - limits.order_interval
        while self._entry_times and self._entry_times[0] <= cutoff:
            self._entry_times.popleft()
        if len(self._entry_times) >= limits.max_orders_per_interval:
            raise MutationBlocked("max orders per interval reached")
        if self._last_entry_at is not None and now - self._last_entry_at < limits.cooldown:
            raise MutationBlocked("Micro-Live cooldown is active")

    @staticmethod
    def _authorize_reduce_only(
        mutation: MainnetMutation,
        snapshot: MainnetSafetySnapshot,
    ) -> None:
        params = mutation.params
        _require_allowed_params(
            params,
            MainnetMutationGateway._REDUCE_ONLY_FIELDS,
            "reduce-only close",
        )
        position = snapshot.position.position
        if position.side is PositionSide.FLAT or position.quantity <= 0:
            raise MutationBlocked("reduce-only close requires a confirmed open position")
        expected_side = (
            OrderSide.SELL if position.side is PositionSide.LONG else OrderSide.BUY
        )
        if _order_side(params) is not expected_side:
            raise MutationBlocked("reduce-only side must close the confirmed position")
        quantity = _positive_decimal(params, "qty")
        _require_step(quantity, snapshot.instrument.qty_step, "qty")
        if quantity > position.quantity:
            raise MutationBlocked("reduce-only quantity exceeds the confirmed position")
        order_type = params.get("orderType")
        if order_type == OrderType.LIMIT.value:
            price = _positive_decimal(params, "price")
            _require_step(price, snapshot.instrument.tick_size, "price")
            if params.get("timeInForce") not in {"GTC", "IOC"}:
                raise MutationBlocked("reduce-only Limit requires explicit GTC or IOC")
        elif order_type != OrderType.MARKET.value:
            raise MutationBlocked("reduce-only orderType must be Market or Limit")
        elif params.get("price") not in (None, "") or params.get("timeInForce") not in (
            None,
            "",
        ):
            raise MutationBlocked("reduce-only Market cannot declare price or timeInForce")
        if params.get("closeOnTrigger", False) is not False:
            raise MutationBlocked("direct reduce-only close cannot use closeOnTrigger")
        MainnetMutationGateway._require_link_id(mutation)

    @staticmethod
    def _authorize_cancel(params: Mapping[str, Any]) -> None:
        _require_allowed_params(params, MainnetMutationGateway._CANCEL_FIELDS, "cancel")
        order_id = str(params.get("orderId") or "").strip()
        link_id = str(params.get("orderLinkId") or "").strip()
        if bool(order_id) == bool(link_id):
            raise MutationBlocked("cancel requires exactly one of orderId or orderLinkId")

    @staticmethod
    def _authorize_protection(
        params: Mapping[str, Any],
        snapshot: MainnetSafetySnapshot,
    ) -> None:
        _require_allowed_params(
            params,
            MainnetMutationGateway._PROTECTION_FIELDS,
            "server protection",
        )
        position = snapshot.position
        if position.position.side is PositionSide.FLAT:
            raise MutationBlocked("server protection requires a confirmed open position")
        mark = _required_mark_price(position)
        stop = _positive_decimal(params, "stopLoss")
        _require_step(stop, snapshot.instrument.tick_size, "stopLoss")
        if (
            params.get("slTriggerBy") != "MarkPrice"
            or params.get("tpslMode") != "Full"
            or params.get("slOrderType") != "Market"
        ):
            raise MutationBlocked(
                "server protection must use Full mode, MarkPrice and Market stop execution"
            )
        side = (
            OrderSide.BUY
            if position.position.side is PositionSide.LONG
            else OrderSide.SELL
        )
        _require_protective_direction(side, mark, stop, "stopLoss")
        existing_stop = position.stop_loss
        if existing_stop is not None and (
            (side is OrderSide.BUY and stop < existing_stop)
            or (side is OrderSide.SELL and stop > existing_stop)
        ):
            raise MutationBlocked("server stop cannot be moved in the risk-increasing direction")
        if params.get("takeProfit") not in (None, "", 0, "0", Decimal("0")):
            take_profit = _positive_decimal(params, "takeProfit")
            _require_step(take_profit, snapshot.instrument.tick_size, "takeProfit")
            _require_profit_direction(side, mark, take_profit)
            if params.get("tpTriggerBy") != "MarkPrice":
                raise MutationBlocked("take-profit protection must trigger by MarkPrice")
            if params.get("tpOrderType") != "Market":
                raise MutationBlocked("take-profit protection must execute as Market")
        trailing = params.get("trailingStop")
        active_price = params.get("activePrice")
        if trailing not in (None, "", 0, "0", Decimal("0")):
            trailing_distance = _positive_decimal(params, "trailingStop")
            _require_step(trailing_distance, snapshot.instrument.tick_size, "trailingStop")
        elif active_price not in (None, "", 0, "0", Decimal("0")):
            raise MutationBlocked("activePrice requires a positive trailingStop")
        if active_price not in (None, "", 0, "0", Decimal("0")):
            activation = _positive_decimal(params, "activePrice")
            _require_step(activation, snapshot.instrument.tick_size, "activePrice")

    @staticmethod
    def _require_link_id(mutation: MainnetMutation) -> None:
        link_id = str(mutation.params.get("orderLinkId") or "")
        if link_id != mutation.idempotency_key:
            raise MutationBlocked("orderLinkId must equal the durable idempotency key")


def require_confirmed_server_stop(position: BybitPositionSnapshot) -> None:
    if position.position.quantity > 0 and (position.stop_loss is None or position.stop_loss <= 0):
        raise UnprotectedPositionEmergency(
            "open position has no confirmed server-side stop; block entries and initiate "
            "reduce-only emergency close"
        )


def _validate_snapshot(
    snapshot: MainnetSafetySnapshot,
    *,
    endpoint: str,
    symbol: str,
    limits: MicroLiveLimits,
    expected_key: ApiKeyIdentity | None,
    now: datetime,
    max_state_age: timedelta,
    require_entry_configuration: bool,
    require_daily_pnl: bool,
    require_protected_position: bool,
) -> None:
    if _normalized_endpoint(snapshot.endpoint) != _normalized_endpoint(endpoint):
        raise MutationBlocked("fresh state endpoint does not match the armed endpoint")
    if snapshot.instrument.symbol != symbol or snapshot.position.position.symbol != symbol:
        raise MutationBlocked("fresh state symbol does not match the armed symbol")
    if not snapshot.reconciliation_complete:
        raise MutationBlocked("exchange truth is not reconciled with the local projection")
    if require_entry_configuration:
        if not snapshot.positions_complete:
            raise MutationBlocked("account-wide position snapshot is incomplete")
        if not snapshot.open_orders_complete:
            raise MutationBlocked("account-wide open-order snapshot is incomplete")
        for other in snapshot.other_positions:
            if other.position.symbol == symbol:
                raise MutationBlocked("duplicate selected position exists in account-wide state")
            if other.position.quantity > 0:
                raise MutationBlocked("open position exists outside the one-symbol allowlist")
    for name, observed_at in (
        ("Public WS", snapshot.public_observed_at),
        ("Private WS", snapshot.private_observed_at),
        ("REST", snapshot.rest_observed_at),
    ):
        _require_fresh_state(name, observed_at, now, max_state_age)
    _validate_key(snapshot.api_key, now)
    if expected_key is not None and _key_identity(snapshot.api_key) != expected_key:
        raise MutationBlocked("API key identity changed after arming")
    account = snapshot.account
    if account.account_type != "UNIFIED":
        raise MutationBlocked("Micro-Live requires the Unified Trading Account")
    if account.unified_margin_status not in {5, 6}:
        raise MutationBlocked("Micro-Live requires UTA 2.0 account status")
    position = snapshot.position
    if position.position_idx != 0:
        raise MutationBlocked("Micro-Live requires one-way positionIdx=0")
    if require_entry_configuration:
        if limits.require_isolated_margin and account.margin_mode != "ISOLATED_MARGIN":
            raise MutationBlocked("Micro-Live requires account marginMode=ISOLATED_MARGIN")
        if position.leverage != limits.required_leverage:
            raise MutationBlocked("fresh position leverage does not match the Micro-Live limit")
    if require_daily_pnl and account.daily_realized_pnl is None:
        raise MutationBlocked("fresh UTC-day realized PnL is required")
    if require_protected_position:
        try:
            require_confirmed_server_stop(position)
        except UnprotectedPositionEmergency as exc:
            raise MutationBlocked(str(exc)) from exc


def _validate_key(key_info: ApiKeyInfo, now: datetime) -> None:
    if key_info.read_only:
        raise MutationBlocked("API key is read-only")
    if key_info.permissions.blocking_reasons:
        raise MutationBlocked("; ".join(key_info.permissions.blocking_reasons))
    if key_info.expired_at is not None and key_info.expired_at <= now:
        raise MutationBlocked("API key is expired")
    if not key_info.is_master or key_info.parent_uid is not None:
        raise MutationBlocked("this release requires the dedicated main account, not a subaccount")
    if not key_info.unified_account:
        raise MutationBlocked("API key is not bound to a Unified Trading Account")
    if key_info.key_type != 1:
        raise MutationBlocked("Micro-Live requires a personal transaction API key (type=1)")
    permissions = key_info.permissions
    missing_contract = {"Order", "Position"}.difference(permissions.contract_trade)
    if missing_contract:
        missing = ", ".join(sorted(missing_contract))
        raise MutationBlocked(f"ContractTrade permissions are missing: {missing}")
    if permissions.spot:
        raise MutationBlocked("excess Spot permissions must be removed before Micro-Live")
    if permissions.options:
        raise MutationBlocked("excess Options/USDC permissions must be removed before Micro-Live")
    forbidden_other = tuple(
        (name, values)
        for name, values in permissions.other
        if name != "Derivatives" or any(value != "DerivativesTrade" for value in values)
    )
    if forbidden_other:
        names = ", ".join(name for name, _values in forbidden_other)
        raise MutationBlocked(f"unrecognized excess API permissions must be removed: {names}")


def _current_gross_exposure(snapshot: MainnetSafetySnapshot, mark_price: Decimal) -> Decimal:
    position = snapshot.position.position
    prices = [mark_price]
    if position.average_price is not None and position.average_price > 0:
        prices.append(position.average_price)
    exposure = position.quantity * max(prices)
    for order in snapshot.open_orders:
        request = order.request
        if request.symbol != snapshot.instrument.symbol:
            raise MutationBlocked("open order exists outside the one-symbol allowlist")
        if request.reduce_only:
            continue
        if request.order_type is not OrderType.LIMIT or request.price is None:
            raise MutationBlocked("unpriced open entry order prevents safe exposure calculation")
        remaining = order.remaining_quantity
        if remaining > 0:
            exposure += remaining * max(request.price, mark_price)
    return exposure


def _derived_daily_loss(account: AccountSnapshot) -> Decimal:
    if account.daily_realized_pnl is None:
        raise MutationBlocked("fresh UTC-day realized PnL is required")
    conservative_pnl = account.daily_realized_pnl + min(account.unrealized_pnl, Decimal("0"))
    return max(Decimal("0"), -conservative_pnl)


def _required_mark_price(position: BybitPositionSnapshot) -> Decimal:
    mark = position.mark_price
    if mark is None or not mark.is_finite() or mark <= 0:
        raise MutationBlocked("fresh positive Mark Price is required")
    return mark


def _order_side(params: Mapping[str, Any]) -> OrderSide:
    try:
        return OrderSide(str(params.get("side")))
    except ValueError as exc:
        raise MutationBlocked("order side must be Buy or Sell") from exc


def _positive_decimal(params: Mapping[str, Any], name: str) -> Decimal:
    value = params.get(name)
    if isinstance(value, bool) or value in (None, ""):
        raise MutationBlocked(f"{name} must be a positive decimal")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MutationBlocked(f"{name} must be a positive decimal") from exc
    if not decimal.is_finite() or decimal <= 0:
        raise MutationBlocked(f"{name} must be a positive decimal")
    return decimal


def _require_step(value: Decimal, step: Decimal, name: str) -> None:
    if value % step != 0:
        raise MutationBlocked(f"{name} is not aligned to current exchange precision")


def _require_exact_entry_plan(
    plan: MicroLiveEntryPlan,
    *,
    side: OrderSide,
    order_type: OrderType,
    quantity: Decimal,
    price: Decimal | None,
    stop: Decimal,
    take_profit: Decimal | None,
    client_order_id: str,
) -> None:
    if client_order_id != plan.client_order_id:
        raise MutationBlocked("entry orderLinkId differs from the sealed Micro-Live plan")
    if side is not plan.side:
        raise MutationBlocked("entry side differs from the sealed Micro-Live plan")
    if order_type is not plan.order_type:
        raise MutationBlocked("entry order type differs from the sealed Micro-Live plan")
    if quantity != plan.quantity:
        raise MutationBlocked("entry quantity differs from the sealed Micro-Live plan")
    if order_type is OrderType.LIMIT and price != plan.limit_price:
        raise MutationBlocked("entry price differs from the sealed Micro-Live plan")
    if order_type is OrderType.MARKET and price is not None:
        raise MutationBlocked("sealed Market entry must not carry a limit price")
    if stop != plan.stop_loss:
        raise MutationBlocked("entry stop differs from the sealed Micro-Live plan")
    if take_profit != plan.take_profit:
        raise MutationBlocked("entry take-profit differs from the sealed Micro-Live plan")


def _require_allowed_params(
    params: Mapping[str, Any],
    allowed: frozenset[str],
    operation: str,
) -> None:
    unexpected = sorted(set(params).difference(allowed))
    if unexpected:
        raise MutationBlocked(
            f"{operation} contains unsupported parameters: {', '.join(unexpected)}"
        )


def _require_protective_direction(
    side: OrderSide,
    reference: Decimal,
    stop: Decimal,
    name: str,
) -> None:
    if (side is OrderSide.BUY and stop >= reference) or (
        side is OrderSide.SELL and stop <= reference
    ):
        raise MutationBlocked(f"{name} is on the risk-increasing side of the position")


def _require_profit_direction(side: OrderSide, reference: Decimal, take_profit: Decimal) -> None:
    if (side is OrderSide.BUY and take_profit <= reference) or (
        side is OrderSide.SELL and take_profit >= reference
    ):
        raise MutationBlocked("takeProfit is not on the profitable side of the entry")


def _key_identity(key_info: ApiKeyInfo) -> ApiKeyIdentity:
    return ApiKeyIdentity(
        key_info.note,
        key_info.created_at,
        key_info.key_type,
        key_info.is_master,
        key_info.parent_uid,
        key_info.unified_account,
        key_info.key_id,
    )


def _require_fresh_state(
    name: str,
    observed_at: datetime,
    now: datetime,
    budget: timedelta,
) -> None:
    if observed_at.tzinfo is None:
        raise MutationBlocked(f"{name} safety timestamp is timezone-naive")
    age = now - observed_at.astimezone(UTC)
    future_tolerance = timedelta(seconds=2)
    if age < -future_tolerance:
        lead_seconds = abs(age.total_seconds())
        raise MutationBlocked(
            f"{name} safety state is from the future by {lead_seconds:.3f}s"
        )
    if age > budget:
        raise MutationBlocked(
            f"{name} safety state is stale: age={age.total_seconds():.3f}s "
            f"budget={budget.total_seconds():.3f}s"
        )


def _is_fresh(observed_at: datetime, now: datetime, budget: timedelta) -> bool:
    if observed_at.tzinfo is None:
        return False
    age = now - observed_at.astimezone(UTC)
    return -timedelta(seconds=2) <= age <= budget


def _normalized_endpoint(endpoint: str) -> str:
    return endpoint.rstrip("/")


def _aware_now(value: datetime | None) -> datetime:
    selected = _utc_now() if value is None else value
    if selected.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return selected.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)
