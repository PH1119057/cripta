#!/usr/bin/env python3
"""First-month research #19 range-compression and #22 VWAP/POC. No trading.

Exact v1 formula lineage: range_vwap_profile_v1.py, with #19 20 completed
minute closes vs preceding 20, now *continuous across UTC midnight*.
#22 running base-size VWAP and exact-price base-size POC reset each UTC day.
Research event-time minute end is a derived cutoff, NOT proved receiver time.
Filesystem write contract: only new child under
/data/cripta/research/runs/microstructure_firstmonth_price19_22_20261023.
Writer cripta; immutable input /data/cripta/datasets/raw/20260518_20260816;
read GitHub published source mirror and frozen trade manifest; no DB/systemd.
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
from collections import defaultdict, deque
from decimal import Decimal

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
REPO = pathlib.Path("/srv/cripta/source_checkout")
FROZEN = REPO / "research/microstructure/TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
ROOT = pathlib.Path("/data/cripta/research/runs/microstructure_firstmonth_price19_22_20261023")
START = dt.date(2026, 5, 18)
VERSION = "range20ratio_and_utc_daily_base_volume_vwap_poc_v2"


def hashfile(p):
    h = hashlib.sha256()
    with p.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def newjson(path, payload):
    tmp = path.with_name(path.name + ".part")
    with tmp.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
    tmp.rename(path)


def reconstruct_day(symbol, day, source):
    """Return completed-minute source state, not future price outcomes."""
    start = Decimal(int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()))
    counts = [0] * 1440
    closes = [None] * 1440
    volumes = [Decimal(0) for _ in range(1440)]
    notionals = [Decimal(0) for _ in range(1440)]
    profiles = [defaultdict(Decimal) for _ in range(1440)]
    previous = None
    fills = 0
    with gzip.open(source, "rt", newline="") as f:
        for r in csv.DictReader(f):
            stamp = Decimal(r["timestamp"])
            if previous is not None and stamp < previous:
                raise ValueError("NONMONOTONIC_SOURCE_TIME")
            previous = stamp
            idx = int((stamp - start) // 60)
            if not 0 <= idx < 1440:
                raise ValueError("SOURCE_OUTSIDE_DAY")
            if r["symbol"] != symbol:
                raise ValueError("WRONG_INSTRUMENT")
            price, qty = Decimal(r["price"]), Decimal(r["size"])
            notional = Decimal(r["foreignNotional"])
            if not (price.is_finite() and qty.is_finite() and notional.is_finite()):
                raise ValueError("NONFINITE_TRADE")
            if price <= 0 or qty <= 0 or notional < 0:
                raise ValueError("INVALID_TRADE")
            if abs(price * qty - notional) > Decimal("0.000001"):
                raise ValueError("SIZE_NOTIONAL_PARITY")
            counts[idx] += 1
            closes[idx] = price
            volumes[idx] += qty
            notionals[idx] += price * qty
            profiles[idx][price] += qty
            fills += 1
    return counts, closes, volumes, notionals, profiles, fills


def build_day(symbol, day, source, history):
    counts, closes, volumes, notionals, profiles, fills = reconstruct_day(
        symbol, day, source
    )
    cumulative_qty = Decimal(0)
    cumulative_cost = Decimal(0)
    cumulative_profile = defaultdict(Decimal)
    rows = []
    for i in range(1440):
        history.append(closes[i])
        old_range = new_range = ratio = None
        if len(history) == 40 and all(p is not None for p in history):
            ordered = list(history)
            old_range = max(ordered[:20]) - min(ordered[:20])
            new_range = max(ordered[20:]) - min(ordered[20:])
            if old_range > 0:
                ratio = new_range / old_range
        cumulative_qty += volumes[i]
        cumulative_cost += notionals[i]
        for price, qty in profiles[i].items():
            cumulative_profile[price] += qty
        vwap = cumulative_cost / cumulative_qty if cumulative_qty else None
        poc = min(
            cumulative_profile,
            key=lambda p: (-cumulative_profile[p], p),
        ) if cumulative_profile else None
        stamp = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(minutes=i)
        rows.append({
            "instrument_id": symbol,
            "venue_id": "Bybit",
            "market_source_id": "BYBIT_PUBLIC_TRADES_ARCHIVE",
            "formula_version": VERSION,
            "indicator_ids": [19, 22],
            "window_start_utc": stamp.isoformat(),
            "derived_window_end_utc": (stamp + dt.timedelta(minutes=1)).isoformat(),
            "observed_at_utc": None,
            "verified_available_after_utc": None,
            "quality": "OBSERVED_EXCHANGE_TAPE" if counts[i] else "NO_DATA",
            "input_fill_count": counts[i],
            "factor_19_range_compression_ratio": str(ratio) if ratio is not None else None,
            "factor_19_previous20_close_range_quote": (
                str(old_range) if old_range is not None else None
            ),
            "factor_19_current20_close_range_quote": (
                str(new_range) if new_range is not None else None
            ),
            "factor_22_running_vwap_quote_per_base": str(vwap) if vwap is not None else None,
            "factor_22_running_poc_exact_price": str(poc) if poc is not None else None,
            "factor_22_daily_running_base_volume": str(cumulative_qty),
            "price_close": str(closes[i]) if closes[i] is not None else None,
        })
    return rows, fills


def run(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("REQUIRES_REAL_CRIPTA_ACTOR")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("UNAPPROVED_SCOPE")
    if not os.access(ROOT.parent, os.W_OK | os.X_OK):
        raise PermissionError("NO_RESEARCH_WRITE_ACCESS")
    manifest = json.loads(FROZEN.read_text())
    manifest_rows = {
        (x["symbol"], x["date_utc"]): x
        for x in manifest["source_files"]
    }
    src = []
    for i in range(days):
        day = START + dt.timedelta(days=i)
        path = RAW / symbol / "public_trades" / (symbol + day.isoformat() + ".csv.gz")
        if (symbol, day.isoformat()) not in manifest_rows or not path.is_file():
            raise FileNotFoundError("MISSING_FROZEN_SOURCE " + day.isoformat())
        src.append((day, path))
    dest = ROOT / ("physical_" + str(days) + "d") / symbol
    if dest.exists():
        raise FileExistsError("NO_RESEARCH_RESULT_OVERWRITE")
    os.umask(0o007)
    ROOT.mkdir(mode=0o2770, exist_ok=True)
    dest.parent.mkdir(mode=0o2770, exist_ok=True)
    dest.mkdir(mode=0o2770, exist_ok=False)
    (dest / "source_snapshot").mkdir(mode=0o2770, exist_ok=False)
    code = pathlib.Path(__file__).resolve()
    shutil.copyfile(code, dest / "source_snapshot" / "materializer.py")
    codehash = hashfile(code)
    source_commit = (REPO / ".git/refs/heads/main").read_text().strip()
    (dest / "source_commit.txt").write_text(source_commit + "\n")
    (dest / "source_sha256.txt").write_text(codehash + "\n")
    (dest / "command.txt").write_text(
        "python3 -B " + str(code) + " --symbol " + symbol
        + " --days " + str(days) + "\n"
    )
    (dest / "environment.txt").write_text(
        "uid=" + str(os.geteuid()) + "\npython=" + sys.executable + "\n"
    )
    expected = [
        {"day": day.isoformat(), "path": str(path),
         "sha256": manifest_rows[(symbol, day.isoformat())]["compressed_sha256"]}
        for day, path in src
    ]
    newjson(dest / "input_provenance.json", {
        "source_manifest_sha256": hashfile(FROZEN), "files": expected
    })
    newjson(dest / "run_manifest.json", {
        "status": "RUNNING", "scope": [symbol, days], "source_commit": source_commit,
        "code_sha256": codehash, "writer_uid": os.geteuid(),
        "next_minute_receiver_availability": "UNKNOWN",
    })
    output = dest / "features.jsonl.gz"
    tmp = dest / "features.jsonl.gz.part"
    history = deque(maxlen=40)
    sources = []
    total = 0
    started = time.monotonic()
    with tmp.open("xb") as rawout:
        with gzip.GzipFile(fileobj=rawout, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8") as stream:
                for n, (day, path) in enumerate(src, start=1):
                    realsha = hashfile(path)
                    if realsha != expected[n-1]["sha256"]:
                        raise ValueError("RAW_SHA_DRIFT")
                    rows, fills = build_day(symbol, day, path, history)
                    sources.append({"day": day.isoformat(), "fills": fills, "sha256": realsha})
                    for row in rows:
                        row["source_archive_sha256"] = realsha
                        stream.write(json.dumps(row, sort_keys=True) + "\n")
                        total += 1
                    elapsed = time.monotonic() - started
                    print(json.dumps({"symbol": symbol, "finished_day": n, "days": days,
                                      "elapsed_s": round(elapsed, 2), "heartbeat": True}),
                          file=sys.stderr, flush=True)
    if total != days * 1440:
        raise ValueError("CARDINALITY_MISMATCH")
    tmp.rename(output)
    result = {
        "status": "FIRST_MONTH_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC",
        "symbols": [symbol], "days": days, "feature_ids": [19, 22],
        "minute_rows": total, "raw_fills": sum(x["fills"] for x in sources),
        "source_days": sources, "source_manifest_sha256": hashfile(FROZEN),
        "file_sha256": hashfile(output), "program_sha256": codehash,
        "last_day": src[-1][0].isoformat(), "receipt_time": "UNKNOWN",
        "limitations": ["No receiver-clock capture, therefore no RESEARCH-READY",
                        "Rolling prior40 close requires all 40 actual minute prints",
                        "VWAP/POC reset at UTC day; exact price/size source identity"],
    }
    newjson(dest / "result.json", result)
    print(json.dumps({"status": result["status"], "symbol": symbol, "days": days,
                      "rows": total, "fills": result["raw_fills"],
                      "sha256": result["file_sha256"], "run_dir": str(dest)}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True, choices=SYMS)
    p.add_argument("--days", required=True, type=int, choices=(2, 30))
    a = p.parse_args()
    run(a.symbol, a.days)
