"""Independent deterministic P90L90 Entry-source tests, no server/data dependency."""
import csv
import gzip
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from scripts.research.p90l90_entry_v1 import (
    RollingClosedBars,
    crossing,
    geometry_candidates,
    load_frozen,
    source_paths,
)


def test_geometry_strict_same_side_raw_h9_union():
    local = (D("101"), D("119"), D("1.8"))
    # H9 raw union=20, LONG numerator 1 -> 5%; SHORT H9 5m high
    # 120 vs 15m high=121 does not qualify.
    got = geometry_candidates(
        local, (D("100"), D("120")), (D("100"), D("121"))
    )
    assert set(got) == {"LONG"}
    assert got["LONG"]["denominator"] == D("21")
    assert got["LONG"]["numerator"] == D("1")
    assert got["LONG"]["depth_pct"] == D("100") / D("21")


def test_geometry_zero_span_or_nonconcordant_rejected():
    assert not geometry_candidates(
        (D("10"), D("10"), D("0")), (D("10"), D("10")),
        (D("10"), D("10")),
    )
    assert not geometry_candidates(
        (D("10.1"), D("11.5"), D("2")),
        (D("10"), D("12")), (D("9"), D("13")),
    )


def test_depth_boundaries_inclusive_no_tolerance():
    h = (D("100"), D("120"))
    assert "LONG" in geometry_candidates((D("103"), D("119"), D("1")), h, h)
    assert "LONG" not in geometry_candidates((D("103.0000001"), D("119"), D("1")), h, h)
    assert "LONG" in geometry_candidates((D("100"), D("119"), D("1")), h, h)
    assert "LONG" not in geometry_candidates((D("99.999999"), D("119"), D("1")), h, h)


def test_crossing_exact_strict_previous_and_inclusive_touch():
    assert crossing("LONG", D("101"), D("100"), D("100"))
    assert not crossing("LONG", D("100"), D("99"), D("100"))
    assert not crossing("LONG", D("101"), D("101"), D("100"))
    assert crossing("SHORT", D("99"), D("100"), D("100"))
    assert not crossing("SHORT", D("100"), D("101"), D("100"))
    with pytest.raises(ValueError):
        crossing("OTHER", D("1"), D("2"), D("1"))


def test_closed_bar_available_only_after_next_bucket():
    b = RollingClosedBars(300, 2)
    b.update(100.1, D("100"))
    b.update(200.0, D("102"))
    assert b.last_close_ts is None
    assert b.extrema() is None
    b.update(301.1, D("105"))
    assert b.last_close_ts == 300
    assert b.extrema() is None  # ATR200 is not seeded yet.
    b.update(601.0, D("110"))
    assert b.last_close_ts == 600
    assert b.extrema() is None
    assert b.bars[-1] == (D("105"), D("105"))


def test_missing_raw_bar_is_not_forward_filled():
    b = RollingClosedBars(300, 2)
    b.update(10, D("100"))
    b.update(610, D("101"))
    assert b.gaps == 1
    assert b.bars == b.bars.__class__(maxlen=2)
    assert b.last_close_ts is None


def test_frozen_clean_width_and_prev7_complete_utc_days(tmp_path: Path):
    p = tmp_path / "states_LTCUSDT.csv.gz"
    fields = [
        "state_effective_ts", "source_bar_end_ts", "range_low",
        "range_high", "working_width_pct_lower", "lower_inner",
        "upper_inner", "symbol",
    ]
    start = datetime(2026, 5, 19, tzinfo=timezone.utc)
    with gzip.open(p, "wt", newline="") as fd:
        w = csv.DictWriter(fd, fieldnames=fields)
        w.writeheader()
        for day in range(8):
            for i in range(288):
                ts = int((start + timedelta(days=day, minutes=5 * i)).timestamp())
                width = 1 + day
                w.writerow({
                    "state_effective_ts": ts,
                    "source_bar_end_ts": ts,
                    "range_low": "100",
                    "range_high": "110",
                    "working_width_pct_lower": str(width),
                    "lower_inner": "100",
                    "upper_inner": str(100 + width),
                    "symbol": "LTCUSDT",
                })
    states, fixed, prev7, info = load_frozen(p)
    assert info["states"] == 2304
    assert fixed == D("4.5")
    assert prev7[start.date() + timedelta(days=7)] == D("4")
    assert start.date() + timedelta(days=6) not in prev7
    assert states[int(start.timestamp())][2] == D("1")


def test_frozen_state_gap_is_qc_failure(tmp_path: Path):
    p = tmp_path / "states_LTCUSDT.csv.gz"
    fields = [
        "state_effective_ts", "source_bar_end_ts", "range_low",
        "range_high", "working_width_pct_lower", "lower_inner",
        "upper_inner", "symbol",
    ]
    with gzip.open(p, "wt", newline="") as fd:
        w = csv.DictWriter(fd, fieldnames=fields)
        w.writeheader()
        for ts in (1779062400, 1779063000):
            w.writerow(dict(zip(fields, [ts, ts, "100", "110", "5", "100", "105", "LTCUSDT"])))
    with pytest.raises(ValueError, match="timeline gap"):
        load_frozen(p)


def test_source_inventory_exact_day_paths(tmp_path: Path):
    d = tmp_path / "LTCUSDT" / "public_trades"
    d.mkdir(parents=True)
    for day in ("2026-05-17", "2026-05-18"):
        (d / f"LTCUSDT{day}.csv.gz").write_bytes(b"nonempty")
    found = source_paths(tmp_path, "LTCUSDT", "2026-05-17", "2026-05-18")
    assert len(found) == 2
    with pytest.raises(FileNotFoundError):
        source_paths(tmp_path, "LTCUSDT", "2026-05-17", "2026-05-19")
