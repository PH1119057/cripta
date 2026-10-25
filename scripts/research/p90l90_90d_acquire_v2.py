#!/usr/bin/env python3
"""Immutable Bybit public LINEAR trade input for P90L90 full-90d research v2.

Downloads ONLY new pre-window warmup days (May 08..May 16 UTC) to the
versioned Data-disk research run. Existing May17..Aug15 files are linked
read-only and verified against the original immutable Entry SHA256 manifest.
No exchange credentials / orders, no trading runtime changes.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import tempfile
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SYMBOLS = ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
FIRST = date(2026, 5, 8)
EVAL_START = date(2026, 5, 18)
END = date(2026, 8, 16)
WARMUP_LINK_START = date(2026, 5, 17)
DATA_ROOT = Path("/data/cripta/research/runs")
OLD_RAW = Path("/data/cripta/datasets/raw/20260518_20260816")
OLD_ENTRY = DATA_ROOT / "p90l90_v1_20261024/results"
MAX_DAILY_BYTES = 250 * 1024 * 1024
TRADE_FIELDS = frozenset(("timestamp", "symbol", "price", "tickDirection", "trdMatchID"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def dates(start: date, end: date):
    return [start + timedelta(days=d) for d in range((end - start).days)]


def validate_day(path: Path, symbol: str, target_date: date) -> dict:
    expected_start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc).timestamp()
    expected_end = expected_start + 86400
    first = last = None
    count = 0
    last_ids = set()
    with gzip.open(path, "rt", newline="") as stream:
        reader = csv.DictReader(stream)
        if not TRADE_FIELDS.issubset(set(reader.fieldnames or ())):
            raise ValueError("native Bybit public trade schema mismatch")
        for row in reader:
            timestamp = float(row["timestamp"])
            if row["symbol"] != symbol or not expected_start <= timestamp < expected_end:
                raise ValueError("off-symbol or off-day native trade")
            if last is not None and timestamp < last:
                raise ValueError("out-of-order native trade rows")
            if not row["trdMatchID"] or not row["price"] or not row["tickDirection"]:
                raise ValueError("missing trade identity / price / tick direction")
            if last is None or timestamp != last:
                last_ids.clear()
            if row["trdMatchID"] in last_ids:
                raise ValueError("duplicate trade identity within timestamp")
            last_ids.add(row["trdMatchID"])
            first = timestamp if first is None else first
            last = timestamp
            count += 1
    if count <= 0:
        raise ValueError("zero native trade rows")
    return {"rows": count, "first_timestamp": first, "last_timestamp": last}


def download_one(symbol: str, day: date, output: Path) -> dict:
    url = f"https://public.bybit.com/trading/{symbol}/{symbol}{day.isoformat()}.csv.gz"
    request = urllib.request.Request(
        url, headers={"User-Agent": "cripta-P90L90-research-v2/1"}
    )
    tmp = None
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            if response.status != 200:
                raise ValueError(f"archive status {response.status}")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DAILY_BYTES:
                raise ValueError("over-size source archive")
            fd, tmp = tempfile.mkstemp(prefix=".bybit_warmup_", suffix=".partial", dir=output.parent)
            n = 0
            with os.fdopen(fd, "wb") as dst:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    n += len(chunk)
                    if n > MAX_DAILY_BYTES:
                        raise ValueError("over-size stream")
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
        if n <= 0 or (content_length and n != int(content_length)):
            raise ValueError("incomplete remote transfer")
        with Path(tmp).open("rb") as source:
            if source.read(2) != bytes.fromhex("1f8b"):
                raise ValueError("gzip header mismatch")
        qc = validate_day(Path(tmp), symbol, day)
        os.replace(tmp, output)
        tmp = None
        return {"source": url, "sha256": sha256(output), "size": n, **qc}
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def acquire(symbol: str, run_root: Path, original_root: Path, entry_root: Path) -> dict:
    if symbol not in SYMBOLS:
        raise ValueError("outside owner-approved P90L90 symbols")
    root = run_root.resolve()
    if not root.is_absolute() or DATA_ROOT not in root.parents:
        raise ValueError("only approved Data research run")
    if os.geteuid() == 0:
        raise PermissionError("root is not the research writer")
    if not root.is_dir() or not os.access(root, os.W_OK | os.X_OK):
        raise PermissionError("pre-created run root not writable by actual actor")

    alias = symbol.removesuffix("USDT")
    manifest_path = entry_root / f"entry_{alias}_FULL90/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if not manifest["complete_90d_input"] or manifest["symbol"] != symbol:
        raise ValueError("original Entry lineage is not complete for recorded inputs")
    old_files = {z["file"]: z for z in manifest["inputs"]}
    if len(old_files) != 91:
        raise ValueError("original lineage must have 91 days including May17")

    folder = root / "inputs" / symbol / "public_trades"
    folder.mkdir(parents=True, exist_ok=False)
    records = []
    for day in dates(FIRST, END):
        filename = f"{symbol}{day.isoformat()}.csv.gz"
        path = folder / filename
        if day < WARMUP_LINK_START:
            record = download_one(symbol, day, path)
            record["kind"] = "NEW_WARMUP_SOURCE"
        else:
            old = original_root / symbol / "public_trades" / filename
            ref = old_files.get(filename)
            if ref is None or not old.is_file() or old.stat().st_size <= 0:
                raise FileNotFoundError("original daily input missing/empty")
            current = sha256(old)
            if current != ref["sha256"]:
                raise ValueError("previous Entry raw source SHA drift")
            path.symlink_to(old.resolve())
            record = {
                "kind": "VERIFIED_READ_ONLY_EXISTING",
                "original": str(old.resolve()),
                "sha256": current,
                "size": old.stat().st_size,
                "rows": ref["rows"],
            }
        records.append({"date": day.isoformat(), "file": filename, **record})
        print(json.dumps({"stage": "DAY_VERIFIED", "symbol": symbol,
                          "date": day.isoformat(), "kind": record["kind"]}), flush=True)

    if len(records) != 100:
        raise ValueError("expected 10 warmup + 90 evaluated UTC days")
    if sum(r["kind"] == "NEW_WARMUP_SOURCE" for r in records) != 9:
        raise ValueError("expected 9 newly downloaded daily archives")
    report = {
        "dataset_version": "P90L90_90D_WARMUP_V2",
        "symbol": symbol,
        "source": "Bybit public LINEAR native trades",
        "evaluated_window": ["2026-05-18T00:00:00Z", "2026-08-16T00:00:00Z"],
        "warmup_window": ["2026-05-08T00:00:00Z", "2026-05-18T00:00:00Z"],
        "status": "INPUT_QC_PASS_NOT_YET_GEOMETRY_READY",
        "legacy_entry_events_sha256": manifest["events_sha256"],
        "input_rows_counted_warmup": sum(r["rows"] for r in records if r["kind"] == "NEW_WARMUP_SOURCE"),
        "files": records,
    }
    target = root / "inputs" / f"manifest_{symbol}.json"
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return {"status": report["status"], "symbol": symbol, "files": len(records),
            "downloaded": 9, "input_manifest_sha256": sha256(target)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True, choices=SYMBOLS)
    p.add_argument("--run-root", required=True, type=Path)
    p.add_argument("--old-raw-root", type=Path, default=OLD_RAW)
    p.add_argument("--old-entry-root", type=Path, default=OLD_ENTRY)
    args = p.parse_args()
    print(json.dumps(acquire(args.symbol, args.run_root,
                             args.old_raw_root, args.old_entry_root)), flush=True)


if __name__ == "__main__":
    main()
