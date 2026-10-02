from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations.monitoring import mayak_v2


def test_continuity_counts_missed_calendar_minutes_explicitly() -> None:
    collector = mayak_v2.Collector()
    first = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    one = collector._advance_continuity(first)
    assert one["expected_snapshots"] == 1
    assert one["actual_snapshots"] == 1
    assert one["missing_snapshots"] == 0
    later = collector._advance_continuity(first + timedelta(minutes=3))
    assert later["last_gap_minutes"] == 3
    assert later["expected_snapshots"] == 4
    assert later["actual_snapshots"] == 2
    assert later["missing_snapshots"] == 2
    assert later["max_gap_minutes"] == 3
    assert later["coverage_pct"] == 50.0


def test_state_writes_are_atomic_under_concurrent_error_and_main_loop(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "status.json"
    monkeypatch.setattr(mayak_v2, "STATE_PATH", target)
    collector = mayak_v2.Collector()

    def writer(index: int) -> None:
        for iteration in range(25):
            collector._write_state({"writer": index, "iteration": iteration})

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["writer"] in range(4)
    assert payload["iteration"] in range(25)
    assert list(tmp_path.glob("status.json.*.tmp")) == []


def test_error_payload_does_not_mutate_last_good_snapshot(tmp_path, monkeypatch) -> None:
    target = tmp_path / "status.json"
    monkeypatch.setattr(mayak_v2, "STATE_PATH", target)
    collector = mayak_v2.Collector()
    collector.last_snapshot = {"state": "спокойный рынок", "confidence": 0.5, "coins": {}}
    collector._write_error("linear", RuntimeError("test"))
    assert "collector_error" not in collector.last_snapshot
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["collector_error"]["market"] == "linear"


def test_report_continuity_is_explicit_about_gaps() -> None:
    from operations.monitoring.mayak_v2_report import _continuity

    start = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    result = _continuity([start, start + timedelta(minutes=1), start + timedelta(minutes=4)])
    assert result["expected_snapshots"] == 5
    assert result["actual_snapshots"] == 3
    assert result["missing_snapshots"] == 2
    assert result["max_gap_minutes"] == 3
    assert result["coverage_pct"] == 60.0


def test_liquidation_checkpoint_restores_persisted_exact_events() -> None:
    collector = mayak_v2.Collector()
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    rows = [
        ((now - timedelta(minutes=minute)).timestamp(), "BTCUSDT", "Buy", 100.0, 2.0)
        for minute in range(2, 7)
    ]
    collector._restore_liquidation_checkpoint(rows)
    collector.engine.set_instrument_support("linear", set(collector.engine.symbols))
    collector.engine.on_transport(
        "linear", connected=True, timestamp=now.timestamp() - 3600
    )
    collector.engine.on_transport("linear", connected=True, timestamp=now.timestamp())
    collector.engine.on_liquidation(
        "BTCUSDT", (now - timedelta(seconds=10)).timestamp(), "Buy", 100.0, 2.0
    )
    liquidation = collector.engine.snapshot(now)["liquidations"]
    assert liquidation["status"] == "VALID"
    assert liquidation["baseline_nonzero_minutes"] == 5
    assert liquidation["current_1m_usd"] == 200.0


def test_liquidation_checkpoint_query_is_bounded_and_causal() -> None:
    source = Path("operations/monitoring/mayak_v2.py").read_text(encoding="utf-8")
    assert "FROM mayak_v2.liquidations" in source
    assert "occurred_at >= clock_timestamp() - interval '24 hours'" in source
    assert "occurred_at <= clock_timestamp()" in source
    assert "ORDER BY occurred_at" in source
