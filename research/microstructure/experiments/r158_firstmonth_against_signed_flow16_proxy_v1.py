#!/usr/bin/env python3
"""Research #16 observed against-flow price-move proxy, NOT passive absorption.

Flag a minute if its signed aggressor quote notional magnitude >= preceding
60 completed 1m slots' observed-magnitude p90 and the same-minute first/last
print price change OPPOSES signed flow. Needs >=30 observed prior minutes.
No following-price outcome, book/order/participant identity or trade alpha.
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
from decimal import Decimal

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
REPO = pathlib.Path("/srv/cripta/source_checkout")
MANIFEST = REPO / "research/microstructure/TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
OUT = pathlib.Path("/data/cripta/research/runs/r158_firstmonth_flow16_against_proxy_20261023")
FORMULA = "flow16_prior60_observed_signed_quote_p90_opposite_minute_endpoint_v1"
COMPLETE = "FLOW16_AGAINST_PROXY_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1048576), b""):
            h.update(b)
    return h.hexdigest()


def json_new(path, data):
    part = path.with_name(path.name + ".part")
    with part.open("x", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    part.rename(path)


def terminal(path, status, result=None, error=None):
    original = json.loads(path.read_text())
    if original["status"] != "RUNNING" or status not in (COMPLETE, "FAILED"):
        raise ValueError("NOT_A_VALID_TERMINAL_TRANSITION")
    if (status == COMPLETE) != (result is not None):
        raise ValueError("MISSING_OR_EXTRA_RESULT")
    if status == "FAILED" and error is None:
        raise ValueError("MISSING_ERROR")
    original["status"] = status
    if result:
        original["rows"] = result["rows"]
        original["output_sha256"] = result["output_sha256"]
        original["independent_qc"] = "PENDING"
    else:
        original["error_type"] = type(error).__name__
        original["error_message"] = str(error)[:240]
    part = path.with_name(path.name + ".terminal.part")
    with part.open("x", encoding="utf-8") as f:
        json.dump(original, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, path)


def source_days(symbol, days):
    records = json.loads(MANIFEST.read_text())["source_files"]
    all_coin = [r for r in records if r["symbol"] == symbol]
    lookup = {r["date_utc"]: r for r in all_coin}
    if len(all_coin) != 30 or len(lookup) != 30:
        raise ValueError("SOURCE_MANIFEST_NOT_30D")
    files = []
    for i in range(days):
        day = FIRST + dt.timedelta(days=i)
        path = RAW / symbol / "public_trades" / (
            symbol + day.isoformat() + ".csv.gz")
        if not path.is_file() or not os.access(path, os.R_OK):
            raise FileNotFoundError("TRADE_SOURCE_NOT_READABLE " + str(path))
        files.append((day, path, lookup[day.isoformat()]))
    return files


def minute_aggregates(day, path):
    start = Decimal(int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()))
    rows = [{"first": None, "last": None, "flow": Decimal(0), "n": 0}
            for _ in range(1440)]
    prev = None
    with gzip.open(path, "rt", newline="") as stream:
        for r in csv.DictReader(stream):
            ts = Decimal(r["timestamp"])
            price = Decimal(r["price"])
            value = Decimal(r["foreignNotional"])
            side = r["side"]
            if (not ts.is_finite() or not price.is_finite() or price <= 0
                    or not value.is_finite() or value < 0
                    or side not in ("Buy", "Sell")):
                raise ValueError("BAD_SOURCE_TRADE_VALUE")
            if prev is not None and ts < prev:
                raise ValueError("SOURCE_TIMESTAMP_NONMONOTONE")
            prev = ts
            idx = int((ts - start) // 60)
            if idx < 0 or idx >= 1440:
                raise ValueError("TRADE_OUTSIDE_UTC_DAY")
            m = rows[idx]
            if m["first"] is None:
                m["first"] = price
            m["last"] = price
            m["flow"] += value if side == "Buy" else -value
            m["n"] += 1
    return rows


def measure(prior, current):
    """Prior is exactly 60 completed minute slots (including no-print slots)."""
    if len(prior) < 60:
        return None, None, None, "INSUFFICIENT_60M_LOOKBACK"
    if current["n"] == 0:
        return None, None, None, "NO_PRINTS_IN_CURRENT_MINUTE"
    if current["flow"] == 0:
        return None, None, None, "ZERO_CURRENT_SIGNED_QUOTE_FLOW"
    basis = sorted(abs(v["flow"]) for v in prior if v["n"] > 0)
    if len(basis) < 30:
        return None, None, None, "PRIOR60_LESS_THAN_30_OBSERVED_MINUTES"
    # Exact order-statistic floor 90%, as in historical pilot; no float rounding.
    baseline = basis[((len(basis) - 1) * 9) // 10]
    move = (current["last"] / current["first"] - Decimal(1)) * Decimal(10000)
    if abs(current["flow"]) < baseline:
        return baseline, move, None, "BELOW_PRIOR60_FLOW_P90"
    relation = current["flow"] * move
    if relation < 0:
        return baseline, move, True, "UNUSUAL_AGAINST_FLOW_MOVE_PROXY"
    if relation > 0:
        return baseline, move, False, "UNUSUAL_WITH_FLOW_MOVE"
    return baseline, move, False, "UNUSUAL_FLAT_ENDPOINT_PRICE"


def run(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("UNAPPROVED_SCOPE")
    if not os.access(OUT.parent, os.W_OK | os.X_OK):
        raise PermissionError("DATA_DISK_PARENT_NOT_WRITABLE")
    files = source_days(symbol, days)
    dest = OUT / ("physical_" + str(days) + "d") / symbol
    if dest.exists():
        raise FileExistsError("IMMUTABLE_RESULT_ALREADY_EXISTS_DO_NOT_OVERWRITE")
    os.umask(0o007)
    OUT.mkdir(mode=0o2770, exist_ok=True)
    dest.parent.mkdir(mode=0o2770, exist_ok=True)
    dest.mkdir(mode=0o2770)
    (dest / "source_snapshot").mkdir(mode=0o2770)
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, dest / "source_snapshot/materializer.py")
    script_sha = digest(script)
    source_commit = (REPO / ".git/refs/heads/main").read_text().strip()
    (dest / "source_commit.txt").write_text(source_commit + "\n")
    (dest / "source_sha256.txt").write_text(script_sha + "\n")
    (dest / "command.txt").write_text(
        f"python3 -B {script} --symbol {symbol} --days {days}\n")
    manifest = dest / "run_manifest.json"
    json_new(manifest, {"status": "RUNNING", "symbol": symbol, "days": days,
                        "source_commit": source_commit,
                        "program_sha256": script_sha, "formula_version": FORMULA,
                        "actor_uid": os.geteuid()})
    try:
        all_minutes, provenance, total_fills = [], [], 0
        for i, (day, path, known) in enumerate(files, 1):
            source_sha = digest(path)
            if (source_sha != known["compressed_sha256"]
                    or path.stat().st_size != known["compressed_bytes"]):
                raise ValueError("SOURCE_SHA_OR_LENGTH_DRIFT")
            slots = minute_aggregates(day, path)
            fills = sum(v["n"] for v in slots)
            total_fills += fills
            all_minutes.extend((day, idx, slot, source_sha)
                               for idx, slot in enumerate(slots))
            provenance.append({"date_utc": day.isoformat(), "source_path": str(path),
                               "source_sha256": source_sha,
                               "source_bytes": path.stat().st_size,
                               "minute_rows": len(slots), "fills": fills})
            print(json.dumps({"stage": "FLOW16_PHYSICAL", "symbol": symbol,
                              "days_done": i, "days_total": days,
                              "heartbeat": True}), flush=True)
        temp = dest / "features_1m.jsonl.gz.part"
        artifact = dest / "features_1m.jsonl.gz"
        nrows = eligible = against = unmeasured = 0
        with temp.open("xb") as output:
            with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as gz:
                with io.TextIOWrapper(gz, encoding="utf-8") as writer:
                    for pos, (day, idx, slot, raw_sha) in enumerate(all_minutes):
                        history = [entry[2] for entry in all_minutes[max(0, pos-60):pos]]
                        baseline, move, is_against, quality = measure(history, slot)
                        if is_against is None:
                            unmeasured += 1
                        else:
                            eligible += 1
                            against += int(is_against)
                        moment = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(
                            minutes=idx)
                        row = {"venue_id": "Bybit", "instrument_id": symbol,
                               "market_source_id": "BYBIT_PUBLIC_TRADES_ARCHIVE",
                               "indicator_id": 16, "formula_version": FORMULA,
                               "window_start_utc": moment.isoformat(),
                               "derived_window_end_utc": (moment+dt.timedelta(minutes=1)).isoformat(),
                               "observed_at_utc": None,
                               "verified_available_after_utc": None,
                               "fill_count": slot["n"],
                               "signed_aggressor_quote_usdt": str(slot["flow"]),
                               "first_trade_price": str(slot["first"]) if slot["first"] is not None else None,
                               "last_trade_price": str(slot["last"]) if slot["last"] is not None else None,
                               "prior60_abs_signed_quote_p90": str(baseline) if baseline is not None else None,
                               "intraminute_endpoint_return_bps": str(move) if move is not None else None,
                               "against_signed_flow_price_move_proxy": is_against,
                               "quality": quality,
                               "source_archive_sha256": raw_sha}
                        writer.write(json.dumps(row, sort_keys=True) + "\n")
                        nrows += 1
            output.flush()
            os.fsync(output.fileno())
        if nrows != days * 1440:
            raise ValueError("INCOMPLETE_1M_SCOPE")
        temp.rename(artifact)
        json_new(dest / "input_provenance.json", {"days": provenance})
        result = {"status": COMPLETE, "symbol": symbol, "days": days,
                  "rows": nrows, "fills": total_fills,
                  "unusual_flow_minutes": eligible, "against_flow_proxy_minutes": against,
                  "null_or_below_threshold_minutes": unmeasured,
                  "indicator_id": 16, "formula_version": FORMULA,
                  "source_commit": source_commit, "program_sha256": script_sha,
                  "source_manifest_sha256": digest(MANIFEST),
                  "output_sha256": digest(artifact),
                  "receiver_clock_verified": False,
                  "source_continuity_verified": False, "research_ready": False,
                  "meaning": "descriptive opposing minute-endpoint move with unusual signed flow, not verified passive absorption",
                  "future_return_used": False}
        json_new(dest / "result.json", result)
        terminal(manifest, COMPLETE, result=result)
        print(json.dumps({"status": COMPLETE, "symbol": symbol,
                          "rows": nrows, "output_sha256": result["output_sha256"],
                          "output_dir": str(dest)}), flush=True)
    except Exception as e:
        if not (dest / "result.json").exists():
            try:
                terminal(manifest, "FAILED", error=e)
            except Exception as err:
                print("TERMINAL_STATE_WRITE_FAILED " + str(err), file=sys.stderr)
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=SYMS, required=True)
    p.add_argument("--days", choices=(2, 30), type=int, required=True)
    args = p.parse_args()
    run(args.symbol, args.days)
