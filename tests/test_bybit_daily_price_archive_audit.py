"""Official daily public-trade archive comparison; never an Exit authorization."""
from __future__ import annotations

import csv
import gzip
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.market_data.bybit_daily_price_archive_audit import (
    BybitDailyPriceArchiveUnverified,
    audit_bybit_daily_price_archive,
)

DAY = date(2026, 11, 8)
START = datetime(2026, 11, 8, 1, 0, 0, tzinfo=UTC)
CHECKED = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
SYMBOL = "ARBUSDT"
SESSION = "a" * 32
COMMIT = "b" * 40
FIELDS = [
    "timestamp", "symbol", "side", "size", "price", "tickDirection",
    "trdMatchID", "grossValue", "homeNotional", "foreignNotional",
]


def _trade(id_: str, seconds: int, price: str, *, size: str = "1") -> dict[str, str]:
    return {
        "timestamp": str(Decimal(str((START + timedelta(seconds=seconds)).timestamp()))),
        "symbol": SYMBOL,
        "side": "Sell" if seconds == 2 else "Buy",
        "size": size,
        "price": price,
        "tickDirection": "ZeroMinusTick",
        "trdMatchID": id_,
        "grossValue": "100", "homeNotional": size, "foreignNotional": "1",
    }


def _rows():
    return [
        _trade("before", -1, "0.1742"),
        _trade("first", 1, "0.1730"),
        _trade("second", 2, "0.1710"),
        _trade("after", 4, "0.1750"),
    ]


def _write(directory: Path, *, rows=None, truncate: bool = False) -> Path:
    path = directory / "ARBUSDT2026-11-08.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(_rows() if rows is None else rows)
    if truncate:
        path.write_bytes(path.read_bytes()[:-8])
    return path


def _lookup():
    return {
        "first": (int((START + timedelta(seconds=1)).timestamp() * 1000),
                  Decimal("0.1730"), Decimal("1"), "Buy"),
        "second": (int((START + timedelta(seconds=2)).timestamp() * 1000),
                   Decimal("0.1710"), Decimal("1"), "Sell"),
    }


def _audit(path, *, natives=None, count=2, lookup=None, **changes):
    original = _lookup() if natives is None else natives
    query = lookup if lookup is not None else (
        lambda ids: {key: original[key] for key in ids if key in original}
    )
    kwargs = {
        "symbol": SYMBOL,
        "archive_day": DAY,
        "source_session_id": SESSION,
        "source_commit": COMMIT,
        "coverage_start": START,
        "coverage_end": START + timedelta(seconds=3),
        "checked_at": CHECKED,
        "native_interval_row_count": count,
        "lookup_original_native": query,
        "max_archive_rows": 20,
    }
    kwargs.update(changes)
    return audit_bybit_daily_price_archive(path, **kwargs)


def test_complete_gzip_matches_each_original_ws_trade_but_not_exit_authority(tmp_path):
    path = _write(tmp_path)
    result = _audit(path)
    assert result.symbol == SYMBOL
    assert result.archive_total_rows == 4
    assert result.interval_matched_rows == 2
    assert result.interval_low == Decimal("0.1710")
    assert result.interval_high == Decimal("0.1730")
    assert result.archive_gzip_sha256
    assert len(result.archive_gzip_sha256) == 64
    assert result.official_archive_url == (
        "https://public.bybit.com/trading/ARBUSDT/ARBUSDT2026-11-08.csv.gz"
    )
    assert result.original_trade_ids_matched
    assert not result.archive_issuer_verified
    assert not result.price_history_verified
    assert not result.native_continuity_verified
    assert not result.exit_ready
    assert result.reason.endswith("COVERAGE_ATTESTATION_MISSING")


def test_repeated_audit_is_deterministic_for_immutable_archive(tmp_path):
    path = _write(tmp_path)
    first = _audit(path)
    second = _audit(path)
    assert first == second


@pytest.mark.parametrize("fault", [
    "missing-native", "extra-native", "price-drift", "size-drift",
    "side-drift", "time-drift", "foreign-symbol", "duplicate-id",
    "archive-price-invalid", "archive-qty-invalid", "out-of-order",
    "foreign-day", "budget", "CRC-truncated", "CSV-missing-field",
    "archive-row-missing", "archive-extra-row", "wrong-file",
])
def test_incomplete_price_history_fails_closed(tmp_path, fault):
    rows = _rows()
    natives = _lookup()
    count = 2
    changes = {}
    if fault == "missing-native":
        natives.pop("second")
    elif fault == "extra-native":
        count = 3
    elif fault == "price-drift":
        natives["second"] = (natives["second"][0], Decimal("0.1709"),
                             natives["second"][2], natives["second"][3])
    elif fault == "size-drift":
        natives["second"] = (natives["second"][0], natives["second"][1],
                             Decimal("2"), natives["second"][3])
    elif fault == "side-drift":
        natives["second"] = (natives["second"][0], natives["second"][1],
                             natives["second"][2], "Buy")
    elif fault == "time-drift":
        natives["second"] = (natives["second"][0] + 1, *natives["second"][1:])
    elif fault == "foreign-symbol":
        rows[1]["symbol"] = "BTCUSDT"
    elif fault == "duplicate-id":
        rows[2]["trdMatchID"] = "first"
    elif fault == "archive-price-invalid":
        rows[2]["price"] = "NaN"
    elif fault == "archive-qty-invalid":
        rows[2]["size"] = "-1"
    elif fault == "out-of-order":
        rows[2]["timestamp"] = rows[0]["timestamp"]
    elif fault == "foreign-day":
        rows[2]["timestamp"] = str(Decimal("1794182400"))
    elif fault == "budget":
        changes["max_archive_rows"] = 2
    elif fault == "archive-row-missing":
        rows.pop(2)
    elif fault == "archive-extra-row":
        rows.insert(2, _trade("extra", 1, "0.1720"))
    elif fault == "wrong-file":
        pass
    path = _write(tmp_path, rows=rows, truncate=fault == "CRC-truncated")
    if fault == "wrong-file":
        path = path.rename(tmp_path / "ARBUSDT2026-11-07.csv.gz")
    if fault == "CSV-missing-field":
        with gzip.open(path, "wt", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS[:-1],
                                    extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    with pytest.raises(BybitDailyPriceArchiveUnverified):
        _audit(path, natives=natives, count=count, **changes)


@pytest.mark.parametrize("changes", [
    {"checked_at": datetime(2026, 11, 8, 23, 59, 59, tzinfo=UTC)},
    {"coverage_start": START.replace(tzinfo=None)},
    {"coverage_end": START.replace(tzinfo=None)},
    {"coverage_end": START - timedelta(seconds=1)},
    {"coverage_end": datetime(2026, 11, 9, tzinfo=UTC)},
    {"source_session_id": "bad-id"},
    {"source_commit": "no-release"},
    {"archive_day": datetime(2026, 11, 8, tzinfo=UTC)},
    {"native_interval_row_count": 0},
    {"max_archive_rows": 4_000_001},
])
def test_invalid_provenance_and_unfinished_utc_day_are_rejected(tmp_path, changes):
    path = _write(tmp_path)
    with pytest.raises(BybitDailyPriceArchiveUnverified):
        _audit(path, **changes)


def test_lookup_is_id_scoped_and_no_trading_action_possible(tmp_path):
    path = _write(tmp_path)
    seen = []

    def lookup(ids):
        assert set(ids) == {"first", "second"}
        seen.append(ids)
        return {key: _lookup()[key] for key in ids}

    audit = _audit(path, lookup=lookup)
    assert len(seen) == 1
    assert audit.interval_matched_rows == 2
    assert not hasattr(audit, "trailing_request")
    assert not hasattr(audit, "source_seq")
    assert not hasattr(audit, "complete_coverage_verified")


def test_false_positive_provider_label_never_grants_verified_price_mfe(tmp_path):
    path = _write(tmp_path)
    result = _audit(path)
    assert result.official_archive_url.startswith("https://public.bybit.com/")
    assert result.price_history_verified is False
    assert result.exit_ready is False
