from __future__ import annotations

import csv
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.research.orderbook_plan_v7 import (
    BasisSignal,
    OrderbookPlanConfig,
    build_windows,
    choose_pilot_days,
    load_basis_signals,
    probe_orderbook_archive,
    rank_days,
)


def _signal(
    at: str,
    *,
    accepted: bool = True,
    flow_state: str = "pressure_then_reversal",
    oi_tail: bool = False,
) -> BasisSignal:
    timestamp = datetime.fromisoformat(at).replace(tzinfo=UTC)
    return BasisSignal(
        symbol="UNIUSDT",
        direction="Long",
        candidate_bar_at=timestamp,
        touch_at=timestamp,
        entry_price="1.0",
        flow_state=flow_state,
        accepted_after_failure_embargo=accepted,
        oi_tail_danger=oi_tail,
        basis_accel_quartile="Q2",
    )


def test_config_rejects_non_positive_windows() -> None:
    with pytest.raises(ValueError):
        OrderbookPlanConfig(pre_seconds=0)
    with pytest.raises(ValueError):
        OrderbookPlanConfig(post_seconds=0)


def test_load_basis_signals(tmp_path: Path) -> None:
    path = tmp_path / "signals_basis.csv"
    columns = [
        "symbol",
        "direction",
        "candidate_bar_at",
        "touch_at",
        "entry_price",
        "flow_state",
        "accepted_after_failure_embargo",
        "oi_tail_danger",
        "basis_accel_quartile",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "UNIUSDT",
                "direction": "Short",
                "candidate_bar_at": "2026-05-18T00:00:00+00:00",
                "touch_at": "2026-05-18T00:00:10+00:00",
                "entry_price": "5.00",
                "flow_state": "pressure_then_reversal",
                "accepted_after_failure_embargo": "True",
                "oi_tail_danger": "False",
                "basis_accel_quartile": "Q1",
            }
        )
    loaded = load_basis_signals(path)
    assert len(loaded) == 1
    assert loaded[0].direction == "Short"
    assert loaded[0].is_core is True


def test_build_windows_keeps_only_core_signals() -> None:
    signals = [
        _signal("2026-05-18T00:00:00"),
        _signal("2026-05-18T00:05:00", accepted=False),
        _signal("2026-05-18T00:10:00", oi_tail=True),
    ]
    windows = build_windows(
        signals,
        evaluation_start=datetime(2026, 5, 18, tzinfo=UTC),
        config=OrderbookPlanConfig(pre_seconds=120, post_seconds=60),
    )
    assert len(windows) == 1
    assert windows[0].window_start == datetime(2026, 5, 17, 23, 58, tzinfo=UTC)
    assert windows[0].window_end == datetime(2026, 5, 18, 0, 1, tzinfo=UTC)


def test_rank_days_prefers_more_core_signals() -> None:
    signals = [
        _signal("2026-05-18T00:00:00"),
        _signal("2026-05-18T01:00:00"),
        _signal("2026-05-19T00:00:00"),
    ]
    windows = build_windows(
        signals,
        evaluation_start=datetime(2026, 5, 18, tzinfo=UTC),
        config=OrderbookPlanConfig(),
    )
    days = rank_days(signals, windows)
    assert days[0].day.isoformat() == "2026-05-18"
    assert days[0].core_signals == 2
    assert days[-1].cumulative_core_percent == 100.0


def test_choose_pilot_days_returns_best_day_per_segment() -> None:
    signals = [
        _signal("2026-05-18T00:00:00"),
        _signal("2026-05-18T01:00:00"),
        _signal("2026-06-20T00:00:00"),
        _signal("2026-07-20T00:00:00"),
    ]
    start = datetime(2026, 5, 18, tzinfo=UTC)
    windows = build_windows(signals, evaluation_start=start, config=OrderbookPlanConfig())
    days = rank_days(signals, windows)
    pilot = choose_pilot_days(days, 3)
    assert [item.segment for item in pilot] == [1, 2, 3]
    assert pilot[0].core_signals == 2


def test_probe_orderbook_archive_recognizes_snapshot_delta(tmp_path: Path) -> None:
    path = tmp_path / "sample.data.zip"
    records = [
        {
            "type": "snapshot",
            "ts": 1,
            "data": {"s": "UNIUSDT", "b": [["1", "2"]], "a": [["2", "3"]]},
        },
        {
            "type": "delta",
            "ts": 2,
            "data": {"s": "UNIUSDT", "b": [["1", "0"]], "a": []},
        },
    ]
    payload = "\n".join(json.dumps(record) for record in records) + "\n"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample.data", payload)
    result = probe_orderbook_archive(path)
    assert result["records_parsed"] == 2
    assert result["record_types"] == {"snapshot": 1, "delta": 1}
    assert result["looks_like_v5_snapshot_delta"] is True
