#!/usr/bin/env python3
"""#30 descriptive TOP5 BOOK STOCK recovery after an observed decline, NOT order replenishment.

At each OB200 delta, sum price * visible quantity at the best five bid/ask
price keys. If stock declines, open at most one side episode with pre-decline
stock as target. A later DISTINCT event timestamp may recover within 60 s.
Snapshots censor active episodes. Same-timestamp alleged recovery is ambiguous
and censored; expired only when a later DELTA is observed beyond 60 s; an
unresolved day-end episode is right-censored (not an observed failure).
No order/participant identity, guaranteed feed continuity, or receiver clock.
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
ROOT = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
REPO = pathlib.Path("/srv/cripta/source_checkout")
DEST = pathlib.Path("/data/cripta/research/runs/r158_firstmonth_book30_top5_stock_recovery_20261023")
FORMULA = "ob200_top5_quote_stock_decline_later_ts_60s_recovery_v1"
FINISHED = "BOOK30_TOP5_STOCK_RECOVERY_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
FIELDS = ("opened", "recovered", "observed_expired", "snapshot_censored",
          "same_timestamp_ambiguous", "day_end_censored", "recovery_ms_total")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def new_json(path, data):
    tmp = path.with_name(path.name + ".part")
    with tmp.open("x", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.rename(path)


def terminal(path, state, result=None, error=None):
    orig = json.loads(path.read_text())
    if orig["status"] != "RUNNING" or state not in (FINISHED, "FAILED"):
        raise ValueError("INVALID_RUN_MANIFEST_TRANSITION")
    orig["status"] = state
    if state == FINISHED:
        if result is None:
            raise ValueError("NO_SUCCESS_RESULT")
        orig.update({"rows": result["rows"], "output_sha256": result["output_sha256"],
                     "independent_qc": "PENDING"})
    else:
        if error is None:
            raise ValueError("NO_FAILURE_EVIDENCE")
        orig["error_type"] = type(error).__name__
        orig["error_detail"] = str(error)[:220]
    tmp = path.with_name(path.name + ".terminal.part")
    with tmp.open("x", encoding="utf-8") as f:
        json.dump(orig, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def top5(book, bid):
    return sum((p * book[p] for p in sorted(book, reverse=bid)[:5]), Decimal(0))


def minute_bin():
    return {"messages": 0, "snapshots": 0, "deltas": 0,
            "bid": {x: 0 for x in FIELDS if x != "day_end_censored"},
            "ask": {x: 0 for x in FIELDS if x != "day_end_censored"}}


def physical_day(day, archive, symbol):
    midnight = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()) * 1000
    bins = [minute_bin() for _ in range(1440)]
    books = [{}, {}]
    last = [None, None]
    pending = [None, None]
    total = [{"opened": 0, "recovered": 0, "observed_expired": 0,
              "snapshot_censored": 0, "same_timestamp_ambiguous": 0,
              "day_end_censored": 0, "recovery_ms_total": 0} for _ in range(2)]
    first_type = prev_ts = None
    messages = snapshots = deltas = suffix = gaps_60s = 0
    with zipfile.ZipFile(archive) as z:
        member = day.isoformat() + "_" + symbol + "_ob200.data"
        if z.namelist() != [member]:
            raise ValueError("ZIP_MEMBER_IDENTITY_MISMATCH")
        with z.open(member) as stream:
            for line in stream:
                obj = json.loads(line)
                ts = int(obj["ts"])
                if prev_ts is not None and ts < prev_ts:
                    raise ValueError("BOOK_RAW_TIMESTAMP_NONMONOTONE")
                if prev_ts is not None and ts - prev_ts > 60000 and ts < midnight+86400000:
                    gaps_60s += 1
                prev_ts = ts
                if ts < midnight:
                    raise ValueError("BOOK_TIMESTAMP_BEFORE_UTC_DAY")
                if ts >= midnight + 86400000:
                    suffix += 1
                    if suffix > 1000:
                        raise ValueError("UNBOUNDED_NEXT_UTC_DAY_SUFFIX")
                    continue
                if suffix:
                    raise ValueError("REENTERED_UTC_DAY_AFTER_SUFFIX")
                b = bins[(ts - midnight) // 60000]
                kind = obj["type"]
                data = obj["data"]
                if first_type is None:
                    first_type = kind
                    if kind != "snapshot":
                        raise ValueError("BOOK_INITIAL_SNAPSHOT_REQUIRED")
                messages += 1
                b["messages"] += 1
                if kind == "snapshot":
                    snapshots += 1
                    b["snapshots"] += 1
                    for side, key in enumerate(("b", "a")):
                        if pending[side] is not None:
                            label = "bid" if side == 0 else "ask"
                            b[label]["snapshot_censored"] += 1
                            total[side]["snapshot_censored"] += 1
                            pending[side] = None
                        replacement = {}
                        for pstr, qstr in data[key]:
                            price, qty = Decimal(pstr), Decimal(qstr)
                            if not price.is_finite() or price <= 0 or not qty.is_finite() or qty < 0:
                                raise ValueError("INVALID_BOOK_SNAPSHOT_LEVEL")
                            if qty:
                                replacement[price] = qty
                        books[side] = replacement
                        last[side] = top5(replacement, side == 0)
                elif kind == "delta":
                    deltas += 1
                    b["deltas"] += 1
                    for side, key in enumerate(("b", "a")):
                        label = "bid" if side == 0 else "ask"
                        book = books[side]
                        for pstr, qstr in data[key]:
                            price, qty = Decimal(pstr), Decimal(qstr)
                            if not price.is_finite() or price <= 0 or not qty.is_finite() or qty < 0:
                                raise ValueError("INVALID_BOOK_DELTA_LEVEL")
                            if qty == 0:
                                book.pop(price, None)
                            else:
                                book[price] = qty
                        current = top5(book, side == 0)
                        active = pending[side]
                        if active is not None:
                            start_ts, target = active
                            elapsed = ts - start_ts
                            if elapsed > 60000:
                                b[label]["observed_expired"] += 1
                                total[side]["observed_expired"] += 1
                                pending[side] = None
                            elif current >= target:
                                if elapsed == 0:
                                    b[label]["same_timestamp_ambiguous"] += 1
                                    total[side]["same_timestamp_ambiguous"] += 1
                                else:
                                    b[label]["recovered"] += 1
                                    b[label]["recovery_ms_total"] += elapsed
                                    total[side]["recovered"] += 1
                                    total[side]["recovery_ms_total"] += elapsed
                                pending[side] = None
                        if pending[side] is None and last[side] is not None and current < last[side]:
                            pending[side] = (ts, last[side])
                            b[label]["opened"] += 1
                            total[side]["opened"] += 1
                        last[side] = current
                else:
                    raise ValueError("UNKNOWN_BOOK_MESSAGE")
    if first_type != "snapshot" or not snapshots or not messages:
        raise ValueError("INITIAL_DAY_BOOK_SNAPSHOT_UNVERIFIED")
    for side in (0, 1):
        total[side]["day_end_censored"] += int(pending[side] is not None)
        r = total[side]
        if r["opened"] != sum(r[x] for x in (
                "recovered", "observed_expired", "snapshot_censored",
                "same_timestamp_ambiguous", "day_end_censored")):
            raise ValueError("EPISODE_ACCOUNTING_PARTITION_FAILED")
    return bins, {
        "messages": messages, "snapshots": snapshots, "deltas": deltas,
        "next_day_suffix_messages": suffix, "gaps_gt_60s": gaps_60s,
        "first_type": first_type, "bid": total[0], "ask": total[1],
        "sequence_continuity_verified": False,
        "receiver_clock_verified": False,
    }


def main(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_IS_NOT_CRIPTA")
    if symbol not in COINS or days not in (2, 30):
        raise ValueError("UNAPPROVED_DAYS_OR_COIN")
    if not os.access(DEST.parent, os.W_OK | os.X_OK):
        raise PermissionError("DATA_RESEARCH_PARENT_UNWRITABLE")
    archive_days = []
    for i in range(days):
        day = FIRST + dt.timedelta(days=i)
        archive = ROOT / symbol / "orderbook" / (day.isoformat() + "_" + symbol + "_ob200.data.zip")
        if not archive.is_file() or not os.access(archive, os.R_OK):
            raise FileNotFoundError("SOURCE_ARCHIVE_NOT_READABLE " + str(archive))
        archive_days.append((day, archive))
    folder = DEST / f"physical_{days}d" / symbol
    if folder.exists():
        raise FileExistsError("IMMUTABLE_OUTPUT_EXISTS_DO_NOT_OVERWRITE")
    os.umask(0o007)
    DEST.mkdir(mode=0o2770, exist_ok=True)
    folder.parent.mkdir(mode=0o2770, exist_ok=True)
    folder.mkdir(mode=0o2770)
    (folder / "source_snapshot").mkdir(mode=0o2770)
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, folder / "source_snapshot/materializer.py")
    commit = (REPO / ".git/refs/heads/main").read_text().strip()
    program_sha = sha(script)
    (folder / "source_commit.txt").write_text(commit + "\n")
    (folder / "source_sha256.txt").write_text(program_sha + "\n")
    (folder / "command.txt").write_text(f"python3 -B {script} --symbol {symbol} --days {days}\n")
    (folder / "environment.txt").write_text(f"uid={os.geteuid()}\npython={sys.executable}\n")
    manifest = folder / "run_manifest.json"
    new_json(manifest, {"status": "RUNNING", "actor_uid": os.geteuid(),
                        "symbol": symbol, "days": days, "source_commit": commit,
                        "program_sha256": program_sha, "formula_version": FORMULA})
    try:
        artifact = folder / "features_1m.jsonl.gz"
        part = folder / "features_1m.jsonl.gz.part"
        counts = [{"opened": 0, "recovered": 0, "observed_expired": 0,
                   "snapshot_censored": 0, "same_timestamp_ambiguous": 0,
                   "day_end_censored": 0, "recovery_ms_total": 0} for _ in range(2)]
        provenance = []
        row_count = total_messages = total_deltas = 0
        started = time.monotonic()
        with part.open("xb") as f:
            with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as zipped:
                with io.TextIOWrapper(zipped, encoding="utf-8") as out:
                    for dayno, (day, source) in enumerate(archive_days, 1):
                        digest = sha(source)
                        bins, report = physical_day(day, source, symbol)
                        provenance.append({"date_utc": day.isoformat(),
                                           "source_path": str(source),
                                           "source_sha256": digest,
                                           "source_bytes": source.stat().st_size,
                                           "minute_rows": 1440, **report})
                        total_messages += report["messages"]
                        total_deltas += report["deltas"]
                        for s, label in enumerate(("bid", "ask")):
                            for key in counts[s]:
                                counts[s][key] += report[label][key]
                        for minute, b in enumerate(bins):
                            start = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(minutes=minute)
                            observed = b["deltas"] > 0
                            payload = {
                                "venue_id": "Bybit", "instrument_id": symbol,
                                "market_source_id": "BYBIT_OB200_ARCHIVE",
                                "indicator_id": 30, "formula_version": FORMULA,
                                "window_start_utc": start.isoformat(),
                                "derived_window_end_utc": (start+dt.timedelta(minutes=1)).isoformat(),
                                "observed_at_utc": None,
                                "verified_available_after_utc": None,
                                "book_messages": b["messages"],
                                "snapshot_messages": b["snapshots"],
                                "delta_messages": b["deltas"],
                                "quality": ("OBSERVED_TOP5_STOCK_EVENTS_ORDER_IDENTITY_UNVERIFIED"
                                            if observed else "NO_BOOK_DELTA_IN_MINUTE"),
                                "source_archive_sha256": digest,
                            }
                            for label in ("bid", "ask"):
                                for key in FIELDS:
                                    if key == "day_end_censored":
                                        continue
                                    payload[label + "_" + key] = b[label][key] if observed or (
                                        key == "snapshot_censored" and b["snapshots"] > 0) else None
                            out.write(json.dumps(payload, sort_keys=True) + "\n")
                            row_count += 1
                        print(json.dumps({"stage": "BOOK30_SOURCE_DAY", "symbol": symbol,
                                          "days_done": dayno, "days_total": days,
                                          "elapsed_s": round(time.monotonic()-started, 2),
                                          "heartbeat": True}), flush=True)
            f.flush()
            os.fsync(f.fileno())
        if row_count != 1440 * days:
            raise ValueError("UTC_MINUTE_GRID_INCOMPLETE")
        part.rename(artifact)
        new_json(folder / "input_provenance.json", {"days": provenance})
        result = {
            "status": FINISHED, "symbol": symbol, "days": days, "rows": row_count,
            "book_messages": total_messages, "book_delta_messages": total_deltas,
            "bid": counts[0], "ask": counts[1], "indicator_id": 30,
            "formula_version": FORMULA, "source_commit": commit,
            "program_sha256": program_sha, "output_sha256": sha(artifact),
            "recovered_same_level_or_order_verified": False,
            "continuous_60s_observation_verified": False,
            "book_sequence_continuity_verified": False,
            "receiver_clock_verified": False, "research_ready": False,
            "meaning": "descriptive Top5 quote-stock recovery at later observed timestamp; not verified order-level replenishment",
        }
        new_json(folder / "result.json", result)
        terminal(manifest, FINISHED, result)
        print(json.dumps({"status": FINISHED, "symbol": symbol, "rows": row_count,
                          "output_sha256": result["output_sha256"],
                          "output_dir": str(folder)}), flush=True)
    except Exception as e:
        if not (folder / "result.json").exists():
            try:
                terminal(manifest, "FAILED", error=e)
            except Exception as nested:
                print("MANIFEST_TERMINAL_WRITE_FAILED "+str(nested), file=sys.stderr)
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=COINS, required=True)
    p.add_argument("--days", type=int, choices=(2, 30), required=True)
    a = p.parse_args()
    main(a.symbol, a.days)
