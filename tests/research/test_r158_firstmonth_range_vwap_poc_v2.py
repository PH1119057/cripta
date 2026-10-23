"""Research-only first-month price #19/#22 deterministic physical tests."""
import csv
import datetime as dt
import gzip
import importlib.util
from collections import deque
from decimal import Decimal
from pathlib import Path

PROGRAM = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_firstmonth_range_vwap_poc_v2.py"
)
SPEC = importlib.util.spec_from_file_location("r158_range_vwap", PROGRAM)
SUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUT)


def file_with_rows(tmp_path, day, rows):
    p = tmp_path / (day.isoformat() + ".csv.gz")
    start = dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()
    with gzip.open(p, "wt", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=("timestamp", "symbol", "price", "size", "foreignNotional")
        )
        w.writeheader()
        for minute, price, qty in rows:
            w.writerow({
                "timestamp": int(start + minute * 60 + 1),
                "symbol": "APTUSDT",
                "price": price, "size": qty,
                "foreignNotional": str(Decimal(price) * Decimal(qty)),
            })
    return p


def test_causal_40m_crosses_utc_day_and_daily_vwap_resets(tmp_path):
    first = dt.date(2026, 5, 18)
    second = first + dt.timedelta(days=1)
    input1 = file_with_rows(tmp_path, first, [
        (1400 + k, str(Decimal("100") + Decimal(k) / 10), "1")
        for k in range(40)
    ])
    input2 = file_with_rows(tmp_path, second, [(0, "104", "2")])
    history = deque(maxlen=40)
    a, n1 = SUT.build_day("APTUSDT", first, input1, history)
    b, n2 = SUT.build_day("APTUSDT", second, input2, history)
    assert n1 == 40 and n2 == 1
    assert len(a) == len(b) == 1440
    assert a[1400]["factor_19_range_compression_ratio"] is None
    assert a[1439]["factor_19_range_compression_ratio"] == "1"
    assert b[0]["factor_19_range_compression_ratio"] == "1"
    assert b[0]["factor_22_running_vwap_quote_per_base"] == "104"
    assert b[0]["factor_22_daily_running_base_volume"] == "2"
    assert b[0]["factor_22_running_poc_exact_price"] == "104"
    assert b[0]["derived_window_end_utc"] == "2026-05-19T00:01:00+00:00"
    assert b[0]["observed_at_utc"] is None
    assert b[0]["verified_available_after_utc"] is None


def test_base_quantity_weighted_vwap_not_quote_notional_weighted(tmp_path):
    day = dt.date(2026, 5, 18)
    inp = file_with_rows(tmp_path, day, [
        (0, "100", "1"), (0, "200", "2"),
        (1, "200", "1"),
    ])
    history = deque(maxlen=40)
    rows, n = SUT.build_day("APTUSDT", day, inp, history)
    assert n == 3
    assert Decimal(rows[0]["factor_22_running_vwap_quote_per_base"]) == Decimal(500) / 3
    assert rows[0]["factor_22_running_poc_exact_price"] == "200"
    assert rows[1]["factor_22_running_vwap_quote_per_base"] == "175"
    assert rows[1]["factor_22_daily_running_base_volume"] == "4"


def test_no_trade_minute_stays_no_data(tmp_path):
    day = dt.date(2026, 5, 18)
    inp = file_with_rows(tmp_path, day, [(0, "100", "1")])
    history = deque(maxlen=40)
    rows, n = SUT.build_day("APTUSDT", day, inp, history)
    assert n == 1
    assert rows[1]["quality"] == "NO_DATA"
    assert rows[1]["factor_19_range_compression_ratio"] is None
    # VWAP is a running daily aggregate, not a fresh fill.
    assert rows[1]["factor_22_running_vwap_quote_per_base"] == "100"
    assert rows[1]["factor_22_running_poc_exact_price"] == "100"
