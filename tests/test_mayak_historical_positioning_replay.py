from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.mayak.research.historical_positioning_replay import replay_positioning
from bybit_workbench.mayak.research.historical_signal_backfill import Signal


def _write_oi(path: Path, start: datetime, base: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "open_interest"])
        writer.writeheader()
        for index in range(25):
            writer.writerow(
                {
                    "timestamp": (start + timedelta(minutes=5 * index)).isoformat(),
                    "open_interest": base + index * 10,
                }
            )


def test_positioning_replay_uses_same_live_oi_math(tmp_path: Path) -> None:
    start = datetime(2026, 5, 18, 0, 0, tzinfo=UTC)
    for symbol, base in (("BTCUSDT", 1000.0), ("ETHUSDT", 2000.0)):
        _write_oi(tmp_path / f"{symbol}.csv", start, base)
    touch = start + timedelta(minutes=60, seconds=10)
    signal = Signal("BTCUSDT", "Long", touch.isoformat(), touch.timestamp(), 100.0)
    rows = replay_positioning([signal], symbols=("BTCUSDT", "ETHUSDT"), oi_dir=tmp_path)
    positioning = rows[0]["positioning"]
    current = 1000.0 + 12 * 10
    baseline_5 = 1000.0 + 11 * 10
    baseline_60 = 1000.0
    assert positioning["open_interest"] == pytest.approx(current)
    assert positioning["open_interest_change_5m_pct"] == pytest.approx(
        (current / baseline_5 - 1) * 100
    )
    assert positioning["open_interest_change_60m_pct"] == pytest.approx(
        (current / baseline_60 - 1) * 100
    )
    serialized = str(rows[0]).lower()
    assert "outcome" not in serialized
    assert "pnl" not in serialized
