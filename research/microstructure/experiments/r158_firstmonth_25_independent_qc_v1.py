#!/usr/bin/env python3
"""Independent full-row QC for Issue #158 / #25 Spot/Perp divergence.

Does not import the primary #25 reader/materializer. Re-reads immutable Spot
originals and original Bybit USDT-linear public trades, independently computes
executed-base VWAP and minute divergence, verifies source hashes/provenance, and
compares every one of 43,200 materialized minute rows.

Historical local receiver/available_after is unavailable, so PASS remains
DESCRIPTIVE_ONLY_NOT_RESEARCH_READY.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
DAYS = 30
SPOT_ROOT = Path("/data/cripta/research/cache/r158_spot_firstmonth_20261030")
PERP_ROOT = Path("/data/cripta/datasets/raw/20260518_20260816")
RUN_ROOT = Path("/data/cripta/research/runs/r158_firstmonth_25_20261031")
FORMULA = "R158_SPOT_PERP_EXECUTED_VWAP_1M_V2"
VERSION = "R158_FIRSTMONTH_25_INDEPENDENT_FULLROW_QC_V1"
REL_BOUND = Decimal("1e-14")
ABS_BOUND = Decimal("1e-16")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decimal(value: object, *, positive: bool = False) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("QC_INVALID_DECIMAL") from exc
    if not number.is_finite() or (positive and number <= 0):
        raise ValueError("QC_INVALID_DECIMAL_SIGN")
    return number


def actor_uid(require_actor: bool) -> int:
    if not hasattr(os, "geteuid"):
        if require_actor:
            raise OSError("UNIX_EFFECTIVE_UID_REQUIRED")
        return -1
    uid = os.geteuid()
    if require_actor:
        import pwd

        if uid != pwd.getpwnam("cripta").pw_uid:
            raise PermissionError("EFFECTIVE_ACTOR_NOT_CRIPTA")
    return uid


def load_spot_manifest(root: Path) -> tuple[str, dict[tuple[str, str], dict]]:
    path = root / "SOURCE_MANIFEST.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        data.get("status") != "COMPLETE_IMMUTABLE_RESEARCH_SOURCE_CORPUS"
        or data.get("files") != 150
        or data.get("expected_files") != 150
    ):
        raise ValueError("QC_SPOT_SOURCE_CORPUS_NOT_COMPLETE")
    index = {}
    for item in data.get("file_records", []):
        key = (item.get("symbol"), item.get("date_utc"))
        if key in index:
            raise ValueError("QC_DUPLICATE_SPOT_SOURCE_MANIFEST_KEY")
        index[key] = item
    if len(index) != 150:
        raise ValueError("QC_SPOT_SOURCE_MANIFEST_CARDINALITY")
    return sha256(path), index


def aggregate_spot(
    path: Path,
    symbol: str,
    day: date,
    expected: dict,
) -> tuple[dict[int, dict], int]:
    if path.stat().st_size != int(expected["bytes"]):
        raise ValueError("QC_SPOT_SOURCE_SIZE_DRIFT")
    if sha256(path) != expected["sha256"]:
        raise ValueError("QC_SPOT_SOURCE_HASH_DRIFT")
    start_ms = int(
        datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000
    )
    end_ms = start_ms + 86_400_000
    agg: dict[int, dict] = {}
    rows = 0
    prior = None
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["id", "timestamp", "price", "volume", "side", "rpi"]:
            raise ValueError("QC_SPOT_HEADER")
        for row in reader:
            if not row["id"]:
                raise ValueError("QC_SPOT_ID_MISSING")
            ts = int(row["timestamp"])
            if not start_ms <= ts < end_ms:
                raise ValueError("QC_SPOT_TIMESTAMP_OUTSIDE_DAY")
            if prior is not None and ts < prior:
                raise ValueError("QC_SPOT_TIMESTAMP_BACKWARDS")
            prior = ts
            if row["side"].strip().lower() not in ("buy", "sell"):
                raise ValueError("QC_SPOT_SIDE")
            price = decimal(row["price"], positive=True)
            qty = decimal(row["volume"], positive=True)
            minute = (ts - start_ms) // 60_000
            cell = agg.setdefault(
                minute, {"count": 0, "base": Decimal(0), "quote": Decimal(0)}
            )
            cell["count"] += 1
            cell["base"] += qty
            cell["quote"] += price * qty
            rows += 1
    if rows == 0:
        raise ValueError("QC_SPOT_EMPTY")
    return agg, rows


def aggregate_perp(
    path: Path, symbol: str, day: date
) -> tuple[dict[int, dict], dict]:
    start_s = Decimal(
        int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    )
    end_s = start_s + Decimal(86_400)
    agg: dict[int, dict] = {}
    rows = rounding_rows = 0
    max_abs = Decimal(0)
    max_rel = Decimal(0)
    prior = None
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {
            "timestamp", "price", "size", "side", "symbol", "trdMatchID",
            "homeNotional", "foreignNotional",
        }
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("QC_PERP_HEADER")
        for row in reader:
            ts = decimal(row["timestamp"])
            if not start_s <= ts < end_s:
                raise ValueError("QC_PERP_TIMESTAMP_OUTSIDE_DAY")
            if prior is not None and ts < prior:
                raise ValueError("QC_PERP_TIMESTAMP_BACKWARDS")
            prior = ts
            if row["symbol"] != symbol or row["side"] not in ("Buy", "Sell"):
                raise ValueError("QC_PERP_IDENTITY")
            if not row["trdMatchID"]:
                raise ValueError("QC_PERP_TRADE_ID_MISSING")
            price = decimal(row["price"], positive=True)
            qty = decimal(row["size"], positive=True)
            native_base = decimal(row["homeNotional"], positive=True)
            native_quote = decimal(row["foreignNotional"], positive=True)
            if native_base != qty:
                raise ValueError("QC_PERP_NATIVE_BASE_PARITY")
            expected_quote = price * qty
            diff = abs(native_quote - expected_quote)
            allowed = max(ABS_BOUND, REL_BOUND * expected_quote)
            if diff > allowed:
                raise ValueError("QC_PERP_NATIVE_QUOTE_PARITY")
            if diff:
                rounding_rows += 1
                max_abs = max(max_abs, diff)
                max_rel = max(max_rel, diff / expected_quote)
            minute = int(
                ((ts - start_s) / Decimal(60)).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )
            cell = agg.setdefault(
                minute, {"count": 0, "base": Decimal(0), "quote": Decimal(0)}
            )
            cell["count"] += 1
            cell["base"] += qty
            cell["quote"] += expected_quote
            rows += 1
    if rows == 0:
        raise ValueError("QC_PERP_EMPTY")
    return agg, {
        "rows": rows,
        "rounding_rows": rounding_rows,
        "max_abs_rounding": str(max_abs),
        "max_relative_rounding": str(max_rel),
        "sha256": sha256(path),
    }


def expected_day_rows(
    symbol: str,
    day: date,
    spot_root: Path,
    perp_root: Path,
    spot_item: dict,
) -> tuple[list[dict], dict]:
    spot_path = spot_root / symbol / f"{symbol}_{day}.csv.gz"
    perp_path = (
        perp_root / symbol / "public_trades" / f"{symbol}{day}.csv.gz"
    )
    spot, spot_rows = aggregate_spot(spot_path, symbol, day, spot_item)
    perp, perp_meta = aggregate_perp(perp_path, symbol, day)
    rows = []
    observed = 0
    for minute in range(1440):
        s = spot.get(minute)
        p = perp.get(minute)
        stamp = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(
            minutes=minute
        )
        base = {
            "venue_id": "Bybit",
            "instrument_id": symbol,
            "indicator_id": 25,
            "formula_version": FORMULA,
            "minute_start_utc": stamp.isoformat(),
            "observed_at_utc": None,
            "verified_available_after_utc": None,
            "minute_utc": minute,
        }
        if (
            s is None
            or p is None
            or s["base"] <= 0
            or p["base"] <= 0
        ):
            rows.append(
                {
                    **base,
                    "status": "NO_DATA",
                    "spot_vwap": None,
                    "perp_vwap": None,
                    "divergence_bps": None,
                    "spot_fills": s["count"] if s else 0,
                    "perp_fills": p["count"] if p else 0,
                    "spot_base_qty": str(s["base"]) if s else None,
                    "perp_base_qty": str(p["base"]) if p else None,
                }
            )
            continue
        sv = s["quote"] / s["base"]
        pv = p["quote"] / p["base"]
        rows.append(
            {
                **base,
                "status": "OBSERVED",
                "spot_vwap": str(sv),
                "perp_vwap": str(pv),
                "divergence_bps": str(Decimal(10000) * (pv - sv) / sv),
                "spot_fills": s["count"],
                "perp_fills": p["count"],
                "spot_base_qty": str(s["base"]),
                "perp_base_qty": str(p["base"]),
                "spot_quote_usdt": str(s["quote"]),
                "perp_quote_usdt": str(p["quote"]),
            }
        )
        observed += 1
    return rows, {
        "spot_sha256": spot_item["sha256"],
        "spot_rows": spot_rows,
        "perp_sha256": perp_meta["sha256"],
        "perp_rows": perp_meta["rows"],
        "perp_rounding_rows": perp_meta["rounding_rows"],
        "observed_minutes": observed,
    }


def read_output(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def write_json_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def check_symbol(
    symbol: str,
    *,
    spot_root: Path = SPOT_ROOT,
    perp_root: Path = PERP_ROOT,
    run_root: Path = RUN_ROOT,
    require_actor: bool = True,
) -> dict:
    if symbol not in COHORT:
        raise ValueError("SYMBOL_OUTSIDE_OWNER_COHORT")
    actor_uid(require_actor)
    source_manifest_sha, spot_index = load_spot_manifest(spot_root)
    target = run_root / symbol
    final_path = target / "run_final.json"
    provenance_path = target / "input_provenance.json"
    output_path = target / "divergence_1m.jsonl.gz"
    report_path = target / "independent_qc_report.json"
    if report_path.exists():
        raise FileExistsError("IMMUTABLE_QC_REPORT_ALREADY_EXISTS")
    for path in (final_path, provenance_path, output_path):
        if not path.is_file():
            raise FileNotFoundError(f"QC_REQUIRED_ARTIFACT_MISSING {path}")

    final = json.loads(final_path.read_text())
    provenance = json.loads(provenance_path.read_text())["days"]
    if (
        final.get("status")
        != "SPOT_PERP_25_FIRSTMONTH_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
        or final.get("formula_version") != FORMULA
        or final.get("symbol") != symbol
        or final.get("days") != DAYS
        or final.get("spot_source_manifest_sha256") != source_manifest_sha
    ):
        raise ValueError("QC_MATERIALIZED_RUN_IDENTITY")
    if sha256(output_path) != final["output_sha256"]:
        raise ValueError("QC_MATERIALIZED_OUTPUT_HASH_DRIFT")
    if len(provenance) != DAYS:
        raise ValueError("QC_PROVENANCE_DAY_COUNT")

    actual = iter(read_output(output_path))
    checked = observed = 0
    source_days = []
    for offset in range(DAYS):
        day = START + timedelta(days=offset)
        item = spot_index[(symbol, day.isoformat())]
        expected, meta = expected_day_rows(
            symbol, day, spot_root, perp_root, item
        )
        prov = provenance[offset]
        if (
            prov["date_utc"] != day.isoformat()
            or prov["spot_sha256"] != meta["spot_sha256"]
            or prov["perpetual_sha256"] != meta["perp_sha256"]
            or int(prov["spot_rows"]) != meta["spot_rows"]
            or int(prov["perpetual_rows"]) != meta["perp_rows"]
        ):
            raise ValueError("QC_SOURCE_PROVENANCE_DRIFT")
        for row in expected:
            try:
                got = next(actual)
            except StopIteration as exc:
                raise ValueError("QC_OUTPUT_TRUNCATED") from exc
            if got != row:
                raise ValueError(
                    f"QC_FULLROW_MISMATCH day={day} minute={row['minute_utc']}"
                )
            checked += 1
            observed += int(row["status"] == "OBSERVED")
        source_days.append({"date_utc": day.isoformat(), **meta})
    try:
        next(actual)
        raise ValueError("QC_OUTPUT_HAS_EXTRA_ROWS")
    except StopIteration:
        pass

    if checked != DAYS * 1440 or checked != final["rows"]:
        raise ValueError("QC_ROW_COUNT_MISMATCH")
    if observed != final["observed_minutes"]:
        raise ValueError("QC_OBSERVED_MINUTE_COUNT_MISMATCH")
    report = {
        "status": "SPOT_PERP_25_FIRSTMONTH_INDEPENDENT_FULLROW_QC_PASS_NOT_RESEARCH_READY",
        "checker_version": VERSION,
        "checker_sha256": sha256(Path(__file__).resolve()),
        "symbol": symbol,
        "indicator_id": 25,
        "formula_version": FORMULA,
        "checked_days": DAYS,
        "checked_rows": checked,
        "observed_minutes": observed,
        "no_data_minutes": checked - observed,
        "materialized_output_sha256": sha256(output_path),
        "spot_source_manifest_sha256": source_manifest_sha,
        "source_days": source_days,
        "receiver_clock_verified": False,
        "verified_available_after": False,
        "research_ready": False,
    }
    write_json_new(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=COHORT)
    args = parser.parse_args()
    print(json.dumps(check_symbol(args.symbol), sort_keys=True))


if __name__ == "__main__":
    main()
