"""Versioned restart state for the P90L90 native-tick crossing consumer.

The checkpoint is ONLY a value object; persistence MUST be atomic with the
causal transport join and the dependent PAPER position transaction. A caller
must recompute geometry from the original completed candle history at the
persisted effective boundary and assert exact equality before replaying.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from .p90l90_causal import P90L90CausalEntry, P90L90EntryState, Side

_SCHEMA = "P90L90_CAUSAL_ENTRY_RESTART_V1"
_GEOMETRY_FIELDS = frozenset(item.name for item in fields(P90L90EntryState))
_PRICE_FIELDS = frozenset({
    "l53_low_raw", "l53_high_raw", "h9_5m_low_raw", "h9_5m_high_raw",
    "h9_15m_low_raw", "h9_15m_high_raw", "raw_h9_union_span",
})
_NUMERIC_FIELDS = _GEOMETRY_FIELDS - {"effective_at"}
_CHECKPOINT_FIELDS = frozenset({
    "schema", "geometry", "previous_price", "previous_at", "seen",
})


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("P90L90 restart UTC timestamp missing")
    when = datetime.fromisoformat(value)
    if when.tzinfo is None or when.utcoffset() != timedelta():
        raise ValueError("P90L90 restart timestamp must be UTC")
    return when.astimezone(UTC)


def _decimal(value: object, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("P90L90 restart Decimal representation invalid")
    try:
        out = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("P90L90 restart Decimal invalid") from None
    if not out.is_finite() or (positive and out <= 0):
        raise ValueError("P90L90 restart Decimal range invalid")
    return out


def entry_restart_snapshot(entry: P90L90CausalEntry) -> dict[str, object]:
    """Canonical serializable value, no inferred price or exchange fill."""
    state = entry.state
    geometry: dict[str, object] | None = None
    if state is not None:
        geometry = {
            key: (
                state.effective_at.astimezone(UTC).isoformat()
                if key == "effective_at" else
                None if getattr(state, key) is None
                else str(getattr(state, key))
            )
            for key in _GEOMETRY_FIELDS
        }
    return {
        "schema": _SCHEMA,
        "geometry": geometry,
        "previous_price": (
            None if entry.previous_price is None else str(entry.previous_price)
        ),
        "previous_at": (
            None if entry.previous_at is None
            else entry.previous_at.astimezone(UTC).isoformat()
        ),
        "seen": sorted(entry.seen),
    }


def restore_entry_restart_snapshot(
    raw: object,
    *,
    seeded_geometry: P90L90EntryState | None,
) -> P90L90CausalEntry:
    """Fail closed on absent candle parity, malformed or impossible state."""
    if not isinstance(raw, dict) or frozenset(raw) != _CHECKPOINT_FIELDS or (
        raw["schema"] != _SCHEMA
    ):
        raise ValueError("P90L90 entry restart checkpoint schema mismatch")
    geometry_raw = raw["geometry"]
    restored: P90L90EntryState | None = None
    if geometry_raw is not None:
        if not isinstance(geometry_raw, dict) or (
            frozenset(geometry_raw) != _GEOMETRY_FIELDS
        ):
            raise ValueError("P90L90 entry restart geometry schema mismatch")
        effective_at = _time(geometry_raw["effective_at"])
        if (
            effective_at.second or effective_at.microsecond
            or effective_at.minute % 5
        ):
            raise ValueError("P90L90 entry restart geometry boundary invalid")
        numbers: dict[str, Decimal | None] = {
            key: (
                None if geometry_raw[key] is None
                and key in {"long_depth_pct", "short_depth_pct"}
                else _decimal(
                    geometry_raw[key], positive=key in _PRICE_FIELDS
                )
            )
            for key in _NUMERIC_FIELDS
        }
        long_depth = numbers["long_depth_pct"]
        short_depth = numbers["short_depth_pct"]
        if long_depth is not None and not (0 <= long_depth <= 15):
            raise ValueError("P90L90 entry restart long depth invalid")
        if short_depth is not None and not (0 <= short_depth <= 15):
            raise ValueError("P90L90 entry restart short depth invalid")
        restored = P90L90EntryState(
            effective_at=effective_at,
            l53_low_raw=cast(Decimal, numbers["l53_low_raw"]),
            l53_high_raw=cast(Decimal, numbers["l53_high_raw"]),
            h9_5m_low_raw=cast(Decimal, numbers["h9_5m_low_raw"]),
            h9_5m_high_raw=cast(Decimal, numbers["h9_5m_high_raw"]),
            h9_15m_low_raw=cast(Decimal, numbers["h9_15m_low_raw"]),
            h9_15m_high_raw=cast(Decimal, numbers["h9_15m_high_raw"]),
            raw_h9_union_span=cast(Decimal, numbers["raw_h9_union_span"]),
            working_width_pct=cast(Decimal, numbers["working_width_pct"]),
            previous_seven_day_median=cast(
                Decimal, numbers["previous_seven_day_median"]
            ),
            long_depth_pct=long_depth,
            short_depth_pct=short_depth,
        )
    if restored != seeded_geometry:
        raise RuntimeError(
            "P90L90 restart historical candle geometry does not match checkpoint"
        )
    price_raw, at_raw = raw["previous_price"], raw["previous_at"]
    if (price_raw is None) != (at_raw is None):
        raise ValueError("P90L90 restart previous tick identity incomplete")
    previous = None if price_raw is None else _decimal(price_raw, positive=True)
    previous_at = None if at_raw is None else _time(at_raw)
    seen_raw: Any = raw["seen"]
    if (
        not isinstance(seen_raw, list)
        or len(set(map(str, seen_raw))) != len(seen_raw)
        or any(not isinstance(side, str) or side not in {"LONG", "SHORT"} for side in seen_raw)
        or (restored is None and seen_raw)
        or (previous is None and seen_raw)
    ):
        raise ValueError("P90L90 restart crossing-dedupe state invalid")
    if previous_at is not None and restored is not None and (
        previous_at >= restored.effective_at + timedelta(minutes=5)
    ):
        raise ValueError("P90L90 restart native tick later than geometry")
    sides: set[Side] = {
        cast(Side, side) for side in seen_raw
    }
    if (
        restored is not None
        and (
            ("LONG" in sides and restored.long_depth_pct is None)
            or ("SHORT" in sides and restored.short_depth_pct is None)
        )
    ):
        raise ValueError("P90L90 restart crossing outside valid side geometry")
    entry = P90L90CausalEntry(
        state=restored, previous_price=previous, previous_at=previous_at,
        seen=sides,
    )
    if entry_restart_snapshot(entry) != raw:
        raise ValueError("P90L90 restart checkpoint not canonical")
    return entry
