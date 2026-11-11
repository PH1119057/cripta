"""R06 persisted no-order P90 Entry automaton; no Execution/Exit authority.

The native cursor AND this state are committed inside one PostgreSQL transaction.
Do not infer a previous price or a previous crossing from a technical ACK.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.fingerprint import fingerprint
from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90CausalEntry,
    P90L90Crossing,
    P90L90EntryState,
    causal_entry_state,
)


class R06NoOrderStateBlocked(RuntimeError):
    """A durable current Entry automaton cannot be recovered exactly."""

def _utc(value: str) -> datetime:
    try:
        at = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise R06NoOrderStateBlocked("R06_STATE_TIME_INVALID") from exc
    if at.utcoffset() != timedelta(0):
        raise R06NoOrderStateBlocked("R06_STATE_TIME_NOT_UTC")
    return at.astimezone(UTC)

def _geometry(
    five: tuple[Candle, ...], fifteen: tuple[Candle, ...],
    boundary: datetime,
) -> P90L90EntryState:
    if (
        boundary.utcoffset() != timedelta(0)
        or boundary.second or boundary.microsecond
        or boundary.minute % 5
    ):
        raise R06NoOrderStateBlocked("R06_ENTRY_EFFECTIVE_BOUNDARY_INVALID")
    f = tuple(c for c in five if c.closed_at <= boundary)
    q = tuple(c for c in fifteen if c.closed_at <= boundary)
    result = causal_entry_state(f, q, effective_at=boundary)
    if result is None or result.effective_at != boundary:
        raise R06NoOrderStateBlocked("R06_ENTRY_CAUSAL_GEOMETRY_UNVERIFIED")
    return result

def restore_symbol(
    raw: Any,
    *,
    five: tuple[Candle, ...],
    fifteen: tuple[Candle, ...],
    published_closed_at: datetime,
) -> P90L90CausalEntry:
    """Restore exact previously committed price/side state, never guess it."""
    if raw is None:
        return P90L90CausalEntry()
    if not isinstance(raw, dict) or set(raw) != {
        "previous_price", "previous_at", "geometry_at", "geometry_fingerprint", "seen",
    }:
        raise R06NoOrderStateBlocked("R06_ENTRY_STATE_SHAPE_DRIFT")
    price, at, boundary, seen = (
        raw["previous_price"], raw["previous_at"], raw["geometry_at"], raw["seen"]
    )
    expected_fingerprint = raw["geometry_fingerprint"]
    if not isinstance(expected_fingerprint, str) or len(expected_fingerprint) != 64:
        raise R06NoOrderStateBlocked("R06_STATE_GEOMETRY_IDENTITY_INVALID")
    if not isinstance(price, str) or not isinstance(at, str) or not isinstance(boundary, str):
        raise R06NoOrderStateBlocked("R06_ENTRY_STATE_MISSING_PREVIOUS")
    try:
        previous = Decimal(price)
    except InvalidOperation as exc:
        raise R06NoOrderStateBlocked("R06_ENTRY_STATE_PRICE_INVALID") from exc
    seen_at, close_at = _utc(at), _utc(boundary)
    if (
        not previous.is_finite() or previous <= 0
        or seen_at > published_closed_at + timedelta(minutes=5)
        or close_at > published_closed_at
        or seen_at < close_at
        or not isinstance(seen, list) or len(set(seen)) != len(seen)
        or not set(seen) <= {"LONG", "SHORT"}
    ):
        raise R06NoOrderStateBlocked("R06_ENTRY_STATE_CAUSAL_DRIFT")
    state = _geometry(five, fifteen, close_at)
    if fingerprint(state) != expected_fingerprint:
        raise R06NoOrderStateBlocked("R06_ENTRY_GEOMETRY_CHANGED_SINCE_CHECKPOINT")
    return P90L90CausalEntry(
        state=state,
        previous_price=previous,
        previous_at=seen_at,
        seen=set(seen),
    )

def advance_symbol(
    entry: P90L90CausalEntry,
    *,
    price: Decimal,
    event_at: datetime,
    five: tuple[Candle, ...],
    fifteen: tuple[Candle, ...],
    published_closed_at: datetime,
) -> tuple[P90L90Crossing, ...]:
    """Recompute the last proven CLOSED geometry for the event's own 5m."""
    if event_at.utcoffset() != timedelta(0):
        raise R06NoOrderStateBlocked("R06_NATIVE_EVENT_NOT_UTC")
    when = event_at.astimezone(UTC)
    boundary = when.replace(minute=when.minute // 5 * 5, second=0, microsecond=0)
    if boundary > published_closed_at or when >= published_closed_at + timedelta(minutes=5):
        raise R06NoOrderStateBlocked("R06_NATIVE_FUTURE_GEOMETRY_UNPUBLISHED")
    current = entry.state
    if current is None or current.effective_at < boundary:
        entry.update_geometry(_geometry(five, fifteen, boundary))
    elif current.effective_at > boundary:
        raise R06NoOrderStateBlocked("R06_NATIVE_GEOMETRY_REGRESSION")
    try:
        return entry.on_trade(price=price, event_at=when)
    except ValueError as exc:
        raise R06NoOrderStateBlocked("R06_NATIVE_ENTRY_EVENT_CAUSAL_INVALID") from exc

def dump_symbol(entry: P90L90CausalEntry) -> dict[str, object]:
    if entry.previous_price is None or entry.previous_at is None or entry.state is None:
        raise R06NoOrderStateBlocked("R06_ENTRY_STATE_NOT_YET_INITIALIZED")
    return {
        "previous_price": str(entry.previous_price),
        "previous_at": entry.previous_at.astimezone(UTC).isoformat(),
        "geometry_at": entry.state.effective_at.isoformat(),
        "geometry_fingerprint": fingerprint(entry.state),
        "seen": sorted(entry.seen),
    }
