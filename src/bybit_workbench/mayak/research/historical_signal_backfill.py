from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import heapq
import json
import os
import subprocess
import time as time_module
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast

from bybit_workbench.mayak.research.objective_replay import CausalMayakReplay

VERSION = "mayak-historical-signal-backfill-v1"
PRE_ROLL_SECONDS = 7200
HEARTBEAT_SECONDS = 20.0
REPLAY_EXACT_LAYERS = {
    "derivatives_public_trades": "EXACT_RAW_ARCHIVE",
    "derivatives_price_path": "EXACT_RAW_ARCHIVE",
    "relative_strength": "EXACT_WITHIN_SELECTED_PANEL",
    "spot_public_trades": "NO_DATA",
    "open_interest": "NO_DATA",
    "funding": "NO_DATA",
    "mark_index_premium": "NO_DATA",
    "orderbook": "NO_DATA_FIRST_PASS",
    "liquidations": "NO_DATA_EXACT_SOURCE_NOT_AVAILABLE",
    "transport_history": "NO_DATA",
}


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    direction: str
    touch_at: str
    touch_epoch: float
    original_entry_price: float

    @property
    def key(self) -> str:
        return f"{self.symbol}|{self.direction}|{self.touch_at}"


@dataclass(slots=True)
class StreamStats:
    symbol: str
    files_opened: int = 0
    compressed_bytes: int = 0
    rows_seen: int = 0
    rows_yielded: int = 0
    first_event_at: float | None = None
    last_event_at: float | None = None


@dataclass(frozen=True, slots=True)
class BlockSpec:
    block_id: str
    start_epoch: float
    end_epoch: float
    signals: tuple[Signal, ...]
    symbols: tuple[str, ...]
    raw_root: str
    output_dir: str
    source_commit: str
    replay_code_sha256: str
    force: bool = False


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _replay_code_fingerprint() -> tuple[str, list[dict[str, str]]]:
    base = Path(__file__).resolve().parent
    paths = [
        base.parent / "core" / "live.py",
        base / "objective_replay.py",
        Path(__file__).resolve(),
    ]
    rows = [
        {"path": str(path), "sha256": _sha256(path)}
        for path in paths
    ]
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), rows


def _repo_commit() -> str:
    repo = Path(__file__).resolve().parents[4]
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()


def load_baseline(path: Path, scenario: str = "BASELINE_0P00") -> list[Signal]:
    required = {"symbol", "direction", "touch_at", "original_entry_price", "scenario"}
    rows: list[Signal] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"baseline missing columns: {sorted(missing)}")
        for row in reader:
            if row["scenario"] != scenario:
                continue
            touch = _parse_datetime(row["touch_at"])
            rows.append(
                Signal(
                    symbol=row["symbol"].strip().upper(),
                    direction=row["direction"].strip(),
                    touch_at=touch.isoformat(),
                    touch_epoch=touch.timestamp(),
                    original_entry_price=float(row["original_entry_price"]),
                )
            )
    keys = [row.key for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("baseline has duplicate symbol/direction/touch_at keys")
    return sorted(rows, key=lambda row: (row.touch_epoch, row.symbol, row.direction))


def load_plus_110_outcomes(path: Path) -> dict[str, dict[str, Any]]:
    required = {
        "symbol",
        "direction",
        "touch_at",
        "entry_price",
        "outcome",
        "event_at",
        "complete_horizon",
    }
    result: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"+1.10 outcome file missing columns: {sorted(missing)}")
        for row in reader:
            touch = _parse_datetime(row["touch_at"]).isoformat()
            key = f"{row['symbol'].strip().upper()}|{row['direction'].strip()}|{touch}"
            if key in result:
                raise ValueError(f"duplicate +1.10 outcome key: {key}")
            result[key] = {
                "plus_110_vs_minus_100": row["outcome"],
                "plus_110_event_at": row["event_at"],
                "plus_110_complete_horizon": row["complete_horizon"],
            }
    return result


def load_plus_010_outcomes(
    path: Path, baseline: Sequence[Signal]
) -> dict[str, dict[str, Any]]:
    """Recover exact +0.10-vs--1 path from the P47J activation audit.

    Rows absent from the audit are the frozen initial -1% stops. Rows with an
    activation timestamp reached +0.10 before the initial stop. Rows present without
    activation are unresolved at data end. This mapping is external to MAYAK replay.
    """
    by_symbol_touch = {(row.symbol, row.touch_at): row for row in baseline}
    audit: dict[tuple[str, str], dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"symbol", "touch_at", "activation_at", "outcome"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"+0.10 audit missing columns: {sorted(missing)}")
        for raw_audit_row in reader:
            touch = _parse_datetime(raw_audit_row["touch_at"]).isoformat()
            key = (raw_audit_row["symbol"].strip().upper(), touch)
            if key in audit:
                raise ValueError(f"duplicate +0.10 audit key: {key}")
            audit[key] = dict(raw_audit_row)
    unknown = set(audit).difference(by_symbol_touch)
    if unknown:
        raise ValueError(f"+0.10 audit contains keys outside baseline: {len(unknown)}")
    result: dict[str, dict[str, Any]] = {}
    for signal in baseline:
        audit_row = audit.get((signal.symbol, signal.touch_at))
        if audit_row is None:
            state = "hit_minus_1p00_before_plus_0p10"
            activation_at = ""
        elif audit_row.get("activation_at"):
            state = "reached_plus_0p10_before_minus_1p00"
            activation_at = audit_row["activation_at"]
        else:
            state = "data_end_before_plus_0p10_or_minus_1p00"
            activation_at = ""
        result[signal.key] = {
            "plus_010_vs_minus_100": state,
            "plus_010_activation_at": activation_at,
        }
    return result


def validate_outcome_coverage(
    signals: Sequence[Signal],
    plus_110: dict[str, dict[str, Any]],
    plus_010: dict[str, dict[str, Any]],
) -> None:
    keys = {signal.key for signal in signals}
    missing_110 = keys.difference(plus_110)
    missing_010 = keys.difference(plus_010)
    if missing_110 or missing_010:
        raise ValueError(
            f"outcome coverage mismatch: missing_110={len(missing_110)} "
            f"missing_010={len(missing_010)}"
        )


def _dates(start_epoch: float, end_epoch: float) -> Iterator[date]:
    current = datetime.fromtimestamp(start_epoch, UTC).date()
    end = datetime.fromtimestamp(end_epoch, UTC).date()
    while current <= end:
        yield current
        current += timedelta(days=1)


def required_archives(specs: Sequence[BlockSpec]) -> list[Path]:
    paths: set[Path] = set()
    for spec in specs:
        root = Path(spec.raw_root)
        for symbol in spec.symbols:
            for day in _dates(spec.start_epoch, spec.end_epoch):
                paths.add(root / symbol / "public_trades" / f"{symbol}{day.isoformat()}.csv.gz")
    return sorted(paths)


def archive_metadata_fingerprint(
    paths: Sequence[Path], root: Path
) -> tuple[str, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for path in paths:
        if not path.exists():
            missing.append(str(path))
            continue
        stat = path.stat()
        try:
            relative = str(path.relative_to(root))
        except ValueError:
            relative = str(path)
        rows.append(
            {
                "path": relative,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    if missing:
        raise FileNotFoundError("required raw archives missing: " + ", ".join(missing[:20]))
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), rows


def archive_content_manifest_fingerprint(
    paths: Sequence[Path], root: Path, manifest_path: Path
) -> tuple[str, str, list[dict[str, Any]]]:
    raw = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    document = json.loads(raw.decode("utf-8-sig"))
    entries = document.get("entries") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise ValueError("raw SHA256 manifest has no entries list")
    by_path: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("path") or "")
        digest = str(item.get("sha256") or "")
        size = item.get("bytes")
        if relative and len(digest) == 64 and isinstance(size, int):
            by_path[relative] = {"path": relative, "bytes": size, "sha256": digest}
    selected: list[dict[str, Any]] = []
    missing: list[str] = []
    mismatched: list[str] = []
    for path in paths:
        relative = str(path.relative_to(root))
        item = by_path.get(relative)
        if item is None:
            missing.append(relative)
            continue
        if not path.exists() or path.stat().st_size != item["bytes"]:
            mismatched.append(relative)
            continue
        selected.append(item)
    if missing or mismatched:
        raise ValueError(
            "raw SHA256 manifest mismatch: "
            f"missing={len(missing)} size_mismatch={len(mismatched)} "
            f"examples={(missing + mismatched)[:10]}"
        )
    selected.sort(key=lambda item: str(item["path"]))
    canonical = json.dumps(selected, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return fingerprint, manifest_sha256, selected


def _iter_symbol_trades(
    raw_root: Path,
    symbol: str,
    start_epoch: float,
    end_epoch: float,
    stats: StreamStats,
) -> Iterator[tuple[float, str, float, float]]:
    required = {"timestamp", "symbol", "side", "size", "price"}
    for day in _dates(start_epoch, end_epoch):
        path = raw_root / symbol / "public_trades" / f"{symbol}{day.isoformat()}.csv.gz"
        if not path.exists():
            raise FileNotFoundError(path)
        stats.files_opened += 1
        stats.compressed_bytes += path.stat().st_size
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None:
                raise ValueError(f"empty raw trade archive: {path}")
            columns = {name: index for index, name in enumerate(header)}
            missing = required.difference(columns)
            if missing:
                raise ValueError(f"raw trade archive {path} missing columns: {sorted(missing)}")
            for row in reader:
                stats.rows_seen += 1
                event_at = float(row[columns["timestamp"]])
                if event_at < start_epoch:
                    continue
                if event_at > end_epoch:
                    break
                row_symbol = row[columns["symbol"]].strip().upper()
                if row_symbol != symbol:
                    raise ValueError(
                        f"archive symbol mismatch: expected={symbol} actual={row_symbol}"
                    )
                stats.rows_yielded += 1
                stats.first_event_at = (
                    event_at if stats.first_event_at is None else stats.first_event_at
                )
                stats.last_event_at = event_at
                yield (
                    event_at,
                    row[columns["side"]],
                    float(row[columns["price"]]),
                    float(row[columns["size"]]),
                )


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _block_path(spec: BlockSpec) -> Path:
    return Path(spec.output_dir) / "blocks" / f"{spec.block_id}.json"


def _reuse_block(spec: BlockSpec) -> dict[str, Any] | None:
    path = _block_path(spec)
    if spec.force or not path.exists():
        return None
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw_payload, dict):
        return None
    payload = cast(dict[str, Any], raw_payload)
    expected_keys = [signal.key for signal in spec.signals]
    if (
        payload.get("version") != VERSION
        or payload.get("source_commit") != spec.source_commit
        or payload.get("symbols") != list(spec.symbols)
        or payload.get("signal_keys") != expected_keys
        or payload.get("replay_code_sha256") != spec.replay_code_sha256
    ):
        return None
    payload["reused"] = True
    return payload


def run_block(spec: BlockSpec) -> dict[str, Any]:
    reused = _reuse_block(spec)
    if reused is not None:
        print(
            f"MAYAK_REPLAY block={spec.block_id} cache=HIT "
            f"signals={len(spec.signals)}",
            flush=True,
        )
        return reused
    raw_root = Path(spec.raw_root)
    replay = CausalMayakReplay(spec.symbols, exact_liquidations=False)
    started = time_module.monotonic()
    next_heartbeat = started + HEARTBEAT_SECONDS
    print(
        f"MAYAK_REPLAY block={spec.block_id} cache=MISS stage=START "
        f"signals={len(spec.signals)} symbols={len(spec.symbols)}",
        flush=True,
    )
    stats = [StreamStats(symbol=symbol) for symbol in spec.symbols]
    streams = [
        iter(_iter_symbol_trades(raw_root, symbol, spec.start_epoch, spec.end_epoch, stat))
        for symbol, stat in zip(spec.symbols, stats, strict=True)
    ]
    heap: list[tuple[float, int, str, float, float]] = []
    for index, stream in enumerate(streams):
        try:
            event_at, side, price, size = next(stream)
        except StopIteration as exc:
            raise RuntimeError(
                f"no derivatives trade events for {spec.symbols[index]} in block {spec.block_id}"
            ) from exc
        heapq.heappush(heap, (event_at, index, side, price, size))

    grouped: dict[float, list[Signal]] = defaultdict(list)
    for signal in spec.signals:
        grouped[signal.touch_epoch].append(signal)
    rows: list[dict[str, Any]] = []
    processed_events = 0
    targets = sorted(grouped)
    for target_index, target_at in enumerate(targets, start=1):
        while heap and heap[0][0] <= target_at:
            event_at, index, side, price, size = heapq.heappop(heap)
            replay.feed_trade(
                event_at,
                spec.symbols[index],
                "linear",
                side,
                price,
                size,
            )
            processed_events += 1
            now_mono = time_module.monotonic()
            if now_mono >= next_heartbeat:
                elapsed = max(0.001, now_mono - started)
                completed = len(rows)
                print(
                    f"MAYAK_REPLAY block={spec.block_id} stage=STREAM "
                    f"signals={completed}/{len(spec.signals)} "
                    f"events={processed_events} events_per_s={processed_events / elapsed:.0f} "
                    f"elapsed_s={elapsed:.1f}",
                    flush=True,
                )
                next_heartbeat = now_mono + HEARTBEAT_SECONDS
            try:
                next_at, next_side, next_price, next_size = next(streams[index])
            except StopIteration:
                continue
            heapq.heappush(
                heap,
                (next_at, index, next_side, next_price, next_size),
            )
        snapshot_started = time_module.monotonic()
        print(
            f"MAYAK_REPLAY block={spec.block_id} stage=SNAPSHOT_START "
            f"target={target_index}/{len(targets)} "
            f"signals_done={len(rows)}/{len(spec.signals)} events={processed_events} "
            f"at={datetime.fromtimestamp(target_at, UTC).isoformat()}",
            flush=True,
        )
        snapshot = replay.snapshot(target_at)
        snapshot_elapsed = time_module.monotonic() - snapshot_started
        print(
            f"MAYAK_REPLAY block={spec.block_id} stage=SNAPSHOT_DONE "
            f"target={target_index}/{len(targets)} events={processed_events} "
            f"snapshot_s={snapshot_elapsed:.3f}",
            flush=True,
        )
        for signal in grouped[target_at]:
            coin_context = snapshot["coin_market_contexts"].get(signal.symbol)
            if coin_context is None:
                raise RuntimeError(f"missing coin context for signal {signal.key}")
            if _parse_datetime(str(coin_context["observed_at"])).timestamp() > signal.touch_epoch:
                raise RuntimeError(f"future coin context for signal {signal.key}")
            rows.append(
                {
                    "signal": asdict(signal),
                    "signal_key": signal.key,
                    "mayak": {
                        "observed_at": snapshot["observed_at"],
                        "engine_version": snapshot["engine_version"],
                        "feature_version": snapshot["feature_version"],
                        "state": str(snapshot["state"]),
                        "confidence": snapshot["confidence"],
                        "price_breadth": snapshot["price_breadth"],
                        "money_breadth": snapshot["money_breadth"],
                        "direction_synchronization": snapshot[
                            "direction_synchronization"
                        ],
                        "coin_context": coin_context,
                        "dispatcher_handoff": snapshot["dispatcher_handoff"],
                    },
                    "replay_source_coverage": REPLAY_EXACT_LAYERS,
                }
            )
    if len(rows) != len(spec.signals):
        raise RuntimeError(
            f"block signal count mismatch expected={len(spec.signals)} actual={len(rows)}"
        )
    rows.sort(key=lambda row: (row["signal"]["touch_epoch"], row["signal_key"]))
    payload = {
        "version": VERSION,
        "source_commit": spec.source_commit,
        "replay_code_sha256": spec.replay_code_sha256,
        "block_id": spec.block_id,
        "symbols": list(spec.symbols),
        "start_epoch": spec.start_epoch,
        "end_epoch": spec.end_epoch,
        "signal_keys": [signal.key for signal in spec.signals],
        "signals": rows,
        "processed_events": processed_events,
        "stream_stats": [asdict(item) for item in stats],
        "replay_source_coverage": REPLAY_EXACT_LAYERS,
        "reused": False,
    }
    _atomic_json(_block_path(spec), payload)
    elapsed = max(0.001, time_module.monotonic() - started)
    print(
        f"MAYAK_REPLAY block={spec.block_id} stage=DONE "
        f"signals={len(rows)}/{len(spec.signals)} events={processed_events} "
        f"events_per_s={processed_events / elapsed:.0f} elapsed_s={elapsed:.1f}",
        flush=True,
    )
    return payload


def build_blocks(
    signals: Sequence[Signal],
    *,
    symbols: tuple[str, ...],
    raw_root: Path,
    output_dir: Path,
    source_commit: str,
    replay_code_sha256: str,
    block_days: int,
    force: bool,
) -> list[BlockSpec]:
    if not signals:
        return []
    if block_days < 1:
        raise ValueError("block_days must be >= 1")
    first_day = datetime.fromtimestamp(min(row.touch_epoch for row in signals), UTC).date()
    grouped: dict[int, list[Signal]] = defaultdict(list)
    for signal in signals:
        day = datetime.fromtimestamp(signal.touch_epoch, UTC).date()
        grouped[(day - first_day).days // block_days].append(signal)
    result: list[BlockSpec] = []
    for index in sorted(grouped):
        rows = sorted(grouped[index], key=lambda row: (row.touch_epoch, row.key))
        block_start_day = first_day + timedelta(days=index * block_days)
        block_end_day = block_start_day + timedelta(days=block_days - 1)
        start_at = datetime.combine(block_start_day, time.min, tzinfo=UTC).timestamp()
        start_at -= PRE_ROLL_SECONDS
        end_at = max(row.touch_epoch for row in rows)
        result.append(
            BlockSpec(
                block_id=f"{block_start_day.isoformat()}__{block_end_day.isoformat()}",
                start_epoch=start_at,
                end_epoch=end_at,
                signals=tuple(rows),
                symbols=symbols,
                raw_root=str(raw_root),
                output_dir=str(output_dir),
                source_commit=source_commit,
                replay_code_sha256=replay_code_sha256,
                force=force,
            )
        )
    return result


def _flatten(
    context_row: dict[str, Any],
    plus_010: dict[str, Any],
    plus_110: dict[str, Any],
) -> dict[str, Any]:
    signal = context_row["signal"]
    mayak = context_row["mayak"]
    coin = mayak["coin_context"]
    payload = coin["payload"]
    result: dict[str, Any] = {
        "signal_key": context_row["signal_key"],
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "touch_at": signal["touch_at"],
        "original_entry_price": signal["original_entry_price"],
        **plus_010,
        **plus_110,
        "mayak_state": mayak["state"],
        "mayak_confidence": mayak["confidence"],
        "mayak_engine_version": mayak["engine_version"],
        "mayak_feature_version": mayak["feature_version"],
        "coin_context_id": coin["coin_context_id"],
        "coin_data_quality": coin["data_quality"],
        "market_median_return_5m_pct": mayak["price_breadth"].get(
            "median_return_pct"
        ),
        "market_up_share": mayak["price_breadth"].get("up_share"),
        "market_down_share": mayak["price_breadth"].get("down_share"),
        "market_synchronization": mayak["direction_synchronization"].get(
            "agreement"
        ),
        "spot_status": (payload["money"]["spot"].get("5m") or {}).get("status"),
        "liquidation_status": payload["liquidations"].get("status"),
    }
    for horizon in ("1m", "5m", "15m", "30m", "60m"):
        flow = payload["money"]["derivatives"].get(horizon) or {}
        prefix = f"derivatives_{horizon}_"
        for field in (
            "buy_usd",
            "sell_usd",
            "net_usd",
            "turnover_usd",
            "net_share",
            "speed_usd_per_min",
            "acceleration_usd_per_min2",
            "large_trade_share",
            "turnover_ratio_to_prior",
        ):
            result[prefix + field] = flow.get(field)
    for horizon in ("1m", "5m", "15m", "60m"):
        strength = payload["relative_strength"].get(horizon) or {}
        prefix = f"relative_{horizon}_"
        for field in (
            "coin_return_pct",
            "panel_median_return_pct",
            "relative_to_panel_pct",
            "relative_to_btc_pct",
            "relative_to_eth_pct",
        ):
            result[prefix + field] = strength.get(field)
    return result


def write_outputs(
    *,
    output_dir: Path,
    blocks: Sequence[dict[str, Any]],
    selected_signals: Sequence[Signal],
    plus_010: dict[str, dict[str, Any]],
    plus_110: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    contexts = [row for block in blocks for row in block["signals"]]
    contexts.sort(key=lambda row: (row["signal"]["touch_epoch"], row["signal_key"]))
    expected = [signal.key for signal in selected_signals]
    actual = [row["signal_key"] for row in contexts]
    if actual != expected:
        raise RuntimeError("combined replay context keys do not match selected baseline")

    output_dir.mkdir(parents=True, exist_ok=True)
    context_path = output_dir / "MAYAK_SIGNAL_CONTEXTS.jsonl"
    tmp = context_path.with_name(context_path.name + ".partial")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in contexts:
            handle.write(
                json.dumps(row, ensure_ascii=False, default=str, sort_keys=True) + "\n"
            )
    os.replace(tmp, context_path)

    flat = [
        _flatten(row, plus_010[row["signal_key"]], plus_110[row["signal_key"]])
        for row in contexts
    ]
    csv_path = output_dir / "MAYAK_ENTRY_CORRELATION.csv"
    tmp_csv = csv_path.with_name(csv_path.name + ".partial")
    fieldnames = list(flat[0]) if flat else []
    with tmp_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(flat)
    os.replace(tmp_csv, csv_path)

    summary = {
        "version": VERSION,
        "signals": len(contexts),
        "symbols": sorted({row["signal"]["symbol"] for row in contexts}),
        "plus_010_outcomes": dict(
            Counter(row["plus_010_vs_minus_100"] for row in flat)
        ),
        "plus_110_outcomes": dict(
            Counter(row["plus_110_vs_minus_100"] for row in flat)
        ),
        "coin_data_quality": dict(Counter(row["coin_data_quality"] for row in flat)),
        "exact_layers": REPLAY_EXACT_LAYERS,
        "processed_events": sum(int(block["processed_events"]) for block in blocks),
        "reused_blocks": sum(bool(block.get("reused")) for block in blocks),
    }
    _atomic_json(output_dir / "SUMMARY.json", summary)
    _atomic_json(output_dir / "RUN_MANIFEST.json", manifest)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Causal MAYAK v2.2 historical context replay at frozen Entry signals"
    )
    parser.add_argument("--baseline-csv", type=Path, required=True)
    parser.add_argument("--outcome-110-csv", type=Path, required=True)
    parser.add_argument("--outcome-010-events", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbols", required=True, help="comma-separated explicit replay panel")
    parser.add_argument("--dataset-label", required=True)
    parser.add_argument(
        "--source-commit",
        help="explicit authoritative source Git commit; required for non-git overlays",
    )
    parser.add_argument("--expected-signals", type=int)
    parser.add_argument("--block-days", type=int, default=7)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--date", help="optional UTC date YYYY-MM-DD smoke filter")
    parser.add_argument("--limit-signals", type=int)
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    symbols = tuple(
        item.strip().upper() for item in args.symbols.split(",") if item.strip()
    )
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("--symbols must contain a non-empty unique explicit panel")
    baseline_all = load_baseline(args.baseline_csv)
    if args.expected_signals is not None and len(baseline_all) != args.expected_signals:
        raise ValueError(
            f"baseline count mismatch expected={args.expected_signals} actual={len(baseline_all)}"
        )
    unknown = {row.symbol for row in baseline_all}.difference(symbols)
    if unknown:
        raise ValueError(f"baseline contains symbols outside explicit panel: {sorted(unknown)}")
    selected = list(baseline_all)
    if args.date:
        selected = [
            row
            for row in selected
            if datetime.fromtimestamp(row.touch_epoch, UTC).date().isoformat() == args.date
        ]
    if args.limit_signals is not None:
        if args.limit_signals < 1:
            raise ValueError("--limit-signals must be positive")
        selected = selected[: args.limit_signals]
    if not selected:
        raise ValueError("no signals selected for replay")

    source_commit = args.source_commit or _repo_commit()
    valid_sha = len(source_commit) == 40 and all(
        ch in "0123456789abcdef" for ch in source_commit.lower()
    )
    if not valid_sha:
        raise ValueError("--source-commit must be a 40-character Git SHA")
    replay_code_sha256, replay_code_files = _replay_code_fingerprint()
    specs = build_blocks(
        selected,
        symbols=symbols,
        raw_root=args.raw_root,
        output_dir=args.output_dir,
        source_commit=source_commit,
        replay_code_sha256=replay_code_sha256,
        block_days=args.block_days,
        force=args.force,
    )
    archive_paths = required_archives(specs)
    metadata_fingerprint, archive_rows = archive_metadata_fingerprint(
        archive_paths, args.raw_root
    )
    raw_sha_manifest = args.raw_root / "MANIFEST.sha256.json"
    content_fingerprint, raw_manifest_sha256, content_rows = (
        archive_content_manifest_fingerprint(
            archive_paths, args.raw_root, raw_sha_manifest
        )
    )
    manifest = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "project_commit": source_commit,
        "replay_code_sha256": replay_code_sha256,
        "replay_code_files": replay_code_files,
        "dataset_label": args.dataset_label,
        "baseline_csv": str(args.baseline_csv),
        "baseline_sha256": _sha256(args.baseline_csv),
        "outcome_110_csv": str(args.outcome_110_csv),
        "outcome_110_sha256": _sha256(args.outcome_110_csv),
        "outcome_010_events": str(args.outcome_010_events),
        "outcome_010_sha256": _sha256(args.outcome_010_events),
        "raw_root": str(args.raw_root),
        "archive_metadata_fingerprint": metadata_fingerprint,
        "archive_manifest": archive_rows,
        "raw_sha256_manifest": str(raw_sha_manifest),
        "raw_sha256_manifest_sha256": raw_manifest_sha256,
        "selected_raw_content_fingerprint": content_fingerprint,
        "selected_raw_content_manifest": content_rows,
        "symbols": list(symbols),
        "selected_signal_count": len(selected),
        "selected_first_signal_at": selected[0].touch_at,
        "selected_last_signal_at": selected[-1].touch_at,
        "block_days": args.block_days,
        "workers": args.workers,
        "exact_layers": REPLAY_EXACT_LAYERS,
        "exact_liquidation_coverage": {
            "status": "NO_DATA",
            "interval": None,
            "reason": "EXACT_LIQUIDATION_SOURCE_NOT_AVAILABLE_FOR_FROZEN_PERIOD",
        },
        "replay_code_hashes": {
            "historical_signal_backfill_sha256": _sha256(Path(__file__)),
            "objective_replay_sha256": _sha256(Path(__file__).with_name("objective_replay.py")),
            "live_engine_sha256": _sha256(
                Path(__file__).resolve().parents[1] / "core" / "live.py"
            ),
        },
        "causality": "raw market events event_at <= signal touch_at only",
        "outcomes_used_by_replay": False,
        "synchronization_change_note": (
            "block replay preserves agreement/state/coin contexts; the change field on the "
            "first snapshot of a block has no prior snapshot and is not used as an exact feature"
        ),
    }

    workers = max(1, int(args.workers))
    if workers == 1:
        blocks = [run_block(spec) for spec in specs]
    else:
        by_id: dict[str, dict[str, Any]] = {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_map = {pool.submit(run_block, spec): spec.block_id for spec in specs}
            for future in as_completed(future_map):
                block = future.result()
                by_id[block["block_id"]] = block
        blocks = [by_id[spec.block_id] for spec in specs]

    plus_110_all = load_plus_110_outcomes(args.outcome_110_csv)
    plus_010_all = load_plus_010_outcomes(args.outcome_010_events, baseline_all)
    validate_outcome_coverage(baseline_all, plus_110_all, plus_010_all)
    selected_keys = {row.key for row in selected}
    plus_110 = {key: value for key, value in plus_110_all.items() if key in selected_keys}
    plus_010 = {key: value for key, value in plus_010_all.items() if key in selected_keys}
    write_outputs(
        output_dir=args.output_dir,
        blocks=blocks,
        selected_signals=selected,
        plus_010=plus_010,
        plus_110=plus_110,
        manifest=manifest,
    )
    print(f"MAYAK_HISTORICAL_SIGNAL_BACKFILL=PASS signals={len(selected)} blocks={len(blocks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
