#!/usr/bin/env python3
"""Independent full-row QC for Issue #158 / #24 liquidation windows.

Does not import primary #24 reader/materializer and does not use normalized CSV
for recomputation. Re-reads original CryptoHFTData Parquet/Zstd objects with the
isolated PyArrow site, verifies receipt byte/hash/schema/native invariants, then
independently builds the 1m/5m/15m coverage-aware grids and compares every row.

Missing object/hour remains UNKNOWN, never a synthetic zero.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
END_EXCLUSIVE = date(2026, 6, 17)
WINDOWS = (1, 5, 15)
VENDOR_ROOT = Path(
    "/data/cripta/datasets/external/cryptohftdata/bybit_liquidations_v1"
)
PYARROW_ROOT = Path(
    "/data/cripta/research/cache/r158_pyarrow26_site_20261030"
)
RUN_ROOT = Path("/data/cripta/research/runs/r158_firstmonth_24_20261031")
FORMULA = "R158_LIQ_PUBLISHED_OBJECTS_BY_SIDE_V2"
VERSION = "R158_FIRSTMONTH_24_INDEPENDENT_ORIGINAL_PARQUET_QC_V1"
REQUIRED = {
    "received_time",
    "event_time",
    "symbol",
    "side",
    "order_type",
    "time_in_force",
    "quantity",
    "price",
    "average_price",
    "order_status",
    "last_filled_quantity",
    "filled_quantity",
    "trade_time",
}


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
        raise ValueError("QC_INVALID_LIQUIDATION_DECIMAL") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("QC_NONPOSITIVE_LIQUIDATION_DECIMAL")
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


def load_decoder(
    pyarrow_root: Path = PYARROW_ROOT,
) -> Callable[[Path], tuple[list[dict], set[str]]]:
    manifest_path = pyarrow_root / "MANIFEST.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        data.get("status") != "ISOLATED_DECODER_SITE_COMPLETE"
        or data.get("package") != "pyarrow"
        or data.get("package_version") != "26.0.0"
    ):
        raise ValueError("QC_PYARROW_SITE_IDENTITY")
    site = pyarrow_root / "site"
    sys.path.insert(0, str(site))
    pyarrow = importlib.import_module("pyarrow")
    parquet = importlib.import_module("pyarrow.parquet")
    arrow_fs = importlib.import_module("pyarrow.fs")
    if pyarrow.__version__ != "26.0.0":
        raise ValueError("QC_PYARROW_VERSION")

    def decode(path: Path) -> tuple[list[dict], set[str]]:
        filesystem = arrow_fs.LocalFileSystem()
        with filesystem.open_input_stream(
            str(path), compression="zstd"
        ) as compressed:
            payload = compressed.readall()
        table = parquet.read_table(pyarrow.BufferReader(payload))
        return table.to_pylist(), set(table.column_names)

    return decode


def read_receipts(
    vendor_root: Path, symbol: str
) -> tuple[list[dict], set[tuple[str, int]]]:
    receipts = []
    covered = set()
    with (vendor_root / "OBJECTS.jsonl").open("rt", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            if item.get("symbol") != symbol:
                continue
            day = date.fromisoformat(item["date"])
            if not START <= day < END_EXCLUSIVE:
                continue
            hour = int(item["hour"])
            key = (day.isoformat(), hour)
            if key in covered:
                raise ValueError("QC_DUPLICATE_OBJECT_HOUR")
            covered.add(key)
            receipts.append(item)
    return sorted(receipts, key=lambda x: x["key"]), covered


def local_object(vendor_root: Path, item: dict) -> Path:
    return (
        vendor_root
        / "raw"
        / item["date"]
        / item["hour"]
        / f"{item['symbol']}_liquidations.parquet.zst"
    )


def add_event(
    bins: dict[int, dict[int, dict]],
    *,
    received_ns: int,
    side: str,
    amount: Decimal,
) -> None:
    month_start_ns = int(
        datetime(START.year, START.month, START.day, tzinfo=UTC).timestamp()
        * 1_000_000_000
    )
    for window in WINDOWS:
        index = (received_ns - month_start_ns) // (
            window * 60 * 1_000_000_000
        )
        cell = bins[window][index]
        cell["count"] += 1
        cell["quote"] += amount
        cell[f"{side}_count"] += 1
        cell[f"{side}_quote"] += amount


def aggregate_originals(
    vendor_root: Path,
    symbol: str,
    receipts: list[dict],
    decoder: Callable[[Path], tuple[list[dict], set[str]]],
) -> tuple[dict[int, dict[int, dict]], int, list[dict]]:
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
    total_rows = 0
    source_results = []
    for item in receipts:
        path = local_object(vendor_root, item)
        if not path.is_file():
            raise FileNotFoundError(f"QC_RAW_OBJECT_MISSING {path}")
        if path.stat().st_size != int(item["bytes"]):
            raise ValueError("QC_RAW_OBJECT_SIZE_DRIFT")
        if sha256(path) != item["sha256"]:
            raise ValueError("QC_RAW_OBJECT_HASH_DRIFT")
        rows, columns = decoder(path)
        if REQUIRED - set(columns):
            raise ValueError("QC_RAW_PARQUET_COLUMNS_MISSING")
        for row in rows:
            if row["symbol"] != symbol:
                raise ValueError("QC_RAW_SYMBOL_MISMATCH")
            raw_side = str(row["side"]).upper()
            if raw_side == "BUY":
                liquidated = "SHORT"
            elif raw_side == "SELL":
                liquidated = "LONG"
            else:
                raise ValueError("QC_RAW_SIDE_INVALID")
            received_ns = int(row["received_time"])
            trade_ms = int(row["trade_time"])
            event_ns = int(row["event_time"])
            if received_ns <= 0 or trade_ms <= 0 or event_ns <= 0:
                raise ValueError("QC_RAW_TIMESTAMP_INVALID")
            received = datetime.fromtimestamp(
                received_ns / 1_000_000_000, UTC
            )
            if (
                received.date().isoformat() != item["date"]
                or f"{received.hour:02d}" != item["hour"]
            ):
                raise ValueError("QC_RAW_RECEIVED_OUTSIDE_OBJECT_HOUR")
            price = decimal(row["price"])
            quantity = decimal(row["quantity"])
            if decimal(row["average_price"]) != price:
                raise ValueError("QC_RAW_AVERAGE_PRICE_DRIFT")
            if decimal(row["last_filled_quantity"]) != quantity:
                raise ValueError("QC_RAW_LAST_FILLED_QTY_DRIFT")
            if decimal(row["filled_quantity"]) != quantity:
                raise ValueError("QC_RAW_FILLED_QTY_DRIFT")
            if (
                str(row["order_type"]).upper() != "MARKET"
                or str(row["time_in_force"]).upper() != "IOC"
                or str(row["order_status"]).upper() != "FILLED"
            ):
                raise ValueError("QC_RAW_PLACEHOLDER_INVARIANT_DRIFT")
            add_event(
                bins,
                received_ns=received_ns,
                side=liquidated,
                amount=price * quantity,
            )
            total_rows += 1
        source_results.append(
            {
                "key": item["key"],
                "sha256": item["sha256"],
                "bytes": int(item["bytes"]),
                "rows": len(rows),
            }
        )
    return bins, total_rows, source_results


def expected_rows(
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
            source_present = (stamp.date().isoformat(), stamp.hour) in covered
            cell = bins[window].get(index)
            base = {
                "venue_id": "Bybit",
                "instrument_id": symbol,
                "indicator_id": 24,
                "formula_version": FORMULA,
                "window_minutes": window,
                "window_start_utc": stamp.isoformat(),
                "vendor_received_time_basis": True,
                "observed_at_utc": None,
                "verified_available_after_utc": None,
            }
            if not source_present:
                rows.append(
                    {
                        **base,
                        "quality": "UNKNOWN_MISSING_ORIGINAL_OBJECT_HOUR",
                        "count": None,
                        "published_bankruptcy_price_x_qty_usdt": None,
                        "liquidated_long_count": None,
                        "liquidated_long_quote_usdt": None,
                        "liquidated_short_count": None,
                        "liquidated_short_quote_usdt": None,
                    }
                )
            else:
                cell = cell or {
                    "count": 0,
                    "quote": Decimal(0),
                    "LONG_count": 0,
                    "LONG_quote": Decimal(0),
                    "SHORT_count": 0,
                    "SHORT_quote": Decimal(0),
                }
                rows.append(
                    {
                        **base,
                        "quality": "SOURCE_OBJECT_PRESENT",
                        "count": int(cell["count"]),
                        "published_bankruptcy_price_x_qty_usdt": str(
                            cell["quote"]
                        ),
                        "liquidated_long_count": int(cell["LONG_count"]),
                        "liquidated_long_quote_usdt": str(cell["LONG_quote"]),
                        "liquidated_short_count": int(cell["SHORT_count"]),
                        "liquidated_short_quote_usdt": str(cell["SHORT_quote"]),
                    }
                )
    return rows


def read_output(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def write_json_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def check_symbol(
    symbol: str,
    *,
    vendor_root: Path = VENDOR_ROOT,
    run_root: Path = RUN_ROOT,
    pyarrow_root: Path = PYARROW_ROOT,
    decoder: Callable[[Path], tuple[list[dict], set[str]]] | None = None,
    require_actor: bool = True,
) -> dict:
    if symbol not in COHORT:
        raise ValueError("SYMBOL_OUTSIDE_OWNER_COHORT")
    actor_uid(require_actor)
    target = run_root / symbol
    final_path = target / "run_final.json"
    provenance_path = target / "input_provenance.json"
    output_path = target / "liquidation_windows.jsonl.gz"
    report_path = target / "independent_qc_report.json"
    if report_path.exists():
        raise FileExistsError("IMMUTABLE_QC_REPORT_ALREADY_EXISTS")
    for path in (final_path, provenance_path, output_path):
        if not path.is_file():
            raise FileNotFoundError(f"QC_REQUIRED_ARTIFACT_MISSING {path}")

    final = json.loads(final_path.read_text())
    provenance = json.loads(provenance_path.read_text())
    if (
        final.get("status")
        != "LIQUIDATION_24_FIRSTMONTH_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
        or final.get("symbol") != symbol
        or final.get("formula_version") != FORMULA
    ):
        raise ValueError("QC_MATERIALIZED_RUN_IDENTITY")
    if sha256(output_path) != final["output_sha256"]:
        raise ValueError("QC_MATERIALIZED_OUTPUT_HASH_DRIFT")
    if (
        provenance.get("objects_manifest_sha256")
        != sha256(vendor_root / "OBJECTS.jsonl")
    ):
        raise ValueError("QC_OBJECTS_MANIFEST_DRIFT")

    receipts, covered = read_receipts(vendor_root, symbol)
    if len(receipts) != int(final["original_object_hours"]):
        raise ValueError("QC_OBJECT_RECEIPT_COUNT_DRIFT")
    decode = decoder or load_decoder(pyarrow_root)
    bins, raw_rows, source_results = aggregate_originals(
        vendor_root, symbol, receipts, decode
    )
    if raw_rows != int(final["normalized_rows"]):
        raise ValueError("QC_RAW_ROW_COUNT_DIFFERS_FROM_PARITY_MATERIALIZATION")
    expected = expected_rows(symbol, covered, bins)
    actual = iter(read_output(output_path))
    checked = 0
    for row in expected:
        try:
            got = next(actual)
        except StopIteration as exc:
            raise ValueError("QC_OUTPUT_TRUNCATED") from exc
        if got != row:
            raise ValueError(
                "QC_FULLROW_MISMATCH "
                f"window={row['window_minutes']} start={row['window_start_utc']}"
            )
        checked += 1
    try:
        next(actual)
        raise ValueError("QC_OUTPUT_HAS_EXTRA_ROWS")
    except StopIteration:
        pass
    if checked != int(final["rows"]):
        raise ValueError("QC_OUTPUT_ROW_COUNT_DRIFT")

    report = {
        "status": "LIQUIDATION_24_FIRSTMONTH_INDEPENDENT_ORIGINAL_PARQUET_QC_PASS_NOT_RESEARCH_READY",
        "checker_version": VERSION,
        "checker_sha256": sha256(Path(__file__).resolve()),
        "symbol": symbol,
        "indicator_id": 24,
        "formula_version": FORMULA,
        "checked_rows": checked,
        "original_object_hours": len(receipts),
        "missing_object_hours": 720 - len(receipts),
        "raw_parquet_rows": raw_rows,
        "materialized_output_sha256": sha256(output_path),
        "objects_manifest_sha256": sha256(
            vendor_root / "OBJECTS.jsonl"
        ),
        "source_results": source_results,
        "vendor_received_time_is_not_cripta_available_after": True,
        "bankruptcy_price_is_not_execution_price": True,
        "verified_available_after": False,
        "research_ready": False,
    }
    write_json_new(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=COHORT)
    args = parser.parse_args()
    print(json.dumps(check_symbol(args.symbol), sort_keys=True))


if __name__ == "__main__":
    main()
