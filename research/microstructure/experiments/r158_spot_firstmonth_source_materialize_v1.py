#!/usr/bin/env python3
"""Issue #158 / #25: build one immutable first-month Bybit Spot source corpus.

Research-only bounded writer. Existing legacy originals are independently
validated against their historical source manifest and copied byte-for-byte.
Only physically missing cohort/day files are downloaded from public.bybit.com.
The legacy cache is never modified.

Production CLI has no arbitrary path options:
READ:
  legacy Spot cache + historical SOURCE_MANIFEST.json
WRITE/CREATE:
  /data/cripta/research/cache/r158_spot_firstmonth_20261030.partial
RENAME:
  whole completed .partial directory -> final immutable corpus
DELETE:
  none
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
DAYS = 30
LEGACY_ROOT = Path(
    "/data/cripta/research/runs/"
    "_legacy_system_offload_research_runs_20260911/"
    "mayak_component_oos_new15_v1_20260906_7b366ea/"
    "full_aux/spot_cache"
)
LEGACY_MANIFEST = Path(
    "/data/cripta/research/runs/"
    "_legacy_system_offload_research_runs_20260911/"
    "mayak_component_oos_new15_v1_20260906_7b366ea/"
    "full_aux/spot/SOURCE_MANIFEST.json"
)
OUTPUT_ROOT = Path("/data/cripta/research/cache/r158_spot_firstmonth_20261030")
ARCHIVE_BASE = "https://public.bybit.com/spot"
VERSION = "R158_SPOT_FIRSTMONTH_SOURCE_MATERIALIZE_V1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def load_auditor():
    path = Path(__file__).resolve().with_name(
        "r158_spot_legacy_original_audit_v1.py"
    )
    spec = importlib.util.spec_from_file_location("r158_spot_auditor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("SPOT_AUDITOR_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.VERSION != "R158_SPOT_LEGACY_ORIGINAL_AUDIT_V1":
        raise ValueError("SPOT_AUDITOR_VERSION_DRIFT")
    return module, path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_url(symbol: str, day: date) -> str:
    return f"{ARCHIVE_BASE}/{symbol}/{symbol}_{day.isoformat()}.csv.gz"


def fetch_public_archive(url: str) -> bytes:
    if not url.startswith(ARCHIVE_BASE + "/"):
        raise ValueError("DOWNLOAD_URL_OUTSIDE_APPROVED_ARCHIVE")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CRIPTA-R158-Spot-Source-Materializer/1"},
    )
    with urllib.request.urlopen(request, timeout=60.0) as response:
        if int(getattr(response, "status", 200)) != 200:
            raise RuntimeError(f"SPOT_DOWNLOAD_HTTP_STATUS {response.status}")
        return response.read()


def write_bytes_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def write_json_new(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def materialize(
    source_commit: str,
    *,
    legacy_root: Path = LEGACY_ROOT,
    legacy_manifest: Path = LEGACY_MANIFEST,
    output_root: Path = OUTPUT_ROOT,
    cohort: tuple[str, ...] = COHORT,
    start: date = START,
    days: int = DAYS,
    require_actor: bool = True,
    fetcher: Callable[[str], bytes] = fetch_public_archive,
) -> dict:
    if not SHA_RE.fullmatch(source_commit):
        raise ValueError("SOURCE_COMMIT_MUST_BE_EXACT_40HEX")
    if require_actor:
        import pwd

        if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
            raise PermissionError("EFFECTIVE_ACTOR_NOT_CRIPTA")
    if days <= 0:
        raise ValueError("INVALID_DAY_COUNT")
    if not output_root.parent.is_dir():
        raise FileNotFoundError("OUTPUT_PARENT_MISSING")
    if not os.access(output_root.parent, os.W_OK | os.X_OK):
        raise PermissionError("OUTPUT_PARENT_NOT_WRITABLE_BY_ACTOR")
    staging = output_root.with_name(output_root.name + ".partial")
    if output_root.exists():
        raise FileExistsError("IMMUTABLE_OUTPUT_ROOT_ALREADY_EXISTS")
    if staging.exists():
        raise FileExistsError("INCOMPLETE_STAGING_ROOT_ALREADY_EXISTS")

    auditor, auditor_path = load_auditor()
    manifest_index = auditor.load_manifest(legacy_manifest)
    plan = []
    legacy_preflight = []

    # No output bytes are created until every physically present legacy file
    # passes current independent size/SHA/schema/full-row validation.
    for symbol in cohort:
        if symbol not in COHORT:
            raise ValueError("SYMBOL_OUTSIDE_OWNER_COHORT")
        for offset in range(days):
            day = start + timedelta(days=offset)
            legacy_path = (
                legacy_root / symbol / f"{symbol}_{day.isoformat()}.csv.gz"
            )
            old_item = manifest_index.get((symbol, day.isoformat()))
            if legacy_path.is_file():
                if old_item is None:
                    raise ValueError(
                        f"LEGACY_FILE_WITHOUT_MANIFEST {symbol} {day}"
                    )
                checked = auditor.audit_file(
                    legacy_path, symbol, day, old_item
                )
                legacy_preflight.append(checked)
                plan.append(
                    {
                        "symbol": symbol,
                        "day": day,
                        "origin": "LEGACY_REUSE",
                        "legacy_path": legacy_path,
                        "legacy_item": old_item,
                    }
                )
            else:
                plan.append(
                    {
                        "symbol": symbol,
                        "day": day,
                        "origin": "FRESH_DOWNLOAD",
                        "legacy_path": None,
                        "legacy_item": old_item,
                    }
                )

    os.umask(0o007)
    staging.mkdir(mode=0o2770)
    snapshots = staging / "source_snapshot"
    snapshots.mkdir(mode=0o2770)
    this_script = Path(__file__).resolve()
    shutil.copyfile(this_script, snapshots / this_script.name)
    shutil.copyfile(auditor_path, snapshots / auditor_path.name)

    run_start = {
        "version": VERSION,
        "status": "STARTED_NOT_COMPLETION_PROOF",
        "source_commit": source_commit,
        "actor_uid": os.geteuid(),
        "period_start_utc": start.isoformat(),
        "period_end_exclusive_utc": (
            start + timedelta(days=days)
        ).isoformat(),
        "cohort": list(cohort),
        "expected_files": len(cohort) * days,
        "legacy_files_preflight_pass": len(legacy_preflight),
        "legacy_root": str(legacy_root),
        "legacy_manifest": str(legacy_manifest),
        "legacy_manifest_sha256": sha256(legacy_manifest),
        "write_staging_root": str(staging),
        "write_final_root": str(output_root),
        "delete_operations": "NONE",
        "db_service_live_mutation": False,
    }
    write_json_new(staging / "run_start.json", run_start)

    records = []
    reused = downloaded = 0
    for item in plan:
        symbol = item["symbol"]
        day = item["day"]
        target_dir = staging / symbol
        target_dir.mkdir(mode=0o2770, exist_ok=True)
        final_name = f"{symbol}_{day.isoformat()}.csv.gz"
        part = target_dir / (final_name + ".part")
        final = target_dir / final_name
        url = source_url(symbol, day)

        if item["origin"] == "LEGACY_REUSE":
            source = item["legacy_path"]
            assert source is not None
            with source.open("rb") as src, part.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            source_digest = sha256(source)
            copied_digest = sha256(part)
            if copied_digest != source_digest:
                raise ValueError(f"COPY_SHA_MISMATCH {symbol} {day}")
            old = item["legacy_item"]
            check_item = {
                "bytes": part.stat().st_size,
                "day": day.isoformat(),
                "path": str(source),
                "sha256": copied_digest,
                "source_symbol": symbol,
                "symbol": symbol,
                "url": old["url"],
            }
            checked = auditor.audit_file(part, symbol, day, check_item)
            origin_detail = {
                "origin": "LEGACY_REUSE",
                "legacy_path": str(source),
                "legacy_manifest_sha256": old["sha256"],
            }
            reused += 1
        else:
            payload = fetcher(url)
            if not isinstance(payload, (bytes, bytearray)) or not payload:
                raise RuntimeError(f"EMPTY_SPOT_DOWNLOAD {symbol} {day}")
            write_bytes_new(part, bytes(payload))
            digest = sha256(part)
            check_item = {
                "bytes": part.stat().st_size,
                "day": day.isoformat(),
                "path": str(part),
                "sha256": digest,
                "source_symbol": symbol,
                "symbol": symbol,
                "url": url,
            }
            checked = auditor.audit_file(part, symbol, day, check_item)
            origin_detail = {
                "origin": "FRESH_DOWNLOAD",
                "legacy_manifest_entry_existed": item["legacy_item"] is not None,
            }
            downloaded += 1

        os.rename(part, final)
        records.append(
            {
                "symbol": symbol,
                "date_utc": day.isoformat(),
                "path": str(output_root / symbol / final_name),
                "url": url,
                "bytes": final.stat().st_size,
                "sha256": sha256(final),
                "rows": checked["rows"],
                "first_exchange_timestamp_ms": checked[
                    "first_exchange_timestamp_ms"
                ],
                "last_exchange_timestamp_ms": checked[
                    "last_exchange_timestamp_ms"
                ],
                **origin_detail,
            }
        )

    expected = len(cohort) * days
    if len(records) != expected:
        raise ValueError("FINAL_SOURCE_FILE_COUNT_MISMATCH")
    manifest = {
        "version": VERSION,
        "status": "COMPLETE_IMMUTABLE_RESEARCH_SOURCE_CORPUS",
        "source_commit": source_commit,
        "archive_base": ARCHIVE_BASE,
        "period_start_utc": start.isoformat(),
        "period_end_exclusive_utc": (
            start + timedelta(days=days)
        ).isoformat(),
        "cohort": list(cohort),
        "expected_files": expected,
        "files": len(records),
        "legacy_reused": reused,
        "fresh_downloaded": downloaded,
        "legacy_manifest_sha256": sha256(legacy_manifest),
        "auditor_version": auditor.VERSION,
        "auditor_sha256": sha256(auditor_path),
        "materializer_sha256": sha256(this_script),
        "local_receiver_available_after_verified": False,
        "research_ready": False,
        "trading_effect": "NONE",
        "file_records": records,
    }
    write_json_new(staging / "SOURCE_MANIFEST.json", manifest)
    os.rename(staging, output_root)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.source_commit), sort_keys=True))


if __name__ == "__main__":
    main()
