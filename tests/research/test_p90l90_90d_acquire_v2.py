"""P90L90 full-90 UTC-day raw acquisition and input provenance tests."""
from __future__ import annotations

import csv
import gzip
from datetime import date
from pathlib import Path

import pytest

from scripts.research.p90l90_90d_acquire_v2 import (
    END,
    FIRST,
    WARMUP_LINK_START,
    dates,
    validate_day,
)


def write_archive(path: Path, rows: list[dict[str, str]]) -> None:
    with gzip.open(path, "wt", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=("timestamp", "symbol", "price", "tickDirection", "trdMatchID")
        )
        writer.writeheader()
        writer.writerows(rows)


def item(ts: str, trade_id: str = "a", symbol: str = "LTCUSDT") -> dict[str, str]:
    return {
        "timestamp": ts, "symbol": symbol, "price": "42.10",
        "tickDirection": "PlusTick", "trdMatchID": trade_id,
    }


def test_exact_warmup_plus_full_90_calendar_days() -> None:
    all_days = dates(FIRST, END)
    assert len(all_days) == 100
    assert all_days[0] == date(2026, 5, 8)
    assert all_days[9] == date(2026, 5, 17)
    assert all_days[10] == date(2026, 5, 18)
    assert all_days[-1] == date(2026, 8, 15)
    assert sum(x < WARMUP_LINK_START for x in all_days) == 9


def test_native_archive_daily_qc(tmp_path: Path) -> None:
    p = tmp_path / "sample.csv.gz"
    write_archive(p, [item("1778198400.000", "first"), item("1778284799.999", "last")])
    result = validate_day(p, "LTCUSDT", date(2026, 5, 8))
    assert result["rows"] == 2
    assert result["last_timestamp"] > result["first_timestamp"]


def test_native_archive_rejects_cross_day(tmp_path: Path) -> None:
    p = tmp_path / "sample.csv.gz"
    write_archive(p, [item("1778284800", "wrong_day")])
    with pytest.raises(ValueError, match="off-day"):
        validate_day(p, "LTCUSDT", date(2026, 5, 8))


def test_native_archive_rejects_ordering(tmp_path: Path) -> None:
    p = tmp_path / "sample.csv.gz"
    write_archive(p, [item("1778198401", "one"), item("1778198400", "two")])
    with pytest.raises(ValueError, match="out-of-order"):
        validate_day(p, "LTCUSDT", date(2026, 5, 8))


def test_native_archive_rejects_duplicate_trade_id_same_timestamp(tmp_path: Path) -> None:
    p = tmp_path / "sample.csv.gz"
    write_archive(p, [item("1778198400.1"), item("1778198400.1")])
    with pytest.raises(ValueError, match="duplicate"):
        validate_day(p, "LTCUSDT", date(2026, 5, 8))


def test_native_archive_rejects_empty(tmp_path: Path) -> None:
    p = tmp_path / "sample.csv.gz"
    write_archive(p, [])
    with pytest.raises(ValueError, match="zero"):
        validate_day(p, "LTCUSDT", date(2026, 5, 8))
