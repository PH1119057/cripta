from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Literal

ENGINE = "independent-entry-search-v1"
TARGET_PCT = 1.10
STOP_PCT = 1.00
Direction = Literal["Long", "Short"]


@dataclass(frozen=True, slots=True)
class Bar:
    at: float
    open: float
    high: float
    low: float
    close: float
    volume_usd: float
    net_buy_usd: float


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    family: str
    variant: str
    direction: Direction
    signal_at: str
    entry_at: str
    entry_price: float
    outcome: str
    exit_at: str


class PriceIndex:
    def __init__(self, bars: list[Bar]) -> None:
        size = 1
        while size < len(bars):
            size *= 2
        self.size = size
        self.highs = [-math.inf] * (2 * size)
        self.lows = [math.inf] * (2 * size)
        for index, bar in enumerate(bars):
            self.highs[size + index] = bar.high
            self.lows[size + index] = bar.low
        for index in range(size - 1, 0, -1):
            self.highs[index] = max(self.highs[2 * index], self.highs[2 * index + 1])
            self.lows[index] = min(self.lows[2 * index], self.lows[2 * index + 1])

    def first_ge(self, start: int, threshold: float) -> int | None:
        return self._first(start, threshold, 1, 0, self.size, True)

    def first_le(self, start: int, threshold: float) -> int | None:
        return self._first(start, threshold, 1, 0, self.size, False)

    def _first(
        self,
        start: int,
        threshold: float,
        node: int,
        left: int,
        right: int,
        high: bool,
    ) -> int | None:
        if right <= start:
            return None
        value = self.highs[node] if high else self.lows[node]
        if (high and value < threshold) or (not high and value > threshold):
            return None
        if right - left == 1:
            return left
        middle = (left + right) // 2
        found = self._first(start, threshold, node * 2, left, middle, high)
        if found is not None:
            return found
        return self._first(start, threshold, node * 2 + 1, middle, right, high)


def _archives(raw_root: Path, symbol: str) -> list[Path]:
    paths = sorted(
        (raw_root / symbol / "public_trades").glob(f"{symbol}*.csv.gz"),
        key=lambda path: path.name,
    )
    if not paths:
        raise FileNotFoundError(f"no public trades for {symbol}")
    return paths


def build_bars(raw_root: Path, symbol: str) -> list[Bar]:
    bars: list[Bar] = []
    bucket: int | None = None
    open_price = high = low = close = volume = net = 0.0
    for path in _archives(raw_root, symbol):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                timestamp = float(row["timestamp"])
                current = int(timestamp // 300) * 300
                price = float(row["price"])
                size = float(row["size"])
                notional = price * size
                if bucket != current:
                    if bucket is not None:
                        bars.append(Bar(bucket, open_price, high, low, close, volume, net))
                    bucket = current
                    open_price = high = low = close = price
                    volume = net = 0.0
                high = max(high, price)
                low = min(low, price)
                close = price
                volume += notional
                net += notional if row["side"] == "Buy" else -notional
    if bucket is not None:
        bars.append(Bar(bucket, open_price, high, low, close, volume, net))
    return bars


def _z(value: float, history: list[float]) -> float:
    deviation = pstdev(history)
    return 0.0 if deviation == 0 else (value - fmean(history)) / deviation


def _direction(value: float) -> Direction:
    return "Long" if value > 0 else "Short"


def _candidate_states(bars: list[Bar], index: int) -> dict[tuple[str, str], Direction]:
    if index < 288:
        return {}
    bar = bars[index]
    previous = bars[index - 288 : index]
    returns = [math.log(previous[i].close / previous[i - 1].close) for i in range(1, len(previous))]
    current_return = math.log(bar.close / bars[index - 1].close)
    return_z = _z(current_return, returns)
    volumes = [item.volume_usd for item in previous]
    volume_z = _z(bar.volume_usd, volumes)
    flows = [item.net_buy_usd for item in previous]
    flow_z = _z(bar.net_buy_usd, flows)
    imbalance = abs(bar.net_buy_usd) / bar.volume_usd if bar.volume_usd else 0.0
    result: dict[tuple[str, str], Direction] = {}

    for threshold in (1.5, 2.0, 2.5):
        if abs(return_z) >= threshold:
            result[("price_impulse", f"z{threshold:g}")] = _direction(return_z)

    for threshold, minimum_imbalance in ((1.5, 0.20), (2.0, 0.25), (2.5, 0.30)):
        if abs(flow_z) >= threshold and imbalance >= minimum_imbalance:
            variant = f"z{threshold:g}_imb{minimum_imbalance:g}"
            result[("money_flow", variant)] = _direction(bar.net_buy_usd)

    for lookback, minimum_volume_z in ((12, 1.0), (36, 1.0), (72, 1.5)):
        window = bars[index - lookback : index]
        if volume_z >= minimum_volume_z and bar.close > max(item.high for item in window):
            result[("range_breakout", f"n{lookback}_vz{minimum_volume_z:g}")] = "Long"
        elif volume_z >= minimum_volume_z and bar.close < min(item.low for item in window):
            result[("range_breakout", f"n{lookback}_vz{minimum_volume_z:g}")] = "Short"

    typical = [(item.high + item.low + item.close) / 3 for item in previous[-72:]]
    weights = [item.volume_usd for item in previous[-72:]]
    total_weight = sum(weights)
    vwap = (
        sum(value * weight for value, weight in zip(typical, weights, strict=True)) / total_weight
    )
    distances = [(value / vwap - 1.0) for value in typical]
    distance = bar.close / vwap - 1.0
    distance_z = _z(distance, distances)
    for threshold in (2.0, 2.5, 3.0):
        if abs(distance_z) >= threshold:
            result[("vwap_reversion", f"z{threshold:g}")] = "Short" if distance_z > 0 else "Long"
    return result


def generate_signals(symbol: str, bars: list[Bar]) -> list[Signal]:
    active: dict[tuple[str, str], Direction] = {}
    pending: list[tuple[int, str, str, Direction]] = []
    for index in range(288, len(bars) - 1):
        states = _candidate_states(bars, index)
        for key, direction in states.items():
            if active.get(key) != direction:
                pending.append((index, key[0], key[1], direction))
        active = states

    output: list[Signal] = []
    prices = PriceIndex(bars)
    for index, family, variant, direction in pending:
        entry_index = index + 1
        entry = bars[entry_index].open
        target = (
            entry * (1 + TARGET_PCT / 100)
            if direction == "Long"
            else entry * (1 - TARGET_PCT / 100)
        )
        stop = entry * (1 - STOP_PCT / 100) if direction == "Long" else entry * (1 + STOP_PCT / 100)
        if direction == "Long":
            target_index = prices.first_ge(entry_index, target)
            stop_index = prices.first_le(entry_index, stop)
        else:
            target_index = prices.first_le(entry_index, target)
            stop_index = prices.first_ge(entry_index, stop)
        outcome, exit_at = "data_end", ""
        hits = [hit for hit in (target_index, stop_index) if hit is not None]
        if hits:
            first = min(hits)
            if target_index == stop_index:
                outcome = "stop_ambiguous"
            else:
                outcome = "target" if first == target_index else "stop"
            exit_at = datetime.fromtimestamp(bars[first].at, UTC).isoformat()
        output.append(
            Signal(
                symbol,
                family,
                variant,
                direction,
                datetime.fromtimestamp(bars[index].at, UTC).isoformat(),
                datetime.fromtimestamp(bars[entry_index].at, UTC).isoformat(),
                entry,
                outcome,
                exit_at,
            )
        )
    return output


def _metrics(rows: list[Signal]) -> dict[str, Any]:
    targets = sum(row.outcome == "target" for row in rows)
    stops = sum(row.outcome in {"stop", "stop_ambiguous"} for row in rows)
    resolved = targets + stops
    win = targets / resolved if resolved else 0.0
    return {
        "signals": len(rows),
        "resolved": resolved,
        "targets": targets,
        "stops": stops,
        "win_pct": round(100 * win, 2) if resolved else None,
        "gross_ev_pct": round(TARGET_PCT * win - STOP_PCT * (1 - win), 4) if resolved else None,
    }


def summarize(symbol: str, bars: list[Bar], signals: list[Signal]) -> dict[str, Any]:
    start, end = bars[0].at, bars[-1].at
    train_end = start + 0.60 * (end - start)
    validation_end = start + 0.80 * (end - start)
    groups: dict[tuple[str, str, Direction], list[Signal]] = {}
    for row in signals:
        groups.setdefault((row.family, row.variant, row.direction), []).append(row)
    candidates = []
    for (family, variant, direction), rows in sorted(groups.items()):
        train = [
            row for row in rows if datetime.fromisoformat(row.signal_at).timestamp() < train_end
        ]
        validation = [
            row
            for row in rows
            if train_end <= datetime.fromisoformat(row.signal_at).timestamp() < validation_end
        ]
        test = [
            row
            for row in rows
            if datetime.fromisoformat(row.signal_at).timestamp() >= validation_end
        ]
        candidates.append(
            {
                "family": family,
                "variant": variant,
                "direction": direction,
                "train": _metrics(train),
                "validation": _metrics(validation),
                "test": _metrics(test),
            }
        )
    return {
        "engine": ENGINE,
        "symbol": symbol,
        "bar_minutes": 5,
        "entry": "next 5m open after first bar of condition episode",
        "target_pct": TARGET_PCT,
        "stop_pct": STOP_PCT,
        "ambiguous_same_bar": "counted_as_stop",
        "split": {"train": 0.60, "validation": 0.20, "test": 0.20},
        "bars": len(bars),
        "signals": len(signals),
        "candidates": candidates,
    }


def run(raw_root: Path, output_root: Path, symbol: str) -> dict[str, Any]:
    bars = build_bars(raw_root, symbol)
    signals = generate_signals(symbol, bars)
    summary = summarize(symbol, bars, signals)
    destination = output_root / symbol
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Signal.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(row) for row in signals)
    (destination / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"symbol": symbol, "bars": len(bars), "signals": len(signals)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.raw_root, args.output_root, args.symbol.upper())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
