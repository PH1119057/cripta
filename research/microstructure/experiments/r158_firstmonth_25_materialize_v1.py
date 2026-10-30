#!/usr/bin/env python3
"""Issue #158 / #25 full first-month Spot/Perpetual VWAP materializer.

Research-only bounded writer. Requires the immutable 150/150 Spot source corpus
and original Bybit USDT-linear public trades. Writes one immutable per-symbol
43,200-minute artifact under data-disk. Historical local available_after is
unknown; outputs are DESCRIPTIVE_ONLY, never Strategy/LIVE evidence.
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
DAYS = 30
SPOT_ROOT = Path("/data/cripta/research/cache/r158_spot_firstmonth_20261030")
PERP_ROOT = Path("/data/cripta/datasets/raw/20260518_20260816")
OUTPUT_ROOT = Path("/data/cripta/research/runs/r158_firstmonth_25_20261031")
READER_VERSION = "R158_SPOT_PERP_EXECUTED_VWAP_1M_V2"
VERSION = "R158_FIRSTMONTH_25_MATERIALIZER_V1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def actor_uid(require_actor: bool) -> int:
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


def load_reader(path: Path):
    spec = importlib.util.spec_from_file_location("r158_spot_perp_primary", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("SPOT_PERP_READER_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if module.VERSION != READER_VERSION:
        raise ValueError("SPOT_PERP_READER_VERSION_DRIFT")
    return module


def load_spot_manifest(root: Path) -> tuple[dict, dict[tuple[str, str], dict]]:
    path = root / "SOURCE_MANIFEST.json"
    if not path.is_file():
        raise FileNotFoundError("SPOT_SOURCE_MANIFEST_MISSING")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        data.get("status") != "COMPLETE_IMMUTABLE_RESEARCH_SOURCE_CORPUS"
        or data.get("expected_files") != 150
        or data.get("files") != 150
    ):
        raise ValueError("SPOT_SOURCE_CORPUS_NOT_COMPLETE")
    indexed = {}
    for item in data.get("file_records", []):
        key = (item.get("symbol"), item.get("date_utc"))
        if (
            key[0] not in COHORT
            or not isinstance(key[1], str)
            or key in indexed
        ):
            raise ValueError("SPOT_SOURCE_MANIFEST_IDENTITY")
        indexed[key] = item
    if len(indexed) != 150:
        raise ValueError("SPOT_SOURCE_MANIFEST_CARDINALITY")
    return data, indexed


def materialize_symbol(
    symbol: str,
    source_commit: str,
    *,
    spot_root: Path = SPOT_ROOT,
    perp_root: Path = PERP_ROOT,
    output_root: Path = OUTPUT_ROOT,
    require_actor: bool = True,
) -> dict:
    if symbol not in COHORT:
        raise ValueError("SYMBOL_OUTSIDE_OWNER_COHORT")
    if not SHA_RE.fullmatch(source_commit):
        raise ValueError("SOURCE_COMMIT_MUST_BE_EXACT_40HEX")
    uid = actor_uid(require_actor)
    if not output_root.parent.is_dir():
        raise FileNotFoundError("OUTPUT_PARENT_MISSING")
    if not os.access(output_root.parent, os.W_OK | os.X_OK):
        raise PermissionError("OUTPUT_PARENT_NOT_WRITABLE")

    source_dir = Path(__file__).resolve().parent
    reader_path = source_dir / "r158_spot_perp_vwap_v1.py"
    reader = load_reader(reader_path)
    _, spot_index = load_spot_manifest(spot_root)
    spot_manifest_sha = sha256(spot_root / "SOURCE_MANIFEST.json")

    source_days = []
    for offset in range(DAYS):
        day = START + timedelta(days=offset)
        spot = reader.original_archive_path(spot_root, symbol, day, "spot")
        perp = reader.original_archive_path(perp_root, symbol, day, "perpetual")
        item = spot_index.get((symbol, day.isoformat()))
        if item is None or not spot.is_file() or not perp.is_file():
            raise FileNotFoundError(f"FIRSTMONTH_SOURCE_MISSING {symbol} {day}")
        if spot.stat().st_size != int(item["bytes"]):
            raise ValueError("SPOT_SOURCE_SIZE_DRIFT")
        if sha256(spot) != item["sha256"]:
            raise ValueError("SPOT_SOURCE_HASH_DRIFT")
        source_days.append((day, spot, perp, item))

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
    shutil.copyfile(reader_path, snapshots / reader_path.name)

    identity = {
        "materializer_version": VERSION,
        "materializer_sha256": sha256(this_script),
        "reader_version": reader.VERSION,
        "reader_sha256": sha256(reader_path),
        "source_commit": source_commit,
        "spot_source_manifest_sha256": spot_manifest_sha,
    }
    write_json_new(
        target / "run_start.json",
        {
            "status": "STARTED_NOT_COMPLETION_PROOF",
            "actor_uid": uid,
            "symbol": symbol,
            "period_utc": "[2026-05-18T00:00:00Z,2026-06-17T00:00:00Z)",
            "read_spot_root": str(spot_root),
            "read_perpetual_root": str(perp_root),
            "write_root": str(target),
            "delete_operations": "NONE",
            "db_service_live_mutation": False,
            **identity,
        },
    )

    part = target / "divergence_1m.jsonl.gz.part"
    final = target / "divergence_1m.jsonl.gz"
    provenance = []
    observed = no_data = rows = spot_rows = perp_rows = 0
    max_rounding = None
    with part.open("xb") as binary:
        with gzip.GzipFile(fileobj=binary, mode="wb", mtime=0) as zipped:
            with io.TextIOWrapper(zipped, encoding="utf-8", newline="\n") as out:
                for day, spot, perp, source_item in source_days:
                    manifest, day_rows = reader.calculate_day(
                        symbol, day, spot_root, perp_root
                    )
                    if len(day_rows) != 1440:
                        raise ValueError("DAY_MINUTE_GRID_INCOMPLETE")
                    if (
                        manifest["spot"]["source_sha256"] != source_item["sha256"]
                        or manifest["spot"]["source_sha256"] != sha256(spot)
                    ):
                        raise ValueError("SPOT_DAY_HASH_DRIFT")
                    perp_sha = sha256(perp)
                    if manifest["perpetual"]["source_sha256"] != perp_sha:
                        raise ValueError("PERP_DAY_HASH_DRIFT")
                    if (
                        manifest["perpetual"]["native_perpetual_notional_parity"]
                        != "PASS_ALL_ROWS_FLOAT_REPR_ROUNDING_BOUNDED"
                    ):
                        raise ValueError("PERP_NATIVE_NOTIONAL_PARITY_FAILED")
                    day_observed = 0
                    for row in day_rows:
                        minute = int(row["minute_utc"])
                        stamp = datetime(
                            day.year, day.month, day.day, tzinfo=UTC
                        ) + timedelta(minutes=minute)
                        payload = {
                            "venue_id": "Bybit",
                            "instrument_id": symbol,
                            "indicator_id": 25,
                            "formula_version": reader.VERSION,
                            "minute_start_utc": stamp.isoformat(),
                            "observed_at_utc": None,
                            "verified_available_after_utc": None,
                            **row,
                        }
                        out.write(json.dumps(payload, sort_keys=True) + "\n")
                        rows += 1
                        if row["status"] == "OBSERVED":
                            observed += 1
                            day_observed += 1
                        else:
                            no_data += 1
                    spot_rows += int(manifest["spot"]["rows"])
                    perp_rows += int(manifest["perpetual"]["rows"])
                    relative = manifest["perpetual"][
                        "native_perpetual_max_relative_rounding"
                    ]
                    if relative is not None:
                        from decimal import Decimal

                        value = Decimal(relative)
                        max_rounding = (
                            value
                            if max_rounding is None
                            else max(max_rounding, value)
                        )
                    provenance.append(
                        {
                            "date_utc": day.isoformat(),
                            "spot_path": str(spot),
                            "spot_bytes": spot.stat().st_size,
                            "spot_sha256": manifest["spot"]["source_sha256"],
                            "spot_rows": manifest["spot"]["rows"],
                            "spot_origin": source_item["origin"],
                            "perpetual_path": str(perp),
                            "perpetual_bytes": perp.stat().st_size,
                            "perpetual_sha256": perp_sha,
                            "perpetual_rows": manifest["perpetual"]["rows"],
                            "observed_minutes": day_observed,
                            "no_data_minutes": 1440 - day_observed,
                            "perpetual_rounding_rows": manifest["perpetual"][
                                "native_perpetual_foreign_rounding_rows"
                            ],
                        }
                    )
        binary.flush()
        os.fsync(binary.fileno())
    os.rename(part, final)
    if rows != DAYS * 1440 or observed + no_data != rows:
        raise ValueError("FIRSTMONTH_MINUTE_CARDINALITY_MISMATCH")

    write_json_new(target / "input_provenance.json", {"days": provenance})
    result = {
        "status": "SPOT_PERP_25_FIRSTMONTH_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC",
        "symbol": symbol,
        "indicator_id": 25,
        "formula_version": reader.VERSION,
        "days": DAYS,
        "rows": rows,
        "observed_minutes": observed,
        "no_data_minutes": no_data,
        "spot_rows": spot_rows,
        "perpetual_rows": perp_rows,
        "max_perpetual_native_relative_rounding": (
            str(max_rounding) if max_rounding is not None else None
        ),
        "output_file": str(final),
        "output_sha256": sha256(final),
        "receiver_clock_verified": False,
        "verified_available_after": False,
        "research_ready": False,
        "classification": "DESCRIPTIVE_ONLY",
        **identity,
    }
    write_json_new(target / "run_final.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=COHORT)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            materialize_symbol(args.symbol, args.source_commit),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
