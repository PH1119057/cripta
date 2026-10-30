#!/usr/bin/env python3
"""Issue #158 first-month materializer for #10 OFI and #16 absorption components.

Research-only bounded writer. Production CLI is fixed to the owner-approved
first-month cohort, immutable raw root and one new research run root. It does
not mutate datasets, Git, DB, systemd, Strategy, Execution or LIVE state.

EFFECTIVE_ACTOR: cripta
READ:
  /data/cripta/datasets/raw/20260518_20260816
  this versioned research source and sibling #10/#16 readers
WRITE/CREATE:
  /data/cripta/research/runs/r158_firstmonth_10_16_20261030/<SYMBOL>
RENAME:
  only new *.part -> final inside that new symbol directory
DELETE: none
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
END_EXCLUSIVE = date(2026, 6, 17)
RAW_ROOT = Path("/data/cripta/datasets/raw/20260518_20260816")
OUTPUT_ROOT = Path("/data/cripta/research/runs/r158_firstmonth_10_16_20261030")
EXPECTED_OFI_VERSION = "R158_OFI_EXACT_PRICE_TOP5_UNION_V1"
EXPECTED_ABSORPTION_VERSION = "R158_ABSORPTION_COMPONENTS_NATIVE_V1"
MATERIALIZER_VERSION = "R158_FIRSTMONTH_10_16_MATERIALIZER_V1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _effective_actor_uid(require_actor: bool) -> int | None:
    getter = getattr(os, "geteuid", None)
    if getter is None:
        if require_actor:
            raise RuntimeError("EFFECTIVE_UID_UNAVAILABLE")
        return None
    uid = int(getter())
    if require_actor:
        import pwd

        if uid != pwd.getpwnam("cripta").pw_uid:
            raise PermissionError("EFFECTIVE_ACTOR_NOT_CRIPTA")
    return uid


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new_json(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_reader(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("READER_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def minute_start_iso(day: date, minute: int) -> str:
    return (
        datetime(day.year, day.month, day.day, tzinfo=UTC)
        + timedelta(minutes=minute)
    ).isoformat()


def _source_paths(raw_root: Path, symbol: str, day: date) -> tuple[Path, Path]:
    book = raw_root / symbol / "orderbook" / f"{day}_{symbol}_ob200.data.zip"
    trades = raw_root / symbol / "public_trades" / f"{symbol}{day}.csv.gz"
    return book, trades


def materialize_symbol(
    symbol: str,
    source_commit: str,
    *,
    raw_root: Path = RAW_ROOT,
    output_root: Path = OUTPUT_ROOT,
    days: int = 30,
    require_actor: bool = True,
) -> dict:
    if symbol not in COHORT:
        raise ValueError("SYMBOL_OUTSIDE_OWNER_COHORT")
    if not SHA_RE.fullmatch(source_commit):
        raise ValueError("SOURCE_COMMIT_MUST_BE_EXACT_40HEX")
    if not 1 <= days <= 30:
        raise ValueError("DAYS_OUTSIDE_FIRST_MONTH")
    actor_uid = _effective_actor_uid(require_actor)
    if not output_root.parent.is_dir():
        raise FileNotFoundError("OUTPUT_PARENT_MISSING")
    if not os.access(output_root.parent, os.W_OK | os.X_OK):
        raise PermissionError("OUTPUT_PARENT_NOT_WRITABLE_BY_ACTOR")

    source_dir = Path(__file__).resolve().parent
    ofi_path = source_dir / "r158_ofi_top5_exact_union_v1.py"
    absorption_path = source_dir / "r158_absorption_components_v1.py"
    if not ofi_path.is_file() or not absorption_path.is_file():
        raise FileNotFoundError("REQUIRED_VERSIONED_READER_MISSING")

    ofi = load_reader(ofi_path, "r158_materialize_ofi")
    absorption = load_reader(absorption_path, "r158_materialize_absorption")
    if ofi.VERSION != EXPECTED_OFI_VERSION:
        raise ValueError("OFI_READER_VERSION_DRIFT")
    if absorption.VERSION != EXPECTED_ABSORPTION_VERSION:
        raise ValueError("ABSORPTION_READER_VERSION_DRIFT")

    expected_days = [START + timedelta(days=i) for i in range(days)]
    for day in expected_days:
        if not START <= day < END_EXCLUSIVE:
            raise ValueError("DAY_OUTSIDE_OWNER_FIRST_MONTH")
        book, trades = _source_paths(raw_root, symbol, day)
        if not book.is_file():
            raise FileNotFoundError(f"BOOK_SOURCE_MISSING {book}")
        if not trades.is_file():
            raise FileNotFoundError(f"TRADE_SOURCE_MISSING {trades}")

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
    shutil.copyfile(ofi_path, snapshots / ofi_path.name)
    shutil.copyfile(absorption_path, snapshots / absorption_path.name)

    reader_identity = {
        "materializer_version": MATERIALIZER_VERSION,
        "materializer_sha256": sha256(this_script),
        "ofi_version": ofi.VERSION,
        "ofi_sha256": sha256(ofi_path),
        "absorption_version": absorption.VERSION,
        "absorption_sha256": sha256(absorption_path),
        "source_commit": source_commit,
    }
    write_new_json(
        target / "run_start.json",
        {
            "status": "STARTED_NOT_COMPLETION_PROOF",
            "actor_uid": actor_uid,
            "symbol": symbol,
            "period_start_utc": START.isoformat(),
            "period_end_exclusive_utc": (START + timedelta(days=days)).isoformat(),
            "days": days,
            "read_root": str(raw_root),
            "write_root": str(target),
            "delete_operations": "NONE",
            "db_service_live_mutation": False,
            **reader_identity,
        },
    )

    ofi_part = target / "indicator_10_ofi_1m.jsonl.gz.part"
    ofi_final = target / "indicator_10_ofi_1m.jsonl.gz"
    abs_part = target / "indicator_16_absorption_components_1m.jsonl.gz.part"
    abs_final = target / "indicator_16_absorption_components_1m.jsonl.gz"
    provenance = []
    ofi_status = {"OBSERVED": 0, "UNKNOWN": 0, "NO_DATA": 0}
    abs_book_status = {"OBSERVED": 0, "UNKNOWN": 0, "NO_DATA": 0}
    abs_trade_status = {"OBSERVED": 0, "UNKNOWN": 0, "NO_DATA": 0}
    ofi_rows_total = abs_rows_total = 0

    with ofi_part.open("xb") as raw_ofi, abs_part.open("xb") as raw_abs:
        with gzip.GzipFile(fileobj=raw_ofi, mode="wb", mtime=0) as gz_ofi:
            with gzip.GzipFile(fileobj=raw_abs, mode="wb", mtime=0) as gz_abs:
                with io.TextIOWrapper(
                    gz_ofi, encoding="utf-8", newline="\n"
                ) as out_ofi:
                    with io.TextIOWrapper(
                        gz_abs, encoding="utf-8", newline="\n"
                    ) as out_abs:
                        for day in expected_days:
                            book_path, trade_path = _source_paths(
                                raw_root, symbol, day
                            )
                            ofi_manifest, ofi_rows = ofi.calculate_archive(
                                book_path, symbol, day
                            )
                            abs_manifest, abs_rows = absorption.calculate(
                                symbol, day, raw_root
                            )
                            if len(ofi_rows) != 1440 or len(abs_rows) != 1440:
                                raise ValueError("DAILY_MINUTE_CARDINALITY_MISMATCH")
                            if (
                                ofi_manifest["source_zip_sha256"]
                                != abs_manifest["orderbook"]["sha256"]
                            ):
                                raise ValueError(
                                    "BOOK_HASH_DISAGREEMENT_BETWEEN_READERS"
                                )
                            if (
                                ofi_manifest["source_suffix_records"]
                                != abs_manifest["orderbook"][
                                    "source_suffix_records"
                                ]
                            ):
                                raise ValueError(
                                    "BOOK_SUFFIX_COUNT_DISAGREEMENT_BETWEEN_READERS"
                                )
                            if abs_manifest["orderbook"]["state"] != "SOURCE_PRESENT":
                                raise ValueError(
                                    "BOOK_SOURCE_NOT_PRESENT_AFTER_PREFLIGHT"
                                )
                            if (
                                abs_manifest["perp_trades"]["state"]
                                != "SOURCE_PRESENT"
                            ):
                                raise ValueError(
                                    "TRADE_SOURCE_NOT_PRESENT_AFTER_PREFLIGHT"
                                )

                            for minute, row in enumerate(ofi_rows):
                                status = row["status"]
                                if status not in ofi_status:
                                    raise ValueError("UNEXPECTED_OFI_QUALITY")
                                ofi_status[status] += 1
                                payload = {
                                    "venue_id": "Bybit",
                                    "market_source_id": "BYBIT_OB200_ARCHIVE",
                                    "instrument_id": symbol,
                                    "indicator_id": 10,
                                    "formula_version": ofi.VERSION,
                                    "minute_start_utc": minute_start_iso(
                                        day, minute
                                    ),
                                    "observed_at_utc": None,
                                    "verified_available_after_utc": None,
                                    **row,
                                }
                                out_ofi.write(
                                    json.dumps(payload, sort_keys=True) + "\n"
                                )
                                ofi_rows_total += 1

                            for minute, row in enumerate(abs_rows):
                                book_status = row["book_status"]
                                trade_status = row["trade_status"]
                                if book_status not in abs_book_status:
                                    raise ValueError(
                                        "UNEXPECTED_ABSORPTION_BOOK_QUALITY"
                                    )
                                if trade_status not in abs_trade_status:
                                    raise ValueError(
                                        "UNEXPECTED_ABSORPTION_TRADE_QUALITY"
                                    )
                                abs_book_status[book_status] += 1
                                abs_trade_status[trade_status] += 1
                                payload = {
                                    "venue_id": "Bybit",
                                    "market_source_id": (
                                        "BYBIT_OB200_AND_PUBLIC_TRADES_ARCHIVE"
                                    ),
                                    "instrument_id": symbol,
                                    "indicator_id": 16,
                                    "formula_version": absorption.VERSION,
                                    "minute_start_utc": minute_start_iso(
                                        day, minute
                                    ),
                                    "observed_at_utc": None,
                                    "verified_available_after_utc": None,
                                    "proxy_classification": (
                                        "ABSORPTION_EVIDENCE_PROXY_COMPONENTS_ONLY"
                                    ),
                                    **row,
                                }
                                out_abs.write(
                                    json.dumps(payload, sort_keys=True) + "\n"
                                )
                                abs_rows_total += 1

                            provenance.append(
                                {
                                    "date_utc": day.isoformat(),
                                    "orderbook_path": str(book_path),
                                    "orderbook_sha256": ofi_manifest[
                                        "source_zip_sha256"
                                    ],
                                    "orderbook_raw_records": ofi_manifest[
                                        "raw_records_read"
                                    ],
                                    "orderbook_source_suffix_records": (
                                        ofi_manifest["source_suffix_records"]
                                    ),
                                    "ofi_minute_status_counts": ofi_manifest[
                                        "minute_status_counts"
                                    ],
                                    "trade_path": str(trade_path),
                                    "trade_sha256": abs_manifest["perp_trades"][
                                        "sha256"
                                    ],
                                    "trade_rows": abs_manifest["perp_trades"][
                                        "rows"
                                    ],
                                    "absorption_book_snapshots": abs_manifest[
                                        "orderbook"
                                    ]["snapshots"],
                                    "absorption_book_gaps": abs_manifest[
                                        "orderbook"
                                    ]["gaps"],
                                    "absorption_book_source_suffix_records": (
                                        abs_manifest["orderbook"][
                                            "source_suffix_records"
                                        ]
                                    ),
                                }
                            )

    expected_rows = days * 1440
    if ofi_rows_total != expected_rows or abs_rows_total != expected_rows:
        raise ValueError("FULL_PERIOD_CARDINALITY_MISMATCH")
    os.rename(ofi_part, ofi_final)
    os.rename(abs_part, abs_final)

    write_new_json(
        target / "input_provenance.json",
        {
            "symbol": symbol,
            "days": days,
            "period_start_utc": START.isoformat(),
            "period_end_exclusive_utc": (
                START + timedelta(days=days)
            ).isoformat(),
            "source_days": provenance,
        },
    )
    final = {
        "status": "PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC",
        "symbol": symbol,
        "days": days,
        "period_start_utc": START.isoformat(),
        "period_end_exclusive_utc": (START + timedelta(days=days)).isoformat(),
        "indicator_10": {
            "formula_version": ofi.VERSION,
            "rows": ofi_rows_total,
            "quality_counts": ofi_status,
            "output_file": str(ofi_final),
            "output_sha256": sha256(ofi_final),
        },
        "indicator_16": {
            "formula_version": absorption.VERSION,
            "rows": abs_rows_total,
            "book_quality_counts": abs_book_status,
            "trade_quality_counts": abs_trade_status,
            "output_file": str(abs_final),
            "output_sha256": sha256(abs_final),
            "classification": "ABSORPTION_EVIDENCE_PROXY_COMPONENTS_ONLY",
        },
        "receiver_clock_verified": False,
        "verified_available_after": False,
        "source_completeness_verified": False,
        "causal_asof_join_verified": False,
        "research_ready": False,
        "limitations": [
            "Historical local receiver/publication clock not captured.",
            (
                "OFI and absorption components remain DESCRIPTIVE_ONLY "
                "for causal readiness."
            ),
            (
                "Independent QC is a separate required stage; "
                "this file does not claim it."
            ),
        ],
        **reader_identity,
    }
    write_new_json(target / "run_final.json", final)
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=COHORT)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    result = materialize_symbol(args.symbol, args.source_commit)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
