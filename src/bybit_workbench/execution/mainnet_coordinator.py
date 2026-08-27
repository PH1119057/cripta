from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain.models import Order, OrderRequest, Position
from bybit_workbench.domain.types import (
    AppMode,
    AppState,
    OrderRole,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from bybit_workbench.exchange.bybit.health import BybitHealthSnapshot
from bybit_workbench.exchange.bybit.models import BybitPositionSnapshot
from bybit_workbench.exchange.bybit.streams import BybitStreamSnapshot
from bybit_workbench.exchange.bybit.testnet_execution import (
    BybitOrderAcknowledgement,
    BybitWriteRejected,
    ExchangeProtectionPlan,
)
from bybit_workbench.persistence import TradingJournal

from .commands import (
    AmbiguousExecutionCommand,
    ExecutionCommandKind,
    ExecutionCommandRecord,
    ExecutionCommandStatus,
)
from .mainnet_safety import (
    MainnetMutation,
    MainnetMutationGateway,
    MutationBlocked,
    MutationKind,
)


class MainnetObservationPort(Protocol):
    async def position_snapshot(self, symbol: str) -> BybitPositionSnapshot: ...

    async def open_orders(self, symbol: str) -> list[Order]: ...

    async def order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
    ) -> Order | None: ...


class MainnetExecutionCoordinator:
    """Durable orchestration above the sole Mainnet mutation gateway.

    The coordinator owns command state and exchange confirmation.  It never
    receives a raw HTTP writer and therefore cannot bypass the gateway's fresh
    state, ticket, idempotency or payload checks.
    """

    def __init__(
        self,
        settings: AppSettings,
        gateway: MainnetMutationGateway,
        reader: MainnetObservationPort,
        journal: TradingJournal,
        state_machine: AppStateMachine,
        *,
        private_snapshot_provider: Callable[
            [], tuple[BybitStreamSnapshot, BybitHealthSnapshot] | None
        ]
        | None = None,
        confirmation_attempts: int = 10,
        confirmation_delay: float = 0.5,
    ) -> None:
        settings.validate_startup()
        if settings.mode is not AppMode.LIVE or not settings.allow_live_trading:
            raise PermissionError("Mainnet coordinator is externally locked")
        if confirmation_attempts < 1 or confirmation_delay < 0:
            raise ValueError("invalid Mainnet confirmation policy")
        self.settings = settings
        self.gateway = gateway
        self.reader = reader
        self.journal = journal
        self.state_machine = state_machine
        self.private_snapshot_provider = private_snapshot_provider
        self.confirmation_attempts = confirmation_attempts
        self.confirmation_delay = confirmation_delay
        self.last_observation_source = "REST"

    async def observe_order(self, symbol: str, client_order_id: str) -> Order | None:
        match = self._private_order(symbol, client_order_id)
        if match is not None:
            return match
        self.last_observation_source = "REST fallback"
        return await self.reader.order_by_client_id(symbol, client_order_id)

    def _private_order(self, symbol: str, client_order_id: str) -> Order | None:
        observation = self._fresh_private_snapshot()
        if observation is None:
            return None
        stream, _health = observation
        match = next(
            (
                order
                for order in stream.orders
                if order.request.symbol == symbol
                and order.request.client_order_id == client_order_id
            ),
            None,
        )
        if match is not None:
            self.last_observation_source = "Private WS"
        return match

    async def observe_position(self, symbol: str) -> BybitPositionSnapshot:
        observation = self._fresh_private_snapshot()
        if observation is not None:
            stream, _health = observation
            if stream.position is not None and stream.position.position.symbol == symbol:
                self.last_observation_source = "Private WS"
                return stream.position
        self.last_observation_source = "REST fallback"
        return await self.reader.position_snapshot(symbol)

    async def submit_entry(
        self,
        request: OrderRequest,
        protection: ExchangeProtectionPlan,
        *,
        intent_id: str | None = None,
    ) -> BybitOrderAcknowledgement:
        if self.state_machine.state is not AppState.RUNNING:
            raise PermissionError("engine must be RUNNING before a Mainnet entry")
        if request.role is not OrderRole.ENTRY or request.reduce_only:
            raise ValueError("Mainnet entry requires a non-reduce-only entry request")
        if request.order_type is OrderType.LIMIT and request.price is None:
            raise PermissionError("Limit Micro-Live entry requires an explicit price")
        if request.order_type not in {OrderType.LIMIT, OrderType.MARKET}:
            raise PermissionError("Micro-Live entry order type must be Limit or Market")
        command = self.journal.create_execution_command(
            f"mainnet-entry-{uuid.uuid4().hex}",
            ExecutionCommandKind.ENTRY,
            f"mainnet:entry:{request.client_order_id}",
            request.symbol,
            {"request": request, "protection": protection},
            intent_id=intent_id,
        )
        if command.status in {
            ExecutionCommandStatus.ACKNOWLEDGED,
            ExecutionCommandStatus.CONFIRMED,
        }:
            return BybitOrderAcknowledgement(
                command.exchange_order_id or "",
                request.client_order_id,
            )
        if command.status in {
            ExecutionCommandStatus.REQUESTED,
            ExecutionCommandStatus.AMBIGUOUS,
        }:
            return await self._recover_entry(command, request.client_order_id)
        if command.status is ExecutionCommandStatus.FAILED:
            raise MutationBlocked("the durable Mainnet entry command previously failed")
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.REQUESTED,
        )
        # P25: attach the hard server-side TP/SL to the *same* /v5/order/create
        # mutation as the entry.  Bybit V5 supports TP/SL on linear/inverse order
        # creation.  This removes the avoidable unprotected window between a fill and
        # the follow-up /v5/position/trading-stop call.  The follow-up path remains as
        # a verification/repair fallback if the exchange observation does not match.
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": request.symbol,
            "side": request.side.value,
            "orderType": request.order_type.value,
            "qty": str(request.quantity),
            "positionIdx": 0,
            "orderLinkId": request.client_order_id,
            "tpslMode": "Full",
            "stopLoss": str(protection.stop_loss),
            "slTriggerBy": "MarkPrice",
            "slOrderType": "Market",
        }
        if protection.take_profit is not None:
            params.update(
                takeProfit=str(protection.take_profit),
                tpTriggerBy="MarkPrice",
                tpOrderType="Market",
            )
        if request.order_type is OrderType.LIMIT:
            assert request.price is not None
            params["price"] = str(request.price)
            params["timeInForce"] = "GTC"
        # Market entry deliberately omits price, timeInForce and slippageTolerance*.
        # Bybit therefore applies its exchange-default IOC/slippage protection.
        try:
            response = await self.gateway.submit(
                MainnetMutation(
                    "/v5/order/create",
                    params,
                    MutationKind.ENTRY,
                    request.client_order_id,
                )
            )
            acknowledgement = _acknowledgement(
                response,
                "/v5/order/create",
                request.client_order_id,
            )
        except (MutationBlocked, BybitWriteRejected) as exc:
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.FAILED,
                error=_safe_error(exc),
            )
            raise
        except Exception as exc:
            rejection = _pybit_invalid_request_rejection(
                "/v5/order/create",
                exc,
            )
            if rejection is not None:
                self.journal.update_execution_command(
                    command.command_id,
                    ExecutionCommandStatus.FAILED,
                    error=_safe_error(rejection),
                )
                raise rejection from exc
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.AMBIGUOUS,
                error=_safe_write_error(exc),
            )
            return await self._recover_entry(
                self._required_command(command.command_id),
                request.client_order_id,
                write_error=exc,
            )
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.ACKNOWLEDGED,
            exchange_order_id=acknowledgement.order_id,
            response=response,
        )
        return acknowledgement

    def confirm_entry(self, order: Order) -> ExecutionCommandRecord:
        command = self.journal.execution_command(
            idempotency_key=f"mainnet:entry:{order.request.client_order_id}"
        )
        if command is None:
            raise LookupError("Mainnet entry command does not exist")
        if command.exchange_order_id and command.exchange_order_id != order.order_id:
            raise ValueError("confirmed order differs from Mainnet acknowledgement")
        if command.status is ExecutionCommandStatus.CONFIRMED:
            return command
        if command.status in {
            ExecutionCommandStatus.REQUESTED,
            ExecutionCommandStatus.AMBIGUOUS,
        }:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.ACKNOWLEDGED,
                exchange_order_id=order.order_id,
            )
        if command.status is not ExecutionCommandStatus.ACKNOWLEDGED:
            raise ValueError(f"entry cannot be confirmed from {command.status}")
        self.journal.upsert_order(
            order,
            intent_id=command.intent_id,
            event_id=(
                f"mainnet-confirm-entry:{order.order_id}:{order.updated_at.isoformat()}:"
                f"{order.status.value}:{order.filled_quantity}"
            ),
            raw_payload={"source": self.last_observation_source},
        )
        return self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.CONFIRMED,
            exchange_order_id=order.order_id,
        )

    async def wait_for_entry_confirmation(
        self,
        symbol: str,
        client_order_id: str,
    ) -> Order:
        for attempt in range(self.confirmation_attempts):
            order = await self.observe_order(symbol, client_order_id)
            if order is not None:
                self.confirm_entry(order)
                return order
            if attempt + 1 < self.confirmation_attempts:
                await asyncio.sleep(self.confirmation_delay)
        raise AmbiguousExecutionCommand(
            "Mainnet entry acknowledgement was not confirmed by Private WS or REST"
        )

    async def cancel_order(
        self,
        order: Order,
        *,
        entries_only: bool,
    ) -> ExecutionCommandRecord:
        if order.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise ValueError("only an active order can be cancelled")
        if order.request.role is OrderRole.PROTECTIVE:
            raise ValueError("protective orders cannot be cancelled by maintenance")
        if entries_only and order.request.role is not OrderRole.ENTRY:
            raise ValueError("entry-only cancellation received a non-entry order")
        kind = (
            ExecutionCommandKind.CANCEL_ENTRY
            if entries_only
            else ExecutionCommandKind.CANCEL_ORDER
        )
        journal_key = f"mainnet:cancel:{order.order_id}"
        command = self.journal.create_execution_command(
            f"mainnet-cancel-{uuid.uuid4().hex}",
            kind,
            journal_key,
            order.request.symbol,
            {"order_id": order.order_id, "client_order_id": order.request.client_order_id},
        )
        if command.status in {
            ExecutionCommandStatus.ACKNOWLEDGED,
            ExecutionCommandStatus.CONFIRMED,
        }:
            return command
        if command.status in {
            ExecutionCommandStatus.REQUESTED,
            ExecutionCommandStatus.AMBIGUOUS,
        }:
            observed = await self.observe_order(
                order.request.symbol,
                order.request.client_order_id,
            )
            if observed is not None and observed.status is OrderStatus.CANCELLED:
                return self._confirm_cancel_command(command, observed)
            raise AmbiguousExecutionCommand(
                "Mainnet cancellation outcome is ambiguous; blind retry is blocked"
            )
        if command.status is ExecutionCommandStatus.FAILED:
            raise MutationBlocked("the durable cancellation command previously failed")
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.REQUESTED,
        )
        mutation_key = _mutation_key("mw-c", order.order_id)
        try:
            response = await self.gateway.submit(
                MainnetMutation(
                    "/v5/order/cancel",
                    {
                        "category": "linear",
                        "symbol": order.request.symbol,
                        "orderId": order.order_id,
                    },
                    MutationKind.CANCEL,
                    mutation_key,
                )
            )
            acknowledgement = _acknowledgement(
                response,
                "/v5/order/cancel",
                order.request.client_order_id,
            )
        except (MutationBlocked, BybitWriteRejected) as exc:
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.FAILED,
                error=_safe_error(exc),
            )
            raise
        except Exception as exc:
            rejection = _pybit_invalid_request_rejection(
                "/v5/order/cancel",
                exc,
            )
            if rejection is not None:
                self.journal.update_execution_command(
                    command.command_id,
                    ExecutionCommandStatus.FAILED,
                    error=_safe_error(rejection),
                )
                raise rejection from exc
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.AMBIGUOUS,
                error=_safe_write_error(exc),
            )
            raise AmbiguousExecutionCommand(
                "Mainnet cancellation may have reached Bybit; reconciliation is mandatory"
            ) from exc
        return self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.ACKNOWLEDGED,
            exchange_order_id=acknowledgement.order_id or order.order_id,
            response=response,
        )

    def confirm_cancel(self, order: Order) -> ExecutionCommandRecord:
        command = self.journal.execution_command(
            idempotency_key=f"mainnet:cancel:{order.order_id}"
        )
        if command is None:
            raise LookupError("Mainnet cancellation command does not exist")
        return self._confirm_cancel_command(command, order)

    async def set_protection(
        self,
        position: BybitPositionSnapshot,
        protection: ExchangeProtectionPlan,
        *,
        intent_id: str | None = None,
    ) -> ExecutionCommandRecord:
        if position.position.side is PositionSide.FLAT:
            raise ValueError("cannot protect a flat position")
        material = ":".join(
            (
                intent_id or "-",
                position.position.symbol,
                str(position.position.quantity),
                str(protection.stop_loss),
                str(protection.take_profit or "-"),
                str(protection.trailing_distance or "-"),
                str(protection.trailing_active_price or "-"),
            )
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        command = self.journal.create_execution_command(
            f"mainnet-protection-{uuid.uuid4().hex}",
            ExecutionCommandKind.SET_PROTECTION,
            f"mainnet:protection:{digest}",
            position.position.symbol,
            {"position": position.position, "protection": protection},
            intent_id=intent_id,
        )
        if command.status is ExecutionCommandStatus.CONFIRMED:
            return command
        if command.status in {
            ExecutionCommandStatus.REQUESTED,
            ExecutionCommandStatus.AMBIGUOUS,
            ExecutionCommandStatus.ACKNOWLEDGED,
        }:
            observed = await self.observe_position(position.position.symbol)
            if _protection_matches(observed, protection):
                return self._confirm_protection(command)
            raise AmbiguousExecutionCommand(
                "existing Mainnet protection command is not confirmed; blind retry is blocked"
            )
        if command.status is ExecutionCommandStatus.FAILED:
            raise MutationBlocked("the durable protection command previously failed")
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.REQUESTED,
        )
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": position.position.symbol,
            "positionIdx": 0,
            "tpslMode": "Full",
            "stopLoss": str(protection.stop_loss),
            "slTriggerBy": "MarkPrice",
            "slOrderType": "Market",
        }
        if protection.take_profit is not None:
            params.update(
                takeProfit=str(protection.take_profit),
                tpTriggerBy="MarkPrice",
                tpOrderType="Market",
            )
        if protection.trailing_distance is not None:
            params["trailingStop"] = str(protection.trailing_distance)
        if protection.trailing_active_price is not None:
            params["activePrice"] = str(protection.trailing_active_price)
        try:
            response = await self.gateway.submit(
                MainnetMutation(
                    "/v5/position/trading-stop",
                    params,
                    MutationKind.PROTECTION,
                    _mutation_key("mw-p", digest),
                )
            )
            _require_success(response, "/v5/position/trading-stop")
        except (MutationBlocked, BybitWriteRejected) as exc:
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.FAILED,
                error=_safe_error(exc),
            )
            raise
        except Exception as exc:
            rejection = _pybit_invalid_request_rejection(
                "/v5/position/trading-stop",
                exc,
            )
            if rejection is not None:
                self.journal.update_execution_command(
                    command.command_id,
                    ExecutionCommandStatus.FAILED,
                    error=_safe_error(rejection),
                )
                raise rejection from exc
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.AMBIGUOUS,
                error=_safe_write_error(exc),
            )
            for attempt in range(self.confirmation_attempts):
                observed = await self.observe_position(position.position.symbol)
                if _protection_matches(observed, protection):
                    return self._confirm_protection(command)
                if attempt + 1 < self.confirmation_attempts:
                    await asyncio.sleep(self.confirmation_delay)
            raise AmbiguousExecutionCommand(
                "Mainnet protection outcome is ambiguous; position verification is mandatory"
            ) from exc
        command = self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.ACKNOWLEDGED,
            response=response,
        )
        for attempt in range(self.confirmation_attempts):
            observed = await self.observe_position(position.position.symbol)
            if _protection_matches(observed, protection):
                return self._confirm_protection(command)
            if attempt + 1 < self.confirmation_attempts:
                await asyncio.sleep(self.confirmation_delay)
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.AMBIGUOUS,
            error="server-side protection was acknowledged but not confirmed",
        )
        raise AmbiguousExecutionCommand(
            "server-side protection is not confirmed; activate kill switch"
        )

    async def close_position(
        self,
        position: Position,
        *,
        intent_id: str | None = None,
    ) -> BybitOrderAcknowledgement | None:
        if position.side is PositionSide.FLAT or position.quantity == 0:
            return None
        side = OrderSide.SELL if position.side is PositionSide.LONG else OrderSide.BUY
        material = ":".join(
            (intent_id or "-", position.symbol, position.side.value, str(position.quantity))
        )
        client_id = _mutation_key("mw-x", material)
        command = self.journal.create_execution_command(
            f"mainnet-close-{uuid.uuid4().hex}",
            ExecutionCommandKind.EMERGENCY_CLOSE,
            f"mainnet:close:{material}",
            position.symbol,
            {"position": position, "client_order_id": client_id},
            intent_id=intent_id,
        )
        if command.status in {
            ExecutionCommandStatus.ACKNOWLEDGED,
            ExecutionCommandStatus.CONFIRMED,
        }:
            return BybitOrderAcknowledgement(command.exchange_order_id or "", client_id)
        if command.status in {
            ExecutionCommandStatus.REQUESTED,
            ExecutionCommandStatus.AMBIGUOUS,
        }:
            return await self._recover_close(command, client_id)
        if command.status is ExecutionCommandStatus.FAILED:
            raise MutationBlocked("the durable reduce-only close previously failed")
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.REQUESTED,
        )
        params = {
            "category": "linear",
            "symbol": position.symbol,
            "side": side.value,
            "orderType": OrderType.MARKET.value,
            "qty": str(position.quantity),
            "positionIdx": 0,
            "orderLinkId": client_id,
            "reduceOnly": True,
            "closeOnTrigger": False,
        }
        try:
            response = await self.gateway.submit(
                MainnetMutation(
                    "/v5/order/create",
                    params,
                    MutationKind.REDUCE_ONLY,
                    client_id,
                )
            )
            acknowledgement = _acknowledgement(
                response,
                "/v5/order/create",
                client_id,
            )
        except (MutationBlocked, BybitWriteRejected) as exc:
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.FAILED,
                error=_safe_error(exc),
            )
            raise
        except Exception as exc:
            rejection = _pybit_invalid_request_rejection(
                "/v5/order/create",
                exc,
            )
            if rejection is not None:
                self.journal.update_execution_command(
                    command.command_id,
                    ExecutionCommandStatus.FAILED,
                    error=_safe_error(rejection),
                )
                raise rejection from exc
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.AMBIGUOUS,
                error=_safe_write_error(exc),
            )
            return await self._recover_close(
                self._required_command(command.command_id),
                client_id,
            )
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.ACKNOWLEDGED,
            exchange_order_id=acknowledgement.order_id,
            response=response,
        )
        return acknowledgement

    def confirm_flat(
        self,
        command_id: str,
        position: Position,
    ) -> ExecutionCommandRecord:
        if position.side is not PositionSide.FLAT or position.quantity != 0:
            raise ValueError("reduce-only close is not confirmed flat")
        command = self._required_command(command_id)
        if command.kind not in {
            ExecutionCommandKind.EMERGENCY_CLOSE,
            ExecutionCommandKind.STRATEGY_EXIT,
        }:
            raise ValueError("command is not a position close")
        if command.status is ExecutionCommandStatus.CONFIRMED:
            return command
        if command.status is not ExecutionCommandStatus.ACKNOWLEDGED:
            raise ValueError(f"close cannot be confirmed from {command.status}")
        return self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.CONFIRMED,
        )

    async def cancel_for_symbol(
        self,
        symbol: str,
        *,
        entries_only: bool,
    ) -> tuple[ExecutionCommandRecord, ...]:
        orders = await self.reader.open_orders(symbol)
        targets = (
            order
            for order in orders
            if order.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}
            and order.request.role is not OrderRole.PROTECTIVE
            and (not entries_only or order.request.role is OrderRole.ENTRY)
        )
        results: list[ExecutionCommandRecord] = []
        for order in targets:
            results.append(await self.cancel_order(order, entries_only=entries_only))
        return tuple(results)

    def _confirm_cancel_command(
        self,
        command: ExecutionCommandRecord,
        order: Order,
    ) -> ExecutionCommandRecord:
        if order.status is not OrderStatus.CANCELLED:
            raise ValueError("exchange has not confirmed cancellation")
        if command.status is ExecutionCommandStatus.CONFIRMED:
            return command
        if command.status in {
            ExecutionCommandStatus.REQUESTED,
            ExecutionCommandStatus.AMBIGUOUS,
        }:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.ACKNOWLEDGED,
                exchange_order_id=order.order_id,
            )
        if command.status is not ExecutionCommandStatus.ACKNOWLEDGED:
            raise ValueError(f"cancel cannot be confirmed from {command.status}")
        self.journal.upsert_order(
            order,
            event_id=(
                f"mainnet-confirm-cancel:{order.order_id}:{order.updated_at.isoformat()}:"
                f"{order.filled_quantity}"
            ),
            raw_payload={"source": self.last_observation_source},
        )
        return self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.CONFIRMED,
            exchange_order_id=order.order_id,
        )

    def _confirm_protection(
        self,
        command: ExecutionCommandRecord,
    ) -> ExecutionCommandRecord:
        if command.status is ExecutionCommandStatus.CONFIRMED:
            return command
        if command.status in {
            ExecutionCommandStatus.REQUESTED,
            ExecutionCommandStatus.AMBIGUOUS,
        }:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.ACKNOWLEDGED,
            )
        if command.status is not ExecutionCommandStatus.ACKNOWLEDGED:
            raise ValueError(f"protection cannot be confirmed from {command.status}")
        return self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.CONFIRMED,
        )

    async def _recover_entry(
        self,
        command: ExecutionCommandRecord,
        client_order_id: str,
        *,
        write_error: Exception | None = None,
    ) -> BybitOrderAcknowledgement:
        # A lost/malformed POST response is ambiguous, never a reason to resend.
        # Give the private order stream a short grace window first; Bybit normally
        # publishes the order event before REST reconciliation is needed.  Then use
        # the dedicated resilient GET session for two orderLinkId lookups.
        private_grace = (
            1 if self.confirmation_attempts <= 2 else min(4, self.confirmation_attempts - 1)
        )
        last_lookup_error: Exception | None = None
        successful_empty_lookups = 0
        rest_attempts = 0

        for attempt in range(self.confirmation_attempts):
            order = self._private_order(command.symbol, client_order_id)
            if order is not None:
                confirmed = self.confirm_entry(order)
                return BybitOrderAcknowledgement(
                    confirmed.exchange_order_id or order.order_id,
                    client_order_id,
                )

            should_query_rest = attempt + 1 == private_grace or (
                attempt + 1 == self.confirmation_attempts
                and self.confirmation_attempts > private_grace
            )
            if should_query_rest:
                rest_attempts += 1
                self.last_observation_source = "REST reconciliation"
                try:
                    order = await self.reader.order_by_client_id(
                        command.symbol,
                        client_order_id,
                    )
                except Exception as exc:
                    last_lookup_error = exc
                else:
                    if order is not None:
                        confirmed = self.confirm_entry(order)
                        return BybitOrderAcknowledgement(
                            confirmed.exchange_order_id or order.order_id,
                            client_order_id,
                        )
                    successful_empty_lookups += 1

            if attempt + 1 < self.confirmation_attempts:
                await asyncio.sleep(self.confirmation_delay)

        if command.status is ExecutionCommandStatus.REQUESTED:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.AMBIGUOUS,
                error="entry was requested but orderLinkId is not confirmed",
            )

        write_detail = (
            _safe_write_error(write_error)
            if write_error is not None
            else command.error
        )
        write_suffix = f", write_error={write_detail}" if write_detail else ""
        if last_lookup_error is not None:
            detail = _safe_error(last_lookup_error)
            raise AmbiguousExecutionCommand(
                "Mainnet entry remains unresolved after automatic reconciliation: "
                f"REST attempts={rest_attempts}, empty={successful_empty_lookups}"
                f"{write_suffix}, last_error={detail}; blind retry is blocked"
            ) from last_lookup_error
        raise AmbiguousExecutionCommand(
            "Mainnet entry remains unresolved after automatic reconciliation: "
            f"REST attempts={rest_attempts}, empty={successful_empty_lookups}"
            f"{write_suffix}; blind retry is blocked"
        )

    async def _recover_close(
        self,
        command: ExecutionCommandRecord,
        client_order_id: str,
    ) -> BybitOrderAcknowledgement:
        try:
            position = await self.observe_position(command.symbol)
            if (
                position.position.side is PositionSide.FLAT
                and position.position.quantity == 0
            ):
                if command.status is not ExecutionCommandStatus.ACKNOWLEDGED:
                    command = self.journal.update_execution_command(
                        command.command_id,
                        ExecutionCommandStatus.ACKNOWLEDGED,
                    )
                confirmed = self.confirm_flat(command.command_id, position.position)
                return BybitOrderAcknowledgement(
                    confirmed.exchange_order_id or "",
                    client_order_id,
                )
            order = await self.observe_order(command.symbol, client_order_id)
        except Exception as exc:
            raise AmbiguousExecutionCommand(
                "reduce-only close may have reached Bybit and reconciliation failed"
            ) from exc
        if order is None:
            if command.status is ExecutionCommandStatus.REQUESTED:
                self.journal.update_execution_command(
                    command.command_id,
                    ExecutionCommandStatus.AMBIGUOUS,
                    error="close was requested but orderLinkId is not visible",
                )
            raise AmbiguousExecutionCommand(
                "reduce-only close outcome is ambiguous; blind retry is blocked"
            )
        if command.status is not ExecutionCommandStatus.ACKNOWLEDGED:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.ACKNOWLEDGED,
                exchange_order_id=order.order_id,
            )
        return BybitOrderAcknowledgement(
            command.exchange_order_id or order.order_id,
            client_order_id,
        )

    def _required_command(self, command_id: str) -> ExecutionCommandRecord:
        command = self.journal.execution_command(command_id=command_id)
        if command is None:
            raise RuntimeError("Mainnet execution command disappeared from the journal")
        return command

    def _fresh_private_snapshot(
        self,
    ) -> tuple[BybitStreamSnapshot, BybitHealthSnapshot] | None:
        if self.private_snapshot_provider is None:
            return None
        observation = self.private_snapshot_provider()
        if observation is None or not observation[1].private.fresh:
            return None
        return observation


def _acknowledgement(
    response: Mapping[str, Any],
    endpoint: str,
    client_order_id: str,
) -> BybitOrderAcknowledgement:
    result = _require_success(response, endpoint)
    order_id = str(result.get("orderId") or "")
    if not order_id:
        raise ValueError(f"Bybit {endpoint} response has no orderId")
    returned_link = str(result.get("orderLinkId") or client_order_id)
    if client_order_id and returned_link and returned_link != client_order_id:
        raise ValueError("Bybit acknowledgement returned a mismatched orderLinkId")
    return BybitOrderAcknowledgement(order_id, client_order_id)


def _require_success(
    response: Mapping[str, Any],
    endpoint: str,
) -> Mapping[str, Any]:
    if response.get("retCode") != 0:
        raise BybitWriteRejected(endpoint, response.get("retCode"), response.get("retMsg"))
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise ValueError(f"Bybit {endpoint} response.result must be an object")
    return result


def _mutation_key(prefix: str, material: str) -> str:
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _protection_matches(
    snapshot: BybitPositionSnapshot,
    expected: ExchangeProtectionPlan,
) -> bool:
    if snapshot.stop_loss != expected.stop_loss:
        return False
    if expected.take_profit is not None and snapshot.take_profit != expected.take_profit:
        return False
    return not (
        expected.trailing_distance is not None
        and snapshot.trailing_stop_distance != expected.trailing_distance
    )


def _pybit_invalid_request_rejection(
    endpoint: str,
    error: Exception,
) -> BybitWriteRejected | None:
    """Translate a pybit business retCode into a definite exchange rejection.

    pybit raises ``InvalidRequestError`` after it has received and decoded a Bybit
    response with a non-zero business retCode.  That is different from a network
    timeout, where the mutation outcome is genuinely ambiguous.  Keep the pybit
    dependency at the adapter boundary by detecting its public exception shape
    instead of importing the optional package here.
    """

    error_type = error.__class__
    if error_type.__name__ != "InvalidRequestError":
        return None
    if not error_type.__module__.startswith("pybit"):
        return None
    ret_code = getattr(error, "status_code", None)
    message = getattr(error, "message", None)
    if not isinstance(message, str) or not message.strip():
        message = _single_line_error(error)
    return BybitWriteRejected(endpoint, ret_code, message)


def _single_line_error(error: Exception) -> str:
    text = str(error).strip() or error.__class__.__name__
    return " ".join(text.splitlines()).strip()


def _safe_write_error(error: Exception) -> str:
    """Compact transport diagnostic without serialising requests or credentials."""

    message = getattr(error, "message", None)
    if isinstance(message, str) and message.strip():
        detail = " ".join(message.splitlines()).strip()
    else:
        detail = _single_line_error(error).split(" Request →", 1)[0].strip()
    if len(detail) > 240:
        detail = detail[:237] + "..."
    return f"{error.__class__.__name__}: {detail}"


def _safe_error(error: Exception) -> str:
    return str(error).strip() or error.__class__.__name__
