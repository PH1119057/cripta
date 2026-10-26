#!/usr/bin/env python3
"""#158: original executed Bybit Spot/Perpetual 1m VWAP divergence, research only.

Read-only calculator. Requires original daily CSV.GZ archives already staged
and independently proven as originals. Does not download or write files.
Historical exchange event-time does not prove collector observed/available_after.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path

VERSION = "R158_SPOT_PERP_EXECUTED_VWAP_1M_V1"
COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST_DAY = date(2026, 5, 18)
LAST_DAY = date(2026, 6, 16)
MINUTES_PER_DAY = 1440


def original_archive_path(root: Path, symbol: str, day: date, venue: str) -> Path:
    """Roots have one subdirectory per symbol; never silently fetch missing files."""
    if symbol not in COHORT or not FIRST_DAY <= day <= LAST_DAY:
        raise ValueError("out-of-cohort symbol or UTC date")
    if venue == "spot":
        return root / symbol / f"{symbol}_{day.isoformat()}.csv.gz"
    if venue == "perpetual":
        return root / symbol / f"{symbol}{day.isoformat()}.csv.gz"
    raise ValueError("invalid venue")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal(value: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid numeric market value: {value!r}") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("non-finite or nonpositive numeric market value")
    return result


def _minute(ts_raw: str, venue: str) -> int:
    value = _decimal(ts_raw)
    # Bybit original Spot CSV is integer milliseconds; Perpetual is seconds.
    if venue == "spot":
        return int((value / Decimal(60000)).to_integral_value(rounding=ROUND_FLOOR))
    return int((value / Decimal(60)).to_integral_value(rounding=ROUND_FLOOR))


def aggregate_archive(path: Path, symbol: str, day: date, venue: str) -> dict:
    """Accumulate executed base volume and price*volume per UTC minute.

    Missing source != zero market activity. A present but unreadable / malformed
    file is an error, never NO_DATA. File SHA applies to exact compressed bytes.
    """
    if not path.is_file():
        return {"source_state": "SOURCE_MISSING", "source_path": str(path),
                "source_sha256": None, "rows": 0, "minutes": {}}
    sha = _sha256_file(path)
    day_start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() // 60)
    aggregate: dict[int, dict[str, Decimal | int]] = {}
    rows = 0
    last_minute: int | None = None
    last_exchange_ts: Decimal | None = None
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"timestamp", "price", "volume", "side", "id"} if venue == "spot" else {"timestamp", "price", "size", "side", "symbol", "trdMatchID"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"invalid {venue} archive columns at {path}")
        for row in reader:
            source_ts = _decimal(row["timestamp"])
            minute = _minute(row["timestamp"], venue)
            if minute < day_start or minute >= day_start + MINUTES_PER_DAY:
                raise ValueError("out-of-day source timestamp")
            if last_exchange_ts is not None and source_ts < last_exchange_ts:
                raise ValueError("unsorted original exchange timestamps")
            if last_minute is not None and minute < last_minute:
                raise ValueError("unsorted original exchange minutes")
            last_exchange_ts = source_ts
            last_minute = minute
            if venue == "perpetual" and row["symbol"] != symbol:
                raise ValueError("perpetual archive symbol mismatch")
            if row["side"].strip().lower() not in ("buy", "sell"):
                raise ValueError("invalid exchange trade side")
            price = _decimal(row["price"], positive=True)
            qty = _decimal(row["volume" if venue == "spot" else "size"], positive=True)
            cell = aggregate.setdefault(minute - day_start, {"count": 0, "base": Decimal(0), "quote": Decimal(0)})
            cell["count"] += 1
            cell["base"] += qty
            cell["quote"] += price * qty
            rows += 1
    return {"source_state": "SOURCE_PRESENT", "source_path": str(path),
            "source_sha256": sha, "rows": rows, "minutes": aggregate}


def calculate_day(symbol: str, day: date, spot_root: Path, perp_root: Path) -> tuple[dict, list[dict]]:
    """Returns a daily manifest and all 1440 minute values; no side effects."""
    sp = aggregate_archive(original_archive_path(spot_root, symbol, day, "spot"), symbol, day, "spot")
    pp = aggregate_archive(original_archive_path(perp_root, symbol, day, "perpetual"), symbol, day, "perpetual")
    minutes: list[dict] = []
    both = 0
    for i in range(MINUTES_PER_DAY):
        spot, perp = sp["minutes"].get(i), pp["minutes"].get(i)
        if not spot or not perp or spot["base"] <= 0 or perp["base"] <= 0:
            minutes.append({"minute_utc": i, "status": "NO_DATA", "spot_vwap": None,
                            "perp_vwap": None, "divergence_bps": None,
                            "spot_fills": spot["count"] if spot else 0,
                            "perp_fills": perp["count"] if perp else 0,
                            "spot_base_qty": str(spot["base"]) if spot else None,
                            "perp_base_qty": str(perp["base"]) if perp else None})
            continue
        sv = spot["quote"] / spot["base"]
        pv = perp["quote"] / perp["base"]
        bps = Decimal(10000) * (pv - sv) / sv
        minutes.append({"minute_utc": i, "status": "OBSERVED", "spot_vwap": str(sv),
                        "perp_vwap": str(pv), "divergence_bps": str(bps),
                        "spot_fills": spot["count"], "perp_fills": perp["count"],
                        "spot_base_qty": str(spot["base"]), "perp_base_qty": str(perp["base"]),
                        "spot_quote_usdt": str(spot["quote"]), "perp_quote_usdt": str(perp["quote"])})
        both += 1
    manifest = {"algorithm": VERSION, "source": "Bybit original executed trades",
                "script_sha256": _sha256_file(Path(__file__)),
                "symbol": symbol, "date_utc": day.isoformat(), "observed_minutes": both,
                "no_data_minutes": MINUTES_PER_DAY - both,
                "spot": {k: sp[k] for k in ("source_state", "source_path", "source_sha256", "rows")},
                "perpetual": {k: pp[k] for k in ("source_state", "source_path", "source_sha256", "rows")},
                "time_basis": "exchange_event_timestamp_only",
                "local_received_at": None, "available_after": None,
                "readiness": "DESCRIPTIVE_ONLY_NOT_RESEARCH_READY",
                "window": "[UTC minute start, next minute start); no carry-forward"}
    return manifest, minutes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=COHORT)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--spot-root", required=True, type=Path)
    parser.add_argument("--perp-root", required=True, type=Path)
    parser.add_argument("--minute-rows", action="store_true", help="emit all minute rows to stdout after manifest")
    args = parser.parse_args()
    manifest, minutes = calculate_day(args.symbol, args.date, args.spot_root, args.perp_root)
    print(json.dumps({"type": "manifest", **manifest}, sort_keys=True))
    if args.minute_rows:
        for row in minutes:
            print(json.dumps({"type": "minute", **row}, sort_keys=True))


if __name__ == "__main__":
    main()
