#!/usr/bin/env python3
"""Frozen 2025 P90L90 causal L5-3 geometry using unchanged 2026 CLEAN v2 core.

Research-only; no strategy or Entry/Exit policy changes. The 2026 parity gate
executes the same function on old May08-09 data and compares row-for-row
to the original 100d frozen L5 output. 2025 geometry is then generated from
all 193 downloaded native archive days (10 warmup + 183 evaluation).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

SOURCE = Path("/srv/cripta/source_checkout")
ROOT = Path("/data/cripta/research/runs/p90l90_bull_2025_holdout_v1")
RAW = ROOT / "inputs"
RESULTS = ROOT / "results"
OLD_RAW = Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/inputs")
OLD_GEOMETRY = Path(
    "/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/results/l53_clean_v2/parts"
)
CORE_REL = "scripts/research/p90l90_l53_clean_v2.py"
SELF_REL = "scripts/research/p90l90_bull2025_geometry_v1.py"
SYMBOLS = ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
FIRST, LAST = "2025-03-22", "2025-09-30"
TOTAL_ROWS = 204446371
EXPECTED_STATES = 193 * 288 - 199
OUTPUTS = {
    "parity-2026": "l53_geometry_parity_2026_v1",
    "smoke-2025": "l53_geometry_smoke_2025_v1",
    "full": "l53_geometry_full_2025_v1",
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: object) -> None:
    tmp = path.with_name("." + path.name + ".next")
    if tmp.exists() or tmp.is_symlink():
        raise FileExistsError("unsafe write collision: " + str(tmp))
    with tmp.open("x", encoding="utf8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
    os.replace(tmp, path)


def source_head() -> str:
    return subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    ).stdout.strip()


def preflight(mode: str, expected_head: str) -> tuple[Path, dict, dict]:
    if os.geteuid() != 107:
        raise PermissionError("research geometry must be written as cripta UID107")
    if source_head() != expected_head or len(expected_head) != 40:
        raise RuntimeError("GitHub main / source mirror identity mismatch")
    if mode not in OUTPUTS or RESULTS.is_symlink():
        raise ValueError("invalid frozen geometry stage")
    if not ROOT.is_dir() or ROOT.stat().st_uid != 107:
        raise PermissionError("exact 2025 data root not owned by cripta")
    if not os.access(ROOT, os.R_OK | os.W_OK | os.X_OK):
        raise PermissionError("no full research-data path access for writer")
    if shutil.disk_usage(ROOT).free < 17 * 2**30:
        raise RuntimeError("research data disk free space below 17GiB preflight")
    original = json.loads((ROOT / "run_manifest.json").read_text())
    qc = json.loads((ROOT / "qc_full.json").read_text())
    if original.get("status") != "INPUT_COMPLETE_QC_PASS_NO_STRATEGY_REPLAY":
        raise RuntimeError("complete 2025 raw source identity not verified")
    if original.get("symbols") != list(SYMBOLS) or original.get("period_utc") != [
        "2025-04-01T00:00:00Z", "2025-10-01T00:00:00Z",
    ] or qc.get("files") != 965 or qc.get("total_native_trades") != TOTAL_ROWS:
        raise ValueError("market-selected cohort, dates or native row count changed")
    core = SOURCE / CORE_REL
    if sha(core) != original["source_snapshot_sha256"][CORE_REL] or (
        sha(ROOT / "source_snapshot" / CORE_REL)
        != original["source_snapshot_sha256"][CORE_REL]
    ):
        raise RuntimeError("2026 geometry core drifted; no implicit algorithm change")
    if not (SOURCE / SELF_REL).is_file():
        raise FileNotFoundError(SELF_REL)
    output = RESULTS / OUTPUTS[mode]
    if output.exists() or output.is_symlink():
        raise FileExistsError("immutable geometry stage already exists: " + str(output))
    if RESULTS.exists() and (
        RESULTS.is_symlink() or RESULTS.stat().st_uid != 107
        or not os.access(RESULTS, os.W_OK | os.X_OK)
    ):
        raise PermissionError("research results path not owned/writable by cripta")
    if mode == "full" and not (
        (RESULTS / OUTPUTS["parity-2026"] / "qc.json").is_file()
        and (RESULTS / OUTPUTS["smoke-2025"] / "qc.json").is_file()
    ):
        raise RuntimeError("both old 2026 parity and new 2025 smoke QC required")
    return output, original, qc


def prefix_parity(generated: Path, frozen: Path) -> tuple[int, str]:
    """Compare complete decompressed rows, field order and values, not gzip bytes."""
    n = 0
    with gzip.open(generated, "rt", newline="") as left, gzip.open(
        frozen, "rt", newline=""
    ) as right:
        a, b = csv.reader(left), csv.reader(right)
        for i, (left_row, right_row) in enumerate(
            itertools.zip_longest(a, itertools.islice(b, None)), start=1
        ):
            if left_row is None:
                break
            if left_row != right_row:
                raise RuntimeError(f"geometry 2026 frozen prefix parity failed at row {i}")
            n += 1
    if n < 100:
        raise ValueError("too few 2026 reference geometry rows")
    return n, sha(generated)


def run(mode: str, commit: str) -> dict:
    output, raw_manifest, qc = preflight(mode, commit)
    sys.path.insert(0, str(SOURCE))
    from scripts.research import p90l90_l53_clean_v2 as core  # noqa: PLC0415

    RESULTS.mkdir(mode=0o2770, exist_ok=True)
    output.mkdir(mode=0o2770, exist_ok=False)
    parts = output / "parts"
    parts.mkdir(mode=0o2770)
    snapshot = output / "source_snapshot"
    snapshot.mkdir(mode=0o2770)
    captured = {}
    for rel in (SELF_REL, CORE_REL):
        src = SOURCE / rel
        dst = snapshot / Path(rel).name
        shutil.copyfile(src, dst)
        captured[dst.name] = sha(dst)
        if captured[dst.name] != sha(src):
            raise RuntimeError("geometry source-snapshot copy mismatch")
    (output / "source_commit.txt").write_text(commit + "\n")
    start = time.monotonic()
    first, last = (
        ("2026-05-08", "2026-05-09") if mode == "parity-2026"
        else ("2025-03-22", "2025-03-23") if mode == "smoke-2025"
        else (FIRST, LAST)
    )
    raw_root = OLD_RAW if mode == "parity-2026" else RAW
    coins = ("LTCUSDT",) if mode != "full" else SYMBOLS
    status = {
        "status": "RUNNING_NOT_VERIFIED", "mode": mode, "symbols": list(coins),
        "source_head": commit, "source_snapshots_sha256": captured,
        "algorithm": "p90l90_l53_clean_v2.process_symbol UNCHANGED",
        "date_range": [first, last],
        "raw_root": str(raw_root),
        "evaluation_utc": raw_manifest["period_utc"],
    }
    write_json(output / "manifest.json", status)
    report = {}
    if mode != "full":
        symbol = coins[0]
        print(json.dumps({"stage": "PROCESS_SYMBOL", "symbol": symbol, "mode": mode}), flush=True)
        report[symbol] = core.process_symbol(symbol, raw_root, str(parts), first, last)
    else:
        with ProcessPoolExecutor(max_workers=2) as pool:
            pending = {
                pool.submit(core.process_symbol, symbol, raw_root, str(parts), first, last): symbol
                for symbol in coins
            }
            while pending:
                done, _ = wait(pending, timeout=20, return_when=FIRST_COMPLETED)
                if not done:
                    print(json.dumps({
                        "stage": "GEOMETRY_HEARTBEAT",
                        "completed": len(report), "total": len(coins),
                        "elapsed_s": round(time.monotonic()-start, 1),
                    }), flush=True)
                for job in done:
                    symbol = pending.pop(job)
                    meta = job.result()
                    report[symbol] = meta
                    print(json.dumps({
                        "stage": "GEOMETRY_SYMBOL_DONE", "symbol": symbol,
                        "native_rows": meta["trades"], "states": meta["states"],
                        "touches": meta["touches"], "completed": len(report),
                        "total": len(coins),
                    }), flush=True)
    for sym, meta in report.items():
        if meta["files"] != ((193 if mode == "full" else 2)):
            raise RuntimeError("raw UTC day coverage missing " + sym)
        if mode == "full":
            rows = sum(
                data["rows"] for path, data in qc["by_day"].items()
                if path.startswith(sym + "/")
            )
            if meta["trades"] != rows or meta["states"] != EXPECTED_STATES:
                raise RuntimeError(f"2025 geometry native row/state parity failed: {sym}")
        for kind in ("states", "touches"):
            path = parts / f"{kind}_{sym}.csv.gz"
            if sha(path) != meta[f"{kind}_sha256"]:
                raise RuntimeError("frozen geometry SHA mismatch: " + str(path))
    if mode == "parity-2026":
        for kind in ("states", "touches"):
            n, _ = prefix_parity(
                parts / f"{kind}_LTCUSDT.csv.gz",
                OLD_GEOMETRY / f"{kind}_LTCUSDT.csv.gz",
            )
            print(json.dumps({"stage": "2026_EXACT_ROW_PARITY", "kind": kind, "rows": n}), flush=True)
    result = {
        **status,
        "status": "QC_PASS_PARITY_UNCHANGED_CORE" if mode == "parity-2026"
        else "QC_PASS_PILOT_2025" if mode == "smoke-2025"
        else "QC_PASS_FULL_2025_GEOMETRY_NOT_TRADING",
        "by_symbol": report, "elapsed_s": round(time.monotonic()-start, 3),
    }
    write_json(output / "qc.json", result)
    print(json.dumps({
        "stage": "GEOMETRY_COMPLETE_QC_PASS", "mode": mode,
        "counts": {s: {k: v[k] for k in ("trades", "states", "touches")}
                   for s, v in report.items()}, "elapsed_s": result["elapsed_s"],
    }), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=OUTPUTS)
    parser.add_argument("--source-head", required=True)
    args = parser.parse_args()
    run(args.mode, args.source_head)


if __name__ == "__main__":
    main()
