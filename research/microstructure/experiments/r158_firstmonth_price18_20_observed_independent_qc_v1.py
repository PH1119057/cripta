#!/usr/bin/env python3
"""Independent 30-day full-minute raw replay for #18/#20 observed-only variants.

Separate source parser, mathematically different covariance formula.
Numerical equality does NOT prove source completeness or receiver timestamps.
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
from collections import deque
from decimal import Decimal, localcontext

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
OUT = pathlib.Path(
    "/data/cripta/research/runs/r158_firstmonth_price18_20_observed_20261023"
)
F18 = "price18_lag1_logret_prev60_consecutive_trade_minutes_v1"
F20 = "price20_prior60_consecutive_trade_minute_close_extrema_v1"
PHYSICAL = "PHYSICAL_18_20_COMPLETE_PENDING_INDEPENDENT_QC"
PASS = "PRICE_18_20_INDEPENDENT_FULL_MINUTE_QC_PASS_NOT_RESEARCH_READY"


def hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as data:
        for part in iter(lambda: data.read(1048576), b""):
            h.update(part)
    return h.hexdigest()


def day_closes(source, day):
    """Independent bucket aggregation, retaining only actual trade minute tails."""
    start = Decimal(
        int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp())
    )
    close = [None for _ in range(1440)]
    counts = [0 for _ in range(1440)]
    preceding = None
    with gzip.open(source, "rt", newline="") as file:
        for record in csv.DictReader(file):
            ts = Decimal(record["timestamp"])
            px = Decimal(record["price"])
            if (
                not ts.is_finite() or not px.is_finite() or px <= 0
                or (preceding is not None and ts < preceding)
            ):
                raise ValueError("QC_BAD_SOURCE_TRADE")
            preceding = ts
            index = int((ts - start) // Decimal(60))
            if index < 0 or index >= 1440:
                raise ValueError("QC_SOURCE_OUT_OF_DATE")
            counts[index] += 1
            close[index] = px
    return close, counts


def alternate_pearson(values):
    """Pearson via sums of squares and products, rather than centered deviations."""
    if len(values) != 60 or None in values:
        return None, "MISSING_CONSECUTIVE_OBSERVED_1M_RETURNS"
    x = list(values)[:-1]
    y = list(values)[1:]
    n = Decimal(59)
    xsum, ysum = sum(x, Decimal(0)), sum(y, Decimal(0))
    x2sum = sum((v * v for v in x), Decimal(0))
    y2sum = sum((v * v for v in y), Decimal(0))
    xy = sum((a * b for a, b in zip(x, y)), Decimal(0))
    varx = x2sum - (xsum * xsum) / n
    vary = y2sum - (ysum * ysum) / n
    if varx <= 0 or vary <= 0:
        return None, "ZERO_LOG_RETURN_VARIANCE"
    numerator = xy - (xsum * ysum) / n
    return numerator / (varx * vary).sqrt(), "OBSERVED_60_RETURN_WINDOW"


def compare(actual, expected, title, tolerance=Decimal("1e-20")):
    if expected is None:
        if actual is not None:
            raise ValueError("QC_EXPECTED_NULL " + title)
    elif actual is None:
        raise ValueError("QC_UNEXPECTED_NULL " + title)
    elif abs(Decimal(actual) - expected) > tolerance:
        raise ValueError("QC_NUMERIC_DIFFERENCE " + title)


def check(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("QC_ACTOR_NOT_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("QC_SCOPE_NOT_ALLOWED")
    folder = OUT / f"physical_{days}d" / symbol
    qc = folder / "independent_qc_report.json"
    if qc.exists() or (folder / "independent_qc_report.json.part").exists():
        raise FileExistsError("QC_RESULT_EXISTS_DO_NOT_OVERWRITE")
    result = json.loads((folder / "result.json").read_text())
    lineage = json.loads((folder / "input_provenance.json").read_text())["days"]
    if (
        result.get("status") != PHYSICAL
        or result.get("symbol") != symbol
        or result.get("days") != days
        or result.get("rows") != 1440 * days
        or result.get("indicator_ids") != [18, 20]
        or result.get("formula_versions") != {"18": F18, "20": F20}
        or result.get("source_continuity_verified") is not False
        or result.get("receiver_clock_verified") is not False
        or len(lineage) != days
    ):
        raise ValueError("QC_RESULT_CONTRACT_INVALID")
    primary = folder / "features.jsonl.gz"
    snapshot = folder / "source_snapshot/materializer.py"
    if (
        hash_file(primary) != result["output_sha256"]
        or hash_file(snapshot) != result["program_sha256"]
    ):
        raise ValueError("QC_PRIMARY_HASH_DRIFT")
    previous_close = deque(maxlen=60)
    previous_returns = deque(maxlen=60)
    all_rows = 0
    input_fills = 0
    missing18 = missing20 = 0
    daily = []
    with localcontext() as context:
        context.prec = 40
        with gzip.open(primary, "rt") as output:
            for date_index in range(days):
                day = FIRST + dt.timedelta(days=date_index)
                path = RAW / symbol / "public_trades" / (
                    symbol + day.isoformat() + ".csv.gz"
                )
                raw_sha = hash_file(path)
                origin = lineage[date_index]
                if (
                    origin["date_utc"] != day.isoformat()
                    or origin["source_path"] != str(path)
                    or origin["source_sha256"] != raw_sha
                    or origin["source_bytes"] != path.stat().st_size
                    or origin["minute_rows"] != 1440
                ):
                    raise ValueError("QC_SOURCE_PROVENANCE_DIFF")
                closes, counts = day_closes(path, day)
                start = dt.datetime.combine(day, dt.time(), dt.UTC)
                for index, current in enumerate(closes):
                    prev = previous_close[-1] if previous_close else None
                    if prev is None or current is None:
                        ret = None
                    else:
                        ret = (current / prev).ln()
                    previous_returns.append(ret)
                    autocorr, q18 = alternate_pearson(previous_returns)
                    if autocorr is None:
                        missing18 += 1
                    if current is None:
                        hi = lo = None
                        above = below = None
                        q20 = "NO_CURRENT_TRADE_MINUTE_CLOSE"
                    elif len(previous_close) != 60 or None in previous_close:
                        hi = lo = None
                        above = below = None
                        q20 = "MISSING_PRIOR60_OBSERVED_MINUTE_CLOSES"
                    else:
                        hi, lo = max(previous_close), min(previous_close)
                        above, below = current > hi, current < lo
                        q20 = "OBSERVED_PRIOR60_TRADE_CLOSE_WINDOW"
                    if hi is None:
                        missing20 += 1
                    age = (
                        None if current is not None
                        else next(
                            (n for n, v in enumerate(reversed(previous_close), 1)
                             if v is not None), None
                        )
                    )
                    line = output.readline()
                    if not line:
                        raise ValueError("QC_OUTPUT_ROWS_MISSING")
                    value = json.loads(line)
                    expected_start = start + dt.timedelta(minutes=index)
                    fixed = {
                        "venue_id": "Bybit",
                        "instrument_id": symbol,
                        "market_source_id": "BYBIT_PUBLIC_TRADES_ARCHIVE",
                        "indicator_ids": [18, 20],
                        "formula_versions": {"18": F18, "20": F20},
                        "window_start_utc": expected_start.isoformat(),
                        "derived_window_end_utc": (
                            expected_start + dt.timedelta(minutes=1)
                        ).isoformat(),
                        "observed_at_utc": None,
                        "verified_available_after_utc": None,
                        "trade_present": current is not None,
                        "fill_count": counts[index],
                        "last_observed_close_age_minutes": age,
                        "above_prior60_trade_close_high": above,
                        "below_prior60_trade_close_low": below,
                        "quality_18": q18,
                        "quality_20": q20,
                        "source_archive_sha256": raw_sha,
                    }
                    if any(value.get(k) != v for k, v in fixed.items()):
                        raise ValueError(
                            "QC_ROW_METADATA_DIFF " + str(all_rows)
                        )
                    compare(
                        value["minute_trade_close"], current,
                        "minute_close", Decimal(0)
                    )
                    compare(
                        value["returns_autocorr_lag1_prev60"], autocorr,
                        "lag1_autocorr"
                    )
                    compare(
                        value["prior60_trade_close_high"], hi,
                        "prior60_high", Decimal(0)
                    )
                    compare(
                        value["prior60_trade_close_low"], lo,
                        "prior60_low", Decimal(0)
                    )
                    previous_close.append(current)
                    all_rows += 1
                if sum(counts) != origin["fills"]:
                    raise ValueError("QC_DAILY_FILL_COUNT_DIFF")
                input_fills += sum(counts)
                daily.append({
                    "date_utc": day.isoformat(),
                    "rows": 1440, "fills": sum(counts),
                    "source_sha256": raw_sha,
                })
                print(json.dumps({
                    "stage": "PRICE18_20_INDEPENDENT_QC",
                    "symbol": symbol, "days_done": date_index + 1,
                    "days_total": days, "heartbeat": True,
                }), flush=True)
            if output.readline():
                raise ValueError("QC_EXTRA_ROWS")
    if (
        all_rows != days * 1440
        or input_fills != result["fills"]
        or missing18 != result["autocorr_null_count"]
        or missing20 != result["extrema_null_count"]
    ):
        raise ValueError("QC_TOTALS_DIFF")
    script = pathlib.Path(__file__).resolve()
    checker_snapshot = folder / "source_snapshot/independent_checker.py"
    if checker_snapshot.exists():
        raise FileExistsError("QC_SNAPSHOT_ALREADY_EXISTS")
    shutil.copyfile(script, checker_snapshot)
    report = {
        "status": PASS, "symbol": symbol, "days": days,
        "verified_minute_rows": all_rows, "verified_fills": input_fills,
        "indicator_ids": [18, 20],
        "original_sha256": result["output_sha256"],
        "checker_sha256": hash_file(script),
        "source_days": daily,
        "autocorr_null_count": missing18,
        "extrema_null_count": missing20,
        "receiver_clock_verified": False,
        "source_continuity_verified": False,
        "research_ready": False,
    }
    partial = qc.with_name(qc.name + ".part")
    with partial.open("x", encoding="utf-8") as file:
        json.dump(report, file, sort_keys=True, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    partial.rename(qc)
    print(json.dumps({
        "status": PASS, "symbol": symbol,
        "verified_minute_rows": all_rows,
        "receipt_sha256": hash_file(qc),
    }), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=SYMS, required=True)
    p.add_argument("--days", choices=(2, 30), type=int, required=True)
    args = p.parse_args()
    check(args.symbol, args.days)
