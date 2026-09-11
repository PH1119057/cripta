from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from threading import RLock

from .transport_continuity import ContinuityNotProvable, ReplayTrade, TradeCursor


@dataclass(frozen=True, slots=True)
class MirrorTrade:
    symbol: str
    exec_id: str
    seq: int
    traded_at: datetime
    price: str
    size: str
    side: str
    received_at: datetime
    ordinal: int

    def as_replay_trade(self) -> ReplayTrade:
        return ReplayTrade(
            symbol=self.symbol,
            exec_id=self.exec_id,
            seq=self.seq,
            traded_at=self.traded_at,
            price=self.price,
            size=self.size,
            side=self.side,
        )


class PublicTradeMirrorBuffer:
    """Exact received-order buffer for one continuous publicTrade mirror epoch."""

    def __init__(
        self,
        required_symbols: Sequence[str],
        *,
        retention_seconds: int,
    ) -> None:
        normalized = tuple(str(symbol).strip().upper() for symbol in required_symbols)
        if not normalized or any(not symbol for symbol in normalized):
            raise ValueError("required_symbols must be non-empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("required_symbols must be unique")
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        self.required_symbols = normalized
        self.retention_seconds = retention_seconds
        self._lock = RLock()
        self._events: deque[MirrorTrade] = deque()
        self._exec_ids: set[str] = set()
        self._state = "INITIALIZING"
        self._epoch = 0
        self._epoch_started_at: datetime | None = None
        self._last_received_at: datetime | None = None
        self._last_error: str | None = None
        self._duplicates = 0
        self._ordinal = 0
        self._gaps = 0

    def start_epoch(self, started_at: datetime) -> int:
        if started_at.tzinfo is None:
            raise ValueError("mirror epoch timestamp must be timezone-aware")
        with self._lock:
            self._epoch += 1
            self._events.clear()
            self._exec_ids.clear()
            self._epoch_started_at = started_at.astimezone(UTC)
            self._last_received_at = None
            self._last_error = None
            self._state = "ACTIVE"
            self._ordinal = 0
            return self._epoch

    def mark_gap(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("mirror gap reason is required")
        with self._lock:
            self._state = "GAP"
            self._last_error = reason
            self._gaps += 1

    def _purge(self, now: datetime) -> None:
        cutoff = now.astimezone(UTC) - timedelta(seconds=self.retention_seconds)
        changed = False
        while self._events and self._events[0].received_at < cutoff:
            self._events.popleft()
            changed = True
        if changed:
            self._exec_ids = {event.exec_id for event in self._events}

    @staticmethod
    def _parse_trade(raw: Mapping[str, object], received_at: datetime, ordinal: int) -> MirrorTrade:
        symbol = str(raw.get("s") or "").strip().upper()
        exec_id = str(raw.get("i") or "").strip()
        side = str(raw.get("S") or "").strip()
        price = str(raw.get("p") or "").strip()
        size = str(raw.get("v") or "").strip()
        if not symbol or not exec_id or side not in {"Buy", "Sell"} or not price or not size:
            raise ContinuityNotProvable(
                "publicTrade mirror row lacks exact trade identity/semantics"
            )
        try:
            seq = int(str(raw["seq"]))
            traded_at = datetime.fromtimestamp(int(str(raw["T"])) / 1000, UTC)
            parsed_price = Decimal(price)
            parsed_size = Decimal(size)
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise ContinuityNotProvable("publicTrade mirror row is invalid") from exc
        if not parsed_price.is_finite() or parsed_price <= 0:
            raise ContinuityNotProvable("publicTrade mirror price is invalid")
        if not parsed_size.is_finite() or parsed_size < 0:
            raise ContinuityNotProvable("publicTrade mirror size is invalid")
        return MirrorTrade(
            symbol=symbol,
            exec_id=exec_id,
            seq=seq,
            traded_at=traded_at,
            price=price,
            size=size,
            side=side,
            received_at=received_at.astimezone(UTC),
            ordinal=ordinal,
        )

    def record_message(self, message: Mapping[str, object], *, received_at: datetime) -> int:
        if received_at.tzinfo is None:
            raise ValueError("mirror receive timestamp must be timezone-aware")
        topic = str(message.get("topic") or "")
        if not topic.startswith("publicTrade."):
            return 0
        raw_data = message.get("data")
        if not isinstance(raw_data, list):
            raise ContinuityNotProvable("publicTrade mirror message data is not a list")
        accepted = 0
        with self._lock:
            if self._state != "ACTIVE":
                raise ContinuityNotProvable("publicTrade mirror epoch is not active")
            now = received_at.astimezone(UTC)
            self._purge(now)
            for raw in raw_data:
                if not isinstance(raw, Mapping):
                    raise ContinuityNotProvable("publicTrade mirror message contains invalid row")
                self._ordinal += 1
                trade = self._parse_trade(raw, now, self._ordinal)
                if trade.symbol not in self.required_symbols:
                    continue
                if trade.exec_id in self._exec_ids:
                    self._duplicates += 1
                    continue
                self._events.append(trade)
                self._exec_ids.add(trade.exec_id)
                accepted += 1
            self._last_received_at = now
        return accepted

    def current_cursors(self) -> tuple[dict[str, TradeCursor], dict[str, set[str]]]:
        with self._lock:
            if self._state != "ACTIVE":
                return {}, {}
            latest: dict[str, MirrorTrade] = {}
            for event in self._events:
                latest[event.symbol] = event
            cursors = {
                symbol: TradeCursor(
                    symbol=event.symbol,
                    exec_id=event.exec_id,
                    seq=event.seq,
                    traded_at=event.traded_at,
                )
                for symbol, event in latest.items()
            }
            seq_ids: dict[str, set[str]] = {}
            for symbol, event in latest.items():
                seq_ids[symbol] = {
                    item.exec_id
                    for item in self._events
                    if item.symbol == symbol and item.seq == event.seq
                }
            return cursors, seq_ids

    def recover_after(
        self,
        cursors: Mapping[str, TradeCursor],
        *,
        cutoff_at: datetime,
    ) -> tuple[MirrorTrade, ...]:
        if cutoff_at.tzinfo is None:
            raise ValueError("mirror recovery cutoff must be timezone-aware")
        cutoff = cutoff_at.astimezone(UTC)
        with self._lock:
            if self._state != "ACTIVE":
                raise ContinuityNotProvable("publicTrade mirror continuity is not active")
            events = tuple(self._events)
            anchor_ordinals: dict[str, int] = {}
            for symbol, cursor in cursors.items():
                anchor = next(
                    (
                        event
                        for event in events
                        if event.symbol == symbol
                        and event.exec_id == cursor.exec_id
                        and event.seq == cursor.seq
                    ),
                    None,
                )
                if anchor is None:
                    raise ContinuityNotProvable(
                        f"mirror exact anchor missing for {symbol}: {cursor.exec_id}/{cursor.seq}"
                    )
                anchor_ordinals[symbol] = anchor.ordinal
            recovered: list[MirrorTrade] = []
            for event in events:
                anchor_ordinal = anchor_ordinals.get(event.symbol)
                if anchor_ordinal is None or event.ordinal <= anchor_ordinal:
                    continue
                if event.traded_at > cutoff:
                    continue
                cursor = cursors[event.symbol]
                if event.seq < cursor.seq:
                    raise ContinuityNotProvable(
                        f"publicTrade mirror sequence regressed for {event.symbol}"
                    )
                recovered.append(event)
            return tuple(recovered)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self._state,
                "epoch": self._epoch,
                "epoch_started_at": (
                    None if self._epoch_started_at is None else self._epoch_started_at.isoformat()
                ),
                "last_received_at": (
                    None if self._last_received_at is None else self._last_received_at.isoformat()
                ),
                "events": len(self._events),
                "duplicates": self._duplicates,
                "gaps": self._gaps,
                "last_error": self._last_error,
                "retention_seconds": self.retention_seconds,
            }
