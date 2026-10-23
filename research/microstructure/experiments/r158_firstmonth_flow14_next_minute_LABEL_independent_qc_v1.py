#!/usr/bin/env python3
"""Independent complete raw-minute replay: #14 signed current flow + future LABEL.

Reconstructs minute counts/flow/last prices directly from gz trade rows.
No derived next return accepted as a feature or genuine price impact.
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
from decimal import Decimal, localcontext

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
ROOT = pathlib.Path("/data/cripta/research/runs/r158_firstmonth_flow14_futurelabel_20261023")
FORMULA = "flow14_completed_minute_signed_quote_next_close_response_LABEL_v1"
PRIMARY = "FLOW14_FUTURE_LABEL_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
PASS = "FLOW14_FUTURE_LABEL_INDEPENDENT_FULL_MINUTE_DECIMAL_QC_PASS_NOT_RESEARCH_READY"


def hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def read_source(day, path):
    first = Decimal(int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()))
    closes = [None] * 1440
    signed_flow = [Decimal(0)] * 1440
    counts = [0] * 1440
    old_ts = None
    with gzip.open(path, "rt", newline="") as stream:
        for entry in csv.DictReader(stream):
            timestamp, price, notional = (Decimal(entry[k]) for k in (
                "timestamp", "price", "foreignNotional"))
            side = entry["side"]
            if (not timestamp.is_finite() or not price.is_finite() or price <= 0
                    or not notional.is_finite() or notional < 0
                    or side not in ("Buy", "Sell")):
                raise ValueError("QC_INVALID_TRADE_DATA")
            if old_ts is not None and timestamp < old_ts:
                raise ValueError("QC_TAPE_TIMESTAMP_NOT_SORTED")
            old_ts = timestamp
            minute = int((timestamp - first) // Decimal(60))
            if minute not in range(1440):
                raise ValueError("QC_TRADE_OUT_OF_UTC_DAY")
            closes[minute] = price
            signed_flow[minute] += notional * (1 if side == "Buy" else -1)
            counts[minute] += 1
    return [(closes[i], signed_flow[i], counts[i]) for i in range(1440)]


def expected_label(current, following):
    close, flow, count = current
    if count == 0:
        return None, "NO_CURRENT_TRADE"
    if flow == 0:
        return None, "ZERO_CURRENT_SIGNED_FLOW"
    if following is None:
        return None, "NO_FOLLOWING_MINUTE_INSIDE_SCOPE"
    nxt, _, nxt_count = following
    if nxt_count == 0:
        return None, "NO_FOLLOWING_MINUTE_TRADE"
    # Algebraically different from primary: ratio, not close difference.
    ratio = (nxt / close - Decimal(1)) * Decimal(10000)
    return (ratio if flow > 0 else -ratio), "PAIRED_NEXT_MINUTE_FUTURE_LABEL_ONLY"


def cmp_decimal(given, expected, tag, tolerance=Decimal("1e-20")):
    if expected is None:
        if given is not None:
            raise ValueError("QC_VALUE_EXPECTED_NULL " + tag)
    elif given is None or abs(Decimal(given) - expected) > tolerance:
        raise ValueError("QC_DECIMAL_VALUE_MISMATCH " + tag)


def run(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("QC_WRITER_NOT_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("QC_SCOPE_NOT_ALLOWED")
    folder = ROOT / f"physical_{days}d" / symbol
    receipt = folder / "independent_qc_report.json"
    if receipt.exists() or (folder / "independent_qc_report.json.part").exists():
        raise FileExistsError("QC_RESULT_EXISTS_DO_NOT_OVERWRITE")
    result = json.loads((folder / "result.json").read_text())
    proven = json.loads((folder / "input_provenance.json").read_text())["days"]
    artifact = folder / "features_1m.jsonl.gz"
    snapshot = folder / "source_snapshot/materializer.py"
    if (result.get("status") != PRIMARY or result.get("symbol") != symbol
            or result.get("days") != days or result.get("rows") != 1440 * days
            or result.get("indicator_id") != 14 or result.get("formula_version") != FORMULA
            or result.get("future_label_not_feature") is not True
            or result.get("marginal_order_price_impact_verified") is not False
            or result.get("research_ready") is not False
            or len(proven) != days):
        raise ValueError("QC_PRIMARY_CONTRACT_MISMATCH")
    if (hash_file(artifact) != result["output_sha256"]
            or hash_file(snapshot) != result["program_sha256"]):
        raise ValueError("QC_OUTPUT_OR_CODE_SHA_MISMATCH")
    reconstructed, sources = [], []
    for index in range(days):
        day = FIRST + dt.timedelta(days=index)
        path = RAW / symbol / "public_trades" / (symbol + day.isoformat() + ".csv.gz")
        source_sha = hash_file(path)
        evidence = proven[index]
        if (evidence["date_utc"] != day.isoformat()
                or evidence["source_path"] != str(path)
                or evidence["source_sha256"] != source_sha
                or evidence["source_bytes"] != path.stat().st_size
                or evidence["minute_rows"] != 1440):
            raise ValueError("QC_SOURCE_IDENTITY_OR_HASH_DIFFERENCE")
        bars = read_source(day, path)
        fills = sum(row[2] for row in bars)
        if fills != evidence["fills"]:
            raise ValueError("QC_FILLS_MISMATCH")
        reconstructed.extend((day, i, row, source_sha) for i, row in enumerate(bars))
        sources.append({"date_utc": day.isoformat(),
                        "source_sha256": source_sha,
                        "rows": 1440, "fills": fills})
    count = valid = pos = neg = zero = 0
    with localcontext() as ctx:
        ctx.prec = 40
        with gzip.open(artifact, "rt") as source:
            for index, (day, minute, bar, original_sha) in enumerate(reconstructed):
                following = reconstructed[index+1][2] if index+1 < len(reconstructed) else None
                label, quality = expected_label(bar, following)
                if label is not None:
                    valid += 1
                    pos += int(label > 0)
                    neg += int(label < 0)
                    zero += int(label == 0)
                line = source.readline()
                if not line:
                    raise ValueError("QC_MISSING_OUTPUT_ROW")
                measured = json.loads(line)
                start = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(minutes=minute)
                end = start + dt.timedelta(minutes=1)
                nxt_close = following[0] if following else None
                expected = {
                    "venue_id": "Bybit", "instrument_id": symbol,
                    "market_source_id": "BYBIT_PUBLIC_TRADES_ARCHIVE",
                    "indicator_id": 14, "formula_version": FORMULA,
                    "window_start_utc": start.isoformat(),
                    "derived_feature_window_end_utc": end.isoformat(),
                    "future_label_window_end_utc": (
                        (end + dt.timedelta(minutes=1)).isoformat()
                        if following is not None else None),
                    "observed_at_utc": None,
                    "verified_available_after_utc": None,
                    "future_label_verified_available_after_utc": None,
                    "current_minute_fill_count": bar[2],
                    "label_quality": quality, "source_archive_sha256": original_sha,
                }
                if any(measured.get(key) != val for key, val in expected.items()):
                    raise ValueError("QC_ROW_METADATA_QUALITY " + str(index))
                cmp_decimal(measured["current_minute_signed_quote_usdt"],
                            bar[1] if bar[2] else None, "signed")
                cmp_decimal(measured["current_minute_trade_close"], bar[0], "current_close")
                cmp_decimal(measured["next_minute_trade_close_OUTCOME_ONLY"],
                            nxt_close, "next_close")
                cmp_decimal(measured["signed_next_minute_response_bps_FUTURE_LABEL_ONLY"],
                            label, "future_label")
                count += 1
            if source.readline():
                raise ValueError("QC_UNEXPECTED_EXTRA_ROW")
    if (count != days * 1440 or valid != result["paired_labels"]
            or pos != result["positive_labels"] or neg != result["negative_labels"]
            or zero != result["flat_labels"] or count-valid != result["missing_labels"]
            or sum(x["fills"] for x in sources) != result["fills"]):
        raise ValueError("QC_SUMMARY_MISMATCH")
    checker = pathlib.Path(__file__).resolve()
    copy = folder / "source_snapshot/independent_checker.py"
    if copy.exists():
        raise FileExistsError("CHECKER_SNAPSHOT_ALREADY_EXISTS")
    shutil.copyfile(checker, copy)
    report = {"status": PASS, "symbol": symbol, "days": days,
              "verified_minute_rows": count, "paired_labels": valid,
              "positive_labels": pos, "negative_labels": neg,
              "flat_labels": zero, "missing_labels": count-valid,
              "original_sha256": result["output_sha256"],
              "checker_sha256": hash_file(checker),
              "source_days": sources, "future_label_not_feature": True,
              "receiver_clock_verified": False,
              "source_continuity_verified": False, "research_ready": False}
    part = receipt.with_name(receipt.name + ".part")
    with part.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    part.rename(receipt)
    print(json.dumps({"status": PASS, "symbol": symbol,
                      "verified_minute_rows": count,
                      "receipt_sha256": hash_file(receipt)}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=SYMS, required=True)
    p.add_argument("--days", choices=(2, 30), type=int, required=True)
    args = p.parse_args()
    run(args.symbol, args.days)
