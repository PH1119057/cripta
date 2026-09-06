from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import os
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bybit_workbench.mayak.research.historical_signal_backfill import Signal, load_baseline
from bybit_workbench.mayak.research.objective_replay import CausalMayakReplay, MarketEvent

VERSION = "mayak-historical-positioning-replay-v1"
PRE_ROLL_SECONDS = 7200
OI_FIELDS = (
    "open_interest",
    "open_interest_value",
    "open_interest_change_5m_pct",
    "open_interest_change_15m_pct",
    "open_interest_change_30m_pct",
    "open_interest_change_60m_pct",
    "open_interest_speed_5m_pct_per_min",
    "open_interest_acceleration_5m_pct_per_min2",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_at(value: str) -> datetime:
    item = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if item.tzinfo is None:
        item = item.replace(tzinfo=UTC)
    return item.astimezone(UTC)


def _iter_oi(path: Path, start: float, end: float) -> Iterator[tuple[float, float]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "open_interest"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"OI file {path} missing columns: {sorted(missing)}")
        previous: float | None = None
        for row in reader:
            at = _parse_at(row["timestamp"]).timestamp()
            if previous is not None and at < previous:
                raise ValueError(f"OI file is not ordered: {path}")
            previous = at
            if at < start:
                continue
            if at > end:
                break
            value = float(row["open_interest"])
            if value <= 0:
                raise ValueError(f"non-positive OI in {path}")
            yield at, value


def replay_positioning(
    signals: Sequence[Signal], *, symbols: tuple[str, ...], oi_dir: Path
) -> list[dict[str, Any]]:
    if not signals:
        return []
    start = min(item.touch_epoch for item in signals) - PRE_ROLL_SECONDS
    end = max(item.touch_epoch for item in signals)
    replay = CausalMayakReplay(symbols, exact_liquidations=False)
    replay.set_supported("linear", set(symbols))
    streams = [iter(_iter_oi(oi_dir / f"{symbol}.csv", start, end)) for symbol in symbols]
    heap: list[tuple[float, int, float]] = []
    for index, stream in enumerate(streams):
        try:
            at, value = next(stream)
        except StopIteration as exc:
            raise RuntimeError(f"no OI data for {symbols[index]}") from exc
        heapq.heappush(heap, (at, index, value))

    grouped: dict[float, list[Signal]] = defaultdict(list)
    for signal in signals:
        grouped[signal.touch_epoch].append(signal)
    output: list[dict[str, Any]] = []
    events = 0
    for target in sorted(grouped):
        while heap and heap[0][0] <= target:
            at, index, value = heapq.heappop(heap)
            replay.feed(
                MarketEvent(
                    event_at=at,
                    kind="TICKER",
                    symbol=symbols[index],
                    payload={"open_interest": value},
                )
            )
            events += 1
            try:
                next_at, next_value = next(streams[index])
            except StopIteration:
                continue
            heapq.heappush(heap, (next_at, index, next_value))
        snap = replay.snapshot(target)
        for signal in grouped[target]:
            coin = snap["coin_market_contexts"][signal.symbol]
            positioning = coin["payload"]["positioning"]
            output.append(
                {
                    "signal": asdict(signal),
                    "signal_key": signal.key,
                    "observed_at": snap["observed_at"],
                    "positioning": {field: positioning.get(field) for field in OI_FIELDS},
                    "source": "BYBIT_V5_OPEN_INTEREST_5M_EXACT_API_POINTS",
                    "events_processed": events,
                }
            )
    return sorted(output, key=lambda row: (row["signal"]["touch_epoch"], row["signal_key"]))


def _load_p34(root: Path, symbols: Sequence[str]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for symbol in symbols:
        matches = sorted(root.glob(f"{symbol}_20260518_20260816/p34/signals_open_interest.csv"))
        if len(matches) != 1:
            raise FileNotFoundError(f"expected one P34 file for {symbol}, found {len(matches)}")
        with matches[0].open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                touch = _parse_at(row["touch_at"]).isoformat()
                key = f"{symbol}|{row['direction']}|{touch}"
                if key in result:
                    raise ValueError(f"duplicate P34 key: {key}")
                result[key] = row
    return result


def p34_equivalence(
    rows: Sequence[dict[str, Any]], *, p34_root: Path, symbols: Sequence[str]
) -> dict[str, Any]:
    oracle = _load_p34(p34_root, symbols)
    mapping = {
        "open_interest_change_5m_pct": "oi_change_5m_pct",
        "open_interest_change_15m_pct": "oi_change_15m_pct",
        "open_interest_change_30m_pct": "oi_change_30m_pct",
        "open_interest_change_60m_pct": "oi_change_60m_pct",
    }
    stats: dict[str, dict[str, Any]] = {
        current: {"compared": 0, "missing": 0, "mismatch_gt_1e_9": 0, "max_abs_diff": 0.0}
        for current in mapping
    }
    missing_keys = 0
    for row in rows:
        old = oracle.get(row["signal_key"])
        if old is None:
            missing_keys += 1
            continue
        for current, legacy in mapping.items():
            new_value = row["positioning"].get(current)
            old_raw = old.get(legacy, "")
            if new_value is None or not old_raw:
                stats[current]["missing"] += 1
                continue
            diff = abs(float(new_value) - float(old_raw))
            stats[current]["compared"] += 1
            stats[current]["max_abs_diff"] = max(stats[current]["max_abs_diff"], diff)
            if diff > 1e-9:
                stats[current]["mismatch_gt_1e_9"] += 1
    return {
        "oracle": "P34_DIAGNOSTIC_ONLY_NOT_REPLAY_INPUT",
        "signal_rows": len(rows),
        "oracle_rows": len(oracle),
        "missing_signal_keys": missing_keys,
        "tolerance_report_only": 1e-9,
        "fields": stats,
    }


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
    output_dir: Path,
    source_commit: str,
    baseline: Path,
    oi_dir: Path,
    oi_manifest: Path,
    p34_root: Path | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    contexts = output_dir / "MAYAK_POSITIONING_CONTEXTS.jsonl"
    tmp = contexts.with_name(contexts.name + ".partial")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    os.replace(tmp, contexts)
    flat: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "signal_key": row["signal_key"],
            "symbol": row["signal"]["symbol"],
            "direction": row["signal"]["direction"],
            "touch_at": row["signal"]["touch_at"],
        }
        item.update({f"positioning_{key}": value for key, value in row["positioning"].items()})
        flat.append(item)
    csv_path = output_dir / "MAYAK_POSITIONING_FEATURES.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(flat)
    eq = (
        p34_equivalence(
            rows, p34_root=p34_root, symbols=sorted({r["signal"]["symbol"] for r in rows})
        )
        if p34_root
        else None
    )
    if eq is not None:
        _atomic_json(output_dir / "P34_EQUIVALENCE.json", eq)
    manifest = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "project_commit": source_commit,
        "baseline": str(baseline),
        "baseline_sha256": _sha256(baseline),
        "oi_dir": str(oi_dir),
        "oi_files": {p.name: _sha256(p) for p in sorted(oi_dir.glob("*.csv"))},
        "oi_source_manifest": str(oi_manifest),
        "oi_source_manifest_sha256": _sha256(oi_manifest),
        "signals": len(rows),
        "outcome_used_by_replay": False,
        "source_semantics": "Bybit V5 historical open-interest 5m exact API points",
        "p34_equivalence": eq,
        "trading_effect": "NONE",
    }
    _atomic_json(output_dir / "RUN_MANIFEST.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Causal MAYAK historical OI positioning replay")
    parser.add_argument("--baseline-csv", type=Path, required=True)
    parser.add_argument("--oi-dir", type=Path, required=True)
    parser.add_argument("--oi-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-signals", type=int, default=1063)
    parser.add_argument("--p34-root", type=Path)
    args = parser.parse_args(argv)
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    signals = load_baseline(args.baseline_csv)
    if len(signals) != args.expected_signals:
        raise ValueError(f"signal count mismatch: {len(signals)}")
    unknown = {item.symbol for item in signals}.difference(symbols)
    if unknown:
        raise ValueError(f"signals outside panel: {sorted(unknown)}")
    rows = replay_positioning(signals, symbols=symbols, oi_dir=args.oi_dir)
    manifest = write_outputs(
        rows,
        output_dir=args.output_dir,
        source_commit=args.source_commit,
        baseline=args.baseline_csv,
        oi_dir=args.oi_dir,
        oi_manifest=args.oi_manifest,
        p34_root=args.p34_root,
    )
    print(f"MAYAK_POSITIONING_REPLAY=PASS signals={manifest['signals']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
