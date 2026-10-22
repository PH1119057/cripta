#!/usr/bin/env python3
"""CRIPTA research-only 4-factor physical cube; source event-time, no execution.

SERVER-SIDE WRITE CONTRACT: effective actor cripta, UID from pwd.
READ: /data/cripta/datasets/raw/20260518_20260816/*/public_trades/*.csv.gz,
  /srv/cripta/source_checkout/research/microstructure/TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json,
  current script.
CREATE/WRITE/RENAME: only own new run directory in /data/cripta/research/runs.
No existing-file overwrite, no DELETE, no DB/Exchange access, no systemd sandbox.
Output receipt clock remains UNKNOWN, so RESEARCH-READY is not implied.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import pwd
import shutil
import statistics
import sys
import time
from collections import deque
from decimal import Decimal
from pathlib import Path

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = dt.date(2026, 5, 18)
RAW = Path("/data/cripta/datasets/raw/20260518_20260816")
REPO = Path("/srv/cripta/source_checkout")
SOURCE_MANIFEST = (
    REPO / "research/microstructure/TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
)
OUT_PARENT = Path("/data/cripta/research/runs")
OUT_ROOT = OUT_PARENT / "microstructure_joint_4f_20261023"
FORMULA = "trade_4factor_continuous_prior60_v2"
KEYS = (
    "factor_4_large_trade_share",
    "factor_8_max_timestamp_side_group_streak",
    "factor_27_median_positive_gap_ms",
    "factor_28_minute_fill_notional_p90_usdt",
    "factor_2_trade_rate",
    "factor_5_delta_usdt",
)


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def percentile(values, q):
    return values[int(q * (len(values) - 1))]


def minute_state():
    return {
        "amounts": [],
        "gaps": [],
        "fill_count": 0,
        "buy": 0.0,
        "sell": 0.0,
        "last_ts": None,
        "last_key": None,
        "group_count": 0,
        "run_side": None,
        "run": 0,
        "max_run": 0,
        "last_price": None,
        "ties": 0,
    }


def read_day(symbol, day, path):
    """Return 1440 minute states; event-time decimal flooring is exact."""
    start = Decimal(int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()))
    states = [minute_state() for _ in range(1440)]
    prev = None
    total = 0
    with gzip.open(path, "rt", newline="") as stream:
        for row in csv.DictReader(stream):
            stamp = Decimal(row["timestamp"])
            if prev is not None and stamp < prev:
                raise ValueError("RAW_TIMESTAMP_NONMONOTONE")
            prev = stamp
            minute = int((stamp - start) // 60)
            if not 0 <= minute < 1440:
                raise ValueError("RAW_MINUTE_OUTSIDE_DAY")
            if row["symbol"] != symbol or row["side"] not in ("Buy", "Sell"):
                raise ValueError("RAW_SYMBOL_OR_SIDE")
            value = float(row["foreignNotional"])
            price = float(row["price"])
            if not 0 <= value < float("inf") or not 0 < price < float("inf"):
                raise ValueError("NONFINITE_OR_NEGATIVE_RAW")
            m = states[minute]
            m["fill_count"] += 1
            m["amounts"].append(value)
            m["last_price"] = price
            if row["side"] == "Buy":
                m["buy"] += value
            else:
                m["sell"] += value
            if m["last_ts"] is not None:
                gap = (stamp - m["last_ts"]) * 1000
                if gap > 0:
                    m["gaps"].append(float(gap))
                elif gap == 0:
                    m["ties"] += 1
                else:
                    raise ValueError("MINUTE_TIMESTAMP_NONMONOTONE")
            m["last_ts"] = stamp
            key = (stamp, row["side"])
            if key != m["last_key"]:
                m["group_count"] += 1
                m["run"] = m["run"] + 1 if m["run_side"] == row["side"] else 1
                m["max_run"] = max(m["run"], m["max_run"])
                m["run_side"] = row["side"]
                m["last_key"] = key
            total += 1
    return states, total


def factors_for_minute(state, previous):
    amounts = state["amounts"]
    n = state["fill_count"]
    large = None
    lookback_n = sum(len(bucket) for bucket in previous) if len(previous) == 60 else 0
    if len(previous) == 60 and amounts:
        historical = sorted(v for bucket in previous for v in bucket)
        if lookback_n >= 100 and sum(amounts) > 0:
            cutoff = percentile(historical, 0.99)
            large = sum(v for v in amounts if v >= cutoff) / sum(amounts)
    if n:
        p90 = percentile(sorted(amounts), 0.9)
        gap = statistics.median(state["gaps"]) if state["gaps"] else None
    else:
        p90 = None
        gap = None
    return {
        KEYS[0]: large,
        KEYS[1]: state["max_run"] if n else None,
        KEYS[2]: gap,
        KEYS[3]: p90,
        KEYS[4]: n if n else None,
        KEYS[5]: state["buy"] - state["sell"] if n else None,
        "prior60_n": lookback_n,
        "input_fill_count": n,
        "timestamp_side_groups": state["group_count"],
        "tied_adjacent_fill_timestamps": state["ties"],
        "price_close": state["last_price"],
        "quality": "OBSERVED_EXCHANGE_TAPE" if n else "NO_DATA",
    }


def write_json(path, value):
    tmp = path.with_name(path.name + ".part")
    with tmp.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def run(symbol, days):
    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("INVALID_SCOPE")
    if not os.access(OUT_PARENT, os.W_OK | os.X_OK):
        raise PermissionError("NO_ACTOR_WRITE_TO_RESEARCH_RUNS")
    manifest = json.loads(SOURCE_MANIFEST.read_text())
    sources = {(s["symbol"], s["date_utc"]): s for s in manifest["source_files"]}
    selected = [
        (
            START + dt.timedelta(days=i),
            RAW / symbol / "public_trades"
            / (symbol + (START + dt.timedelta(days=i)).isoformat() + ".csv.gz"),
        )
        for i in range(days)
    ]
    for day, path in selected:
        if (symbol, day.isoformat()) not in sources or not path.is_file():
            raise FileNotFoundError("MISSING_MANIFEST_OR_ARCHIVE " + day.isoformat())
    os.umask(0o007)
    OUT_ROOT.mkdir(mode=0o2770, exist_ok=True)
    parent = OUT_ROOT / ("continuous_" + str(days) + "d_v2")
    parent.mkdir(mode=0o2770, exist_ok=True)
    target = parent / (symbol + "_" + str(days) + "d_v2")
    target.mkdir(mode=0o2770, exist_ok=False)
    code = Path(__file__).resolve()
    snapshot = target / "source_snapshot.py"
    shutil.copyfile(code, snapshot)
    command = [
        "sudo", "-u", "cripta", "python3", "-B", str(code),
        "--symbol", symbol, "--days", str(days),
    ]
    write_json(target / "run_manifest.json", {
        "status": "RUNNING",
        "actor_uid": os.geteuid(),
        "symbol": symbol,
        "days": days,
        "source_commit": (REPO / ".git/refs/heads/main").read_text().strip(),
        "code_sha256": sha256_file(code),
        "source_snapshot_sha256": sha256_file(snapshot),
        "source_manifest_sha256": sha256_file(SOURCE_MANIFEST),
        "command": command,
        "source_arrival_clock": "UNKNOWN",
        "verified_available_after_utc": None,
        "data_source": "BYBIT_PUBLIC_TRADES_ARCHIVE",
        "formula_version": FORMULA,
    })
    outfile = target / "features.jsonl.gz"
    partial = target / "features.jsonl.gz.part"
    history = deque(maxlen=60)
    day_inputs = []
    total_rows = 0
    total_fills = 0
    nulls = {k: 0 for k in KEYS}
    start_time = time.monotonic()
    with partial.open("xb") as raw_out:
        with gzip.GzipFile(fileobj=raw_out, mode="wb", mtime=0) as gz:
            with io.TextIOWrapper(gz, encoding="utf-8", newline="") as stream:
                for offset, (day, path) in enumerate(selected):
                    file_sha = sha256_file(path)
                    if file_sha != sources[(symbol, day.isoformat())]["compressed_sha256"]:
                        raise ValueError("SOURCE_SHA_DRIFT " + day.isoformat())
                    states, fills = read_day(symbol, day, path)
                    total_fills += fills
                    day_inputs.append({
                        "day": day.isoformat(), "source_sha256": file_sha, "fills": fills,
                    })
                    start_utc = dt.datetime.combine(day, dt.time(), dt.UTC)
                    for minute, state in enumerate(states):
                        row = factors_for_minute(state, history)
                        stamp = start_utc + dt.timedelta(minutes=minute)
                        row.update({
                            "venue_id": "Bybit",
                            "market_source_id": "BYBIT_PUBLIC_TRADES_ARCHIVE",
                            "instrument_id": symbol,
                            "indicator_ids": [4, 8, 27, 28, 2, 5],
                            "formula_version": FORMULA,
                            "minute_index": offset * 1440 + minute,
                            "window_start_utc": stamp.isoformat(),
                            "derived_window_end_utc": (
                                stamp + dt.timedelta(minutes=1)
                            ).isoformat(),
                            "observed_at_utc": None,
                            "verified_available_after_utc": None,
                            "source_archive_sha256": file_sha,
                        })
                        if row["input_fill_count"] != len(state["amounts"]):
                            raise ValueError("FILL_COUNT_MISMATCH")
                        if (offset * 1440 + minute) < 60 and row[KEYS[0]] is not None:
                            raise ValueError("PRIOR60_LOOKAHEAD")
                        for key in KEYS:
                            nulls[key] += int(row[key] is None)
                        stream.write(json.dumps(row, separators=(",", ":")) + "\n")
                        history.append(state["amounts"])
                        total_rows += 1
                    elapsed = time.monotonic() - start_time
                    done = offset + 1
                    print(json.dumps({
                        "stage": "MONTH30_PHYSICAL_4FACTOR",
                        "symbol": symbol, "processed_days": done, "total_days": days,
                        "percent": round(100 * done / days, 2),
                        "elapsed_seconds": round(elapsed, 2),
                        "eta_seconds": round(elapsed * (days - done) / done, 2),
                        "heartbeat": True, "cache": "MISS_RAW_ARCHIVE",
                    }), file=sys.stderr, flush=True)
    if total_rows != 1440 * days:
        raise ValueError("OUTPUT_MINUTE_CARDINALITY")
    partial.replace(outfile)
    result = {
        "status": "PHYSICAL_4FACTOR_COMPLETED_NOT_INDEPENDENT_QC_NOT_RESEARCH_READY",
        "symbol": symbol, "days": days,
        "range_utc": [START.isoformat(), selected[-1][0].isoformat()],
        "minute_rows": total_rows, "raw_fill_count": total_fills,
        "source_days": day_inputs,
        "null_counts": nulls, "features_sha256": sha256_file(outfile),
        "code_sha256": sha256_file(snapshot),
        "source_manifest_sha256": sha256_file(SOURCE_MANIFEST),
        "feature_file": str(outfile), "receipt_clock": "UNKNOWN",
        "utc_midnight_previous60_continuity": "ENFORCED",
        "limitations": [
            "Exchange event-time feature end is not verified real receiver availability",
            "Timestamp-side groups do not reconstruct parent orders",
            "No trade execution, fees, OOS or Strategy inference",
        ],
    }
    write_json(target / "result.json", result)
    state = json.loads((target / "run_manifest.json").read_text())
    write_json(target / "run_final.json", {
        **state, "status": "PHYSICAL_COMPLETE",
        "result_sha256": sha256_file(target / "result.json"),
        "output_sha256": sha256_file(outfile),
    })
    print(json.dumps({
        "status": "PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC",
        "symbol": symbol, "days": days, "minute_rows": total_rows,
        "raw_fill_count": total_fills, "output_sha256": result["features_sha256"],
        "run_dir": str(target),
        "elapsed_seconds": round(time.monotonic() - start_time, 2),
    }))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=SYMS, required=True)
    p.add_argument("--days", type=int, choices=(2, 30), required=True)
    args = p.parse_args()
    run(args.symbol, args.days)
