#!/usr/bin/env python3
"""Independent exact-price raw ZIP replay QC for descriptive #12 book decrease.

This checker reopens all 30 source archives, recomputes every minute with
separate book transition logic, and verifies source hashes, every numeric value,
quality and totals. It does NOT infer orders cancelled or receiver continuity.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import shutil
import zipfile
from decimal import Decimal

COINS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
ROOT = pathlib.Path("/data/cripta/research/runs/r158_firstmonth_book12_visible_decrease_20261023")
FORMULA = "ob200_exact_price_level_visible_quantity_decrease_minute_v1"
PHYSICAL = "BOOK12_VISIBLE_DECREASE_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
PASS = "BOOK12_VISIBLE_DECREASE_INDEPENDENT_1M_DECIMAL_QC_PASS_NOT_RESEARCH_READY"


def sha256(path):
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def independently_measure(day, path, symbol):
    """Per update compute previous-minus-next positive part, no parser reuse."""
    beginning = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()) * 1000
    bins = [{
        "messages": 0, "snapshots": 0, "deltas": 0,
        "bid_count": 0, "ask_count": 0,
        "bid_quote": Decimal(0), "ask_quote": Decimal(0),
    } for _ in range(1440)]
    books = {"b": {}, "a": {}}
    before = None
    suffix = False
    suffix_messages = 0
    start_type = None
    totals = {"messages": 0, "snapshots": 0, "deltas": 0}
    with zipfile.ZipFile(path) as archive:
        filename = day.isoformat() + "_" + symbol + "_ob200.data"
        if archive.namelist() != [filename]:
            raise ValueError("QC_WRONG_SOURCE_MEMBER")
        with archive.open(filename) as f:
            for record in f:
                event = json.loads(record)
                ts = int(event["ts"])
                if before is not None and ts < before:
                    raise ValueError("QC_NONMONOTONE_TIMESTAMP")
                before = ts
                if ts < beginning:
                    raise ValueError("QC_SOURCE_BEFORE_DAY")
                if ts >= beginning + 86400000:
                    suffix = True
                    suffix_messages += 1
                    if suffix_messages > 1000:
                        raise ValueError("QC_UNBOUNDED_NEXT_DAY_SUFFIX")
                    continue
                if suffix:
                    raise ValueError("QC_SOURCE_RETURNED_INSIDE_DAY")
                kind = event["type"]
                if start_type is None:
                    start_type = kind
                    if kind != "snapshot":
                        raise ValueError("QC_NO_INITIAL_SNAPSHOT")
                bucket = bins[(ts - beginning) // 60000]
                bucket["messages"] += 1
                totals["messages"] += 1
                if kind == "snapshot":
                    bucket["snapshots"] += 1
                    totals["snapshots"] += 1
                    for side in ("b", "a"):
                        replacement = {}
                        for price_text, qty_text in event["data"][side]:
                            p, q = Decimal(price_text), Decimal(qty_text)
                            if not p.is_finite() or p <= 0 or not q.is_finite() or q < 0:
                                raise ValueError("QC_BAD_BOOK_SNAPSHOT_VALUE")
                            if q > 0:
                                replacement[p] = q
                        books[side] = replacement
                elif kind == "delta":
                    bucket["deltas"] += 1
                    totals["deltas"] += 1
                    for side in ("b", "a"):
                        book = books[side]
                        for price_text, qty_text in event["data"][side]:
                            price = Decimal(price_text)
                            new_qty = Decimal(qty_text)
                            if (not price.is_finite() or price <= 0 or
                                    not new_qty.is_finite() or new_qty < 0):
                                raise ValueError("QC_BAD_BOOK_DELTA_VALUE")
                            previous_qty = book.get(price, Decimal(0))
                            drop = previous_qty - new_qty
                            if drop > 0:
                                label = "bid" if side == "b" else "ask"
                                bucket[label + "_count"] += 1
                                bucket[label + "_quote"] += drop * price
                            if new_qty > 0:
                                book[price] = new_qty
                            else:
                                book.pop(price, None)
                else:
                    raise ValueError("QC_UNKNOWN_MESSAGE_TYPE")
    if not totals["messages"] or not totals["snapshots"] or start_type != "snapshot":
        raise ValueError("QC_MISSING_BOOK_INITIAL_SNAPSHOT")
    return bins, totals, suffix_messages


def compare_decimal(value, expected, label):
    if expected is None:
        if value is not None:
            raise ValueError("QC_EXPECTED_NULL_" + label)
    elif value is None or Decimal(value) != expected:
        raise ValueError("QC_EXACT_DECIMAL_DIFFERENCE_" + label)


def check(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("QC_NOT_AUTHORIZED_CRIPTA")
    if symbol not in COINS or days not in (2, 30):
        raise ValueError("QC_SCOPE_UNAPPROVED")
    folder = ROOT / f"physical_{days}d" / symbol
    receipt = folder / "independent_qc_report.json"
    if receipt.exists() or (folder / "independent_qc_report.json.part").exists():
        raise FileExistsError("QC_RECEIPT_EXISTS_DO_NOT_OVERWRITE")
    original = json.loads((folder / "result.json").read_text())
    input_days = json.loads((folder / "input_provenance.json").read_text())["days"]
    gzfile = folder / "features_1m.jsonl.gz"
    snapshot = folder / "source_snapshot/materializer.py"
    if (
        original.get("status") != PHYSICAL
        or original.get("indicator_id") != 12
        or original.get("formula_version") != FORMULA
        or original.get("symbol") != symbol
        or original.get("days") != days
        or original.get("rows") != days * 1440
        or len(input_days) != days
        or original.get("research_ready") is not False
        or original.get("pull_or_cancel_verified") is not False
        or original.get("receiver_clock_verified") is not False
        or original.get("book_sequence_continuity_verified") is not False
    ):
        raise ValueError("QC_ORIGINAL_RESULT_CONTRACT_MISMATCH")
    if (sha256(gzfile) != original["output_sha256"]
            or sha256(snapshot) != original["program_sha256"]):
        raise ValueError("QC_ORIGINAL_SHA256_DRIFT")
    count = observed = totals_usdt = 0
    totals_usdt = Decimal(0)
    book_messages = delta_messages = 0
    evidence = []
    with gzip.open(gzfile, "rt") as primary:
        for index in range(days):
            day = FIRST + dt.timedelta(days=index)
            archive = RAW / symbol / "orderbook" / (
                day.isoformat() + "_" + symbol + "_ob200.data.zip")
            info = input_days[index]
            actual_sha = sha256(archive)
            if (
                info["date_utc"] != day.isoformat()
                or info["source_path"] != str(archive)
                or info["source_sha256"] != actual_sha
                or info["source_bytes"] != archive.stat().st_size
                or info["minute_rows"] != 1440
            ):
                raise ValueError("QC_SOURCE_ARCHIVE_IDENTITY_MISMATCH")
            minutes, day_total, suffix = independently_measure(day, archive, symbol)
            if any(info[key] != val for key, val in (
                ("messages", day_total["messages"]),
                ("snapshots", day_total["snapshots"]),
                ("deltas", day_total["deltas"]),
                ("source_suffix_messages", suffix),
            )):
                raise ValueError("QC_SOURCE_DAY_COUNTS_MISMATCH")
            book_messages += day_total["messages"]
            delta_messages += day_total["deltas"]
            for minute, rec in enumerate(minutes):
                line = primary.readline()
                if not line:
                    raise ValueError("QC_PRIMARY_TRUNCATED")
                candidate = json.loads(line)
                moment = dt.datetime.combine(day, dt.time(), dt.UTC)
                moment += dt.timedelta(minutes=minute)
                expected = {
                    "venue_id": "Bybit", "instrument_id": symbol,
                    "market_source_id": "BYBIT_OB200_ARCHIVE",
                    "indicator_id": 12, "formula_version": FORMULA,
                    "window_start_utc": moment.isoformat(),
                    "derived_window_end_utc": (
                        moment + dt.timedelta(minutes=1)).isoformat(),
                    "observed_at_utc": None,
                    "verified_available_after_utc": None,
                    "book_messages": rec["messages"],
                    "book_snapshot_messages": rec["snapshots"],
                    "book_delta_messages": rec["deltas"],
                    "visible_bid_decrease_count": (
                        rec["bid_count"] if rec["deltas"] else None),
                    "visible_ask_decrease_count": (
                        rec["ask_count"] if rec["deltas"] else None),
                    "quality": (
                        "OBSERVED_EXACT_PRICE_VISIBLE_DECREASE_CAUSE_UNKNOWN"
                        if rec["deltas"] else "NO_BOOK_DELTA_IN_MINUTE"),
                    "source_archive_sha256": actual_sha,
                }
                if any(candidate.get(field) != value for field, value in expected.items()):
                    raise ValueError("QC_MINUTE_FIELDS_DIFFER_" + str(count))
                compare_decimal(
                    candidate["visible_bid_decrease_usdt"],
                    rec["bid_quote"] if rec["deltas"] else None, "BID")
                compare_decimal(
                    candidate["visible_ask_decrease_usdt"],
                    rec["ask_quote"] if rec["deltas"] else None, "ASK")
                observed += int(bool(rec["deltas"]))
                totals_usdt += rec["bid_quote"] + rec["ask_quote"]
                count += 1
            evidence.append({
                "date_utc": day.isoformat(), "source_sha256": actual_sha,
                "source_bytes": archive.stat().st_size,
                "messages": day_total["messages"], "deltas": day_total["deltas"],
                "snapshots": day_total["snapshots"], "suffix": suffix,
                "minute_rows": 1440,
            })
            print(json.dumps({"stage": "BOOK12_INDEPENDENT_QC",
                              "symbol": symbol, "days_done": index + 1,
                              "days_total": days, "heartbeat": True}), flush=True)
        if primary.readline():
            raise ValueError("QC_UNEXPECTED_EXTRA_ROWS")
    if (
        count != days * 1440
        or observed != original["observed_delta_minutes"]
        or count-observed != original["no_delta_minutes"]
        or totals_usdt != Decimal(original["visible_decrease_usdt_all_sides"])
        or book_messages != original["book_messages"]
        or delta_messages != original["book_delta_messages"]
    ):
        raise ValueError("QC_SUMMARY_DIFFERENCE")
    checker = pathlib.Path(__file__).resolve()
    checker_copy = folder / "source_snapshot/independent_checker.py"
    if checker_copy.exists():
        raise FileExistsError("QC_CHECKER_SNAPSHOT_EXISTS")
    shutil.copyfile(checker, checker_copy)
    result = {
        "status": PASS, "symbol": symbol, "days": days,
        "verified_minute_rows": count, "observed_delta_minutes": observed,
        "no_delta_minutes": count-observed,
        "visible_decrease_usdt_all_sides": str(totals_usdt),
        "book_messages": book_messages, "book_delta_messages": delta_messages,
        "source_days": evidence, "indicator_id": 12,
        "formula_version": FORMULA,
        "original_sha256": original["output_sha256"],
        "checker_sha256": sha256(checker),
        "pull_or_cancel_verified": False, "book_sequence_continuity_verified": False,
        "receiver_clock_verified": False, "research_ready": False,
    }
    partial = receipt.with_name(receipt.name + ".part")
    with partial.open("x", encoding="utf-8") as output:
        json.dump(result, output, sort_keys=True, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    partial.rename(receipt)
    print(json.dumps({"status": PASS, "symbol": symbol,
                      "verified_minute_rows": count,
                      "receipt_sha256": sha256(receipt)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", choices=COINS, required=True)
    parser.add_argument("--days", type=int, choices=(2, 30), required=True)
    args = parser.parse_args()
    check(args.symbol, args.days)
