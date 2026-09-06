from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.mayak.research.historical_basis_replay import replay_basis
from bybit_workbench.mayak.research.historical_signal_backfill import Signal


def _price_file(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "close_price"])
        writer.writeheader()
        for timestamp, price in rows:
            writer.writerow({"timestamp": timestamp, "close_price": price})


def _funding_file(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "funding_rate"], delimiter=";")
        writer.writeheader()
        for timestamp, rate in rows:
            writer.writerow({"timestamp": timestamp, "funding_rate": rate})


def _signal(at: str) -> Signal:
    dt = datetime.fromisoformat(at).astimezone(UTC)
    return Signal(
        symbol="UNIUSDT",
        direction="Long",
        touch_at=dt.isoformat(),
        touch_epoch=dt.timestamp(),
        original_entry_price=10.0,
    )


def test_mark_index_close_is_not_visible_before_candle_close(tmp_path: Path) -> None:
    mark_dir = tmp_path / "mi"
    funding_dir = tmp_path / "funding"
    _price_file(mark_dir / "UNIUSDT/mark_price_5m.csv", [("2026-05-18T00:00:00+00:00", "101")])
    _price_file(mark_dir / "UNIUSDT/index_price_5m.csv", [("2026-05-18T00:00:00+00:00", "100")])
    _funding_file(funding_dir / "UNIUSDT.csv", [("2026-05-17T16:00:00+00:00", "0.0001")])
    rows = replay_basis(
        [_signal("2026-05-18T00:04:59+00:00"), _signal("2026-05-18T00:05:01+00:00")],
        symbols=("UNIUSDT",),
        mark_index_dir=mark_dir,
        funding_dir=funding_dir,
    )
    assert rows[0]["basis"]["mark_index_premium_pct"] is None
    assert rows[1]["basis"]["mark_index_premium_pct"] == pytest.approx(1.0)


def test_funding_change_becomes_available_only_after_second_event(tmp_path: Path) -> None:
    mark_dir = tmp_path / "mi"
    funding_dir = tmp_path / "funding"
    _price_file(mark_dir / "UNIUSDT/mark_price_5m.csv", [("2026-05-17T23:55:00+00:00", "100")])
    _price_file(mark_dir / "UNIUSDT/index_price_5m.csv", [("2026-05-17T23:55:00+00:00", "100")])
    _funding_file(
        funding_dir / "UNIUSDT.csv",
        [
            ("2026-05-17T16:00:00+00:00", "0.0001"),
            ("2026-05-18T00:00:00+00:00", "0.0003"),
        ],
    )
    rows = replay_basis(
        [_signal("2026-05-17T20:00:00+00:00"), _signal("2026-05-18T00:01:00+00:00")],
        symbols=("UNIUSDT",),
        mark_index_dir=mark_dir,
        funding_dir=funding_dir,
    )
    assert rows[0]["basis"]["funding_rate"] == 0.0001
    assert rows[0]["basis"]["funding_rate_change_from_previous"] is None
    assert abs(rows[1]["basis"]["funding_rate_change_from_previous"] - 0.0002) < 1e-12
