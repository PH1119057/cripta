#!/usr/bin/env python3
"""Bybit first-month minute-end book #9/#11/#29, research-only physical facts.

Reconstructs orderbook from Bybit ob200 snapshot/delta ZIP for one day at a
time. One exact UTC minute row; only its LAST valid event inside minute is
eligible, no carry-forward into empty minutes. No inference about cancellations
or actual order executions; source receipt clock unavailable. Prior snapshots
not silently carried across archive/day boundaries.

Read: immutable /data/cripta/datasets/raw/20260518_20260816/*/orderbook.
Write: only new /data/cripta/research/runs/microstructure_firstmonth_book9_11_29_20261023
under cripta UID. No Git, DB, Exchange, systemd, or LIVE mutations.
"""
import argparse
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
import zipfile
from collections import Counter

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
ROOT = pathlib.Path("/data/cripta/research/runs/microstructure_firstmonth_book9_11_29_20261023")
REPO = pathlib.Path("/srv/cripta/source_checkout")
FIRST = dt.date(2026, 5, 18)
VERSION = "book_ob200_minute_last_observed_v2"
DISTANCE = 0.0005


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1048576), b""):
            h.update(part)
    return h.hexdigest()


def json_new(path, obj):
    tmp = path.with_name(path.name + ".part")
    with tmp.open("x", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.rename(path)


def metric(bids, asks, ts, minute_end_ms):
    if not bids or not asks:
        raise ValueError("BOOK_SIDE_EMPTY")
    best_bid = max(bids)
    best_ask = min(asks)
    if best_bid >= best_ask:
        raise ValueError("BOOK_CROSSED_OR_LOCKED")
    mid = (best_bid + best_ask) / 2
    bid_n = sum(
        p * q for p, q in bids.items()
        if mid * (1 - DISTANCE) <= p <= mid
    )
    ask_n = sum(
        p * q for p, q in asks.items()
        if mid <= p <= mid * (1 + DISTANCE)
    )
    return {
        "factor_9_near_mid_queue_imbalance": (
            (bid_n - ask_n) / (bid_n + ask_n) if bid_n + ask_n > 0 else None
        ),
        "factor_11_bid_depth_quote_5bps": bid_n,
        "factor_11_ask_depth_quote_5bps": ask_n,
        "factor_29_spread_bps": (best_ask - best_bid) / mid * 10000,
        "best_bid_quote": best_bid,
        "best_ask_quote": best_ask,
        "latest_book_event_ts_ms": ts,
        "book_age_at_minute_end_ms": minute_end_ms - ts,
    }


def book_day(symbol, date, source):
    start = int(dt.datetime.combine(date, dt.time(), dt.UTC).timestamp() * 1000)
    book_bid, book_ask = {}, {}
    states = [None] * 1440
    previous_ts = previous_seq = previous_u = None
    previous_minute = None
    count = Counter()
    latest_ts = None
    has_snapshot = False

    def finalize(minute):
        if minute is None or not has_snapshot:
            return
        row = metric(
            book_bid, book_ask, latest_ts,
            start + (minute + 1) * 60000,
        )
        states[minute] = row

    with zipfile.ZipFile(source) as z:
        members = [name for name in z.namelist() if name.endswith(".data")]
        if len(members) != 1:
            raise ValueError("ORDERBOOK_ZIP_MEMBER_COUNT")
        with z.open(members[0]) as f:
            for raw in f:
                event = json.loads(raw)
                ts = int(event["ts"])
                if not start <= ts < start + 1440 * 60000:
                    raise ValueError("ORDERBOOK_EVENT_OUTSIDE_UTC_DAY")
                if previous_ts is not None and ts < previous_ts:
                    raise ValueError("ORDERBOOK_NONMONOTONIC_TS")
                previous_ts = ts
                minute = (ts - start) // 60000
                if minute != previous_minute:
                    finalize(previous_minute)
                    previous_minute = minute
                data = event["data"]
                if data.get("s") != symbol:
                    raise ValueError("BOOK_SOURCE_SYMBOL")
                seq, update = int(data["seq"]), int(data["u"])
                typ = event["type"]
                if typ == "snapshot":
                    book_bid = {
                        float(p): float(q) for p, q in data["b"] if float(q) > 0
                    }
                    book_ask = {
                        float(p): float(q) for p, q in data["a"] if float(q) > 0
                    }
                    previous_seq = previous_u = None
                    has_snapshot = True
                    count["snapshot"] += 1
                elif typ == "delta":
                    if not has_snapshot:
                        count["delta_before_snapshot"] += 1
                        continue
                    for levels, side in (
                        (data["b"], book_bid), (data["a"], book_ask)
                    ):
                        for price, qty in levels:
                            p, q = float(price), float(qty)
                            if q < 0:
                                raise ValueError("NEGATIVE_BOOK_QTY")
                            if q == 0:
                                side.pop(p, None)
                            else:
                                side[p] = q
                    count["delta"] += 1
                else:
                    raise ValueError("BOOK_MESSAGE_TYPE")
                if previous_seq is not None and seq <= previous_seq:
                    raise ValueError("BOOK_SEQ_NONMONOTONIC")
                if previous_u is not None:
                    if update <= previous_u:
                        raise ValueError("BOOK_UPDATE_ID_NONMONOTONIC")
                    if update != previous_u + 1:
                        count["update_id_gap_events"] += 1
                previous_seq, previous_u = seq, update
                latest_ts = ts
                count["processed"] += 1
        finalize(previous_minute)
    if not count["snapshot"]:
        raise ValueError("BOOK_NO_INITIAL_SNAPSHOT")
    return states, dict(count)


def run(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("BOOK_RESEARCH_WRITER_NOT_CRIPTA")
    if symbol not in COHORT or days not in (2, 30):
        raise ValueError("UNAPPROVED_BOOK_SCOPE")
    if not os.access(ROOT.parent, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_PARENT_READ_ONLY")
    inputs = [
        (
            FIRST + dt.timedelta(days=i),
            RAW / symbol / "orderbook" / (
                (FIRST + dt.timedelta(days=i)).isoformat()
                + "_" + symbol + "_ob200.data.zip"
            ),
        )
        for i in range(days)
    ]
    if any(not p.is_file() for _, p in inputs):
        raise FileNotFoundError("MISSING_ORDERBOOK_ZIP")
    dest = ROOT / ("physical_" + str(days) + "d") / symbol
    if dest.exists():
        raise FileExistsError("ORDERBOOK_OUTPUT_ALREADY_EXISTS")
    os.umask(0o007)
    ROOT.mkdir(mode=0o2770, exist_ok=True)
    dest.parent.mkdir(mode=0o2770, exist_ok=True)
    dest.mkdir(mode=0o2770, exist_ok=False)
    (dest / "source_snapshot").mkdir(mode=0o2770, exist_ok=False)
    program = pathlib.Path(__file__).resolve()
    shutil.copyfile(program, dest / "source_snapshot" / "book_materializer.py")
    commit = (REPO / ".git/refs/heads/main").read_text().strip()
    (dest / "source_commit.txt").write_text(commit + "\n")
    (dest / "source_sha256.txt").write_text(digest(program) + "\n")
    (dest / "command.txt").write_text(
        "python3 -B " + str(program) + " --symbol " + symbol
        + " --days " + str(days) + "\n"
    )
    (dest / "environment.txt").write_text(
        "uid=" + str(os.geteuid()) + "\npython=" + sys.executable + "\n"
    )
    provenance = []
    status = Counter()
    output = dest / "book_features.jsonl.gz"
    tmp = output.with_name(output.name + ".part")
    start_time = time.monotonic()
    with tmp.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
            with io.TextIOWrapper(gz, encoding="utf-8") as writer:
                for index, (date, path) in enumerate(inputs):
                    src_sha = digest(path)
                    states, stats = book_day(symbol, date, path)
                    for key, val in stats.items():
                        status[key] += val
                    provenance.append({
                        "day": date.isoformat(), "source_sha256": src_sha,
                        "path": str(path), "source_stats": stats,
                        "minute_states": sum(x is not None for x in states),
                    })
                    beginning = dt.datetime.combine(date, dt.time(), dt.UTC)
                    for minute, state in enumerate(states):
                        t = beginning + dt.timedelta(minutes=minute)
                        line = {
                            "venue_id": "Bybit",
                            "instrument_id": symbol,
                            "market_source_id": "BYBIT_OB200_ARCHIVE",
                            "formula_version": VERSION,
                            "indicator_ids": [9, 11, 29],
                            "window_start_utc": t.isoformat(),
                            "derived_window_end_utc": (
                                t + dt.timedelta(minutes=1)
                            ).isoformat(),
                            "observed_at_utc": None,
                            "verified_available_after_utc": None,
                            "source_sha256": src_sha,
                            "quality": (
                                "OBSERVED_BOOK_EVENT_MINUTE"
                                if state is not None else "NO_DATA"
                            ),
                            **(state or {}),
                        }
                        writer.write(json.dumps(line, sort_keys=True) + "\n")
                    elapsed = time.monotonic() - start_time
                    print(json.dumps({
                        "symbol": symbol, "days_done": index + 1, "days": days,
                        "elapsed_s": round(elapsed, 2), "heartbeat": True,
                    }), file=sys.stderr, flush=True)
    tmp.rename(output)
    result = {
        "status": "BOOK_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC",
        "symbol": symbol, "days": days, "minute_rows": days * 1440,
        "indicator_ids": [9, 11, 29], "source_files": provenance,
        "sum_source_events": sum(
            x["source_stats"]["processed"] for x in provenance
        ),
        "aggregate_event_classes": dict(status),
        "data_sha256": digest(output), "code_sha256": digest(program),
        "availability": "UNKNOWN_NO_RECORDED_RECEIVER_CLOCK",
        "notes": [
            "Only minutes with an actual book update are marked observed",
            "No claimed continuity across daily ZIP beyond fresh snapshot",
            "No equal arrival time claim for trade/execution joins",
            "Price/size level changes do not prove order actor/cancellation",
        ],
    }
    json_new(dest / "result.json", result)
    json_new(dest / "input_provenance.json", {"sources": provenance})
    print(json.dumps({
        "status": result["status"], "symbol": symbol,
        "days": days, "source_events": result["sum_source_events"],
        "feature_sha": result["data_sha256"],
        "output_dir": str(dest),
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, choices=COHORT)
    parser.add_argument("--days", required=True, type=int, choices=(2, 30))
    args = parser.parse_args()
    run(args.symbol, args.days)
