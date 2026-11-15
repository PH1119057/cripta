#!/usr/bin/env python3
"""Issue #158 / #25: audit reusable legacy Bybit Spot originals.

READ-ONLY. This verifier independently checks the physically preserved legacy
Spot cache against its historical source manifest. It does not download,
copy, rename or repair files and does not inherit the historical experiment's
policy. Missing first-month originals remain SOURCE_MISSING.

Production CLI is fixed to the current relocated legacy evidence paths and the
owner-selected first-month cohort.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
DAYS = 30
CACHE_ROOT = Path(
    "/data/cripta/research/runs/"
    "_legacy_system_offload_research_runs_20260911/"
    "mayak_component_oos_new15_v1_20260906_7b366ea/"
    "full_aux/spot_cache"
)
SOURCE_MANIFEST = Path(
    "/data/cripta/research/runs/"
    "_legacy_system_offload_research_runs_20260911/"
    "mayak_component_oos_new15_v1_20260906_7b366ea/"
    "full_aux/spot/SOURCE_MANIFEST.json"
)
EXPECTED_ARCHIVE_BASE = "https://public.bybit.com/spot"
EXPECTED_HEADER = ["id", "timestamp", "price", "volume", "side", "rpi"]
VERSION = "R158_SPOT_LEGACY_ORIGINAL_AUDIT_V1"


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


def positive_decimal(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("INVALID_SPOT_NUMERIC_FIELD") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("NONPOSITIVE_SPOT_NUMERIC_FIELD")
    return number


def expected_url(symbol: str, day: date) -> str:
    return (
        f"{EXPECTED_ARCHIVE_BASE}/{symbol}/"
        f"{symbol}_{day.isoformat()}.csv.gz"
    )


def load_manifest(path: Path) -> dict[tuple[str, str], dict]:
    if not path.is_file():
        raise FileNotFoundError(f"SOURCE_MANIFEST_MISSING {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("archive_base") != EXPECTED_ARCHIVE_BASE:
        raise ValueError("UNEXPECTED_SPOT_ARCHIVE_BASE")
    indexed: dict[tuple[str, str], dict] = {}
    for item in payload.get("files", []):
        symbol = item.get("symbol")
        day = item.get("day")
        if symbol not in COHORT:
            continue
        key = (symbol, day)
        if key in indexed:
            raise ValueError("DUPLICATE_SOURCE_MANIFEST_ENTRY")
        if item.get("source_symbol") != symbol:
            raise ValueError("SOURCE_MANIFEST_SYMBOL_MISMATCH")
        if item.get("url") != expected_url(symbol, date.fromisoformat(day)):
            raise ValueError("SOURCE_MANIFEST_URL_MISMATCH")
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("SOURCE_MANIFEST_SHA_FORMAT")
        indexed[key] = item
    return indexed


def audit_file(path: Path, symbol: str, day: date, item: dict) -> dict:
    if path.stat().st_size != int(item["bytes"]):
        raise ValueError(f"FILE_SIZE_MISMATCH {symbol} {day}")
    digest = sha256(path)
    if digest != item["sha256"]:
        raise ValueError(f"FILE_SHA256_MISMATCH {symbol} {day}")

    day_start_ms = int(
        datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000
    )
    day_end_ms = day_start_ms + 86_400_000
    rows = 0
    first_ts = last_ts = None
    previous_ts = None
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != EXPECTED_HEADER:
            raise ValueError(f"SPOT_HEADER_MISMATCH {symbol} {day}")
        for row in reader:
            if not row["id"]:
                raise ValueError(f"SPOT_ID_MISSING {symbol} {day}")
            try:
                ts = int(row["timestamp"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"SPOT_TIMESTAMP_INVALID {symbol} {day}") from exc
            if not day_start_ms <= ts < day_end_ms:
                raise ValueError(f"SPOT_TIMESTAMP_OUTSIDE_DAY {symbol} {day}")
            if previous_ts is not None and ts < previous_ts:
                raise ValueError(f"SPOT_TIMESTAMP_NOT_MONOTONIC {symbol} {day}")
            previous_ts = ts
            if row["side"].strip().lower() not in ("buy", "sell"):
                raise ValueError(f"SPOT_SIDE_INVALID {symbol} {day}")
            positive_decimal(row["price"])
            positive_decimal(row["volume"])
            if row["rpi"] not in ("0", "1"):
                raise ValueError(f"SPOT_RPI_INVALID {symbol} {day}")
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            rows += 1
    if rows == 0:
        raise ValueError(f"SPOT_ARCHIVE_EMPTY {symbol} {day}")
    return {
        "symbol": symbol,
        "date_utc": day.isoformat(),
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest,
        "rows": rows,
        "first_exchange_timestamp_ms": first_ts,
        "last_exchange_timestamp_ms": last_ts,
        "historical_source_url": item["url"],
        "manifest_path_recorded": item.get("path"),
        "status": "ORIGINAL_FILE_QC_PASS",
    }


def audit(
    *,
    cache_root: Path = CACHE_ROOT,
    source_manifest: Path = SOURCE_MANIFEST,
    cohort: tuple[str, ...] = COHORT,
    start: date = START,
    days: int = DAYS,
    require_actor: bool = True,
) -> dict:
    _effective_actor_uid(require_actor)
    if days <= 0:
        raise ValueError("INVALID_DAY_COUNT")
    manifest = load_manifest(source_manifest)
    files = []
    missing = {}
    manifest_missing = {}
    for symbol in cohort:
        if symbol not in COHORT:
            raise ValueError("SYMBOL_OUTSIDE_OWNER_COHORT")
        symbol_missing = []
        symbol_manifest_missing = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            key = (symbol, day.isoformat())
            path = cache_root / symbol / f"{symbol}_{day.isoformat()}.csv.gz"
            item = manifest.get(key)
            if not path.is_file():
                symbol_missing.append(day.isoformat())
                if item is None:
                    symbol_manifest_missing.append(day.isoformat())
                continue
            if item is None:
                raise ValueError(
                    f"PHYSICAL_FILE_WITHOUT_SOURCE_MANIFEST {symbol} {day}"
                )
            files.append(audit_file(path, symbol, day, item))
        missing[symbol] = symbol_missing
        manifest_missing[symbol] = symbol_manifest_missing

    expected = len(cohort) * days
    present = len(files)
    return {
        "version": VERSION,
        "status": (
            "PARTIAL_REUSABLE_ORIGINAL_CORPUS_QC_PASS"
            if present < expected
            else "FULL_REUSABLE_ORIGINAL_CORPUS_QC_PASS"
        ),
        "historical_evidence_only_not_policy_reuse": True,
        "cache_root": str(cache_root),
        "source_manifest": str(source_manifest),
        "source_manifest_sha256": sha256(source_manifest),
        "period_start_utc": start.isoformat(),
        "period_end_exclusive_utc": (start + timedelta(days=days)).isoformat(),
        "expected_files": expected,
        "present_files_qced": present,
        "missing_files": expected - present,
        "missing_dates_by_symbol": missing,
        "missing_manifest_dates_by_symbol": manifest_missing,
        "files": files,
        "local_receiver_available_after_verified": False,
        "research_ready": False,
        "next_action": (
            "Acquire only missing originals under a separate approved "
            "source-acquisition/write contract; do not redownload QC-passed files."
        ),
    }


def main() -> None:
    result = audit()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
