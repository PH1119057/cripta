#!/usr/bin/env python3
"""Independent Decimal full-row QC of observed Bybit ob200 book features.

Checks 2/30 UTC days from original ZIP bytes and per-minute source event
states. Writes immutable QC receipt under materializer's research-only folder.
Not a venue-level proof of message completeness or receiver availability.
"""
import argparse
import datetime as dt
import decimal
import gzip
import hashlib
import json
import math
import os
import pathlib
import shutil
import zipfile

D = decimal.Decimal
SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
ROOT = pathlib.Path("/data/cripta/research/runs/microstructure_firstmonth_book9_11_29_20261023")
FIRST = dt.date(2026, 5, 18)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for piece in iter(lambda: f.read(1048576), b""):
            h.update(piece)
    return h.hexdigest()


def moment(bid, ask, ts, end):
    if not bid or not ask:
        raise ValueError("QC_BOOK_SIDE_EMPTY")
    bb, aa = max(bid), min(ask)
    if bb >= aa:
        raise ValueError("QC_BOOK_CROSSED")
    mid = (bb + aa) / 2
    distance = D("0.0005")
    bd = sum((
        p * q for p, q in bid.items()
        if mid * (1 - distance) <= p <= mid
    ), D(0))
    ad = sum((
        p * q for p, q in ask.items()
        if mid <= p <= mid * (1 + distance)
    ), D(0))
    return {
        "factor_9_near_mid_queue_imbalance": (
            (bd - ad) / (bd + ad) if bd + ad > 0 else None
        ),
        "factor_11_bid_depth_quote_5bps": bd,
        "factor_11_ask_depth_quote_5bps": ad,
        "factor_29_spread_bps": (aa - bb) / mid * D(10000),
        "best_bid_quote": bb,
        "best_ask_quote": aa,
        "latest_book_event_ts_ms": ts,
        "book_age_at_minute_end_ms": end - ts,
    }


def read_book_day(symbol, day, path):
    t0 = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000)
    states = [None] * 1440
    bids, asks = {}, {}
    has_snapshot = False
    last_ts = last_seq = last_u = last_index = None
    count = {"snapshot": 0, "delta": 0, "processed": 0}
    gaps = 0

    def close():
        if last_index is not None and has_snapshot:
            states[last_index] = moment(
                bids, asks, last_ts, t0 + (last_index + 1) * 60000
            )

    with zipfile.ZipFile(path) as archive:
        members = [x for x in archive.namelist() if x.endswith(".data")]
        if len(members) != 1:
            raise ValueError("QC_ZIP_SCHEMA")
        with archive.open(members[0]) as source:
            for raw in source:
                rec = json.loads(raw)
                ts = int(rec["ts"])
                idx = (ts - t0) // 60000
                if not 0 <= idx < 1440:
                    raise ValueError("QC_EVENT_TIME")
                if last_ts is not None and ts < last_ts:
                    raise ValueError("QC_TIME_REVERSAL")
                if last_index != idx:
                    close()
                    last_index = idx
                last_ts = ts
                data = rec["data"]
                if data.get("s") != symbol:
                    raise ValueError("QC_SYMBOL")
                seq, u = int(data["seq"]), int(data["u"])
                if rec["type"] == "snapshot":
                    bids = {D(p): D(q) for p, q in data["b"] if D(q) > 0}
                    asks = {D(p): D(q) for p, q in data["a"] if D(q) > 0}
                    has_snapshot = True
                    last_seq = last_u = None
                    count["snapshot"] += 1
                elif rec["type"] == "delta":
                    if not has_snapshot:
                        continue
                    for changes, book in ((data["b"], bids), (data["a"], asks)):
                        for pr, qu in changes:
                            p, q = D(pr), D(qu)
                            if q < 0:
                                raise ValueError("QC_NEGATIVE_BOOK_QUANTITY")
                            if q == 0:
                                book.pop(p, None)
                            else:
                                book[p] = q
                    count["delta"] += 1
                else:
                    raise ValueError("QC_BOOK_EVENT_TYPE")
                if last_seq is not None and seq <= last_seq:
                    raise ValueError("QC_SEQ_REVERSAL")
                if last_u is not None:
                    if u <= last_u:
                        raise ValueError("QC_U_REVERSAL")
                    gaps += int(u > last_u + 1)
                last_seq, last_u = seq, u
                count["processed"] += 1
        close()
    count["update_id_gap_events"] = gaps
    return states, count


def same(expected, observed, name, minute):
    if expected is None and observed is None:
        return
    if expected is None or observed is None:
        raise ValueError("QC_NULL_MISMATCH_" + name + "_" + str(minute))
    if not math.isclose(float(expected), float(observed), rel_tol=1e-9, abs_tol=1e-6):
        raise ValueError(
            "QC_NUMERIC_MISMATCH_" + name + "_" + str(minute)
            + " expected=" + str(expected) + " stored=" + str(observed)
        )


def verify(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("QC_NOT_ACTOR_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("QC_WRONG_SCOPE")
    target = ROOT / ("physical_" + str(days) + "d") / symbol
    result = json.loads((target / "result.json").read_text())
    if (
        result["status"] != "BOOK_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
        or result["symbol"] != symbol or result["days"] != days
        or result["minute_rows"] != days * 1440
    ):
        raise ValueError("QC_PRIMARY_RESULT_STATUS")
    output = target / "book_features.jsonl.gz"
    if sha(output) != result["data_sha256"]:
        raise ValueError("QC_PRIMARY_OUTPUT_HASH")
    if sha(target / "source_snapshot/book_materializer.py") != result["code_sha256"]:
        raise ValueError("QC_PRIMARY_PROGRAM_HASH")
    dest = target / "independent_qc_report.json"
    if dest.exists():
        raise FileExistsError("QC_ALREADY_DONE")
    totals = {"days": 0, "rows": 0, "observed": 0, "events": 0}
    daily = []
    with gzip.open(output, "rt") as saved:
        for i in range(days):
            day = FIRST + dt.timedelta(days=i)
            source = RAW / symbol / "orderbook" / (
                day.isoformat() + "_" + symbol + "_ob200.data.zip"
            )
            prior = result["source_files"][i]
            source_hash = sha(source)
            if prior["day"] != day.isoformat() or prior["source_sha256"] != source_hash:
                raise ValueError("QC_SOURCE_HASH_DRIFT")
            states, counts = read_book_day(symbol, day, source)
            valid = 0
            for minute, actual in enumerate(states):
                line = saved.readline()
                if not line:
                    raise ValueError("QC_OUTPUT_TRUNCATED")
                rec = json.loads(line)
                stamp = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(
                    minutes=minute
                )
                if (
                    rec["instrument_id"] != symbol
                    or rec["window_start_utc"] != stamp.isoformat()
                    or rec["derived_window_end_utc"] != (
                        stamp + dt.timedelta(minutes=1)
                    ).isoformat()
                    or rec["source_sha256"] != source_hash
                    or rec["observed_at_utc"] is not None
                    or rec["verified_available_after_utc"] is not None
                ):
                    raise ValueError("QC_TIME_OR_ID")
                if actual is None:
                    if rec["quality"] != "NO_DATA":
                        raise ValueError("QC_MISSING_BOOK_NOT_MARKED_NO_DATA")
                else:
                    if rec["quality"] != "OBSERVED_BOOK_EVENT_MINUTE":
                        raise ValueError("QC_OBSERVED_BOOK_QUALITY")
                    for key in actual:
                        same(actual[key], rec[key], key, totals["rows"])
                    valid += 1
                totals["rows"] += 1
            if valid != prior["minute_states"] or counts["processed"] != prior[
                "source_stats"
            ]["processed"]:
                raise ValueError("QC_DAILY_COUNTS")
            totals["days"] += 1
            totals["events"] += counts["processed"]
            totals["observed"] += valid
            daily.append({
                "day": day.isoformat(),
                "rows": 1440, "observed": valid, "events": counts["processed"],
                "source_sha256": source_hash,
            })
            print(json.dumps({
                "qc_symbol": symbol, "days_done": i + 1,
                "days_total": days, "heartbeat": True,
            }), flush=True)
        if saved.readline():
            raise ValueError("QC_EXTRA_ROWS")
    if totals["events"] != result["sum_source_events"]:
        raise ValueError("QC_ALL_SOURCE_EVENT_COUNT")
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, target / "source_snapshot/book_independent_qc.py")
    receipt = {
        "status": "ALL_BOOK_OBSERVED_MINUTES_DECIMAL_QC_PASS_NOT_RESEARCH_READY",
        "symbol": symbol, "scope_days": days,
        "totals": totals, "per_day": daily,
        "book_feature_sha256": result["data_sha256"],
        "checker_sha256": sha(script),
        "receipt_clock": "UNKNOWN",
        "source_completeness": "NOT_PROVEN_ONLY_ARCHIVED_EVENTS",
    }
    tmp = dest.with_suffix(".json.part")
    with tmp.open("x") as f:
        json.dump(receipt, f, sort_keys=True, indent=2)
        f.write("\n")
    tmp.rename(dest)
    print(json.dumps({
        "status": receipt["status"], "symbol": symbol,
        "days": days, "observed": totals["observed"],
        "events": totals["events"], "receipt_sha256": sha(dest),
    }))


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("--symbol", required=True, choices=SYMS)
    a.add_argument("--days", required=True, type=int, choices=(2, 30))
    args = a.parse_args()
    verify(args.symbol, args.days)
