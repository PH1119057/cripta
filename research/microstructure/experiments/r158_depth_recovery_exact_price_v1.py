#!/usr/bin/env python3
"""Issue #158 / #13 strict exact-price Depth Recovery on original Bybit OB200.

Canonical measurement: RESEARCH_COMPUTE §21.8 as refined by owner 2026-10-31.
One UTC source day per calculation; no cross-day carry. The reader groups
messages by native timestamp only to avoid inventing order inside timestamp
ties. Historical local available_after is unknown, therefore outputs remain
DESCRIPTIVE_ONLY_NOT_RESEARCH_READY.
"""
from __future__ import annotations

import argparse
import heapq
import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

VERSION = "R158_DEPTH_RECOVERY_EXACT_PRICE_MULTI_HORIZON_V1"
COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START = date(2026, 5, 18)
END_EXCLUSIVE = date(2026, 6, 17)
HORIZONS_MS = (15_000, 60_000, 300_000)
SIDES = ("b", "a")


def positive(value: object, *, allow_zero: bool = False) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("INVALID_BOOK_DECIMAL") from exc
    if not number.is_finite() or number < 0 or (not allow_zero and number == 0):
        raise ValueError("INVALID_BOOK_DECIMAL_SIGN")
    return number


def trim200(book: dict[Decimal, Decimal], side: str) -> dict[Decimal, Decimal]:
    keys = sorted(book, reverse=side == "b")[:200]
    return {price: book[price] for price in keys}


def visible_quantity(
    book: dict[Decimal, Decimal], side: str, price: Decimal
) -> Decimal | None:
    """Missing is known zero only while exact price is inside visible OB200."""
    if price in book:
        return book[price]
    if len(book) < 200:
        return None
    boundary = min(book) if side == "b" else max(book)
    if (side == "b" and price >= boundary) or (
        side == "a" and price <= boundary
    ):
        return Decimal(0)
    return None


def source_rank(
    book: dict[Decimal, Decimal], side: str, price: Decimal
) -> int | None:
    ordered = sorted(book, reverse=side == "b")
    try:
        return ordered.index(price) + 1
    except ValueError:
        return None


def apply_side(
    before: dict[Decimal, Decimal],
    entries: object,
    side: str,
    *,
    snapshot: bool,
) -> tuple[dict[Decimal, Decimal], set[Decimal]]:
    if not isinstance(entries, list):
        raise ValueError("BOOK_SIDE_NOT_LIST")
    book = {} if snapshot else dict(before)
    touched: set[Decimal] = set()
    for pair in entries:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("MALFORMED_BOOK_LEVEL")
        price = positive(pair[0])
        qty = positive(pair[1], allow_zero=not snapshot)
        if price in touched:
            raise ValueError("DUPLICATE_PRICE_WITHIN_MESSAGE")
        touched.add(price)
        if qty == 0:
            book.pop(price, None)
        else:
            book[price] = qty
    return trim200(book, side), touched


@dataclass
class HorizonState:
    horizon_ms: int
    outcome: str | None = None
    reason: str | None = None
    outcome_ts_ms: int | None = None
    recovery_ms: int | None = None


@dataclass
class Episode:
    episode_id: str
    symbol: str
    day: date
    side: str
    price: Decimal
    target_qty: Decimal
    post_depletion_qty: Decimal
    start_ts_ms: int
    start_rank: int
    stratum: str
    states: dict[int, HorizonState] = field(
        default_factory=lambda: {
            h: HorizonState(horizon_ms=h) for h in HORIZONS_MS
        }
    )

    def unresolved(self) -> list[HorizonState]:
        return [state for state in self.states.values() if state.outcome is None]

    def complete(self) -> bool:
        return not self.unresolved()


class DepthRecoveryReader:
    def __init__(self, symbol: str, day: date):
        if symbol not in COHORT or not START <= day < END_EXCLUSIVE:
            raise ValueError("OUT_OF_CONTRACT_SCOPE")
        self.symbol = symbol
        self.day = day
        self.books: dict[str, dict[Decimal, Decimal]] | None = None
        self.update_id: int | None = None
        self.seq: int | None = None
        self.last_ts: int | None = None
        self.active: dict[str, dict[Decimal, Episode]] = {
            "b": {},
            "a": {},
        }
        self.deadline_heaps: dict[
            str, list[tuple[int, str, Decimal, int]]
        ] = {"b": [], "a": []}
        self.visibility_heaps: dict[
            str, list[tuple[Decimal, str, Decimal]]
        ] = {"b": [], "a": []}
        self.finished: list[Episode] = []
        self.episode_no = 0
        self.counts = {
            "snapshots": 0,
            "deltas": 0,
            "source_sequence_gaps": 0,
            "snapshot_resets": 0,
            "same_timestamp_censored": 0,
            "visibility_censored": 0,
            "day_end_censored": 0,
            "episodes_opened": 0,
        }

    def _finish_if_complete(self, side: str, price: Decimal) -> None:
        episode = self.active[side].get(price)
        if episode is not None and episode.complete():
            self.finished.append(episode)
            del self.active[side][price]

    def _censor_episode(
        self, episode: Episode, reason: str, ts_ms: int | None
    ) -> None:
        changed = False
        for state in episode.unresolved():
            state.outcome = "C"
            state.reason = reason
            state.outcome_ts_ms = ts_ms
            changed = True
        if changed:
            if reason == "SAME_TIMESTAMP_ORDER_AMBIGUOUS":
                self.counts["same_timestamp_censored"] += 1
            elif reason == "OB200_EDGE_VISIBILITY_LOST":
                self.counts["visibility_censored"] += 1
            elif reason == "DAY_END_RIGHT_CENSOR":
                self.counts["day_end_censored"] += 1

    def _censor_all(self, reason: str, ts_ms: int | None) -> None:
        for side in SIDES:
            for price, episode in list(self.active[side].items()):
                self._censor_episode(episode, reason, ts_ms)
                self.finished.append(episode)
                del self.active[side][price]

    def _validate_identity(self, record: dict) -> tuple[int, int, int]:
        data = record.get("data")
        if not isinstance(data, dict):
            raise ValueError("BOOK_DATA_NOT_OBJECT")
        if (
            record.get("topic") != f"orderbook.200.{self.symbol}"
            or data.get("s") != self.symbol
        ):
            raise ValueError("BOOK_SOURCE_IDENTITY_MISMATCH")
        return int(record["ts"]), int(data["u"]), int(data["seq"])

    def _snapshot(self, record: dict) -> None:
        ts, uid, seq = self._validate_identity(record)
        data = record["data"]
        for side in SIDES:
            if not isinstance(data.get(side), list) or len(data[side]) != 200:
                raise ValueError("SNAPSHOT_NOT_EXACTLY_200_LEVELS")
        replacement = {
            side: apply_side({}, data[side], side, snapshot=True)[0]
            for side in SIDES
        }
        if any(len(replacement[side]) != 200 for side in SIDES):
            raise ValueError("SNAPSHOT_NOT_EXACTLY_200_LEVELS")
        if max(replacement["b"]) >= min(replacement["a"]):
            raise ValueError("CROSSED_SNAPSHOT")
        if self.books is not None:
            self._censor_all("SNAPSHOT_RESET", ts)
            self.counts["snapshot_resets"] += 1
        self.books = replacement
        self.update_id = uid
        self.seq = seq
        self.last_ts = ts
        self.counts["snapshots"] += 1

    def _sequence_preflight(self, records: list[dict], ts: int) -> bool:
        if self.books is None or self.update_id is None or self.seq is None:
            return False
        uid = self.update_id
        seq = self.seq
        for record in records:
            rts, ruid, rseq = self._validate_identity(record)
            if record.get("type") != "delta" or rts != ts:
                return False
            if ruid != uid + 1 or rseq <= seq:
                return False
            uid, seq = ruid, rseq
        return True

    def _register_episode(self, episode: Episode) -> None:
        side = episode.side
        self.active[side][episode.price] = episode
        for horizon in HORIZONS_MS:
            heapq.heappush(
                self.deadline_heaps[side],
                (
                    episode.start_ts_ms + horizon,
                    episode.episode_id,
                    episode.price,
                    horizon,
                ),
            )
        priority = episode.price if side == "b" else -episode.price
        heapq.heappush(
            self.visibility_heaps[side],
            (priority, episode.episode_id, episode.price),
        )

    def _resolve_deadlines(self, ts: int, *, inclusive: bool) -> None:
        for side in SIDES:
            heap = self.deadline_heaps[side]
            while heap and (
                heap[0][0] <= ts if inclusive else heap[0][0] < ts
            ):
                deadline, episode_id, price, horizon = heapq.heappop(heap)
                episode = self.active[side].get(price)
                if episode is None or episode.episode_id != episode_id:
                    continue
                state = episode.states[horizon]
                if state.outcome is not None:
                    continue
                state.outcome = "N"
                state.reason = "CONTINUOUS_THROUGH_HORIZON"
                state.outcome_ts_ms = deadline
                self._finish_if_complete(side, price)

    def _censor_outside_visible_edge(self, side: str, ts: int) -> None:
        assert self.books is not None
        book = self.books[side]
        if len(book) != 200:
            raise RuntimeError("OB200_DEPTH_NOT_MAINTAINED")
        boundary = min(book) if side == "b" else max(book)
        heap = self.visibility_heaps[side]
        while heap:
            priority, episode_id, price = heap[0]
            outside = price < boundary if side == "b" else price > boundary
            if not outside:
                break
            heapq.heappop(heap)
            episode = self.active[side].get(price)
            if episode is None or episode.episode_id != episode_id:
                continue
            self._censor_episode(
                episode, "OB200_EDGE_VISIBILITY_LOST", ts
            )
            self._finish_if_complete(side, price)

    def _apply_delta_group(
        self, ts: int, records: list[dict]
    ) -> tuple[
        dict[str, dict[Decimal, Decimal]],
        dict[str, dict[Decimal, Decimal]],
        dict[tuple[str, Decimal], int],
    ]:
        assert self.books is not None
        before = {side: dict(self.books[side]) for side in SIDES}
        touch_counts: dict[tuple[str, Decimal], int] = {}

        for record in records:
            data = record["data"]
            updated: dict[str, dict[Decimal, Decimal]] = {}
            for side in SIDES:
                new_side, touched = apply_side(
                    self.books[side], data[side], side, snapshot=False
                )
                updated[side] = new_side
                for price in touched:
                    key = (side, price)
                    touch_counts[key] = touch_counts.get(key, 0) + 1
            if any(len(updated[side]) != 200 for side in SIDES):
                raise RuntimeError("OB200_DEPTH_NOT_MAINTAINED")
            if max(updated["b"]) >= min(updated["a"]):
                raise RuntimeError("CROSSED_BOOK")
            self.books = updated
            self.update_id = int(data["u"])
            self.seq = int(data["seq"])
            self.last_ts = ts
            self.counts["deltas"] += 1
            for side in SIDES:
                self._censor_outside_visible_edge(side, ts)

        after = {side: dict(self.books[side]) for side in SIDES}
        return before, after, touch_counts

    def _resolve_touched_after_group(
        self,
        ts: int,
        touch_counts: dict[tuple[str, Decimal], int],
    ) -> None:
        assert self.books is not None
        for (side, price), touches in touch_counts.items():
            episode = self.active[side].get(price)
            if episode is None:
                continue
            if touches > 1:
                self._censor_episode(
                    episode, "SAME_TIMESTAMP_ORDER_AMBIGUOUS", ts
                )
                self._finish_if_complete(side, price)
                continue
            qty = visible_quantity(self.books[side], side, price)
            if qty is None:
                self._censor_episode(
                    episode, "OB200_EDGE_VISIBILITY_LOST", ts
                )
                self._finish_if_complete(side, price)
                continue
            if ts > episode.start_ts_ms and qty >= episode.target_qty:
                for state in episode.unresolved():
                    deadline = episode.start_ts_ms + state.horizon_ms
                    if ts <= deadline:
                        state.outcome = "R"
                        state.reason = "EXACT_PRICE_TARGET_RECOVERED"
                        state.outcome_ts_ms = ts
                        state.recovery_ms = ts - episode.start_ts_ms
                self._finish_if_complete(side, price)

    def _open_new_episodes(
        self,
        ts: int,
        before: dict[str, dict[Decimal, Decimal]],
        after: dict[str, dict[Decimal, Decimal]],
        touch_counts: dict[tuple[str, Decimal], int],
    ) -> None:
        for side in SIDES:
            touched_prices = sorted(
                price for s, price in touch_counts if s == side
            )
            for price in touched_prices:
                if price in self.active[side]:
                    continue
                q0 = visible_quantity(before[side], side, price)
                q1 = visible_quantity(after[side], side, price)
                if q0 is None or q1 is None or q0 <= 0 or q1 >= q0:
                    continue
                rank = source_rank(before[side], side, price)
                if rank is None or not 1 <= rank <= 200:
                    continue
                self.episode_no += 1
                episode = Episode(
                    episode_id=(
                        f"{self.symbol}:{self.day.isoformat()}:"
                        f"{side}:{self.episode_no}"
                    ),
                    symbol=self.symbol,
                    day=self.day,
                    side=side,
                    price=price,
                    target_qty=q0,
                    post_depletion_qty=q1,
                    start_ts_ms=ts,
                    start_rank=rank,
                    stratum="Top5" if rank <= 5 else "6-200",
                )
                self.counts["episodes_opened"] += 1
                if touch_counts.get((side, price), 0) > 1:
                    self._censor_episode(
                        episode, "SAME_TIMESTAMP_ORDER_AMBIGUOUS", ts
                    )
                    self.finished.append(episode)
                else:
                    self._register_episode(episode)

    def on_group(self, ts: int, records: list[dict]) -> None:
        if not records:
            return
        kinds = {str(record.get("type")) for record in records}
        if "snapshot" in kinds:
            if len(records) != 1 or kinds != {"snapshot"}:
                self._censor_all("SAME_TIMESTAMP_ORDER_AMBIGUOUS", ts)
                self.books = None
                self.update_id = self.seq = self.last_ts = None
                self.counts["source_sequence_gaps"] += 1
                return
            self._snapshot(records[0])
            return
        if kinds != {"delta"}:
            raise ValueError("UNSUPPORTED_BOOK_MESSAGE_TYPE")
        if self.books is None:
            return
        if not self._sequence_preflight(records, ts):
            self._censor_all("SOURCE_SEQUENCE_GAP", ts)
            self.books = None
            self.update_id = self.seq = self.last_ts = None
            self.counts["source_sequence_gaps"] += 1
            return

        self._resolve_deadlines(ts, inclusive=False)
        try:
            before, after, touches = self._apply_delta_group(ts, records)
        except RuntimeError:
            self._censor_all("SOURCE_SEQUENCE_GAP", ts)
            self.books = None
            self.update_id = self.seq = self.last_ts = None
            self.counts["source_sequence_gaps"] += 1
            return
        self._resolve_touched_after_group(ts, touches)
        self._resolve_deadlines(ts, inclusive=True)
        self._open_new_episodes(ts, before, after, touches)

    def finish_day(self, day_end_ms: int) -> None:
        self._censor_all("DAY_END_RIGHT_CENSOR", day_end_ms)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def render_rows(episodes: list[Episode]) -> list[dict]:
    rows: list[dict] = []
    for episode in episodes:
        for horizon in sorted(episode.states):
            state = episode.states[horizon]
            if state.outcome is None:
                raise ValueError("UNRESOLVED_EPISODE_HORIZON")
            rows.append(
                {
                    "episode_id": episode.episode_id,
                    "indicator_id": 13,
                    "formula_version": VERSION,
                    "venue_id": "Bybit",
                    "market_source_id": "BYBIT_OB200_ARCHIVE",
                    "instrument_id": episode.symbol,
                    "date_utc": episode.day.isoformat(),
                    "side": "BID" if episode.side == "b" else "ASK",
                    "price": str(episode.price),
                    "target_base_qty": str(episode.target_qty),
                    "post_depletion_base_qty": str(
                        episode.post_depletion_qty
                    ),
                    "start_rank": episode.start_rank,
                    "stratum": episode.stratum,
                    "start_ts_exchange_ms": episode.start_ts_ms,
                    "horizon_s": horizon // 1000,
                    "outcome": state.outcome,
                    "outcome_reason": state.reason,
                    "outcome_ts_exchange_ms": state.outcome_ts_ms,
                    "recovery_ms": state.recovery_ms,
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


def calculate_archive(
    path: Path,
    symbol: str,
    day: date,
) -> tuple[dict, list[dict]]:
    if symbol not in COHORT or not START <= day < END_EXCLUSIVE:
        raise ValueError("OUT_OF_CONTRACT_SCOPE")
    if not path.is_file():
        raise FileNotFoundError(str(path))
    start_ms = int(
        datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000
    )
    end_ms = start_ms + 86_400_000
    reader = DepthRecoveryReader(symbol, day)
    previous_ts: int | None = None
    group_ts: int | None = None
    group: list[dict] = []
    records = 0
    suffix_records = 0
    seen_suffix = False

    with zipfile.ZipFile(path) as archive:
        member = f"{day}_{symbol}_ob200.data"
        if archive.namelist() != [member]:
            raise ValueError("ZIP_MEMBER_IDENTITY_MISMATCH")
        with archive.open(member) as stream:
            for line in stream:
                record = json.loads(line)
                ts = int(record["ts"])
                if previous_ts is not None and ts < previous_ts:
                    raise ValueError("BOOK_NATIVE_TIMESTAMP_NONMONOTONE")
                previous_ts = ts
                data = record.get("data")
                if (
                    record.get("topic") != f"orderbook.200.{symbol}"
                    or not isinstance(data, dict)
                    or data.get("s") != symbol
                ):
                    raise ValueError("BOOK_SOURCE_IDENTITY_MISMATCH")
                if ts < start_ms:
                    raise ValueError("BOOK_RECORD_BEFORE_UTC_DAY")
                if ts >= end_ms:
                    suffix_records += 1
                    seen_suffix = True
                    if suffix_records > 1000:
                        raise ValueError("UNBOUNDED_NEXT_UTC_DAY_SUFFIX")
                    continue
                if seen_suffix:
                    raise ValueError("BOOK_REENTERED_UTC_DAY_AFTER_SUFFIX")
                if group_ts is None:
                    group_ts = ts
                if ts != group_ts:
                    reader.on_group(group_ts, group)
                    group = []
                    group_ts = ts
                group.append(record)
                records += 1
        if group:
            assert group_ts is not None
            reader.on_group(group_ts, group)

    if reader.counts["snapshots"] == 0:
        raise ValueError("NO_DAY_SNAPSHOT")
    reader.finish_day(end_ms)
    episodes = sorted(
        reader.finished,
        key=lambda episode: (
            episode.start_ts_ms,
            episode.side,
            episode.price,
            episode.episode_id,
        ),
    )
    rows = render_rows(episodes)
    outcome_counts = {
        f"{outcome}_{h // 1000}s": sum(
            row["outcome"] == outcome and row["horizon_s"] == h // 1000
            for row in rows
        )
        for h in HORIZONS_MS
        for outcome in ("R", "N", "C")
    }
    manifest = {
        "algorithm": VERSION,
        "symbol": symbol,
        "date_utc": day.isoformat(),
        "source": str(path),
        "source_zip_sha256": hash_file(path),
        "source_script_sha256": hash_file(Path(__file__).resolve()),
        "raw_records_read": records,
        "source_suffix_records": suffix_records,
        "episodes": len(episodes),
        "rows": len(rows),
        "outcome_counts": outcome_counts,
        "reader_qc": reader.counts,
        "horizons_seconds": [h // 1000 for h in HORIZONS_MS],
        "horizon_endpoint_inclusive": True,
        "recovery_requires_strictly_later_native_timestamp": True,
        "same_timestamp_order_fail_closed": True,
        "source_gap_snapshot_reset_censors_unresolved": True,
        "ob200_edge_visibility_fail_closed": True,
        "no_cross_day_carry": True,
        "receiver_clock_verified": False,
        "verified_available_after": False,
        "readiness": "DESCRIPTIVE_ONLY_NOT_RESEARCH_READY",
    }
    return manifest, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=COHORT)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--rows", action="store_true")
    args = parser.parse_args()
    source = (
        args.raw_root
        / args.symbol
        / "orderbook"
        / f"{args.date}_{args.symbol}_ob200.data.zip"
    )
    manifest, rows = calculate_archive(source, args.symbol, args.date)
    print(json.dumps({"type": "manifest", **manifest}, sort_keys=True))
    if args.rows:
        for row in rows:
            print(json.dumps({"type": "row", **row}, sort_keys=True))


if __name__ == "__main__":
    main()
