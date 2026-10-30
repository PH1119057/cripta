#!/usr/bin/env python3
"""Independent full-row QC for Issue #158 / #13 first-month Depth Recovery.

This checker intentionally does NOT import the primary #13 reader/materializer.
It replays original OB200 archives with a separate reference implementation and
compares every materialized episode×horizon row plus source hashes.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
RAW_ROOT = Path("/data/cripta/datasets/raw/20260518_20260816")
RUN_ROOT = Path("/data/cripta/research/runs/r158_firstmonth_13_20261031")
FORMULA = "R158_DEPTH_RECOVERY_EXACT_PRICE_MULTI_HORIZON_V1"
VERSION = "R158_FIRSTMONTH_13_INDEPENDENT_FULLROW_QC_V1"
HORIZONS = (15_000, 60_000, 300_000)


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


def number(value, *, zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("QC_INVALID_DECIMAL") from exc
    if not result.is_finite() or result < 0 or (not zero and result == 0):
        raise ValueError("QC_INVALID_DECIMAL_SIGN")
    return result


def keep200(book: dict[Decimal, Decimal], side: str) -> dict[Decimal, Decimal]:
    keys = sorted(book, reverse=side == "b")[:200]
    return {key: book[key] for key in keys}


def q_visible(
    book: dict[Decimal, Decimal], side: str, price: Decimal
) -> Decimal | None:
    if price in book:
        return book[price]
    if len(book) < 200:
        return None
    edge = min(book) if side == "b" else max(book)
    if (side == "b" and price >= edge) or (
        side == "a" and price <= edge
    ):
        return Decimal(0)
    return None


def rank_of(book: dict[Decimal, Decimal], side: str, price: Decimal):
    ordered = sorted(book, reverse=side == "b")
    return ordered.index(price) + 1 if price in book else None


def update_side(book, entries, side, snapshot=False):
    current = {} if snapshot else dict(book)
    touched = set()
    for pair in entries:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("QC_MALFORMED_LEVEL")
        price = number(pair[0])
        qty = number(pair[1], zero=not snapshot)
        if price in touched:
            raise ValueError("QC_DUPLICATE_PRICE_WITHIN_MESSAGE")
        touched.add(price)
        if qty == 0:
            current.pop(price, None)
        else:
            current[price] = qty
    return keep200(current, side), touched


def new_episode(symbol, day, side, ordinal, price, target, after, ts, rank):
    return {
        "episode_id": f"{symbol}:{day.isoformat()}:{side}:{ordinal}",
        "side": side,
        "price": price,
        "target": target,
        "after": after,
        "start": ts,
        "rank": rank,
        "stratum": "Top5" if rank <= 5 else "6-200",
        "state": {
            h: {
                "outcome": None,
                "reason": None,
                "outcome_ts": None,
                "recovery_ms": None,
            }
            for h in HORIZONS
        },
    }


def close_unresolved(ep, reason, ts):
    for h in HORIZONS:
        item = ep["state"][h]
        if item["outcome"] is None:
            item["outcome"] = "C"
            item["reason"] = reason
            item["outcome_ts"] = ts


def is_complete(ep):
    return all(ep["state"][h]["outcome"] is not None for h in HORIZONS)


def replay_day(path: Path, symbol: str, day: date) -> list[dict]:
    day_start = int(
        datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000
    )
    day_end = day_start + 86_400_000
    books = None
    uid = seq = last_ts = None
    active = {"b": {}, "a": {}}
    completed = []
    ordinal = 0

    def flush_complete(side, price):
        ep = active[side].get(price)
        if ep is not None and is_complete(ep):
            completed.append(ep)
            del active[side][price]

    def censor_all(reason, ts):
        for side in ("b", "a"):
            for price, ep in list(active[side].items()):
                close_unresolved(ep, reason, ts)
                completed.append(ep)
                del active[side][price]

    def identity(record):
        data = record["data"]
        if (
            record["topic"] != f"orderbook.200.{symbol}"
            or data["s"] != symbol
        ):
            raise ValueError("QC_SOURCE_IDENTITY_MISMATCH")
        return int(record["ts"]), int(data["u"]), int(data["seq"])

    def process_group(ts, group):
        nonlocal books, uid, seq, last_ts, ordinal
        kinds = {record["type"] for record in group}
        if "snapshot" in kinds:
            if len(group) != 1 or kinds != {"snapshot"}:
                censor_all("SAME_TIMESTAMP_ORDER_AMBIGUOUS", ts)
                books = None
                uid = seq = last_ts = None
                return
            record = group[0]
            rts, ruid, rseq = identity(record)
            data = record["data"]
            replacement = {}
            for side in ("b", "a"):
                if not isinstance(data.get(side), list) or len(data[side]) != 200:
                    raise ValueError("QC_SNAPSHOT_NOT_200")
                replacement[side], _ = update_side(
                    {}, data[side], side, snapshot=True
                )
                if len(replacement[side]) != 200:
                    raise ValueError("QC_SNAPSHOT_NOT_200")
            if max(replacement["b"]) >= min(replacement["a"]):
                raise ValueError("QC_CROSSED_SNAPSHOT")
            if books is not None:
                censor_all("SNAPSHOT_RESET", ts)
            books = replacement
            uid, seq, last_ts = ruid, rseq, rts
            return
        if kinds != {"delta"}:
            raise ValueError("QC_UNSUPPORTED_MESSAGE")
        if books is None:
            return

        check_uid, check_seq = uid, seq
        assert check_uid is not None and check_seq is not None
        sequence_ok = True
        for record in group:
            rts, ruid, rseq = identity(record)
            if (
                rts != ts
                or ruid != check_uid + 1
                or rseq <= check_seq
            ):
                sequence_ok = False
                break
            check_uid, check_seq = ruid, rseq
        if not sequence_ok:
            censor_all("SOURCE_SEQUENCE_GAP", ts)
            books = None
            uid = seq = last_ts = None
            return

        for side in ("b", "a"):
            for price, ep in list(active[side].items()):
                if q_visible(books[side], side, price) is None:
                    close_unresolved(ep, "OB200_EDGE_VISIBILITY_LOST", last_ts)
                    flush_complete(side, price)
                    continue
                for h in HORIZONS:
                    item = ep["state"][h]
                    if item["outcome"] is None and ts > ep["start"] + h:
                        item["outcome"] = "N"
                        item["reason"] = "CONTINUOUS_THROUGH_HORIZON"
                        item["outcome_ts"] = ep["start"] + h
                flush_complete(side, price)

        before = {side: dict(books[side]) for side in ("b", "a")}
        touches = defaultdict(int)
        lost = set()
        valid = True
        for record in group:
            data = record["data"]
            next_books = {}
            for side in ("b", "a"):
                next_books[side], touched = update_side(
                    books[side], data[side], side, snapshot=False
                )
                for price in touched:
                    touches[(side, price)] += 1
            if (
                any(len(next_books[side]) != 200 for side in ("b", "a"))
                or max(next_books["b"]) >= min(next_books["a"])
            ):
                valid = False
                break
            books = next_books
            uid = int(data["u"])
            seq = int(data["seq"])
            last_ts = ts
            for side in ("b", "a"):
                for price in active[side]:
                    if q_visible(books[side], side, price) is None:
                        lost.add((side, price))
        if not valid:
            censor_all("SOURCE_SEQUENCE_GAP", ts)
            books = None
            uid = seq = last_ts = None
            return

        after = {side: dict(books[side]) for side in ("b", "a")}
        for side in ("b", "a"):
            for price, ep in list(active[side].items()):
                if (side, price) in lost:
                    close_unresolved(ep, "OB200_EDGE_VISIBILITY_LOST", ts)
                    flush_complete(side, price)
                    continue
                if touches[(side, price)] > 1:
                    close_unresolved(
                        ep, "SAME_TIMESTAMP_ORDER_AMBIGUOUS", ts
                    )
                    flush_complete(side, price)
                    continue
                current = q_visible(books[side], side, price)
                if current is None:
                    close_unresolved(ep, "OB200_EDGE_VISIBILITY_LOST", ts)
                    flush_complete(side, price)
                    continue
                if ts > ep["start"] and current >= ep["target"]:
                    for h in HORIZONS:
                        item = ep["state"][h]
                        if (
                            item["outcome"] is None
                            and ts <= ep["start"] + h
                        ):
                            item["outcome"] = "R"
                            item["reason"] = "EXACT_PRICE_TARGET_RECOVERED"
                            item["outcome_ts"] = ts
                            item["recovery_ms"] = ts - ep["start"]
                for h in HORIZONS:
                    item = ep["state"][h]
                    if (
                        item["outcome"] is None
                        and ts == ep["start"] + h
                    ):
                        item["outcome"] = "N"
                        item["reason"] = "CONTINUOUS_THROUGH_HORIZON"
                        item["outcome_ts"] = ep["start"] + h
                flush_complete(side, price)

        for side in ("b", "a"):
            prices = sorted(
                price for s, price in touches if s == side
            )
            for price in prices:
                if price in active[side]:
                    continue
                old = q_visible(before[side], side, price)
                current = q_visible(after[side], side, price)
                if (
                    old is None
                    or current is None
                    or old <= 0
                    or current >= old
                ):
                    continue
                rank = rank_of(before[side], side, price)
                if rank is None or not 1 <= rank <= 200:
                    continue
                ordinal += 1
                ep = new_episode(
                    symbol, day, side, ordinal, price, old, current, ts, rank
                )
                if touches[(side, price)] > 1:
                    close_unresolved(
                        ep, "SAME_TIMESTAMP_ORDER_AMBIGUOUS", ts
                    )
                    completed.append(ep)
                else:
                    active[side][price] = ep

    previous_ts = None
    group_ts = None
    group = []
    with zipfile.ZipFile(path) as archive:
        member = f"{day}_{symbol}_ob200.data"
        if archive.namelist() != [member]:
            raise ValueError("QC_ZIP_IDENTITY_MISMATCH")
        with archive.open(member) as stream:
            for line in stream:
                record = json.loads(line)
                ts = int(record["ts"])
                if ts < day_start:
                    raise ValueError("QC_RECORD_BEFORE_DAY")
                if ts >= day_end:
                    break
                if previous_ts is not None and ts < previous_ts:
                    raise ValueError("QC_TIMESTAMP_NONMONOTONE")
                previous_ts = ts
                if group_ts is None:
                    group_ts = ts
                if ts != group_ts:
                    process_group(group_ts, group)
                    group = []
                    group_ts = ts
                group.append(record)
        if group:
            process_group(group_ts, group)

    if books is None and not completed:
        raise ValueError("QC_NO_VALID_DAY_BOOK_STATE")
    censor_all("DAY_END_RIGHT_CENSOR", day_end)

    rows = []
    for ep in completed:
        for h in HORIZONS:
            item = ep["state"][h]
            if item["outcome"] is None:
                raise ValueError("QC_UNRESOLVED_HORIZON")
            rows.append(
                {
                    "episode_id": ep["episode_id"],
                    "indicator_id": 13,
                    "formula_version": FORMULA,
                    "venue_id": "Bybit",
                    "market_source_id": "BYBIT_OB200_ARCHIVE",
                    "instrument_id": symbol,
                    "date_utc": day.isoformat(),
                    "side": "BID" if ep["side"] == "b" else "ASK",
                    "price": str(ep["price"]),
                    "target_base_qty": str(ep["target"]),
                    "post_depletion_base_qty": str(ep["after"]),
                    "start_rank": ep["rank"],
                    "stratum": ep["stratum"],
                    "start_ts_exchange_ms": ep["start"],
                    "horizon_s": h // 1000,
                    "outcome": item["outcome"],
                    "outcome_reason": item["reason"],
                    "outcome_ts_exchange_ms": item["outcome_ts"],
                    "recovery_ms": item["recovery_ms"],
                    "observed_at_utc": None,
                    "verified_available_after_utc": None,
                    "research_ready": False,
                }
            )
    rows.sort(
        key=lambda row: (
            row["start_ts_exchange_ms"],
            row["side"],
            Decimal(row["price"]),
            row["episode_id"],
            row["horizon_s"],
        )
    )
    return rows


def read_output(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def write_json_new(path: Path, payload: dict) -> None:
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
    require_actor: bool = True,
) -> dict:
    if symbol not in COHORT:
        raise ValueError("SYMBOL_OUTSIDE_OWNER_COHORT")
    _effective_actor_uid(require_actor)

    target = run_root / symbol
    final_path = target / "run_final.json"
    provenance_path = target / "input_provenance.json"
    output_path = target / "episode_horizons.jsonl.gz"
    report_path = target / "independent_qc_report.json"
    if report_path.exists():
        raise FileExistsError("IMMUTABLE_QC_REPORT_ALREADY_EXISTS")
    for path in (final_path, provenance_path, output_path):
        if not path.is_file():
            raise FileNotFoundError(f"QC_REQUIRED_ARTIFACT_MISSING {path}")

    final = json.loads(final_path.read_text())
    provenance = json.loads(provenance_path.read_text())["days"]
    if (
        final["status"]
        != "DEPTH13_FIRSTMONTH_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
        or final["formula_version"] != FORMULA
        or final["symbol"] != symbol
        or final["days"] != 30
    ):
        raise ValueError("MATERIALIZED_RUN_SCOPE_OR_STATUS_MISMATCH")
    if sha256(output_path) != final["output_sha256"]:
        raise ValueError("MATERIALIZED_OUTPUT_HASH_DRIFT")
    if len(provenance) != 30:
        raise ValueError("PROVENANCE_DAY_COUNT_MISMATCH")

    actual = iter(read_output(output_path))
    checked = 0
    source_days = []
    for offset in range(30):
        day = START + timedelta(days=offset)
        source = (
            raw_root
            / symbol
            / "orderbook"
            / f"{day}_{symbol}_ob200.data.zip"
        )
        if not source.is_file():
            raise FileNotFoundError(f"QC_ORIGINAL_SOURCE_MISSING {source}")
        digest = sha256(source)
        prov = provenance[offset]
        if (
            prov["date_utc"] != day.isoformat()
            or prov["source_sha256"] != digest
        ):
            raise ValueError("QC_SOURCE_PROVENANCE_DRIFT")
        expected = replay_day(source, symbol, day)
        for row in expected:
            try:
                got = next(actual)
            except StopIteration as exc:
                raise ValueError("MATERIALIZED_OUTPUT_TRUNCATED") from exc
            if got != row:
                raise ValueError(
                    "DEPTH13_FULLROW_MISMATCH "
                    f"day={day} episode={row['episode_id']} "
                    f"horizon={row['horizon_s']}"
                )
            checked += 1
        source_days.append(
            {
                "date_utc": day.isoformat(),
                "source_sha256": digest,
                "independent_rows": len(expected),
            }
        )
    try:
        next(actual)
        raise ValueError("MATERIALIZED_OUTPUT_HAS_EXTRA_ROWS")
    except StopIteration:
        pass
    if checked != final["rows"]:
        raise ValueError("QC_FINAL_ROW_COUNT_MISMATCH")

    report = {
        "status": "DEPTH13_FIRSTMONTH_INDEPENDENT_FULLROW_QC_PASS_NOT_RESEARCH_READY",
        "checker_version": VERSION,
        "checker_sha256": sha256(Path(__file__).resolve()),
        "symbol": symbol,
        "indicator_id": 13,
        "formula_version": FORMULA,
        "checked_rows": checked,
        "checked_days": 30,
        "source_days": source_days,
        "materialized_output_sha256": sha256(output_path),
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
