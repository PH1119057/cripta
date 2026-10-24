"""P90L90 V2 full 90-day, ATR200 and nine-ten-day admission/QC invariants."""
import csv
import gzip
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from scripts.research.p90l90_90d_bins_v2 import (
    N_BINS,
    ONE_DAY,
    TEN_DAYS,
    add_close,
    bucket,
    day_index,
    new_bin,
)
from scripts.research.p90l90_entry_90d_v2 import END_TS, START_TS, load_frozen
from scripts.research.p90l90_exit_90d_v2 import scenario_keys


def test_nine_full_consecutive_utc_ten_day_bins() -> None:
    assert END_TS - START_TS == 90 * ONE_DAY == N_BINS * TEN_DAYS
    assert bucket(START_TS) == 0
    assert bucket(START_TS + TEN_DAYS - 0.001) == 0
    assert bucket(START_TS + TEN_DAYS) == 1
    assert bucket(END_TS - 0.001) == 8
    assert day_index(END_TS - 0.001) == 89
    assert day_index(START_TS) == 0
    with pytest.raises(ValueError, match="outside"):
        bucket(START_TS - 1)
    with pytest.raises(ValueError, match="outside"):
        bucket(END_TS)


def test_non_overlapping_bin_boundaries() -> None:
    b = [new_bin(i) for i in range(9)]
    assert b[0]["from_utc"] == "2026-05-18T00:00:00+00:00"
    assert b[-1]["to_utc_exclusive"] == "2026-08-16T00:00:00+00:00"
    assert all(x["to_utc_exclusive"] == y["from_utc"] for x, y in zip(b, b[1:]))


def test_both_width_cohorts_only_on_full90() -> None:
    assert len(scenario_keys("full90")) == 6
    with pytest.raises(ValueError, match="full90"):
        scenario_keys("common")
    with pytest.raises(ValueError, match="full90"):
        scenario_keys("extended")


def _frozen(path: Path, omit_prior_last: bool = False) -> None:
    names = (
        "state_effective_ts", "source_bar_end_ts", "range_low",
        "range_high", "working_width_pct_lower", "lower_inner",
        "upper_inner", "symbol",
    )
    begin = datetime(2026, 5, 11, tzinfo=UTC)
    with gzip.open(path, "wt", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        for d in range(97):
            for i in range(288):
                if omit_prior_last and d == 6 and i == 287:
                    continue
                ts = int((begin + timedelta(days=d, minutes=5*i)).timestamp())
                width = 1 if d <= 6 else 2
                writer.writerow({
                    "symbol": "LTCUSDT", "state_effective_ts": ts,
                    "source_bar_end_ts": ts,
                    "range_low": "100", "range_high": "110",
                    "working_width_pct_lower": str(width), "lower_inner": "100",
                    "upper_inner": str(100 + width),
                })


def test_complete_prior_seven_days_enables_may18_prev7d(tmp_path: Path) -> None:
    path = tmp_path / "states_LTCUSDT.csv.gz"
    _frozen(path)
    states, fixed, prev7, info = load_frozen(path)
    assert len(states) == 90 * 288
    assert len(prev7) == 90
    assert info["first"] == START_TS
    assert info["last"] == END_TS - 300
    assert prev7[datetime(2026, 5, 18, tzinfo=UTC).date()] == D(1)
    assert fixed == D(2)


def test_missing_prior_day_cannot_be_silently_82d_fallback(tmp_path: Path) -> None:
    path = tmp_path / "states_LTCUSDT.csv.gz"
    _frozen(path, omit_prior_last=True)
    with pytest.raises(ValueError, match="288 causal L5 states"):
        load_frozen(path)


def test_per_trade_net_after_modeled_fees_and_hard_stop() -> None:
    b = new_bin(4)
    trade = {
        "gross_usdt": "-1", "entry_fee_usdt": "0.0055",
        "exit_fee_usdt": "0.00495", "net_usdt": "-1.01045",
        "entry_source_order": 10, "exit_source_order": 20,
        "exit_reason": "HARD_STOP", "symbol": "LTCUSDT", "side": "LONG",
    }
    add_close(b, trade)
    assert b["closed_by_exit"] == b["hard_stops"] == b["losers"] == 1
    assert b["net_usdt"] == D("-1.01045")
    assert b["exit_reasons"]["HARD_STOP"]["net_usdt"] == D("-1.01045")
