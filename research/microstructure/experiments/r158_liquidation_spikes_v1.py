#!/usr/bin/env python3
"""#158: first-month native-vendor liquidation object coverage and 1/5/15m counts.

RESEARCH ONLY. Rechecks archived original object bytes against original vendor
receipts; derives provisional measures from existing NORMALIZED CSV.GZ. Does
not decode native Parquet or establish row-level original/native parity.
Missing source-object hours are UNKNOWN, not zero provider activity.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

VERSION = "R158_LIQ_PUBLISHED_OBJECTS_V1"
COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
END_EXCLUSIVE = date(2026, 6, 17)
HOURS_PER_MONTH = 30 * 24
DAY_MINUTES = 24 * 60
WINDOWS = (1, 5, 15)
KEY_PATTERN = re.compile(
    r"^bybit/(20\d\d-\d\d-\d\d)/(\d\d)/([A-Z0-9]+)_liquidations[.]parquet[.]zst$"
)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_amount(value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("invalid numeric liquidation field") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("invalid positive liquidation field")
    return amount


def original_receipts(
    manifest: Path, source_root: Path, symbol: str, *, verify_hashes: bool = True
) -> dict[str, tuple[date, int]]:
    """Validate all original object receipts for a single instrument/month."""
    if symbol not in COHORT:
        raise ValueError("instrument outside approved cohort")
    result: dict[str, tuple[date, int]] = {}
    with manifest.open("rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("symbol") != symbol:
                continue
            day = date.fromisoformat(item["date"])
            if not START <= day < END_EXCLUSIVE:
                continue
            match = KEY_PATTERN.fullmatch(item["key"])
            if not match or match.group(1) != day.isoformat():
                raise ValueError("malformed original vendor object key/date")
            hour = int(match.group(2))
            if hour not in range(24) or match.group(3) != symbol:
                raise ValueError("wrong original vendor symbol or hour")
            if item.get("hour") != f"{hour:02d}":
                raise ValueError("original vendor receipt hour does not match object key")
            relative = "raw/" + item["key"].removeprefix("bybit/")
            if relative in result:
                raise ValueError("duplicated original source receipt")
            source = source_root / relative
            if not source.is_file():
                raise ValueError("original source object missing")
            if source.stat().st_size != int(item["bytes"]):
                raise ValueError("original source size does not match receipt")
            digest = item["sha256"]
            if not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("invalid original source sha256 receipt")
            if verify_hashes and _hash(source) != digest:
                raise ValueError("original source sha256 mismatch")
            result[relative] = (day, hour)
    return result


def calculate(
    symbol: str, vendor_root: Path, normalized_root: Path, *, verify_hashes: bool = True
) -> dict:
    """Output source-covered counts and price*qty for each H1/H5/H15 window.

    All event timestamps are VENDOR received-time, not CRIPTA local receive.
    No claims about native Parquet row equivalence, fill prices or spot joins.
    """
    receipts = original_receipts(
        vendor_root / "OBJECTS.jsonl", vendor_root, symbol, verify_hashes=verify_hashes
    )
    covered = len(receipts)
    if covered > HOURS_PER_MONTH:
        raise ValueError("more object hours than cohort permits")
    normal = normalized_root / f"{symbol}.csv.gz"
    if not normal.is_file():
        raise ValueError("normalized data is missing; no source fallback")
    start_us = int(datetime(START.year, START.month, START.day, tzinfo=UTC).timestamp()) * 1000000
    end_us = int(
        datetime(END_EXCLUSIVE.year, END_EXCLUSIVE.month, END_EXCLUSIVE.day, tzinfo=UTC).timestamp()
    ) * 1000000
    bins: dict[int, dict[int, tuple[int, Decimal]]] = {
        length: defaultdict(lambda: (0, Decimal(0))) for length in WINDOWS
    }
    events = 0
    notional = Decimal(0)
    sides: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    represented_sources: set[str] = set()
    with gzip.open(normal, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "observed_timestamp_us", "symbol", "source_path", "price",
            "size", "liquidated_position_side", "order_status"
        }
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("normalized source columns missing")
        for row in reader:
            micros = int(row["observed_timestamp_us"])
            if micros < start_us or micros >= end_us:
                continue
            if row["symbol"] != symbol:
                raise ValueError("normalized source symbol mismatch")
            source_name = row["source_path"]
            if source_name not in receipts:
                raise ValueError("normalized row has no verified original object")
            source_day, source_hour = receipts[source_name]
            observed = datetime.fromtimestamp(micros // 1000000, UTC)
            if observed.date() != source_day or observed.hour != source_hour:
                raise ValueError("vendor receive time outside original object hour")
            if row["liquidated_position_side"] not in ("LONG", "SHORT"):
                raise ValueError("unknown liquidated position side")
            if not row["order_status"]:
                raise ValueError("missing source event order status")
            amount = _valid_amount(row["price"]) * _valid_amount(row["size"])
            events += 1
            notional += amount
            represented_sources.add(source_name)
            sides[row["liquidated_position_side"]] += 1
            statuses[row["order_status"]] += 1
            for length in WINDOWS:
                key = micros // (60000000 * length)
                old_count, old_quote = bins[length][key]
                bins[length][key] = (old_count + 1, old_quote + amount)

    def window_metrics(length: int) -> dict:
        sample = bins[length]
        max_count = max(sample, key=lambda k: (sample[k][0], -k), default=None)
        max_quote = max(sample, key=lambda k: (sample[k][1], -k), default=None)
        return {
            "observed_source_file_bins": covered * (60 // length),
            "bins_with_records": len(sample),
            "bins_with_no_file": (HOURS_PER_MONTH - covered) * (60 // length),
            "max_count": sample[max_count][0] if max_count is not None else None,
            "max_count_at_utc": datetime.fromtimestamp(
                max_count * 60 * length, UTC
            ).isoformat() if max_count is not None else None,
            "max_notional_usdt": str(sample[max_quote][1]) if max_quote is not None else None,
            "max_notional_at_utc": datetime.fromtimestamp(
                max_quote * 60 * length, UTC
            ).isoformat() if max_quote is not None else None,
        }

    return {
        "version": VERSION, "symbol": symbol,
        "source_month_start": START.isoformat(),
        "source_month_end_exclusive": END_EXCLUSIVE.isoformat(),
        "original_object_receipts_sha256_verified": verify_hashes,
        "original_object_hours": covered,
        "missing_original_object_hours": HOURS_PER_MONTH - covered,
        "missing_original_object_hours_status": "UNKNOWN_NOT_ZERO",
        "normalized_rows": events,
        "normalized_source_objects_used": len(represented_sources),
        "published_price_x_quantity_usdt": str(notional),
        "liquidated_position_sides": dict(sides),
        "native_order_status": dict(statuses),
        "windows": {f"{n}m": window_metrics(n) for n in WINDOWS},
        "vendor_receive_time": "normalized_from_vendor_received_time",
        "cripta_local_available_after": None,
        "native_parquet_row_parity": "NOT_VERIFIED",
        "source_publication_completeness": "UNKNOWN",
        "readiness": "DESCRIPTIVE_ONLY_NOT_RESEARCH_READY",
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", choices=COHORT, required=True)
    p.add_argument("--vendor-root", type=Path, required=True)
    p.add_argument("--normalized-root", type=Path, required=True)
    args = p.parse_args()
    result = calculate(args.symbol, args.vendor_root, args.normalized_root)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
