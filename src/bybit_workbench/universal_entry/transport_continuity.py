from __future__ import annotations

from collections.abc import Mapping, Sequence
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


def build_exact_trade_replay(
    rows: Sequence[Mapping[str, object]],
    *,
    anchor: TradeCursor,
    cutoff_at: datetime,
) -> tuple[ReplayTrade, ...]:
    """Build exact causal trade replay from recent-trade using execId+seq anchor.

    The REST endpoint is newest-first. Cross-sequence defines causal group order. If a
    same-seq group has different timestamp/price/side, row order could affect V1 state,
    so continuity is rejected instead of guessed.
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
    candidates = [
        row
        for row in parsed
        if row.traded_at <= cutoff and row.seq >= anchor.seq and row.exec_id != anchor.exec_id
    ]
    groups: dict[int, list[ReplayTrade]] = {}
    for row in candidates:
        groups.setdefault(row.seq, []).append(row)
    ordered: list[ReplayTrade] = []
    for seq in sorted(groups):
        group = groups[seq]
        semantic_keys = {(row.traded_at, row.price, row.side) for row in group}
        if len(semantic_keys) > 1:
            raise ContinuityNotProvable(
                f"same-seq trade group {seq} is not semantically commutative"
            )
        # Rows in one cross-sequence are simultaneous for this continuity contract.
        # Stable exec-id order avoids depending on undocumented REST row order.
        ordered.extend(sorted(group, key=lambda row: row.exec_id))
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
