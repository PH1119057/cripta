#!/usr/bin/env python3
"""Issue #158 / #13 bounded first-month materializer.

Production scope is fixed to the owner-selected five Bybit linear symbols and
[2026-05-18, 2026-06-17) UTC. It writes only a new immutable research run
under data-disk. Raw OB200 inputs are read-only; no DB/service/LIVE mutation.
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
from datetime import date, timedelta
from pathlib import Path

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
DAYS = 30
RAW_ROOT = Path("/data/cripta/datasets/raw/20260518_20260816")
OUTPUT_ROOT = Path("/data/cripta/research/runs/r158_firstmonth_13_20261031")
EXPECTED_READER_VERSION = "R158_DEPTH_RECOVERY_EXACT_PRICE_MULTI_HORIZON_V1"
EXPECTED_CUBE_VERSION = (
    "R158_DEPTH_RECOVERY_START_MINUTE_SIDE_STRATUM_HORIZON_V1"
)
CUBE_ROWS_PER_DAY = 1440 * 2 * 2 * 3
VERSION = "R158_FIRSTMONTH_13_MATERIALIZER_V1"
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


def write_json_new(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_reader(path: Path):
    spec = importlib.util.spec_from_file_location("r158_depth13_primary", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("DEPTH13_READER_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if module.VERSION != EXPECTED_READER_VERSION:
        raise ValueError("DEPTH13_READER_VERSION_DRIFT")
    if module.CUBE_VERSION != EXPECTED_CUBE_VERSION:
        raise ValueError("DEPTH13_CUBE_VERSION_DRIFT")
    return module


def materialize_symbol(
    symbol: str,
    source_commit: str,
    *,
    raw_root: Path = RAW_ROOT,
    output_root: Path = OUTPUT_ROOT,
    days: int = DAYS,
    require_actor: bool = True,
) -> dict:
    if symbol not in COHORT:
        raise ValueError("SYMBOL_OUTSIDE_OWNER_COHORT")
    if not SHA_RE.fullmatch(source_commit):
        raise ValueError("SOURCE_COMMIT_MUST_BE_EXACT_40HEX")
    if days != 30:
        raise ValueError("ONLY_FULL_FIRSTMONTH_30D_ALLOWED")
    actor_uid = _effective_actor_uid(require_actor)
    if not output_root.parent.is_dir():
        raise FileNotFoundError("OUTPUT_PARENT_MISSING")
    if not os.access(output_root.parent, os.W_OK | os.X_OK):
        raise PermissionError("OUTPUT_PARENT_NOT_WRITABLE_BY_ACTOR")

    source_dir = Path(__file__).resolve().parent
    reader_path = source_dir / "r158_depth_recovery_exact_price_v1.py"
    if not reader_path.is_file():
        raise FileNotFoundError("VERSIONED_DEPTH13_READER_MISSING")
    reader = load_reader(reader_path)

    source_days: list[tuple[date, Path]] = []
    for offset in range(days):
        day = START + timedelta(days=offset)
        path = (
            raw_root
            / symbol
            / "orderbook"
            / f"{day}_{symbol}_ob200.data.zip"
        )
        if not path.is_file() or not os.access(path, os.R_OK):
            raise FileNotFoundError(f"ORIGINAL_OB200_UNREADABLE {path}")
        source_days.append((day, path))

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
    }
    write_json_new(
        target / "run_start.json",
        {
            "status": "STARTED_NOT_COMPLETION_PROOF",
            "actor_uid": actor_uid,
            "symbol": symbol,
            "period_utc": "[2026-05-18T00:00:00Z,2026-06-17T00:00:00Z)",
            "days": days,
            "read_root": str(raw_root),
            "write_root": str(target),
            "delete_operations": "NONE",
            "db_service_live_mutation": False,
            **identity,
        },
    )

    part = target / "depth_recovery_cube.jsonl.gz.part"
    final = target / "depth_recovery_cube.jsonl.gz"
    provenance = []
    outcome_counts = {
        f"{outcome}_{h}s": 0
        for h in (15, 60, 300)
        for outcome in ("R", "N", "C")
    }
    rows = episodes = episode_horizon_observations = raw_records = 0

    with part.open("xb") as binary:
        with gzip.GzipFile(fileobj=binary, mode="wb", mtime=0) as zipped:
            with io.TextIOWrapper(
                zipped, encoding="utf-8", newline="\n"
            ) as writer:
                for day, source in source_days:
                    manifest, day_rows = reader.calculate_archive(
                        source, symbol, day
                    )
                    if manifest["rows"] != len(day_rows):
                        raise ValueError("DAY_MANIFEST_ROW_COUNT_MISMATCH")
                    if len(day_rows) != CUBE_ROWS_PER_DAY:
                        raise ValueError("DAY_CUBE_CARDINALITY_MISMATCH")
                    if (
                        manifest["episode_horizon_observations"]
                        != manifest["episodes"] * 3
                    ):
                        raise ValueError(
                            "DAY_EPISODE_HORIZON_OBSERVATION_COUNT"
                        )
                    for row in day_rows:
                        writer.write(
                            json.dumps(row, sort_keys=True) + "\n"
                        )
                        rows += 1
                        key = f"{row['outcome']}_{row['horizon_s']}s"
                        outcome_counts[key] += 1
                    episodes += manifest["episodes"]
                    episode_horizon_observations += manifest[
                        "episode_horizon_observations"
                    ]
                    raw_records += manifest["raw_records_read"]
                    provenance.append(
                        {
                            "date_utc": day.isoformat(),
                            "source_path": str(source),
                            "source_sha256": manifest["source_zip_sha256"],
                            "source_bytes": source.stat().st_size,
                            "raw_records_read": manifest["raw_records_read"],
                            "source_suffix_records": manifest[
                                "source_suffix_records"
                            ],
                            "episodes": manifest["episodes"],
                            "episode_horizon_observations": manifest[
                                "episode_horizon_observations"
                            ],
                            "rows": manifest["rows"],
                            "outcome_counts": manifest["outcome_counts"],
                            "reader_qc": manifest["reader_qc"],
                        }
                    )
        binary.flush()
        os.fsync(binary.fileno())
    os.rename(part, final)

    if len(provenance) != 30:
        raise ValueError("FIRSTMONTH_DAY_COUNT_MISMATCH")
    if rows != days * CUBE_ROWS_PER_DAY:
        raise ValueError("FULL_CUBE_CARDINALITY_MISMATCH")
    if episode_horizon_observations != episodes * 3:
        raise ValueError("FULL_EPISODE_HORIZON_OBSERVATION_COUNT")

    write_json_new(target / "input_provenance.json", {"days": provenance})
    result = {
        "status": "DEPTH13_FIRSTMONTH_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC",
        "symbol": symbol,
        "days": days,
        "indicator_id": 13,
        "formula_version": reader.VERSION,
        "cube_version": reader.CUBE_VERSION,
        "cube_grain": "UTC start-minute × side × start-rank stratum × horizon",
        "episodes": episodes,
        "episode_horizon_observations": episode_horizon_observations,
        "rows": rows,
        "raw_records_read": raw_records,
        "outcome_counts": outcome_counts,
        "output_file": str(final),
        "output_sha256": sha256(final),
        "receiver_clock_verified": False,
        "verified_available_after": False,
        "source_completeness_verified": False,
        "research_ready": False,
        "classification": "DESCRIPTIVE_ONLY",
        "limitations": [
            "Historical local receiver/publication available_after not captured.",
            "Independent raw-source QC is a separate mandatory stage.",
        ],
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
