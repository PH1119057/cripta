from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from bybit_workbench.domain.models import Candle
from bybit_workbench.research.entry_one_minute_displacement_p53 import (
    _aggregate_trade_archive,
    _read_candle_cache,
    _write_candle_cache,
)
from bybit_workbench.research.mtf_entry import _write_candles
from bybit_workbench.research.mtf_entry_v3 import (
    EntryResearchV3Config,
    _write_flow,
    aggregate_public_trade_archives,
)

DATASET_ENGINE = "entry-dataset-from-local-public-trades-v1"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_day(path: Path, symbol: str) -> date:
    stem = path.name.removesuffix(".csv.gz")
    prefix = symbol.upper()
    if not stem.upper().startswith(prefix):
        raise ValueError(f"archive name does not start with {symbol}: {path.name}")
    return date.fromisoformat(stem[len(prefix) :])


def _load_or_build_day(
    *,
    archive: Path,
    cache_root: Path,
    symbol: str,
    day: date,
    seed: Decimal | None,
) -> tuple[tuple[Candle, ...], bool]:
    cache = cache_root / symbol / f"{day.isoformat()}.csv.gz"
    meta = cache.with_suffix(cache.suffix + ".json")
    source_sha256 = _sha256(archive)
    seed_text = None if seed is None else str(seed)
    if cache.is_file() and meta.is_file():
        cached_meta = json.loads(meta.read_text(encoding="utf-8-sig"))
        if (
            cached_meta.get("engine") == DATASET_ENGINE
            and cached_meta.get("source_sha256") == source_sha256
            and cached_meta.get("seed_price") == seed_text
            and cached_meta.get("cache_sha256") == _sha256(cache)
        ):
            candles = _read_candle_cache(cache, symbol=symbol)
            if len(candles) == 1440:
                return candles, True
    candles = _aggregate_trade_archive(
        archive,
        symbol=symbol,
        day=day,
        seed_price=seed,
    )
    _write_candle_cache(cache, candles)
    meta.write_text(
        json.dumps(
            {
                "engine": DATASET_ENGINE,
                "symbol": symbol,
                "day": day.isoformat(),
                "source_sha256": source_sha256,
                "seed_price": seed_text,
                "cache_sha256": _sha256(cache),
                "rows": len(candles),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return candles, False


def discover_archives(raw_symbol_dir: Path, symbol: str) -> tuple[tuple[date, Path], ...]:
    archive_dir = raw_symbol_dir / "public_trades"
    items = tuple(
        sorted(
            ((_archive_day(path, symbol), path) for path in archive_dir.glob("*.csv.gz")),
            key=lambda item: item[0],
        )
    )
    if not items:
        raise FileNotFoundError(f"no public trade archives: {archive_dir}")
    for previous, current in zip(items, items[1:], strict=False):
        if current[0] != previous[0] + timedelta(days=1):
            raise ValueError(f"non-contiguous trade days: {previous[0]} -> {current[0]}")
    return items


def aggregate_candles(
    one_minute: tuple[Candle, ...], *, timeframe_minutes: int
) -> tuple[Candle, ...]:
    if timeframe_minutes <= 0:
        raise ValueError("timeframe_minutes must be positive")
    if not one_minute:
        return ()
    result: list[Candle] = []
    for offset in range(0, len(one_minute), timeframe_minutes):
        group = one_minute[offset : offset + timeframe_minutes]
        if len(group) != timeframe_minutes:
            break
        expected = group[0].opened_at
        for index, candle in enumerate(group):
            if candle.opened_at != expected + timedelta(minutes=index):
                raise ValueError("one-minute candle cadence is not contiguous")
        result.append(
            Candle(
                symbol=group[0].symbol,
                timeframe=str(timeframe_minutes),
                opened_at=group[0].opened_at,
                closed_at=group[-1].closed_at,
                open=group[0].open,
                high=max(item.high for item in group),
                low=min(item.low for item in group),
                close=group[-1].close,
                volume=sum((item.volume for item in group), start=group[0].volume * 0),
                is_closed=True,
            )
        )
    return tuple(result)


def materialize(
    *,
    raw_root: Path,
    output_root: Path,
    symbol: str,
    max_days: int | None = None,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    symbol = symbol.upper()
    archives = discover_archives(raw_root / symbol, symbol)
    if max_days is not None:
        if max_days <= 0:
            raise ValueError("max_days must be positive")
        archives = archives[:max_days]
    output = output_root / symbol
    output.mkdir(parents=True, exist_ok=True)
    day_cache = cache_root or output_root.parent / "entry_day_cache"
    manifest_path = output / "dataset_manifest.json"
    complete_path = output / "DATASET_COMPLETE.json"
    if manifest_path.is_file() and complete_path.is_file():
        old_manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        old_complete = json.loads(complete_path.read_text(encoding="utf-8-sig"))
        old_sources = old_manifest.get("sources") or []
        sources_unchanged = len(old_sources) == len(archives) and all(
            Path(str(saved.get("path"))).is_file()
            and Path(str(saved.get("path"))).stat().st_size == int(saved.get("bytes", -1))
            and Path(str(saved.get("path"))).stat().st_mtime_ns
            == int(saved.get("mtime_ns", -1))
            for saved in old_sources
        )
        if (
            old_manifest.get("engine") == DATASET_ENGINE
            and old_manifest.get("symbol") == symbol
            and int(old_manifest.get("archive_days", -1)) == len(archives)
            and old_complete.get("manifest_sha256") == _sha256(manifest_path)
            and sources_unchanged
        ):
            return {
                "engine": DATASET_ENGINE,
                "symbol": symbol,
                "manifest_sha256": old_complete["manifest_sha256"],
                "status": "reused",
            }

    days: list[tuple[Candle, ...]] = []
    seed = None
    reused_days = 0
    for day, archive in archives:
        candles, reused = _load_or_build_day(
            archive=archive,
            cache_root=day_cache,
            symbol=symbol,
            day=day,
            seed=seed,
        )
        days.append(candles)
        seed = candles[-1].close
        reused_days += int(reused)
    one = tuple(candle for day in days for candle in day)
    five = aggregate_candles(one, timeframe_minutes=5)
    fifteen = aggregate_candles(one, timeframe_minutes=15)
    hourly = aggregate_candles(one, timeframe_minutes=60)
    start = one[0].opened_at
    end = one[-1].closed_at
    paths = tuple(path for _, path in archives)
    flow = aggregate_public_trade_archives(paths, start_at=start, end_at=end)

    _write_candles(output / "trade_5m.csv", five)
    _write_candles(output / "trade_15m.csv", fifteen)
    _write_candles(output / "trade_60m.csv", hourly)
    _write_flow(output / "flow_1m.csv", flow)
    # The strategy contract stays at its canonical 90-day horizon.  A smoke dataset
    # may materialize fewer days, but must not mutate Entry parameters to do so.
    config = EntryResearchV3Config(symbol=symbol, days=90, warmup_days=0)
    manifest = {
        "engine": DATASET_ENGINE,
        "symbol": symbol,
        "evaluation_start": start.isoformat(),
        "evaluation_end": end.isoformat(),
        "archive_days": len(archives),
        "reused_day_caches": reused_days,
        "first_archive_day": archives[0][0].isoformat(),
        "last_archive_day": archives[-1][0].isoformat(),
        "config": _jsonable(asdict(config)),
        "rows": {
            "trade_5m": len(five),
            "trade_15m": len(fifteen),
            "trade_60m": len(hourly),
            "flow_1m": len(flow),
        },
        "sources": [
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
                "sha256": _sha256(path),
            }
            for path in paths
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    complete = {
        "engine": DATASET_ENGINE,
        "symbol": symbol,
        "manifest_sha256": _sha256(manifest_path),
        "completed_at": datetime.now(UTC).isoformat(),
    }
    complete_path.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return complete


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сборка Entry-данных из локальных архивов")
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--max-days", type=int)
    parser.add_argument("--cache-root", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _args()
    result = materialize(
        raw_root=args.raw_root.resolve(),
        output_root=args.output_root.resolve(),
        symbol=args.symbol,
        max_days=args.max_days,
        cache_root=args.cache_root.resolve() if args.cache_root is not None else None,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
