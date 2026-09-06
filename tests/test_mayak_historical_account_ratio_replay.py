from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.mayak.research.historical_account_ratio_replay import _replay_symbol
from bybit_workbench.mayak.research.historical_signal_backfill import Signal


def test_account_ratio_replay_is_causal(tmp_path: Path) -> None:
    path = tmp_path / "UNIUSDT.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "long_ratio", "short_ratio"], delimiter=";")
        w.writeheader()
        w.writerow(
            {"timestamp": "2026-05-18T00:00:00+00:00", "long_ratio": "0.6", "short_ratio": "0.4"}
        )
        w.writerow(
            {"timestamp": "2026-05-18T00:05:00+00:00", "long_ratio": "0.1", "short_ratio": "0.9"}
        )
    at = datetime(2026, 5, 18, 0, 4, 59, tzinfo=UTC)
    signal = Signal("UNIUSDT", "Long", at.isoformat(), at.timestamp(), 1.0)
    row = _replay_symbol("UNIUSDT", [signal], path)[0]
    assert row["positioning_long_ratio"] == 0.6
    assert row["positioning_short_ratio"] == 0.4
    assert row["positioning_long_short_imbalance"] == pytest.approx(0.2)
