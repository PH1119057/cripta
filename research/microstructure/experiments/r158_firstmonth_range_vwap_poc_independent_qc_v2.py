#!/usr/bin/env python3
"""Independent full 1m Decimal QC of first-month #19 and #22 materialization.

Re-reads all source trades and output rows; no second-month reads. Checks exact
source SHA, minute close, 20/20m ratio, UTC-day VWAP, exact-price POC, nulls.
Writes a separate immutable QC receipt to the scoped research result folder.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import shutil
from collections import defaultdict, deque
from decimal import Decimal

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
ROOT = pathlib.Path("/data/cripta/research/runs/microstructure_firstmonth_price19_22_20261023")
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
START = dt.date(2026, 5, 18)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for piece in iter(lambda: f.read(1048576), b""):
            h.update(piece)
    return h.hexdigest()


def aggregate_raw(symbol, day):
    source = RAW / symbol / "public_trades" / (
        symbol + day.isoformat() + ".csv.gz"
    )
    start = Decimal(int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()))
    groups = [[] for _ in range(1440)]
    last_ts = None
    with gzip.open(source, "rt", newline="") as f:
        for row in csv.DictReader(f):
            ts = Decimal(row["timestamp"])
            if last_ts is not None and ts < last_ts:
                raise ValueError("RAW_ORDER")
            last_ts = ts
            minute = int((ts - start) // 60)
            if not 0 <= minute < 1440:
                raise ValueError("RAW_OUTSIDE_DAY")
            if row["symbol"] != symbol:
                raise ValueError("RAW_SYMBOL")
            p, q = Decimal(row["price"]), Decimal(row["size"])
            foreign = Decimal(row["foreignNotional"])
            if p <= 0 or q <= 0 or abs(p * q - foreign) > Decimal("0.000001"):
                raise ValueError("RAW_SIZE_PARITY")
            groups[minute].append((p, q))
    return groups, source


def compare_exact(original, stored, feature, idx):
    actual = Decimal(stored) if stored is not None else None
    if actual != original:
        raise ValueError(
            "QC_MISMATCH " + feature + " minute=" + str(idx)
            + " expected=" + str(original) + " actual=" + str(actual)
        )


def qc(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("SCOPE_INVALID")
    target = ROOT / ("physical_" + str(days) + "d") / symbol
    result = json.loads((target / "result.json").read_text())
    if (
        result["status"] != "FIRST_MONTH_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
        or result["days"] != days
        or result["symbols"] != [symbol]
        or result["minute_rows"] != days * 1440
    ):
        raise ValueError("PRIMARY_RESULT_STATE")
    outfile = target / "features.jsonl.gz"
    if result["file_sha256"] != sha(outfile):
        raise ValueError("PRIMARY_OUTPUT_SHA_CHANGED")
    if result["program_sha256"] != sha(target / "source_snapshot/materializer.py"):
        raise ValueError("PRIMARY_PROGRAM_SHA_CHANGED")
    final = target / "independent_qc_report.json"
    if final.exists():
        raise FileExistsError("NO_QC_OVERWRITE")
    history = deque(maxlen=40)
    day_stats = []
    total_rows = total_fills = 0
    nulls = {"19": 0, "22": 0}
    with gzip.open(outfile, "rt") as saved:
        for index in range(days):
            day = START + dt.timedelta(days=index)
            minutes, src = aggregate_raw(symbol, day)
            source_sha = sha(src)
            prior = result["source_days"][index]
            if prior["day"] != day.isoformat() or prior["sha256"] != source_sha:
                raise ValueError("SOURCE_HASH_MISMATCH")
            profile = defaultdict(Decimal)
            cumq = cumcost = Decimal(0)
            day_fills = 0
            for i, prints in enumerate(minutes):
                line = saved.readline()
                if not line:
                    raise ValueError("TRUNCATED_ROWS")
                record = json.loads(line)
                stamp = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(minutes=i)
                if (
                    record["instrument_id"] != symbol
                    or record["window_start_utc"] != stamp.isoformat()
                    or record["derived_window_end_utc"]
                    != (stamp + dt.timedelta(minutes=1)).isoformat()
                    or record["source_archive_sha256"] != source_sha
                    or record["observed_at_utc"] is not None
                    or record["verified_available_after_utc"] is not None
                ):
                    raise ValueError("ROW_TIME_OR_SOURCE_MISMATCH " + str(total_rows))
                close = prints[-1][0] if prints else None
                history.append(close)
                expected_ratio = expected_prev = expected_now = None
                if len(history) == 40 and None not in history:
                    prices = list(history)
                    expected_prev = max(prices[:20]) - min(prices[:20])
                    expected_now = max(prices[20:]) - min(prices[20:])
                    if expected_prev > 0:
                        expected_ratio = expected_now / expected_prev
                for p, q in prints:
                    cumq += q
                    cumcost += p * q
                    profile[p] += q
                vwap = cumcost / cumq if cumq else None
                poc = min(profile, key=lambda x: (-profile[x], x)) if profile else None
                compare_exact(
                    expected_ratio, record["factor_19_range_compression_ratio"],
                    "19_ratio", total_rows,
                )
                compare_exact(
                    expected_prev, record["factor_19_previous20_close_range_quote"],
                    "19_previous", total_rows,
                )
                compare_exact(
                    expected_now, record["factor_19_current20_close_range_quote"],
                    "19_current", total_rows,
                )
                compare_exact(
                    vwap, record["factor_22_running_vwap_quote_per_base"],
                    "22_vwap", total_rows,
                )
                compare_exact(
                    poc, record["factor_22_running_poc_exact_price"],
                    "22_poc", total_rows,
                )
                compare_exact(
                    cumq, record["factor_22_daily_running_base_volume"],
                    "22_qty", total_rows,
                )
                compare_exact(close, record["price_close"], "close", total_rows)
                if record["input_fill_count"] != len(prints):
                    raise ValueError("MINUTE_FILL_COUNT")
                if record["quality"] != ("OBSERVED_EXCHANGE_TAPE" if prints else "NO_DATA"):
                    raise ValueError("NO_DATA_NOT_PRESERVED")
                nulls["19"] += int(expected_ratio is None)
                nulls["22"] += int(vwap is None)
                day_fills += len(prints)
                total_rows += 1
            if day_fills != prior["fills"]:
                raise ValueError("DAY_FILLS")
            total_fills += day_fills
            day_stats.append({"day": day.isoformat(), "fills": day_fills})
            print(json.dumps({
                "qc_symbol": symbol, "qc_done_days": index + 1,
                "qc_total_days": days, "heartbeat": True,
            }), flush=True)
        if saved.readline():
            raise ValueError("EXTRA_ROWS")
    if total_fills != result["raw_fills"] or total_rows != result["minute_rows"]:
        raise ValueError("FULL_QC_COUNT")
    receipt = {
        "status": "ALL_MINUTES_DECIMAL_QC_PASS_NOT_RESEARCH_READY",
        "symbol": symbol, "days": days, "rows": total_rows,
        "raw_fills": total_fills, "nulls": nulls,
        "original_sha256": result["file_sha256"],
        "day_checks": day_stats,
        "source_receiver_clock": "UNKNOWN",
    }
    code = pathlib.Path(__file__).resolve()
    shutil.copyfile(code, target / "source_snapshot" / "independent_checker.py")
    receipt["checker_sha256"] = sha(code)
    tmp = final.with_suffix(".json.part")
    with tmp.open("x", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.rename(final)
    print(json.dumps({"status": receipt["status"], "symbol": symbol,
                      "days": days, "rows": total_rows, "raw_fills": total_fills,
                      "receipt": str(final), "receipt_sha256": sha(final)}))


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("--symbol", required=True, choices=SYMS)
    a.add_argument("--days", required=True, type=int, choices=(2, 30))
    opts = a.parse_args()
    qc(opts.symbol, opts.days)
