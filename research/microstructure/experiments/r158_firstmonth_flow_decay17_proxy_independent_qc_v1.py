#!/usr/bin/env python3
"""Independent full 5m-block raw archive replay of #17 descriptive flow proxy.

Separate source reader and window comparison; exact Decimal values, no future
price block or downstream labels. This numerical QC does NOT establish feed
receiver/publication time or participant-level exhaustion.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import pwd
import shutil
from decimal import Decimal

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
OUT = pathlib.Path("/data/cripta/research/runs/r158_firstmonth_flow17_proxy_20261023")
FORMULA = "flow17_5m_current_to_prior_signed_notional_ratio_direction_qualified_v1"
PHYSICAL = "FLOW17_PROXY_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
QC_PASS = "FLOW17_PROXY_INDEPENDENT_ALL_5M_DECIMAL_QC_PASS_NOT_RESEARCH_READY"


def hash_file(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1048576), b""):
            value.update(part)
    return value.hexdigest()


def audit_source(path, date):
    epoch = Decimal(int(dt.datetime.combine(date, dt.time(), dt.UTC).timestamp()))
    prices = [None] * 288
    volume = [Decimal(0)] * 288
    counts = [0] * 288
    prev_stamp = None
    with gzip.open(path, "rt", newline="") as stream:
        for trade in csv.DictReader(stream):
            t, p, n = (Decimal(trade[k]) for k in (
                "timestamp", "price", "foreignNotional"))
            side = trade["side"]
            if (not t.is_finite() or not p.is_finite() or p <= 0
                    or not n.is_finite() or n < 0 or side not in ("Buy", "Sell")):
                raise ValueError("BAD_QC_SOURCE_VALUE")
            if prev_stamp is not None and t < prev_stamp:
                raise ValueError("BAD_QC_TRADE_SEQUENCE")
            prev_stamp = t
            idx = int((t - epoch) // Decimal(300))
            if not 0 <= idx < 288:
                raise ValueError("QC_WRONG_UTC_DAY")
            prices[idx] = p
            volume[idx] += n if side == "Buy" else -n
            counts[idx] += 1
    return list(zip(prices, volume, counts))


def expected_feature(previous_two, previous, current):
    if previous_two is None or previous is None:
        return None, None, "INSUFFICIENT_PRECEDING_BLOCKS"
    pp_close, _, pp_count = previous_two
    pr_close, pr_volume, pr_count = previous
    _, cur_volume, cur_count = current
    if not pp_count or not pr_count or not cur_count:
        return None, None, "MISSING_OBSERVED_5M_BLOCK"
    if not pr_volume or not cur_volume:
        return None, None, "ZERO_SIGNED_FLOW"
    last_price_delta = pr_close - pp_close
    if last_price_delta == 0 or last_price_delta * cur_volume <= 0:
        return None, None, "FLOW_NOT_ALIGNED_WITH_PRIOR_PRICE_MOVE"
    if pr_volume * cur_volume <= 0:
        return None, None, "FLOW_DIRECTION_CHANGED"
    # Independent alternative to abs(current)/abs(prior): signed denominator
    # is same sign as current for eligible pairs, so ratio is positive.
    ratio = cur_volume / pr_volume
    is_decay = ratio < Decimal(1)
    return ratio, is_decay, (
        "ELIGIBLE_FLOW_DECAY" if is_decay else "ELIGIBLE_FLOW_NONDECAY"
    )


def match_decimal(observed, expected, label):
    if expected is None:
        if observed is not None:
            raise ValueError("UNEXPECTED_VALUE " + label)
    elif observed is None or abs(Decimal(observed) - expected) > Decimal("1e-23"):
        raise ValueError("NUMERIC_MISMATCH " + label)


def verify(symbol, days):
    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("QC_ACTOR_NOT_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("QC_SCOPE_NOT_APPROVED")
    folder = OUT / ("physical_" + str(days) + "d") / symbol
    receipt = folder / "independent_qc_report.json"
    if receipt.exists() or (folder / "independent_qc_report.json.part").exists():
        raise FileExistsError("QC_ALREADY_EXISTS_DO_NOT_OVERWRITE")
    datafile = folder / "features_5m.jsonl.gz"
    result = json.loads((folder / "result.json").read_text())
    provenance = json.loads((folder / "input_provenance.json").read_text())["days"]
    snapshot = folder / "source_snapshot/materializer.py"
    if (result.get("status") != PHYSICAL or result.get("symbol") != symbol
            or result.get("days") != days or result.get("rows") != days * 288
            or result.get("formula_version") != FORMULA
            or result.get("indicator_id") != 17 or len(provenance) != days
            or result.get("research_ready") is not False
            or result.get("receiver_clock_verified") is not False
            or result.get("future_return_used") is not False):
        raise ValueError("QC_PHYSICAL_METADATA_MISMATCH")
    if (hash_file(datafile) != result["output_sha256"]
            or hash_file(snapshot) != result["program_sha256"]):
        raise ValueError("QC_SOURCE_OR_OUTPUT_HASH_MISMATCH")
    blocks = []
    checked_sources = []
    for di in range(days):
        day = FIRST + dt.timedelta(days=di)
        source = RAW / symbol / "public_trades" / (
            symbol + day.isoformat() + ".csv.gz")
        sha = hash_file(source)
        orig = provenance[di]
        if (orig["date_utc"] != day.isoformat()
                or orig["source_path"] != str(source)
                or orig["source_sha256"] != sha
                or orig["source_bytes"] != source.stat().st_size
                or orig["blocks"] != 288):
            raise ValueError("QC_SOURCE_INPUT_PROVENANCE")
        values = audit_source(source, day)
        fills = sum(r[2] for r in values)
        if orig["fills"] != fills:
            raise ValueError("QC_SOURCE_FILL_COUNT")
        blocks.extend((day, i, value, sha) for i, value in enumerate(values))
        checked_sources.append({"date_utc": day.isoformat(),
                                "source_sha256": sha, "blocks": 288,
                                "fills": fills})
    total, eligible, decays, nulls = 0, 0, 0, 0
    with gzip.open(datafile, "rt") as stream:
        for ix, (day, block, cur, sha) in enumerate(blocks):
            pp = blocks[ix-2][2] if ix >= 2 else None
            pr = blocks[ix-1][2] if ix >= 1 else None
            ratio, decay, quality = expected_feature(pp, pr, cur)
            if ratio is not None:
                eligible += 1
                decays += int(decay)
            else:
                nulls += 1
            raw = stream.readline()
            if not raw:
                raise ValueError("QC_MISSING_5M_OUTPUT_ROW")
            actual = json.loads(raw)
            moment = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(
                minutes=block * 5)
            expected = {
                "venue_id": "Bybit",
                "instrument_id": symbol,
                "market_source_id": "BYBIT_PUBLIC_TRADES_ARCHIVE",
                "indicator_id": 17, "formula_version": FORMULA,
                "window_start_utc": moment.isoformat(),
                "derived_window_end_utc": (moment+dt.timedelta(minutes=5)).isoformat(),
                "observed_at_utc": None,
                "verified_available_after_utc": None,
                "fill_count": cur[2],
                "flow_decay_proxy": decay,
                "quality": quality,
                "source_archive_sha256": sha,
            }
            if any(actual.get(k) != v for k, v in expected.items()):
                raise ValueError("QC_5M_BLOCK_FIELDS_MISMATCH " + str(ix))
            match_decimal(actual["signed_aggressor_quote_usdt"], cur[1], "flow")
            match_decimal(actual["last_trade_price"], cur[0], "last_price")
            match_decimal(
                actual["previous_block_last_price"],
                pr[0] if pr else None, "prior_last_price"
            )
            match_decimal(
                actual["previous_previous_block_last_price"],
                pp[0] if pp else None, "prior2_last_price"
            )
            match_decimal(actual["flow_ratio_abs_current_to_prior"], ratio, "ratio")
            total += 1
        if stream.readline():
            raise ValueError("QC_UNEXPECTED_EXTRA_OUTPUT_ROWS")
    if (total != days * 288 or result["eligible"] != eligible
            or result["decays"] != decays or result["unavailable"] != nulls
            or sum(x["fills"] for x in checked_sources) != result["fills"]):
        raise ValueError("QC_TOTALS_MISMATCH")
    checker = pathlib.Path(__file__).resolve()
    checker_snapshot = folder / "source_snapshot/independent_checker.py"
    if checker_snapshot.exists():
        raise FileExistsError("QC_CHECKER_SNAPSHOT_ALREADY_EXISTS")
    shutil.copyfile(checker, checker_snapshot)
    report = {"status": QC_PASS, "symbol": symbol, "days": days,
              "verified_5m_rows": total, "eligible": eligible,
              "flow_decays": decays, "null_rows": nulls,
              "original_sha256": result["output_sha256"],
              "checker_sha256": hash_file(checker),
              "source_days": checked_sources,
              "receiver_clock_verified": False,
              "source_continuity_verified": False,
              "research_ready": False}
    part = receipt.with_name(receipt.name + ".part")
    with part.open("x", encoding="utf-8") as output:
        json.dump(report, output, sort_keys=True, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    part.rename(receipt)
    print(json.dumps({"status": QC_PASS, "symbol": symbol,
                      "verified_5m_rows": total,
                      "receipt_sha256": hash_file(receipt)}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=SYMS, required=True)
    p.add_argument("--days", type=int, choices=(2, 30), required=True)
    args = p.parse_args()
    verify(args.symbol, args.days)
