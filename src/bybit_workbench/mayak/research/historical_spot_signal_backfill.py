from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bybit_workbench.mayak.research.historical_signal_backfill import Signal, load_baseline
from bybit_workbench.mayak.research.objective_replay import CausalMayakReplay

VERSION = "mayak-historical-spot-signal-backfill-v1"
ARCHIVE_BASE = "https://public.bybit.com/spot"
SPOT_SYMBOL_MAP = {"1000PEPEUSDT": "PEPEUSDT"}


def _spot_source_symbol(symbol: str) -> str:
    return SPOT_SYMBOL_MAP.get(symbol, symbol)


PRE_ROLL_SECONDS = 7200
WINDOW_LABELS = ("1m", "5m", "15m", "30m", "60m")
FLOW_FIELDS = (
    "buy_usd",
    "sell_usd",
    "net_usd",
    "turnover_usd",
    "net_share",
    "speed_usd_per_min",
    "acceleration_usd_per_min2",
    "large_buy_usd",
    "large_sell_usd",
    "large_trade_share",
    "prior_turnover_usd",
    "turnover_ratio_to_prior",
    "return_pct",
    "status",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _months(start_epoch: float, end_epoch: float) -> list[str]:
    current = datetime.fromtimestamp(start_epoch, UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    end = datetime.fromtimestamp(end_epoch, UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    result: list[str] = []
    while current <= end:
        result.append(current.strftime("%Y-%m"))
        current = current.replace(
            year=current.year + (current.month == 12),
            month=1 if current.month == 12 else current.month + 1,
        )
    return result


def _download(url: str, destination: Path, retries: int = 5) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "cripta-mayak-research/1"})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
                shutil.copyfileobj(response, out, length=1024 * 1024)
            os.replace(partial, destination)
            return
        except (OSError, urllib.error.URLError) as exc:
            last = exc
            partial.unlink(missing_ok=True)
            time.sleep(min(8.0, 0.5 * (2**attempt)))
    raise RuntimeError(f"download failed: {url}") from last


def download_sources(
    signals: Sequence[Signal], symbols: tuple[str, ...], cache_dir: Path
) -> dict[str, Any]:
    start = min(item.touch_epoch for item in signals) - PRE_ROLL_SECONDS
    end = max(item.touch_epoch for item in signals)
    months = _months(start, end)
    files: list[dict[str, Any]] = []
    for symbol in symbols:
        source_symbol = _spot_source_symbol(symbol)
        for month in months:
            name = f"{source_symbol}-{month}.csv.gz"
            path = cache_dir / symbol / name
            url = f"{ARCHIVE_BASE}/{source_symbol}/{name}"
            if not path.exists():
                _download(url, path)
            # Fail closed on corrupt/non-gzip sources.
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                header = handle.readline().strip().split(",")
            if header[:5] != ["id", "timestamp", "price", "volume", "side"]:
                raise ValueError(f"unexpected spot archive schema: {path} {header}")
            files.append(
                {
                    "symbol": symbol,
                    "month": month,
                    "path": str(path),
                    "url": url,
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return {
        "version": VERSION,
        "source": "BYBIT_PUBLIC_SPOT_MONTHLY_ARCHIVE",
        "archive_base": ARCHIVE_BASE,
        "period_start": datetime.fromtimestamp(start, UTC).isoformat(),
        "period_end": datetime.fromtimestamp(end, UTC).isoformat(),
        "months": months,
        "files": files,
    }


def _iter_file(path: Path, start: float, end: float) -> Iterator[tuple[float, str, float, float]]:
    previous: float | None = None
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "price", "volume", "side"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"spot file missing columns {sorted(missing)}: {path}")
        for row in reader:
            event_at = float(row["timestamp"]) / 1000.0
            if previous is not None and event_at < previous:
                raise ValueError(f"spot archive is not ordered: {path}")
            previous = event_at
            if event_at < start:
                continue
            if event_at > end:
                break
            price = float(row["price"])
            size = float(row["volume"])
            side = row["side"].strip().lower()
            if side not in {"buy", "sell"} or price <= 0 or size <= 0:
                raise ValueError(f"invalid spot trade in {path}")
            yield event_at, side, price, size


def replay_symbol(
    symbol: str, signals: Sequence[Signal], source_files: Sequence[Path]
) -> tuple[list[dict[str, Any]], int]:
    ordered = sorted(signals, key=lambda item: item.touch_epoch)
    start = ordered[0].touch_epoch - PRE_ROLL_SECONDS
    end = ordered[-1].touch_epoch
    replay = CausalMayakReplay((symbol,), exact_liquidations=False)
    replay.set_supported("spot", {symbol})
    index = 0
    events = 0
    output: list[dict[str, Any]] = []

    def capture(signal: Signal) -> None:
        snap = replay.snapshot(signal.touch_epoch)
        flow = snap["coin_market_contexts"][symbol]["payload"]["money"]["spot"]
        output.append(
            {
                "signal": asdict(signal),
                "signal_key": signal.key,
                "observed_at": snap["observed_at"],
                "spot": {
                    label: {field: (flow.get(label) or {}).get(field) for field in FLOW_FIELDS}
                    for label in WINDOW_LABELS
                },
                "events_processed": events,
            }
        )

    for path in source_files:
        for event_at, side, price, size in _iter_file(path, start, end):
            while index < len(ordered) and ordered[index].touch_epoch < event_at:
                capture(ordered[index])
                index += 1
            if index >= len(ordered):
                break
            replay.feed_trade(event_at, symbol, "spot", side, price, size)
            events += 1
        if index >= len(ordered):
            break
    while index < len(ordered):
        capture(ordered[index])
        index += 1
    return output, events


def _flatten(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "signal_key": row["signal_key"],
            "symbol": row["signal"]["symbol"],
            "direction": row["signal"]["direction"],
            "touch_at": row["signal"]["touch_at"],
        }
        for label in WINDOW_LABELS:
            for field, value in row["spot"][label].items():
                item[f"spot_{label}_{field}"] = value
        result.append(item)
    return result


def _atomic_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exact causal MAYAK historical Spot replay")
    parser.add_argument("--baseline-csv", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
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
        raise ValueError("signals outside explicit Spot panel")
    source_manifest = download_sources(signals, symbols, args.cache_dir)
    source_manifest["project_commit"] = args.source_commit
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source_manifest_path = args.output_dir / "SOURCE_MANIFEST.json"
    _atomic_json(source_manifest_path, source_manifest)
    by_symbol = defaultdict(list)
    for signal in signals:
        by_symbol[signal.symbol].append(signal)
    by_path: dict[str, list[Path]] = defaultdict(list)
    for item in source_manifest["files"]:
        by_path[str(item["symbol"])].append(Path(str(item["path"])))
    all_rows: list[dict[str, Any]] = []
    event_counts: dict[str, int] = {}
    for symbol in symbols:
        rows, events = replay_symbol(symbol, by_symbol[symbol], sorted(by_path[symbol]))
        all_rows.extend(rows)
        event_counts[symbol] = events
        print(
            f"SPOT_REPLAY symbol={symbol} signals={len(rows)} events={events} stage=DONE",
            flush=True,
        )
    all_rows.sort(key=lambda row: (row["signal"]["touch_epoch"], row["signal_key"]))
    if len(all_rows) != args.expected_signals:
        raise ValueError(f"Spot replay output mismatch: {len(all_rows)}")
    contexts = args.output_dir / "MAYAK_SPOT_CONTEXTS.jsonl"
    with contexts.open("w", encoding="utf-8") as handle:
        for row in all_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    flat = _flatten(all_rows)
    features = args.output_dir / "MAYAK_SPOT_FEATURES.csv"
    with features.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(flat)
    manifest = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "project_commit": args.source_commit,
        "replay_code_sha256": _sha256(Path(__file__).resolve()),
        "baseline": str(args.baseline_csv),
        "baseline_sha256": _sha256(args.baseline_csv),
        "source_manifest": str(source_manifest_path),
        "source_manifest_sha256": _sha256(source_manifest_path),
        "signals": len(all_rows),
        "symbols": list(symbols),
        "event_counts": event_counts,
        "outcome_used_by_replay": False,
        "trading_effect": "NONE",
    }
    _atomic_json(args.output_dir / "RUN_MANIFEST.json", manifest)
    print(
        f"MAYAK_SPOT_REPLAY=PASS signals={len(all_rows)} events={sum(event_counts.values())}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
