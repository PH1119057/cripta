#!/usr/bin/env python3
"""#21 VERSIONED trade-print excursion/re-entry at STRICTLY LATER timestamps.

Physical description: current 1m prints exceed extrema over 60 strictly
preceding, nonempty complete minutes; later timestamp group inside CURRENT
minute prints back within prior boundary. No claim of order-book liquidity
sweep, verified fill order within tied timestamps, or real market reception.
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
from collections import deque
from decimal import Decimal

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
REPO = pathlib.Path("/srv/cripta/source_checkout")
MANIFEST = REPO / "research/microstructure/TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
OUT = pathlib.Path("/data/cripta/research/runs/r158_firstmonth_print21_distinct_timestamp_20261023")
FORMULA = "print21_prior60_1m_trade_extrema_distinct_timestamp_reentry_v1"
COMPLETE = "PRINT21_DISTINCT_TS_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"


def sha(path):
    d = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1048576), b""):
            d.update(part)
    return d.hexdigest()


def json_new(path, data):
    part = path.with_name(path.name + ".part")
    with part.open("x", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    part.rename(path)


def terminal(path, status, evidence):
    obj = json.loads(path.read_text())
    if obj["status"] != "RUNNING" or status not in (COMPLETE, "FAILED"):
        raise ValueError("INVALID_RUN_MANIFEST_TRANSITION")
    obj["status"] = status
    if status == COMPLETE:
        obj["rows"] = evidence["rows"]
        obj["output_sha256"] = evidence["output_sha256"]
        obj["independent_qc"] = "PENDING"
    else:
        obj["failure_type"] = type(evidence).__name__
        obj["failure_message"] = str(evidence)[:240]
    part = path.with_name(path.name + ".terminal.part")
    with part.open("x", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, path)


def source_files(symbol, days):
    all_sources = json.loads(MANIFEST.read_text())["source_files"]
    scoped = [x for x in all_sources if x["symbol"] == symbol]
    lookup = {x["date_utc"]: x for x in scoped}
    if len(scoped) != 30 or len(lookup) != 30:
        raise ValueError("EXPECTED_30_DAY_SOURCE_MANIFEST")
    result = []
    for offset in range(days):
        day = FIRST + dt.timedelta(days=offset)
        source = RAW / symbol / "public_trades" / (symbol + day.isoformat() + ".csv.gz")
        if not source.is_file() or not os.access(source, os.R_OK):
            raise FileNotFoundError("RAW_ARCHIVE_UNREADABLE " + str(source))
        result.append((day, source, lookup[day.isoformat()]))
    return result


def grouped_minutes(day, source):
    """Timestamp-group min/max: no assumptions about order inside a tie group."""
    start = Decimal(int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()))
    rows = [{"groups": [], "fills": 0, "ties": 0, "multi_price_ties": 0}
            for _ in range(1440)]
    prev_timestamp = None
    with gzip.open(source, "rt", newline="") as f:
        for r in csv.DictReader(f):
            timestamp = Decimal(r["timestamp"])
            price = Decimal(r["price"])
            if not timestamp.is_finite() or not price.is_finite() or price <= 0:
                raise ValueError("INVALID_RAW_TIMESTAMP_OR_PRICE")
            if prev_timestamp is not None and timestamp < prev_timestamp:
                raise ValueError("NONMONOTONE_RAW_TRADE_TAPE")
            prev_timestamp = timestamp
            minute = int((timestamp - start) // 60)
            if not 0 <= minute < 1440:
                raise ValueError("TRADE_OUTSIDE_SOURCE_UTC_DAY")
            slot = rows[minute]
            slot["fills"] += 1
            groups = slot["groups"]
            if groups and groups[-1][0] == timestamp:
                group = groups[-1]
                group[1] = min(group[1], price)
                group[2] = max(group[2], price)
                group[3] += 1
            else:
                groups.append([timestamp, price, price, 1])
    return rows


def evaluate(history, current):
    """History holds exact last 60 completed 1m slots, across UTC midnight."""
    if current["fills"] == 0:
        return None, "NO_CURRENT_PRINT"
    if len(history) != 60:
        return None, "INSUFFICIENT_PRIOR60_MINUTES"
    if any(x[0] == 0 for x in history):
        return None, "PRIOR60_CONTAINS_NO_PRINT_MINUTE"
    high = max(x[2] for x in history)
    low = min(x[1] for x in history)
    seen_high = seen_low = False
    returned_high = returned_low = False
    ambiguous_high = ambiguous_low = False
    ties = multiprice_ties = 0
    current_hi = current_lo = None
    for timestamp, group_low, group_high, n in current["groups"]:
        current_hi = group_high if current_hi is None else max(current_hi, group_high)
        current_lo = group_low if current_lo is None else min(current_lo, group_low)
        if n > 1:
            ties += 1
            multiprice_ties += int(group_low != group_high)
        if group_low <= high < group_high:
            ambiguous_high = True
        if group_low < low <= group_high:
            ambiguous_low = True
        # Only earlier timestamp groups can establish a crossing for this test.
        if seen_high and group_low <= high:
            returned_high = True
        if seen_low and group_high >= low:
            returned_low = True
        seen_high |= group_high > high
        seen_low |= group_low < low
    return {
        "prior60_high": high, "prior60_low": low,
        "current_print_high": current_hi, "current_print_low": current_lo,
        "high_crossed": seen_high, "low_crossed": seen_low,
        "high_reentry_later_timestamp": returned_high,
        "low_reentry_later_timestamp": returned_low,
        "high_same_timestamp_ambiguous": ambiguous_high,
        "low_same_timestamp_ambiguous": ambiguous_low,
        "tied_timestamp_groups": ties,
        "multiprice_tied_groups": multiprice_ties,
    }, "OBSERVED_PRIOR60_AND_CURRENT_PRINTS"


def run(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("RESEARCH_ACTOR_MUST_BE_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("SCOPE_NOT_ALLOWED")
    if not os.access(OUT.parent, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_PARENT_UNWRITABLE")
    files = source_files(symbol, days)
    target = OUT / f"physical_{days}d" / symbol
    if target.exists():
        raise FileExistsError("IMMUTABLE_OUTPUT_EXISTS_DO_NOT_OVERWRITE")
    os.umask(0o007)
    OUT.mkdir(mode=0o2770, exist_ok=True)
    target.parent.mkdir(mode=0o2770, exist_ok=True)
    target.mkdir(mode=0o2770)
    (target / "source_snapshot").mkdir(mode=0o2770)
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, target / "source_snapshot/materializer.py")
    code_sha = sha(script)
    commit = (REPO / ".git/refs/heads/main").read_text().strip()
    (target / "source_commit.txt").write_text(commit + "\n")
    (target / "source_sha256.txt").write_text(code_sha + "\n")
    (target / "command.txt").write_text(
        f"python3 -B {script} --symbol {symbol} --days {days}\n")
    manifest = target / "run_manifest.json"
    json_new(manifest, {"status": "RUNNING", "symbol": symbol, "days": days,
                        "actor_uid": os.geteuid(), "source_commit": commit,
                        "program_sha256": code_sha, "formula_version": FORMULA})
    try:
        temp = target / "features_1m.jsonl.gz.part"
        output = target / "features_1m.jsonl.gz"
        history = deque(maxlen=60)
        provenance = []
        counts = {"eligible": 0, "high_crossed": 0, "high_reentry": 0,
                  "low_crossed": 0, "low_reentry": 0, "high_ambiguous": 0,
                  "low_ambiguous": 0, "null": 0, "tied_groups": 0}
        total_fills = 0
        rows = 0
        with temp.open("xb") as f:
            with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
                with io.TextIOWrapper(gz, encoding="utf-8") as writer:
                    for i, (day, path, known) in enumerate(files, 1):
                        digest = sha(path)
                        if (digest != known["compressed_sha256"]
                                or path.stat().st_size != known["compressed_bytes"]):
                            raise ValueError("RAW_SOURCE_SHA_OR_SIZE_DRIFT")
                        slots = grouped_minutes(day, path)
                        fills = sum(x["fills"] for x in slots)
                        total_fills += fills
                        provenance.append({"date_utc": day.isoformat(),
                                           "source_path": str(path),
                                           "source_sha256": digest,
                                           "source_bytes": path.stat().st_size,
                                           "fills": fills, "minute_rows": 1440})
                        for minute, slot in enumerate(slots):
                            value, quality = evaluate(history, slot)
                            if value is None:
                                counts["null"] += 1
                            else:
                                counts["eligible"] += 1
                                for name, field in [
                                    ("high_crossed", "high_crossed"),
                                    ("high_reentry", "high_reentry_later_timestamp"),
                                    ("low_crossed", "low_crossed"),
                                    ("low_reentry", "low_reentry_later_timestamp"),
                                    ("high_ambiguous", "high_same_timestamp_ambiguous"),
                                    ("low_ambiguous", "low_same_timestamp_ambiguous"),
                                ]:
                                    counts[name] += int(value[field])
                                counts["tied_groups"] += value["tied_timestamp_groups"]
                            start = dt.datetime.combine(
                                day, dt.time(), dt.UTC) + dt.timedelta(minutes=minute)
                            payload = {
                                "venue_id": "Bybit", "instrument_id": symbol,
                                "market_source_id": "BYBIT_PUBLIC_TRADES_ARCHIVE",
                                "indicator_id": 21, "formula_version": FORMULA,
                                "window_start_utc": start.isoformat(),
                                "derived_window_end_utc": (
                                    start + dt.timedelta(minutes=1)).isoformat(),
                                "observed_at_utc": None,
                                "verified_available_after_utc": None,
                                "fill_count": slot["fills"],
                                "quality": quality,
                                "prior60_trade_print_high": (
                                    str(value["prior60_high"]) if value else None),
                                "prior60_trade_print_low": (
                                    str(value["prior60_low"]) if value else None),
                                "current_trade_print_high": (
                                    str(value["current_print_high"]) if value else None),
                                "current_trade_print_low": (
                                    str(value["current_print_low"]) if value else None),
                                "high_trade_print_cross": (
                                    value["high_crossed"] if value else None),
                                "low_trade_print_cross": (
                                    value["low_crossed"] if value else None),
                                "high_reentry_later_timestamp": (
                                    value["high_reentry_later_timestamp"] if value else None),
                                "low_reentry_later_timestamp": (
                                    value["low_reentry_later_timestamp"] if value else None),
                                "high_same_timestamp_ambiguous": (
                                    value["high_same_timestamp_ambiguous"] if value else None),
                                "low_same_timestamp_ambiguous": (
                                    value["low_same_timestamp_ambiguous"] if value else None),
                                "tied_timestamp_groups": (
                                    value["tied_timestamp_groups"] if value else None),
                                "multiprice_tied_groups": (
                                    value["multiprice_tied_groups"] if value else None),
                                "source_archive_sha256": digest,
                            }
                            writer.write(json.dumps(payload, sort_keys=True) + "\n")
                            if slot["fills"]:
                                minimum = min(g[1] for g in slot["groups"])
                                maximum = max(g[2] for g in slot["groups"])
                                history.append((slot["fills"], minimum, maximum))
                            else:
                                history.append((0, None, None))
                            rows += 1
                        print(json.dumps({"stage": "PRINT21_PHYSICAL", "symbol": symbol,
                                          "days_done": i, "days_total": days,
                                          "heartbeat": True}), flush=True)
            f.flush()
            os.fsync(f.fileno())
        if rows != 1440 * days:
            raise ValueError("FULL_UTC_MINUTE_GRID_EXPECTED")
        temp.rename(output)
        json_new(target / "input_provenance.json", {"days": provenance})
        result = {"status": COMPLETE, "symbol": symbol, "days": days,
                  "rows": rows, "fills": total_fills,
                  "counts": counts, "indicator_id": 21,
                  "formula_version": FORMULA, "source_commit": commit,
                  "program_sha256": code_sha, "source_manifest_sha256": sha(MANIFEST),
                  "output_sha256": sha(output),
                  "real_orderbook_sweep_verified": False,
                  "within_tied_timestamp_order_verified": False,
                  "receiver_clock_verified": False,
                  "source_continuity_verified": False,
                  "research_ready": False}
        json_new(target / "result.json", result)
        terminal(manifest, COMPLETE, result)
        print(json.dumps({"status": COMPLETE, "symbol": symbol,
                          "rows": rows, "output_sha256": result["output_sha256"],
                          "output_dir": str(target)}), flush=True)
    except Exception as exc:
        if not (target / "result.json").exists():
            try:
                terminal(manifest, "FAILED", exc)
            except Exception as nested:
                print("TERMINAL_WRITE_FAILED " + str(nested))
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=SYMS, required=True)
    p.add_argument("--days", choices=(2, 30), type=int, required=True)
    a = p.parse_args()
    run(a.symbol, a.days)
