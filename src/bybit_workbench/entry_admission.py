from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from bybit_workbench.capital_reservation import (
    CapitalReservation,
    CapitalReservationRequest,
    PostgresCapitalReservationPort,
)
from bybit_workbench.universal_entry.fingerprint import fingerprint


class SlotClaimState(StrEnum):
    CLAIMED = "CLAIMED"
    BOUND = "BOUND"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RELEASED = "RELEASED"


class PositionModeStateUnavailable(RuntimeError):
    pass


class PositionModeMismatch(RuntimeError):
    def __init__(self, *, state_ref: str, position_mode: str, position_idx: int | None) -> None:
        super().__init__(
            f"position mode mismatch: mode={position_mode} position_idx={position_idx}"
        )
        self.state_ref = state_ref
        self.position_mode = position_mode
        self.position_idx = position_idx


class SlotOwnershipConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EntryAdmissionRequest:
    account_ref: str
    exchange_position_key: str
    symbol: str
    direction: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    entry_plan_fingerprint: str
    signal_id: str
    strategy_attempt_id: str
    requested_amount: Decimal
    amount_currency: str
    capacity_snapshot_id: str
    capacity_observed_at: datetime
    capacity_available: Decimal
    requested_at: datetime
    pre_dispatch_expires_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "account_ref",
            "exchange_position_key",
            "symbol",
            "direction",
            "strategy_id",
            "strategy_version",
            "strategy_config_fingerprint",
            "entry_plan_fingerprint",
            "signal_id",
            "strategy_attempt_id",
            "amount_currency",
            "capacity_snapshot_id",
        ):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"Entry admission requires {field}")
        if self.direction not in {"LONG", "SHORT"}:
            raise ValueError("Entry admission direction must be LONG or SHORT")
        if self.requested_amount <= 0:
            raise ValueError("Entry admission requested_amount must be positive")
        if self.capacity_available < 0:
            raise ValueError("Entry admission capacity_available cannot be negative")
        for field in (
            "capacity_observed_at",
            "requested_at",
            "pre_dispatch_expires_at",
        ):
            value = getattr(self, field)
            if value.tzinfo is None:
                raise ValueError(f"Entry admission {field} must be timezone-aware")
        if self.pre_dispatch_expires_at <= self.requested_at:
            raise ValueError("Entry admission expiry must be after requested_at")

    @property
    def slot_claim_id(self) -> str:
        return "slot-" + fingerprint(
            {
                "exchange_position_key": self.exchange_position_key,
                "strategy_attempt_id": self.strategy_attempt_id,
            }
        )[:32]


@dataclass(frozen=True, slots=True)
class EntryAdmissionReceipt:
    exchange_position_slot_claim_id: str
    exchange_position_key: str
    position_mode_state_ref: str
    position_idx: int
    capital_reservation: CapitalReservation


class EntryAdmissionPort(Protocol):
    def admit(self, request: EntryAdmissionRequest) -> EntryAdmissionReceipt: ...


class CursorLike(Protocol):
    def fetchone(self) -> Sequence[object] | None: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...

    def transaction(self) -> AbstractContextManager[object]: ...


class PostgresEntryAdmissionPort:
    """Canonical all-or-nothing real Entry admission.

    Lock order is stable:
    1) account identity,
    2) exchange position key,
    3) capital reservation ledger.

    Position-mode validation and physical slot claim happen before capital
    reservation. Any failure rolls the whole transaction back.
    """

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection
        self._capital = PostgresCapitalReservationPort(connection)

    def admit(self, request: EntryAdmissionRequest) -> EntryAdmissionReceipt:
        now = request.requested_at.astimezone(UTC)
        with self._connection.transaction():
            self._connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (request.account_ref,),
            )
            self._connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (request.exchange_position_key,),
            )

            mode = self._connection.execute(
                """SELECT position_mode_state_ref,position_mode,position_idx,
                          observed_at,received_at,fresh_until
                     FROM runtime.position_mode_states
                    WHERE account_ref=%s
                      AND product_category='LINEAR'
                      AND instrument=%s
                    ORDER BY observed_at DESC,created_at DESC
                    LIMIT 1
                    FOR SHARE""",
                (request.account_ref, request.symbol),
            ).fetchone()
            if mode is None:
                raise PositionModeStateUnavailable(
                    f"position mode state missing for {request.account_ref}:{request.symbol}"
                )
            state_ref = str(mode[0])
            position_mode = str(mode[1])
            position_idx = None if mode[2] is None else int(str(mode[2]))
            observed_at = mode[3]
            received_at = mode[4]
            fresh_until = mode[5]
            if not all(
                isinstance(value, datetime)
                for value in (observed_at, received_at, fresh_until)
            ):
                raise PositionModeStateUnavailable("position mode timestamps are invalid")
            if (
                observed_at.tzinfo is None
                or received_at.tzinfo is None
                or fresh_until.tzinfo is None
                or observed_at.astimezone(UTC) > now
                or received_at.astimezone(UTC) > now
                or fresh_until.astimezone(UTC) < now
            ):
                raise PositionModeStateUnavailable(
                    f"position mode state stale/invalid: {state_ref}"
                )
            if position_mode != "ONE_WAY" or position_idx != 0:
                raise PositionModeMismatch(
                    state_ref=state_ref,
                    position_mode=position_mode,
                    position_idx=position_idx,
                )

            existing = self._connection.execute(
                """SELECT exchange_position_slot_claim_id,exchange_position_key,
                          position_mode_state_ref,position_idx,capital_reservation_id,
                          claim_state
                     FROM runtime.exchange_position_slot_claims
                    WHERE strategy_attempt_id=%s
                    FOR UPDATE""",
                (request.strategy_attempt_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing[1]) != request.exchange_position_key
                    or str(existing[2]) != state_ref
                    or int(str(existing[3])) != 0
                    or str(existing[5]) not in {
                        SlotClaimState.CLAIMED.value,
                        SlotClaimState.BOUND.value,
                        SlotClaimState.RECONCILIATION_REQUIRED.value,
                    }
                ):
                    raise ValueError("slot claim identity collision")
                if existing[4] is None:
                    raise RuntimeError("slot claim exists without capital reservation")
                reservation = self._capital.reserve(self._capital_request(request))
                return EntryAdmissionReceipt(
                    exchange_position_slot_claim_id=str(existing[0]),
                    exchange_position_key=request.exchange_position_key,
                    position_mode_state_ref=state_ref,
                    position_idx=0,
                    capital_reservation=reservation,
                )

            conflict = self._connection.execute(
                """SELECT exchange_position_slot_claim_id,strategy_attempt_id
                     FROM runtime.exchange_position_slot_claims
                    WHERE exchange_position_key=%s
                      AND claim_state IN ('CLAIMED','BOUND','RECONCILIATION_REQUIRED')
                    LIMIT 1
                    FOR UPDATE""",
                (request.exchange_position_key,),
            ).fetchone()
            if conflict is not None:
                raise SlotOwnershipConflict(
                    "active slot claim exists: " + str(conflict[0])
                )

            owned = self._connection.execute(
                """SELECT position_id
                     FROM runtime.position_ownership
                    WHERE exchange_position_key=%s
                      AND state IN ('OPEN','RECONCILIATION_REQUIRED')
                    LIMIT 1""",
                (request.exchange_position_key,),
            ).fetchone()
            if owned is not None:
                raise SlotOwnershipConflict("active StrategyPosition owns slot: " + str(owned[0]))

            hot = self._connection.execute(
                """SELECT side,size
                     FROM runtime.hot_positions
                    WHERE symbol=%s AND position_idx=0
                      AND size::numeric <> 0
                    LIMIT 1""",
                (request.symbol,),
            ).fetchone()
            if hot is not None:
                raise SlotOwnershipConflict(
                    f"Exchange hot position occupies slot: side={hot[0]} size={hot[1]}"
                )

            pending = self._connection.execute(
                """SELECT order_id
                     FROM runtime.hot_orders
                    WHERE symbol=%s
                      AND order_status IN ('New','PartiallyFilled','Untriggered')
                      AND coalesce((payload_json::jsonb->>'reduceOnly')::boolean,false)=false
                    LIMIT 1""",
                (request.symbol,),
            ).fetchone()
            if pending is not None:
                raise SlotOwnershipConflict("non-reduce Exchange order occupies slot")

            self._connection.execute(
                """INSERT INTO runtime.exchange_position_slot_claims(
                       exchange_position_slot_claim_id,exchange_position_key,account_ref,
                       symbol,position_idx,strategy_attempt_id,strategy_id,strategy_version,
                       strategy_config_fingerprint,entry_plan_fingerprint,direction,
                       position_mode_state_ref,claim_state,claimed_at,updated_at
                   ) VALUES(%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s,%s,'CLAIMED',%s,%s)""",
                (
                    request.slot_claim_id,
                    request.exchange_position_key,
                    request.account_ref,
                    request.symbol,
                    request.strategy_attempt_id,
                    request.strategy_id,
                    request.strategy_version,
                    request.strategy_config_fingerprint,
                    request.entry_plan_fingerprint,
                    request.direction,
                    state_ref,
                    now,
                    now,
                ),
            )

            reservation = self._capital.reserve(self._capital_request(request))
            self._connection.execute(
                """UPDATE runtime.exchange_position_slot_claims
                      SET capital_reservation_id=%s,updated_at=%s
                    WHERE exchange_position_slot_claim_id=%s
                      AND claim_state='CLAIMED'""",
                (reservation.reservation_id, now, request.slot_claim_id),
            )
            return EntryAdmissionReceipt(
                exchange_position_slot_claim_id=request.slot_claim_id,
                exchange_position_key=request.exchange_position_key,
                position_mode_state_ref=state_ref,
                position_idx=0,
                capital_reservation=reservation,
            )

    @staticmethod
    def _capital_request(request: EntryAdmissionRequest) -> CapitalReservationRequest:
        return CapitalReservationRequest(
            account_ref=request.account_ref,
            strategy_id=request.strategy_id,
            strategy_version=request.strategy_version,
            strategy_config_fingerprint=request.strategy_config_fingerprint,
            entry_plan_fingerprint=request.entry_plan_fingerprint,
            signal_id=request.signal_id,
            strategy_attempt_id=request.strategy_attempt_id,
            requested_amount=request.requested_amount,
            amount_currency=request.amount_currency,
            capacity_snapshot_id=request.capacity_snapshot_id,
            capacity_observed_at=request.capacity_observed_at,
            capacity_available=request.capacity_available,
            requested_at=request.requested_at,
            pre_dispatch_expires_at=request.pre_dispatch_expires_at,
        )


def bind_slot_claim_to_position(
    connection: ConnectionLike,
    *,
    exchange_position_slot_claim_id: str,
    strategy_position_id: str,
    bound_at: datetime,
) -> None:
    cursor = connection.execute(
        """UPDATE runtime.exchange_position_slot_claims
              SET claim_state='BOUND',strategy_position_id=%s,bound_at=%s,updated_at=%s
            WHERE exchange_position_slot_claim_id=%s
              AND claim_state='CLAIMED'""",
        (
            strategy_position_id,
            bound_at.astimezone(UTC),
            bound_at.astimezone(UTC),
            exchange_position_slot_claim_id,
        ),
    )
    rowcount = getattr(cursor, "rowcount", None)
    if rowcount == 0:
        row = connection.execute(
            """SELECT claim_state,strategy_position_id
                 FROM runtime.exchange_position_slot_claims
                WHERE exchange_position_slot_claim_id=%s""",
            (exchange_position_slot_claim_id,),
        ).fetchone()
        if row is None or str(row[0]) != "BOUND" or str(row[1]) != strategy_position_id:
            raise RuntimeError("slot claim could not bind to StrategyPosition")


def transition_slot_claim(
    connection: ConnectionLike,
    *,
    exchange_position_slot_claim_id: str,
    state: SlotClaimState,
    at: datetime,
    reason: str,
) -> None:
    if state is SlotClaimState.RELEASED:
        connection.execute(
            """UPDATE runtime.exchange_position_slot_claims
                  SET claim_state='RELEASED',released_at=%s,release_reason=%s,updated_at=%s
                WHERE exchange_position_slot_claim_id=%s
                  AND claim_state IN ('CLAIMED','BOUND','RECONCILIATION_REQUIRED')""",
            (
                at.astimezone(UTC),
                reason,
                at.astimezone(UTC),
                exchange_position_slot_claim_id,
            ),
        )
        return
    if state is SlotClaimState.RECONCILIATION_REQUIRED:
        connection.execute(
            """UPDATE runtime.exchange_position_slot_claims
                  SET claim_state='RECONCILIATION_REQUIRED',updated_at=%s,
                      release_reason=%s
                WHERE exchange_position_slot_claim_id=%s
                  AND claim_state IN ('CLAIMED','BOUND','RECONCILIATION_REQUIRED')""",
            (
                at.astimezone(UTC),
                reason,
                exchange_position_slot_claim_id,
            ),
        )
        return
    raise ValueError(f"unsupported external slot-claim transition: {state.value}")
