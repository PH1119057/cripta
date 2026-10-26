#!/usr/bin/env python3
"""#158/#10: exact-price oldTop5 UNION newTop5 on original Bybit OB200.

READ-ONLY research calculator, UTC one original day per call. Never carries
state across a source gap/snapshot or silently substitutes rank-level OFI.
Source exchange-event timestamps are not CRIPTA local available_after.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

VERSION = "R158_OFI_EXACT_PRICE_TOP5_UNION_V1"
COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START, END_EXCLUSIVE = date(2026, 5, 18), date(2026, 6, 17)
SIDES = ("b", "a")


def positive(value: str, *, allow_zero: bool = False) -> Decimal:
    try:
        number = Decimal(value)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError("invalid source book decimal") from exc
    if not number.is_finite() or number < 0 or (not allow_zero and number == 0):
        raise ValueError("invalid source book sign")
    return number


def top_levels(book: dict[Decimal, Decimal], side: str, n: int = 200) -> dict:
    keys = sorted(book, reverse=side == "b")[:n]
    return {p: book[p] for p in keys}


def visible_quantity(book: dict[Decimal, Decimal], side: str, price: Decimal):
    """A missing level is proven zero only on the source-visible price side."""
    if price in book:
        return book[price]
    if not book:
        return None
    boundary = min(book) if side == "b" else max(book)
    if (side == "b" and price >= boundary) or (side == "a" and price <= boundary):
        return Decimal(0)
    return None


def book_side(entries, *, snapshot: bool, prior: dict | None = None) -> dict:
    book = {} if snapshot else dict(prior or {})
    touched: set[Decimal] = set()
    for pair in entries:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("malformed original book price/quantity")
        price = positive(pair[0])
        qty = positive(pair[1], allow_zero=not snapshot)
        if price in touched:
            raise ValueError("duplicate side/price within source event")
        touched.add(price)
        if qty == 0:
            book.pop(price, None)
        else:
            book[price] = qty
    return book


def signed_side_delta(before: dict, after: dict, side: str):
    old5 = list(top_levels(before, side, 5))
    new5 = list(top_levels(after, side, 5))
    union = set(old5) | set(new5)
    quote = Decimal(0)
    unknown: list[str] = []
    for price in sorted(union):
        q0 = visible_quantity(before, side, price)
        q1 = visible_quantity(after, side, price)
        if q0 is None or q1 is None:
            unknown.append(str(price))
        else:
            quote += price * (q1 - q0)
    return {"quote": quote if not unknown else None,
            "known_partial_quote": quote, "unknown_prices": unknown,
            "old_top5": [str(p) for p in old5],
            "new_top5": [str(p) for p in new5],
            "union_count": len(union)}


class Reader:
    def __init__(self, symbol: str):
        if symbol not in COHORT:
            raise ValueError("symbol outside source research cohort")
        self.symbol = symbol
        self.books: dict[str, dict[Decimal, Decimal]] | None = None
        self.update_id: int | None = None
        self.seq: int | None = None
        self.ts: int | None = None
        self.counts = defaultdict(int)

    def _reset(self):
        self.books = None
        self.update_id = None
        self.seq = None
        self.ts = None

    def on_record(self, record: dict):
        data = record["data"]
        if (record["topic"] != f"orderbook.200.{self.symbol}"
                or data["s"] != self.symbol):
            raise ValueError("wrong native book source identity")
        kind = record["type"]
        ts = int(record["ts"])
        seq = int(data["seq"])
        uid = int(data["u"])
        if kind == "snapshot":
            raw = {side: book_side(data[side], snapshot=True) for side in SIDES}
            if any(len(raw[side]) != 200 for side in SIDES):
                raise ValueError("snapshot must contain 200 unique levels per side")
            self.books = {side: top_levels(raw[side], side) for side in SIDES}
            self.update_id, self.seq, self.ts = uid, seq, ts
            self.counts["snapshots"] += 1
            return None
        if kind != "delta":
            raise ValueError("unexpected book record type")
        self.counts["deltas"] += 1
        if self.books is None:
            self.counts["deltas_without_snapshot"] += 1
            return None
        if (uid != self.update_id + 1 or seq <= self.seq or ts < self.ts):
            self.counts["source_gap_or_order_failures"] += 1
            self._reset()
            return None
        old = {side: dict(self.books[side]) for side in SIDES}
        raw = {
            side: book_side(data[side], snapshot=False, prior=old[side])
            for side in SIDES
        }
        new = {side: top_levels(raw[side], side) for side in SIDES}
        if any(len(new[side]) < 5 for side in SIDES):
            self.counts["insufficient_visible_levels"] += 1
            self._reset()
            return None
        if max(new["b"]) >= min(new["a"]):
            self.counts["crossed_book"] += 1
            self._reset()
            return None
        bid = signed_side_delta(old["b"], new["b"], "b")
        ask = signed_side_delta(old["a"], new["a"], "a")
        unknown = bool(bid["unknown_prices"] or ask["unknown_prices"])
        self.books = new
        self.update_id, self.seq, self.ts = uid, seq, ts
        self.counts["events_unknown" if unknown else "events_valid"] += 1
        return {
            "ts_exchange_ms": ts, "seq": seq, "u": uid,
            "bid_quote_usdt": str(bid["quote"]) if bid["quote"] is not None else None,
            "ask_quote_usdt": str(ask["quote"]) if ask["quote"] is not None else None,
            "signed_quote_usdt": str(bid["quote"] - ask["quote"]) if not unknown else None,
            "bid_union_levels": bid["union_count"],
            "ask_union_levels": ask["union_count"],
            "unknown_bid_prices": bid["unknown_prices"],
            "unknown_ask_prices": ask["unknown_prices"],
            "status": "UNKNOWN_VISIBILITY" if unknown else "OBSERVED",
        }


def _hash(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def calculate_archive(path: Path, symbol: str, day: date, max_events: int = 0):
    if symbol not in COHORT or not START <= day < END_EXCLUSIVE:
        raise ValueError("out-of-contract symbol or UTC day")
    if max_events < 0:
        raise ValueError("max_events must not be negative")
    if not path.is_file():
        raise FileNotFoundError(str(path))
    reader = Reader(symbol)
    start_ms = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    minute = defaultdict(lambda: {"events": 0, "unknown_events": 0,
                                  "bid_quote": Decimal(0), "ask_quote": Decimal(0)})
    record_count = 0
    previous_ts = None
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != 1 or members[0] != f"{day}_{symbol}_ob200.data":
            raise ValueError("unexpected original ZIP source identity")
        with archive.open(members[0]) as source:
            for line in source:
                record = json.loads(line)
                ts = int(record["ts"])
                if not start_ms <= ts < start_ms + 86400000:
                    raise ValueError("source record outside UTC source day")
                if previous_ts is not None and ts < previous_ts:
                    raise ValueError("unsorted source exchange event timestamps")
                previous_ts = ts
                value = reader.on_record(record)
                if value is not None:
                    cell = minute[(ts - start_ms) // 60000]
                    cell["events"] += 1
                    if value["status"] != "OBSERVED":
                        cell["unknown_events"] += 1
                    else:
                        cell["bid_quote"] += Decimal(value["bid_quote_usdt"])
                        cell["ask_quote"] += Decimal(value["ask_quote_usdt"])
                record_count += 1
                if max_events and record_count >= max_events:
                    break
    rows = []
    for slot in range(1440):
        cell = minute.get(slot)
        status = ("NO_DATA" if cell is None or not cell["events"] else
                  "UNKNOWN" if cell["unknown_events"] else "OBSERVED")
        rows.append({
            "minute_utc": slot, "status": status,
            "events": cell["events"] if cell else 0,
            "unknown_events": cell["unknown_events"] if cell else 0,
            "bid_quote_usdt": str(cell["bid_quote"]) if status == "OBSERVED" else None,
            "ask_quote_usdt": str(cell["ask_quote"]) if status == "OBSERVED" else None,
            "signed_quote_usdt": (
                str(cell["bid_quote"] - cell["ask_quote"])
                if status == "OBSERVED" else None
            ),
        })
    manifest = {
        "algorithm": VERSION, "source": str(path), "source_zip_sha256": _hash(path),
        "source_script_sha256": _hash(Path(__file__)),
        "symbol": symbol, "date_utc": day.isoformat(),
        "raw_records_read": record_count, "max_events_limit": max_events,
        "complete_day": max_events == 0,
        "reader_qc": dict(reader.counts),
        "minute_status_counts": {
            status: sum(r["status"] == status for r in rows)
            for status in ("OBSERVED", "UNKNOWN", "NO_DATA")
        },
        "receiver_clock": None, "available_after": None,
        "readiness": "DESCRIPTIVE_ONLY_NOT_RESEARCH_READY",
        "book_scope": "original OB200 exact-price oldTop5 UNION newTop5",
        "no_cross_day_book_carry": True,
    }
    return manifest, rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", required=True, choices=COHORT)
    p.add_argument("--date", required=True, type=date.fromisoformat)
    p.add_argument("--raw-root", required=True, type=Path)
    p.add_argument("--max-events", type=int, default=0)
    p.add_argument("--minute-rows", action="store_true")
    a = p.parse_args()
    path = (a.raw_root / a.symbol / "orderbook"
            / f"{a.date}_{a.symbol}_ob200.data.zip")
    manifest, minutes = calculate_archive(path, a.symbol, a.date, a.max_events)
    print(json.dumps({"type": "manifest", **manifest}, sort_keys=True))
    if a.minute_rows:
        for cell in minutes:
            print(json.dumps({"type": "minute", **cell}, sort_keys=True))


if __name__ == "__main__":
    main()
