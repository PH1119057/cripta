#!/usr/bin/env python3
"""#12 observed OB200 exact-price visible quantity DECREASE, NOT cancellations.

Each delta changes an existing exact price-key's visible size. When new size
is lower, retain quote-USDT size reduction by passive side. Snapshots replace
book state and never count as pulling. Per-minute zeros only when a delta was
actually observed; no-event minutes are NULL. Publication/stream continuity
and trade attribution are UNKNOWN; this is a separate descriptive proxy.
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
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
REPO = pathlib.Path("/srv/cripta/source_checkout")
OUTPUT = pathlib.Path("/data/cripta/research/runs/r158_firstmonth_book12_visible_decrease_20261023")
FORMULA = "ob200_exact_price_level_visible_quantity_decrease_minute_v1"
DONE = "BOOK12_VISIBLE_DECREASE_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"


def digest(file):
    h = hashlib.sha256()
    with file.open("rb") as f:
        for data in iter(lambda: f.read(1048576), b""):
            h.update(data)
    return h.hexdigest()


def write_new(path, value):
    tmp = path.with_name(path.name + ".part")
    with tmp.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    tmp.rename(path)


def final_manifest(path, status, evidence):
    old = json.loads(path.read_text())
    if old["status"] != "RUNNING" or status not in (DONE, "FAILED"):
        raise ValueError("INVALID_TERMINAL_STATE")
    old["status"] = status
    if status == DONE:
        old.update({"rows": evidence["rows"], "output_sha256": evidence["output_sha256"],
                    "independent_qc": "PENDING"})
    else:
        old["failure_type"] = type(evidence).__name__
        old["failure_detail"] = str(evidence)[:250]
    temp = path.with_name(path.name + ".terminal.part")
    with temp.open("x", encoding="utf-8") as f:
        json.dump(old, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def snapshots_and_deltas(day, archive, symbol):
    """Return complete observed-minute grid, isolated within original day."""
    midnight = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000)
    slots = [{
        "book_messages": 0, "snapshots": 0, "deltas": 0,
        "bid_decrease_events": 0, "ask_decrease_events": 0,
        "bid_visible_decrease_usdt": Decimal(0),
        "ask_visible_decrease_usdt": Decimal(0),
    } for _ in range(1440)]
    bids, asks = {}, {}
    initialized = False
    first_type = None
    prev_ts = None
    suffix = 0
    total_messages = snapshots = deltas = 0
    with zipfile.ZipFile(archive) as z:
        member = day.isoformat() + "_" + symbol + "_ob200.data"
        if z.namelist() != [member]:
            raise ValueError("ZIP_MEMBER_IDENTITY_MISMATCH")
        with z.open(member) as stream:
            for line in stream:
                event = json.loads(line)
                ts = int(event["ts"])
                if prev_ts is not None and ts < prev_ts:
                    raise ValueError("RAW_BOOK_TIME_NONMONOTONE")
                prev_ts = ts
                if ts < midnight:
                    raise ValueError("RAW_BOOK_BEFORE_DAY")
                if ts >= midnight + 86400000:
                    suffix += 1
                    if suffix > 1000:
                        raise ValueError("RAW_BOOK_SUFFIX_EXCEEDS_BOUND")
                    continue
                if suffix:
                    raise ValueError("RAW_BOOK_REENTERED_DAY_AFTER_SUFFIX")
                minute = (ts - midnight) // 60000
                item = slots[minute]
                kind, data = event["type"], event["data"]
                if first_type is None:
                    first_type = kind
                    if first_type != "snapshot":
                        raise ValueError("INITIAL_BOOK_STATE_NOT_SNAPSHOT")
                total_messages += 1
                item["book_messages"] += 1
                if kind == "snapshot":
                    bids.clear()
                    asks.clear()
                    for key, book in (("b", bids), ("a", asks)):
                        for price, amount in data[key]:
                            p, q = Decimal(price), Decimal(amount)
                            if not p.is_finite() or p <= 0 or not q.is_finite() or q < 0:
                                raise ValueError("RAW_BOOK_BAD_LEVEL")
                            if q:
                                book[p] = q
                    initialized = True
                    snapshots += 1
                    item["snapshots"] += 1
                elif kind == "delta":
                    if not initialized:
                        raise ValueError("DELTA_WITHOUT_SNAPSHOT")
                    deltas += 1
                    item["deltas"] += 1
                    for key, book, prefix in (("b", bids, "bid"), ("a", asks, "ask")):
                        for price, amount in data[key]:
                            p, q = Decimal(price), Decimal(amount)
                            if not p.is_finite() or p <= 0 or not q.is_finite() or q < 0:
                                raise ValueError("RAW_BOOK_BAD_LEVEL")
                            old = book.get(p, Decimal(0))
                            if old > q:
                                item[prefix + "_decrease_events"] += 1
                                item[prefix + "_visible_decrease_usdt"] += p * (old - q)
                            if q == 0:
                                book.pop(p, None)
                            else:
                                book[p] = q
                else:
                    raise ValueError("UNSUPPORTED_BOOK_MESSAGE")
    if first_type != "snapshot" or total_messages == 0 or snapshots == 0:
        raise ValueError("NO_VERIFIED_DAY_INITIAL_SNAPSHOT")
    return slots, {"messages": total_messages, "snapshots": snapshots,
                   "deltas": deltas, "source_suffix_messages": suffix,
                   "first_message_type": first_type,
                   "receiver_clock_verified": False, "sequence_continuity_verified": False}


def main(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("RESEARCH_ACTOR_NOT_CRIPTA")
    if symbol not in COINS or days not in (2, 30):
        raise ValueError("SCOPE_NOT_APPROVED")
    if not os.access(OUTPUT.parent, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_PARENT_NOT_WRITABLE")
    source_days = []
    for i in range(days):
        d = FIRST + dt.timedelta(days=i)
        file = RAW / symbol / "orderbook" / (d.isoformat() + "_" + symbol + "_ob200.data.zip")
        if not file.is_file() or not os.access(file, os.R_OK):
            raise FileNotFoundError("ORIGINAL_BOOK_ARCHIVE_UNREADABLE " + str(file))
        source_days.append((d, file))
    dest = OUTPUT / f"physical_{days}d" / symbol
    if dest.exists():
        raise FileExistsError("IMMUTABLE_OUTPUT_EXISTS_DO_NOT_OVERWRITE")
    os.umask(0o007)
    OUTPUT.mkdir(mode=0o2770, exist_ok=True)
    dest.parent.mkdir(mode=0o2770, exist_ok=True)
    dest.mkdir(mode=0o2770)
    (dest / "source_snapshot").mkdir(mode=0o2770)
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, dest / "source_snapshot/materializer.py")
    program_sha = digest(script)
    commit = (REPO / ".git/refs/heads/main").read_text().strip()
    (dest / "source_commit.txt").write_text(commit + "\n")
    (dest / "source_sha256.txt").write_text(program_sha + "\n")
    (dest / "command.txt").write_text(
        f"python3 -B {script} --symbol {symbol} --days {days}\n")
    (dest / "environment.txt").write_text(
        f"effective_uid={os.geteuid()}\npython={sys.executable}\n")
    manifest = dest / "run_manifest.json"
    write_new(manifest, {"status": "RUNNING", "symbol": symbol, "days": days,
                         "source_commit": commit, "formula_version": FORMULA,
                         "program_sha256": program_sha, "actor_uid": os.geteuid()})
    try:
        artifact = dest / "features_1m.jsonl.gz"
        tmp = dest / "features_1m.jsonl.gz.part"
        evidence, nrows, observed, total_drop = [], 0, 0, Decimal(0)
        start = time.monotonic()
        with tmp.open("xb") as binary:
            with gzip.GzipFile(fileobj=binary, mode="wb", mtime=0) as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8") as writer:
                    for i, (day, source) in enumerate(source_days, 1):
                        source_hash = digest(source)
                        records, stats = snapshots_and_deltas(day, source, symbol)
                        for minute, data in enumerate(records):
                            observed += int(data["deltas"] > 0)
                            total_drop += data["bid_visible_decrease_usdt"]
                            total_drop += data["ask_visible_decrease_usdt"]
                            timestamp = dt.datetime.combine(day, dt.time(), dt.UTC)
                            timestamp += dt.timedelta(minutes=minute)
                            ready = data["deltas"] > 0
                            row = {
                                "venue_id": "Bybit", "instrument_id": symbol,
                                "market_source_id": "BYBIT_OB200_ARCHIVE",
                                "indicator_id": 12, "formula_version": FORMULA,
                                "window_start_utc": timestamp.isoformat(),
                                "derived_window_end_utc": (
                                    timestamp + dt.timedelta(minutes=1)).isoformat(),
                                "observed_at_utc": None,
                                "verified_available_after_utc": None,
                                "book_messages": data["book_messages"],
                                "book_snapshot_messages": data["snapshots"],
                                "book_delta_messages": data["deltas"],
                                "visible_bid_decrease_count": (
                                    data["bid_decrease_events"] if ready else None),
                                "visible_ask_decrease_count": (
                                    data["ask_decrease_events"] if ready else None),
                                "visible_bid_decrease_usdt": (
                                    str(data["bid_visible_decrease_usdt"]) if ready else None),
                                "visible_ask_decrease_usdt": (
                                    str(data["ask_visible_decrease_usdt"]) if ready else None),
                                "quality": ("OBSERVED_EXACT_PRICE_VISIBLE_DECREASE_CAUSE_UNKNOWN"
                                            if ready else "NO_BOOK_DELTA_IN_MINUTE"),
                                "source_archive_sha256": source_hash,
                            }
                            writer.write(json.dumps(row, sort_keys=True) + "\n")
                            nrows += 1
                        evidence.append({
                            "date_utc": day.isoformat(), "source_path": str(source),
                            "source_sha256": source_hash, "source_bytes": source.stat().st_size,
                            "minute_rows": 1440, **stats,
                        })
                        print(json.dumps({"stage": "BOOK12_PHYSICAL", "symbol": symbol,
                                          "days_done": i, "days_total": days,
                                          "elapsed_s": round(time.monotonic()-start, 2),
                                          "heartbeat": True}), flush=True)
            binary.flush()
            os.fsync(binary.fileno())
        if nrows != days * 1440:
            raise ValueError("INCOMPLETE_UTC_MINUTE_GRID")
        tmp.rename(artifact)
        write_new(dest / "input_provenance.json", {"days": evidence})
        result = {
            "status": DONE, "symbol": symbol, "days": days, "rows": nrows,
            "observed_delta_minutes": observed,
            "no_delta_minutes": nrows - observed,
            "visible_decrease_usdt_all_sides": str(total_drop),
            "book_messages": sum(x["messages"] for x in evidence),
            "book_delta_messages": sum(x["deltas"] for x in evidence),
            "formula_version": FORMULA, "indicator_id": 12,
            "source_commit": commit, "program_sha256": program_sha,
            "output_sha256": digest(artifact),
            "pull_or_cancel_verified": False,
            "book_sequence_continuity_verified": False,
            "receiver_clock_verified": False, "research_ready": False,
            "note": "Observed same-price orderbook visible size decrease only; fills, cancels and publication lag cannot be separated",
        }
        write_new(dest / "result.json", result)
        final_manifest(manifest, DONE, result)
        print(json.dumps({"status": DONE, "symbol": symbol,
                          "rows": nrows, "output_sha256": result["output_sha256"],
                          "output_dir": str(dest)}), flush=True)
    except Exception as exc:
        if not (dest / "result.json").exists():
            try:
                final_manifest(manifest, "FAILED", exc)
            except Exception as error:
                print("FINAL_MANIFEST_FAILED "+str(error), file=sys.stderr)
        raise


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("--symbol", choices=COINS, required=True)
    a.add_argument("--days", type=int, choices=(2, 30), required=True)
    options = a.parse_args()
    main(options.symbol, options.days)
