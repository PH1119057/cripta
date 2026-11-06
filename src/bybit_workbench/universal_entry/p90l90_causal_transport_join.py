"""P90L90 no-order causal join of independent Bybit publicTrade/kline transports.

This is a pure, bounded, restart-serializable ordering primitive. It never
synthesizes a trade, guesses a candle, or changes the Strategy. A caller must
persist the complete snapshot plus original payloads *before acknowledging*
an inbound event, and must commit delivered results atomically with a queue
checkpoint. Until that durable PG writer is wired this is NOT a SHADOW gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

EventKind = Literal["PUBLIC_TRADE", "CANDLE_CLOSED"]


class ExactNativeIdJournal(Protocol):
    """One source checkpoint transaction owns the journal; not a new actor."""

    def contains(self, seq: int, exec_id: str) -> bool: ...

    def append(self, seq: int, exec_id: str, previous: int | None) -> None: ...
_SCHEMA = "P90L90_CAUSAL_TRANSPORT_JOIN_V1"
_SCHEMA_JOURNALED = "P90L90_CAUSAL_TRANSPORT_JOIN_V2"
_MAX_PENDING = 10000
_INTERVALS = {"5": 5, "15": 15}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("P90L90 market event time must be timezone-aware")
    return value.astimezone(UTC)


def _sequence_before(new: int | None, previous: int | None) -> bool:
    if new is None:
        raise ValueError("P90L90 native cursor sequence missing")
    return previous is not None and new < previous


def _floor(value: datetime, interval: int) -> datetime:
    at = _utc(value)
    minute = (at.minute // interval) * interval
    return at.replace(minute=minute, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class P90TransportEvent:
    """Original source envelope and exact cursor as opaque, durable JSON."""

    event_id: str
    symbol: str
    kind: EventKind
    event_at: datetime
    source_json: str
    timeframe: str | None = None
    exchange_sequence: int | None = None
    exec_id: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.symbol or not self.source_json:
            raise ValueError("P90L90 requires complete native source identity/payload")
        _utc(self.event_at)
        if self.kind == "PUBLIC_TRADE":
            sequence = self.exchange_sequence
            if not isinstance(sequence, int) or isinstance(sequence, bool):
                raise ValueError("P90L90 native trade sequence type invalid")
            if (
                self.timeframe is not None
                or sequence < 0
                or not self.exec_id
            ):
                raise ValueError("P90L90 native trade cursor incomplete")
        elif self.kind == "CANDLE_CLOSED":
            if (
                self.timeframe not in _INTERVALS
                or self.exchange_sequence is not None
                or self.exec_id is not None
                or self.event_at != _floor(
                    self.event_at, _INTERVALS[self.timeframe]
                )
            ):
                raise ValueError("P90L90 closed kline identity/boundary invalid")
        else:
            raise ValueError("P90L90 unsupported market event kind")

    def record(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "symbol": self.symbol,
            "kind": self.kind,
            "event_at": _utc(self.event_at).isoformat(),
            "source_json": self.source_json,
            "timeframe": self.timeframe,
            "exchange_sequence": self.exchange_sequence,
            "exec_id": self.exec_id,
        }

    @classmethod
    def restore(cls, raw: object) -> P90TransportEvent:
        if not isinstance(raw, dict) or set(raw) != {
            "event_id", "symbol", "kind", "event_at", "source_json",
            "timeframe", "exchange_sequence", "exec_id",
        }:
            raise ValueError("P90L90 market event checkpoint schema invalid")
        if not isinstance(raw["event_at"], str):
            raise ValueError("P90L90 market event UTC timestamp invalid")
        when = datetime.fromisoformat(raw["event_at"])
        if when.tzinfo is None or when.utcoffset() != timedelta():
            raise ValueError("P90L90 market event checkpoint requires UTC")
        return cls(
            event_id=raw["event_id"],
            symbol=raw["symbol"],
            kind=raw["kind"],
            event_at=when,
            source_json=raw["source_json"],
            timeframe=raw["timeframe"],
            exchange_sequence=raw["exchange_sequence"],
            exec_id=raw["exec_id"],
        )


@dataclass(slots=True)
class P90L90CausalTransportJoin:
    """Buffer a native tick until BOTH effective completed bar closes exist.

    The watermark and entire pending source payload survive serialization.
    If a late event precedes a processed watermark, FAIL rather than
    retroactively modifying already evaluated Entry/Exit positions.
    """

    symbol: str
    closed_5m_at: datetime
    closed_15m_at: datetime
    pending: list[P90TransportEvent] = field(default_factory=list)
    last_trade_event_at: datetime | None = None
    last_trade_sequence: int | None = None
    last_emitted_trade_at: datetime | None = None
    last_emitted_trade_sequence: int | None = None
    last_emitted_exec_ids: set[str] = field(default_factory=set)
    # None: original V1 full-set checkpoint. Integer: V2 exact external
    # rolling journal, with at most 512 IDs retained as a local witness.
    native_id_journal_count: int | None = None
    _exact_journal: ExactNativeIdJournal | None = field(
        default=None, init=False, repr=False, compare=False,
    )
    last_5m_event_id: str = ""
    last_15m_event_id: str = ""

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("P90L90 join requires explicit symbol")
        self.closed_5m_at = _utc(self.closed_5m_at)
        self.closed_15m_at = _utc(self.closed_15m_at)
        if (
            self.closed_5m_at != _floor(self.closed_5m_at, 5)
            or self.closed_15m_at != _floor(self.closed_15m_at, 15)
            or self.closed_15m_at > self.closed_5m_at
        ):
            raise ValueError("P90L90 causal join seed bar boundaries inconsistent")

    def bind_exact_journal(self, journal: ExactNativeIdJournal) -> None:
        """Only the atomic checkpoint writer may bind its verified sidecar."""
        if self._exact_journal is not None:
            raise RuntimeError("P90_NATIVE_SEQ_ID_JOURNAL_DOUBLE_BIND")
        if self.native_id_journal_count is None:
            self.native_id_journal_count = len(self.last_emitted_exec_ids)
        self._exact_journal = journal

    def accept(self, event: P90TransportEvent) -> tuple[P90TransportEvent, ...]:
        # A V2 snapshot without its exact DB journal cannot evaluate a
        # public event OR a candle which might release pending public events.
        if self.native_id_journal_count is not None and self._exact_journal is None:
            raise RuntimeError("P90_NATIVE_SEQ_ID_JOURNAL_UNBOUND")
        if event.symbol != self.symbol:
            raise ValueError("P90L90 cross-symbol public fact prohibited")
        if event.event_id in {e.event_id for e in self.pending}:
            return ()
        if event.kind == "PUBLIC_TRADE":
            sequence = event.exchange_sequence
            if sequence is None:
                raise ValueError("P90L90 native trade sequence missing")
            if (
                sequence == self.last_emitted_trade_sequence
                and event.exec_id in self.last_emitted_exec_ids
            ):
                return ()  # exact replay after checkpoint commit/restart
            if _sequence_before(sequence, self.last_emitted_trade_sequence):
                raise RuntimeError("P90L90 replay regressed before committed sequence")
            if any(
                e.kind == "PUBLIC_TRADE"
                and e.exchange_sequence == event.exchange_sequence
                and e.exec_id == event.exec_id
                for e in self.pending
            ):
                return ()
            # Fail before changing last_trade_* or pending. The atomic DB
            # writer will roll back too, but a pure join must not silently
            # lose the rejected native envelope from its in-memory queue.
            if (
                sequence == self.last_emitted_trade_sequence
                and self._exact_journal is not None
                and self._exact_journal.contains(sequence, str(event.exec_id))
            ):
                return ()
            if (
                sequence == self.last_emitted_trade_sequence
                and self._exact_journal is None
                and len(self.last_emitted_exec_ids) >= 512
            ):
                raise RuntimeError("P90L90 same-sequence cursor capacity exceeded")
        if len(self.pending) >= _MAX_PENDING:
            raise RuntimeError("P90L90 causal transport backlog capacity exceeded")
        if event.kind == "PUBLIC_TRADE":
            # Narrow this independent transport branch explicitly. The earlier
            # branch validates the cursor, but mypy cannot prove its predicate
            # still holds here for a newly received envelope.
            sequence = event.exchange_sequence
            if not isinstance(sequence, int):
                raise ValueError("P90L90 native trade sequence missing")
            if self.last_trade_event_at is not None and (
                event.event_at < self.last_trade_event_at
            ):
                raise RuntimeError("P90L90 native trade time regressed")
            if _sequence_before(sequence, self.last_trade_sequence):
                raise RuntimeError("P90L90 native trade sequence regressed")
            if (
                self.last_emitted_trade_at is not None
                and event.event_at < self.last_emitted_trade_at
            ):
                raise RuntimeError("P90L90 native event before committed output")
            self.last_trade_event_at = _utc(event.event_at)
            self.last_trade_sequence = event.exchange_sequence
        else:
            watermark = (
                self.closed_5m_at if event.timeframe == "5"
                else self.closed_15m_at
            )
            if event.event_at < watermark:
                raise RuntimeError("P90L90 closed bar arrives before committed watermark")
            if event.event_at == watermark:
                known_id = (
                    self.last_5m_event_id if event.timeframe == "5"
                    else self.last_15m_event_id
                )
                if event.event_id == known_id:
                    return ()
                # A REST seed has no matching WS source fact ID. Treat any
                # later same-boundary close as unproved, not silently equal
                # to the REST OHLCV payload (would permit retrospective edits).
                raise RuntimeError(
                    "P90L90 closed bar duplicate with unproven source identity"
                )
        self.pending.append(event)
        return self._release()

    def _release(self) -> tuple[P90TransportEvent, ...]:
        emitted: list[P90TransportEvent] = []
        while self.pending:
            trade = next(
                (item for item in self.pending if item.kind == "PUBLIC_TRADE"),
                None,
            )
            if trade is not None:
                needed_5 = _floor(trade.event_at, 5)
                needed_15 = _floor(trade.event_at, 15)
                bars = [
                    item for item in self.pending
                    if item.kind == "CANDLE_CLOSED" and (
                        (item.timeframe == "5" and item.event_at <= needed_5)
                        or (item.timeframe == "15" and item.event_at <= needed_15)
                    )
                ]
            else:
                bars = [item for item in self.pending if item.kind == "CANDLE_CLOSED"]
            # Never publish a 15m watermark ahead of the required closed
            # 5m boundary. Bybit's kline channels are independent; a 15m
            # close can arrive before its matching 5m close.
            bars = [
                item for item in bars
                if item.timeframe != "15" or item.event_at <= self.closed_5m_at
            ]
            if bars:
                # At the same effective boundary publish 5m first, then 15m.
                bar = min(
                    bars, key=lambda x: (x.event_at, 0 if x.timeframe == "5" else 1)
                )
                self.pending.remove(bar)
                if bar.timeframe == "5":
                    if bar.event_at != self.closed_5m_at + timedelta(minutes=5):
                        raise RuntimeError("P90L90 missing/noncausal completed 5m bar")
                    self.closed_5m_at = bar.event_at
                    self.last_5m_event_id = bar.event_id
                else:
                    if bar.event_at != self.closed_15m_at + timedelta(minutes=15):
                        raise RuntimeError("P90L90 missing/noncausal completed 15m bar")
                    self.closed_15m_at = bar.event_at
                    self.last_15m_event_id = bar.event_id
                emitted.append(bar)
                continue
            if trade is None:
                break
            if self.closed_5m_at < needed_5 or self.closed_15m_at < needed_15:
                break
            trade_seq = trade.exchange_sequence
            if trade_seq is None:
                raise RuntimeError("P90L90 native queued trade cursor invalid")
            if (
                self.closed_5m_at > needed_5
                or self.closed_15m_at > needed_15
                or (
                    self.last_emitted_trade_at is not None
                    and trade.event_at < self.last_emitted_trade_at
                )
                or _sequence_before(
                    trade_seq, self.last_emitted_trade_sequence
                )
            ):
                raise RuntimeError("P90L90 causally late trade vs committed bar")
            # The pending close/bar release path can emit a batch. Reject
            # overflow before removing this trade or moving its emitted
            # cursor; the surrounding DB checkpoint transaction will roll
            # back any earlier staged batch effects on failure.
            if (
                self.last_emitted_trade_sequence == trade.exchange_sequence
                and self._exact_journal is None
                and len(self.last_emitted_exec_ids) >= 512
            ):
                raise RuntimeError("P90L90 same-sequence cursor capacity exceeded")
            previous = self.last_emitted_trade_sequence
            if self._exact_journal is not None:
                # Atomically INSERT the original exact ID and retire only
                # previous-sequence rows inside the caller's locked xact.
                self._exact_journal.append(
                    trade_seq, str(trade.exec_id), previous,
                )
            self.pending.remove(trade)
            self.last_emitted_trade_at = _utc(trade.event_at)
            if previous != trade.exchange_sequence:
                self.last_emitted_exec_ids.clear()
                if self._exact_journal is not None:
                    self.native_id_journal_count = 0
            self.last_emitted_trade_sequence = trade.exchange_sequence
            if self._exact_journal is not None:
                if len(self.last_emitted_exec_ids) >= 512:
                    # Deterministic witness eviction; the full exact set is
                    # still indexed transactionally in PostgreSQL.
                    self.last_emitted_exec_ids.remove(min(self.last_emitted_exec_ids))
                self.native_id_journal_count = (
                    (self.native_id_journal_count or 0) + 1
                )
            self.last_emitted_exec_ids.add(str(trade.exec_id))
            emitted.append(trade)
        return tuple(emitted)

    def snapshot(self) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "schema": (
                _SCHEMA_JOURNALED
                if self.native_id_journal_count is not None else _SCHEMA
            ),
            "symbol": self.symbol,
            "closed_5m_at": self.closed_5m_at.isoformat(),
            "closed_15m_at": self.closed_15m_at.isoformat(),
            "pending": [item.record() for item in self.pending],
            "last_trade_event_at": (
                None if self.last_trade_event_at is None
                else self.last_trade_event_at.isoformat()
            ),
            "last_trade_sequence": self.last_trade_sequence,
            "last_emitted_trade_at": (
                None if self.last_emitted_trade_at is None
                else self.last_emitted_trade_at.isoformat()
            ),
            "last_emitted_trade_sequence": self.last_emitted_trade_sequence,
            "last_emitted_exec_ids": sorted(self.last_emitted_exec_ids),
            "last_5m_event_id": self.last_5m_event_id,
            "last_15m_event_id": self.last_15m_event_id,
        }
        if self.native_id_journal_count is not None:
            snapshot["native_id_journal_count"] = self.native_id_journal_count
        return snapshot

    @classmethod
    def restore(cls, raw: object) -> P90L90CausalTransportJoin:
        keys = {
            "schema", "symbol", "closed_5m_at", "closed_15m_at",
            "pending", "last_trade_event_at", "last_trade_sequence",
            "last_emitted_trade_at", "last_emitted_trade_sequence",
            "last_emitted_exec_ids", "last_5m_event_id", "last_15m_event_id",
        }
        if not isinstance(raw, dict) or not (
            (set(raw) == keys and raw.get("schema") == _SCHEMA)
            or (
                set(raw) == keys | {"native_id_journal_count"}
                and raw.get("schema") == _SCHEMA_JOURNALED
            )
        ):
            raise ValueError("P90L90 causal join snapshot schema unsupported")
        def date(value: object) -> datetime | None:
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError("P90L90 join snapshot timestamp invalid")
            at = datetime.fromisoformat(value)
            if at.tzinfo is None or at.utcoffset() != timedelta():
                raise ValueError("P90L90 join snapshot timestamp must be UTC")
            return at
        bars_5, bars_15 = date(raw["closed_5m_at"]), date(raw["closed_15m_at"])
        if bars_5 is None or bars_15 is None or not isinstance(raw["pending"], list):
            raise ValueError("P90L90 causal join snapshot boundaries missing")
        if type(raw["last_trade_sequence"]) not in (int, type(None)) or (
            type(raw["last_emitted_trade_sequence"]) not in (int, type(None))
        ):
            raise ValueError("P90L90 causal join snapshot cursor invalid")
        ids = raw["last_emitted_exec_ids"]
        if (
            not isinstance(ids, list)
            or len(ids) > 512
            or any(not isinstance(x, str) or not x for x in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("P90L90 join last emitted cursor IDs invalid")
        count: int | None = None
        if raw["schema"] == _SCHEMA_JOURNALED:
            candidate = raw["native_id_journal_count"]
            sequence = raw["last_emitted_trade_sequence"]
            if (
                type(candidate) is not int or candidate < len(ids)
                or (
                    sequence is None
                    and (candidate != 0 or bool(ids))
                )
                or (
                    sequence is not None
                    and (candidate < 1 or not ids)
                )
            ):
                raise ValueError("P90L90 external native ID checkpoint invalid")
            count = candidate
        state = cls(
            symbol=raw["symbol"],
            closed_5m_at=bars_5,
            closed_15m_at=bars_15,
            last_trade_event_at=date(raw["last_trade_event_at"]),
            last_trade_sequence=raw["last_trade_sequence"],
            last_emitted_trade_at=date(raw["last_emitted_trade_at"]),
            last_emitted_trade_sequence=raw["last_emitted_trade_sequence"],
            last_emitted_exec_ids=set(ids),
            native_id_journal_count=count,
            last_5m_event_id=raw["last_5m_event_id"],
            last_15m_event_id=raw["last_15m_event_id"],
        )
        state.pending = [P90TransportEvent.restore(item) for item in raw["pending"]]
        if len(state.pending) > _MAX_PENDING or len({
            item.event_id for item in state.pending
        }) != len(state.pending) or any(
            item.symbol != state.symbol for item in state.pending
        ):
            raise ValueError("P90L90 causal join snapshot pending invalid")
        return state
