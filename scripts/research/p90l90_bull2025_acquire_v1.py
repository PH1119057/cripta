#!/usr/bin/env python3
"""2025 rising-market P90L90 holdout public Bybit acquisition, research-only.

Independent owner-frozen 2025-04-01..2025-09-30 UTC evaluation window.
The 2025-03-22..2025-03-31 UTC days are warmup only.
This acquires data and QC, NOT trade/backtest results or OOS proof.
Only creates or updates within one new /data/cripta/research/runs child.
No production/DB/service/Exchange/StrategyCard/Trading/LIVE mutation.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SOURCE = Path("/srv/cripta/source_checkout")
PARENT = Path("/data/cripta/research/runs")
ROOT = PARENT / "p90l90_bull_2025_holdout_v1"
INPUTS = ROOT / "inputs"
SNAP = ROOT / "source_snapshot"
PILOT_DAY = dt.date(2025, 3, 22)
FIRST = PILOT_DAY
LAST = dt.date(2025, 9, 30)
EVAL_START = "2025-04-01T00:00:00Z"
EVAL_END = "2025-10-01T00:00:00Z"
SYMBOLS = ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
FILES = (
    "scripts/research/p90l90_bull2025_acquire_v1.py",
    "research/server/dataset/archive_public_trades_daily.py",
    "scripts/research/p90l90_l53_clean_v2.py",
    "scripts/research/p90l90_entry_90d_v2.py",
    "scripts/research/p90l90_exit_90d_v2.py",
    "scripts/research/p90l90_entry_only_hardstop_embargo_v1.py",
    "scripts/research/p90l90_rate_threshold_w7h24_20261110_v1.py",
)
SCHEMA = {"timestamp", "symbol", "price", "tickDirection", "trdMatchID"}
RESERVE_BYTES = 15 * 1024**3
MAX_DAILY_BYTES = 250 * 1024**2

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def write_json(path: Path, obj: object) -> None:
    tmp = path.with_name("." + path.name + ".next")
    if tmp.exists() or tmp.is_symlink():
        raise FileExistsError(f"unexpected temporary output: {tmp}")
    with tmp.open("x", encoding="utf8") as stream:
        json.dump(obj, stream, sort_keys=True, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(tmp, path)

def days(first: dt.date, last: dt.date):
    return (first + dt.timedelta(days=i) for i in range((last - first).days + 1))

def preflight(source_head: str) -> None:
    if os.geteuid() != 107:
        raise PermissionError("only Unix cripta UID 107 may acquire")
    if len(source_head) != 40 or any(c not in "0123456789abcdef" for c in source_head):
        raise ValueError("exact Git commit SHA required")
    p = subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
        check=True, stdout=subprocess.PIPE, text=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if p.stdout.strip() != source_head:
        raise RuntimeError("source mirror/expected commit mismatch")
    stat = PARENT.stat()
    if stat.st_uid != 107 or not os.access(PARENT, os.W_OK | os.X_OK):
        raise PermissionError("research runs parent not writable by actor cripta")
    if ROOT.is_symlink() or (ROOT.exists() and
                             (not ROOT.is_dir() or ROOT.stat().st_uid != 107)):
        raise PermissionError("untrusted exact experiment root")
    if INPUTS.is_symlink() or SNAP.is_symlink():
        raise PermissionError("unsafe research child symlink")
    if shutil.disk_usage(PARENT).free < RESERVE_BYTES + 1 * 1024**3:
        raise RuntimeError("insufficient data disk after 15 GiB reserve")
    if sys.version_info < (3, 11):
        raise RuntimeError("python interpreter too old")
    for rel in FILES:
        if not (SOURCE / rel).is_file():
            raise FileNotFoundError("required published source " + rel)

def start_or_resume(source_head: str, pilot: bool) -> dict:
    manifest_path = ROOT / "run_manifest.json"
    if manifest_path.is_file():
        m = json.loads(manifest_path.read_text())
        if m.get("experiment") != "P90L90_BULLREGIME_2025_V1":
            raise ValueError("other run identity in output")
        if m.get("period_utc") != [EVAL_START, EVAL_END]:
            raise ValueError("frozen evaluation window mismatch")
        if tuple(m.get("symbols", [])) != SYMBOLS:
            raise ValueError("frozen cohort mismatch")
        if m.get("warmup_first") != str(FIRST):
            raise ValueError("warmup mismatch")
        if not pilot and m.get("acquisition_stage") not in ("PILOT_QC_PASS", "FULL_QC_PASS"):
            raise RuntimeError("full acquisition requires successful pilot")
        if pilot:
            raise FileExistsError("pilot already exists; do not duplicate")
        for rel, expected in m["source_snapshot_sha256"].items():
            f = SNAP / rel
            if not f.is_file() or digest(f) != expected:
                raise RuntimeError("immutable source snapshot parity failed")
        return m
    if ROOT.exists():
        raise RuntimeError("existing root without prepared manifest; manual forensic required")
    ROOT.mkdir(mode=0o2770)
    INPUTS.mkdir(mode=0o2770)
    SNAP.mkdir(mode=0o2770)
    (ROOT / "logs").mkdir(mode=0o2770)
    hashes = {}
    for rel in FILES:
        src = SOURCE / rel
        dst = SNAP / rel
        dst.parent.mkdir(mode=0o2770, parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        hashes[rel] = digest(dst)
        if digest(src) != hashes[rel]:
            raise ValueError("source capture hash mismatch")
    m = {
        "experiment": "P90L90_BULLREGIME_2025_V1",
        "status": "ACQUISITION_PREPARED_NOT_REPLAYED",
        "acquisition_stage": "PREPARED",
        "period_utc": [EVAL_START, EVAL_END],
        "warmup_first": str(FIRST),
        "warmup_last": "2025-03-31",
        "symbols": list(SYMBOLS),
        "source_head": source_head,
        "source_snapshot_sha256": hashes,
        "venue": "Bybit public LINEAR USDT public trades; per-day original order",
        "source_url_template": "https://public.bybit.com/trading/{symbol}/{symbol}{YYYY-MM-DD}.csv.gz",
        "expected_archive_days": (LAST - FIRST).days + 1,
        "expected_archive_objects": ((LAST - FIRST).days + 1) * len(SYMBOLS),
        "research_exclusion": "No LIVE, DB, deployment, trading policy, or Strategy mutation",
    }
    write_json(manifest_path, m)
    (ROOT / "source_commit.txt").write_text(source_head + "\n")
    (ROOT / "source_sha256.txt").write_text(
        "".join(f"{v}  source_snapshot/{k}\n" for k, v in sorted(hashes.items()))
    )
    (ROOT / "environment.txt").write_text(json.dumps(
        {"python": sys.version, "uid": os.geteuid(), "gid": os.getegid(),
         "source_head": source_head}, sort_keys=True) + "\n")
    (ROOT / "input_provenance.json").write_text(json.dumps(
        {"period_selected_from": "public total-market cap 2025 Q2 +24%, Q3 +16.4%",
         "archive_url_format": m["source_url_template"],
         "known_warmup": [str(FIRST), "2025-03-31"],
         "eval": [EVAL_START, EVAL_END],
         "new_data_until_verified": "NO_DATA"}, indent=2) + "\n")
    return m

def validate_archive(symbol: str, day: dt.date, path: Path) -> dict:
    start = dt.datetime.combine(day, dt.time(), dt.timezone.utc).timestamp()
    count = 0
    first = last = None
    last_trade_ids = set()
    with gzip.open(path, "rt", newline="") as source:
        reader = csv.DictReader(source)
        if not SCHEMA.issubset(reader.fieldnames or []):
            raise ValueError(f"{path.name} CSV schema mismatch")
        for row in reader:
            t = float(row["timestamp"])
            if row["symbol"] != symbol or not start <= t < start + 86400:
                raise ValueError(f"off-symbol/off-day row {path}")
            if last is not None and t < last:
                raise ValueError(f"out-of-order native event {path}")
            if not row["trdMatchID"] or not row["tickDirection"] or float(row["price"]) <= 0:
                raise ValueError(f"trade source invalid {path}")
            if last is None or t != last:
                last_trade_ids.clear()
            if row["trdMatchID"] in last_trade_ids:
                raise ValueError(f"duplicate native execution ID at same timestamp: {path}")
            last_trade_ids.add(row["trdMatchID"])
            first = t if first is None else first
            last = t
            count += 1
    if not count:
        raise ValueError("empty Bybit day " + path.name)
    return {"sha256": digest(path), "size_bytes": path.stat().st_size,
            "rows": count, "first_ts": first, "last_ts": last}

def run(stage: str, source_head: str) -> None:
    pilot = stage == "pilot"
    preflight(source_head)
    if not ROOT.exists() and not pilot:
        raise RuntimeError("run pilot before full acquisition")
    if ROOT.exists() and pilot:
        raise RuntimeError("pilot cannot run into existing root")
    if not ROOT.exists():
        start_or_resume(source_head, True)
    with (ROOT / ".holdout_acquire.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        m = start_or_resume(source_head, False) if not pilot else json.loads(
            (ROOT / "run_manifest.json").read_text()
        )
        from_day = FIRST
        through_day = PILOT_DAY if pilot else LAST
        expected = (through_day - from_day).days + 1
        for rel, sha in m["source_snapshot_sha256"].items():
            assert digest(SNAP / rel) == sha
        downloader = SOURCE / "research/server/dataset/archive_public_trades_daily.py"
        if digest(downloader) != m["source_snapshot_sha256"][
            "research/server/dataset/archive_public_trades_daily.py"
        ]:
            raise RuntimeError("published downloader source bytes drifted")
        # Validate any previously acquired files by their recorded hash before
        # a resume. This forbids silent replacement of an earlier data day.
        archive_manifest = INPUTS / "MANIFEST.json"
        if archive_manifest.is_file():
            a = json.loads(archive_manifest.read_text())
            for rel, row in a.get("entries", {}).items():
                if (INPUTS / rel).is_file() and digest(INPUTS / rel) != row["sha256"]:
                    raise RuntimeError("previous archive hash mismatch: " + rel)
        cmd = [
            sys.executable, "-B", str(downloader), "--root", str(INPUTS),
            "--symbols", *SYMBOLS, "--start", str(from_day),
            "--end", str(through_day), "--reserve-gb", "15",
        ]
        (ROOT / "command.txt").write_text(" ".join(cmd) + "\n")
        print(json.dumps({"stage": "ACQUISITION_STARTED", "phase": stage,
                          "source_head": source_head,
                          "days": expected, "symbols": SYMBOLS,
                          "objects_expected": expected * len(SYMBOLS),
                          "root": str(ROOT)}), flush=True)
        t0 = time.monotonic()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        while proc.poll() is None:
            time.sleep(20)
            st_path = INPUTS / "archive_state.json"
            progress = {}
            if st_path.is_file():
                try:
                    state = json.loads(st_path.read_text())
                    done = int(state.get("files_checked", 0))
                    total = expected * len(SYMBOLS)
                    elapsed = round(time.monotonic() - t0, 1)
                    progress = {"checked": done, "total": total,
                                "percent": round(100 * done / total, 2),
                                "elapsed_s": elapsed,
                                "eta_s": round(elapsed * (total - done) / done) if done else None,
                                "downloaded": state.get("files_downloaded"),
                                "cached": state.get("files_cached"),
                                "errors": len(state.get("errors", []))}
                except (OSError, ValueError, TypeError):
                    progress = {"status": "progress file not yet consistent"}
            print(json.dumps({"stage": "DOWNLOAD_HEARTBEAT", "phase": stage,
                              "pid": proc.pid, **progress}), flush=True)
        stdout, _ = proc.communicate()
        (ROOT / "logs" / ("download_" + stage + ".log")).write_text(stdout)
        if proc.returncode:
            raise RuntimeError(f"Bybit downloader failed exit={proc.returncode}; see bounded source log")
        state = json.loads((INPUTS / "archive_state.json").read_text())
        if state.get("status") != "complete" or state.get("files_checked") != expected * len(SYMBOLS):
            raise RuntimeError("archive incomplete or missing days")
        entries = json.loads(archive_manifest.read_text())["entries"]
        qc = {}
        n_rows = 0
        for symbol in SYMBOLS:
            for day in days(from_day, through_day):
                rel = f"{symbol}/public_trades/{symbol}{day}.csv.gz"
                raw = INPUTS / rel
                entry = entries.get(rel)
                if not isinstance(entry, dict) or not raw.is_file():
                    raise RuntimeError("missing raw+manifest pair: " + rel)
                if raw.stat().st_size > MAX_DAILY_BYTES:
                    raise ValueError("daily file too large: " + rel)
                validated = validate_archive(symbol, day, raw)
                if validated["sha256"] != entry["sha256"] or validated["size_bytes"] != entry["bytes"]:
                    raise RuntimeError("download manifest mismatch: " + rel)
                qc[rel] = validated
                n_rows += validated["rows"]
        if len(qc) != expected * len(SYMBOLS):
            raise RuntimeError("archive QC cardinality")
        write_json(ROOT / ("qc_" + stage + ".json"), {
            "stage": "ACQUISITION_QC_PASS", "phase": stage,
            "source_head": source_head,
            "files": len(qc), "total_native_trades": n_rows,
            "by_day": qc, "period_utc": [EVAL_START, EVAL_END],
            "warmup": ["2025-03-22", "2025-03-31"],
        })
        m["acquisition_stage"] = "PILOT_QC_PASS" if pilot else "FULL_QC_PASS"
        m["status"] = "INPUT_PILOT_VALIDATED_NOT_REPLAYED" if pilot else (
            "INPUT_COMPLETE_QC_PASS_NO_STRATEGY_REPLAY"
        )
        m["last_source_head"] = source_head
        m["archive_object_count"] = len(qc)
        m["archive_trade_rows"] = n_rows
        write_json(ROOT / "run_manifest.json", m)
        print(json.dumps({"stage": "ACQUISITION_QC_PASS", "phase": stage,
                          "objects": len(qc), "native_rows": n_rows,
                          "status": m["status"]}), flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("pilot", "full"), required=True)
    parser.add_argument("--source-head", required=True)
    args = parser.parse_args()
    run(args.phase, args.source_head)

if __name__ == "__main__":
    main()
