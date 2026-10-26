"""Independent tiny original-format fixtures for #158 same-venue executed VWAP."""
import gzip
import importlib.util
from datetime import date
from pathlib import Path

import pytest

SRC = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_spot_perp_vwap_v1.py"
)
spec = importlib.util.spec_from_file_location("r158_vwap", SRC)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
DAY = date(2026, 5, 19)


def _write(root, symbol, venue, rows):
    p = m.original_archive_path(root, symbol, DAY, venue)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", newline="") as f:
        f.write(rows)
    return p


def _fixtures(root):
    spot = root / "spot"
    perp = root / "perp"
    _write(spot, "APTUSDT", "spot", "id,timestamp,price,volume,side\n"
           "s1,1779148800000,2,3,Buy\n"
           "s2,1779148800123,4,1,Sell\n"
           "s3,1779148860000,6,1,Buy\n")
    _write(perp, "APTUSDT", "perpetual",
           "timestamp,symbol,side,size,price,trdMatchID,homeNotional,foreignNotional\n"
           "1779148800.000,APTUSDT,Buy,1,4,p1,1,4\n"
           "1779148800.700,APTUSDT,Sell,1,2,p2,1,2\n"
           "1779148920.000,APTUSDT,Buy,1,8,p3,1,8\n")
    return spot, perp


def test_vwap_divergence_exact_decimal_no_carry(tmp_path):
    spot, perp = _fixtures(tmp_path)
    manifest, rows = m.calculate_day("APTUSDT", DAY, spot, perp)
    assert len(rows) == 1440
    assert manifest["observed_minutes"] == 1
    assert manifest["spot"]["rows"] == 3
    assert manifest["perpetual"]["rows"] == 3
    assert rows[0]["spot_vwap"] == "2.5"  # (2*3+4*1)/4
    assert rows[0]["perp_vwap"] == "3"    # (4*1+2*1)/2
    assert rows[0]["divergence_bps"] == "2000"
    assert rows[1]["status"] == "NO_DATA"  # Spot only
    assert rows[2]["status"] == "NO_DATA"  # Perp only
    assert rows[3]["divergence_bps"] is None  # No carry
    assert manifest["available_after"] is None
    assert manifest["readiness"] == "DESCRIPTIVE_ONLY_NOT_RESEARCH_READY"


def test_missing_source_is_no_data_not_zero(tmp_path):
    manifest, rows = m.calculate_day("APTUSDT", DAY, tmp_path / "spot", tmp_path / "perp")
    assert manifest["spot"]["source_state"] == "SOURCE_MISSING"
    assert manifest["observed_minutes"] == 0
    assert all(row["status"] == "NO_DATA" for row in rows)


def test_invalid_perp_symbol_fails_closed(tmp_path):
    spot, perp = _fixtures(tmp_path)
    path = m.original_archive_path(perp, "APTUSDT", DAY, "perpetual")
    with gzip.open(path, "wt") as f:
        f.write(
            "timestamp,symbol,side,size,price,trdMatchID,homeNotional,foreignNotional\n"
            "1779148800,DOTUSDT,Buy,1,4,x,1,4\n"
        )
    with pytest.raises(ValueError, match="symbol mismatch"):
        m.calculate_day("APTUSDT", DAY, spot, perp)


def test_out_of_day_and_unordered_fail_closed(tmp_path):
    spot, perp = _fixtures(tmp_path)
    path = m.original_archive_path(spot, "APTUSDT", DAY, "spot")
    with gzip.open(path, "wt") as f:
        f.write(
            "id,timestamp,price,volume,side\n"
            "1,1779148860000,2,1,Buy\n"
            "2,1779148800000,2,1,Buy\n"
        )
    with pytest.raises(ValueError, match="unsorted"):
        m.calculate_day("APTUSDT", DAY, spot, perp)
    with gzip.open(path, "wt") as f:
        f.write("id,timestamp,price,volume,side\n1,1779148740000,2,1,Buy\n")
    with pytest.raises(ValueError, match="out-of-day"):
        m.calculate_day("APTUSDT", DAY, spot, perp)


def test_invalid_prices_and_cohort(tmp_path):
    with pytest.raises(ValueError, match="out-of-cohort"):
        m.original_archive_path(tmp_path, "BTCUSDT", DAY, "spot")
    spot, perp = _fixtures(tmp_path)
    path = m.original_archive_path(spot, "APTUSDT", DAY, "spot")
    with gzip.open(path, "wt") as f:
        f.write("id,timestamp,price,volume,side\n1,1779148800000,NaN,1,Buy\n")
    with pytest.raises(ValueError, match="non-finite"):
        m.calculate_day("APTUSDT", DAY, spot, perp)


def test_spot_exact_timestamps_must_not_decrease_within_minute(tmp_path):
    spot, perp = _fixtures(tmp_path)
    _write(
        spot, "APTUSDT", "spot",
        "id,timestamp,price,volume,side\n"
        "1,1779148800900,2,1,Buy\n"
        "2,1779148800100,2,1,Sell\n",
    )
    with pytest.raises(ValueError, match="unsorted original exchange timestamps"):
        m.calculate_day("APTUSDT", DAY, spot, perp)


def test_perpetual_exact_timestamps_must_not_decrease_within_minute(tmp_path):
    spot, perp = _fixtures(tmp_path)
    _write(
        perp, "APTUSDT", "perpetual",
        "timestamp,symbol,side,size,price,trdMatchID,homeNotional,foreignNotional\n"
        "1779148800.900,APTUSDT,Buy,1,4,p1,1,4\n"
        "1779148800.100,APTUSDT,Sell,1,2,p2,1,2\n",
    )
    with pytest.raises(ValueError, match="unsorted original exchange timestamps"):
        m.calculate_day("APTUSDT", DAY, spot, perp)


def test_original_archive_paths_match_actual_native_storage_layout(tmp_path):
    day = DAY.isoformat()
    spot = m.original_archive_path(tmp_path, "APTUSDT", DAY, "spot")
    perp = m.original_archive_path(tmp_path, "APTUSDT", DAY, "perpetual")
    assert spot == tmp_path / "APTUSDT" / f"APTUSDT_{day}.csv.gz"
    assert perp == (
        tmp_path / "APTUSDT" / "public_trades" / f"APTUSDT{day}.csv.gz"
    )


def test_original_perp_native_home_notional_mismatch_fails_closed(tmp_path):
    spot, perp = _fixtures(tmp_path)
    _write(
        perp, "APTUSDT", "perpetual",
        "timestamp,symbol,side,size,price,trdMatchID,homeNotional,foreignNotional\n"
        "1779148800.000,APTUSDT,Buy,1,4,p1,2,4\n",
    )
    with pytest.raises(ValueError, match="native notional parity mismatch"):
        m.calculate_day("APTUSDT", DAY, spot, perp)


def test_original_perp_native_foreign_notional_mismatch_fails_closed(tmp_path):
    spot, perp = _fixtures(tmp_path)
    _write(
        perp, "APTUSDT", "perpetual",
        "timestamp,symbol,side,size,price,trdMatchID,homeNotional,foreignNotional\n"
        "1779148800.000,APTUSDT,Buy,1,4,p1,1,5\n",
    )
    with pytest.raises(ValueError, match="native notional parity mismatch"):
        m.calculate_day("APTUSDT", DAY, spot, perp)


def test_original_perp_native_parity_is_required_in_source_schema(tmp_path):
    spot, perp = _fixtures(tmp_path)
    _write(
        perp, "APTUSDT", "perpetual",
        "timestamp,symbol,side,size,price,trdMatchID\n"
        "1779148800.000,APTUSDT,Buy,1,4,p1\n",
    )
    with pytest.raises(ValueError, match="invalid perpetual archive columns"):
        m.calculate_day("APTUSDT", DAY, spot, perp)
