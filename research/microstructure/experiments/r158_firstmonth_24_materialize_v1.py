#!/usr/bin/env python3
"""Issue #158 / #24 full first-month liquidation-spike materializer.

Requires a previously verified original-Parquet ↔ normalized full-row parity
report. Uses normalized rows only as a compact materialization input after that
gate, while preserving exact object-hour coverage: missing object/hour is
UNKNOWN, never zero. Writes 1m/5m/15m grids for one exact symbol.

Research only; vendor received_time is not CRIPTA available_after and published
bankruptcy price is not actual execution price.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
END_EXCLUSIVE = date(2026, 6, 17)
WINDOWS = (1, 5, 15)
VENDOR_ROOT = Path(
    "/data/cripta/datasets/external/cryptohftdata/bybit_liquidations_v1"
)
NORMALIZED_ROOT = Path(
    "/data/cripta/research/runs/strategy_full20_liq_20260922/liquidations"
)
PARITY_ROOT = Path(
    "/data/cripta/research/runs/r158_liquidation_parquet_parity_20261030"
)
OUTPUT_ROOT = Path("/data/cripta/research/runs/r158_firstmonth_24_20261031")
FORMULA = "R158_LIQ_PUBLISHED_OBJECTS_BY_SIDE_V2"
VERSION = "R158_FIRSTMONTH_24_MATERIALIZER_V1"
KEY_RE = re.compile(
    r"^bybit/(20\d\d-\d\d-\d\d)/(\d\d)/"
    r"([A-Z0-9]+)_liquidations[.]parquet[.]zst$"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decimal(value: object) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("INVALID_LIQUIDATION_DECIMAL") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("NONPOSITIVE_LIQUIDATION_DECIMAL")
    return number


def actor_uid(require_actor: bool) -> int:
    if not hasattr(os, "geteuid"):
        if require_actor:
            raise OSError("UNIX_EFFECTIVE_UID_REQUIRED")
        return -1
    uid = os.geteuid()
    if require_actor:
        import pwd

        if uid != pwd.getpwnam("cripta").pw_uid:
            raise PermissionError("EFFECTIVE_ACTOR_NOT_CRIPTA")
    return uid


def write_json_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_parity(
    parity_root: Path,
    vendor_root: Path,
    *,
    expected_total_objects: int | None = 1157,
    expected_total_rows: int | None = 8998,
) -> tuple[dict, str]:
    path = parity_root / "parquet_normalized_parity_report.json"
    if not path.is_file():
        raise FileNotFoundError("PARQUET_PARITY_REPORT_MISSING")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("status") != "ORIGINAL_PARQUET_NORMALIZED_FULL_ROW_PARITY_PASS":
        raise ValueError("PARQUET_PARITY_GATE_NOT_PASS")
    objects_path = vendor_root / "OBJECTS.jsonl"
    if report.get("receipt_manifest_sha256") != sha256(objects_path):
        raise ValueError("PARQUET_PARITY_RECEIPT_MANIFEST_DRIFT")
    if (
        expected_total_objects is not None
        and int(report.get("objects", -1)) != expected_total_objects
    ):
        raise ValueError("PARQUET_PARITY_OBJECT_CARDINALITY_DRIFT")
    if (
        expected_total_rows is not None
        and int(report.get("raw_rows", -1)) != expected_total_rows
    ):
        raise ValueError("PARQUET_PARITY_ROW_CARDINALITY_DRIFT")
    return report, sha256(path)


def covered_hours(
    vendor_root: Path, symbol: str
) -> tuple[set[tuple[str, int]], list[dict]]:
    result = set()
    receipts = []
    with (vendor_root / "OBJECTS.jsonl").open("rt", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            if item.get("symbol") != symbol:
                continue
            day = date.fromisoformat(item["date"])
            if not START <= day < END_EXCLUSIVE:
                continue
            match = KEY_RE.fullmatch(item["key"])
            if (
                not match
                or match.group(1) != day.isoformat()
                or match.group(3) != symbol
            ):
                raise ValueError("OBJECT_RECEIPT_IDENTITY_MISMATCH")
            hour = int(match.group(2))
            if item.get("hour") != f"{hour:02d}":
                raise ValueError("OBJECT_RECEIPT_HOUR_MISMATCH")
            key = (day.isoformat(), hour)
            if key in result:
                raise ValueError("DUPLICATE_OBJECT_RECEIPT_HOUR")
            result.add(key)
            receipts.append(
                {
                    "date_utc": day.isoformat(),
                    "hour": hour,
                    "key": item["key"],
                    "bytes": int(item["bytes"]),
                    "sha256": item["sha256"],
                }
            )
    return result, sorted(receipts, key=lambda x: (x["date_utc"], x["hour"]))


def aggregate_normalized(
    normalized_root: Path,
    symbol: str,
    covered: set[tuple[str, int]],
) -> tuple[dict[int, dict[int, dict]], int, str]:
    path = normalized_root / f"{symbol}.csv.gz"
    if not path.is_file():
        raise FileNotFoundError("NORMALIZED_LIQUIDATION_FILE_MISSING")
    start_us = int(
        datetime(START.year, START.month, START.day, tzinfo=UTC).timestamp()
        * 1_000_000
    )
    end_us = int(
        datetime(
            END_EXCLUSIVE.year,
            END_EXCLUSIVE.month,
            END_EXCLUSIVE.day,
            tzinfo=UTC,
        ).timestamp()
        * 1_000_000
    )
    bins: dict[int, dict[int, dict]] = {
        window: defaultdict(
            lambda: {
                "count": 0,
                "quote": Decimal(0),
                "LONG_count": 0,
                "LONG_quote": Decimal(0),
                "SHORT_count": 0,
                "SHORT_quote": Decimal(0),
            }
        )
        for window in WINDOWS
    }
    rows = 0
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        needed = {
            "observed_timestamp_us",
            "symbol",
            "liquidated_position_side",
            "price",
            "size",
            "source_path",
            "order_status",
        }
        if not needed.issubset(reader.fieldnames or []):
            raise ValueError("NORMALIZED_HEADER_MISMATCH")
        for row in reader:
            micros = int(row["observed_timestamp_us"])
            if not start_us <= micros < end_us:
                continue
            if row["symbol"] != symbol:
                raise ValueError("NORMALIZED_SYMBOL_MISMATCH")
            observed = datetime.fromtimestamp(micros / 1_000_000, UTC)
            hour_key = (observed.date().isoformat(), observed.hour)
            if hour_key not in covered:
                raise ValueError("NORMALIZED_ROW_WITHOUT_OBJECT_COVERAGE")
            side = row["liquidated_position_side"]
            if side not in ("LONG", "SHORT"):
                raise ValueError("NORMALIZED_LIQUIDATED_SIDE_INVALID")
            if not row["order_status"]:
                raise ValueError("NORMALIZED_ORDER_STATUS_MISSING")
            amount = decimal(row["price"]) * decimal(row["size"])
            for window in WINDOWS:
                index = (micros - start_us) // (window * 60 * 1_000_000)
                cell = bins[window][index]
                cell["count"] += 1
                cell["quote"] += amount
                cell[f"{side}_count"] += 1
                cell[f"{side}_quote"] += amount
            rows += 1
    return bins, rows, sha256(path)


def rows_for_symbol(
    symbol: str,
    covered: set[tuple[str, int]],
    bins: dict[int, dict[int, dict]],
) -> list[dict]:
    month_start = datetime(START.year, START.month, START.day, tzinfo=UTC)
    rows = []
    for window in WINDOWS:
        total_bins = 30 * 24 * (60 // window)
        for index in range(total_bins):
            stamp = month_start + timedelta(minutes=index * window)
            hour_key = (stamp.date().isoformat(), stamp.hour)
            source_present = hour_key in covered
            cell = bins[window].get(index)
            if not source_present:
                metrics = {
                    "count": None,
                    "published_bankruptcy_price_x_qty_usdt": None,
                    "liquidated_long_count": None,
                    "liquidated_long_quote_usdt": None,
                    "liquidated_short_count": None,
                    "liquidated_short_quote_usdt": None,
                }
                quality = "UNKNOWN_MISSING_ORIGINAL_OBJECT_HOUR"
            else:
                cell = cell or {
                    "count": 0,
                    "quote": Decimal(0),
                    "LONG_count": 0,
                    "LONG_quote": Decimal(0),
                    "SHORT_count": 0,
                    "SHORT_quote": Decimal(0),
                }
                metrics = {
                    "count": int(cell["count"]),
                    "published_bankruptcy_price_x_qty_usdt": str(cell["quote"]),
                    "liquidated_long_count": int(cell["LONG_count"]),
                    "liquidated_long_quote_usdt": str(cell["LONG_quote"]),
                    "liquidated_short_count": int(cell["SHORT_count"]),
                    "liquidated_short_quote_usdt": str(cell["SHORT_quote"]),
                }
                quality = "SOURCE_OBJECT_PRESENT"
            rows.append(
                {
                    "venue_id": "Bybit",
                    "instrument_id": symbol,
                    "indicator_id": 24,
                    "formula_version": FORMULA,
                    "window_minutes": window,
                    "window_start_utc": stamp.isoformat(),
                    "vendor_received_time_basis": True,
                    "observed_at_utc": None,
                    "verified_available_after_utc": None,
                    "quality": quality,
                    **metrics,
                }
            )
    return rows


def materialize_symbol(
    symbol: str,
    source_commit: str,
    *,
    vendor_root: Path = VENDOR_ROOT,
    normalized_root: Path = NORMALIZED_ROOT,
    parity_root: Path = PARITY_ROOT,
    output_root: Path = OUTPUT_ROOT,
    require_actor: bool = True,
    expected_total_objects: int | None = 1157,
    expected_total_rows: int | None = 8998,
) -> dict:
    if symbol not in COHORT:
        raise ValueError("SYMBOL_OUTSIDE_OWNER_COHORT")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("SOURCE_COMMIT_MUST_BE_EXACT_40HEX")
    uid = actor_uid(require_actor)
    if not output_root.parent.is_dir():
        raise FileNotFoundError("OUTPUT_PARENT_MISSING")
    if not os.access(output_root.parent, os.W_OK | os.X_OK):
        raise PermissionError("OUTPUT_PARENT_NOT_WRITABLE")

    parity, parity_sha = load_parity(
        parity_root,
        vendor_root,
        expected_total_objects=expected_total_objects,
        expected_total_rows=expected_total_rows,
    )
    expected_symbol = parity.get("by_symbol", {}).get(symbol)
    if not isinstance(expected_symbol, dict):
        raise ValueError("PARITY_SYMBOL_SUMMARY_MISSING")

    covered, receipts = covered_hours(vendor_root, symbol)
    if len(covered) != int(expected_symbol["objects"]):
        raise ValueError("SYMBOL_OBJECT_COUNT_DRIFT")
    bins, normalized_rows, normalized_sha = aggregate_normalized(
        normalized_root, symbol, covered
    )
    if normalized_rows != int(expected_symbol["rows"]):
        raise ValueError("SYMBOL_NORMALIZED_ROW_COUNT_DRIFT")
    rows = rows_for_symbol(symbol, covered, bins)
    expected_rows = sum(30 * 24 * (60 // w) for w in WINDOWS)
    if len(rows) != expected_rows:
        raise ValueError("OUTPUT_GRID_CARDINALITY_MISMATCH")

    os.umask(0o007)
    output_root.mkdir(mode=0o2770, exist_ok=True)
    target = output_root / symbol
    if target.exists():
        raise FileExistsError("IMMUTABLE_SYMBOL_OUTPUT_ALREADY_EXISTS")
    target.mkdir(mode=0o2770)
    snapshots = target / "source_snapshot"
    snapshots.mkdir(mode=0o2770)
    this_script = Path(__file__).resolve()
    shutil.copyfile(this_script, snapshots / this_script.name)

    write_json_new(
        target / "run_start.json",
        {
            "status": "STARTED_NOT_COMPLETION_PROOF",
            "actor_uid": uid,
            "symbol": symbol,
            "source_commit": source_commit,
            "period_utc": "[2026-05-18T00:00:00Z,2026-06-17T00:00:00Z)",
            "read_vendor_root": str(vendor_root),
            "read_normalized_root": str(normalized_root),
            "parity_report_sha256": parity_sha,
            "write_root": str(target),
            "delete_operations": "NONE",
            "db_service_live_mutation": False,
        },
    )

    part = target / "liquidation_windows.jsonl.gz.part"
    final = target / "liquidation_windows.jsonl.gz"
    with part.open("xb") as binary:
        with gzip.GzipFile(fileobj=binary, mode="wb", mtime=0) as zipped:
            import io

            with io.TextIOWrapper(zipped, encoding="utf-8", newline="\n") as out:
                for row in rows:
                    out.write(json.dumps(row, sort_keys=True) + "\n")
        binary.flush()
        os.fsync(binary.fileno())
    os.rename(part, final)
    write_json_new(
        target / "input_provenance.json",
        {
            "object_receipts": receipts,
            "object_receipts_count": len(receipts),
            "normalized_file": str(normalized_root / f"{symbol}.csv.gz"),
            "normalized_sha256": normalized_sha,
            "normalized_rows": normalized_rows,
            "parity_report_sha256": parity_sha,
            "objects_manifest_sha256": sha256(vendor_root / "OBJECTS.jsonl"),
        },
    )
    missing_hours = 720 - len(covered)
    result = {
        "status": "LIQUIDATION_24_FIRSTMONTH_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC",
        "symbol": symbol,
        "indicator_id": 24,
        "formula_version": FORMULA,
        "windows_minutes": list(WINDOWS),
        "rows": len(rows),
        "original_object_hours": len(covered),
        "missing_object_hours": missing_hours,
        "missing_object_hours_status": "UNKNOWN_NOT_ZERO",
        "normalized_rows": normalized_rows,
        "output_file": str(final),
        "output_sha256": sha256(final),
        "parity_report_sha256": parity_sha,
        "vendor_received_time_is_not_cripta_available_after": True,
        "bankruptcy_price_is_not_execution_price": True,
        "verified_available_after": False,
        "research_ready": False,
        "classification": "DESCRIPTIVE_ONLY",
        "source_commit": source_commit,
        "materializer_version": VERSION,
        "materializer_sha256": sha256(this_script),
    }
    write_json_new(target / "run_final.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=COHORT)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    print(json.dumps(materialize_symbol(args.symbol, args.source_commit), sort_keys=True))


if __name__ == "__main__":
    main()
