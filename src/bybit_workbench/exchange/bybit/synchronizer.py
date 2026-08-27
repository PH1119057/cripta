from dataclasses import dataclass
from datetime import UTC, datetime

from bybit_workbench.app.state_machine import AppStateMachine, InvalidStateTransition
from bybit_workbench.domain.types import AppState, OrderStatus
from bybit_workbench.persistence import (
    ReconciliationResult,
    ReconciliationService,
    TradingJournal,
)

from .health import HealthMonitor
from .models import BybitReadSnapshot
from .rest import BybitReadOnlyAdapter


@dataclass(frozen=True, slots=True)
class ReadOnlySyncOutcome:
    snapshot: BybitReadSnapshot
    initial_reconciliation: ReconciliationResult
    verification: ReconciliationResult


class ReadOnlySynchronizer:
    def __init__(
        self,
        adapter: BybitReadOnlyAdapter,
        journal: TradingJournal,
        state_machine: AppStateMachine,
        health: HealthMonitor,
    ) -> None:
        self.adapter = adapter
        self.journal = journal
        self.state_machine = state_machine
        self.health = health

    async def synchronize(
        self,
        symbol: str,
        reconciliation_id: str,
        *,
        run_id: str | None = None,
        occurred_at: datetime | None = None,
        update_state: bool = True,
    ) -> ReadOnlySyncOutcome:
        timestamp = occurred_at or datetime.now(UTC)
        if update_state:
            self._enter_syncing()
        try:
            snapshot = await self.adapter.read_snapshot(symbol)
            self.health.mark_message("rest", timestamp)
            reconciler = ReconciliationService(self.journal)
            initial = reconciler.run(
                reconciliation_id,
                symbol,
                snapshot.position.position,
                snapshot.open_orders,
                run_id=run_id,
                occurred_at=timestamp,
            )
            local_before = self.journal.load_projection(symbol, run_id=run_id)
            remote_ids = {order.order_id for order in snapshot.open_orders}
            for local_order in local_before.active_orders:
                if local_order.order_id in remote_ids:
                    continue
                local_order.status = OrderStatus.CANCELLED
                local_order.updated_at = timestamp
                self.journal.upsert_order(
                    local_order,
                    event_id=f"{reconciliation_id}:missing-remote:{local_order.order_id}",
                    raw_payload={"source": "reconciliation", "reason": "missing_on_exchange"},
                )
            for remote_order in snapshot.open_orders:
                self.journal.upsert_order(
                    remote_order,
                    event_id=f"{reconciliation_id}:rest:{remote_order.order_id}:{remote_order.updated_at.isoformat()}",
                    raw_payload={"source": "bybit_rest"},
                )
            self.journal.record_position_snapshot(
                snapshot.position.position,
                source="bybit_rest",
                run_id=run_id,
                observed_at=snapshot.position.observed_at,
            )
            verification = reconciler.run(
                f"{reconciliation_id}:verify",
                symbol,
                snapshot.position.position,
                snapshot.open_orders,
                run_id=run_id,
                occurred_at=timestamp,
            )
            if not verification.synchronized:
                if self.state_machine.state in {
                    AppState.READY,
                    AppState.ARMED,
                    AppState.RUNNING,
                    AppState.PAUSED,
                    AppState.SYNCING,
                }:
                    self.state_machine.transition(
                        AppState.DEGRADED,
                        "read-only reconciliation verification failed",
                    )
            elif update_state:
                self.state_machine.transition(
                    AppState.READY,
                    "Bybit read-only snapshot reconciled",
                )
            return ReadOnlySyncOutcome(snapshot, initial, verification)
        except Exception as exc:
            self.health.mark_error("rest", str(exc))
            if update_state and self.state_machine.state is AppState.SYNCING:
                self.state_machine.transition(AppState.DEGRADED, f"read-only sync failed: {exc}")
            raise

    def _enter_syncing(self) -> None:
        if self.state_machine.state is AppState.SYNCING:
            return
        try:
            self.state_machine.transition(AppState.SYNCING, "Bybit read-only sync started")
        except InvalidStateTransition:
            self.state_machine.transition(
                AppState.DEGRADED,
                "trading paused for Bybit read-only resynchronization",
            )
            self.state_machine.transition(AppState.SYNCING, "Bybit read-only sync started")
