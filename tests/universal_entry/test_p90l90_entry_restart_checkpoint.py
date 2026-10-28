"""P90L90 restart snapshot preserves native previous tick and first crossing."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90CausalEntry, P90L90EntryState,
)
from bybit_workbench.universal_entry.p90l90_entry_restart_checkpoint import (
    entry_restart_snapshot,
    restore_entry_restart_snapshot,
)

B = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)


def geometry() -> P90L90EntryState:
    return P90L90EntryState(
        effective_at=B,
        l53_low_raw=D("100"), l53_high_raw=D("110"),
        h9_5m_low_raw=D("98"), h9_5m_high_raw=D("110"),
        h9_15m_low_raw=D("98"), h9_15m_high_raw=D("111"),
        raw_h9_union_span=D("13"),
        working_width_pct=D("1.2"),
        previous_seven_day_median=D("1.1"),
        long_depth_pct=D("1"), short_depth_pct=None,
    )


def test_restart_preserves_previous_price_side_dedupe_and_effective_at():
    state = geometry()
    entry = P90L90CausalEntry(state=state)
    assert entry.on_trade(price=D("101"), event_at=B+timedelta(seconds=1)) == ()
    crossing = entry.on_trade(price=D("99"), event_at=B+timedelta(seconds=2))
    assert len(crossing) == 1 and crossing[0].side == "LONG"
    checkpoint = entry_restart_snapshot(entry)
    restored = restore_entry_restart_snapshot(checkpoint, seeded_geometry=state)
    assert entry_restart_snapshot(restored) == checkpoint
    assert restored.previous_price == D("99")
    assert restored.previous_at == B+timedelta(seconds=2)
    assert restored.seen == {"LONG"}
    # No duplicate first crossing after a normal service restart.
    assert restored.on_trade(price=D("98"), event_at=B+timedelta(seconds=3)) == ()
    # New causal bar resets seen without inventing an exchange fill.
    restored.update_geometry(replace(state, effective_at=B+timedelta(minutes=5)))
    assert restored.seen == set()


def test_no_restore_if_recomputed_immutable_candle_geometry_differs():
    state = geometry()
    snapshot = entry_restart_snapshot(P90L90CausalEntry(state=state))
    with pytest.raises(RuntimeError, match="historical candle geometry"):
        restore_entry_restart_snapshot(
            snapshot, seeded_geometry=replace(state, l53_low_raw=D("100.1"))
        )


def test_fail_closed_for_invalid_checkpoint_temporal_and_side_state():
    state = geometry()
    entry = P90L90CausalEntry(
        state=state, previous_price=D("100.5"),
        previous_at=B+timedelta(seconds=1),
        seen={"LONG"},
    )
    snapshot = entry_restart_snapshot(entry)
    with pytest.raises(ValueError, match="must be UTC"):
        restore_entry_restart_snapshot({
            **snapshot, "previous_at": "2026-10-28T05:00:01"
        }, seeded_geometry=state)
    with pytest.raises(ValueError, match="later than geometry"):
        restore_entry_restart_snapshot({
            **snapshot, "previous_at": (B+timedelta(minutes=6)).isoformat()
        }, seeded_geometry=state)
    with pytest.raises(ValueError, match="outside valid side"):
        restore_entry_restart_snapshot({
            **snapshot, "seen": ["SHORT"]
        }, seeded_geometry=state)
    with pytest.raises(ValueError, match="incomplete"):
        restore_entry_restart_snapshot({
            **snapshot, "previous_price": None
        }, seeded_geometry=state)
    invalid_geometry = dict(snapshot["geometry"])
    invalid_geometry["long_depth_pct"] = "16"
    with pytest.raises(ValueError, match="long depth"):
        restore_entry_restart_snapshot({
            **snapshot, "geometry": invalid_geometry
        }, seeded_geometry=state)


def test_warmup_unready_state_can_resume_without_fabricated_geometry():
    entry = P90L90CausalEntry(
        state=None, previous_price=D("10"),
        previous_at=B-timedelta(minutes=2),
    )
    raw = entry_restart_snapshot(entry)
    assert restore_entry_restart_snapshot(raw, seeded_geometry=None) == entry
    with pytest.raises(RuntimeError, match="historical candle geometry"):
        restore_entry_restart_snapshot(raw, seeded_geometry=geometry())
