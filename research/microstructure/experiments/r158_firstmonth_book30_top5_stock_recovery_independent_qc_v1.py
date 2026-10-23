#!/usr/bin/env python3
"""#30 independent raw-ZIP full-minute Decimal check: Top5 stock recovery proxy.

Book state, Top5 computation and episode detection reimplemented separately
from materializer. Check exact 1m rows, all original ZIP bytes/sha, full daily
episode accounting and terminal output/source hashes; no research-ready claim.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import heapq
import json
import os
import pathlib
import shutil
import zipfile
from decimal import Decimal

COINS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
BASE = pathlib.Path("/data/cripta/research/runs/r158_firstmonth_book30_top5_stock_recovery_20261023")
FORMULA = "ob200_top5_quote_stock_decline_later_ts_60s_recovery_v1"
PHYSICAL = "BOOK30_TOP5_STOCK_RECOVERY_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
PASS = "BOOK30_TOP5_STOCK_RECOVERY_INDEPENDENT_FULL_MINUTE_DECIMAL_QC_PASS_NOT_RESEARCH_READY"
MEASURES = ("opened", "recovered", "observed_expired", "snapshot_censored",
            "same_timestamp_ambiguous", "recovery_ms_total")
TOTALS = MEASURES + ("day_end_censored",)


def digest(p):
    h = hashlib.sha256()
    with p.open("rb") as source:
        for chunk in iter(lambda: source.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def stock(book, side):
    # Independent heap-based five-largest-bid/five-smallest-ask price selection.
    keys = (heapq.nlargest(5, book) if side == "b"
            else heapq.nsmallest(5, book))
    return sum((price * book[price] for price in keys), Decimal(0))


def parse_day(day, archive, symbol):
    start = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()) * 1000
    state = {"b": {}, "a": {}}
    previous_stock = {"b": None, "a": None}
    episodes = {"b": None, "a": None}
    summary = {side: {field: 0 for field in TOTALS} for side in ("b", "a")}
    bins = [{side: {field: 0 for field in MEASURES}
             for side in ("b", "a")} for _ in range(1440)]
    metadata = [{"messages": 0, "snapshots": 0, "deltas": 0}
                for _ in range(1440)]
    last_timestamp = None
    first_type = None
    suffix = gaps = msg_total = snap_total = delta_total = 0
    with zipfile.ZipFile(archive) as z:
        member = day.isoformat() + "_" + symbol + "_ob200.data"
        if z.namelist() != [member]:
            raise ValueError("QC_ORIGINAL_MEMBER_MISMATCH")
        with z.open(member) as stream:
            for raw in stream:
                event = json.loads(raw)
                timestamp = int(event["ts"])
                if last_timestamp is not None and timestamp < last_timestamp:
                    raise ValueError("QC_UNSORTED_BOOK_SOURCE")
                if last_timestamp is not None and timestamp - last_timestamp > 60000 and timestamp < start + 86400000:
                    gaps += 1
                last_timestamp = timestamp
                if timestamp < start:
                    raise ValueError("QC_EVENT_BEFORE_DAY")
                if timestamp >= start + 86400000:
                    suffix += 1
                    if suffix > 1000:
                        raise ValueError("QC_EXCESSIVE_SOURCE_SUFFIX")
                    continue
                if suffix:
                    raise ValueError("QC_SOURCE_REENTERED_DAY")
                minute = (timestamp - start) // 60000
                slot = metadata[minute]
                side_stats = bins[minute]
                typ = event["type"]
                if first_type is None:
                    first_type = typ
                    if typ != "snapshot":
                        raise ValueError("QC_INITIAL_SNAPSHOT_MISSING")
                slot["messages"] += 1
                msg_total += 1
                if typ == "snapshot":
                    slot["snapshots"] += 1
                    snap_total += 1
                    for side in ("b", "a"):
                        if episodes[side] is not None:
                            side_stats[side]["snapshot_censored"] += 1
                            summary[side]["snapshot_censored"] += 1
                            episodes[side] = None
                        fresh = {}
                        for pstr, qstr in event["data"][side]:
                            price, amount = Decimal(pstr), Decimal(qstr)
                            if (not price.is_finite() or price <= 0
                                    or not amount.is_finite() or amount < 0):
                                raise ValueError("QC_BAD_SNAPSHOT")
                            if amount > 0:
                                fresh[price] = amount
                        state[side] = fresh
                        previous_stock[side] = stock(fresh, side)
                elif typ == "delta":
                    slot["deltas"] += 1
                    delta_total += 1
                    for side in ("b", "a"):
                        # Independent update: build current side via a copy.
                        current_book = state[side].copy()
                        for pstr, qstr in event["data"][side]:
                            p, q = Decimal(pstr), Decimal(qstr)
                            if (not p.is_finite() or p <= 0
                                    or not q.is_finite() or q < 0):
                                raise ValueError("QC_BAD_DELTA")
                            current_book.pop(p, None)
                            if q > 0:
                                current_book[p] = q
                        current_stock = stock(current_book, side)
                        ongoing = episodes[side]
                        if ongoing is not None:
                            duration = timestamp - ongoing["time"]
                            status = None
                            if duration > 60000:
                                status = "observed_expired"
                            elif current_stock >= ongoing["target"]:
                                status = ("same_timestamp_ambiguous" if duration == 0
                                          else "recovered")
                            if status is not None:
                                side_stats[side][status] += 1
                                summary[side][status] += 1
                                if status == "recovered":
                                    side_stats[side]["recovery_ms_total"] += duration
                                    summary[side]["recovery_ms_total"] += duration
                                episodes[side] = None
                        if (episodes[side] is None and previous_stock[side] is not None
                                and current_stock < previous_stock[side]):
                            episodes[side] = {"time": timestamp,
                                              "target": previous_stock[side]}
                            side_stats[side]["opened"] += 1
                            summary[side]["opened"] += 1
                        previous_stock[side] = current_stock
                        state[side] = current_book
                else:
                    raise ValueError("QC_BAD_BOOK_MESSAGE_TYPE")
    if not msg_total or not snap_total or first_type != "snapshot":
        raise ValueError("QC_NO_DAY_INITIAL_SNAPSHOT")
    for side in ("b", "a"):
        summary[side]["day_end_censored"] = int(episodes[side] is not None)
        measure = summary[side]
        if measure["opened"] != sum(measure[k] for k in (
                "recovered", "observed_expired", "snapshot_censored",
                "same_timestamp_ambiguous", "day_end_censored")):
            raise ValueError("QC_EPISODE_ACCOUNTING")
    return metadata, bins, {
        "messages": msg_total, "snapshots": snap_total, "deltas": delta_total,
        "next_day_suffix_messages": suffix, "gaps_gt_60s": gaps,
        "first_type": first_type,
        "bid": summary["b"], "ask": summary["a"],
        "sequence_continuity_verified": False,
        "receiver_clock_verified": False,
    }


def main(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("QC_ACTOR_NOT_CRIPTA")
    if symbol not in COINS or days not in (2, 30):
        raise ValueError("QC_SCOPE_NOT_APPROVED")
    folder = BASE / f"physical_{days}d" / symbol
    receipt = folder / "independent_qc_report.json"
    if receipt.exists() or (folder / "independent_qc_report.json.part").exists():
        raise FileExistsError("QC_REPORT_ALREADY_EXISTS_DO_NOT_OVERWRITE")
    result = json.loads((folder / "result.json").read_text())
    provenance = json.loads((folder / "input_provenance.json").read_text())["days"]
    data = folder / "features_1m.jsonl.gz"
    source_snapshot = folder / "source_snapshot/materializer.py"
    if (result.get("status") != PHYSICAL or result.get("symbol") != symbol
            or result.get("days") != days or result.get("rows") != 1440*days
            or result.get("indicator_id") != 30
            or result.get("formula_version") != FORMULA or len(provenance) != days
            or result.get("recovered_same_level_or_order_verified") is not False
            or result.get("continuous_60s_observation_verified") is not False
            or result.get("receiver_clock_verified") is not False
            or result.get("research_ready") is not False):
        raise ValueError("QC_PRIMARY_CONTRACT_INVALID")
    if digest(data) != result["output_sha256"] or digest(source_snapshot) != result["program_sha256"]:
        raise ValueError("QC_PRIMARY_SHA_CHANGED")
    sums = {"b": {key: 0 for key in TOTALS},
            "a": {key: 0 for key in TOTALS}}
    receipts = []
    verified_rows = messages = deltas = 0
    with gzip.open(data, "rt") as out:
        for i in range(days):
            day = FIRST + dt.timedelta(days=i)
            archive = RAW / symbol / "orderbook" / (
                day.isoformat() + "_" + symbol + "_ob200.data.zip")
            p = provenance[i]
            source_sha = digest(archive)
            if (p["date_utc"] != day.isoformat() or p["source_path"] != str(archive)
                    or p["source_sha256"] != source_sha
                    or p["source_bytes"] != archive.stat().st_size
                    or p["minute_rows"] != 1440):
                raise ValueError("QC_ORIGINAL_ZIP_PROVENANCE_INVALID")
            m, rows, stats = parse_day(day, archive, symbol)
            for k in ("messages", "snapshots", "deltas",
                      "next_day_suffix_messages", "gaps_gt_60s", "first_type",
                      "bid", "ask", "sequence_continuity_verified",
                      "receiver_clock_verified"):
                if p[k] != stats[k]:
                    raise ValueError("QC_DAILY_SOURCE_OR_EPISODE_MISMATCH_"+k)
            messages += stats["messages"]
            deltas += stats["deltas"]
            for side in ("b", "a"):
                for field in TOTALS:
                    sums[side][field] += stats["bid" if side == "b" else "ask"][field]
            for minute, (meta, bucket) in enumerate(zip(m, rows)):
                line = out.readline()
                if not line:
                    raise ValueError("QC_MISSING_MINUTE_ROW")
                actual = json.loads(line)
                stamp = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(minutes=minute)
                observed = meta["deltas"] > 0
                expected = {
                    "venue_id": "Bybit", "instrument_id": symbol,
                    "market_source_id": "BYBIT_OB200_ARCHIVE",
                    "indicator_id": 30, "formula_version": FORMULA,
                    "window_start_utc": stamp.isoformat(),
                    "derived_window_end_utc": (
                        stamp+dt.timedelta(minutes=1)).isoformat(),
                    "observed_at_utc": None, "verified_available_after_utc": None,
                    "book_messages": meta["messages"],
                    "snapshot_messages": meta["snapshots"],
                    "delta_messages": meta["deltas"],
                    "quality": ("OBSERVED_TOP5_STOCK_EVENTS_ORDER_IDENTITY_UNVERIFIED"
                                if observed else "NO_BOOK_DELTA_IN_MINUTE"),
                    "source_archive_sha256": source_sha,
                }
                for side in ("b", "a"):
                    label = "bid" if side == "b" else "ask"
                    for field in MEASURES:
                        available = observed or (field == "snapshot_censored"
                                                 and meta["snapshots"] > 0)
                        expected[label+"_"+field] = bucket[side][field] if available else None
                if actual != expected:
                    raise ValueError("QC_MINUTE_RECORD_MISMATCH_"+str(verified_rows))
                verified_rows += 1
            receipts.append({"date_utc": day.isoformat(), "source_sha256": source_sha,
                             "source_bytes": archive.stat().st_size,
                             "minute_rows": 1440,
                             "messages": stats["messages"],
                             "deltas": stats["deltas"],
                             "bid": stats["bid"], "ask": stats["ask"]})
            print(json.dumps({"stage": "BOOK30_INDEPENDENT_QC", "symbol": symbol,
                              "days_done": i+1, "days_total": days,
                              "heartbeat": True}), flush=True)
        if out.readline():
            raise ValueError("QC_EXTRA_MINUTE_ROW")
    if (verified_rows != 1440*days or messages != result["book_messages"]
            or deltas != result["book_delta_messages"]
            or sums["b"] != result["bid"] or sums["a"] != result["ask"]):
        raise ValueError("QC_MONTHLY_TOTALS_MISMATCH")
    checker = pathlib.Path(__file__).resolve()
    snapshot = folder / "source_snapshot/independent_checker.py"
    if snapshot.exists():
        raise FileExistsError("QC_SNAPSHOT_ALREADY_PRESENT")
    shutil.copyfile(checker, snapshot)
    report = {
        "status": PASS, "symbol": symbol, "days": days,
        "verified_minute_rows": verified_rows, "source_days": receipts,
        "book_messages": messages, "book_delta_messages": deltas,
        "bid": sums["b"], "ask": sums["a"],
        "indicator_id": 30, "formula_version": FORMULA,
        "original_sha256": result["output_sha256"],
        "checker_sha256": digest(checker),
        "recovered_same_level_or_order_verified": False,
        "continuous_60s_observation_verified": False,
        "book_sequence_continuity_verified": False,
        "receiver_clock_verified": False, "research_ready": False,
    }
    tmp = receipt.with_name(receipt.name + ".part")
    with tmp.open("x", encoding="utf-8") as f:
        json.dump(report, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.rename(receipt)
    print(json.dumps({"status": PASS, "symbol": symbol,
                      "verified_minute_rows": verified_rows,
                      "receipt_sha256": digest(receipt)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", choices=COINS, required=True)
    parser.add_argument("--days", type=int, choices=(2, 30), required=True)
    args = parser.parse_args()
    main(args.symbol, args.days)
