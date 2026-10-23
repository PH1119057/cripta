#!/usr/bin/env python3
"""Month-one Bybit ob200 price-key snapshots: #9 imbalance, #11 depth, #29 spread.

Published research-only script; actor cripta; no database, trading, Exchange,
service or source checkout mutation. Reads 30 UTC daily compressed Bybit ob200
files from /data/cripta/datasets/raw/20260518_20260816. Writes ONLY new
paths under /data/cripta/research/runs/microstructure_firstmonth_book9_11_29_utcclip_20261023.
Source complete availability/receipt is UNKNOWN; no zero-imputation.
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
from decimal import Decimal

COINS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
ROOT = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
OUTPUT = pathlib.Path(
    "/data/cripta/research/runs/microstructure_firstmonth_book9_11_29_20261023"
)
REPO = pathlib.Path("/srv/cripta/source_checkout")
FIRST = dt.date(2026, 5, 18)
D0 = Decimal("0.9995")
D1 = Decimal("1.0005")
FORMULA = "ob200_minute_last_observed_5bp_depth_imbalance_spread_utcclip_v2"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def new_json(path, value):
    partial = path.with_suffix(path.suffix + ".part")
    with partial.open("x", encoding="utf-8") as out:
        json.dump(value, out, indent=2, sort_keys=True, ensure_ascii=False)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    partial.rename(path)


def update_book(bids, asks, event, saw_snapshot):
    kind = event["type"]
    payload = event["data"]
    if kind == "snapshot":
        bids.clear()
        asks.clear()
        for src, target in ((payload["b"], bids), (payload["a"], asks)):
            for price, quantity in src:
                p, qty = Decimal(price), Decimal(quantity)
                if p <= 0 or qty < 0 or not p.is_finite() or not qty.is_finite():
                    raise ValueError("INVALID_SNAPSHOT_PRICE_OR_SIZE")
                if qty:
                    target[p] = qty
        return True
    if kind != "delta":
        raise ValueError("UNRECOGNIZED_OB_MESSAGE_TYPE")
    if not saw_snapshot:
        raise ValueError("DELTA_BEFORE_INITIAL_SNAPSHOT")
    for src, target in ((payload["b"], bids), (payload["a"], asks)):
        for price, quantity in src:
            p, qty = Decimal(price), Decimal(quantity)
            if p <= 0 or qty < 0 or not p.is_finite() or not qty.is_finite():
                raise ValueError("INVALID_DELTA_PRICE_OR_SIZE")
            if qty == 0:
                target.pop(p, None)
            else:
                target[p] = qty
    return True


def current_features(bids, asks, message_ms, minute_end_ms):
    if not bids or not asks:
        raise ValueError("NO_BOTH_BOOK_SIDES")
    bid, ask = max(bids), min(asks)
    if bid >= ask:
        raise ValueError("CROSSED_OR_LOCKED_BOOK")
    mid = (bid + ask) / 2
    bd = sum(
        (price * qty for price, qty in bids.items()
         if mid * D0 <= price <= mid), Decimal(0)
    )
    ad = sum(
        (price * qty for price, qty in asks.items()
         if mid <= price <= mid * D1), Decimal(0)
    )
    total = bd + ad
    age = minute_end_ms - message_ms
    if not 0 < age <= 60000:
        raise ValueError("INVALID_LAST_MESSAGE_AGE")
    return {
        "factor_9_queue_imbalance_near5bp": str((bd - ad) / total) if total else None,
        "factor_11_bid_depth_usdt_near5bp": str(bd),
        "factor_11_ask_depth_usdt_near5bp": str(ad),
        "factor_29_spread_bps": str((ask - bid) * 10000 / mid),
        "best_bid_quote": str(bid),
        "best_ask_quote": str(ask),
        "last_book_event_timestamp_ms": message_ms,
        "book_event_age_to_derived_end_ms": age,
        "book_levels_bid": len(bids),
        "book_levels_ask": len(asks),
        "quality": "LAST_OBSERVED_BOOK_STATE_SOURCE_CONTINUITY_UNKNOWN",
    }


def event_digest(event):
    return hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def verified_boundary(previous, current):
    if previous["out_of_day_suffix_messages"] == 0:
        return True
    return (
        previous["out_of_day_suffix_last_type"] == "snapshot"
        and current["first_event_type"] == "snapshot"
        and previous["out_of_day_suffix_last_hash"] == current["first_event_hash"]
    )


def day_rows(symbol, day, zipfile_path):
    """Source day starts from initial snapshot. No artificial book carry/zeros."""
    start_ms = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000)
    bids, asks = {}, {}
    rows = [None] * 1440
    last_ts = last_minute = previous_source_ts = None
    first_event_hash = first_event_type = None
    suffix_last_hash = suffix_last_type = None
    suffix_count = 0
    suffix_started = False
    saw_snapshot = False
    count = snapshots = 0
    max_event_gap_ms = 0
    first_sequence = last_sequence = None
    with zipfile.ZipFile(zipfile_path) as zipin:
        names = zipin.namelist()
        expect = day.isoformat() + "_" + symbol + "_ob200.data"
        if names != [expect]:
            raise ValueError("UNEXPECTED_ZIP_MEMBER_OR_MULTIPLE_MEMBERS")
        with zipin.open(names[0]) as reader:
            for line in reader:
                event = json.loads(line)
                ts = int(event["ts"])
                if previous_source_ts is not None and ts < previous_source_ts:
                    raise ValueError("OB_TIMESTAMP_NON_MONOTONE")
                previous_source_ts = ts
                if ts < start_ms:
                    raise ValueError("OB_SOURCE_HAS_EARLY_EVENTS")
                if first_event_hash is None:
                    first_event_hash = event_digest(event)
                    first_event_type = event["type"]
                if ts >= start_ms + 86400000:
                    suffix_started = True
                    suffix_count += 1
                    if suffix_count > 1000:
                        raise ValueError("OUT_OF_DAY_SUFFIX_EXCEEDS_BOUND")
                    suffix_last_hash = event_digest(event)
                    suffix_last_type = event["type"]
                    continue
                if suffix_started:
                    raise ValueError("SOURCE_REENTERS_PREVIOUS_DAY_AFTER_SUFFIX")
                if last_ts is not None:
                    max_event_gap_ms = max(max_event_gap_ms, ts - last_ts)
                minute = (ts - start_ms) // 60000
                if last_minute is not None and minute != last_minute:
                    end_ms = start_ms + (last_minute + 1) * 60000
                    rows[last_minute] = current_features(bids, asks, last_ts, end_ms)
                saw_snapshot = update_book(bids, asks, event, saw_snapshot)
                if event["type"] == "snapshot":
                    snapshots += 1
                seq = event["data"].get("seq")
                if seq is not None:
                    if first_sequence is None:
                        first_sequence = seq
                    last_sequence = seq
                last_ts, last_minute = ts, minute
                count += 1
        if last_minute is not None:
            rows[last_minute] = current_features(
                bids, asks, last_ts, start_ms + (last_minute + 1) * 60000
            )
        if count == 0 or snapshots == 0:
            raise ValueError("MISSING_SNAPSHOT_OR_EMPTY_FILE")
    out = []
    for i, feat in enumerate(rows):
        stamp = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(minutes=i)
        item = {
            "venue_id": "Bybit",
            "instrument_id": symbol,
            "market_source_id": "BYBIT_OB200_ARCHIVE",
            "formula_version": FORMULA,
            "indicator_ids": [9, 11, 29],
            "window_start_utc": stamp.isoformat(),
            "derived_window_end_utc": (stamp + dt.timedelta(minutes=1)).isoformat(),
            "observed_at_utc": None,
            "verified_available_after_utc": None,
        }
        if feat is None:
            item.update({
                "factor_9_queue_imbalance_near5bp": None,
                "factor_11_bid_depth_usdt_near5bp": None,
                "factor_11_ask_depth_usdt_near5bp": None,
                "factor_29_spread_bps": None,
                "best_bid_quote": None,
                "best_ask_quote": None,
                "last_book_event_timestamp_ms": None,
                "book_event_age_to_derived_end_ms": None,
                "book_levels_bid": None,
                "book_levels_ask": None,
                "quality": "NO_BOOK_EVENT_IN_MINUTE",
            })
        else:
            item.update(feat)
        out.append(item)
    return out, {
        "messages": count,
        "snapshots": snapshots,
        "minute_observed": sum(x is not None for x in rows),
        "max_observed_message_gap_ms": max_event_gap_ms,
        "first_sequence_observed": first_sequence,
        "last_sequence_observed": last_sequence,
        "sequence_continuity_verified": False,
        "receipt_clock_verified": False,
        "first_event_hash": first_event_hash,
        "first_event_type": first_event_type,
        "out_of_day_suffix_messages": suffix_count,
        "out_of_day_suffix_last_hash": suffix_last_hash,
        "out_of_day_suffix_last_type": suffix_last_type,
        "out_of_day_suffix_boundary_verified_with_next": None,
    }


def run(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    if symbol not in COINS or days not in (2, 30):
        raise ValueError("UNDECLARED_SCOPE")
    if not os.access(OUTPUT.parent, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_PARENT_CREATE_NOT_ALLOWED")
    sources = []
    for i in range(days):
        date = FIRST + dt.timedelta(days=i)
        f = ROOT / symbol / "orderbook" / (
            date.isoformat() + "_" + symbol + "_ob200.data.zip"
        )
        if not f.is_file():
            raise FileNotFoundError("REQUIRED_BOOK_SOURCE_MISSING " + date.isoformat())
        sources.append((date, f))
    target = OUTPUT / ("physical_" + str(days) + "d") / symbol
    if target.exists():
        raise FileExistsError("RESEARCH_OUTPUT_EXISTS_DO_NOT_OVERWRITE")
    os.umask(0o007)
    OUTPUT.mkdir(mode=0o2770, exist_ok=True)
    target.parent.mkdir(mode=0o2770, exist_ok=True)
    target.mkdir(mode=0o2770, exist_ok=False)
    (target / "source_snapshot").mkdir(mode=0o2770, exist_ok=False)
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, target / "source_snapshot/materializer.py")
    source_commit = (REPO / ".git/refs/heads/main").read_text().strip()
    code_sha = sha(script)
    (target / "source_commit.txt").write_text(source_commit + "\n")
    (target / "source_sha256.txt").write_text(code_sha + "\n")
    (target / "command.txt").write_text(
        "python3 -B " + str(script) + " --symbol " + symbol
        + " --days " + str(days) + "\n"
    )
    (target / "environment.txt").write_text(
        "uid=" + str(os.geteuid()) + "\npython=" + sys.executable + "\n"
    )
    new_json(target / "run_manifest.json", {
        "status": "RUNNING",
        "scope": [symbol, days], "source_commit": source_commit,
        "code_sha256": code_sha, "actor": "cripta",
        "receiver_clock": "UNKNOWN",
    })
    out = target / "features.jsonl.gz"
    tmp = target / "features.jsonl.gz.part"
    day_evidence = []
    started = time.monotonic()
    total_rows = 0
    with tmp.open("xb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as gz:
            with io.TextIOWrapper(gz, encoding="utf-8", newline="") as stream:
                for index, (date, path) in enumerate(sources, 1):
                    source_hash = sha(path)
                    rows, meta = day_rows(symbol, date, path)
                    if day_evidence and day_evidence[-1]["out_of_day_suffix_messages"]:
                        if not verified_boundary(day_evidence[-1], meta):
                            raise ValueError("ADJACENT_UTC_ARCHIVE_BOUNDARY_SNAPSHOT_MISMATCH")
                        day_evidence[-1]["out_of_day_suffix_boundary_verified_with_next"] = True
                    for row in rows:
                        row["source_archive_sha256"] = source_hash
                        stream.write(json.dumps(row, sort_keys=True) + "\n")
                        total_rows += 1
                    day_evidence.append({
                        "day": date.isoformat(), "path": str(path),
                        "zip_sha256": source_hash, **meta,
                    })
                    elapsed = time.monotonic() - started
                    print(json.dumps({
                        "stage": "OB200_PHYSICAL_9_11_29",
                        "symbol": symbol, "days_done": index,
                        "days_total": days, "percent": round(100 * index / days, 2),
                        "elapsed_s": round(elapsed, 1), "heartbeat": True,
                    }), flush=True)
    if total_rows != days * 1440:
        raise ValueError("MINUTE_COUNT")
    tmp.rename(out)
    new_json(target / "input_provenance.json", {
        "days": day_evidence,
        "source_gap_visibility": "UNVERIFIED_NO_RECEIVER_LOG",
    })
    result = {
        "status": "PHYSICAL_BOOK_MONTH_COMPLETE_PENDING_INDEPENDENT_QC",
        "symbol": symbol, "days": days, "rows": total_rows,
        "source_message_count": sum(x["messages"] for x in day_evidence),
        "observed_minute_count": sum(x["minute_observed"] for x in day_evidence),
        "zip_input_sha256": [x["zip_sha256"] for x in day_evidence],
        "output_sha256": sha(out), "program_sha256": code_sha,
        "has_verified_source_continuity": False,
        "has_receiver_clock": False,
        "limitations": [
            "Last observed snapshot per minute, not intraminute average",
            "Missing book event minute is null rather than presumed unchanged/zero",
            "Unverified sequence gaps and receipt timing; not RESEARCH-READY",
            "Out-of-day ZIP suffix cut by published ts; internal overlap snapshot checked",
            "Final run day outside-month boundary left UNVERIFIED, no next-period read",
            "Top 5bp quote depth is snapshot stock, not cancellations or trades",
        ],
    }
    new_json(target / "result.json", result)
    print(json.dumps({
        "status": result["status"], "symbol": symbol, "days": days,
        "rows": total_rows, "messages": result["source_message_count"],
        "output_sha256": result["output_sha256"],
        "output_dir": str(target),
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, choices=COINS)
    parser.add_argument("--days", required=True, type=int, choices=(2, 30))
    args = parser.parse_args()
    run(args.symbol, args.days)
