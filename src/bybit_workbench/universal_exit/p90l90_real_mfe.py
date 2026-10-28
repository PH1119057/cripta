"""P90L90 REAL ordered after-fill MFE checkpoint, independent of PAPER state.

This is a pure, restart-serializable calculation. It accepts ONLY original
native publicTrade cursors after an externally verified actual Exchange fill.
The caller must prove complete source coverage from the fill, sign/account
lineage and atomically persist snapshots with its source checkpoint. This
module does not manufacture that evidence or confer LIVE readiness.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import TradeDirection
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

_SCHEMA = "P90_REAL_POSTFILL_MFE_CHECKPOINT_V1"
_SHA = re.compile(r"^[0-9a-f]{40}$")


class P90RealMfeUnverified(ValueError):
    """The after-fill native price stream or physical position is not proven."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise P90RealMfeUnverified("P90_REAL_MFE_TIME_NAIVE")
    return value.astimezone(UTC)


def _positive(value: object) -> Decimal:
    try:
        number = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise P90RealMfeUnverified("P90_REAL_MFE_PRICE_INVALID") from None
    if not number.is_finite() or number <= 0:
        raise P90RealMfeUnverified("P90_REAL_MFE_PRICE_INVALID")
    return number


def _proof(position: StrategyPosition, source_commit: str) -> None:
    if (
        position.strategy_id not in ALLOWED_SYMBOLS
        or position.strategy_version != VERSION
        or position.symbol not in ALLOWED_SYMBOLS[position.strategy_id]
        or position.identity_status != "PROVEN"
        or position.trading_account_id is None
        or position.strategy_execution_binding_id is None
        or position.position_idx != 0
        or position.exchange_position_key != exchange_position_key(
            position.trading_account_id, position.symbol, 0,
        )
        or position.initial_protection_confirmed_at is None
        or position.initial_protection_confirmed_at < position.fill_at
        or _SHA.fullmatch(source_commit) is None
    ):
        raise P90RealMfeUnverified("P90_REAL_MFE_EXACT_FILL_OR_PROTECTION_UNPROVEN")


@dataclass(frozen=True, slots=True)
class P90RealMfeCheckpoint:
    """An immutable cursor from a *verified* fill-forward native source epoch."""

    position_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    account_id: str
    binding_id: str
    exchange_position_key: str
    symbol: str
    direction: TradeDirection
    fill_at: datetime
    fill_price: Decimal
    source_commit: str
    best_favorable_price: Decimal
    last_native_price: Decimal
    cursor_seq: int
    cursor_exec_id: str
    cursor_at: datetime

    @classmethod
    def begin(
        cls,
        *,
        position: StrategyPosition,
        source_commit: str,
        first_cursor: TradeCursor,
        first_price: Decimal,
        verified_fill_to_first_trade_coverage: bool,
    ) -> P90RealMfeCheckpoint:
        """Start ONLY after a separate auditor proves no missing after-fill tick.

        No caller may bootstrap from the first convenient market tick merely
        because a private position now appears open. Gap means fail closed.
        """
        _proof(position, source_commit)
        first = _positive(first_price)
        if (
            verified_fill_to_first_trade_coverage is not True
            or first_cursor.symbol != position.symbol
            or not first_cursor.exec_id
            or type(first_cursor.seq) is not int
            or first_cursor.seq < 0
            or _utc(first_cursor.traded_at) < _utc(position.fill_at)
        ):
            raise P90RealMfeUnverified("P90_REAL_MFE_FILL_TO_NATIVE_GAP_UNPROVEN")
        baseline = _positive(position.actual_avg_fill)
        best = max(baseline, first) if position.direction is TradeDirection.LONG else (
            min(baseline, first)
        )
        return cls(
            position_id=position.strategy_position_id,
            strategy_id=position.strategy_id,
            strategy_version=position.strategy_version,
            strategy_config_fingerprint=position.strategy_config_fingerprint,
            account_id=position.trading_account_id or "",
            binding_id=position.strategy_execution_binding_id or "",
            exchange_position_key=position.exchange_position_key,
            symbol=position.symbol,
            direction=position.direction,
            fill_at=_utc(position.fill_at),
            fill_price=baseline,
            source_commit=source_commit,
            best_favorable_price=best,
            last_native_price=first,
            cursor_seq=first_cursor.seq,
            cursor_exec_id=first_cursor.exec_id,
            cursor_at=_utc(first_cursor.traded_at),
        )

    def advance(
        self,
        *,
        position: StrategyPosition,
        cursor: TradeCursor,
        price: Decimal,
        proven_contiguous_native_cursor: bool,
    ) -> P90RealMfeCheckpoint:
        """Return a new state, or the identical checkpoint for an exact replay.

        Same-sequence different executions are supported only when their
        timestamp proves strict causal order; ambiguous batching cannot
        change stop/TP decisions. Continuity is a separate source proof.
        """
        _proof(position, self.source_commit)
        if (
            self.position_id != position.strategy_position_id
            or self.account_id != position.trading_account_id
            or self.binding_id != position.strategy_execution_binding_id
            or self.exchange_position_key != position.exchange_position_key
            or self.strategy_id != position.strategy_id
            or self.strategy_version != position.strategy_version
            or self.strategy_config_fingerprint
            != position.strategy_config_fingerprint
            or self.fill_at != _utc(position.fill_at)
            or self.fill_price != position.actual_avg_fill
            or self.direction is not position.direction
            or self.symbol != cursor.symbol
            or self.symbol != position.symbol
        ):
            raise P90RealMfeUnverified("P90_REAL_MFE_POSITION_OR_ACCOUNT_DRIFT")
        if (
            proven_contiguous_native_cursor is not True
            or not cursor.exec_id
            or type(cursor.seq) is not int
            or cursor.seq < 0
        ):
            raise P90RealMfeUnverified("P90_REAL_MFE_NATIVE_CURSOR_GAP")
        at = _utc(cursor.traded_at)
        native_price = _positive(price)
        if (
            cursor.seq == self.cursor_seq
            and cursor.exec_id == self.cursor_exec_id
            and at == self.cursor_at
            and native_price == self.last_native_price
        ):
            return self
        if (
            cursor.seq < self.cursor_seq
            or at < self.cursor_at
            or (cursor.seq == self.cursor_seq and at <= self.cursor_at)
            or (cursor.seq == self.cursor_seq and cursor.exec_id == self.cursor_exec_id)
        ):
            raise P90RealMfeUnverified("P90_REAL_MFE_NATIVE_ORDER_NOT_PROVEN")
        best = (
            max(self.best_favorable_price, native_price)
            if self.direction is TradeDirection.LONG
            else min(self.best_favorable_price, native_price)
        )
        return replace(
            self,
            best_favorable_price=best,
            last_native_price=native_price,
            cursor_seq=cursor.seq,
            cursor_exec_id=cursor.exec_id,
            cursor_at=at,
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA,
            "position_id": self.position_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_config_fingerprint": self.strategy_config_fingerprint,
            "account_id": self.account_id,
            "binding_id": self.binding_id,
            "exchange_position_key": self.exchange_position_key,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "fill_at": self.fill_at.isoformat(),
            "fill_price": str(self.fill_price),
            "source_commit": self.source_commit,
            "best_favorable_price": str(self.best_favorable_price),
            "last_native_price": str(self.last_native_price),
            "cursor_seq": self.cursor_seq,
            "cursor_exec_id": self.cursor_exec_id,
            "cursor_at": self.cursor_at.isoformat(),
        }

    @classmethod
    def restore(cls, raw: object) -> P90RealMfeCheckpoint:
        if not isinstance(raw, dict) or set(raw) != set(cls._fields()):
            raise P90RealMfeUnverified("P90_REAL_MFE_SNAPSHOT_SCHEMA_INVALID")
        if raw.get("schema") != _SCHEMA:
            raise P90RealMfeUnverified("P90_REAL_MFE_SNAPSHOT_VERSION_INVALID")
        try:
            direction = TradeDirection(raw["direction"])
            fill_at = datetime.fromisoformat(raw["fill_at"])
            cursor_at = datetime.fromisoformat(raw["cursor_at"])
        except (TypeError, ValueError, KeyError) as exc:
            raise P90RealMfeUnverified("P90_REAL_MFE_SNAPSHOT_TIME_OR_SIDE") from exc
        if (
            fill_at.tzinfo is None or fill_at.utcoffset() != timedelta()
            or cursor_at.tzinfo is None or cursor_at.utcoffset() != timedelta()
            or cursor_at < fill_at
            or not isinstance(raw["cursor_seq"], int)
            or isinstance(raw["cursor_seq"], bool)
            or raw["cursor_seq"] < 0
            or not isinstance(raw["cursor_exec_id"], str)
            or not raw["cursor_exec_id"]
            or not isinstance(raw["source_commit"], str)
            or _SHA.fullmatch(raw["source_commit"]) is None
        ):
            raise P90RealMfeUnverified("P90_REAL_MFE_SNAPSHOT_CURSOR_INVALID")
        value = cls(
            position_id=str(raw["position_id"]),
            strategy_id=str(raw["strategy_id"]),
            strategy_version=str(raw["strategy_version"]),
            strategy_config_fingerprint=str(raw["strategy_config_fingerprint"]),
            account_id=str(raw["account_id"]),
            binding_id=str(raw["binding_id"]),
            exchange_position_key=str(raw["exchange_position_key"]),
            symbol=str(raw["symbol"]),
            direction=direction,
            fill_at=fill_at,
            fill_price=_positive(raw["fill_price"]),
            source_commit=raw["source_commit"],
            best_favorable_price=_positive(raw["best_favorable_price"]),
            last_native_price=_positive(raw["last_native_price"]),
            cursor_seq=raw["cursor_seq"],
            cursor_exec_id=raw["cursor_exec_id"],
            cursor_at=cursor_at,
        )
        if (
            value.strategy_id not in ALLOWED_SYMBOLS
            or value.strategy_version != VERSION
            or value.symbol not in ALLOWED_SYMBOLS[value.strategy_id]
            or not value.account_id or not value.binding_id
            or not value.position_id or not value.strategy_config_fingerprint
            or value.exchange_position_key != exchange_position_key(
                value.account_id, value.symbol, 0,
            )
            or (
                value.best_favorable_price < value.fill_price
                if value.direction is TradeDirection.LONG
                else value.best_favorable_price > value.fill_price
            )
        ):
            raise P90RealMfeUnverified("P90_REAL_MFE_SNAPSHOT_IDENTITY_INVALID")
        return value

    @staticmethod
    def _fields() -> tuple[str, ...]:
        return (
            "schema", "position_id", "strategy_id", "strategy_version",
            "strategy_config_fingerprint", "account_id", "binding_id",
            "exchange_position_key", "symbol", "direction", "fill_at",
            "fill_price", "source_commit", "best_favorable_price",
            "last_native_price", "cursor_seq", "cursor_exec_id", "cursor_at",
        )
