from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock

from bybit_workbench.domain.types import AppState


class InvalidStateTransition(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StateTransition:
    previous: AppState
    current: AppState
    reason: str
    occurred_at: datetime


_ALLOWED: dict[AppState, frozenset[AppState]] = {
    AppState.DISCONNECTED: frozenset({AppState.SYNCING}),
    AppState.SYNCING: frozenset(
        {AppState.READY, AppState.DEGRADED, AppState.ERROR, AppState.DISCONNECTED}
    ),
    AppState.READY: frozenset(
        {
            AppState.ARMED,
            AppState.SYNCING,
            AppState.DEGRADED,
            AppState.ERROR,
            AppState.DISCONNECTED,
            AppState.EMERGENCY_STOP,
        }
    ),
    AppState.ARMED: frozenset(
        {
            AppState.RUNNING,
            AppState.READY,
            AppState.PAUSED,
            AppState.DEGRADED,
            AppState.ERROR,
            AppState.DISCONNECTED,
            AppState.EMERGENCY_STOP,
        }
    ),
    AppState.RUNNING: frozenset(
        {
            AppState.PAUSED,
            AppState.READY,
            AppState.DEGRADED,
            AppState.ERROR,
            AppState.DISCONNECTED,
            AppState.EMERGENCY_STOP,
        }
    ),
    AppState.PAUSED: frozenset(
        {
            AppState.ARMED,
            AppState.READY,
            AppState.SYNCING,
            AppState.DEGRADED,
            AppState.ERROR,
            AppState.DISCONNECTED,
            AppState.EMERGENCY_STOP,
        }
    ),
    AppState.DEGRADED: frozenset(
        {AppState.SYNCING, AppState.ERROR, AppState.DISCONNECTED, AppState.EMERGENCY_STOP}
    ),
    AppState.ERROR: frozenset({AppState.SYNCING, AppState.DISCONNECTED, AppState.EMERGENCY_STOP}),
    AppState.EMERGENCY_STOP: frozenset({AppState.SYNCING, AppState.ERROR, AppState.DISCONNECTED}),
}


class AppStateMachine:
    def __init__(
        self,
        on_transition: Callable[[StateTransition], None] | None = None,
    ) -> None:
        self._state = AppState.DISCONNECTED
        self._on_transition = on_transition
        self._lock = RLock()

    @property
    def state(self) -> AppState:
        with self._lock:
            return self._state

    @property
    def can_create_entry(self) -> bool:
        with self._lock:
            return self._state is AppState.RUNNING

    def transition(self, target: AppState, reason: str) -> StateTransition:
        with self._lock:
            if not reason.strip():
                raise ValueError("state transition reason is required")
            if target not in _ALLOWED[self._state]:
                raise InvalidStateTransition(f"cannot transition {self._state} -> {target}")
            event = StateTransition(self._state, target, reason, datetime.now(UTC))
            self._state = target
            if self._on_transition is not None:
                self._on_transition(event)
            return event
