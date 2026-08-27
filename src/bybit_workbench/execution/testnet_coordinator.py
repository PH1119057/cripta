from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.state_machine import AppStateMachine, InvalidStateTransition
from bybit_workbench.domain.models import Order, OrderRequest, Position
from bybit_workbench.domain.types import AppState, OrderRole, OrderStatus, PositionSide
from bybit_workbench.exchange.bybit.health import BybitHealthSnapshot
from bybit_workbench.exchange.bybit.models import BybitPositionSnapshot
from bybit_workbench.exchange.bybit.streams import BybitStreamSnapshot
from bybit_workbench.exchange.bybit.testnet_execution import (
    BybitOrderAcknowledgement,
    BybitWriteRejected,
    ExchangeProtectionPlan,
)
from bybit_workbench.persistence import TradingJournal
from bybit_workbench.stops import validate_stop_update

from .commands import (
    AmbiguousExecutionCommand,
    ExecutionCommandKind,
    ExecutionCommandRecord,
    ExecutionCommandStatus,
    ProtectionConfirmationError,
)


class TestnetExecutionPort(Protocol):
    async def place_entry(
        self,
        request: OrderRequest,
        protection: ExchangeProtectionPlan,
    ) -> BybitOrderAcknowledgement: ...

    async def set_full_protection(
        self,
        symbol: str,
        protection: ExchangeProtectionPlan,
    ) -> None: ...

    async def cancel_entry(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> BybitOrderAcknowledgement: ...

    async def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> BybitOrderAcknowledgement: ...

    async def emergency_close(
        self,
        position: Position,
        client_order_id: str,
    ) -> BybitOrderAcknowledgement | None: ...

    async def position(self, symbol: str) -> BybitPositionSnapshot: ...

    async def order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
    ) -> Order | None: ...


@dataclass(frozen=True, slots=True)
class ProtectionOutcome:
    protection_command: ExecutionCommandRecord
    emergency_command: ExecutionCommandRecord | None = None


class TestnetExecutionCoordinator:
    """Durable, fail-closed orchestration for the first Testnet write slice."""

    def __init__(
        self,
        settings: AppSettings,
        adapter: TestnetExecutionPort,
        journal: TradingJournal,
        state_machine: AppStateMachine,
        *,
        protection_confirmation_attempts: int = 10,
        protection_confirmation_delay: float = 0.5,
        private_snapshot_provider: Callable[
            [], tuple[BybitStreamSnapshot, BybitHealthSnapshot] | None
        ]
        | None = None,
    ) -> None:
        settings.validate_startup()
        if not settings.testnet_execution_allowed:
            raise PermissionError("Testnet execution is not enabled")
        if protection_confirmation_attempts < 1 or protection_confirmation_delay < 0:
            raise ValueError("invalid protection confirmation policy")
        self.settings = settings
        self.adapter = adapter
        self.journal = journal
        self.state_machine = state_machine
        self.protection_confirmation_attempts = protection_confirmation_attempts
        self.protection_confirmation_delay = protection_confirmation_delay
        self.private_snapshot_provider = private_snapshot_provider
        self.last_observation_source = "REST"

    async def observe_order(self, symbol: str, client_order_id: str) -> Order | None:
        observation = self._fresh_private_snapshot()
        if observation is not None:
            snapshot, _ = observation
            match = next(
                (
                    order
                    for order in snapshot.orders
                    if order.request.symbol == symbol
                    and order.request.client_order_id == client_order_id
                ),
                None,
            )
            if match is not None:
                self.last_observation_source = "Private WS"
                return match
        self.last_observation_source = "REST fallback"
        return await self.adapter.order_by_client_id(symbol, client_order_id)

    async def observe_position(self, symbol: str) -> BybitPositionSnapshot:
        observation = self._fresh_private_snapshot()
        if observation is not None:
            snapshot, _ = observation
            if snapshot.position is not None and snapshot.position.position.symbol == symbol:
                self.last_observation_source = "Private WS"
                return snapshot.position
        self.last_observation_source = "REST fallback"
        return await self.adapter.position(symbol)

    async def submit_entry(
        self,
        request: OrderRequest,
        protection: ExchangeProtectionPlan,
        health: BybitHealthSnapshot,
        *,
        intent_id: str | None = None,
    ) -> BybitOrderAcknowledgement:
        self._require_entry_safety(health)
        command = self.journal.create_execution_command(
            f"entry-{uuid.uuid4().hex}",
            ExecutionCommandKind.ENTRY,
            f"entry:{request.client_order_id}",
            request.symbol,
            {"request": request, "protection": protection},
            intent_id=intent_id,
        )
        if command.status is not ExecutionCommandStatus.PLANNED:
            return await self._recover_entry(command, request.client_order_id)
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.REQUESTED,
        )
        try:
            acknowledgement = await self.adapter.place_entry(request, protection)
        except BybitWriteRejected as exc:
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.FAILED,
                error=str(exc),
            )
            raise
        except Exception as exc:
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.AMBIGUOUS,
                error=_safe_error(exc),
            )
            return await self._recover_entry(
                self._required_command(command.command_id),
                request.client_order_id,
            )
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.ACKNOWLEDGED,
            exchange_order_id=acknowledgement.order_id,
            response={
                "order_id": acknowledgement.order_id,
                "client_order_id": acknowledgement.client_order_id,
            },
        )
        return acknowledgement

    def confirm_entry(self, order: Order) -> ExecutionCommandRecord:
        command = self.journal.execution_command(
            idempotency_key=f"entry:{order.request.client_order_id}"
        )
        if command is None:
            raise LookupError("entry command does not exist")
        self.journal.upsert_order(
            order,
            intent_id=command.intent_id,
            event_id=(
                f"confirm-entry:{order.order_id}:{order.updated_at.isoformat()}:"
                f"{order.status.value}:{order.filled_quantity}"
            ),
            raw_payload={"source": self.last_observation_source},
        )
        if command.status is ExecutionCommandStatus.CONFIRMED:
            return command
        if command.status not in {
            ExecutionCommandStatus.ACKNOWLEDGED,
            ExecutionCommandStatus.AMBIGUOUS,
            ExecutionCommandStatus.REQUESTED,
        }:
            raise ValueError(f"entry cannot be confirmed from {command.status}")
        if command.exchange_order_id and command.exchange_order_id != order.order_id:
            raise ValueError("confirmed order id differs from REST acknowledgement")
        if command.status in {
            ExecutionCommandStatus.AMBIGUOUS,
            ExecutionCommandStatus.REQUESTED,
        }:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.ACKNOWLEDGED,
                exchange_order_id=order.order_id,
            )
        result = self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.CONFIRMED,
            exchange_order_id=order.order_id,
        )
        return result

    async def cancel_entry(self, order: Order) -> ExecutionCommandRecord:
        if order.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise ValueError("only an active or partially filled entry can be cancelled")
        if order.request.role is not OrderRole.ENTRY:
            raise ValueError("cancel_entry requires an entry order")
        return await self._cancel_order(order, ExecutionCommandKind.CANCEL_ENTRY)

    async def cancel_non_protective(self, order: Order) -> ExecutionCommandRecord:
        if order.request.role is OrderRole.PROTECTIVE:
            raise ValueError("protective orders cannot be cancelled by this operation")
        if order.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            raise ValueError("only an active order can be cancelled")
        return await self._cancel_order(order, ExecutionCommandKind.CANCEL_ORDER)

    async def _cancel_order(
        self,
        order: Order,
        kind: ExecutionCommandKind,
    ) -> ExecutionCommandRecord:
        key = f"cancel:{order.order_id}"
        command = self.journal.create_execution_command(
            f"cancel-{uuid.uuid4().hex}",
            kind,
            key,
            order.request.symbol,
            {"order_id": order.order_id, "client_order_id": order.request.client_order_id},
        )
        if command.status in {
            ExecutionCommandStatus.ACKNOWLEDGED,
            ExecutionCommandStatus.CONFIRMED,
        }:
            return command
        if command.status is not ExecutionCommandStatus.PLANNED:
            raise AmbiguousExecutionCommand(
                f"cancel command already exists in state {command.status}"
            )
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.REQUESTED,
        )
        try:
            if kind is ExecutionCommandKind.CANCEL_ENTRY:
                acknowledgement = await self.adapter.cancel_entry(
                    symbol=order.request.symbol,
                    order_id=order.order_id,
                )
            else:
                acknowledgement = await self.adapter.cancel_order(
                    symbol=order.request.symbol,
                    order_id=order.order_id,
                )
        except BybitWriteRejected as exc:
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.FAILED,
                error=str(exc),
            )
            raise
        except Exception as exc:
            self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.AMBIGUOUS,
                error=_safe_error(exc),
            )
            raise AmbiguousExecutionCommand(
                "entry cancellation outcome is ambiguous; reconciliation is mandatory"
            ) from exc
        return self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.ACKNOWLEDGED,
            exchange_order_id=acknowledgement.order_id,
        )

    def confirm_cancel(self, order: Order) -> ExecutionCommandRecord:
        if order.status is not OrderStatus.CANCELLED:
            raise ValueError("exchange order has not confirmed cancellation")
        command = self.journal.execution_command(idempotency_key=f"cancel:{order.order_id}")
        if command is None:
            raise LookupError("cancel command does not exist")
        if command.status is ExecutionCommandStatus.CONFIRMED:
            return command
        if command.status is not ExecutionCommandStatus.ACKNOWLEDGED:
            raise ValueError(f"cancel cannot be confirmed from {command.status}")
        result = self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.CONFIRMED,
            exchange_order_id=order.order_id,
        )
        self.journal.upsert_order(
            order,
            event_id=(
                f"confirm-cancel:{order.order_id}:{order.updated_at.isoformat()}:"
                f"{order.status.value}:{order.filled_quantity}"
            ),
            raw_payload={"source": self.last_observation_source},
        )
        return result

    async def ensure_protection(
        self,
        position: BybitPositionSnapshot,
        protection: ExchangeProtectionPlan,
        *,
        intent_id: str | None = None,
    ) -> ProtectionOutcome:
        return await self._ensure_protection(
            position,
            protection,
            ExecutionCommandKind.SET_PROTECTION,
            intent_id=intent_id,
        )

    async def move_stop(
        self,
        position: BybitPositionSnapshot,
        protection: ExchangeProtectionPlan,
        *,
        intent_id: str | None = None,
    ) -> ProtectionOutcome:
        if position.stop_loss is None:
            raise ValueError("a confirmed current stop is required before moving it")
        validate_stop_update(
            position.stop_loss,
            protection.stop_loss,
            position.position.side,
        )
        return await self._ensure_protection(
            position,
            protection,
            ExecutionCommandKind.MOVE_STOP,
            intent_id=intent_id,
        )

    async def _ensure_protection(
        self,
        position: BybitPositionSnapshot,
        protection: ExchangeProtectionPlan,
        kind: ExecutionCommandKind,
        *,
        intent_id: str | None,
    ) -> ProtectionOutcome:
        self._validate_position_and_stop(position, protection)
        stable_key = _protection_key(
            kind,
            intent_id,
            position.position.symbol,
            position.position.quantity,
            protection,
        )
        command = self.journal.create_execution_command(
            f"protection-{uuid.uuid4().hex}",
            kind,
            stable_key,
            position.position.symbol,
            {"protection": protection, "quantity": position.position.quantity},
            intent_id=intent_id,
        )
        if command.status is ExecutionCommandStatus.CONFIRMED:
            return ProtectionOutcome(command)
        if command.status in {
            ExecutionCommandStatus.REQUESTED,
            ExecutionCommandStatus.AMBIGUOUS,
            ExecutionCommandStatus.ACKNOWLEDGED,
        }:
            try:
                if await self._wait_for_protection(position.position.symbol, protection):
                    if command.status is not ExecutionCommandStatus.ACKNOWLEDGED:
                        command = self.journal.update_execution_command(
                            command.command_id,
                            ExecutionCommandStatus.ACKNOWLEDGED,
                        )
                    command = self.journal.update_execution_command(
                        command.command_id,
                        ExecutionCommandStatus.CONFIRMED,
                    )
                    return ProtectionOutcome(command)
            except Exception:
                pass
            emergency = await self.emergency_close(position.position, intent_id=intent_id)
            raise ProtectionConfirmationError(
                "existing protection command could not be confirmed; emergency reduce-only "
                f"close was requested as command {emergency.command_id}"
            )
        if command.status is ExecutionCommandStatus.FAILED:
            emergency = await self.emergency_close(position.position, intent_id=intent_id)
            raise ProtectionConfirmationError(
                "protection previously failed; emergency reduce-only close was requested "
                f"as command {emergency.command_id}"
            )
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.REQUESTED,
        )
        try:
            await self.adapter.set_full_protection(position.position.symbol, protection)
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.ACKNOWLEDGED,
            )
            confirmed = await self._wait_for_protection(position.position.symbol, protection)
            if confirmed:
                command = self.journal.update_execution_command(
                    command.command_id,
                    ExecutionCommandStatus.CONFIRMED,
                )
                return ProtectionOutcome(command)
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.FAILED,
                error="Bybit position did not confirm requested protection",
            )
        except BybitWriteRejected as exc:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.FAILED,
                error=str(exc),
            )
        except Exception as exc:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.AMBIGUOUS,
                error=_safe_error(exc),
            )
            try:
                if await self._wait_for_protection(position.position.symbol, protection):
                    command = self.journal.update_execution_command(
                        command.command_id,
                        ExecutionCommandStatus.ACKNOWLEDGED,
                    )
                    command = self.journal.update_execution_command(
                        command.command_id,
                        ExecutionCommandStatus.CONFIRMED,
                    )
                    return ProtectionOutcome(command)
            except Exception:
                pass
        emergency = await self.emergency_close(position.position, intent_id=intent_id)
        raise ProtectionConfirmationError(
            "Bybit did not confirm the hard stop; emergency reduce-only close was requested "
            f"as command {emergency.command_id}"
        )

    async def emergency_close(
        self,
        position: Position,
        *,
        intent_id: str | None = None,
    ) -> ExecutionCommandRecord:
        self._enter_emergency_state()
        key_material = f"{intent_id or '-'}:{position.symbol}:{position.side}:{position.quantity}"
        digest = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:20]
        client_order_id = f"bw-emergency-{digest}"
        command = self.journal.create_execution_command(
            f"emergency-{uuid.uuid4().hex}",
            ExecutionCommandKind.EMERGENCY_CLOSE,
            f"emergency:{key_material}",
            position.symbol,
            {"position": position, "client_order_id": client_order_id},
            intent_id=intent_id,
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
            return await self._recover_emergency(command, client_order_id)
        if command.status is ExecutionCommandStatus.FAILED:
            raise RuntimeError("emergency close previously failed; operator intervention required")
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.REQUESTED,
        )
        try:
            acknowledgement = await self.adapter.emergency_close(
                position,
                client_order_id,
            )
        except Exception as exc:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.AMBIGUOUS,
                error=_safe_error(exc),
            )
            try:
                return await self._recover_emergency(command, client_order_id)
            except AmbiguousExecutionCommand:
                raise
        command = self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.ACKNOWLEDGED,
            exchange_order_id=None if acknowledgement is None else acknowledgement.order_id,
        )
        if acknowledgement is None:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.CONFIRMED,
            )
        return command

    async def close_position(
        self,
        position: Position,
        *,
        intent_id: str,
    ) -> ExecutionCommandRecord:
        """Submit an idempotent reduce-only strategy exit without entering emergency state."""
        if position.side is PositionSide.FLAT or position.quantity == 0:
            raise ValueError("strategy exit requires an open position")
        key_material = f"{intent_id}:{position.symbol}:{position.side}:{position.quantity}"
        digest = hashlib.sha256(key_material.encode()).hexdigest()[:20]
        client_order_id = f"bw-exit-{digest}"
        command = self.journal.create_execution_command(
            f"strategy-exit-{uuid.uuid4().hex}",
            ExecutionCommandKind.STRATEGY_EXIT,
            f"strategy-exit:{key_material}",
            position.symbol,
            {"position": position, "client_order_id": client_order_id},
            intent_id=intent_id,
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
            return await self._recover_emergency(command, client_order_id)
        if command.status is ExecutionCommandStatus.FAILED:
            raise RuntimeError("strategy exit previously failed; reconciliation required")
        self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.REQUESTED,
        )
        try:
            acknowledgement = await self.adapter.emergency_close(position, client_order_id)
        except Exception as exc:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.AMBIGUOUS,
                error=_safe_error(exc),
            )
            return await self._recover_emergency(command, client_order_id)
        command = self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.ACKNOWLEDGED,
            exchange_order_id=None if acknowledgement is None else acknowledgement.order_id,
        )
        if acknowledgement is None:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.CONFIRMED,
            )
        return command

    def confirm_emergency_close(
        self,
        command_id: str,
        observed_position: Position,
    ) -> ExecutionCommandRecord:
        command = self._required_command(command_id)
        if command.kind is not ExecutionCommandKind.EMERGENCY_CLOSE:
            raise ValueError("command is not an emergency close")
        if observed_position.symbol != command.symbol:
            raise ValueError("position symbol differs from emergency command")
        if observed_position.side is not PositionSide.FLAT or observed_position.quantity != 0:
            raise ProtectionConfirmationError("emergency close is not yet confirmed flat")
        if command.status is ExecutionCommandStatus.CONFIRMED:
            return command
        if command.status is not ExecutionCommandStatus.ACKNOWLEDGED:
            raise ValueError(f"emergency close cannot be confirmed from {command.status}")
        return self.journal.update_execution_command(
            command.command_id,
            ExecutionCommandStatus.CONFIRMED,
        )

    async def _recover_entry(
        self,
        command: ExecutionCommandRecord,
        client_order_id: str,
    ) -> BybitOrderAcknowledgement:
        if command.status in {
            ExecutionCommandStatus.ACKNOWLEDGED,
            ExecutionCommandStatus.CONFIRMED,
        }:
            if command.exchange_order_id is None:
                raise RuntimeError("acknowledged entry has no exchange order id")
            return BybitOrderAcknowledgement(command.exchange_order_id, client_order_id)
        if command.status is ExecutionCommandStatus.FAILED:
            raise RuntimeError("entry command previously failed; a new intent is required")
        if command.status is ExecutionCommandStatus.PLANNED:
            raise RuntimeError("planned entry recovery is not valid")
        try:
            order = await self.observe_order(command.symbol, client_order_id)
        except Exception as exc:
            raise AmbiguousExecutionCommand(
                "entry outcome is ambiguous and lookup failed; blind retry is forbidden"
            ) from exc
        if order is None:
            if command.status is ExecutionCommandStatus.REQUESTED:
                self.journal.update_execution_command(
                    command.command_id,
                    ExecutionCommandStatus.AMBIGUOUS,
                    error="request sent but order was not found during recovery",
                )
            raise AmbiguousExecutionCommand(
                "entry outcome is ambiguous; reconcile by orderLinkId before retry"
            )
        confirmed = self.confirm_entry(order)
        return BybitOrderAcknowledgement(
            confirmed.exchange_order_id or order.order_id,
            client_order_id,
        )

    async def _recover_emergency(
        self,
        command: ExecutionCommandRecord,
        client_order_id: str,
    ) -> ExecutionCommandRecord:
        try:
            order = await self.observe_order(command.symbol, client_order_id)
        except Exception as exc:
            raise AmbiguousExecutionCommand(
                "emergency close outcome is ambiguous and lookup failed"
            ) from exc
        if order is None:
            if command.status is ExecutionCommandStatus.REQUESTED:
                command = self.journal.update_execution_command(
                    command.command_id,
                    ExecutionCommandStatus.AMBIGUOUS,
                    error="emergency request sent but order was not found during recovery",
                )
            raise AmbiguousExecutionCommand(
                "emergency close outcome is ambiguous; operator reconciliation is mandatory"
            )
        if order.request.client_order_id != client_order_id:
            raise AmbiguousExecutionCommand(
                "emergency lookup returned a mismatched client order id"
            )
        if command.status is not ExecutionCommandStatus.ACKNOWLEDGED:
            command = self.journal.update_execution_command(
                command.command_id,
                ExecutionCommandStatus.ACKNOWLEDGED,
                exchange_order_id=order.order_id,
            )
        return command

    async def _wait_for_protection(
        self,
        symbol: str,
        expected: ExchangeProtectionPlan,
    ) -> bool:
        for attempt in range(self.protection_confirmation_attempts):
            observed = await self.observe_position(symbol)
            if _protection_matches(observed, expected):
                return True
            if attempt + 1 < self.protection_confirmation_attempts:
                await asyncio.sleep(self.protection_confirmation_delay)
        return False

    def _require_entry_safety(self, health: BybitHealthSnapshot) -> None:
        if self.state_machine.state is not AppState.RUNNING:
            raise PermissionError("engine must be RUNNING before an entry can be submitted")
        if not health.can_create_entry:
            raise PermissionError("all Public WS, Private WS, and REST data must be fresh")

    @staticmethod
    def _validate_position_and_stop(
        snapshot: BybitPositionSnapshot,
        protection: ExchangeProtectionPlan,
    ) -> None:
        position = snapshot.position
        if position.side is PositionSide.FLAT or position.quantity == 0:
            raise ValueError("cannot protect a flat position")
        reference = position.average_price or snapshot.mark_price
        if reference is None:
            raise ValueError("position reference price is required")
        if position.side is PositionSide.LONG and protection.stop_loss >= reference:
            raise ValueError("long hard stop must be below the position reference price")
        if position.side is PositionSide.SHORT and protection.stop_loss <= reference:
            raise ValueError("short hard stop must be above the position reference price")

    def _enter_emergency_state(self) -> None:
        if self.state_machine.state is AppState.EMERGENCY_STOP:
            return
        try:
            self.state_machine.transition(
                AppState.EMERGENCY_STOP,
                "position protection was not confirmed",
            )
        except InvalidStateTransition as exc:
            raise RuntimeError("cannot enter emergency state from current engine state") from exc

    def _required_command(self, command_id: str) -> ExecutionCommandRecord:
        command = self.journal.execution_command(command_id=command_id)
        if command is None:
            raise RuntimeError("execution command disappeared from journal")
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


def _protection_key(
    kind: ExecutionCommandKind,
    intent_id: str | None,
    symbol: str,
    quantity: Decimal,
    protection: ExchangeProtectionPlan,
) -> str:
    material = ":".join(
        (
            kind.value,
            intent_id or "-",
            symbol,
            str(quantity),
            str(protection.stop_loss),
            str(protection.take_profit or "-"),
            str(protection.trailing_distance or "-"),
            str(protection.trailing_active_price or "-"),
        )
    )
    return f"protection:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


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


def _safe_error(error: Exception) -> str:
    return str(error).strip() or error.__class__.__name__
