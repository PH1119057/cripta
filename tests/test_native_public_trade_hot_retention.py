"""Scope tests for bounded native publicTrade hot retention (#642)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "operations/monitoring/native_public_trade_hot_retention.py"
SPEC = importlib.util.spec_from_file_location(
    "native_public_trade_hot_retention", SOURCE
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_native_retention_scope_is_bounded_and_position_pinned():
    assert MODULE.RETENTION_MINUTES == 15
    assert MODULE.DEFAULT_BATCH_LIMIT == 50_000
    assert MODULE.MAX_BATCH_LIMIT == 50_000
    text = SOURCE.read_text()
    assert "DELETE FROM analytics.native_public_trade_source_events" in text
    assert "DELETE FROM analytics.native_public_trade_events" in text
    assert "p.fill_at - interval '60 seconds'" in text
    assert "COALESCE(p.closed_at,clock_timestamp())" in text
    assert "s.state='GAP'" in text
    assert "ended_at < %s" in text
    assert "e.accepted_at < %s" in text
    assert "runtime.hot_orders" in text
    assert "OPEN_POSITION_STATE_DRIFT" in text
    for protected in (
        "analytics.closed_candles",
        "analytics.market_minutes",
        "strategy_entry.entry_decisions",
        "strategy_exit.exit_decisions",
        "runtime.position_ownership",
        "runtime.hot_positions",
        "runtime.hot_orders",
    ):
        assert f"DELETE FROM {protected}" not in text
        assert f"TRUNCATE TABLE {protected}" not in text
    assert "VACUUM (FULL" not in text
    assert "VACUUM FULL" not in text


def test_all_native_point_owners_are_explicit_pins():
    assert set(MODULE.POINT_PIN_TABLES) == {
        "analytics.native_public_trade_observer_delivery",
        "monitoring.opportunity_source_checkpoints",
        "strategy_entry.p90_r06_checkpoint_release_bridges",
        "strategy_entry.p90_r06_publication_observations",
        "strategy_entry.p90_scoped_native_consumer_checkpoints",
    }


def test_parent_delete_requires_no_source_event_and_no_cursor_owner():
    text = SOURCE.read_text()
    assert "FROM analytics.native_public_trade_source_events e" in text
    assert "FROM analytics.native_public_trade_source_cursors c" in text
    assert "deleted_native_keys" in text


def test_systemd_and_installer_wire_exact_helper():
    service = (
        ROOT / "operations/systemd/cripta-market-hot-retention.service"
    ).read_text()
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text()
    helper = "cripta-native-public-trade-hot-retention.py"
    assert (
        f"/usr/local/libexec/{helper} --apply --batch-limit 15000"
        in service
    )
    assert "User=postgres" in service
    assert f"/usr/local/libexec/{helper}" in installer
    assert "native_public_trade_hot_retention.py" in installer
    assert "native publicTrade retention actor path bytes differ" in installer

def test_gap_cleanup_uses_session_pk_not_global_gap_sort():
    text = SOURCE.read_text()
    assert "def _eligible_gap_sessions(" in text
    assert "WHERE e.source_session_id=%s" in text
    assert "ORDER BY s.ended_at,s.source_session_id" not in text
    assert (
        "JOIN analytics.native_public_trade_source_events e\n"
        "                  USING(source_session_id)"
        not in text
    )

def test_parent_cleanup_uses_fk_not_source_table_rescan():
    text = SOURCE.read_text()
    start = text.index("def _delete_unowned_parents(")
    end = text.index("\n\ndef _inline_point_exclusion(", start)
    block = text[start:end]
    assert "cursor_owned AS MATERIALIZED" in block
    assert "FROM analytics.native_public_trade_source_events" not in block
    assert "PARENT_SOURCE_EVENT_FK_INVALID" in text
    assert "_validate_parent_fk(connection)" in text

