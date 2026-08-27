from __future__ import annotations

import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from bybit_workbench.research.orderbook_pilot_v8 import (
    BookState,
    PilotWindow,
    _book_metrics,
    _event_timestamp,
    analyze_archive,
    archive_url,
)


def _window() -> PilotWindow:
    touch = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    return PilotWindow(
        symbol="UNIUSDT",
        direction="Long",
        candidate_bar_at=touch,
        touch_at=touch,
        entry_price=10.0,
        day=date(2026, 8, 7),
        segment=3,
        window_start=touch - timedelta(seconds=120),
        window_end=touch + timedelta(seconds=60),
        flow_state="pressure_then_reversal",
        basis_accel_quartile="Q2",
        first_0_5_vs_1_0="plus_0_5_first",
        first_1_0_vs_1_0="plus_1_0_first",
    )


def test_archive_url_uses_linear_daily_naming() -> None:
    assert archive_url("UNIUSDT", date(2026, 8, 7), 200).endswith(
        "/UNIUSDT/2026-08-07_UNIUSDT_ob200.data.zip"
    )


def test_book_state_applies_snapshot_and_delta() -> None:
    state = BookState.empty()
    state.apply("snapshot", {"b": [["10", "2"]], "a": [["10.1", "3"]]})
    state.apply("delta", {"b": [["10", "0"], ["9.9", "4"]], "a": []})
    assert "10" not in state.bids
    assert state.bids["9.9"] == 4.0
    assert state.asks["10.1"] == 3.0


def test_event_timestamp_prefers_matching_engine_cts() -> None:
    payload = {"ts": 1_000, "cts": 2_000, "data": {}}
    assert _event_timestamp(payload) == datetime.fromtimestamp(2, tz=UTC)


def test_book_metrics_are_directional() -> None:
    state = BookState(
        bids={"10.00": 100.0, "9.99": 50.0},
        asks={"10.01": 10.0, "10.02": 10.0},
        ready=True,
    )
    long_metrics = _book_metrics(state, direction="Long", entry_price=10.0)
    short_metrics = _book_metrics(state, direction="Short", entry_price=10.0)
    assert long_metrics["directional_imbalance_50bps"] > 0
    assert short_metrics["directional_imbalance_50bps"] < 0


def test_analyze_archive_samples_touch_without_lookahead(tmp_path: Path) -> None:
    window = _window()
    path = tmp_path / "sample.data.zip"
    before = int((window.touch_at - timedelta(seconds=130)).timestamp() * 1000)
    at_touch = int(window.touch_at.timestamp() * 1000)
    after = int((window.touch_at + timedelta(seconds=1)).timestamp() * 1000)
    records = [
        {
            "type": "snapshot",
            "ts": before,
            "cts": before,
            "data": {
                "s": "UNIUSDT",
                "b": [["9.99", "100"]],
                "a": [["10.01", "100"]],
                "u": 1,
                "seq": 1,
            },
        },
        {
            "type": "delta",
            "ts": at_touch,
            "cts": at_touch,
            "data": {
                "s": "UNIUSDT",
                "b": [["9.99", "200"]],
                "a": [],
                "u": 2,
                "seq": 2,
            },
        },
        {
            "type": "delta",
            "ts": after,
            "cts": after,
            "data": {
                "s": "UNIUSDT",
                "b": [["9.99", "1"]],
                "a": [],
                "u": 3,
                "seq": 3,
            },
        },
    ]
    payload = "\n".join(json.dumps(record) for record in records) + "\n"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample.data", payload)

    rows, stats = analyze_archive(path, [window])
    assert stats["records"] == 3
    assert len(rows) == 1
    row = rows[0]
    touch_bid = float(row["bid_notional_50bps_p0s"])
    after_bid = float(row["bid_notional_50bps_p10s"])
    assert touch_bid > after_bid


def test_analyze_archive_preserves_outcome_labels(tmp_path: Path) -> None:
    window = _window()
    path = tmp_path / "sample.data.zip"
    timestamp = int((window.touch_at - timedelta(seconds=130)).timestamp() * 1000)
    record = {
        "type": "snapshot",
        "ts": timestamp,
        "cts": timestamp,
        "data": {"s": "UNIUSDT", "b": [["9.99", "1"]], "a": [["10.01", "1"]]},
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample.data", json.dumps(record) + "\n")
    rows, _stats = analyze_archive(path, [window])
    assert rows[0]["first_0_5_vs_1_0"] == "plus_0_5_first"
    assert rows[0]["first_1_0_vs_1_0"] == "plus_1_0_first"
