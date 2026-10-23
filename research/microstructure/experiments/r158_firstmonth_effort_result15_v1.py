#!/usr/bin/env python3
"""First-month #15 Effort/Result physical *proxy*, no trading or future labels.

Definition is frozen from effort_result_minute_v2.py:
abs(last_trade_price / first_trade_price - 1) * 10000 /
(sum(foreignNotional) / 1000000) for the same UTC minute.
No fabricated receiver clock, no cross-coin normalization or marginal impact.
Writer cripta, raw archives read-only, new isolated data-disk directory only.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import pathlib
import shutil
import sys
import time
from decimal import Decimal

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
ROOT = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
REPO = pathlib.Path("/srv/cripta/source_checkout")
OUT = pathlib.Path(
    "/data/cripta/research/runs/r158_firstmonth_effort_result15_20261023"
)
MANIFEST = (
    REPO / "research/microstructure/"
    "TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
)
FORMULA = "effort_result_intraminute_endpoints_quote_million_v1"
PHYSICAL = "PHYSICAL_15_COMPLETE_PENDING_INDEPENDENT_QC"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def create_json(path, obj):
    part = path.with_name(path.name + ".part")
    with part.open("x", encoding="utf-8") as f:
        json.dump(obj, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    part.rename(path)


def terminal(path, status, *, result=None, error=None):
    if status not in (PHYSICAL, "FAILED"):
        raise ValueError("UNDECLARED_TERMINAL_STATUS")
    current = json.loads(path.read_text())
    if current["status"] != "RUNNING":
        raise ValueError("TERMINAL_ALREADY_SET")
    if status == PHYSICAL and result is None:
        raise ValueError("MISSING_SUCCESS_EVIDENCE")
    if status == "FAILED" and error is None:
        raise ValueError("MISSING_FAILURE_EVIDENCE")
    updated = dict(current)
    updated["status"] = status
    if result is not None:
        updated["output_sha256"] = result["output_sha256"]
        updated["rows"] = result["rows"]
        updated["independent_qc"] = "PENDING"
    if error is not None:
        updated["error_type"] = type(error).__name__
        updated["error_message"] = str(error)[:300]
    part = path.with_name(path.name + ".terminal.part")
    with part.open("x", encoding="utf-8") as f:
        json.dump(updated, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, path)


def source_days(symbol, days):
    manifest = json.loads(MANIFEST.read_text())
    items = [
        r for r in manifest["source_files"] if r["symbol"] == symbol
    ]
    known = {r["date_utc"]: r for r in items}
    if len(known) != 30 or len(items) != 30:
        raise ValueError("BAD_30DAY_SOURCE_MANIFEST")
    files = []
    for i in range(days):
        day = FIRST + dt.timedelta(days=i)
        p = ROOT / symbol / "public_trades" / (
            symbol + day.isoformat() + ".csv.gz"
        )
        if not p.is_file() or not os.access(p, os.R_OK):
            raise FileNotFoundError("RAW_SOURCE_MISSING " + str(p))
        files.append((day, p, known[day.isoformat()]))
    return files


def minute_rows(symbol, day, path):
    start = Decimal(int(dt.datetime.combine(
        day, dt.time(), dt.UTC
    ).timestamp()))
    first = [None] * 1440
    last = [None] * 1440
    quote = [Decimal(0) for _ in range(1440)]
    count = [0] * 1440
    preceding = None
    with gzip.open(path, "rt", newline="") as f:
        for r in csv.DictReader(f):
            timestamp = Decimal(r["timestamp"])
            if not timestamp.is_finite():
                raise ValueError("NONFINITE_TRADE_TIMESTAMP")
            if preceding is not None and timestamp < preceding:
                raise ValueError("NONMONOTONE_PUBLIC_TRADES")
            preceding = timestamp
            idx = int((timestamp - start) // 60)
            if not 0 <= idx < 1440:
                raise ValueError("PUBLIC_TRADE_OUT_OF_UTC_DAY")
            price = Decimal(r["price"])
            notional = Decimal(r["foreignNotional"])
            if (
                not price.is_finite() or price <= 0 or
                not notional.is_finite() or notional < 0
            ):
                raise ValueError("BAD_PRICE_OR_NOTIONAL")
            if first[idx] is None:
                first[idx] = price
            last[idx] = price
            quote[idx] += notional
            count[idx] += 1
    rows = []
    for idx in range(1440):
        beginning = dt.datetime.combine(
            day, dt.time(), dt.UTC
        ) + dt.timedelta(minutes=idx)
        if count[idx] == 0:
            value, movement, quality = None, None, "NO_PRINTS_IN_MINUTE"
        elif quote[idx] == 0:
            movement = abs(last[idx] / first[idx] - 1) * Decimal(10000)
            value, quality = None, "ZERO_QUOTE_NOTIONAL"
        else:
            movement = abs(last[idx] / first[idx] - 1) * Decimal(10000)
            value = movement / (quote[idx] / Decimal(1000000))
            quality = "NUMERIC_PHYSICAL_SOURCE_CONTINUITY_UNKNOWN"
        rows.append({
            "instrument_id": symbol,
            "venue_id": "Bybit",
            "indicator_id": 15,
            "formula_version": FORMULA,
            "window_start_utc": beginning.isoformat(),
            "derived_window_end_utc": (
                beginning + dt.timedelta(minutes=1)
            ).isoformat(),
            "observed_at_utc": None,
            "verified_available_after_utc": None,
            "first_trade_price": str(first[idx]) if first[idx] is not None else None,
            "last_trade_price": str(last[idx]) if last[idx] is not None else None,
            "quote_turnover_usdt": str(quote[idx]),
            "fill_count": count[idx],
            "abs_intraminute_endpoint_bps": (
                str(movement) if movement is not None else None
            ),
            "effort_result_bps_per_million_usdt": (
                str(value) if value is not None else None
            ),
            "quality": quality,
        })
    return rows, sum(count)


def run(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("UNAUTHORIZED_SCOPE")
    if not os.access(OUT.parent, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_PARENT_NOT_WRITABLE")
    sources = source_days(symbol, days)
    dest = OUT / ("physical_" + str(days) + "d") / symbol
    if dest.exists():
        raise FileExistsError("RESEARCH_RESULT_ALREADY_EXISTS_DO_NOT_OVERWRITE")
    os.umask(0o007)
    OUT.mkdir(mode=0o2770, exist_ok=True)
    dest.parent.mkdir(mode=0o2770, exist_ok=True)
    dest.mkdir(mode=0o2770, exist_ok=False)
    (dest / "source_snapshot").mkdir(mode=0o2770)
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, dest / "source_snapshot/materializer.py")
    codehash = sha(script)
    sourcecommit = (REPO / ".git/refs/heads/main").read_text().strip()
    (dest / "source_commit.txt").write_text(sourcecommit + "\n")
    (dest / "source_sha256.txt").write_text(codehash + "\n")
    (dest / "command.txt").write_text(
        f"python3 -B {script} --symbol {symbol} --days {days}\n"
    )
    (dest / "environment.txt").write_text(
        f"uid={os.geteuid()}\npython={sys.executable}\n"
    )
    manifest = dest / "run_manifest.json"
    create_json(manifest, {
        "status": "RUNNING", "symbol": symbol, "days": days,
        "actor": "cripta", "source_commit": sourcecommit,
        "program_sha256": codehash, "receiver_clock": "UNKNOWN",
    })
    try:
        output = dest / "features.jsonl.gz"
        part = dest / "features.jsonl.gz.part"
        evidence = []
        seen = 0
        fills = 0
        t0 = time.monotonic()
        with part.open("xb") as file:
            with gzip.GzipFile(fileobj=file, mode="wb", mtime=0) as gz:
                with io.TextIOWrapper(gz, encoding="utf-8") as writer:
                    for i, (day, path, expected) in enumerate(sources, 1):
                        inputhash = sha(path)
                        if (
                            inputhash != expected["compressed_sha256"] or
                            path.stat().st_size != expected["compressed_bytes"]
                        ):
                            raise ValueError("RAW_MANIFEST_SHA_OR_SIZE_DRIFT")
                        rows, n = minute_rows(symbol, day, path)
                        for row in rows:
                            row["source_archive_sha256"] = inputhash
                            writer.write(json.dumps(row, sort_keys=True) + "\n")
                            seen += 1
                        fills += n
                        evidence.append({
                            "date_utc": day.isoformat(),
                            "source_path": str(path),
                            "source_sha256": inputhash,
                            "source_bytes": path.stat().st_size,
                            "fills": n,
                            "minute_rows": len(rows),
                        })
                        print(json.dumps({
                            "stage": "EFFORT_RESULT_15_PHYSICAL",
                            "symbol": symbol, "days_done": i,
                            "days_total": days,
                            "elapsed_s": round(time.monotonic() - t0, 2),
                            "heartbeat": True,
                        }), flush=True)
        if seen != days * 1440:
            raise ValueError("TOTAL_MINUTE_COUNT_MISMATCH")
        part.rename(output)
        create_json(dest / "input_provenance.json", {"days": evidence})
        result = {
            "status": PHYSICAL, "symbol": symbol, "days": days,
            "rows": seen, "fills": fills,
            "indicator_ids": [15], "formula_version": FORMULA,
            "source_commit": sourcecommit, "program_sha256": codehash,
            "source_manifest_sha256": sha(MANIFEST),
            "output_sha256": sha(output),
            "source_continuity_verified": False,
            "receiver_clock_verified": False,
            "quality": "PHYSICAL_PROXY_NOT_RESEARCH_READY",
            "limitations": [
                "Endpoint price change, not intraminute high-low range",
                "Millions-of-USDT scaling amplifies low-volume observations",
                "Not measured order marginal price impact",
                "Availability/receiver clock unknown; no future labels",
            ],
        }
        create_json(dest / "result.json", result)
        terminal(manifest, PHYSICAL, result=result)
        print(json.dumps({
            "symbol": symbol, "days": days, "rows": seen,
            "output_sha256": result["output_sha256"], "output_dir": str(dest),
            "status": PHYSICAL,
        }), flush=True)
    except Exception as error:
        if not (dest / "result.json").exists():
            try:
                terminal(manifest, "FAILED", error=error)
            except Exception as secondary:
                print(
                    "TERMINAL_WRITE_FAILED: " + str(secondary),
                    file=sys.stderr,
                )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, choices=SYMS)
    parser.add_argument("--days", required=True, type=int, choices=(2, 30))
    args = parser.parse_args()
    run(args.symbol, args.days)
