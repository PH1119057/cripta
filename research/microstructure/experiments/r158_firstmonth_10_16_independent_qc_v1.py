#!/usr/bin/env python3
"""Independent full-period QC for Issue #158 materialized #10/#16 outputs.

This checker intentionally does NOT import the primary #10/#16 readers. It
reconstructs the required semantics independently from original OB200 and
executed Perp archives and compares every minute row to materialized outputs.

Production CLI is fixed to the first-month raw/run roots. It writes only one
new independent_qc_report.json inside an already completed symbol run.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
RAW_ROOT = Path("/data/cripta/datasets/raw/20260518_20260816")
RUN_ROOT = Path("/data/cripta/research/runs/r158_firstmonth_10_16_20261030")
OFI_VERSION = "R158_OFI_EXACT_PRICE_TOP5_UNION_V1"
ABS_VERSION = "R158_ABSORPTION_COMPONENTS_NATIVE_V1"
CHECKER_VERSION = "R158_FIRSTMONTH_10_16_INDEPENDENT_QC_V1"


def _effective_actor_uid(require_actor: bool) -> int | None:
    getter = getattr(os, "geteuid", None)
    if getter is None:
        if require_actor:
            raise RuntimeError("EFFECTIVE_UID_UNAVAILABLE")
        return None
    uid = int(getter())
    if require_actor:
        import pwd

        if uid != pwd.getpwnam("cripta").pw_uid:
            raise PermissionError("EFFECTIVE_ACTOR_NOT_CRIPTA")
    return uid


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def positive(value, *, zero: bool = False) -> Decimal:
    try:
        number = Decimal(value)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError("INVALID_DECIMAL") from exc
    if not number.is_finite() or number < 0 or (not zero and number == 0):
        raise ValueError("INVALID_DECIMAL_SIGN")
    return number


def trim200(book: dict[Decimal, Decimal], side: str) -> dict[Decimal, Decimal]:
    keys = sorted(book, reverse=side == "b")[:200]
    return {price: book[price] for price in keys}


def visible(book: dict[Decimal, Decimal], side: str, price: Decimal):
    if price in book:
        return book[price]
    if not book:
        return None
    boundary = min(book) if side == "b" else max(book)
    if (side == "b" and price >= boundary) or (
        side == "a" and price <= boundary
    ):
        return Decimal(0)
    return None


def minute_iso(day: date, minute: int) -> str:
    return (
        datetime(day.year, day.month, day.day, tzinfo=UTC)
        + timedelta(minutes=minute)
    ).isoformat()


def empty_book_cell():
    return {
        "ofi_events": 0,
        "ofi_unknown": 0,
        "ofi_bid": Decimal(0),
        "ofi_ask": Decimal(0),
        "abs_valid": 0,
        "abs_unknown": 0,
        "b_add": Decimal(0),
        "b_remove": Decimal(0),
        "a_add": Decimal(0),
        "a_remove": Decimal(0),
    }


def apply_entries(
    prior: dict[Decimal, Decimal],
    entries,
    *,
    snapshot: bool,
) -> tuple[dict[Decimal, Decimal], set[Decimal]]:
    result = {} if snapshot else dict(prior)
    touched = set()
    for item in entries:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("MALFORMED_BOOK_ENTRY")
        price = positive(item[0])
        qty = positive(item[1], zero=not snapshot)
        if price in touched:
            raise ValueError("DUPLICATE_PRICE_IN_EVENT")
        touched.add(price)
        if qty:
            result[price] = qty
        else:
            result.pop(price, None)
    return result, touched


def independent_book_day(path: Path, symbol: str, day: date):
    start_ms = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    cells = defaultdict(empty_book_cell)
    state = None
    prior_u = prior_seq = prior_ts = None
    record_count = snapshots = gaps = suffix_records = 0
    seen_suffix = False
    prior_source_ts = None
    end_ms = start_ms + 86400000
    with zipfile.ZipFile(path) as archive:
        member = f"{day}_{symbol}_ob200.data"
        if archive.namelist() != [member]:
            raise ValueError("BAD_OB200_MEMBER")
        with archive.open(member) as stream:
            for line in stream:
                event = json.loads(line)
                data = event["data"]
                ts = int(event["ts"])
                if prior_source_ts is not None and ts < prior_source_ts:
                    raise ValueError("OB200_SOURCE_TIME_BACKWARDS")
                prior_source_ts = ts
                if (
                    event["topic"] != f"orderbook.200.{symbol}"
                    or data["s"] != symbol
                ):
                    raise ValueError("OB200_IDENTITY")
                if ts < start_ms:
                    raise ValueError("OB200_BEFORE_DAY")
                if ts >= end_ms:
                    suffix_records += 1
                    seen_suffix = True
                    if suffix_records > 1000:
                        raise ValueError("OB200_UNBOUNDED_NEXT_DAY_SUFFIX")
                    continue
                if seen_suffix:
                    raise ValueError("OB200_REENTERED_DAY_AFTER_SUFFIX")
                minute = (ts - start_ms) // 60000
                cell = cells[minute]
                record_count += 1

                if event["type"] == "snapshot":
                    if snapshots:
                        cell["ofi_unknown"] += 1
                        cell["abs_unknown"] += 1
                    new_state = {}
                    for side in ("b", "a"):
                        raw, _ = apply_entries({}, data[side], snapshot=True)
                        if len(raw) != 200:
                            raise ValueError("SNAPSHOT_NOT_200")
                        new_state[side] = trim200(raw, side)
                    if max(new_state["b"]) >= min(new_state["a"]):
                        raise ValueError("CROSSED_SNAPSHOT")
                    state = new_state
                    prior_u = int(data["u"])
                    prior_seq = int(data["seq"])
                    prior_ts = ts
                    snapshots += 1
                    continue
                if event["type"] != "delta":
                    raise ValueError("UNKNOWN_OB200_EVENT")

                if (
                    state is None
                    or int(data["u"]) != prior_u + 1
                    or int(data["seq"]) <= prior_seq
                    or ts < prior_ts
                ):
                    cell["ofi_unknown"] += 1
                    cell["abs_unknown"] += 1
                    if state is not None:
                        gaps += 1
                    state = None
                    prior_u = prior_seq = prior_ts = None
                    continue

                before = {side: dict(state[side]) for side in ("b", "a")}
                after = {}
                touched_by_side = {}
                for side in ("b", "a"):
                    raw, touched = apply_entries(
                        before[side], data[side], snapshot=False
                    )
                    after[side] = trim200(raw, side)
                    touched_by_side[side] = touched
                if (
                    len(after["b"]) < 5
                    or len(after["a"]) < 5
                    or max(after["b"]) >= min(after["a"])
                ):
                    cell["ofi_unknown"] += 1
                    cell["abs_unknown"] += 1
                    gaps += 1
                    state = None
                    prior_u = prior_seq = prior_ts = None
                    continue

                ofi_known = True
                ofi_quote = {}
                for side in ("b", "a"):
                    old5 = set(
                        sorted(before[side], reverse=side == "b")[:5]
                    )
                    new5 = set(
                        sorted(after[side], reverse=side == "b")[:5]
                    )
                    value = Decimal(0)
                    for price in old5 | new5:
                        q0 = visible(before[side], side, price)
                        q1 = visible(after[side], side, price)
                        if q0 is None or q1 is None:
                            ofi_known = False
                        else:
                            value += price * (q1 - q0)
                    ofi_quote[side] = value
                cell["ofi_events"] += 1
                if not ofi_known:
                    cell["ofi_unknown"] += 1
                else:
                    cell["ofi_bid"] += ofi_quote["b"]
                    cell["ofi_ask"] += ofi_quote["a"]

                abs_known = True
                changes = {}
                for side in ("b", "a"):
                    additions = Decimal(0)
                    removals = Decimal(0)
                    for price in touched_by_side[side]:
                        q0 = visible(before[side], side, price)
                        q1 = visible(after[side], side, price)
                        if q0 is None or q1 is None:
                            abs_known = False
                            continue
                        delta = price * (q1 - q0)
                        if delta > 0:
                            additions += delta
                        else:
                            removals -= delta
                    changes[side] = (additions, removals)
                if not abs_known:
                    cell["abs_unknown"] += 1
                else:
                    cell["abs_valid"] += 1
                    cell["b_add"] += changes["b"][0]
                    cell["b_remove"] += changes["b"][1]
                    cell["a_add"] += changes["a"][0]
                    cell["a_remove"] += changes["a"][1]

                state = after
                prior_u = int(data["u"])
                prior_seq = int(data["seq"])
                prior_ts = ts

    return cells, {
        "sha256": sha256(path),
        "records": record_count,
        "snapshots": snapshots,
        "gaps": gaps,
        "source_suffix_records": suffix_records,
    }


def empty_trade_cell():
    return {
        "fills": 0,
        "buy": Decimal(0),
        "sell": Decimal(0),
        "first": None,
        "last": None,
    }


def independent_trade_day(path: Path, symbol: str, day: date):
    start_minute = int(
        datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() // 60
    )
    cells = defaultdict(empty_trade_cell)
    prior_ts = None
    rows = 0
    with gzip.open(path, "rt", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        needed = {"timestamp", "symbol", "side", "size", "price", "trdMatchID"}
        if not needed.issubset(reader.fieldnames or []):
            raise ValueError("BAD_TRADE_HEADER")
        for row in reader:
            ts = positive(row["timestamp"])
            if prior_ts is not None and ts < prior_ts:
                raise ValueError("TRADE_TIME_BACKWARDS")
            prior_ts = ts
            if row["symbol"] != symbol or row["side"] not in ("Buy", "Sell"):
                raise ValueError("TRADE_IDENTITY")
            minute = int(
                (ts / Decimal(60)).to_integral_value(rounding=ROUND_FLOOR)
            )
            if not start_minute <= minute < start_minute + 1440:
                raise ValueError("TRADE_OUTSIDE_DAY")
            price = positive(row["price"])
            qty = positive(row["size"])
            cell = cells[minute - start_minute]
            if cell["first"] is None:
                cell["first"] = price
            cell["last"] = price
            cell["fills"] += 1
            cell["buy" if row["side"] == "Buy" else "sell"] += price * qty
            rows += 1
    return cells, {"sha256": sha256(path), "rows": rows}


def expected_rows(symbol: str, day: date, book_cells: dict, trade_cells: dict):
    ofi_rows = []
    abs_rows = []
    for minute in range(1440):
        b = book_cells.get(minute, empty_book_cell())
        t = trade_cells.get(minute, empty_trade_cell())

        ofi_status = (
            "NO_DATA"
            if not (b["ofi_events"] or b["ofi_unknown"])
            else "UNKNOWN"
            if b["ofi_unknown"]
            else "OBSERVED"
        )
        ofi_rows.append(
            {
                "venue_id": "Bybit",
                "market_source_id": "BYBIT_OB200_ARCHIVE",
                "instrument_id": symbol,
                "indicator_id": 10,
                "formula_version": OFI_VERSION,
                "minute_start_utc": minute_iso(day, minute),
                "observed_at_utc": None,
                "verified_available_after_utc": None,
                "minute_utc": minute,
                "status": ofi_status,
                "events": b["ofi_events"],
                "unknown_events": b["ofi_unknown"],
                "bid_quote_usdt": (
                    str(b["ofi_bid"]) if ofi_status == "OBSERVED" else None
                ),
                "ask_quote_usdt": (
                    str(b["ofi_ask"]) if ofi_status == "OBSERVED" else None
                ),
                "signed_quote_usdt": (
                    str(b["ofi_bid"] - b["ofi_ask"])
                    if ofi_status == "OBSERVED"
                    else None
                ),
            }
        )

        book_status = (
            "NO_DATA"
            if not (b["abs_valid"] or b["abs_unknown"])
            else "UNKNOWN"
            if b["abs_unknown"]
            else "OBSERVED"
        )
        trade_status = "OBSERVED" if t["fills"] else "NO_DATA"
        price_change = None
        if trade_status == "OBSERVED":
            price_change = str(Decimal(100) * (t["last"] / t["first"] - 1))
        abs_rows.append(
            {
                "venue_id": "Bybit",
                "market_source_id": "BYBIT_OB200_AND_PUBLIC_TRADES_ARCHIVE",
                "instrument_id": symbol,
                "indicator_id": 16,
                "formula_version": ABS_VERSION,
                "minute_start_utc": minute_iso(day, minute),
                "observed_at_utc": None,
                "verified_available_after_utc": None,
                "proxy_classification": (
                    "ABSORPTION_EVIDENCE_PROXY_COMPONENTS_ONLY"
                ),
                "minute_utc": minute,
                "book_status": book_status,
                "trade_status": trade_status,
                "book_deltas_valid": b["abs_valid"],
                "book_deltas_unknown": b["abs_unknown"],
                "bid_added_quote_usdt": (
                    str(b["b_add"]) if book_status == "OBSERVED" else None
                ),
                "bid_removed_quote_usdt": (
                    str(b["b_remove"]) if book_status == "OBSERVED" else None
                ),
                "ask_added_quote_usdt": (
                    str(b["a_add"]) if book_status == "OBSERVED" else None
                ),
                "ask_removed_quote_usdt": (
                    str(b["a_remove"]) if book_status == "OBSERVED" else None
                ),
                "aggressive_buy_quote_usdt": (
                    str(t["buy"]) if trade_status == "OBSERVED" else None
                ),
                "aggressive_sell_quote_usdt": (
                    str(t["sell"]) if trade_status == "OBSERVED" else None
                ),
                "fills": t["fills"],
                "executed_first_price": (
                    str(t["first"]) if trade_status == "OBSERVED" else None
                ),
                "executed_last_price": (
                    str(t["last"]) if trade_status == "OBSERVED" else None
                ),
                "within_minute_executed_price_change_pct": price_change,
                "executed_absorption_confirmed": None,
                "maker_identity_proven": None,
                "causal_asof_join_verified": False,
            }
        )
    return ofi_rows, abs_rows


def read_jsonl_gzip(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def write_new_json(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def check_symbol(
    symbol: str,
    *,
    raw_root: Path = RAW_ROOT,
    run_root: Path = RUN_ROOT,
    days: int = 30,
    require_actor: bool = True,
) -> dict:
    if symbol not in COHORT:
        raise ValueError("SYMBOL_OUTSIDE_OWNER_COHORT")
    if not 1 <= days <= 30:
        raise ValueError("DAYS_OUTSIDE_FIRST_MONTH")
    _effective_actor_uid(require_actor)

    target = run_root / symbol
    final_path = target / "run_final.json"
    provenance_path = target / "input_provenance.json"
    ofi_path = target / "indicator_10_ofi_1m.jsonl.gz"
    abs_path = target / "indicator_16_absorption_components_1m.jsonl.gz"
    report_path = target / "independent_qc_report.json"
    if report_path.exists():
        raise FileExistsError("IMMUTABLE_QC_REPORT_ALREADY_EXISTS")
    for path in (final_path, provenance_path, ofi_path, abs_path):
        if not path.is_file():
            raise FileNotFoundError(
                f"REQUIRED_MATERIALIZED_ARTIFACT_MISSING {path}"
            )

    final = json.loads(final_path.read_text())
    provenance = json.loads(provenance_path.read_text())
    if final["status"] != "PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC":
        raise ValueError("MATERIALIZER_NOT_COMPLETE")
    if final["symbol"] != symbol or final["days"] != days:
        raise ValueError("MATERIALIZER_SCOPE_MISMATCH")
    if len(provenance["source_days"]) != days:
        raise ValueError("PROVENANCE_DAY_COUNT_MISMATCH")
    if sha256(ofi_path) != final["indicator_10"]["output_sha256"]:
        raise ValueError("OFI_OUTPUT_HASH_DRIFT")
    if sha256(abs_path) != final["indicator_16"]["output_sha256"]:
        raise ValueError("ABS_OUTPUT_HASH_DRIFT")

    ofi_actual = iter(read_jsonl_gzip(ofi_path))
    abs_actual = iter(read_jsonl_gzip(abs_path))
    checked_rows = 0
    source_days = []
    for offset in range(days):
        day = START + timedelta(days=offset)
        book_path = (
            raw_root
            / symbol
            / "orderbook"
            / f"{day}_{symbol}_ob200.data.zip"
        )
        trade_path = (
            raw_root / symbol / "public_trades" / f"{symbol}{day}.csv.gz"
        )
        if not book_path.is_file() or not trade_path.is_file():
            raise FileNotFoundError("QC_ORIGINAL_SOURCE_MISSING")
        book_cells, book_meta = independent_book_day(book_path, symbol, day)
        trade_cells, trade_meta = independent_trade_day(trade_path, symbol, day)
        expected_ofi, expected_abs = expected_rows(
            symbol, day, book_cells, trade_cells
        )
        prov = provenance["source_days"][offset]
        if (
            prov["date_utc"] != day.isoformat()
            or prov["orderbook_sha256"] != book_meta["sha256"]
            or prov["trade_sha256"] != trade_meta["sha256"]
            or (
                prov["orderbook_source_suffix_records"]
                != book_meta["source_suffix_records"]
            )
            or (
                prov["absorption_book_source_suffix_records"]
                != book_meta["source_suffix_records"]
            )
        ):
            raise ValueError("SOURCE_PROVENANCE_DRIFT")
        for exp_ofi, exp_abs in zip(expected_ofi, expected_abs):
            try:
                got_ofi = next(ofi_actual)
                got_abs = next(abs_actual)
            except StopIteration as exc:
                raise ValueError("OUTPUT_TRUNCATED") from exc
            if got_ofi != exp_ofi:
                raise ValueError(
                    f"OFI_ROW_MISMATCH {day} minute={exp_ofi['minute_utc']}"
                )
            if got_abs != exp_abs:
                raise ValueError(
                    f"ABS_ROW_MISMATCH {day} minute={exp_abs['minute_utc']}"
                )
            checked_rows += 1
        source_days.append(
            {
                "date_utc": day.isoformat(),
                "orderbook_sha256": book_meta["sha256"],
                "orderbook_records": book_meta["records"],
                "orderbook_snapshots": book_meta["snapshots"],
                "orderbook_gaps": book_meta["gaps"],
                "orderbook_source_suffix_records": book_meta[
                    "source_suffix_records"
                ],
                "trade_sha256": trade_meta["sha256"],
                "trade_rows": trade_meta["rows"],
            }
        )
    try:
        next(ofi_actual)
        raise ValueError("OFI_OUTPUT_HAS_EXTRA_ROWS")
    except StopIteration:
        pass
    try:
        next(abs_actual)
        raise ValueError("ABS_OUTPUT_HAS_EXTRA_ROWS")
    except StopIteration:
        pass

    report = {
        "status": "FULL_PERIOD_INDEPENDENT_SOURCE_QC_PASS_NOT_RESEARCH_READY",
        "checker_version": CHECKER_VERSION,
        "checker_sha256": sha256(Path(__file__).resolve()),
        "symbol": symbol,
        "days": days,
        "checked_minute_rows_per_indicator": checked_rows,
        "indicator_ids": [10, 16],
        "source_days": source_days,
        "materialized_run_final_sha256": sha256(final_path),
        "indicator_10_output_sha256": sha256(ofi_path),
        "indicator_16_output_sha256": sha256(abs_path),
        "receiver_clock_verified": False,
        "verified_available_after": False,
        "source_completeness_verified": False,
        "causal_asof_join_verified": False,
        "research_ready": False,
    }
    write_new_json(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=COHORT)
    args = parser.parse_args()
    print(json.dumps(check_symbol(args.symbol), sort_keys=True))


if __name__ == "__main__":
    main()
