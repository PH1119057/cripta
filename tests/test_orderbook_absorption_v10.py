from __future__ import annotations

import csv
import gzip
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.research.orderbook_absorption_v10 import (
    WindowAccumulator,
    _activity_fields,
    analyze_orderbook_activity,
    analyze_trade_activity,
    merge_trade_metrics,
    observe_delta,
)
from bybit_workbench.research.orderbook_pilot_v8 import BookState, PilotWindow


def _window(direction: str = "Long") -> PilotWindow:
    touch = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    return PilotWindow(
        symbol="UNIUSDT",
        direction=direction,  # type: ignore[arg-type]
        candidate_bar_at=touch - timedelta(minutes=1),
        touch_at=touch,
        entry_price=10.0,
        day=touch.date(),
        segment=3,
        window_start=touch - timedelta(seconds=120),
        window_end=touch + timedelta(seconds=60),
        flow_state="pressure_then_reversal",
        basis_accel_quartile="Q2",
        first_0_5_vs_1_0="favorable_first",
        first_1_0_vs_1_0="favorable_first",
    )


def test_observe_delta_tracks_same_level_support_refill() -> None:
    state = BookState(bids={"9.995": 100.0}, asks={"10.005": 100.0}, ready=True)
    acc = WindowAccumulator(window=_window("Long"))
    observe_delta(acc, state, {"b": [["9.995", "40"]], "a": []})
    state.apply("delta", {"b": [["9.995", "40"]], "a": []})
    observe_delta(acc, state, {"b": [["9.995", "90"]], "a": []})
    fields = _activity_fields(acc)
    assert fields["support_remove_notional_10bps_30s"] == pytest.approx(599.7)
    assert fields["support_add_notional_10bps_30s"] == pytest.approx(499.75)
    assert fields["support_refill_notional_10bps_30s"] == pytest.approx(499.75)
    assert fields["support_refill_events_10bps_30s"] == 1


def test_short_ask_is_support_side() -> None:
    state = BookState(bids={"9.995": 100.0}, asks={"10.005": 100.0}, ready=True)
    acc = WindowAccumulator(window=_window("Short"))
    observe_delta(acc, state, {"b": [], "a": [["10.005", "150"]]})
    fields = _activity_fields(acc)
    assert fields["support_add_notional_10bps_30s"] == pytest.approx(500.25)
    assert fields["adverse_add_notional_10bps_30s"] == 0.0


def test_trade_activity_is_direction_aware(tmp_path: Path) -> None:
    window = _window("Long")
    path = tmp_path / "UNIUSDT2026-08-07.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "side", "size", "price"])
        writer.writeheader()
        writer.writerow(
            {
                "timestamp": str((window.touch_at - timedelta(seconds=20)).timestamp()),
                "side": "Sell",
                "size": "10",
                "price": "10.0",
            }
        )
        writer.writerow(
            {
                "timestamp": str((window.touch_at - timedelta(seconds=5)).timestamp()),
                "side": "Buy",
                "size": "2",
                "price": "10.01",
            }
        )
    metrics = analyze_trade_activity(path, [window])[("Long", window.touch_at.isoformat())]
    assert metrics["adverse_taker_notional_30s"] == pytest.approx(100.0)
    assert metrics["favorable_taker_notional_30s"] == pytest.approx(20.02)
    assert metrics["adverse_taker_dominant_30s"] == "true"
    assert metrics["directional_price_change_bps_30s"] == pytest.approx(10.0)


def test_trade_activity_excludes_post_touch_trade(tmp_path: Path) -> None:
    window = _window("Long")
    path = tmp_path / "UNIUSDT2026-08-07.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "side", "size", "price"])
        writer.writeheader()
        writer.writerow(
            {
                "timestamp": str((window.touch_at - timedelta(seconds=1)).timestamp()),
                "side": "Sell",
                "size": "1",
                "price": "10",
            }
        )
        writer.writerow(
            {
                "timestamp": str((window.touch_at + timedelta(seconds=1)).timestamp()),
                "side": "Sell",
                "size": "1000",
                "price": "9",
            }
        )
    metrics = analyze_trade_activity(path, [window])[("Long", window.touch_at.isoformat())]
    assert metrics["trade_count_30s"] == 1
    assert metrics["adverse_taker_notional_30s"] == pytest.approx(10.0)


def test_merge_trade_metrics_builds_absorption_ratios() -> None:
    row = {
        "direction": "Long",
        "touch_at": _window().touch_at.isoformat(),
        "support_add_notional_10bps_30s": 200.0,
        "support_refill_notional_10bps_30s": 80.0,
        "support_net_notional_10bps_30s": 20.0,
        "support_add_notional_25bps_30s": 300.0,
        "support_refill_notional_25bps_30s": 100.0,
        "support_net_notional_25bps_30s": 50.0,
    }
    metrics = {
        ("Long", row["touch_at"]): {
            "adverse_taker_notional_30s": 100.0,
            "favorable_taker_notional_30s": 40.0,
        }
    }
    merge_trade_metrics([row], metrics)
    assert row["support_add_to_adverse_taker_ratio_10bps_30s"] == pytest.approx(2.0)
    assert row["support_refill_to_adverse_taker_ratio_10bps_30s"] == pytest.approx(0.8)
    assert row["support_net_positive_10bps_30s"] == "true"


def test_synthetic_archive_counts_only_pre_touch_deltas(tmp_path: Path) -> None:
    window = _window("Long")
    archive_path = tmp_path / "book.zip"
    events = [
        {
            "type": "snapshot",
            "cts": int((window.touch_at - timedelta(seconds=40)).timestamp() * 1000),
            "data": {"b": [["9.995", "100"]], "a": [["10.005", "100"]]},
        },
        {
            "type": "delta",
            "cts": int((window.touch_at - timedelta(seconds=20)).timestamp() * 1000),
            "data": {"b": [["9.995", "50"]], "a": []},
        },
        {
            "type": "delta",
            "cts": int((window.touch_at - timedelta(seconds=10)).timestamp() * 1000),
            "data": {"b": [["9.995", "90"]], "a": []},
        },
        {
            "type": "delta",
            "cts": int((window.touch_at + timedelta(seconds=1)).timestamp() * 1000),
            "data": {"b": [["9.995", "1"]], "a": []},
        },
    ]
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("book.data", "\n".join(json.dumps(item) for item in events) + "\n")
    rows, stats = analyze_orderbook_activity(archive_path, [window])
    assert stats["deltas"] == 3
    assert len(rows) == 1
    assert rows[0]["support_remove_notional_10bps_30s"] == pytest.approx(499.75)
    assert rows[0]["support_refill_notional_10bps_30s"] == pytest.approx(399.8)
