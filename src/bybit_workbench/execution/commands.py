from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class ExecutionCommandKind(StrEnum):
    ENTRY = "entry"
    SET_PROTECTION = "set_protection"
    MOVE_STOP = "move_stop"
    CANCEL_ENTRY = "cancel_entry"
    CANCEL_ORDER = "cancel_order"
    STRATEGY_EXIT = "strategy_exit"
    EMERGENCY_CLOSE = "emergency_close"


class ExecutionCommandStatus(StrEnum):
    PLANNED = "planned"
    REQUESTED = "requested"
    ACKNOWLEDGED = "acknowledged"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class ExecutionCommandRecord:
    command_id: str
    kind: ExecutionCommandKind
    idempotency_key: str
    symbol: str
    request: Mapping[str, Any]
    status: ExecutionCommandStatus
    intent_id: str | None
    exchange_order_id: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class AmbiguousExecutionCommand(RuntimeError):
    """The exchange may have accepted a request, so blind retry is unsafe."""


class ProtectionConfirmationError(RuntimeError):
    pass
