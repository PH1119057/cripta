from __future__ import annotations

import argparse
import bisect
import csv
import json
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from bybit_workbench.domain.models import Candle
from bybit_workbench.research.flow_reversal_v1 import TradeDay, _load_trade_day
from bybit_workbench.research.mtf_entry import Direction
from bybit_workbench.research.mtf_entry_v3 import (
    EntryResearchV3Config,
    ZoneV3,
    _precompute_post_shock_zones,
    _read_candles,
)
from bybit_workbench.research.universal_entry_pool import (
    _config_from_manifest,
    _evaluation_bounds,
)

ENGINE_ID = "zone-episode-entry-depth-v1"
DEPTHS = (Decimal("0"), Decimal("0.25"), Decimal("0.50"), Decimal("0.75"), Decimal("1"))
Outcome = Literal["target", "stop", "data_end", "unfilled"]


@dataclass(frozen=True, slots=True)
class Episode:
    symbol: str
    direction: Direction
    started_at: datetime
    ended_at: datetime
    overlap_low: Decimal
    overlap_high: Decimal


@dataclass(frozen=True, slots=True)
class Result:
    symbol: str
    direction: Direction
    episode_started_at: str
    episode_ended_at: str
    depth: str
    overlap_low: str
    overlap_high: str
    entry_price: str
    fill_at: str
    outcome: Outcome
    exit_at: str
    duration_to_fill_seconds: float | None
    duration_fill_to_exit_seconds: float | None


class DayCache:
    def __init__(self, max_days: int = 10) -> None:
        self.max_days = max_days
        self.items: OrderedDict[Path, TradeDay] = OrderedDict()

    def get(self, path: Path) -> TradeDay:
        tape = self.items.get(path)
        if tape is not None:
            self.items.move_to_end(path)
            return tape
        tape = _load_trade_day(path)
        self.items[path] = tape
        while len(self.items) > self.max_days:
            self.items.popitem(last=False)
        return tape


def _manifest(dataset_root: Path, symbol: str) -> tuple[Path, dict[str, Any]]:
    direct = dataset_root / symbol
    path = direct / "dataset_manifest.json"
    if path.is_file():
        return direct, dict(json.loads(path.read_text(encoding="utf-8-sig")))
    matches = sorted(dataset_root.glob(f"{symbol}_*/p30/dataset/dataset_manifest.json"))
    if len(matches) != 1:
        raise FileNotFoundError(f"one dataset manifest required for {symbol}: {matches}")
    return matches[0].parent, dict(json.loads(matches[0].read_text(encoding="utf-8-sig")))


def _zones(
    candles: tuple[Candle, ...], timeframe: str, config: EntryResearchV3Config
) -> tuple[ZoneV3 | None, ...]:
    minutes = 5 if timeframe == "5" else 15
    return _precompute_post_shock_zones(
        candles,
        timeframe=timeframe,
        lookback=config.five_minute_lookback
        if timeframe == "5"
        else config.fifteen_minute_lookback,
        atr_period=config.atr_period,
        width_atr=config.zone_half_width_atr,
        shock_atr_period=config.shock_atr_period,
        shock_atr_multiple=config.shock_atr_multiple,
        minimum_regime_bars=max(1, config.embargo_minutes_after_shock // minutes),
    )


def _intersection(
    left_low: Decimal, left_high: Decimal, right_low: Decimal, right_high: Decimal
) -> tuple[Decimal, Decimal] | None:
    low, high = max(left_low, right_low), min(left_high, right_high)
    return (low, high) if low <= high else None


def _states(
    five: ZoneV3 | None,
    fifteen: ZoneV3 | None,
) -> dict[Direction, tuple[Decimal, Decimal]]:
    if five is None or fifteen is None:
        return {}
    support = _intersection(
        five.support_bottom,
        five.support_top,
        fifteen.support_bottom,
        fifteen.support_top,
    )
    resistance = _intersection(
        five.resistance_bottom,
        five.resistance_top,
        fifteen.resistance_bottom,
        fifteen.resistance_top,
    )
    result: dict[Direction, tuple[Decimal, Decimal]] = {}
    if support is not None:
        result["Long"] = support
    if resistance is not None:
        result["Short"] = resistance
    return result


def build_episodes(
    symbol: str,
    five: tuple[Candle, ...],
    fifteen: tuple[Candle, ...],
    config: EntryResearchV3Config,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> tuple[Episode, ...]:
    five = tuple(sorted(five, key=lambda row: row.opened_at))
    fifteen = tuple(sorted(fifteen, key=lambda row: row.opened_at))
    five_zones, fifteen_zones = _zones(five, "5", config), _zones(fifteen, "15", config)
    fifteen_end = 0
    active: dict[Direction, Episode] = {}
    output: list[Episode] = []
    for index, bar in enumerate(five):
        if bar.opened_at < evaluation_start or bar.opened_at >= evaluation_end:
            continue
        while fifteen_end < len(fifteen) and fifteen[fifteen_end].closed_at <= bar.opened_at:
            fifteen_end += 1
        fifteen_zone = fifteen_zones[fifteen_end] if fifteen_end < len(fifteen_zones) else None
        current = _states(five_zones[index], fifteen_zone)
        for direction in ("Long", "Short"):
            if direction in active and direction not in current:
                episode = active.pop(direction)
                output.append(
                    Episode(
                        episode.symbol,
                        episode.direction,
                        episode.started_at,
                        bar.opened_at,
                        episode.overlap_low,
                        episode.overlap_high,
                    )
                )
            if direction not in active and direction in current:
                low, high = current[direction]
                active[direction] = Episode(
                    symbol,
                    direction,
                    bar.opened_at,
                    evaluation_end,
                    low,
                    high,
                )
    output.extend(active.values())
    return tuple(sorted(output, key=lambda row: (row.started_at, row.direction)))


def entry_price(episode: Episode, depth: Decimal) -> Decimal:
    width = episode.overlap_high - episode.overlap_low
    if episode.direction == "Long":
        return episode.overlap_high - depth * width
    return episode.overlap_low + depth * width


def _days(start: datetime, end: datetime) -> tuple[str, ...]:
    if end <= start:
        return ()
    current = start.date()
    last = (end - timedelta(microseconds=1)).date()
    result: list[str] = []
    while current <= last:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(result)


def _archive_map(raw_root: Path, symbol: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in (raw_root / symbol / "public_trades").glob(f"{symbol}*.csv.gz"):
        result[path.name.removeprefix(symbol).removesuffix(".csv.gz")] = path
    if not result:
        raise FileNotFoundError(f"public trades missing for {symbol}")
    return result


def _iter_window(
    archives: dict[str, Path],
    cache: DayCache,
    start: datetime,
    end: datetime,
) -> Iterator[tuple[float, float]]:
    start_ts, end_ts = start.timestamp(), end.timestamp()
    for day in _days(start, end):
        path = archives.get(day)
        if path is None:
            raise FileNotFoundError(f"public-trade archive missing for {day}")
        tape = cache.get(path)
        left = bisect.bisect_left(tape.timestamps, start_ts)
        right = bisect.bisect_left(tape.timestamps, end_ts)
        for index in range(left, right):
            yield tape.timestamps[index], tape.prices[index]


def replay(
    episode: Episode,
    depth: Decimal,
    archives: dict[str, Path],
    cache: DayCache,
    evaluation_end: datetime,
) -> Result:
    level = entry_price(episode, depth)
    fill_at: datetime | None = None
    for timestamp, price in _iter_window(archives, cache, episode.started_at, episode.ended_at):
        if (episode.direction == "Long" and price <= float(level)) or (
            episode.direction == "Short" and price >= float(level)
        ):
            fill_at = datetime.fromtimestamp(timestamp, UTC)
            break
    if fill_at is None:
        return Result(
            episode.symbol,
            episode.direction,
            episode.started_at.isoformat(),
            episode.ended_at.isoformat(),
            str(depth),
            str(episode.overlap_low),
            str(episode.overlap_high),
            str(level),
            "",
            "unfilled",
            "",
            None,
            None,
        )
    outcome: Outcome = "data_end"
    exit_at: datetime | None = None
    for timestamp, price in _iter_window(archives, cache, fill_at, evaluation_end):
        raw = (price / float(level) - 1.0) * 100.0
        move = raw if episode.direction == "Long" else -raw
        if move >= 1.1:
            outcome, exit_at = "target", datetime.fromtimestamp(timestamp, UTC)
            break
        if move <= -1.0:
            outcome, exit_at = "stop", datetime.fromtimestamp(timestamp, UTC)
            break
    return Result(
        episode.symbol,
        episode.direction,
        episode.started_at.isoformat(),
        episode.ended_at.isoformat(),
        str(depth),
        str(episode.overlap_low),
        str(episode.overlap_high),
        str(level),
        fill_at.isoformat(),
        outcome,
        "" if exit_at is None else exit_at.isoformat(),
        (fill_at - episode.started_at).total_seconds(),
        None if exit_at is None else (exit_at - fill_at).total_seconds(),
    )


def _metrics(rows: list[Result]) -> dict[str, Any]:
    filled = [row for row in rows if row.outcome != "unfilled"]
    targets = sum(row.outcome == "target" for row in filled)
    stops = sum(row.outcome == "stop" for row in filled)
    resolved = targets + stops
    return {
        "episodes": len(rows),
        "filled": len(filled),
        "fill_rate_pct": 100 * len(filled) / len(rows) if rows else None,
        "targets": targets,
        "stops": stops,
        "data_end": sum(row.outcome == "data_end" for row in filled),
        "target_rate_pct": 100 * targets / resolved if resolved else None,
        "gross_ev_pct_per_resolved": (1.1 * targets - stops) / resolved if resolved else None,
        "gross_ev_pct_per_episode": (1.1 * targets - stops) / len(rows) if rows else None,
    }


def run_symbol(
    dataset_root: Path, raw_root: Path, output_root: Path, symbol: str
) -> dict[str, Any]:
    dataset, manifest = _manifest(dataset_root, symbol)
    config = _config_from_manifest(manifest, symbol)
    evaluation_start, evaluation_end = _evaluation_bounds(manifest)
    five = _read_candles(dataset / "trade_5m.csv", symbol=symbol, timeframe="5")
    fifteen = _read_candles(dataset / "trade_15m.csv", symbol=symbol, timeframe="15")
    episodes = build_episodes(symbol, five, fifteen, config, evaluation_start, evaluation_end)
    archives, cache = _archive_map(raw_root, symbol), DayCache()
    rows = [
        replay(episode, depth, archives, cache, evaluation_end)
        for episode in episodes
        for depth in DEPTHS
    ]
    destination = output_root / symbol
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=[field.name for field in __import__("dataclasses").fields(Result)]
        )
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    split_at = evaluation_start + (evaluation_end - evaluation_start) * 2 / 3
    summary: dict[str, Any] = {
        "engine": ENGINE_ID,
        "symbol": symbol,
        "contract": {
            "signal": "one continuous exact 15m/5m zone-overlap episode",
            "depths": [str(value) for value in DEPTHS],
            "pending_cancel": "episode end",
            "target_pct": 1.1,
            "stop_pct": 1.0,
            "extra_filters": "NONE",
        },
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end": evaluation_end.isoformat(),
        "split_at": split_at.isoformat(),
        "episodes": len(episodes),
        "depths": [],
    }
    for depth in DEPTHS:
        selected = [row for row in rows if row.depth == str(depth)]
        discovery = [
            row for row in selected if datetime.fromisoformat(row.episode_started_at) < split_at
        ]
        validation = [
            row for row in selected if datetime.fromisoformat(row.episode_started_at) >= split_at
        ]
        summary["depths"].append(
            {
                "depth": str(depth),
                "all": _metrics(selected),
                "discovery": _metrics(discovery),
                "validation": _metrics(validation),
            }
        )
    (destination / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "symbol": symbol,
        "episodes": len(episodes),
        "rows": len(rows),
        "output": str(destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="One signal per exact 15m/5m overlap episode")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run_symbol(args.dataset_root, args.raw_root, args.output_root, args.symbol.upper()),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
