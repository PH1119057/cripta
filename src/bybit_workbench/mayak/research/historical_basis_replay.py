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
from typing import Any, cast

from bybit_workbench.mayak.research.historical_signal_backfill import Signal, load_baseline
from bybit_workbench.mayak.research.objective_replay import CausalMayakReplay, MarketEvent

VERSION = "mayak-historical-basis-funding-replay-v1"
CANDLE_SECONDS = 300
BASIS_FIELDS = (
    "funding_rate",
    "funding_rate_change_from_previous",
    "mark_price",
    "index_price",
    "mark_index_premium_pct",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_sha256() -> str:
    return _sha256(Path(__file__).resolve())


def _parse_at(value: str) -> datetime:
    item = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if item.tzinfo is None:
        item = item.replace(tzinfo=UTC)
    return item.astimezone(UTC)


def _read_price_points(path: Path) -> dict[float, float]:
    result: dict[float, float] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "close_price"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"price file {path} missing columns: {sorted(missing)}")
        previous: float | None = None
        for row in reader:
            opened_at = _parse_at(row["timestamp"]).timestamp()
            if previous is not None and opened_at < previous:
                raise ValueError(f"price file is not ordered: {path}")
            previous = opened_at
            price = float(row["close_price"])
            if price <= 0:
                raise ValueError(f"non-positive price in {path}")
            result[opened_at] = price
    return result


def _iter_basis(
    mark_path: Path, index_path: Path, start: float, end: float
) -> Iterator[tuple[float, dict[str, float]]]:
    mark = _read_price_points(mark_path)
    index = _read_price_points(index_path)
    if mark.keys() != index.keys():
        raise ValueError(f"mark/index timestamp mismatch: {mark_path.name}")
    for opened_at in sorted(mark):
        available_at = opened_at + CANDLE_SECONDS
        if available_at < start:
            continue
        if available_at > end:
            break
        yield available_at, {"mark_price": mark[opened_at], "index_price": index[opened_at]}


def _iter_funding(path: Path, start: float, end: float) -> Iterator[tuple[float, dict[str, float]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        required = {"timestamp", "funding_rate"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"funding file {path} missing columns: {sorted(missing)}")
        previous: float | None = None
        for row in reader:
            at = _parse_at(row["timestamp"]).timestamp()
            if previous is not None and at < previous:
                raise ValueError(f"funding file is not ordered: {path}")
            previous = at
            if at < start:
                continue
            if at > end:
                break
            yield at, {"funding_rate": float(row["funding_rate"])}


def replay_basis(
    signals: Sequence[Signal],
    *,
    symbols: tuple[str, ...],
    mark_index_dir: Path,
    funding_dir: Path,
) -> list[dict[str, Any]]:
    if not signals:
        return []
    start = min(item.touch_epoch for item in signals) - 86400
    end = max(item.touch_epoch for item in signals)
    replay = CausalMayakReplay(symbols, exact_liquidations=False)
    replay.set_supported("linear", set(symbols))

    streams: list[Iterator[tuple[float, dict[str, float]]]] = []
    stream_symbols: list[str] = []
    for symbol in symbols:
        streams.append(
            iter(
                _iter_basis(
                    mark_index_dir / symbol / "mark_price_5m.csv",
                    mark_index_dir / symbol / "index_price_5m.csv",
                    start,
                    end,
                )
            )
        )
        stream_symbols.append(symbol)
        streams.append(iter(_iter_funding(funding_dir / f"{symbol}.csv", start, end)))
        stream_symbols.append(symbol)

    heap: list[tuple[float, int, dict[str, float]]] = []
    for index, stream in enumerate(streams):
        try:
            at, payload = next(stream)
        except StopIteration as exc:
            raise RuntimeError(f"no basis/funding data for {stream_symbols[index]}") from exc
        heapq.heappush(heap, (at, index, payload))

    grouped: dict[float, list[Signal]] = defaultdict(list)
    for signal in signals:
        grouped[signal.touch_epoch].append(signal)

    output: list[dict[str, Any]] = []
    events = 0
    for target in sorted(grouped):
        while heap and heap[0][0] <= target:
            event_at = heap[0][0]
            merged: dict[str, dict[str, float]] = {}
            consumed: list[int] = []
            while heap and heap[0][0] == event_at:
                at, index, payload = heapq.heappop(heap)
                symbol = stream_symbols[index]
                merged.setdefault(symbol, {}).update(payload)
                consumed.append(index)
                if at != event_at:
                    raise AssertionError("heap timestamp changed during merge")
            for symbol in sorted(merged):
                replay.feed(
                    MarketEvent(
                        event_at=event_at,
                        kind="TICKER",
                        symbol=symbol,
                        payload=merged[symbol],
                    )
                )
                events += 1
            for index in consumed:
                try:
                    next_at, next_payload = next(streams[index])
                except StopIteration:
                    continue
                heapq.heappush(heap, (next_at, index, next_payload))

        snap = replay.snapshot(target)
        for signal in grouped[target]:
            positioning = snap["coin_market_contexts"][signal.symbol]["payload"]["positioning"]
            output.append(
                {
                    "signal": asdict(signal),
                    "signal_key": signal.key,
                    "observed_at": snap["observed_at"],
                    "basis": {field: positioning.get(field) for field in BASIS_FIELDS},
                    "events_processed": events,
                }
            )
    return sorted(output, key=lambda row: (row["signal"]["touch_epoch"], row["signal_key"]))


def _atomic_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _verify_mark_index_manifest(manifest_path: Path, data_dir: Path) -> dict[str, Any]:
    manifest = cast(dict[str, Any], json.loads(manifest_path.read_text(encoding="utf-8")))
    for item in manifest.get("files", []):
        symbol = str(item["symbol"])
        for kind in ("mark", "index"):
            path = data_dir / symbol / f"{kind}_price_5m.csv"
            expected = str(item[f"{kind}_sha256"])
            if _sha256(path) != expected:
                raise ValueError(f"{kind} source hash mismatch for {symbol}")
    return manifest


def _verify_funding_manifest(manifest_path: Path, funding_dir: Path) -> dict[str, Any]:
    manifest = cast(dict[str, Any], json.loads(manifest_path.read_text(encoding="utf-8")))
    for item in manifest.get("files", []):
        path = funding_dir / f"{item['symbol']}.csv"
        if _sha256(path) != str(item["sha256"]):
            raise ValueError(f"funding source hash mismatch for {item['symbol']}")
    return manifest


def write_outputs(
    rows: Sequence[dict[str, Any]],
    *,
    output_dir: Path,
    source_commit: str,
    baseline: Path,
    mark_index_dir: Path,
    mark_index_manifest: Path,
    funding_dir: Path,
    funding_manifest: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    contexts = output_dir / "MAYAK_BASIS_CONTEXTS.jsonl"
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
        item.update({f"basis_{key}": value for key, value in row["basis"].items()})
        flat.append(item)
    csv_path = output_dir / "MAYAK_BASIS_FEATURES.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(flat)

    mark_manifest = _verify_mark_index_manifest(mark_index_manifest, mark_index_dir)
    funding_source_manifest = _verify_funding_manifest(funding_manifest, funding_dir)
    manifest = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "project_commit": source_commit,
        "replay_code_sha256": _code_sha256(),
        "baseline": str(baseline),
        "baseline_sha256": _sha256(baseline),
        "mark_index_manifest": str(mark_index_manifest),
        "mark_index_manifest_sha256": _sha256(mark_index_manifest),
        "mark_index_source_project_commit": mark_manifest.get("project_commit"),
        "funding_manifest": str(funding_manifest),
        "funding_manifest_sha256": _sha256(funding_manifest),
        "funding_source_project_commit": funding_source_manifest.get("project_commit"),
        "signals": len(rows),
        "availability_policy": {
            "mark_index_close": "candle_open_plus_5m",
            "funding": "fundingRateTimestamp",
        },
        "last_price_source": "NO_DATA_NOT_SUBSTITUTED_FROM_ENTRY",
        "outcome_used_by_replay": False,
        "trading_effect": "NONE",
    }
    _atomic_json(output_dir / "RUN_MANIFEST.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Causal MAYAK historical funding/mark-index replay"
    )
    parser.add_argument("--baseline-csv", type=Path, required=True)
    parser.add_argument("--mark-index-dir", type=Path, required=True)
    parser.add_argument("--mark-index-manifest", type=Path, required=True)
    parser.add_argument("--funding-dir", type=Path, required=True)
    parser.add_argument("--funding-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-signals", type=int, default=1063)
    args = parser.parse_args(argv)

    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    signals = load_baseline(args.baseline_csv)
    if len(signals) != args.expected_signals:
        raise ValueError(f"signal count mismatch: {len(signals)}")
    if {item.symbol for item in signals}.difference(symbols):
        raise ValueError("signals outside explicit panel")
    rows = replay_basis(
        signals,
        symbols=symbols,
        mark_index_dir=args.mark_index_dir,
        funding_dir=args.funding_dir,
    )
    manifest = write_outputs(
        rows,
        output_dir=args.output_dir,
        source_commit=args.source_commit,
        baseline=args.baseline_csv,
        mark_index_dir=args.mark_index_dir,
        mark_index_manifest=args.mark_index_manifest,
        funding_dir=args.funding_dir,
        funding_manifest=args.funding_manifest,
    )
    print(f"MAYAK_BASIS_REPLAY=PASS signals={manifest['signals']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
