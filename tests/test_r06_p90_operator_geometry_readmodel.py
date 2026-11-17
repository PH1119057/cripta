"""R06 exact read-only operator geometry; never a second Entry owner."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bybit_workbench.universal_entry.p90_r06_no_order_state import dump_symbol
from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90CausalEntry,
    P90L90EntryState,
)
from operations.monitoring.r06_pg_only_entry import _operator_geometry_projection

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
OBSERVER = (ROOT / "operations/monitoring/r06_pg_only_entry.py").read_text(
    encoding="utf-8"
)


def _entry() -> P90L90CausalEntry:
    at = datetime(2026, 11, 17, 1, 20, tzinfo=UTC)
    geometry = P90L90EntryState(
        effective_at=at,
        l53_low_raw=Decimal("6.12"),
        l53_high_raw=Decimal("6.80"),
        h9_5m_low_raw=Decimal("6.19"),
        h9_5m_high_raw=Decimal("6.74"),
        h9_15m_low_raw=Decimal("6.19"),
        h9_15m_high_raw=Decimal("6.76"),
        raw_h9_union_span=Decimal("0.57"),
        working_width_pct=Decimal("9.98"),
        previous_seven_day_median=Decimal("9.50"),
        long_depth_pct=Decimal("1.5"),
        short_depth_pct=None,
    )
    return P90L90CausalEntry(
        state=geometry,
        previous_price=Decimal("6.55"),
        previous_at=at + timedelta(seconds=2),
    )


def test_operator_snapshot_is_the_same_causal_object_and_fingerprint():
    entry = _entry()
    snapshot = _operator_geometry_projection({"INJUSDT": entry})
    assert set(snapshot) == {"INJUSDT"}
    row = snapshot["INJUSDT"]
    assert row["effective_at"] == "2026-11-17T01:20:00+00:00"
    assert row["geometry_fingerprint"] == dump_symbol(entry)["geometry_fingerprint"]
    assert row["l53_low_raw"] == "6.12"
    assert row["h9_5m_low_raw"] == row["h9_15m_low_raw"]
    assert row["long_depth_pct"] == "1.5"
    assert row["short_depth_pct"] is None
    assert "raw_h9_union_span" not in row


def test_incomplete_automaton_cannot_publish_operator_geometry():
    entry = _entry()
    entry.previous_price = None
    assert _operator_geometry_projection({"INJUSDT": entry}) == {}
    entry = _entry()
    entry.state = None
    assert _operator_geometry_projection({"INJUSDT": entry}) == {}


def test_projection_persisted_atomically_without_changing_recovery_state():
    assert 'operator_geometry = _operator_geometry_projection(automata)' in OBSERVER
    assert '"operator_geometry": operator_geometry' in OBSERVER
    assert '"by_symbol": {sym: dump_symbol(state)' in OBSERVER
    assert '"version": 1' in OBSERVER
    assert (
        "SET causal_state=%s::jsonb,last_publication_id=%s,checked_at=clock_timestamp()"
        in OBSERVER
    )
    assert "R06_FINANCIAL_OR_CROSS_PLAN_EFFECT_FORBIDDEN" in OBSERVER


def test_dashboard_consumes_only_exact_current_source_and_fingerprint():
    assert "JOIN analytics.native_public_trade_source_sessions src" in APP
    assert "src.state='ACTIVE' AND src.ended_at IS NULL" in APP
    assert "cp.consumer_release_commit=%s" in APP
    assert "geometry.get(\"geometry_fingerprint\")" in APP
    assert 'state.get("geometry_fingerprint")' in APP
    assert 'str(geometry.get("effective_at")) != str(state.get("geometry_at"))' in APP
    assert "now_r06 - previous_at" in APP
    assert "now_r06 - checked_at.astimezone(UTC)" in APP
    assert '"R06_EXACT_ATOMIC_CAUSAL_STATE"' in APP
    assert '"R06_NATIVE_PUBLIC_TRADE"' in APP
    assert 'live_checkpoint = r06_projections.get(symbol)' in APP


def test_legacy_stale_data_cannot_become_current_without_r06():
    assert 'legacy_checkpoint = p90_checkpoints.get(symbol)' in APP
    assert 'checkpoint = live_checkpoint or legacy_checkpoint' in APP
    assert 'entry = checkpoint.get("entry", {}) if checkpoint and geometry_fresh else {}' in APP
    assert 'state = "STALE"' in APP
    assert 'if position_payload is not None:' in APP
