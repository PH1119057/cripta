from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class ContinuityNotProvable(RuntimeError):
    """Exact public-stream continuity cannot be proven for the current parity run."""


class ContinuityAction(StrEnum):
    RESUME_SAME_RUN = "RESUME_SAME_RUN"
    NOT_COMPARABLE = "NOT_COMPARABLE"


@dataclass(frozen=True, slots=True)
class ContinuityVerdict:
    proven: bool
    reason: str
    earliest_deadline: datetime | None = None


@dataclass(frozen=True, slots=True)
class OiSampleCursor:
    symbol: str
    observed_at: datetime
    ticker_cross_sequence: int | None


@dataclass(frozen=True, slots=True)
class TradeCursor:
    symbol: str
    exec_id: str
    seq: int
    traded_at: datetime


@dataclass(frozen=True, slots=True)
class ReplayTrade:
    symbol: str
    exec_id: str
    seq: int
    traded_at: datetime
    price: str
    size: str
    side: str


class PublicWsHeartbeat:
    """Application-level public WebSocket liveness proof.

    Socket openness and successful send() are not liveness evidence.  A received
    public frame or explicit Bybit pong resets the heartbeat; a sent ping starts
    a bounded proof deadline.
    """

    def __init__(
        self,
        ping_interval_seconds: float,
        pong_timeout_seconds: float,
        *,
        started_monotonic: float,
    ) -> None:
        if ping_interval_seconds <= 0:
            raise ValueError("ping_interval_seconds must be positive")
        if pong_timeout_seconds <= 0:
            raise ValueError("pong_timeout_seconds must be positive")
        self.ping_interval_seconds = ping_interval_seconds
        self.pong_timeout_seconds = pong_timeout_seconds
        self._last_activity_monotonic = started_monotonic
        self._pending_ping_sent_at: float | None = None

    def ping_due(self, now_monotonic: float) -> bool:
        return (
            self._pending_ping_sent_at is None
            and now_monotonic - self._last_activity_monotonic >= self.ping_interval_seconds
        )

    def note_ping_sent(self, now_monotonic: float) -> None:
        if self._pending_ping_sent_at is None:
            self._pending_ping_sent_at = now_monotonic

    def note_pong(self, now_monotonic: float) -> None:
        self._last_activity_monotonic = now_monotonic
        self._pending_ping_sent_at = None

    def note_market_frame(self, now_monotonic: float) -> None:
        self._last_activity_monotonic = now_monotonic
        self._pending_ping_sent_at = None

    def deadline_exceeded(self, now_monotonic: float) -> bool:
        sent_at = self._pending_ping_sent_at
        return sent_at is not None and now_monotonic - sent_at >= self.pong_timeout_seconds


class ExactFactDeduper:
    """Deduplicate only exact normalized source identities."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def accept(self, source_identity: str) -> bool:
        if not source_identity:
            raise ValueError("source identity must be non-empty")
        if source_identity in self._seen:
            return False
        self._seen.add(source_identity)
        return True


def assess_oi30s_continuity(
    cursors: Mapping[str, OiSampleCursor],
    *,
    required_symbols: Sequence[str],
    subscription_ready_server_at: datetime,
    sample_seconds: int,
) -> ContinuityVerdict:
    if sample_seconds <= 0:
        raise ValueError("sample_seconds must be positive")
    ready = subscription_ready_server_at.astimezone(UTC)
    deadlines: list[datetime] = []
    missing = [symbol for symbol in required_symbols if symbol not in cursors]
    if missing:
        return ContinuityVerdict(
            False,
            "exact OI30S cursor missing for: " + ",".join(sorted(missing)),
        )
    for symbol in required_symbols:
        cursor = cursors[symbol]
        deadlines.append(cursor.observed_at.astimezone(UTC) + timedelta(seconds=sample_seconds))
    earliest = min(deadlines) if deadlines else None
    if earliest is None:
        return ContinuityVerdict(False, "exact OI30S cursor set is empty")
    if ready >= earliest:
        return ContinuityVerdict(
            False,
            (
                "OI30S continuity not provable: subscription readiness crossed the next eligible "
                "OI30S sample deadline; 5m historical OI cannot substitute for this source"
            ),
            earliest,
        )
    return ContinuityVerdict(
        True,
        "all ticker subscriptions restored before every next eligible OI30S sample deadline",
        earliest,
    )


def resolve_continuity_action(verdict: ContinuityVerdict) -> ContinuityAction:
    return ContinuityAction.RESUME_SAME_RUN if verdict.proven else ContinuityAction.NOT_COMPARABLE


def _require_row_text(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if value is None or value == "":
        raise ContinuityNotProvable(f"recent-trade row missing {key}")
    return str(value)


def _parse_trade_row(row: Mapping[str, object], *, symbol: str) -> ReplayTrade:
    try:
        seq = int(_require_row_text(row, "seq"))
        traded_at = datetime.fromtimestamp(int(_require_row_text(row, "time")) / 1000, UTC)
    except ValueError as exc:
        raise ContinuityNotProvable("recent-trade row has invalid seq/time") from exc
    row_symbol = _require_row_text(row, "symbol").upper()
    if row_symbol != symbol.upper():
        raise ContinuityNotProvable("recent-trade row symbol does not match exact cursor symbol")
    side = _require_row_text(row, "side")
    if side not in {"Buy", "Sell"}:
        raise ContinuityNotProvable("recent-trade row has invalid taker side")
    return ReplayTrade(
        symbol=row_symbol,
        exec_id=_require_row_text(row, "execId"),
        seq=seq,
        traded_at=traded_at,
        price=_require_row_text(row, "price"),
        size=_require_row_text(row, "size"),
        side=side,
    )


def audit_recent_trade_window(
    rows: Sequence[Mapping[str, object]],
    *,
    anchor: TradeCursor,
    cutoff_at: datetime,
    known_exec_ids: Collection[str] = (),
) -> tuple[str, ...]:
    """Detect exact unknown trades without inventing their causal order.

    This function is only a liveness/gap detector.  Any unknown exact execId in
    the current anchor sequence or a later sequence proves the primary stream is
    behind; ordering is deferred to mirror/strict replay recovery.
    """

    parsed = tuple(_parse_trade_row(row, symbol=anchor.symbol) for row in rows)
    if not any(row.exec_id == anchor.exec_id and row.seq == anchor.seq for row in parsed):
        raise ContinuityNotProvable("exact recent-trade execId+seq anchor not found")
    cutoff = cutoff_at.astimezone(UTC)
    known = set(known_exec_ids)
    known.add(anchor.exec_id)
    return tuple(
        row.exec_id
        for row in parsed
        if row.traded_at <= cutoff and row.seq >= anchor.seq and row.exec_id not in known
    )


def build_exact_trade_replay(
    rows: Sequence[Mapping[str, object]],
    *,
    anchor: TradeCursor,
    cutoff_at: datetime,
    known_exec_ids: Collection[str] = (),
) -> tuple[ReplayTrade, ...]:
    """Build exact causal trade replay from recent-trade using execId+seq anchor.

    The REST endpoint is newest-first. Cross-sequence defines causal group order.
    Within one sequence, distinct exact trade timestamps define chronological order.
    If multiple rows share the same exact timestamp but differ in price/size/side,
    order can affect V1 state and continuity is rejected instead of guessed.
    """

    parsed = tuple(_parse_trade_row(row, symbol=anchor.symbol) for row in rows)
    anchor_index = next(
        (
            index
            for index, row in enumerate(parsed)
            if row.exec_id == anchor.exec_id and row.seq == anchor.seq
        ),
        None,
    )
    if anchor_index is None:
        raise ContinuityNotProvable("exact recent-trade execId+seq anchor not found")
    cutoff = cutoff_at.astimezone(UTC)
    known = set(known_exec_ids)
    candidates = [
        row
        for row in parsed
        if row.traded_at <= cutoff
        and row.seq >= anchor.seq
        and row.exec_id != anchor.exec_id
        and row.exec_id not in known
    ]
    for row in candidates:
        if row.seq == anchor.seq and row.traded_at <= anchor.traded_at.astimezone(UTC):
            raise ContinuityNotProvable(
                "anchor sequence "
                f"{anchor.seq} contains unknown trade not strictly after exact anchor time"
            )
    groups: dict[int, list[ReplayTrade]] = {}
    for row in candidates:
        groups.setdefault(row.seq, []).append(row)
    ordered: list[ReplayTrade] = []
    for seq in sorted(groups):
        group = groups[seq]
        by_timestamp: dict[datetime, list[ReplayTrade]] = {}
        for row in group:
            by_timestamp.setdefault(row.traded_at, []).append(row)
        for traded_at in sorted(by_timestamp):
            timestamp_group = by_timestamp[traded_at]
            semantic_keys = {(row.price, row.size, row.side) for row in timestamp_group}
            if len(semantic_keys) > 1:
                raise ContinuityNotProvable(
                    f"same-seq trade group {seq} has ambiguous same timestamp order"
                )
            # At one exact timestamp, only fully identical trade semantics are
            # permutation-safe for V1/Universal state. execId is then a stable
            # transport-only tie breaker.
            ordered.extend(sorted(timestamp_group, key=lambda row: row.exec_id))
    return tuple(ordered)


def expected_boundaries(
    last_boundary: datetime,
    end_at: datetime,
    *,
    timeframe_minutes: int,
) -> tuple[datetime, ...]:
    if timeframe_minutes <= 0:
        raise ValueError("timeframe_minutes must be positive")
    cursor = last_boundary.astimezone(UTC)
    end = end_at.astimezone(UTC)
    step = timedelta(minutes=timeframe_minutes)
    result: list[datetime] = []
    cursor += step
    while cursor <= end:
        result.append(cursor)
        cursor += step
    return tuple(result)
