from __future__ import annotations

import csv
import gzip
from datetime import UTC, datetime
from pathlib import Path

from bybit_workbench.mayak.research.historical_signal_backfill import Signal
from bybit_workbench.mayak.research.historical_spot_signal_backfill import (
    _days_for_intervals,
    _iter_file,
    _iter_file_intervals,
    _merge_intervals,
    _spot_source_symbol,
    replay_symbol,
)


def _signal(at: str) -> Signal:
    dt = datetime.fromisoformat(at).astimezone(UTC)
    return Signal("UNIUSDT", "Long", dt.isoformat(), dt.timestamp(), 1.0)


def _spot(path: Path, rows: list[tuple[int, float, float, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "timestamp", "price", "volume", "side", "rpi"])
        for idx, (ts, price, volume, side) in enumerate(rows, 1):
            writer.writerow([idx, ts, price, volume, side, 0])


def test_iter_spot_archive_is_ordered_and_exact(tmp_path: Path) -> None:
    path = tmp_path / "UNIUSDT-2026-05.csv.gz"
    _spot(path, [(1000, 2.0, 3.0, "buy"), (2000, 2.1, 4.0, "sell")])
    assert list(_iter_file(path, 0.0, 3.0)) == [(1.0, "buy", 2.0, 3.0), (2.0, "sell", 2.1, 4.0)]


def test_replay_does_not_use_future_trade(tmp_path: Path) -> None:
    path = tmp_path / "UNIUSDT-2026-05.csv.gz"
    base = datetime(2026, 5, 18, 0, 0, tzinfo=UTC).timestamp()
    _spot(
        path,
        [
            (int((base + 1) * 1000), 10.0, 2.0, "buy"),
            (int((base + 59) * 1000), 10.0, 1.0, "sell"),
            (int((base + 61) * 1000), 10.0, 1000.0, "sell"),
        ],
    )
    rows, events = replay_symbol(
        "UNIUSDT", [_signal(datetime.fromtimestamp(base + 60, UTC).isoformat())], [path]
    )
    one = rows[0]["spot"]["1m"]
    assert events == 2
    assert one["buy_usd"] == 20.0
    assert one["sell_usd"] == 10.0
    assert one["net_usd"] == 10.0


def test_spot_symbol_mapping_is_explicit() -> None:
    assert _spot_source_symbol("1000PEPEUSDT") == "PEPEUSDT"
    assert _spot_source_symbol("UNIUSDT") == "UNIUSDT"


def test_daily_intervals_cover_only_required_signal_windows() -> None:
    first = _signal("2026-05-18T01:00:00+00:00")
    second = _signal("2026-05-18T10:00:00+00:00")
    intervals = _merge_intervals([first, second])
    assert intervals == [
        (first.touch_epoch - 7200, first.touch_epoch),
        (second.touch_epoch - 7200, second.touch_epoch),
    ]
    days = _days_for_intervals(intervals)
    assert [item.isoformat() for item in days] == ["2026-05-17", "2026-05-18"]


def test_iter_spot_archive_intervals_skips_noncausal_gap(tmp_path: Path) -> None:
    path = tmp_path / "UNIUSDT_2026-05-18.csv.gz"
    _spot(
        path,
        [
            (1_000, 2.0, 1.0, "buy"),
            (2_000, 2.0, 1.0, "sell"),
            (10_000, 2.0, 1.0, "buy"),
        ],
    )
    rows = list(_iter_file_intervals(path, [(0.5, 2.5)]))
    assert [item[0] for item in rows] == [1.0, 2.0]
