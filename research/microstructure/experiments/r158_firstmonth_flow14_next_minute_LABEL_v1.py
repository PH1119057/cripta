#!/usr/bin/env python3
"""#14 research variant: CURRENT minute signed taker flow + NEXT minute outcome LABEL.

No assertion of single-order marginal price impact, no future-return feature.
No carry of unobserved close. 30 UTC days May18-Jun16 2026, exact five symbols.
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
from decimal import Decimal, localcontext

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
REPO = pathlib.Path("/srv/cripta/source_checkout")
MANIFEST = REPO / "research/microstructure/TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
OUT = pathlib.Path("/data/cripta/research/runs/r158_firstmonth_flow14_futurelabel_20261023")
FORMULA = "flow14_completed_minute_signed_quote_next_close_response_LABEL_v1"
DONE = "FLOW14_FUTURE_LABEL_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_new(path, obj):
    part = path.with_name(path.name + ".part")
    with part.open("x", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    part.rename(path)


def close_manifest(path, status, detail):
    current = json.loads(path.read_text())
    if current["status"] != "RUNNING" or status not in (DONE, "FAILED"):
        raise ValueError("BAD_RUN_MANIFEST_TRANSITION")
    current["status"] = status
    if status == DONE:
        current["rows"] = detail["rows"]
        current["output_sha256"] = detail["output_sha256"]
        current["independent_qc"] = "PENDING"
    else:
        current["error_type"] = type(detail).__name__
        current["error_detail"] = str(detail)[:240]
    part = path.with_name(path.name + ".terminal.part")
    with part.open("x", encoding="utf-8") as f:
        json.dump(current, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, path)


def source_days(symbol, days):
    records = json.loads(MANIFEST.read_text())["source_files"]
    selected = [r for r in records if r["symbol"] == symbol]
    lookup = {r["date_utc"]: r for r in selected}
    if len(selected) != 30 or len(lookup) != 30:
        raise ValueError("RAW_MANIFEST_30_DAYS_EXPECTED")
    result = []
    for offset in range(days):
        day = FIRST + dt.timedelta(days=offset)
        path = RAW / symbol / "public_trades" / (symbol + day.isoformat() + ".csv.gz")
        if not path.is_file() or not os.access(path, os.R_OK):
            raise FileNotFoundError("RAW_NOT_READABLE " + str(path))
        result.append((day, path, lookup[day.isoformat()]))
    return result


def minutes(day, path):
    start = Decimal(int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()))
    buckets = [{"count": 0, "signed": Decimal(0), "close": None} for _ in range(1440)]
    prev = None
    with gzip.open(path, "rt", newline="") as f:
        for r in csv.DictReader(f):
            timestamp = Decimal(r["timestamp"])
            price = Decimal(r["price"])
            notional = Decimal(r["foreignNotional"])
            side = r["side"]
            if (not timestamp.is_finite() or not price.is_finite() or price <= 0
                    or not notional.is_finite() or notional < 0
                    or side not in ("Buy", "Sell")):
                raise ValueError("BAD_SOURCE_TRADE")
            if prev is not None and timestamp < prev:
                raise ValueError("OUT_OF_ORDER_SOURCE")
            prev = timestamp
            minute = int((timestamp - start) // 60)
            if minute < 0 or minute >= 1440:
                raise ValueError("TRADE_OUTSIDE_DATE")
            slot = buckets[minute]
            slot["count"] += 1
            slot["signed"] += notional if side == "Buy" else -notional
            slot["close"] = price
    return buckets


def paired_label(current, following):
    """The future label is NOT an as-of-current-minute feature."""
    if not current["count"]:
        return None, "NO_CURRENT_TRADE"
    if not current["signed"]:
        return None, "ZERO_CURRENT_SIGNED_FLOW"
    if following is None:
        return None, "NO_FOLLOWING_MINUTE_INSIDE_SCOPE"
    if not following["count"]:
        return None, "NO_FOLLOWING_MINUTE_TRADE"
    response = (following["close"] - current["close"]) / current["close"] * 10000
    if current["signed"] < 0:
        response = -response
    return response, "PAIRED_NEXT_MINUTE_FUTURE_LABEL_ONLY"


def run(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("NOT_CRIPTA_ACTOR")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("SCOPE_NOT_ALLOWED")
    if not os.access(OUT.parent, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_ROOT_UNWRITABLE")
    inputs = source_days(symbol, days)
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
    source_commit = (REPO / ".git/refs/heads/main").read_text().strip()
    (target / "source_commit.txt").write_text(source_commit + "\n")
    (target / "source_sha256.txt").write_text(code_sha + "\n")
    (target / "command.txt").write_text(
        f"python3 -B {script} --symbol {symbol} --days {days}\n"
    )
    manifest = target / "run_manifest.json"
    write_new(manifest, {"status": "RUNNING", "symbol": symbol, "days": days,
                         "source_commit": source_commit, "program_sha256": code_sha,
                         "actor_uid": os.geteuid(), "formula_version": FORMULA})
    try:
        arrays, prov, trade_total = [], [], 0
        for count, (day, path, record) in enumerate(inputs, 1):
            digest = sha(path)
            if (digest != record["compressed_sha256"]
                    or path.stat().st_size != record["compressed_bytes"]):
                raise ValueError("SOURCE_SHA_OR_SIZE_DRIFT")
            blocks = minutes(day, path)
            fills = sum(v["count"] for v in blocks)
            trade_total += fills
            arrays.extend((day, minute, b, digest) for minute, b in enumerate(blocks))
            prov.append({"date_utc": day.isoformat(), "source_path": str(path),
                         "source_sha256": digest, "source_bytes": path.stat().st_size,
                         "fills": fills, "minute_rows": 1440})
            print(json.dumps({"stage": "FLOW14_SOURCE", "symbol": symbol,
                              "days_done": count, "days_total": days,
                              "heartbeat": True}), flush=True)
        output = target / "features_1m.jsonl.gz"
        temp = target / "features_1m.jsonl.gz.part"
        rows = eligible = positive = negative = flat = 0
        with localcontext() as ctx:
            ctx.prec = 40
            with temp.open("xb") as f:
                with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
                    with io.TextIOWrapper(gz, encoding="utf-8") as writer:
                        for i, (day, minute, b, digest) in enumerate(arrays):
                            next_block = arrays[i + 1][2] if i + 1 < len(arrays) else None
                            value, quality = paired_label(b, next_block)
                            if value is not None:
                                eligible += 1
                                positive += int(value > 0)
                                negative += int(value < 0)
                                flat += int(value == 0)
                            start = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(minutes=minute)
                            end = start + dt.timedelta(minutes=1)
                            row = {"venue_id": "Bybit", "instrument_id": symbol,
                                   "market_source_id": "BYBIT_PUBLIC_TRADES_ARCHIVE",
                                   "indicator_id": 14, "formula_version": FORMULA,
                                   "window_start_utc": start.isoformat(),
                                   "derived_feature_window_end_utc": end.isoformat(),
                                   "future_label_window_end_utc": (end+dt.timedelta(minutes=1)).isoformat()
                                       if next_block is not None else None,
                                   "observed_at_utc": None,
                                   "verified_available_after_utc": None,
                                   "future_label_verified_available_after_utc": None,
                                   "current_minute_fill_count": b["count"],
                                   "current_minute_signed_quote_usdt":
                                       str(b["signed"]) if b["count"] else None,
                                   "current_minute_trade_close":
                                       str(b["close"]) if b["close"] is not None else None,
                                   "next_minute_trade_close_OUTCOME_ONLY":
                                       str(next_block["close"])
                                       if next_block is not None and next_block["close"] is not None else None,
                                   "signed_next_minute_response_bps_FUTURE_LABEL_ONLY":
                                       str(value) if value is not None else None,
                                   "label_quality": quality,
                                   "source_archive_sha256": digest}
                            writer.write(json.dumps(row, sort_keys=True) + "\n")
                            rows += 1
                f.flush()
                os.fsync(f.fileno())
        if rows != 1440 * days:
            raise ValueError("MINUTE_ROW_COUNT_MISMATCH")
        temp.rename(output)
        write_new(target / "input_provenance.json", {"days": prov})
        result = {"status": DONE, "symbol": symbol, "days": days, "rows": rows,
                  "fills": trade_total, "paired_labels": eligible,
                  "positive_labels": positive, "negative_labels": negative,
                  "flat_labels": flat, "missing_labels": rows - eligible,
                  "indicator_id": 14, "formula_version": FORMULA,
                  "source_commit": source_commit, "program_sha256": code_sha,
                  "source_manifest_sha256": sha(MANIFEST),
                  "output_sha256": sha(output),
                  "future_label_not_feature": True,
                  "marginal_order_price_impact_verified": False,
                  "receiver_clock_verified": False, "source_continuity_verified": False,
                  "research_ready": False}
        write_new(target / "result.json", result)
        close_manifest(manifest, DONE, result)
        print(json.dumps({"status": DONE, "symbol": symbol, "rows": rows,
                          "output_sha256": result["output_sha256"],
                          "output_dir": str(target)}), flush=True)
    except Exception as exc:
        if not (target / "result.json").exists():
            try:
                close_manifest(manifest, "FAILED", exc)
            except Exception as nested:
                print("FAILED_MANIFEST_WRITE: " + str(nested))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", choices=SYMS, required=True)
    parser.add_argument("--days", type=int, choices=(2, 30), required=True)
    args = parser.parse_args()
    run(args.symbol, args.days)
