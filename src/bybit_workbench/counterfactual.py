from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol

from bybit_workbench.universal_entry.contracts import (
    EntryDecisionCode,
    EntryEvaluation,
    EntryPlan,
    ExitPlan,
    FrozenPolicy,
    NotificationKind,
    TradeDirection,
)
from bybit_workbench.universal_entry.fingerprint import fingerprint


class CursorLike(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Mapping[str, object] | Sequence[object] | None: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...

    def transaction(self) -> AbstractContextManager[object]: ...


def _row_values(
    row: Mapping[str, object] | Sequence[object],
    keys: tuple[str, ...],
) -> tuple[object, ...]:
    if isinstance(row, Mapping):
        return tuple(row[key] for key in keys)
    return tuple(row)


class CounterfactualOutcomeStatus(StrEnum):
    NO_ENTRY = "NO_ENTRY"
    OPEN_AT_CUTOFF = "OPEN_AT_CUTOFF"
    CLOSED = "CLOSED"


class CounterfactualEconomicsStatus(StrEnum):
    NO_ENTRY = "NO_ENTRY"
    OPEN_UNREALIZED = "OPEN_UNREALIZED"
    PARTIAL_NO_FUNDING = "PARTIAL_NO_FUNDING"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True, slots=True)
class CounterfactualCandidate:
    counterfactual_id: str
    strategy_activation_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    entry_plan_fingerprint: str
    exit_plan_fingerprint: str
    signal_id: str
    strategy_attempt_id: str
    entry_decision_id: str
    symbol: str
    direction: TradeDirection
    decided_at: datetime
    captured_at: datetime
    requested_amount: Decimal
    amount_currency: str | None
    capacity_snapshot_id: str | None
    reported_available_amount: Decimal | None
    decision_code: EntryDecisionCode
    decision_reason: str
    evidence: FrozenPolicy

    def __post_init__(self) -> None:
        if self.decided_at.tzinfo is None or self.captured_at.tzinfo is None:
            raise ValueError("counterfactual timestamps must be timezone-aware")
        if self.captured_at < self.decided_at:
            raise ValueError("counterfactual capture cannot precede EntryDecision")
        if self.requested_amount <= 0:
            raise ValueError("counterfactual requested_amount must be positive")
        if self.reported_available_amount is not None and self.reported_available_amount < 0:
            raise ValueError("counterfactual reported_available_amount cannot be negative")


@dataclass(frozen=True, slots=True)
class CounterfactualOutcome:
    outcome_id: str
    counterfactual_id: str
    status: CounterfactualOutcomeStatus
    evaluated_at: datetime
    opened_at: datetime | None
    entry_price: Decimal | None
    closed_at: datetime | None
    exit_price: Decimal | None
    exit_reason: str | None
    gross_pnl: Decimal | None
    fees: Decimal | None
    funding: Decimal | None
    slippage: Decimal | None
    net_pnl_after_fees: Decimal | None
    economics_status: CounterfactualEconomicsStatus
    evidence: FrozenPolicy

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None:
            raise ValueError("counterfactual outcome evaluated_at must be timezone-aware")
        if self.opened_at is not None and self.opened_at.tzinfo is None:
            raise ValueError("counterfactual opened_at must be timezone-aware")
        if self.closed_at is not None and self.closed_at.tzinfo is None:
            raise ValueError("counterfactual closed_at must be timezone-aware")
        if self.entry_price is not None and self.entry_price <= 0:
            raise ValueError("counterfactual entry_price must be positive")
        if self.exit_price is not None and self.exit_price <= 0:
            raise ValueError("counterfactual exit_price must be positive")
        if self.status is CounterfactualOutcomeStatus.NO_ENTRY:
            if any(
                value is not None
                for value in (
                    self.opened_at,
                    self.entry_price,
                    self.closed_at,
                    self.exit_price,
                )
            ):
                raise ValueError("NO_ENTRY counterfactual cannot carry fill/close fields")
        elif self.status is CounterfactualOutcomeStatus.OPEN_AT_CUTOFF:
            if (
                self.opened_at is None
                or self.entry_price is None
                or self.closed_at is not None
                or self.exit_price is not None
            ):
                raise ValueError("OPEN_AT_CUTOFF counterfactual fields are inconsistent")
        elif self.status is CounterfactualOutcomeStatus.CLOSED:
            if (
                self.opened_at is None
                or self.entry_price is None
                or self.closed_at is None
                or self.exit_price is None
            ):
                raise ValueError("CLOSED counterfactual requires exact open/close fields")
            if self.closed_at < self.opened_at:
                raise ValueError("counterfactual close cannot precede open")

    @classmethod
    def build(
        cls,
        *,
        counterfactual_id: str,
        status: CounterfactualOutcomeStatus,
        evaluated_at: datetime,
        opened_at: datetime | None = None,
        entry_price: Decimal | None = None,
        closed_at: datetime | None = None,
        exit_price: Decimal | None = None,
        exit_reason: str | None = None,
        gross_pnl: Decimal | None = None,
        fees: Decimal | None = None,
        funding: Decimal | None = None,
        slippage: Decimal | None = None,
        net_pnl_after_fees: Decimal | None = None,
        economics_status: CounterfactualEconomicsStatus,
        evidence: FrozenPolicy | None = None,
    ) -> CounterfactualOutcome:
        outcome_id = (
            "counterfactual-outcome-"
            + fingerprint(
                {
                    "counterfactual_id": counterfactual_id,
                    "status": status.value,
                    "evaluated_at": evaluated_at,
                }
            )[:32]
        )
        return cls(
            outcome_id=outcome_id,
            counterfactual_id=counterfactual_id,
            status=status,
            evaluated_at=evaluated_at.astimezone(UTC),
            opened_at=None if opened_at is None else opened_at.astimezone(UTC),
            entry_price=entry_price,
            closed_at=None if closed_at is None else closed_at.astimezone(UTC),
            exit_price=exit_price,
            exit_reason=exit_reason,
            gross_pnl=gross_pnl,
            fees=fees,
            funding=funding,
            slippage=slippage,
            net_pnl_after_fees=net_pnl_after_fees,
            economics_status=economics_status,
            evidence=evidence or FrozenPolicy.from_mapping({}),
        )


def _positive_decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} must be decimal") from None
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def build_admission_counterfactual_candidate(
    evaluation: EntryEvaluation,
    *,
    entry_plan: EntryPlan,
    exit_plan: ExitPlan,
    captured_at: datetime,
) -> CounterfactualCandidate | None:
    decision = evaluation.decision
    if decision is None:
        return None
    allowed = {
        EntryDecisionCode.INSUFFICIENT_AVAILABLE_FUNDS:
            NotificationKind.INSUFFICIENT_AVAILABLE_FUNDS,
        EntryDecisionCode.EXCHANGE_POSITION_OWNERSHIP_CONFLICT:
            NotificationKind.EXCHANGE_POSITION_OWNERSHIP_CONFLICT,
    }
    expected_notice_kind = allowed.get(decision.code)
    if expected_notice_kind is None:
        return None
    if evaluation.execution_request is not None:
        raise RuntimeError("blocked admission counterfactual cannot have ExecutionRequest")
    if decision.capital_reservation_id is not None:
        raise RuntimeError("blocked admission counterfactual cannot keep capital reservation")
    if decision.exchange_position_slot_claim_id is not None:
        raise RuntimeError("blocked admission counterfactual cannot keep physical slot claim")

    signal = evaluation.signal
    attempt = evaluation.attempt
    entry_identity = (
        entry_plan.strategy_id,
        entry_plan.strategy_version,
        entry_plan.strategy_config_fingerprint,
        entry_plan.strategy_activation_id,
        entry_plan.entry_plan_fingerprint,
    )
    signal_identity = (
        signal.strategy_id,
        signal.strategy_version,
        signal.strategy_config_fingerprint,
        signal.strategy_activation_id,
        signal.entry_plan_fingerprint,
    )
    attempt_identity = (
        attempt.strategy_id,
        attempt.strategy_version,
        attempt.strategy_config_fingerprint,
        attempt.strategy_activation_id,
        attempt.entry_plan_fingerprint,
    )
    exit_identity = (
        exit_plan.strategy_id,
        exit_plan.strategy_version,
        exit_plan.strategy_config_fingerprint,
        exit_plan.strategy_activation_id,
    )
    if signal_identity != entry_identity or attempt_identity != entry_identity:
        raise RuntimeError("counterfactual Entry lineage mismatch")
    if exit_identity != entry_identity[:4]:
        raise RuntimeError("counterfactual ExitPlan lineage mismatch")
    if attempt.signal_id != signal.signal_id:
        raise RuntimeError("counterfactual attempt/signal mismatch")
    if (
        decision.strategy_attempt_id != attempt.strategy_attempt_id
        or decision.signal_id != signal.signal_id
    ):
        raise RuntimeError("counterfactual EntryDecision lineage mismatch")

    capital = entry_plan.capital_policy.to_dict()
    requested_amount = _positive_decimal(
        capital.get("requested_amount"),
        "EntryPlan.capital_policy.requested_amount",
    )
    amount_currency_raw = str(capital.get("amount_currency") or "").strip().upper()
    amount_currency = amount_currency_raw or None

    notices = tuple(
        notice
        for notice in evaluation.notifications
        if notice.kind is expected_notice_kind
    )
    if len(notices) != 1:
        raise RuntimeError("admission counterfactual requires exact block notification")
    notice = notices[0]
    if notice.requested_amount is not None and notice.requested_amount != requested_amount:
        raise RuntimeError("counterfactual requested amount differs from EntryPlan")

    captured = captured_at.astimezone(UTC)
    candidate_id = (
        "counterfactual-"
        + fingerprint(
            {
                "entry_decision_id": decision.entry_decision_id,
                "entry_plan_fingerprint": entry_plan.entry_plan_fingerprint,
                "exit_plan_fingerprint": exit_plan.exit_plan_fingerprint,
                "decision_code": decision.code.value,
            }
        )[:32]
    )
    return CounterfactualCandidate(
        counterfactual_id=candidate_id,
        strategy_activation_id=entry_plan.strategy_activation_id,
        strategy_id=entry_plan.strategy_id,
        strategy_version=entry_plan.strategy_version,
        strategy_config_fingerprint=entry_plan.strategy_config_fingerprint,
        entry_plan_fingerprint=entry_plan.entry_plan_fingerprint,
        exit_plan_fingerprint=exit_plan.exit_plan_fingerprint,
        signal_id=signal.signal_id,
        strategy_attempt_id=attempt.strategy_attempt_id,
        entry_decision_id=decision.entry_decision_id,
        symbol=signal.symbol,
        direction=signal.direction,
        decided_at=decision.decided_at.astimezone(UTC),
        captured_at=captured,
        requested_amount=requested_amount,
        amount_currency=amount_currency,
        capacity_snapshot_id=decision.capacity_snapshot_id,
        reported_available_amount=notice.available_amount,
        decision_code=decision.code,
        decision_reason=decision.reason,
        evidence=FrozenPolicy.from_mapping(
            {
                "notification_id": notice.notification_id,
                "notification_reason": notice.reason,
                "reported_requested_amount": (
                    None if notice.requested_amount is None else str(notice.requested_amount)
                ),
                "reported_available_amount": (
                    None if notice.available_amount is None else str(notice.available_amount)
                ),
                "counterfactual_block_reason": decision.code.value,
                "source": "universal_entry_admission_counterfactual",
            }
        ),
    )


def build_insufficient_funds_candidate(
    evaluation: EntryEvaluation,
    *,
    entry_plan: EntryPlan,
    exit_plan: ExitPlan,
    captured_at: datetime,
) -> CounterfactualCandidate | None:
    """Backward-compatible name; now supports both canonical admission block causes."""
    return build_admission_counterfactual_candidate(
        evaluation,
        entry_plan=entry_plan,
        exit_plan=exit_plan,
        captured_at=captured_at,
    )


class AnalystCounterfactualStore:
    """Append-only Analyst evidence with no reservation, Execution or Exchange rights."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def record_candidate(self, candidate: CounterfactualCandidate) -> None:
        with self._connection.transaction():
            self._connection.execute(
                """INSERT INTO analytics.counterfactual_candidates(
                       counterfactual_id,strategy_activation_id,strategy_id,
                       strategy_version,strategy_config_fingerprint,
                       entry_plan_fingerprint,exit_plan_fingerprint,signal_id,
                       strategy_attempt_id,entry_decision_id,symbol,direction,
                       decided_at,captured_at,decision_code,requested_amount,
                       amount_currency,capacity_snapshot_id,reported_available_amount,
                       decision_reason,evidence
                   ) VALUES(
                       %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       %s,%s,%s,%s,%s,%s,%s::jsonb
                   )
                   ON CONFLICT(counterfactual_id) DO NOTHING""",
                (
                    candidate.counterfactual_id,
                    candidate.strategy_activation_id,
                    candidate.strategy_id,
                    candidate.strategy_version,
                    candidate.strategy_config_fingerprint,
                    candidate.entry_plan_fingerprint,
                    candidate.exit_plan_fingerprint,
                    candidate.signal_id,
                    candidate.strategy_attempt_id,
                    candidate.entry_decision_id,
                    candidate.symbol,
                    candidate.direction.value,
                    candidate.decided_at,
                    candidate.captured_at,
                    candidate.decision_code.value,
                    candidate.requested_amount,
                    candidate.amount_currency,
                    candidate.capacity_snapshot_id,
                    candidate.reported_available_amount,
                    candidate.decision_reason,
                    candidate.evidence.payload_json,
                ),
            )
            row = self._connection.execute(
                """SELECT strategy_activation_id,strategy_id,strategy_version,
                          strategy_config_fingerprint,entry_plan_fingerprint,
                          exit_plan_fingerprint,signal_id,strategy_attempt_id,
                          entry_decision_id,symbol,direction,decided_at,captured_at,
                          decision_code,requested_amount,amount_currency,
                          capacity_snapshot_id,reported_available_amount,
                          decision_reason,evidence
                     FROM analytics.counterfactual_candidates
                    WHERE counterfactual_id=%s""",
                (candidate.counterfactual_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("counterfactual candidate persistence disappeared")
            expected = (
                candidate.strategy_activation_id,
                candidate.strategy_id,
                candidate.strategy_version,
                candidate.strategy_config_fingerprint,
                candidate.entry_plan_fingerprint,
                candidate.exit_plan_fingerprint,
                candidate.signal_id,
                candidate.strategy_attempt_id,
                candidate.entry_decision_id,
                candidate.symbol,
                candidate.direction.value,
                candidate.decided_at,
                candidate.captured_at,
                candidate.decision_code.value,
                candidate.requested_amount,
                candidate.amount_currency,
                candidate.capacity_snapshot_id,
                candidate.reported_available_amount,
                candidate.decision_reason,
                candidate.evidence.to_dict(),
            )
            actual = _row_values(
                row,
                (
                    "strategy_activation_id",
                    "strategy_id",
                    "strategy_version",
                    "strategy_config_fingerprint",
                    "entry_plan_fingerprint",
                    "exit_plan_fingerprint",
                    "signal_id",
                    "strategy_attempt_id",
                    "entry_decision_id",
                    "symbol",
                    "direction",
                    "decided_at",
                    "captured_at",
                    "decision_code",
                    "requested_amount",
                    "amount_currency",
                    "capacity_snapshot_id",
                    "reported_available_amount",
                    "decision_reason",
                    "evidence",
                ),
            )
            if actual != expected:
                raise RuntimeError("counterfactual candidate identity collision")

    def record_outcome(self, outcome: CounterfactualOutcome) -> None:
        with self._connection.transaction():
            candidate = self._connection.execute(
                """SELECT counterfactual_id
                     FROM analytics.counterfactual_candidates
                    WHERE counterfactual_id=%s""",
                (outcome.counterfactual_id,),
            ).fetchone()
            if candidate is None:
                raise KeyError(f"unknown counterfactual: {outcome.counterfactual_id}")
            self._connection.execute(
                """INSERT INTO analytics.counterfactual_outcomes(
                       outcome_id,counterfactual_id,status,evaluated_at,opened_at,
                       entry_price,closed_at,exit_price,exit_reason,gross_pnl,
                       fees,funding,slippage,net_pnl_after_fees,economics_status,evidence
                   ) VALUES(
                       %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
                   )
                   ON CONFLICT(outcome_id) DO NOTHING""",
                (
                    outcome.outcome_id,
                    outcome.counterfactual_id,
                    outcome.status.value,
                    outcome.evaluated_at,
                    outcome.opened_at,
                    outcome.entry_price,
                    outcome.closed_at,
                    outcome.exit_price,
                    outcome.exit_reason,
                    outcome.gross_pnl,
                    outcome.fees,
                    outcome.funding,
                    outcome.slippage,
                    outcome.net_pnl_after_fees,
                    outcome.economics_status.value,
                    outcome.evidence.payload_json,
                ),
            )
            row = self._connection.execute(
                """SELECT counterfactual_id,status,evaluated_at,opened_at,
                          entry_price,closed_at,exit_price,exit_reason,gross_pnl,
                          fees,funding,slippage,net_pnl_after_fees,economics_status,
                          evidence
                     FROM analytics.counterfactual_outcomes
                    WHERE outcome_id=%s""",
                (outcome.outcome_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("counterfactual outcome persistence disappeared")
            expected = (
                outcome.counterfactual_id,
                outcome.status.value,
                outcome.evaluated_at,
                outcome.opened_at,
                outcome.entry_price,
                outcome.closed_at,
                outcome.exit_price,
                outcome.exit_reason,
                outcome.gross_pnl,
                outcome.fees,
                outcome.funding,
                outcome.slippage,
                outcome.net_pnl_after_fees,
                outcome.economics_status.value,
                outcome.evidence.to_dict(),
            )
            actual = _row_values(
                row,
                (
                    "counterfactual_id",
                    "status",
                    "evaluated_at",
                    "opened_at",
                    "entry_price",
                    "closed_at",
                    "exit_price",
                    "exit_reason",
                    "gross_pnl",
                    "fees",
                    "funding",
                    "slippage",
                    "net_pnl_after_fees",
                    "economics_status",
                    "evidence",
                ),
            )
            if actual != expected:
                raise RuntimeError("counterfactual outcome identity collision")
