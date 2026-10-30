#!/usr/bin/env python3
"""Issue #158 / #24: independent original-Parquet ↔ normalized row parity.

Research-only bounded QC. Raw CryptoHFTData objects and historical normalized
CSV.GZ files are READ-ONLY. The checker verifies receipt size/SHA, decodes each
registered first-month Parquet/Zstd object with one isolated pyarrow site,
validates Bybit liquidation wire invariants and compares every row as a
multiset against the normalized historical representation.

It writes only one new report under the fixed research run root.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib
import json
import os
import pwd
import sys
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
END_EXCLUSIVE = date(2026, 6, 17)
VENDOR_ROOT = Path(
    "/data/cripta/datasets/external/cryptohftdata/bybit_liquidations_v1"
)
NORMALIZED_ROOT = Path(
    "/data/cripta/research/runs/strategy_full20_liq_20260922/liquidations"
)
PYARROW_ROOT = Path(
    "/data/cripta/research/cache/r158_pyarrow26_site_20261030"
)
OUTPUT_ROOT = Path(
    "/data/cripta/research/runs/r158_liquidation_parquet_parity_20261030"
)
VERSION = "R158_LIQUIDATION_PARQUET_PARITY_V1"
EXPECTED_RECEIPTS = 1157
EXPECTED_NORMALIZED_ROWS = 8998
REQUIRED_PARQUET_COLUMNS = {
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


def decimal(value, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"INVALID_DECIMAL {label}") from exc
    if not number.is_finite():
        raise ValueError(f"NONFINITE_DECIMAL {label}")
    return number


def load_pyarrow_decoder(
    pyarrow_root: Path = PYARROW_ROOT,
) -> Callable[[Path], tuple[list[dict], set[str]]]:
    manifest_path = pyarrow_root / "MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("ISOLATED_PYARROW_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("status") != "ISOLATED_DECODER_SITE_COMPLETE"
        or manifest.get("package") != "pyarrow"
        or manifest.get("package_version") != "26.0.0"
    ):
        raise ValueError("ISOLATED_PYARROW_IDENTITY_MISMATCH")
    site = pyarrow_root / "site"
    sys.path.insert(0, str(site))
    pyarrow = importlib.import_module("pyarrow")
    parquet = importlib.import_module("pyarrow.parquet")
    arrow_fs = importlib.import_module("pyarrow.fs")
    if pyarrow.__version__ != "26.0.0":
        raise ValueError("PYARROW_VERSION_MISMATCH")

    def decode(path: Path) -> tuple[list[dict], set[str]]:
        # Streaming outer Zstd decompression is handled inside pyarrow.
        # No subprocess, alternate interpreter, shell pipeline or system
        # package is invoked.
        filesystem = arrow_fs.LocalFileSystem()
        with filesystem.open_input_stream(
            str(path), compression="zstd"
        ) as compressed_stream:
            parquet_bytes = compressed_stream.readall()
        table = parquet.read_table(pyarrow.BufferReader(parquet_bytes))
        return table.to_pylist(), set(table.column_names)

    return decode


def read_receipts(
    root: Path,
    *,
    cohort: tuple[str, ...],
    expected_receipts: int | None,
) -> list[dict]:
    path = root / "OBJECTS.jsonl"
    if not path.is_file():
        raise FileNotFoundError("OBJECTS_MANIFEST_MISSING")
    receipts = []
    seen = set()
    with path.open("rt", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            symbol = item.get("symbol")
            day_text = item.get("date")
            if symbol not in cohort or not isinstance(day_text, str):
                continue
            day = date.fromisoformat(day_text)
            if not START <= day < END_EXCLUSIVE:
                continue
            key = item.get("key")
            if not isinstance(key, str) or key in seen:
                raise ValueError("DUPLICATE_OR_INVALID_OBJECT_KEY")
            seen.add(key)
            expected_key = (
                f"bybit/{day_text}/{item.get('hour')}/"
                f"{symbol}_liquidations.parquet.zst"
            )
            if key != expected_key:
                raise ValueError("OBJECT_KEY_IDENTITY_MISMATCH")
            if int(item.get("bytes", 0)) <= 0:
                raise ValueError("OBJECT_RECEIPT_BYTES_INVALID")
            digest = item.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError("OBJECT_RECEIPT_SHA_INVALID")
            receipts.append(item)
    if expected_receipts is not None and len(receipts) != expected_receipts:
        raise ValueError(
            f"FIRSTMONTH_RECEIPT_COUNT_DRIFT {len(receipts)} "
            f"expected={expected_receipts}"
        )
    return sorted(receipts, key=lambda x: x["key"])


def raw_local_path(root: Path, item: dict) -> Path:
    return (
        root
        / "raw"
        / item["date"]
        / item["hour"]
        / f"{item['symbol']}_liquidations.parquet.zst"
    )


def normalized_source_path(item: dict) -> str:
    return (
        f"raw/{item['date']}/{item['hour']}/"
        f"{item['symbol']}_liquidations.parquet.zst"
    )


def raw_row_tuple(row: dict, *, item: dict):
    missing = REQUIRED_PARQUET_COLUMNS - row.keys()
    if missing:
        raise ValueError(f"RAW_PARQUET_COLUMNS_MISSING {sorted(missing)}")
    symbol = str(row["symbol"])
    if symbol != item["symbol"]:
        raise ValueError("RAW_SYMBOL_MISMATCH")
    side = str(row["side"]).upper()
    if side == "BUY":
        position_side = "SHORT"
    elif side == "SELL":
        position_side = "LONG"
    else:
        raise ValueError("RAW_SIDE_INVALID")

    received_ns = int(row["received_time"])
    trade_ms = int(row["trade_time"])
    event_time = int(row["event_time"])
    if received_ns <= 0 or trade_ms <= 0 or event_time <= 0:
        raise ValueError("RAW_TIMESTAMP_NONPOSITIVE")
    received = datetime.fromtimestamp(received_ns / 1_000_000_000, UTC)
    if (
        received.date().isoformat() != item["date"]
        or f"{received.hour:02d}" != item["hour"]
    ):
        raise ValueError("RAW_RECEIVED_TIME_OUTSIDE_OBJECT_HOUR")

    price = decimal(row["price"], "price")
    quantity = decimal(row["quantity"], "quantity")
    if price <= 0 or quantity <= 0:
        raise ValueError("RAW_PRICE_OR_QUANTITY_NONPOSITIVE")
    if decimal(row["average_price"], "average_price") != price:
        raise ValueError("BYBIT_AVERAGE_PRICE_NOT_BANKRUPTCY_PRICE")
    if decimal(row["last_filled_quantity"], "last_filled_quantity") != quantity:
        raise ValueError("BYBIT_LAST_FILLED_QUANTITY_MISMATCH")
    if decimal(row["filled_quantity"], "filled_quantity") != quantity:
        raise ValueError("BYBIT_FILLED_QUANTITY_MISMATCH")
    if str(row["order_type"]).upper() != "MARKET":
        raise ValueError("BYBIT_ORDER_TYPE_PLACEHOLDER_DRIFT")
    if str(row["time_in_force"]).upper() != "IOC":
        raise ValueError("BYBIT_TIF_PLACEHOLDER_DRIFT")
    order_status = str(row["order_status"]).upper()
    if order_status != "FILLED":
        raise ValueError("BYBIT_ORDER_STATUS_PLACEHOLDER_DRIFT")

    return (
        received_ns // 1000,
        trade_ms * 1000,
        symbol,
        position_side,
        price,
        quantity,
        order_status,
        normalized_source_path(item),
    )


def normalized_row_tuple(row: dict):
    try:
        return (
            int(row["observed_timestamp_us"]),
            int(row["trade_timestamp_us"]),
            row["symbol"],
            row["liquidated_position_side"],
            decimal(row["price"], "normalized_price"),
            decimal(row["size"], "normalized_size"),
            row["order_status"].upper(),
            row["source_path"],
        )
    except KeyError as exc:
        raise ValueError("NORMALIZED_REQUIRED_COLUMN_MISSING") from exc


def read_normalized(
    root: Path,
    *,
    cohort: tuple[str, ...],
) -> dict[str, list[dict]]:
    by_source = defaultdict(list)
    for symbol in cohort:
        path = root / f"{symbol}.csv.gz"
        if not path.is_file():
            raise FileNotFoundError(f"NORMALIZED_FILE_MISSING {path}")
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            expected = {
                "observed_timestamp_us",
                "trade_timestamp_us",
                "symbol",
                "liquidated_position_side",
                "price",
                "size",
                "order_status",
                "source_path",
            }
            if not expected.issubset(reader.fieldnames or []):
                raise ValueError("NORMALIZED_HEADER_MISMATCH")
            for row in reader:
                source = row["source_path"]
                parts = source.split("/")
                if len(parts) != 4 or parts[0] != "raw":
                    continue
                day = date.fromisoformat(parts[1])
                if START <= day < END_EXCLUSIVE and row["symbol"] in cohort:
                    by_source[source].append(row)
    return by_source


def write_new_json(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def audit(
    source_commit: str,
    *,
    vendor_root: Path = VENDOR_ROOT,
    normalized_root: Path = NORMALIZED_ROOT,
    output_root: Path = OUTPUT_ROOT,
    pyarrow_root: Path = PYARROW_ROOT,
    cohort: tuple[str, ...] = COHORT,
    decoder: Callable[[Path], tuple[list[dict], set[str]]] | None = None,
    expected_receipts: int | None = EXPECTED_RECEIPTS,
    expected_normalized_rows: int | None = EXPECTED_NORMALIZED_ROWS,
    require_actor: bool = True,
) -> dict:
    if len(source_commit) != 40 or any(
        char not in "0123456789abcdef" for char in source_commit
    ):
        raise ValueError("SOURCE_COMMIT_MUST_BE_EXACT_40HEX")
    if not cohort or any(symbol not in COHORT for symbol in cohort):
        raise ValueError("COHORT_OUTSIDE_OWNER_SCOPE")
    if require_actor and os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("EFFECTIVE_ACTOR_NOT_CRIPTA")
    if output_root.exists():
        raise FileExistsError("IMMUTABLE_PARITY_REPORT_ROOT_EXISTS")
    if not output_root.parent.is_dir():
        raise FileNotFoundError("OUTPUT_PARENT_MISSING")
    if not os.access(output_root.parent, os.W_OK | os.X_OK):
        raise PermissionError("OUTPUT_PARENT_NOT_WRITABLE_BY_ACTOR")

    receipts = read_receipts(
        vendor_root, cohort=cohort, expected_receipts=expected_receipts
    )
    decode = decoder or load_pyarrow_decoder(pyarrow_root)
    normalized = read_normalized(normalized_root, cohort=cohort)
    receipt_sources = {normalized_source_path(item) for item in receipts}
    unregistered = sorted(set(normalized) - receipt_sources)
    if unregistered:
        raise ValueError(
            f"NORMALIZED_FIRSTMONTH_SOURCE_WITHOUT_RECEIPT {unregistered[:3]}"
        )

    results = []
    raw_total = normalized_total = 0
    by_symbol = defaultdict(lambda: {"objects": 0, "rows": 0})
    for item in receipts:
        path = raw_local_path(vendor_root, item)
        if not path.is_file():
            raise FileNotFoundError(f"RAW_OBJECT_MISSING {path}")
        if path.stat().st_size != int(item["bytes"]):
            raise ValueError("RAW_OBJECT_SIZE_MISMATCH")
        if sha256(path) != item["sha256"]:
            raise ValueError("RAW_OBJECT_SHA256_MISMATCH")
        rows, columns = decode(path)
        missing = REQUIRED_PARQUET_COLUMNS - set(columns)
        if missing:
            raise ValueError(f"RAW_PARQUET_COLUMNS_MISSING {sorted(missing)}")

        raw_counter = Counter(raw_row_tuple(row, item=item) for row in rows)
        source = normalized_source_path(item)
        normalized_rows = normalized.get(source, [])
        normalized_counter = Counter(
            normalized_row_tuple(row) for row in normalized_rows
        )
        if raw_counter != normalized_counter:
            raw_only = list((raw_counter - normalized_counter).elements())[:3]
            normalized_only = list(
                (normalized_counter - raw_counter).elements()
            )[:3]
            raise ValueError(
                f"PARQUET_NORMALIZED_ROW_MISMATCH source={source} "
                f"raw_only={raw_only} normalized_only={normalized_only}"
            )
        raw_total += len(rows)
        normalized_total += len(normalized_rows)
        by_symbol[item["symbol"]]["objects"] += 1
        by_symbol[item["symbol"]]["rows"] += len(rows)
        results.append(
            {
                "key": item["key"],
                "source_path": source,
                "bytes": path.stat().st_size,
                "sha256": item["sha256"],
                "rows": len(rows),
                "parquet_columns": sorted(columns),
                "status": "ROW_PARITY_PASS",
            }
        )

    if (
        expected_normalized_rows is not None
        and normalized_total != expected_normalized_rows
    ):
        raise ValueError(
            f"NORMALIZED_FIRSTMONTH_ROW_COUNT_DRIFT {normalized_total} "
            f"expected={expected_normalized_rows}"
        )
    if raw_total != normalized_total:
        raise ValueError("TOTAL_RAW_NORMALIZED_COUNT_MISMATCH")

    os.umask(0o007)
    output_root.mkdir(mode=0o2770)
    report = {
        "version": VERSION,
        "status": "ORIGINAL_PARQUET_NORMALIZED_FULL_ROW_PARITY_PASS",
        "source_commit": source_commit,
        "period_utc": "[2026-05-18T00:00:00Z,2026-06-17T00:00:00Z)",
        "cohort": list(cohort),
        "objects": len(receipts),
        "raw_rows": raw_total,
        "normalized_rows": normalized_total,
        "potential_coin_hours": len(cohort) * 30 * 24,
        "missing_object_hours_unknown_not_zero": (
            len(cohort) * 30 * 24 - len(receipts)
        ),
        "by_symbol": dict(sorted(by_symbol.items())),
        "object_results": results,
        "receipt_manifest_sha256": sha256(vendor_root / "OBJECTS.jsonl"),
        "vendor_received_time_is_not_cripta_available_after": True,
        "bankruptcy_price_is_not_execution_price": True,
        "research_ready": False,
        "trading_effect": "NONE",
    }
    write_new_json(
        output_root / "parquet_normalized_parity_report.json", report
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.source_commit), sort_keys=True))


if __name__ == "__main__":
    main()
