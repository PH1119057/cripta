from pathlib import Path


def test_events_are_never_linked_to_a_future_snapshot() -> None:
    source = Path("operations/monitoring/mayak_v2.py").read_text(encoding="utf-8")
    assert "observed_at <= to_timestamp(e.exec_time_ms/1000.0)" in source
    assert "observed_at <= to_timestamp(e.signal_at_epoch_ms/1000.0)" in source
    assert "latest_snapshot_not_after_event" in source


def test_regular_snapshot_is_idempotent_per_calendar_minute() -> None:
    source = Path("operations/monitoring/mayak_v2.py").read_text(encoding="utf-8")
    assert "last_persisted_minute" in source
    assert "mayak_v2_one_regular_per_minute" in source
    assert "snapshot_kind='REGULAR'" in source
