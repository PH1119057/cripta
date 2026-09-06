from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import os
import zipfile
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from bybit_workbench.mayak.research.historical_signal_backfill import Signal, load_baseline
from bybit_workbench.mayak.research.objective_replay import CausalMayakReplay, MarketEvent

_orjson: Any
try:
    _orjson = importlib.import_module("orjson")
except ModuleNotFoundError:  # optional research acceleration; stdlib remains canonical fallback.
    _orjson = None

VERSION = "mayak-historical-orderbook-replay-v1"


def _json_backend() -> tuple[str, str | None]:
    if _orjson is not None:
        return "orjson", str(_orjson.__version__)
    return "stdlib-json", None


def _decode_json(raw: bytes) -> Any:
    return _orjson.loads(raw) if _orjson is not None else json.loads(raw)


RETAIN_SECONDS = 1005.0
PREVIOUS_DAY_THRESHOLD_SECONDS = 1000.0


def _levels(value: Any) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, list) or len(item) < 2:
            continue
        price = str(item[0])
        try:
            qty = float(item[1])
        except (TypeError, ValueError):
            continue
        result.append((price, qty))
    return result


def _event_timestamp(payload: dict[str, Any]) -> datetime | None:
    data = payload.get("data")
    data_map = data if isinstance(data, dict) else {}
    raw = payload.get("cts", data_map.get("cts", payload.get("ts", data_map.get("ts"))))
    if not isinstance(raw, (str, int, float)):
        return None
    try:
        millis = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000.0, tz=UTC)


def _normalize_event(payload: Any) -> tuple[str, datetime, dict[str, Any]] | None:
    if not isinstance(payload, dict):
        return None
    record_type = str(payload.get("type", "")).lower()
    if record_type not in {"snapshot", "delta"}:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    timestamp = _event_timestamp(payload)
    if timestamp is None:
        return None
    return record_type, timestamp, data


BOOK_FIELDS = (
    "best_bid",
    "best_ask",
    "mid_price",
    "bid_usd",
    "ask_usd",
    "imbalance",
    "bid_depth_5bps_usd",
    "ask_depth_5bps_usd",
    "bid_depth_10bps_usd",
    "ask_depth_10bps_usd",
    "bid_depth_25bps_usd",
    "ask_depth_25bps_usd",
    "bid_depth_50bps_usd",
    "ask_depth_50bps_usd",
    "bid_change_pct",
    "ask_change_pct",
    "bid_change_1m_pct",
    "ask_change_1m_pct",
    "imbalance_change_1m",
    "bid_change_5m_pct",
    "ask_change_5m_pct",
    "imbalance_change_5m",
    "bid_change_15m_pct",
    "ask_change_15m_pct",
    "imbalance_change_15m",
)


@dataclass(slots=True)
class MutableBook:
    bids: dict[str, float]
    asks: dict[str, float]
    ready: bool = False

    @classmethod
    def empty(cls) -> MutableBook:
        return cls({}, {}, False)


@dataclass(frozen=True, slots=True)
class UndoEvent:
    serial: int
    event_at: float
    uid: str
    prior_event_at: float | None
    prior_uid: str | None
    prior_ready: bool
    bid_old: tuple[tuple[str, float | None], ...] = ()
    ask_old: tuple[tuple[str, float | None], ...] = ()
    snapshot_prior_bids: tuple[tuple[str, float], ...] | None = None
    snapshot_prior_asks: tuple[tuple[str, float], ...] | None = None


@dataclass(frozen=True, slots=True)
class BookPoint:
    serial: int
    event_at: float
    uid: str
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]


@dataclass(slots=True)
class ReconstructionState:
    book: MutableBook
    undos: deque[UndoEvent]
    serial: int = 0
    last_event_at: float | None = None
    last_uid: str | None = None
    previous_update_id: int | None = None
    records: int = 0
    snapshots: int = 0
    deltas: int = 0
    bytes_read: int = 0

    @classmethod
    def empty(cls) -> ReconstructionState:
        return cls(MutableBook.empty(), deque())

    def reset_for_gap(self) -> None:
        self.book = MutableBook.empty()
        self.undos.clear()
        self.last_event_at = None
        self.last_uid = None
        self.previous_update_id = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _capture(book: MutableBook, *, serial: int, event_at: float, uid: str) -> BookPoint:
    if not book.ready:
        raise ValueError("cannot capture uninitialized orderbook")
    bids = tuple(sorted(((float(p), q) for p, q in book.bids.items() if q > 0), reverse=True))
    asks = tuple(sorted((float(p), q) for p, q in book.asks.items() if q > 0))
    return BookPoint(serial=serial, event_at=event_at, uid=uid, bids=bids, asks=asks)


def _apply_levels(side: dict[str, float], levels: Iterable[tuple[str, float]]) -> None:
    for price, qty in levels:
        if qty <= 0:
            side.pop(price, None)
        else:
            side[price] = qty


def _apply_event(
    state: ReconstructionState,
    *,
    record_type: str,
    event_at: float,
    uid: str,
    data: dict[str, Any],
) -> None:
    bids = _levels(data.get("b"))
    asks = _levels(data.get("a"))
    update_raw = data.get("u")
    update_id = int(update_raw) if update_raw is not None else None

    prior_at = state.last_event_at
    prior_uid = state.last_uid
    prior_ready = state.book.ready
    state.serial += 1

    if record_type == "snapshot":
        undo = UndoEvent(
            serial=state.serial,
            event_at=event_at,
            uid=uid,
            prior_event_at=prior_at,
            prior_uid=prior_uid,
            prior_ready=prior_ready,
            snapshot_prior_bids=tuple(state.book.bids.items()),
            snapshot_prior_asks=tuple(state.book.asks.items()),
        )
        state.book.bids = {price: qty for price, qty in bids if qty > 0}
        state.book.asks = {price: qty for price, qty in asks if qty > 0}
        state.book.ready = True
        state.previous_update_id = update_id
        state.snapshots += 1
    elif record_type == "delta":
        if not state.book.ready:
            raise ValueError(f"delta before first snapshot at {event_at}")
        if update_id is None or state.previous_update_id is None:
            raise ValueError(f"missing update id at {event_at}")
        if update_id != state.previous_update_id + 1:
            raise ValueError(
                f"ORDERBOOK_UPDATE_ID_GAP prior={state.previous_update_id} current={update_id}"
            )
        bid_old = tuple((price, state.book.bids.get(price)) for price, _ in bids)
        ask_old = tuple((price, state.book.asks.get(price)) for price, _ in asks)
        undo = UndoEvent(
            serial=state.serial,
            event_at=event_at,
            uid=uid,
            prior_event_at=prior_at,
            prior_uid=prior_uid,
            prior_ready=prior_ready,
            bid_old=bid_old,
            ask_old=ask_old,
        )
        _apply_levels(state.book.bids, bids)
        _apply_levels(state.book.asks, asks)
        state.previous_update_id = update_id
        state.deltas += 1
    else:
        raise ValueError(f"unsupported orderbook record type: {record_type}")

    state.undos.append(undo)
    state.last_event_at = event_at
    state.last_uid = uid
    while state.undos and state.undos[0].event_at < event_at - RETAIN_SECONDS:
        state.undos.popleft()


def _apply_event_light(
    state: ReconstructionState,
    *,
    record_type: str,
    event_at: float,
    uid: str,
    data: dict[str, Any],
) -> None:
    """Apply exact raw book state without retaining reverse history.

    This is used only outside the 1005-second causal window before a frozen
    touch. Update-id continuity, full book state, timestamps and counters remain
    exact; only undo objects that cannot affect any future snapshot are skipped.
    """
    bids = _levels(data.get("b"))
    asks = _levels(data.get("a"))
    update_raw = data.get("u")
    update_id = int(update_raw) if update_raw is not None else None
    state.serial += 1
    if record_type == "snapshot":
        state.book.bids = {price: qty for price, qty in bids if qty > 0}
        state.book.asks = {price: qty for price, qty in asks if qty > 0}
        state.book.ready = True
        state.previous_update_id = update_id
        state.snapshots += 1
    elif record_type == "delta":
        if not state.book.ready:
            raise ValueError(f"delta before first snapshot at {event_at}")
        if update_id is None or state.previous_update_id is None:
            raise ValueError(f"missing update id at {event_at}")
        if update_id != state.previous_update_id + 1:
            raise ValueError(
                f"ORDERBOOK_UPDATE_ID_GAP prior={state.previous_update_id} current={update_id}"
            )
        _apply_levels(state.book.bids, bids)
        _apply_levels(state.book.asks, asks)
        state.previous_update_id = update_id
        state.deltas += 1
    else:
        raise ValueError(f"unsupported orderbook record type: {record_type}")
    state.last_event_at = event_at
    state.last_uid = uid
    state.undos.clear()


def _reverse_once(
    bids: dict[str, float], asks: dict[str, float], undo: UndoEvent
) -> tuple[dict[str, float], dict[str, float], bool]:
    if undo.snapshot_prior_bids is not None and undo.snapshot_prior_asks is not None:
        return dict(undo.snapshot_prior_bids), dict(undo.snapshot_prior_asks), undo.prior_ready
    for price, old in undo.bid_old:
        if old is None:
            bids.pop(price, None)
        else:
            bids[price] = old
    for price, old in undo.ask_old:
        if old is None:
            asks.pop(price, None)
        else:
            asks[price] = old
    return bids, asks, undo.prior_ready


def _point_at_serial(state: ReconstructionState, target_serial: int) -> BookPoint | None:
    if not state.book.ready or state.last_event_at is None or state.last_uid is None:
        return None
    bids = dict(state.book.bids)
    asks = dict(state.book.asks)
    current_serial = state.serial
    current_at: float | None = state.last_event_at
    current_uid: str | None = state.last_uid
    ready: bool = state.book.ready
    for undo in reversed(state.undos):
        if current_serial <= target_serial:
            break
        bids, asks, ready = _reverse_once(bids, asks, undo)
        current_serial = undo.serial - 1
        current_at = undo.prior_event_at
        current_uid = undo.prior_uid
    if current_serial != target_serial or current_at is None or current_uid is None or not ready:
        return None
    return _capture(
        MutableBook(bids, asks, ready=True),
        serial=current_serial,
        event_at=current_at,
        uid=current_uid,
    )


def _history_baseline_serial(state: ReconstructionState, cutoff: float) -> int | None:
    # LiveMayakEngine collapses all book updates in the same integer second to the last one.
    second_last: dict[int, UndoEvent] = {}
    for undo in state.undos:
        second_last[int(undo.event_at)] = undo
    candidates = [item for item in second_last.values() if item.event_at <= cutoff]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.serial).serial


def _selected_points(state: ReconstructionState) -> tuple[list[BookPoint], dict[str, bool]]:
    if state.last_event_at is None or not state.undos:
        return [], {"current": False, "previous": False, "1m": False, "5m": False, "15m": False}
    current = _point_at_serial(state, state.serial)
    points: list[BookPoint] = []
    present = {
        "current": current is not None,
        "previous": False,
        "1m": False,
        "5m": False,
        "15m": False,
    }
    if current is None:
        return points, present
    points.append(current)
    if state.serial > 1:
        previous = _point_at_serial(state, state.serial - 1)
        if previous is not None:
            points.append(previous)
            present["previous"] = True
    for label, seconds in (("1m", 60), ("5m", 300), ("15m", 900)):
        serial = _history_baseline_serial(state, current.event_at - seconds)
        point = _point_at_serial(state, serial) if serial is not None else None
        if point is not None:
            points.append(point)
            present[label] = True
    unique = {point.serial: point for point in points}
    return sorted(unique.values(), key=lambda point: point.serial), present


def _liquidity_from_points(
    symbol: str, points: Sequence[BookPoint], target: float
) -> dict[str, Any]:
    if not points:
        return {}
    replay = CausalMayakReplay((symbol,), exact_liquidations=False)
    replay.set_supported("linear", {symbol})
    for point in points:
        replay.feed(
            MarketEvent(
                event_at=point.event_at,
                kind="ORDERBOOK",
                symbol=symbol,
                market="linear",
                payload={"bids": point.bids, "asks": point.asks},
            )
        )
    snapshot = replay.snapshot(target)
    book = snapshot["coin_market_contexts"][symbol]["payload"]["liquidity"]["derivatives"]
    return dict(book or {})


def _capture_signal(state: ReconstructionState, signal: Signal) -> dict[str, Any]:
    points, present = _selected_points(state)
    liquidity = _liquidity_from_points(signal.symbol, points, signal.touch_epoch)
    current = max(points, key=lambda point: point.serial) if points else None
    return {
        "signal": asdict(signal),
        "signal_key": signal.key,
        "liquidity": {field: liquidity.get(field) for field in BOOK_FIELDS},
        "source": {
            "current_event_at": current.event_at if current else None,
            "current_event_uid": current.uid if current else None,
            "baseline_present": present,
        },
    }


def _archive_events(path: Path) -> Iterable[tuple[int, str, float, dict[str, Any], int]]:
    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if len(members) != 1:
            raise ValueError(f"expected one member in {path}, found {len(members)}")
        with archive.open(members[0], "r") as handle:
            for line_no, raw_line in enumerate(handle, start=1):
                payload = _decode_json(raw_line)
                event = _normalize_event(payload)
                if event is None:
                    raise ValueError(f"unrecognized orderbook event {path}:{line_no}")
                record_type, event_dt, data = event
                yield line_no, record_type, event_dt.timestamp(), data, len(raw_line)


def _seconds_since_midnight(epoch: float) -> float:
    dt = datetime.fromtimestamp(epoch, UTC)
    midnight = datetime.combine(dt.date(), datetime.min.time(), tzinfo=UTC)
    return epoch - midnight.timestamp()


def _process_archive(
    path: Path,
    *,
    signals: Sequence[Signal],
    state: ReconstructionState,
    need_tail: bool,
) -> list[dict[str, Any]]:
    ordered = sorted(signals, key=lambda item: item.touch_epoch)
    index = 0
    output: list[dict[str, Any]] = []
    first_event = True
    tail_record_after: float | None = None
    for line_no, record_type, event_at, data, raw_bytes in _archive_events(path):
        state.bytes_read += raw_bytes
        state.records += 1
        while index < len(ordered) and ordered[index].touch_epoch < event_at:
            output.append(_capture_signal(state, ordered[index]))
            index += 1
        if index >= len(ordered) and not need_tail:
            break
        if first_event:
            if record_type != "snapshot":
                raise ValueError(f"first orderbook event is not snapshot: {path}")
            if need_tail:
                dt = datetime.fromtimestamp(event_at, UTC)
                next_midnight = datetime.combine(
                    dt.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                )
                tail_record_after = next_midnight.timestamp() - RETAIN_SECONDS
            first_event = False
        uid = f"{path.name}:{line_no}"
        next_touch = ordered[index].touch_epoch if index < len(ordered) else None
        record_undo = bool(next_touch is not None and event_at >= next_touch - RETAIN_SECONDS)
        if tail_record_after is not None and event_at >= tail_record_after:
            record_undo = True
        if record_undo:
            _apply_event(state, record_type=record_type, event_at=event_at, uid=uid, data=data)
        else:
            _apply_event_light(
                state, record_type=record_type, event_at=event_at, uid=uid, data=data
            )
    while index < len(ordered):
        output.append(_capture_signal(state, ordered[index]))
        index += 1
    return output


def _required_days(signals: Sequence[Signal]) -> dict[date, bool]:
    signal_days = {datetime.fromtimestamp(item.touch_epoch, UTC).date() for item in signals}
    required: dict[date, bool] = {day: False for day in signal_days}
    for signal in signals:
        if _seconds_since_midnight(signal.touch_epoch) < PREVIOUS_DAY_THRESHOLD_SECONDS:
            previous = datetime.fromtimestamp(signal.touch_epoch, UTC).date() - timedelta(days=1)
            required[previous] = True
    return required


def _load_raw_manifest(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("raw manifest entries missing")
    mapping: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, dict) or "path" not in item:
            raise ValueError("invalid raw manifest entry")
        mapping[str(item["path"])] = item
    return mapping, _sha256(path)


def replay_symbol(
    symbol: str,
    signals: Sequence[Signal],
    *,
    raw_root: Path,
    manifest_entries: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not signals:
        return [], {}
    by_day: dict[date, list[Signal]] = defaultdict(list)
    for signal in signals:
        by_day[datetime.fromtimestamp(signal.touch_epoch, UTC).date()].append(signal)
    required = _required_days(signals)
    days = sorted(required)
    state = ReconstructionState.empty()
    output: list[dict[str, Any]] = []
    archives: list[dict[str, Any]] = []
    last_day: date | None = None
    for pos, day in enumerate(days):
        if last_day is None or day != last_day + timedelta(days=1):
            state.reset_for_gap()
        rel = f"{symbol}/orderbook/{day.isoformat()}_{symbol}_ob200.data.zip"
        path = raw_root / rel
        entry = manifest_entries.get(rel)
        if entry is None or not path.is_file():
            raise FileNotFoundError(f"required orderbook archive missing from manifest/disk: {rel}")
        if path.stat().st_size != int(entry["bytes"]):
            raise ValueError(f"orderbook archive size mismatch: {rel}")
        next_day = days[pos + 1] if pos + 1 < len(days) else None
        need_tail = (
            bool(required.get(day))
            or next_day == day + timedelta(days=1)
            and bool(required.get(day))
        )
        day_signals = by_day.get(day, [])
        before_records = state.records
        before_bytes = state.bytes_read
        output.extend(_process_archive(path, signals=day_signals, state=state, need_tail=need_tail))
        archives.append(
            {
                "path": rel,
                "bytes": int(entry["bytes"]),
                "sha256": str(entry["sha256"]),
                "records_read": state.records - before_records,
                "raw_line_bytes_read": state.bytes_read - before_bytes,
                "signals": len(day_signals),
                "tail_required": need_tail,
            }
        )
        last_day = day
    stats = {
        "symbol": symbol,
        "signals": len(output),
        "archives": archives,
        "records_read": state.records,
        "snapshots": state.snapshots,
        "deltas": state.deltas,
        "raw_line_bytes_read": state.bytes_read,
    }
    return output, stats


def _flatten(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "signal_key": row["signal_key"],
            "symbol": row["signal"]["symbol"],
            "direction": row["signal"]["direction"],
            "touch_at": row["signal"]["touch_at"],
            "orderbook_current_event_at": row["source"]["current_event_at"],
        }
        item.update({f"liquidity_{key}": value for key, value in row["liquidity"].items()})
        present = row["source"]["baseline_present"]
        item.update({f"liquidity_baseline_{key}_present": value for key, value in present.items()})
        flat.append(item)
    return flat


def _atomic_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def write_outputs(
    rows: Sequence[dict[str, Any]],
    *,
    stats: Sequence[dict[str, Any]],
    output_dir: Path,
    source_commit: str,
    baseline: Path,
    raw_manifest: Path,
    raw_manifest_sha256: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    contexts = output_dir / "MAYAK_ORDERBOOK_CONTEXTS.jsonl"
    tmp = contexts.with_name(contexts.name + ".partial")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    os.replace(tmp, contexts)
    flat = _flatten(rows)
    csv_path = output_dir / "MAYAK_ORDERBOOK_FEATURES.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(flat)
    missing = {
        key: sum(not bool(row["source"]["baseline_present"].get(key)) for row in rows)
        for key in ("current", "previous", "1m", "5m", "15m")
    }
    manifest = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "project_commit": source_commit,
        "replay_code_sha256": _sha256(Path(__file__).resolve()),
        "json_parser_backend": _json_backend()[0],
        "json_parser_version": _json_backend()[1],
        "undo_capture_mode": "signal-window-only-v1",
        "baseline": str(baseline),
        "baseline_sha256": _sha256(baseline),
        "raw_manifest": str(raw_manifest),
        "raw_manifest_sha256": raw_manifest_sha256,
        "signals": len(rows),
        "symbols": sorted({row["signal"]["symbol"] for row in rows}),
        "source_semantics": "exact depth-200 snapshot+delta raw archive",
        "sparse_equivalence_contract": "FULL_BOOK_FEED_LIQUIDITY_JSON_EQ_SPARSE_REPLAY",
        "baseline_missing_counts": missing,
        "symbol_stats": list(stats),
        "records_read": sum(int(item["records_read"]) for item in stats),
        "raw_line_bytes_read": sum(int(item["raw_line_bytes_read"]) for item in stats),
        "outcome_used_by_replay": False,
        "trading_effect": "NONE",
    }
    _atomic_json(output_dir / "RUN_MANIFEST.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exact causal MAYAK depth-200 orderbook replay")
    parser.add_argument("--baseline-csv", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--raw-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-signals", type=int, default=1063)
    args = parser.parse_args(argv)
    if len(args.source_commit) != 40:
        raise ValueError("source_commit must be full Git SHA")
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    signals = load_baseline(args.baseline_csv)
    if len(signals) != args.expected_signals:
        raise ValueError(f"signal count mismatch: {len(signals)}")
    if {item.symbol for item in signals}.difference(symbols):
        raise ValueError("signals outside explicit orderbook panel")
    entries, manifest_sha = _load_raw_manifest(args.raw_manifest)
    all_rows: list[dict[str, Any]] = []
    all_stats: list[dict[str, Any]] = []
    for symbol in symbols:
        symbol_signals = [item for item in signals if item.symbol == symbol]
        print(
            f"ORDERBOOK_REPLAY symbol={symbol} signals={len(symbol_signals)} stage=START",
            flush=True,
        )
        rows, stats = replay_symbol(
            symbol,
            symbol_signals,
            raw_root=args.raw_root,
            manifest_entries=entries,
        )
        all_rows.extend(rows)
        all_stats.append(stats)
        print(
            f"ORDERBOOK_REPLAY symbol={symbol} signals={len(rows)} "
            f"records={stats['records_read']} stage=DONE",
            flush=True,
        )
    all_rows.sort(key=lambda row: (row["signal"]["touch_epoch"], row["signal_key"]))
    manifest = write_outputs(
        all_rows,
        stats=all_stats,
        output_dir=args.output_dir,
        source_commit=args.source_commit,
        baseline=args.baseline_csv,
        raw_manifest=args.raw_manifest,
        raw_manifest_sha256=manifest_sha,
    )
    print(
        f"MAYAK_ORDERBOOK_REPLAY=PASS signals={manifest['signals']} "
        f"records={manifest['records_read']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
