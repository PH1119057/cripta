"""Durable, account-explicit P90L90 paper Exit checkpoint (NO exchange orders).

This is a fail-closed state codec for a future PostgreSQL PaperTradeRuntime
consumer.  It does not approve capital, construct a paper Entry, choose the
unresolved SECOND48 cross-account domain, or modify any database.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .p90l90_causal import P90L90CausalExit, P90L90PaperClose, Side

_SCHEMA = "P90L90_PAPER_EXIT_CHECKPOINT_V1"
_FIVE_MINUTES = timedelta(minutes=5)
_MAX_EXEC_IDS_PER_SEQUENCE = 1000


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _positive_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a decimal string")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} is not decimal") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{label} must be positive and finite")
    return result


def _nonnegative_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a decimal string")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} is not decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{label} must be nonnegative and finite")
    return result


def _utc_time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not ISO timestamp") from exc
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must have explicit UTC timezone")
    return result.astimezone(UTC)


def _identity(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label} must be a nonempty exact identity")
    return value


def _exit_payload(exit_state: P90L90CausalExit) -> dict[str, object]:
    return {
        "side": exit_state.side,
        "entry_price": str(exit_state.entry_price),
        "entry_sequence": exit_state.entry_sequence,
        "state_bucket": exit_state.state_bucket,
        "stop": str(exit_state.stop),
        "best_favorable_pct": str(exit_state.best_favorable_pct),
        "protected": exit_state.protected,
        "target": None if exit_state.target is None else str(exit_state.target),
        "target_live_after_sequence": exit_state.target_live_after_sequence,
        "last_sequence": exit_state.last_sequence,
        "closed": exit_state.closed,
    }


def _restore_exit(raw: object) -> P90L90CausalExit:
    if not isinstance(raw, Mapping) or set(raw) != {
        "side", "entry_price", "entry_sequence", "state_bucket", "stop",
        "best_favorable_pct", "protected", "target", "target_live_after_sequence",
        "last_sequence", "closed",
    }:
        raise ValueError("P90L90 paper Exit snapshot schema mismatch")
    side = raw["side"]
    if side not in ("LONG", "SHORT"):
        raise ValueError("invalid P90L90 paper Exit side")
    entry_price = _positive_decimal(raw["entry_price"], "entry_price")
    entry_sequence = _integer(raw["entry_sequence"], "entry_sequence")
    bucket = _integer(raw["state_bucket"], "state_bucket")
    result = P90L90CausalExit(
        side=side,  # type: ignore[arg-type]
        entry_price=entry_price,
        entry_sequence=entry_sequence,
        state_bucket=bucket,
    )
    stop = _positive_decimal(raw["stop"], "stop")
    best = _nonnegative_decimal(raw["best_favorable_pct"], "best_favorable_pct")
    protected = raw["protected"]
    closed = raw["closed"]
    if not isinstance(protected, bool) or not isinstance(closed, bool):
        raise ValueError("P90L90 paper Exit flags must be boolean")
    last_sequence = _integer(raw["last_sequence"], "last_sequence", minimum=entry_sequence)
    target_sequence = _integer(
        raw["target_live_after_sequence"] + 1
        if type(raw["target_live_after_sequence"]) is int else None,
        "target_live_after_sequence+1",
    ) - 1
    target = (
        None if raw["target"] is None
        else _positive_decimal(raw["target"], "target")
    )
    if (target is None) != (target_sequence == -1):
        raise ValueError("P90L90 target and target-live sequence disagree")
    if target is not None and not entry_sequence <= target_sequence <= last_sequence:
        raise ValueError("P90L90 target-live sequence outside position lifetime")
    initial = result.stop
    expected = result._threshold(best - Decimal("0.35"))
    if protected:
        if best < Decimal("0.5") or stop != expected:
            raise ValueError("P90L90 protected stop is not causal TRAIL035")
    elif best >= Decimal("0.5") or stop != initial:
        raise ValueError("P90L90 unprotected stop is not initial HARD_STOP")
    result.stop = stop
    result.best_favorable_pct = best
    result.protected = protected
    result.target = target
    result.target_live_after_sequence = target_sequence
    result.last_sequence = last_sequence
    result.closed = closed
    return result


@dataclass(slots=True)
class P90L90PaperExitCheckpoint:
    """Per-position, per-account checkpoint with native trade replay dedupe.

    Exchange seq can be shared by several exec IDs. Persist the full set for
    the current seq so a restart cannot replay an earlier ID in that batch.
    The local Exit sequence is independent for every paper position.
    """

    trading_account_id: str
    strategy_id: str
    paper_position_id: str
    symbol: str
    exit_state: P90L90CausalExit
    last_exchange_sequence: int | None = None
    last_trade_at: datetime | None = None
    seen_exec_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        for label in ("trading_account_id", "strategy_id", "paper_position_id", "symbol"):
            _identity(getattr(self, label), label)
        if self.last_exchange_sequence is None:
            if self.last_trade_at is not None or self.seen_exec_ids:
                raise ValueError("trade replay cursor is incomplete")
        else:
            _integer(self.last_exchange_sequence, "last_exchange_sequence")
            if self.last_trade_at is None or not self.seen_exec_ids:
                raise ValueError("trade replay cursor is incomplete")
            if self.last_trade_at.tzinfo is None:
                raise ValueError("last_trade_at must be aware")
            if len(self.seen_exec_ids) > _MAX_EXEC_IDS_PER_SEQUENCE:
                raise ValueError("native trade sequence exceeds bounded dedupe capacity")
            for exec_id in self.seen_exec_ids:
                _identity(exec_id, "exec_id")

    def snapshot(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA,
            "trading_account_id": self.trading_account_id,
            "strategy_id": self.strategy_id,
            "paper_position_id": self.paper_position_id,
            "symbol": self.symbol,
            "exit": _exit_payload(self.exit_state),
            "last_exchange_sequence": self.last_exchange_sequence,
            "last_trade_at": (
                None if self.last_trade_at is None
                else self.last_trade_at.astimezone(UTC).isoformat()
            ),
            "seen_exec_ids": sorted(self.seen_exec_ids),
        }

    @classmethod
    def restore(
        cls,
        payload: Mapping[str, Any],
        *,
        trading_account_id: str,
        strategy_id: str,
        paper_position_id: str,
        symbol: str,
        side: Side,
        entry_price: Decimal,
    ) -> P90L90PaperExitCheckpoint:
        if set(payload) != {
            "schema", "trading_account_id", "strategy_id", "paper_position_id",
            "symbol", "exit", "last_exchange_sequence", "last_trade_at",
            "seen_exec_ids",
        } or payload["schema"] != _SCHEMA:
            raise ValueError("P90L90 checkpoint schema mismatch")
        for key, expected in (
            ("trading_account_id", trading_account_id),
            ("strategy_id", strategy_id),
            ("paper_position_id", paper_position_id),
            ("symbol", symbol),
        ):
            if payload[key] != _identity(expected, key):
                raise ValueError(f"P90L90 checkpoint {key} mismatch")
        state = _restore_exit(payload["exit"])
        if state.side != side or state.entry_price != entry_price:
            raise ValueError("P90L90 checkpoint position side/entry mismatch")
        exchange_seq_raw = payload["last_exchange_sequence"]
        exchange_seq = (
            None if exchange_seq_raw is None
            else _integer(exchange_seq_raw, "last_exchange_sequence")
        )
        trade_at_raw = payload["last_trade_at"]
        trade_at = (
            None if trade_at_raw is None
            else _utc_time(trade_at_raw, "last_trade_at")
        )
        seen = payload["seen_exec_ids"]
        if not isinstance(seen, list) or any(
            not isinstance(value, str) for value in seen
        ) or len(seen) != len(set(seen)):
            raise ValueError("P90L90 exec ID set invalid")
        return cls(
            trading_account_id=trading_account_id,
            strategy_id=strategy_id,
            paper_position_id=paper_position_id,
            symbol=symbol,
            exit_state=state,
            last_exchange_sequence=exchange_seq,
            last_trade_at=trade_at,
            seen_exec_ids=set(seen),
        )

    def on_native_trade(
        self,
        *,
        exec_id: str,
        exchange_sequence: int,
        event_at: datetime,
        price: Decimal,
        geometry_effective_at: datetime | None,
        candidate_opposite_raw_tp: Decimal | None,
    ) -> tuple[bool, P90L90PaperClose | None]:
        """Return (accepted_new_trade, close). Caller persists snapshot atomically.

        A missing closed 5m geometry at a new bucket is an error, not a
        guessed target. Never use current/open candles or synthetic ticks.
        """
        _identity(exec_id, "exec_id")
        seq = _integer(exchange_sequence, "exchange_sequence")
        if event_at.tzinfo is None:
            raise ValueError("native event_at must be aware")
        when = event_at.astimezone(UTC)
        if self.last_exchange_sequence is not None:
            if seq < self.last_exchange_sequence:
                raise ValueError("P90L90 native trade sequence regressed")
            if seq == self.last_exchange_sequence and exec_id in self.seen_exec_ids:
                return False, None
            if self.last_trade_at is not None and when < self.last_trade_at:
                raise ValueError("P90L90 native trade event time regressed")
        if (seq == self.last_exchange_sequence
                and len(self.seen_exec_ids) >= _MAX_EXEC_IDS_PER_SEQUENCE):
            raise ValueError("P90L90 native sequence dedupe capacity exceeded")
        if self.exit_state.closed:
            raise ValueError("P90L90 paper position already closed")
        bucket = int(when.timestamp() // _FIVE_MINUTES.total_seconds())
        if bucket < self.exit_state.state_bucket:
            raise ValueError("P90L90 paper state bucket regressed")
        if bucket > self.exit_state.state_bucket:
            if geometry_effective_at is None or geometry_effective_at.tzinfo is None:
                raise ValueError("P90L90 missing causal closed 5m geometry")
            expected = datetime.fromtimestamp(
                bucket * int(_FIVE_MINUTES.total_seconds()), UTC
            )
            if geometry_effective_at.astimezone(UTC) != expected:
                raise ValueError("P90L90 stale/future 5m geometry")
            if candidate_opposite_raw_tp is None or (
                not candidate_opposite_raw_tp.is_finite()
                or candidate_opposite_raw_tp <= 0
            ):
                raise ValueError("P90L90 missing positive opposite RAW target")
        close = self.exit_state.on_trade(
            price=price,
            sequence=self.exit_state.last_sequence + 1,
            bucket=bucket,
            candidate_opposite_raw_tp=candidate_opposite_raw_tp,
        )
        if seq != self.last_exchange_sequence:
            self.seen_exec_ids.clear()
        self.seen_exec_ids.add(exec_id)
        if len(self.seen_exec_ids) > _MAX_EXEC_IDS_PER_SEQUENCE:
            raise ValueError("P90L90 native sequence dedupe capacity exceeded")
        self.last_exchange_sequence = seq
        self.last_trade_at = when
        return True, close
