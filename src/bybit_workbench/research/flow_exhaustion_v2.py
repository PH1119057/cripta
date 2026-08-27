from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast

from bybit_workbench.research.flow_reversal_v1 import _archive_map, _required
from bybit_workbench.research.mtf_entry import Direction, _decimal_json
from bybit_workbench.research.mtf_entry_v3 import _parse_archive_timestamp

TransitionState = Literal[
    "preflip_holds",
    "preflip_fails",
    "late_flip_after_touch",
    "pressure_continues",
    "favorable_holds",
    "favorable_fades_after_touch",
    "recovery_after_fade",
    "fade_continues",
    "other",
]


@dataclass(frozen=True, slots=True)
class ExhaustionResearchConfig:
    symbol: str = "UNIUSDT"
    post_windows_seconds: tuple[int, ...] = (15, 30, 60)
    pressure_window_seconds: int = 240
    pressure_gap_seconds: int = 60
    decay_window_seconds: int = 30
    persistence_window_seconds: int = 120

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if any(value <= 0 for value in self.post_windows_seconds):
            raise ValueError("post windows must be positive")
        if self.pressure_window_seconds <= 0:
            raise ValueError("pressure window must be positive")
        if self.pressure_gap_seconds < 0:
            raise ValueError("pressure gap cannot be negative")
        if self.decay_window_seconds <= 0:
            raise ValueError("decay window must be positive")
        if self.persistence_window_seconds <= 0:
            raise ValueError("persistence window must be positive")


@dataclass(frozen=True, slots=True)
class P31SourceSignal:
    symbol: str
    direction: Direction
    candidate_bar_at: datetime
    entry_price: Decimal
    touch_at: datetime
    hourly_alignment: str
    zone_gap_percent: Decimal
    exact_first_0_5_vs_0_5: str
    exact_first_0_5_vs_1_0: str
    exact_mfe_30m_pct: Decimal | None
    exact_mae_30m_pct: Decimal | None
    p31_flow_state: str
    p31_pressure_delta_pct: Decimal | None
    p31_reversal_delta_pct: Decimal | None


@dataclass(frozen=True, slots=True)
class MicroTape:
    timestamps: tuple[float, ...]
    prices: tuple[float, ...]
    buy_prefix: tuple[float, ...]
    sell_prefix: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.timestamps) != len(self.prices):
            raise ValueError("tape timestamps/prices length mismatch")
        if len(self.buy_prefix) != len(self.timestamps) + 1:
            raise ValueError("buy prefix length mismatch")
        if len(self.sell_prefix) != len(self.timestamps) + 1:
            raise ValueError("sell prefix length mismatch")


@dataclass(frozen=True, slots=True)
class MicroFeatures:
    pressure_delta_exact_pct: Decimal
    pre60_delta_pct: Decimal
    pre30_delta_pct: Decimal
    post15_delta_pct: Decimal
    post30_delta_pct: Decimal
    post60_delta_pct: Decimal
    adverse_decay_ratio_30s: Decimal | None
    adverse_persistence_120s: Decimal
    pre120_adverse_net_notional: Decimal
    pre120_adverse_progress_bps: Decimal
    absorption_notional_per_bp: Decimal | None
    post15_price_move_pct: Decimal | None
    post30_price_move_pct: Decimal | None
    post60_price_move_pct: Decimal | None
    transition_30s: TransitionState
    transition_60s: TransitionState


@dataclass(frozen=True, slots=True)
class P32Signal:
    source: P31SourceSignal
    features: MicroFeatures


def _optional_decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {value}") from exc


def _load_p31_signals(path: Path) -> tuple[P31SourceSignal, ...]:
    items: list[P31SourceSignal] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            touch_raw = row.get("touch_at")
            if not touch_raw:
                continue
            direction_raw = _required(row, "direction")
            if direction_raw not in {"Long", "Short"}:
                raise ValueError(f"unsupported direction: {direction_raw}")
            items.append(
                P31SourceSignal(
                    symbol=_required(row, "symbol"),
                    direction=cast(Direction, direction_raw),
                    candidate_bar_at=datetime.fromisoformat(
                        _required(row, "candidate_bar_at")
                    ).astimezone(UTC),
                    entry_price=Decimal(_required(row, "entry_price")),
                    touch_at=datetime.fromisoformat(touch_raw).astimezone(UTC),
                    hourly_alignment=str(row.get("hourly_alignment") or ""),
                    zone_gap_percent=Decimal(_required(row, "zone_gap_percent")),
                    exact_first_0_5_vs_0_5=_required(row, "exact_first_0_5_vs_0_5"),
                    exact_first_0_5_vs_1_0=_required(row, "exact_first_0_5_vs_1_0"),
                    exact_mfe_30m_pct=_optional_decimal(row.get("exact_mfe_30m_pct")),
                    exact_mae_30m_pct=_optional_decimal(row.get("exact_mae_30m_pct")),
                    p31_flow_state=str(row.get("flow_state") or ""),
                    p31_pressure_delta_pct=_optional_decimal(
                        row.get("pressure_directional_delta_pct")
                    ),
                    p31_reversal_delta_pct=_optional_decimal(
                        row.get("reversal_directional_delta_pct")
                    ),
                )
            )
    return tuple(items)


def _load_micro_tape(path: Path) -> MicroTape:
    rows: list[tuple[float, float, str, float]] = []
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"trade archive has no header: {path}")
        names = {name.strip().lower(): name for name in reader.fieldnames if name}
        required = {"timestamp", "side", "size", "price"}
        if not required.issubset(names):
            raise ValueError(
                f"unsupported trade archive header in {path.name}: {reader.fieldnames}"
            )
        for row in reader:
            try:
                traded_at = _parse_archive_timestamp(_required(row, names["timestamp"]))
                price = float(Decimal(_required(row, names["price"])))
                size = float(Decimal(_required(row, names["size"])))
                side = _required(row, names["side"]).strip().title()
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise ValueError(f"invalid trade row in {path.name}: {row}") from exc
            if side not in {"Buy", "Sell"}:
                continue
            rows.append((traded_at.timestamp(), price, side, price * size))

    if any(left[0] > right[0] for left, right in zip(rows, rows[1:], strict=False)):
        rows.sort(key=lambda item: item[0])

    timestamps: list[float] = []
    prices: list[float] = []
    buy_prefix: list[float] = [0.0]
    sell_prefix: list[float] = [0.0]
    for timestamp, price, side, notional in rows:
        timestamps.append(timestamp)
        prices.append(price)
        buy_prefix.append(buy_prefix[-1] + (notional if side == "Buy" else 0.0))
        sell_prefix.append(sell_prefix[-1] + (notional if side == "Sell" else 0.0))
    return MicroTape(
        timestamps=tuple(timestamps),
        prices=tuple(prices),
        buy_prefix=tuple(buy_prefix),
        sell_prefix=tuple(sell_prefix),
    )


def _combine_tapes(first: MicroTape, second: MicroTape | None) -> MicroTape:
    if second is None:
        return first
    timestamps = first.timestamps + second.timestamps
    prices = first.prices + second.prices
    first_buy = first.buy_prefix[-1]
    first_sell = first.sell_prefix[-1]
    buy_prefix = first.buy_prefix + tuple(first_buy + value for value in second.buy_prefix[1:])
    sell_prefix = first.sell_prefix + tuple(
        first_sell + value for value in second.sell_prefix[1:]
    )
    return MicroTape(timestamps, prices, buy_prefix, sell_prefix)


def _window_notional(tape: MicroTape, start_ts: float, end_ts: float) -> tuple[float, float]:
    if end_ts <= start_ts:
        return 0.0, 0.0
    start = bisect.bisect_left(tape.timestamps, start_ts)
    end = bisect.bisect_left(tape.timestamps, end_ts)
    buy = tape.buy_prefix[end] - tape.buy_prefix[start]
    sell = tape.sell_prefix[end] - tape.sell_prefix[start]
    return buy, sell


def _directional_delta_pct(direction: Direction, buy: float, sell: float) -> float:
    total = buy + sell
    if total <= 0:
        return 0.0
    raw = (buy - sell) / total * 100.0
    return raw if direction == "Long" else -raw


def _adverse_notional(direction: Direction, buy: float, sell: float) -> float:
    return sell if direction == "Long" else buy


def _favorable_notional(direction: Direction, buy: float, sell: float) -> float:
    return buy if direction == "Long" else sell


def _directional_return_pct(direction: Direction, start_price: float, end_price: float) -> float:
    if start_price <= 0:
        return 0.0
    raw = (end_price - start_price) / start_price * 100.0
    return raw if direction == "Long" else -raw


def _price_at_or_after(tape: MicroTape, timestamp: float) -> float | None:
    index = bisect.bisect_left(tape.timestamps, timestamp)
    if index >= len(tape.prices):
        return None
    return tape.prices[index]


def _price_at_or_before(tape: MicroTape, timestamp: float) -> float | None:
    index = bisect.bisect_right(tape.timestamps, timestamp) - 1
    if index < 0:
        return None
    return tape.prices[index]


def _decimal(value: float, digits: int = 8) -> Decimal:
    return Decimal(str(round(value, digits)))


def _transition_state(p31_state: str, post_delta: float) -> TransitionState:
    favorable_post = post_delta > 0
    if p31_state == "pressure_then_reversal":
        return "preflip_holds" if favorable_post else "preflip_fails"
    if p31_state == "pressure_continues":
        return "late_flip_after_touch" if favorable_post else "pressure_continues"
    if p31_state == "already_favorable":
        return "favorable_holds" if favorable_post else "favorable_fades_after_touch"
    if p31_state == "favorable_then_fades":
        return "recovery_after_fade" if favorable_post else "fade_continues"
    return "other"


def _analyse_micro_features(
    signal: P31SourceSignal,
    tape: MicroTape,
    *,
    config: ExhaustionResearchConfig,
) -> MicroFeatures:
    touch_ts = signal.touch_at.timestamp()
    pressure_end = touch_ts - config.pressure_gap_seconds
    pressure_start = pressure_end - config.pressure_window_seconds
    pressure_buy, pressure_sell = _window_notional(tape, pressure_start, pressure_end)
    pressure_delta = _directional_delta_pct(signal.direction, pressure_buy, pressure_sell)

    pre60_buy, pre60_sell = _window_notional(tape, touch_ts - 60, touch_ts)
    pre30_buy, pre30_sell = _window_notional(tape, touch_ts - 30, touch_ts)
    pre60_delta = _directional_delta_pct(signal.direction, pre60_buy, pre60_sell)
    pre30_delta = _directional_delta_pct(signal.direction, pre30_buy, pre30_sell)

    post_deltas: dict[int, float] = {}
    post_moves: dict[int, float | None] = {}
    touch_price = _price_at_or_after(tape, touch_ts)
    for seconds in config.post_windows_seconds:
        buy, sell = _window_notional(tape, touch_ts, touch_ts + seconds)
        post_deltas[seconds] = _directional_delta_pct(signal.direction, buy, sell)
        end_price = _price_at_or_before(tape, touch_ts + seconds)
        post_moves[seconds] = (
            None
            if touch_price is None or end_price is None
            else _directional_return_pct(signal.direction, touch_price, end_price)
        )

    decay_seconds = config.decay_window_seconds
    recent_buy, recent_sell = _window_notional(tape, touch_ts - decay_seconds, touch_ts)
    prior_start = touch_ts - 4 * decay_seconds
    prior_end = touch_ts - decay_seconds
    prior_buy, prior_sell = _window_notional(tape, prior_start, prior_end)
    recent_adverse = _adverse_notional(signal.direction, recent_buy, recent_sell)
    prior_adverse_per_window = _adverse_notional(signal.direction, prior_buy, prior_sell) / 3.0
    decay_ratio = (
        None
        if prior_adverse_per_window <= 0
        else recent_adverse / prior_adverse_per_window
    )

    persistence_seconds = config.persistence_window_seconds
    negative_buckets = 0
    populated_buckets = 0
    bucket_seconds = 30
    for offset in range(0, persistence_seconds, bucket_seconds):
        end = touch_ts - offset
        start = end - bucket_seconds
        buy, sell = _window_notional(tape, start, end)
        if buy + sell <= 0:
            continue
        populated_buckets += 1
        if _directional_delta_pct(signal.direction, buy, sell) < 0:
            negative_buckets += 1
    persistence = 0.0 if populated_buckets == 0 else negative_buckets / populated_buckets

    pre120_buy, pre120_sell = _window_notional(tape, touch_ts - 120, touch_ts)
    adverse = _adverse_notional(signal.direction, pre120_buy, pre120_sell)
    favorable = _favorable_notional(signal.direction, pre120_buy, pre120_sell)
    adverse_net = max(0.0, adverse - favorable)
    start_price = _price_at_or_after(tape, touch_ts - 120)
    adverse_progress_bps = 0.0
    if touch_price is not None and start_price is not None:
        directional_progress = _directional_return_pct(
            signal.direction, start_price, touch_price
        )
        adverse_progress_bps = max(0.0, -directional_progress * 100.0)
    absorption = None if adverse_net <= 0 else adverse_net / max(adverse_progress_bps, 1.0)

    post15 = post_deltas.get(15, 0.0)
    post30 = post_deltas.get(30, 0.0)
    post60 = post_deltas.get(60, 0.0)
    post15_move = post_moves.get(15)
    post30_move = post_moves.get(30)
    post60_move = post_moves.get(60)
    return MicroFeatures(
        pressure_delta_exact_pct=_decimal(pressure_delta),
        pre60_delta_pct=_decimal(pre60_delta),
        pre30_delta_pct=_decimal(pre30_delta),
        post15_delta_pct=_decimal(post15),
        post30_delta_pct=_decimal(post30),
        post60_delta_pct=_decimal(post60),
        adverse_decay_ratio_30s=None if decay_ratio is None else _decimal(decay_ratio),
        adverse_persistence_120s=_decimal(persistence),
        pre120_adverse_net_notional=_decimal(adverse_net),
        pre120_adverse_progress_bps=_decimal(adverse_progress_bps),
        absorption_notional_per_bp=None if absorption is None else _decimal(absorption),
        post15_price_move_pct=None if post15_move is None else _decimal(post15_move),
        post30_price_move_pct=None if post30_move is None else _decimal(post30_move),
        post60_price_move_pct=None if post60_move is None else _decimal(post60_move),
        transition_30s=_transition_state(signal.p31_flow_state, post30),
        transition_60s=_transition_state(signal.p31_flow_state, post60),
    )


def _percentage(count: int, total: int) -> float:
    return 0.0 if total <= 0 else round(count * 100.0 / total, 2)


def _safe_median(values: list[Decimal]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 4)


def _subset_summary(signals: tuple[P32Signal, ...]) -> dict[str, Any]:
    resolved_10 = tuple(
        item
        for item in signals
        if item.source.exact_first_0_5_vs_1_0 not in {"incomplete"}
    )
    resolved_05 = tuple(
        item
        for item in signals
        if item.source.exact_first_0_5_vs_0_5 not in {"incomplete"}
    )
    favorable_10 = len(
        [
            item
            for item in resolved_10
            if item.source.exact_first_0_5_vs_1_0 == "favorable_first"
        ]
    )
    favorable_05 = len(
        [
            item
            for item in resolved_05
            if item.source.exact_first_0_5_vs_0_5 == "favorable_first"
        ]
    )
    mfe = [
        item.source.exact_mfe_30m_pct
        for item in signals
        if item.source.exact_mfe_30m_pct is not None
    ]
    mae = [
        item.source.exact_mae_30m_pct
        for item in signals
        if item.source.exact_mae_30m_pct is not None
    ]
    return {
        "signals": len(signals),
        "long": len([item for item in signals if item.source.direction == "Long"]),
        "short": len([item for item in signals if item.source.direction == "Short"]),
        "exact_first_0_5_vs_1_0_favorable_percent": _percentage(
            favorable_10, len(resolved_10)
        ),
        "exact_first_0_5_vs_0_5_favorable_percent": _percentage(
            favorable_05, len(resolved_05)
        ),
        "median_exact_mfe_30m_pct": _safe_median(mfe),
        "median_exact_mae_30m_pct": _safe_median(mae),
    }


def _slice_index(signal: P32Signal, evaluation_start: datetime) -> int:
    elapsed = signal.source.candidate_bar_at - evaluation_start
    return int(elapsed.total_seconds() // (30 * 86400)) + 1


def _transition_rows(
    signals: tuple[P32Signal, ...],
    *,
    evaluation_start: datetime,
    window_seconds: int,
) -> list[dict[str, Any]]:
    states: tuple[TransitionState, ...] = (
        "preflip_holds",
        "preflip_fails",
        "late_flip_after_touch",
        "pressure_continues",
        "favorable_holds",
        "favorable_fades_after_touch",
        "recovery_after_fade",
        "fade_continues",
        "other",
    )
    rows: list[dict[str, Any]] = []
    attr = "transition_30s" if window_seconds == 30 else "transition_60s"
    for state in states:
        subset = tuple(item for item in signals if getattr(item.features, attr) == state)
        if not subset:
            continue
        row: dict[str, Any] = {
            "window_seconds": window_seconds,
            "transition_state": state,
            **_subset_summary(subset),
        }
        for index in (1, 2, 3):
            monthly = tuple(
                item for item in subset if _slice_index(item, evaluation_start) == index
            )
            month_summary = _subset_summary(monthly)
            row[f"month_{index}_signals"] = month_summary["signals"]
            row[f"month_{index}_fav_0_5_before_1_0_pct"] = month_summary[
                "exact_first_0_5_vs_1_0_favorable_percent"
            ]
            row[f"month_{index}_fav_0_5_before_0_5_pct"] = month_summary[
                "exact_first_0_5_vs_0_5_favorable_percent"
            ]
        rows.append(row)
    return rows


def _quartile_cutpoints(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal] | None:
    if len(values) < 4:
        return None
    numeric = [float(value) for value in values]
    cuts = statistics.quantiles(numeric, n=4, method="inclusive")
    return (
        Decimal(str(cuts[0])),
        Decimal(str(cuts[1])),
        Decimal(str(cuts[2])),
    )


def _quartile_rows(signals: tuple[P32Signal, ...]) -> list[dict[str, Any]]:
    features = (
        "pre30_delta_pct",
        "post15_delta_pct",
        "post30_delta_pct",
        "post60_delta_pct",
        "adverse_decay_ratio_30s",
        "adverse_persistence_120s",
        "pre120_adverse_progress_bps",
        "absorption_notional_per_bp",
        "post30_price_move_pct",
        "post60_price_move_pct",
    )
    rows: list[dict[str, Any]] = []
    for feature in features:
        available = tuple(
            item
            for item in signals
            if getattr(item.features, feature) is not None
        )
        values = [getattr(item.features, feature) for item in available]
        decimal_values = [value for value in values if isinstance(value, Decimal)]
        cuts = _quartile_cutpoints(decimal_values)
        if cuts is None:
            continue
        boundaries: tuple[tuple[Decimal | None, Decimal | None], ...] = (
            (None, cuts[0]),
            (cuts[0], cuts[1]),
            (cuts[1], cuts[2]),
            (cuts[2], None),
        )
        for index, (lower, upper) in enumerate(boundaries, start=1):
            subset = tuple(
                item
                for item in available
                if (lower is None or getattr(item.features, feature) > lower)
                and (upper is None or getattr(item.features, feature) <= upper)
            )
            rows.append(
                {
                    "feature": feature,
                    "quartile": index,
                    "lower_exclusive": lower,
                    "upper_inclusive": upper,
                    **_subset_summary(subset),
                }
            )
    return rows


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return _decimal_json(value)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_signals(path: Path, signals: tuple[P32Signal, ...]) -> None:
    fields = [
        "symbol",
        "direction",
        "candidate_bar_at",
        "entry_price",
        "touch_at",
        "hourly_alignment",
        "zone_gap_percent",
        "exact_first_0_5_vs_0_5",
        "exact_first_0_5_vs_1_0",
        "exact_mfe_30m_pct",
        "exact_mae_30m_pct",
        "p31_flow_state",
        "p31_pressure_delta_pct",
        "p31_reversal_delta_pct",
        *[field.name for field in MicroFeatures.__dataclass_fields__.values()],
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in signals:
            row: dict[str, Any] = {
                "symbol": item.source.symbol,
                "direction": item.source.direction,
                "candidate_bar_at": item.source.candidate_bar_at.isoformat(),
                "entry_price": item.source.entry_price,
                "touch_at": item.source.touch_at.isoformat(),
                "hourly_alignment": item.source.hourly_alignment,
                "zone_gap_percent": item.source.zone_gap_percent,
                "exact_first_0_5_vs_0_5": item.source.exact_first_0_5_vs_0_5,
                "exact_first_0_5_vs_1_0": item.source.exact_first_0_5_vs_1_0,
                "exact_mfe_30m_pct": item.source.exact_mfe_30m_pct,
                "exact_mae_30m_pct": item.source.exact_mae_30m_pct,
                "p31_flow_state": item.source.p31_flow_state,
                "p31_pressure_delta_pct": item.source.p31_pressure_delta_pct,
                "p31_reversal_delta_pct": item.source.p31_reversal_delta_pct,
            }
            row.update(asdict(item.features))
            writer.writerow(row)


def _p31_metadata(p31_dir: Path) -> tuple[Path, datetime, datetime]:
    summary_path = p31_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"P31 summary not found: {summary_path}")
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    dataset_dir = Path(str(payload["dataset_dir"]))
    evaluation_start = datetime.fromisoformat(str(payload["evaluation_start"])).astimezone(UTC)
    evaluation_end = datetime.fromisoformat(str(payload["evaluation_end"])).astimezone(UTC)
    return dataset_dir, evaluation_start, evaluation_end


def run_exhaustion_research(
    p31_dir: Path,
    *,
    config: ExhaustionResearchConfig,
    dataset_dir_override: Path | None = None,
) -> tuple[tuple[P32Signal, ...], Path, datetime, datetime]:
    dataset_dir, evaluation_start, evaluation_end = _p31_metadata(p31_dir)
    if dataset_dir_override is not None:
        dataset_dir = dataset_dir_override
    signal_path = p31_dir / "signals_touch_exact.csv"
    source_signals = _load_p31_signals(signal_path)
    archives = _archive_map(dataset_dir)

    by_day: dict[str, list[P31SourceSignal]] = {}
    for signal in source_signals:
        by_day.setdefault(signal.touch_at.date().isoformat(), []).append(signal)

    cache: dict[str, MicroTape] = {}
    analysed: list[P32Signal] = []
    ordered_days = sorted(by_day)
    for day_index, day_key in enumerate(ordered_days, start=1):
        print(f"P32 microstructure day {day_index}/{len(ordered_days)}: {day_key}")
        current_path = archives.get(day_key)
        if current_path is None:
            continue
        if day_key not in cache:
            cache[day_key] = _load_micro_tape(current_path)
        current = cache[day_key]
        next_key = (datetime.fromisoformat(day_key).date() + timedelta(days=1)).isoformat()
        next_path = archives.get(next_key)
        if next_path is not None and next_key not in cache:
            cache[next_key] = _load_micro_tape(next_path)
        combined = _combine_tapes(current, cache.get(next_key))
        for signal in by_day[day_key]:
            analysed.append(
                P32Signal(
                    source=signal,
                    features=_analyse_micro_features(signal, combined, config=config),
                )
            )
        previous_key = (datetime.fromisoformat(day_key).date() - timedelta(days=1)).isoformat()
        cache.pop(previous_key, None)

    return (
        tuple(sorted(analysed, key=lambda item: item.source.candidate_bar_at)),
        dataset_dir,
        evaluation_start,
        evaluation_end,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P32 flow exhaustion microstructure research")
    parser.add_argument("--p31-dir", required=True)
    parser.add_argument("--dataset-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--symbol", default="UNIUSDT")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    p31_dir = Path(args.p31_dir).resolve()
    if not p31_dir.exists():
        raise FileNotFoundError(f"P31 result directory not found: {p31_dir}")
    dataset_override = Path(args.dataset_dir).resolve() if args.dataset_dir else None
    config = ExhaustionResearchConfig(symbol=args.symbol.strip().upper())
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("reports") / "entry_research_v5" / f"{config.symbol}_{stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    signals, dataset_dir, evaluation_start, evaluation_end = run_exhaustion_research(
        p31_dir,
        config=config,
        dataset_dir_override=dataset_override,
    )
    transition_30 = _transition_rows(
        signals, evaluation_start=evaluation_start, window_seconds=30
    )
    transition_60 = _transition_rows(
        signals, evaluation_start=evaluation_start, window_seconds=60
    )
    quartiles = _quartile_rows(signals)
    _write_signals(output_dir / "signals_exhaustion_micro.csv", signals)
    _write_rows(output_dir / "transition_summary_30s.csv", transition_30)
    _write_rows(output_dir / "transition_summary_60s.csv", transition_60)
    _write_rows(output_dir / "feature_quartiles.csv", quartiles)

    summary = {
        "architecture": "p32_flow_exhaustion_microstructure",
        "dataset_dir": dataset_dir,
        "p31_dir": p31_dir,
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "config": asdict(config),
        "all": _subset_summary(signals),
        "transition_30s": transition_30,
        "transition_60s": transition_60,
        "feature_quartiles": quartiles,
        "notes": [
            "P32 changes no live-trading, exit, stop-loss, or take-profit logic.",
            (
                "P31 exact touch outcomes remain the labels; P32 only studies "
                "microstructure around touch."
            ),
            (
                "Post-touch flow is diagnostic future information relative to touch and "
                "is NOT yet treated as an executable entry."
            ),
            (
                "P32 separates a pre-touch flip that holds, a pre-touch flip that fails, "
                "and a late flip that occurs only after touch."
            ),
            (
                "Adverse-flow decay and absorption metrics are exploratory; no threshold "
                "is promoted into a trading gate."
            ),
            (
                "Research target is not the exact reversal tick: small adverse movement "
                "inside a valid entry zone is expected and is not treated as failure."
            ),
            (
                "Post-touch confirmation must not be assumed free: any future executable "
                "model must price latency and the worse entry obtained while waiting."
            ),
        ],
    }
    _write_json(output_dir / "summary.json", summary)

    print(f"Dataset: {dataset_dir}")
    print(f"P31 source: {p31_dir}")
    print(f"Signals analysed: {len(signals)}")
    print(f"Report: {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
