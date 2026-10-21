from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from bybit_workbench.universal_entry.fingerprint import fingerprint

# TC §7.5.1: release-side fail-closed envelope for one unified R1 Strategy.
# The immutable StrategyCard and its version must not be rewritten in-place.
_R1_UNIFIED_STRATEGY_ID = "r1_five_symbols"
_R1_UNIFIED_VERSION = "1.0-five-symbols-20261021"
_R1_PER_POSITION_USDT = Decimal("10")
_R1_AGGREGATE_USDT = Decimal("50")


class CapitalReservationState(StrEnum):
    RESERVED = "RESERVED"
    DISPATCHED = "DISPATCHED"
    PENDING_EXCHANGE_REFLECTION = "PENDING_EXCHANGE_REFLECTION"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class CapitalReservationRequest:
    account_ref: str
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
    strategy_execution_binding_id: str | None = None
    execution_leg: str | None = None
    trading_account_id: str | None = None

    def __post_init__(self) -> None:
        for field in (
            "account_ref",
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
                raise ValueError(f"capital reservation requires {field}")
        binding_values = (
            self.strategy_execution_binding_id,
            self.execution_leg,
            self.trading_account_id,
        )
        if any(value is not None for value in binding_values) and not all(
            value is not None and str(value).strip() for value in binding_values
        ):
            raise ValueError("capital reservation binding identity must be all-or-none")
        if self.requested_amount <= 0:
            raise ValueError("requested_amount must be positive")
        if self.capacity_available < 0:
            raise ValueError("capacity_available cannot be negative")
        if (
            self.capacity_observed_at.tzinfo is None
            or self.requested_at.tzinfo is None
            or self.pre_dispatch_expires_at.tzinfo is None
        ):
            raise ValueError("capital reservation timestamps must be timezone-aware")
        if self.pre_dispatch_expires_at <= self.requested_at:
            raise ValueError("pre_dispatch_expires_at must be after requested_at")

    @property
    def reservation_id(self) -> str:
        return "cap-" + fingerprint(
            {
                "account_ref": self.account_ref,
                "strategy_attempt_id": self.strategy_attempt_id,
                "requested_amount": self.requested_amount,
                "amount_currency": self.amount_currency.upper(),
                "capacity_snapshot_id": self.capacity_snapshot_id,
                "pre_dispatch_expires_at": self.pre_dispatch_expires_at.astimezone(UTC),
                "strategy_execution_binding_id": self.strategy_execution_binding_id,
                "execution_leg": self.execution_leg,
                "trading_account_id": self.trading_account_id,
            }
        )[:32]


@dataclass(frozen=True, slots=True)
class CapitalReservation:
    reservation_id: str
    account_ref: str
    strategy_attempt_id: str
    requested_amount: Decimal
    amount_currency: str
    capacity_snapshot_id: str
    state: CapitalReservationState
    created_at: datetime
    updated_at: datetime
    pre_dispatch_expires_at: datetime
    state_reason: str | None = None
    exchange_commitment_ref: str | None = None
    exchange_commitment_at: datetime | None = None
    strategy_execution_binding_id: str | None = None
    execution_leg: str | None = None
    trading_account_id: str | None = None


class InsufficientCapital(RuntimeError):
    def __init__(self, requested: Decimal, effective_available: Decimal) -> None:
        super().__init__(
            f"requested capital {requested} exceeds effective available {effective_available}"
        )
        self.requested = requested
        self.effective_available = effective_available


class CapitalReservationPort(Protocol):
    def reserve(self, request: CapitalReservationRequest) -> CapitalReservation: ...


class CursorLike(Protocol):
    def fetchone(self) -> Sequence[object] | None: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...

    def transaction(self) -> AbstractContextManager[object]: ...


class PostgresCapitalReservationPort:
    """Atomic first-come-first-served reservation for one trading account.

    The account-scoped advisory transaction lock serializes competing Entry
    attempts without ranking Strategy. The Dispatcher snapshot remains an input
    fact; this port only prevents concurrent over-allocation of that fact.
    """

    _OUTSTANDING_STATES = (
        CapitalReservationState.RESERVED.value,
        CapitalReservationState.DISPATCHED.value,
        CapitalReservationState.PENDING_EXCHANGE_REFLECTION.value,
        CapitalReservationState.RECONCILIATION_REQUIRED.value,
    )

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def reserve(self, request: CapitalReservationRequest) -> CapitalReservation:
        if (
            not request.strategy_execution_binding_id
            or not request.execution_leg
            or not request.trading_account_id
        ):
            raise ValueError(
                "R10 capital reservation requires PROVEN StrategyExecutionBinding lineage"
            )
        with self._connection.transaction():
            lock_identity = request.trading_account_id
            self._connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (lock_identity,),
            )
            # The portfolio ceiling is Strategy-wide even across release
            # generations. Serialize all unified R1 commitments too.
            if request.strategy_id == _R1_UNIFIED_STRATEGY_ID:
                self._connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    ("r1-aggregate-cap:" + request.strategy_id,),
                )
            existing = self._connection.execute(
                """SELECT reservation_id,account_ref,strategy_attempt_id,requested_amount,
                          amount_currency,capacity_snapshot_id,state,created_at,updated_at,
                          exchange_commitment_ref,exchange_commitment_at,
                          pre_dispatch_expires_at,state_reason,
                          strategy_id,strategy_version,strategy_config_fingerprint,
                          entry_plan_fingerprint,signal_id,
                          strategy_execution_binding_id,execution_leg,trading_account_id
                     FROM runtime.capital_reservations
                    WHERE strategy_attempt_id=%s""",
                (request.strategy_attempt_id,),
            ).fetchone()
            if existing is not None:
                self._validate_existing(existing, request)
                return self._reservation_from_row(existing)

            self._guard_unified_r1_envelope(request)

            row = self._connection.execute(
                """SELECT coalesce(sum(requested_amount),0)
                     FROM runtime.capital_reservations
                    WHERE trading_account_id=%s
                      AND identity_status='PROVEN'
                      AND (
                          state = ANY(%s)
                          OR (
                              state='CONSUMED'
                              AND (
                                  exchange_commitment_at IS NULL
                                  OR exchange_commitment_at > %s
                              )
                          )
                      )""",
                (
                    request.trading_account_id,
                    list(self._OUTSTANDING_STATES),
                    request.capacity_observed_at.astimezone(UTC),
                ),
            ).fetchone()
            outstanding = Decimal(str(0 if row is None else row[0]))
            effective = max(Decimal("0"), request.capacity_available - outstanding)
            if request.requested_amount > effective:
                raise InsufficientCapital(request.requested_amount, effective)

            now = request.requested_at.astimezone(UTC)
            self._connection.execute(
                """INSERT INTO runtime.capital_reservations(
                       reservation_id,account_ref,strategy_id,strategy_version,
                       strategy_config_fingerprint,entry_plan_fingerprint,signal_id,
                       strategy_attempt_id,requested_amount,amount_currency,
                       capacity_snapshot_id,capacity_observed_at,
                       capacity_available_at_reservation,pre_dispatch_expires_at,
                       state,state_reason,created_at,updated_at,
                       strategy_execution_binding_id,execution_leg,trading_account_id,
                       identity_status
                   ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            'RESERVED','RESERVATION_CREATED',%s,%s,%s,%s,%s,%s)""",
                (
                    request.reservation_id,
                    request.account_ref,
                    request.strategy_id,
                    request.strategy_version,
                    request.strategy_config_fingerprint,
                    request.entry_plan_fingerprint,
                    request.signal_id,
                    request.strategy_attempt_id,
                    request.requested_amount,
                    request.amount_currency.upper(),
                    request.capacity_snapshot_id,
                    request.capacity_observed_at.astimezone(UTC),
                    request.capacity_available,
                    request.pre_dispatch_expires_at.astimezone(UTC),
                    now,
                    now,
                    request.strategy_execution_binding_id,
                    request.execution_leg,
                    request.trading_account_id,
                    "PROVEN",
                ),
            )
            return CapitalReservation(
                reservation_id=request.reservation_id,
                account_ref=request.account_ref,
                strategy_attempt_id=request.strategy_attempt_id,
                requested_amount=request.requested_amount,
                amount_currency=request.amount_currency.upper(),
                capacity_snapshot_id=request.capacity_snapshot_id,
                state=CapitalReservationState.RESERVED,
                created_at=now,
                updated_at=now,
                pre_dispatch_expires_at=request.pre_dispatch_expires_at.astimezone(UTC),
                state_reason="RESERVATION_CREATED",
            )

    def _guard_unified_r1_envelope(
        self, request: CapitalReservationRequest
    ) -> None:
        """TC §7.5.1: account-locked 10/50 USDT hard ceiling for unified R1.

        Executed after idempotency lookup under the transaction's
        account advisory lock. All non-RELEASED reservation states count,
        including CONSUMED (open position), reconciliation and pending
        Exchange reflection. Old versions of this Strategy still count.
        Unknown account/currency/identity is a hard stop, not free capacity.
        """
        if request.strategy_id != _R1_UNIFIED_STRATEGY_ID:
            return
        if (
            request.strategy_version != _R1_UNIFIED_VERSION
            or request.amount_currency.upper() != "USDT"
            or request.requested_amount != _R1_PER_POSITION_USDT
            or request.execution_leg != "PRIMARY"
        ):
            raise ValueError("R1_UNIFIED_CAPITAL_POLICY_MISMATCH")

        row = self._connection.execute(
            """SELECT coalesce(sum(requested_amount),0),
                      count(*) FILTER (
                          WHERE identity_status IS DISTINCT FROM 'PROVEN'
                             OR trading_account_id IS DISTINCT FROM %s
                             OR amount_currency IS DISTINCT FROM 'USDT'
                      )
                 FROM runtime.capital_reservations
                WHERE strategy_id=%s AND state<>'RELEASED'""",
            (request.trading_account_id, request.strategy_id),
        ).fetchone()
        if row is None or len(row) != 2:
            raise ValueError("R1_UNIFIED_CAPITAL_LEDGER_UNAVAILABLE")
        if int(str(row[1])) != 0:
            raise ValueError("R1_UNIFIED_CAPITAL_LEDGER_IDENTITY_UNPROVEN")
        committed = Decimal(str(row[0]))
        if not committed.is_finite() or committed < 0:
            raise ValueError("R1_UNIFIED_CAPITAL_LEDGER_INVALID")
        available = max(Decimal("0"), _R1_AGGREGATE_USDT - committed)
        if request.requested_amount > available:
            raise InsufficientCapital(request.requested_amount, available)

    def transition(
        self,
        reservation_id: str,
        *,
        state: CapitalReservationState,
        exchange_commitment_ref: str | None = None,
        exchange_commitment_at: datetime | None = None,
        state_reason: str | None = None,
    ) -> CapitalReservation:
        with self._connection.transaction():
            row = self._connection.execute(
                """SELECT reservation_id,account_ref,strategy_attempt_id,requested_amount,
                          amount_currency,capacity_snapshot_id,state,created_at,updated_at,
                          exchange_commitment_ref,exchange_commitment_at,
                          pre_dispatch_expires_at,state_reason
                     FROM runtime.capital_reservations
                    WHERE reservation_id=%s
                    FOR UPDATE""",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown capital reservation: {reservation_id}")
            current = CapitalReservationState(str(row[6]))
            if current == state:
                return self._reservation_from_short_row(row)
            allowed = {
                CapitalReservationState.RESERVED: {
                    CapitalReservationState.DISPATCHED,
                    CapitalReservationState.RELEASED,
                    CapitalReservationState.RECONCILIATION_REQUIRED,
                },
                CapitalReservationState.DISPATCHED: {
                    CapitalReservationState.PENDING_EXCHANGE_REFLECTION,
                    CapitalReservationState.RELEASED,
                    CapitalReservationState.RECONCILIATION_REQUIRED,
                },
                CapitalReservationState.PENDING_EXCHANGE_REFLECTION: {
                    CapitalReservationState.CONSUMED,
                    CapitalReservationState.RELEASED,
                    CapitalReservationState.RECONCILIATION_REQUIRED,
                },
                CapitalReservationState.CONSUMED: {
                    CapitalReservationState.RELEASED,
                    CapitalReservationState.RECONCILIATION_REQUIRED,
                },
                CapitalReservationState.RECONCILIATION_REQUIRED: {
                    CapitalReservationState.CONSUMED,
                    CapitalReservationState.RELEASED,
                },
                CapitalReservationState.RELEASED: set(),
            }
            if state not in allowed[current]:
                raise ValueError(f"invalid capital reservation transition {current}->{state}")
            if state in {
                CapitalReservationState.PENDING_EXCHANGE_REFLECTION,
                CapitalReservationState.CONSUMED,
            } and exchange_commitment_at is None:
                raise ValueError(f"{state.value} requires exchange_commitment_at")
            self._connection.execute(
                """UPDATE runtime.capital_reservations
                      SET state=%s,
                          state_reason=coalesce(%s,state_reason),
                          exchange_commitment_ref=coalesce(%s,exchange_commitment_ref),
                          exchange_commitment_at=coalesce(%s,exchange_commitment_at)
                    WHERE reservation_id=%s""",
                (
                    state.value,
                    state_reason,
                    exchange_commitment_ref,
                    None
                    if exchange_commitment_at is None
                    else exchange_commitment_at.astimezone(UTC),
                    reservation_id,
                ),
            )
            updated = self._connection.execute(
                """SELECT reservation_id,account_ref,strategy_attempt_id,requested_amount,
                          amount_currency,capacity_snapshot_id,state,created_at,updated_at,
                          exchange_commitment_ref,exchange_commitment_at,
                          pre_dispatch_expires_at,state_reason
                     FROM runtime.capital_reservations
                    WHERE reservation_id=%s""",
                (reservation_id,),
            ).fetchone()
            assert updated is not None
            return self._reservation_from_short_row(updated)

    @staticmethod
    def _validate_existing(
        row: Sequence[object],
        request: CapitalReservationRequest,
    ) -> None:
        existing_expiry = row[11]
        if not isinstance(existing_expiry, datetime):
            raise ValueError("capital reservation pre_dispatch_expires_at is invalid")
        actual = (
            str(row[1]),
            str(row[2]),
            Decimal(str(row[3])),
            str(row[4]),
            str(row[5]),
            existing_expiry.astimezone(UTC),
            str(row[13]),
            str(row[14]),
            str(row[15]),
            str(row[16]),
            str(row[17]),
            None if row[18] is None else str(row[18]),
            None if row[19] is None else str(row[19]),
            None if row[20] is None else str(row[20]),
        )
        expected = (
            request.account_ref,
            request.strategy_attempt_id,
            request.requested_amount,
            request.amount_currency.upper(),
            request.capacity_snapshot_id,
            request.pre_dispatch_expires_at.astimezone(UTC),
            request.strategy_id,
            request.strategy_version,
            request.strategy_config_fingerprint,
            request.entry_plan_fingerprint,
            request.signal_id,
            request.strategy_execution_binding_id,
            request.execution_leg,
            request.trading_account_id,
        )
        if actual != expected:
            raise ValueError("capital reservation identity collision")

    @staticmethod
    def _reservation_from_row(row: Sequence[object]) -> CapitalReservation:
        return CapitalReservation(
            reservation_id=str(row[0]),
            account_ref=str(row[1]),
            strategy_attempt_id=str(row[2]),
            requested_amount=Decimal(str(row[3])),
            amount_currency=str(row[4]),
            capacity_snapshot_id=str(row[5]),
            state=CapitalReservationState(str(row[6])),
            created_at=row[7],  # type: ignore[arg-type]
            updated_at=row[8],  # type: ignore[arg-type]
            pre_dispatch_expires_at=row[11],  # type: ignore[arg-type]
            state_reason=None if row[12] is None else str(row[12]),
            exchange_commitment_ref=None if row[9] is None else str(row[9]),
            exchange_commitment_at=row[10],  # type: ignore[arg-type]
            strategy_execution_binding_id=(
                None if len(row) <= 18 or row[18] is None else str(row[18])
            ),
            execution_leg=None if len(row) <= 19 or row[19] is None else str(row[19]),
            trading_account_id=(
                None if len(row) <= 20 or row[20] is None else str(row[20])
            ),
        )

    @classmethod
    def _reservation_from_short_row(cls, row: Sequence[object]) -> CapitalReservation:
        return cls._reservation_from_row(row)
