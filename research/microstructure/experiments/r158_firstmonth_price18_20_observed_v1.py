#!/usr/bin/env python3
"""Research #18/#20: strictly observed one-minute trade closes, first month only.

This is a NEW estimator variant, not a retroactive correction to
price_minute_features_v1.py's float timestamp, carry-forward and zero returns.
#18: lag-1 autocorrelation of 60 consecutive observed log close returns.
#20: high/low of 60 preceding consecutive observed trade-minute closes;
      compare CURRENT observed close, not trade high/low or order-book liquidity.
No fake stale prices, no data gaps filled, no reception time invented.
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
import sys
import time
from collections import deque
from decimal import Decimal, localcontext

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
REPO = pathlib.Path("/srv/cripta/source_checkout")
OUT = pathlib.Path(
    "/data/cripta/research/runs/r158_firstmonth_price18_20_observed_20261023"
)
RAW_MANIFEST = (
    REPO / "research/microstructure/"
    "TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
)
F18 = "price18_lag1_logret_prev60_consecutive_trade_minutes_v1"
F20 = "price20_prior60_consecutive_trade_minute_close_extrema_v1"
COMPLETE = "PHYSICAL_18_20_COMPLETE_PENDING_INDEPENDENT_QC"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def new_json(path, value):
    part = path.with_name(path.name + ".part")
    with part.open("x", encoding="utf-8") as f:
        json.dump(value, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    part.rename(path)


def set_terminal(path, status, *, result=None, error=None):
    if status not in (COMPLETE, "FAILED"):
        raise ValueError("UNKNOWN_TERMINAL_STATE")
    current = json.loads(path.read_text())
    if current["status"] != "RUNNING":
        raise ValueError("TERMINAL_STATE_ALREADY_SET")
    if (status == COMPLETE) != (result is not None):
        raise ValueError("RESULT_REQUIRED_ONLY_ON_SUCCESS")
    if (status == "FAILED") != (error is not None):
        raise ValueError("ERROR_REQUIRED_ONLY_ON_FAILURE")
    current["status"] = status
    if result:
        current["output_sha256"] = result["output_sha256"]
        current["rows"] = result["rows"]
        current["independent_qc"] = "PENDING"
    if error:
        current["failure_class"] = type(error).__name__
        current["failure_detail"] = str(error)[:300]
    part = path.with_name(path.name + ".terminal.part")
    with part.open("x", encoding="utf-8") as file:
        json.dump(current, file, sort_keys=True, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(part, path)


def sources_for(symbol, days):
    all_sources = json.loads(RAW_MANIFEST.read_text())["source_files"]
    items = [r for r in all_sources if r["symbol"] == symbol]
    lookup = {r["date_utc"]: r for r in items}
    if len(items) != 30 or len(lookup) != 30:
        raise ValueError("EXPECTED_30_SOURCE_DAYS")
    found = []
    for index in range(days):
        day = FIRST + dt.timedelta(days=index)
        source = RAW / symbol / "public_trades" / (
            symbol + day.isoformat() + ".csv.gz"
        )
        if not source.is_file() or not os.access(source, os.R_OK):
            raise FileNotFoundError("RAW_TAPE_MISSING: " + str(source))
        found.append((day, source, lookup[day.isoformat()]))
    return found


def read_day_closes(day, source):
    """Only the LAST actually recorded price of each UTC minute."""
    minute = [None] * 1440
    counts = [0] * 1440
    start = Decimal(
        int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp())
    )
    previous = None
    with gzip.open(source, "rt", newline="") as file:
        for trade in csv.DictReader(file):
            timestamp = Decimal(trade["timestamp"])
            price = Decimal(trade["price"])
            if not timestamp.is_finite() or not price.is_finite() or price <= 0:
                raise ValueError("BAD_RAW_TIMESTAMP_OR_PRICE")
            if previous is not None and timestamp < previous:
                raise ValueError("RAW_TRADE_TIME_NOT_MONOTONE")
            previous = timestamp
            minute_index = int((timestamp - start) // 60)
            if not 0 <= minute_index < 1440:
                raise ValueError("TRADE_OUTSIDE_UTC_DAY")
            minute[minute_index] = price
            counts[minute_index] += 1
    return minute, counts


def lag1_of_60_returns(returns):
    """Pearson lag-1 over 59 pairs of 60 complete log close returns."""
    if len(returns) != 60 or any(v is None for v in returns):
        return None, "MISSING_CONSECUTIVE_OBSERVED_1M_RETURNS"
    xx = list(returns)[:-1]
    yy = list(returns)[1:]
    n = Decimal(59)
    mx = sum(xx, Decimal(0)) / n
    my = sum(yy, Decimal(0)) / n
    covariance = sum(((a - mx) * (b - my) for a, b in zip(xx, yy)), Decimal(0))
    vx = sum(((a - mx) ** 2 for a in xx), Decimal(0))
    vy = sum(((b - my) ** 2 for b in yy), Decimal(0))
    if vx == 0 or vy == 0:
        return None, "ZERO_LOG_RETURN_VARIANCE"
    return covariance / (vx * vy).sqrt(), "OBSERVED_60_RETURN_WINDOW"


def run(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("EXPECTED_RESEARCH_ACTOR_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("SCOPE_NOT_APPROVED")
    if not os.access(OUT.parent, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_PARENT_UNWRITABLE")
    files = sources_for(symbol, days)
    destination = OUT / f"physical_{days}d" / symbol
    if destination.exists():
        raise FileExistsError("IMMUTABLE_RESULT_EXISTS_DO_NOT_OVERWRITE")
    os.umask(0o007)
    OUT.mkdir(mode=0o2770, exist_ok=True)
    destination.parent.mkdir(mode=0o2770, exist_ok=True)
    destination.mkdir(mode=0o2770)
    (destination / "source_snapshot").mkdir(mode=0o2770)
    program = pathlib.Path(__file__).resolve()
    shutil.copyfile(program, destination / "source_snapshot/materializer.py")
    code_sha = digest(program)
    commit = (REPO / ".git/refs/heads/main").read_text().strip()
    (destination / "source_commit.txt").write_text(commit + "\n")
    (destination / "source_sha256.txt").write_text(code_sha + "\n")
    (destination / "command.txt").write_text(
        f"python3 -B {program} --symbol {symbol} --days {days}\n"
    )
    (destination / "environment.txt").write_text(
        f"actor_uid={os.geteuid()}\npython={sys.executable}\n"
    )
    manifest = destination / "run_manifest.json"
    new_json(manifest, {
        "status": "RUNNING",
        "actor": "cripta",
        "symbol": symbol,
        "days": days,
        "source_commit": commit,
        "program_sha256": code_sha,
        "formula_versions": {"18": F18, "20": F20},
        "receiver_clock": "UNKNOWN",
    })
    try:
        partial = destination / "features.jsonl.gz.part"
        completed = destination / "features.jsonl.gz"
        provenance = []
        close_history = deque(maxlen=60)
        returns = deque(maxlen=60)
        row_count, fills_total = 0, 0
        null18, null20, zero_var = 0, 0, 0
        started = time.monotonic()
        with localcontext() as context:
            context.prec = 40
            with partial.open("xb") as output:
                with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as gz:
                    with io.TextIOWrapper(gz, encoding="utf-8") as writer:
                        for i, (day, source, info) in enumerate(files, 1):
                            sha = digest(source)
                            if (
                                sha != info["compressed_sha256"]
                                or source.stat().st_size != info["compressed_bytes"]
                            ):
                                raise ValueError("SOURCE_SHA_OR_LENGTH_DRIFT")
                            minute, counts = read_day_closes(day, source)
                            beginning = dt.datetime.combine(
                                day, dt.time(), dt.UTC
                            )
                            for k, current in enumerate(minute):
                                previous = close_history[-1] if close_history else None
                                if current is not None and previous is not None:
                                    ret = (current / previous).ln()
                                else:
                                    ret = None
                                returns.append(ret)
                                autocorr, q18 = lag1_of_60_returns(returns)
                                if autocorr is None:
                                    null18 += 1
                                if q18 == "ZERO_LOG_RETURN_VARIANCE":
                                    zero_var += 1
                                if current is None:
                                    hi = lo = above = below = None
                                    q20 = "NO_CURRENT_TRADE_MINUTE_CLOSE"
                                elif (
                                    len(close_history) != 60
                                    or any(v is None for v in close_history)
                                ):
                                    hi = lo = above = below = None
                                    q20 = "MISSING_PRIOR60_OBSERVED_MINUTE_CLOSES"
                                else:
                                    hi = max(close_history)
                                    lo = min(close_history)
                                    above = current > hi
                                    below = current < lo
                                    q20 = "OBSERVED_PRIOR60_TRADE_CLOSE_WINDOW"
                                if hi is None:
                                    null20 += 1
                                now = beginning + dt.timedelta(minutes=k)
                                row = {
                                    "venue_id": "Bybit", "instrument_id": symbol,
                                    "market_source_id": "BYBIT_PUBLIC_TRADES_ARCHIVE",
                                    "indicator_ids": [18, 20],
                                    "formula_versions": {"18": F18, "20": F20},
                                    "window_start_utc": now.isoformat(),
                                    "derived_window_end_utc": (
                                        now + dt.timedelta(minutes=1)
                                    ).isoformat(),
                                    "observed_at_utc": None,
                                    "verified_available_after_utc": None,
                                    "trade_present": current is not None,
                                    "fill_count": counts[k],
                                    "minute_trade_close": (
                                        str(current) if current is not None else None
                                    ),
                                    "last_observed_close_age_minutes": (
                                        None if current is not None
                                        else next(
                                            (
                                                age for age, price in
                                                enumerate(reversed(close_history), 1)
                                                if price is not None
                                            ), None
                                        )
                                    ),
                                    "returns_autocorr_lag1_prev60": (
                                        str(autocorr) if autocorr is not None else None
                                    ),
                                    "prior60_trade_close_high": (
                                        str(hi) if hi is not None else None
                                    ),
                                    "prior60_trade_close_low": (
                                        str(lo) if lo is not None else None
                                    ),
                                    "above_prior60_trade_close_high": above,
                                    "below_prior60_trade_close_low": below,
                                    "quality_18": q18,
                                    "quality_20": q20,
                                    "source_archive_sha256": sha,
                                }
                                writer.write(json.dumps(row, sort_keys=True) + "\n")
                                row_count += 1
                                close_history.append(current)
                            fills_total += sum(counts)
                            provenance.append({
                                "date_utc": day.isoformat(),
                                "source_path": str(source),
                                "source_sha256": sha,
                                "source_bytes": source.stat().st_size,
                                "fills": sum(counts),
                                "minute_rows": len(minute),
                            })
                            print(json.dumps({
                                "stage": "PRICE_18_20_OBSERVED_PHYSICAL",
                                "symbol": symbol,
                                "days_done": i, "days_total": days,
                                "elapsed_seconds": round(time.monotonic() - started, 2),
                                "heartbeat": True,
                            }), flush=True)
                output.flush()
                os.fsync(output.fileno())
        if row_count != days * 1440:
            raise ValueError("MINUTE_ROWS_NOT_MATCHING_SCOPE")
        partial.rename(completed)
        new_json(destination / "input_provenance.json", {"days": provenance})
        result = {
            "status": COMPLETE,
            "symbol": symbol, "days": days,
            "rows": row_count, "fills": fills_total,
            "indicator_ids": [18, 20],
            "formula_versions": {"18": F18, "20": F20},
            "autocorr_null_count": null18,
            "autocorr_zero_variance_count": zero_var,
            "extrema_null_count": null20,
            "output_sha256": digest(completed),
            "program_sha256": code_sha,
            "source_manifest_sha256": digest(RAW_MANIFEST),
            "source_commit": commit,
            "source_continuity_verified": False,
            "receiver_clock_verified": False,
            "research_ready": False,
            "limitations": [
                "No carry-forward of missing minute close or zero-filled returns",
                "Autocorrelation: 60 consecutive observed minute-close log returns",
                "Extremes: 60 preceding trade-minute closes, not candle extremes",
                "No verified receiver/publication clock or full source continuity",
            ],
        }
        new_json(destination / "result.json", result)
        set_terminal(manifest, COMPLETE, result=result)
        print(json.dumps({
            "status": COMPLETE, "symbol": symbol, "rows": row_count,
            "output_sha256": result["output_sha256"],
            "output_dir": str(destination),
        }), flush=True)
    except Exception as error:
        if not (destination / "result.json").exists():
            try:
                set_terminal(manifest, "FAILED", error=error)
            except Exception as another:
                print("TERMINAL_WRITE_FAILURE " + str(another), file=sys.stderr)
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=SYMS, required=True)
    p.add_argument("--days", choices=(2, 30), type=int, required=True)
    args = p.parse_args()
    run(args.symbol, args.days)
