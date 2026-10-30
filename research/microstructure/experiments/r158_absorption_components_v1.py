#!/usr/bin/env python3
"""Issue #158 / #16: independently observed absorption-evidence components.

Original executed Bybit USDT-perpetual trades and independently reconstructed
OB200 exact side/price quantity changes. Minute co-reporting is DESCRIPTIVE,
not a causal as-of join. No claims of passive-maker identity, spoofing,
executed absorption, binary signal, future performance, or CRIPTA receipt time.
Read-only input, stdout-only output, no downloads or writes.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START, END = date(2026, 5, 18), date(2026, 6, 17)
VERSION = "R158_ABSORPTION_COMPONENTS_NATIVE_V1"
SIDE = ("b", "a")


def positive(value: str, allow_zero: bool = False) -> Decimal:
    try:
        number = Decimal(value)
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ValueError("invalid original source numeric field") from exc
    if not number.is_finite() or number < 0 or (not allow_zero and number == 0):
        raise ValueError("non-finite or invalid source price/quantity")
    return number


def hashed(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for part in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def visible_qty(book: dict, side: str, price: Decimal):
    if price in book:
        return book[price]
    if not book:
        return None
    edge = min(book) if side == "b" else max(book)
    if (side == "b" and price >= edge) or (side == "a" and price <= edge):
        return Decimal(0)
    return None


def trim(book: dict, side: str) -> dict:
    return {p: book[p] for p in sorted(book, reverse=side == "b")[:200]}


def cell() -> dict:
    return {
        "book_deltas": 0, "book_unknown": 0,
        "b_add": Decimal(0), "b_remove": Decimal(0),
        "a_add": Decimal(0), "a_remove": Decimal(0),
        "fills": 0, "buy_quote": Decimal(0), "sell_quote": Decimal(0),
        "first_price": None, "last_price": None,
    }


def process_book(path: Path, symbol: str, day: date, bins: dict) -> dict:
    """Counts changes at EXPLICIT, visible native delta side+price IDs only.

    Implicit eviction over the OB200 depth boundary is not interpreted as a
    cancelled order. Source gaps poison the exact minute and require snapshot.
    """
    if not path.is_file():
        return {
            "state": "SOURCE_MISSING",
            "sha256": None,
            "snapshots": 0,
            "gaps": 0,
            "records": 0,
            "source_suffix_records": 0,
        }
    day_start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    state = None
    uid = seq = ts0 = None
    snapshots = gaps = records = suffix_records = 0
    seen_suffix = False
    previous_source_ts = None
    day_end = day_start + 86400000
    with zipfile.ZipFile(path) as archive:
        expected = f"{day}_{symbol}_ob200.data"
        if archive.namelist() != [expected]:
            raise ValueError("wrong original OB200 ZIP member")
        with archive.open(expected) as fh:
            for line in fh:
                item = json.loads(line)
                data = item["data"]
                ts = int(item["ts"])
                if previous_source_ts is not None and ts < previous_source_ts:
                    raise ValueError("native OB200 source timestamp out of order")
                previous_source_ts = ts
                if (
                    item["topic"] != f"orderbook.200.{symbol}"
                    or data["s"] != symbol
                ):
                    raise ValueError("wrong native OB200 source identity")
                if ts < day_start:
                    raise ValueError("OB200 timestamp before file day")
                if ts >= day_end:
                    suffix_records += 1
                    seen_suffix = True
                    if suffix_records > 1000:
                        raise ValueError("unbounded next UTC day suffix")
                    continue
                if seen_suffix:
                    raise ValueError("OB200 reentered file day after suffix")
                minute = (ts - day_start) // 60000
                current = bins[minute]
                records += 1
                if item["type"] == "snapshot":
                    if snapshots:
                        # Later source resnapshot breaks proved continuous updates.
                        current["book_unknown"] += 1
                    state = {}
                    for side in SIDE:
                        side_book = {}
                        for price, qty in data[side]:
                            p = positive(price)
                            q = positive(qty)
                            if p in side_book:
                                raise ValueError("duplicate snapshot price")
                            side_book[p] = q
                        if len(side_book) != 200:
                            raise ValueError("incomplete original OB200 snapshot")
                        state[side] = trim(side_book, side)
                    if max(state["b"]) >= min(state["a"]):
                        raise ValueError("crossed original snapshot book")
                    uid, seq, ts0 = int(data["u"]), int(data["seq"]), ts
                    snapshots += 1
                    continue
                if item["type"] != "delta":
                    raise ValueError("unknown native OB200 record type")
                if (state is None or int(data["u"]) != uid + 1
                        or int(data["seq"]) <= seq or ts < ts0):
                    current["book_unknown"] += 1
                    if state is not None:
                        gaps += 1
                    state = None
                    continue
                changes = {}
                uncertain = False
                for side in SIDE:
                    before = state[side]
                    after = dict(before)
                    touched = set()
                    for price, qty in data[side]:
                        p = positive(price)
                        q = positive(qty, allow_zero=True)
                        if p in touched:
                            raise ValueError("duplicate native delta price")
                        touched.add(p)
                        if q:
                            after[p] = q
                        else:
                            after.pop(p, None)
                    after = trim(after, side)
                    additions = removals = Decimal(0)
                    for price in touched:
                        old_qty = visible_qty(before, side, price)
                        new_qty = visible_qty(after, side, price)
                        if old_qty is None or new_qty is None:
                            uncertain = True
                        else:
                            quote_change = price * (new_qty - old_qty)
                            if quote_change > 0:
                                additions += quote_change
                            else:
                                removals -= quote_change
                    state[side] = after
                    changes[side] = (additions, removals)
                if max(state["b"]) >= min(state["a"]):
                    current["book_unknown"] += 1
                    gaps += 1
                    state = None
                    continue
                uid, seq, ts0 = int(data["u"]), int(data["seq"]), ts
                if uncertain:
                    current["book_unknown"] += 1
                else:
                    current["book_deltas"] += 1
                    for side in SIDE:
                        current[side + "_add"] += changes[side][0]
                        current[side + "_remove"] += changes[side][1]
    return {
        "state": "SOURCE_PRESENT",
        "sha256": hashed(path),
        "snapshots": snapshots,
        "gaps": gaps,
        "records": records,
        "source_suffix_records": suffix_records,
    }


def process_trades(path: Path, symbol: str, day: date, bins: dict) -> dict:
    if not path.is_file():
        return {"state": "SOURCE_MISSING", "sha256": None, "rows": 0}
    day_start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() // 60)
    rows = 0
    prior = None
    with gzip.open(path, "rt", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        needed = {"timestamp", "symbol", "side", "size", "price", "trdMatchID"}
        if not needed.issubset(reader.fieldnames or []):
            raise ValueError("invalid native Perp trade file columns")
        for item in reader:
            native_ts = positive(item["timestamp"])
            minute = int(
                (native_ts / Decimal(60)).to_integral_value(rounding=ROUND_FLOOR)
            )
            if minute < day_start or minute >= day_start + 1440:
                raise ValueError("native Perp trade outside UTC source day")
            if prior is not None and native_ts < prior:
                raise ValueError("native Perp trade timestamps are unsorted")
            if item["symbol"] != symbol or item["side"] not in ("Buy", "Sell"):
                raise ValueError("wrong native Perp trade identity/side")
            prior = native_ts
            price = positive(item["price"])
            qty = positive(item["size"])
            current = bins[minute - day_start]
            if current["first_price"] is None:
                current["first_price"] = price
            current["last_price"] = price
            current["fills"] += 1
            current["buy_quote" if item["side"] == "Buy" else "sell_quote"] += price * qty
            rows += 1
    return {"state": "SOURCE_PRESENT", "sha256": hashed(path), "rows": rows}


def calculate(symbol: str, day: date, source_root: Path):
    if symbol not in COHORT or not START <= day < END:
        raise ValueError("outside approved cohort or exact UTC first month")
    buckets: dict[int, dict] = defaultdict(cell)
    book = source_root / symbol / "orderbook" / f"{day}_{symbol}_ob200.data.zip"
    trades = source_root / symbol / "public_trades" / f"{symbol}{day}.csv.gz"
    book_meta = process_book(book, symbol, day, buckets)
    trade_meta = process_trades(trades, symbol, day, buckets)
    output = []
    for minute in range(1440):
        b = buckets.get(minute)
        if b is None:
            b = cell()
        book_status = (
            "NO_DATA" if book_meta["state"] == "SOURCE_MISSING"
            or not (b["book_deltas"] or b["book_unknown"])
            else "UNKNOWN" if b["book_unknown"] else "OBSERVED"
        )
        trade_status = (
            "NO_DATA" if trade_meta["state"] == "SOURCE_MISSING" or not b["fills"]
            else "OBSERVED"
        )
        close_return = None
        if trade_status == "OBSERVED":
            close_return = str(
                Decimal(100) * (b["last_price"] / b["first_price"] - 1)
            )
        output.append({
            "minute_utc": minute,
            "book_status": book_status, "trade_status": trade_status,
            "book_deltas_valid": b["book_deltas"],
            "book_deltas_unknown": b["book_unknown"],
            "bid_added_quote_usdt": str(b["b_add"]) if book_status == "OBSERVED" else None,
            "bid_removed_quote_usdt": (
                str(b["b_remove"]) if book_status == "OBSERVED" else None
            ),
            "ask_added_quote_usdt": str(b["a_add"]) if book_status == "OBSERVED" else None,
            "ask_removed_quote_usdt": (
                str(b["a_remove"]) if book_status == "OBSERVED" else None
            ),
            "aggressive_buy_quote_usdt": (
                str(b["buy_quote"]) if trade_status == "OBSERVED" else None
            ),
            "aggressive_sell_quote_usdt": (
                str(b["sell_quote"]) if trade_status == "OBSERVED" else None
            ),
            "fills": b["fills"],
            "executed_first_price": (
                str(b["first_price"]) if trade_status == "OBSERVED" else None
            ),
            "executed_last_price": (
                str(b["last_price"]) if trade_status == "OBSERVED" else None
            ),
            "within_minute_executed_price_change_pct": close_return,
            "executed_absorption_confirmed": None,
            "maker_identity_proven": None,
            "causal_asof_join_verified": False,
        })
    manifest = {
        "algorithm": VERSION, "symbol": symbol, "day_utc": day.isoformat(),
        "orderbook": book_meta, "perp_trades": trade_meta,
        "script_sha256": hashed(Path(__file__)),
        "receiver_clock": None, "available_after": None,
        "proxy_classification": "ABSORPTION_EVIDENCE_PROXY_COMPONENTS_ONLY",
        "same_minute_co_reporting": "EX_POST_DESCRIPTIVE_NOT_CAUSAL_ASOF",
        "readiness": "DESCRIPTIVE_ONLY_NOT_RESEARCH_READY",
    }
    return manifest, output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", choices=COHORT, required=True)
    p.add_argument("--date", type=date.fromisoformat, required=True)
    p.add_argument("--raw-root", type=Path, required=True)
    p.add_argument("--minute-rows", action="store_true")
    args = p.parse_args()
    manifest, minutes = calculate(args.symbol, args.date, args.raw_root)
    print(json.dumps({"type": "manifest", **manifest}, sort_keys=True))
    if args.minute_rows:
        for row in minutes:
            print(json.dumps({"type": "minute", **row}, sort_keys=True))


if __name__ == "__main__":
    main()
