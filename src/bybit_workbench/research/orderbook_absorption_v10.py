from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import shutil
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from bybit_workbench.research.orderbook_cache_utils import find_local_orderbook_archive
from bybit_workbench.research.orderbook_full_v9 import (
    OUTCOME_LABELS,
    _outcome_counts,
    _percentile,
    _read_csv,
    _write_csv,
    _write_json,
)
from bybit_workbench.research.orderbook_pilot_v8 import (
    BookState,
    PilotWindow,
    _levels,
    _normalize_event,
    discover_archive,
    download_archive,
    load_windows,
)
from bybit_workbench.research.orderbook_plan_v7 import Direction

WINDOW_SECONDS = 30
BANDS_BPS = (5, 10, 25)
QUARTILE_FEATURES = (
    "support_refill_ratio_to_removed_10bps_30s",
    "support_refill_ratio_to_removed_25bps_30s",
    "support_add_to_adverse_taker_ratio_10bps_30s",
    "support_refill_to_adverse_taker_ratio_10bps_30s",
    "support_add_to_adverse_taker_ratio_25bps_30s",
    "support_refill_to_adverse_taker_ratio_25bps_30s",
    "support_net_notional_10bps_30s",
    "support_net_notional_25bps_30s",
    "adverse_taker_notional_30s",
    "adverse_price_progress_bps_30s",
    "adverse_flow_resistance_proxy_30s",
    "directional_price_change_bps_30s",
)
BINARY_STATES = (
    "adverse_taker_dominant_30s",
    "price_favorable_or_flat_30s",
    "adverse_flow_but_price_holds_30s",
    "support_net_positive_10bps_30s",
    "support_refill_present_10bps_30s",
    "support_refill_present_25bps_30s",
)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _parse_trade_timestamp(raw: str) -> datetime:
    value = Decimal(raw.strip())
    absolute = abs(value)
    if absolute >= Decimal("1e18"):
        seconds = value / Decimal("1e9")
    elif absolute >= Decimal("1e15"):
        seconds = value / Decimal("1e6")
    elif absolute >= Decimal("1e11"):
        seconds = value / Decimal("1e3")
    else:
        seconds = value
    return datetime.fromtimestamp(float(seconds), UTC)


@dataclass(slots=True)
class BandActivity:
    support_add_notional: float = 0.0
    support_remove_notional: float = 0.0
    support_refill_notional: float = 0.0
    support_update_events: int = 0
    support_refill_events: int = 0
    adverse_add_notional: float = 0.0
    adverse_remove_notional: float = 0.0
    adverse_refill_notional: float = 0.0
    adverse_update_events: int = 0
    adverse_refill_events: int = 0
    removed_support_qty: dict[str, float] = field(default_factory=dict)
    removed_adverse_qty: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class WindowAccumulator:
    window: PilotWindow
    bands: dict[int, BandActivity] = field(
        default_factory=lambda: {band: BandActivity() for band in BANDS_BPS}
    )
    start_captured: bool = False
    touch_captured: bool = False
    snapshot_during_window: bool = False
    start_support_notional: dict[int, float] = field(default_factory=dict)
    touch_support_notional: dict[int, float] = field(default_factory=dict)
    start_adverse_notional: dict[int, float] = field(default_factory=dict)
    touch_adverse_notional: dict[int, float] = field(default_factory=dict)

    @property
    def start_at(self) -> datetime:
        return self.window.touch_at - timedelta(seconds=WINDOW_SECONDS)

    @property
    def touch_at(self) -> datetime:
        return self.window.touch_at


@dataclass(slots=True)
class TradeAccumulator:
    buy_notional: float = 0.0
    sell_notional: float = 0.0
    trades: int = 0
    first_ts: float | None = None
    last_ts: float | None = None
    first_price: float | None = None
    last_price: float | None = None


def _distance_bps(price: float, anchor: float) -> float:
    if anchor <= 0:
        return math.inf
    return abs(price / anchor - 1.0) * 10000.0


def _role(direction: str, book_side: str) -> str:
    support = (direction == "Long" and book_side == "bid") or (
        direction == "Short" and book_side == "ask"
    )
    return "support" if support else "adverse"


def _state_notional(
    state: BookState,
    *,
    direction: str,
    entry_price: float,
    band_bps: int,
    role: str,
) -> float:
    if not state.ready:
        return 0.0
    if role == "support":
        levels = state.bids if direction == "Long" else state.asks
    else:
        levels = state.asks if direction == "Long" else state.bids
    total = 0.0
    for raw_price, qty in levels.items():
        try:
            price = float(raw_price)
        except ValueError:
            continue
        if qty > 0 and _distance_bps(price, entry_price) <= band_bps:
            total += price * qty
    return total


def _capture_state(acc: WindowAccumulator, state: BookState, *, touch: bool) -> None:
    for band in BANDS_BPS:
        support = _state_notional(
            state,
            direction=acc.window.direction,
            entry_price=acc.window.entry_price,
            band_bps=band,
            role="support",
        )
        adverse = _state_notional(
            state,
            direction=acc.window.direction,
            entry_price=acc.window.entry_price,
            band_bps=band,
            role="adverse",
        )
        if touch:
            acc.touch_support_notional[band] = support
            acc.touch_adverse_notional[band] = adverse
        else:
            acc.start_support_notional[band] = support
            acc.start_adverse_notional[band] = adverse
    if touch:
        acc.touch_captured = True
    else:
        acc.start_captured = True


def _record_level_change(
    activity: BandActivity,
    *,
    role: str,
    price_key: str,
    price: float,
    old_qty: float,
    new_qty: float,
) -> None:
    change = new_qty - old_qty
    if change == 0:
        return
    add = max(change, 0.0)
    remove = max(-change, 0.0)
    if role == "support":
        if add > 0:
            activity.support_add_notional += add * price
            activity.support_update_events += 1
            outstanding = activity.removed_support_qty.get(price_key, 0.0)
            refill = min(add, outstanding)
            if refill > 0:
                activity.support_refill_notional += refill * price
                activity.support_refill_events += 1
                remaining = outstanding - refill
                if remaining > 0:
                    activity.removed_support_qty[price_key] = remaining
                else:
                    activity.removed_support_qty.pop(price_key, None)
        if remove > 0:
            activity.support_remove_notional += remove * price
            activity.support_update_events += 1
            activity.removed_support_qty[price_key] = (
                activity.removed_support_qty.get(price_key, 0.0) + remove
            )
        return

    if add > 0:
        activity.adverse_add_notional += add * price
        activity.adverse_update_events += 1
        outstanding = activity.removed_adverse_qty.get(price_key, 0.0)
        refill = min(add, outstanding)
        if refill > 0:
            activity.adverse_refill_notional += refill * price
            activity.adverse_refill_events += 1
            remaining = outstanding - refill
            if remaining > 0:
                activity.removed_adverse_qty[price_key] = remaining
            else:
                activity.removed_adverse_qty.pop(price_key, None)
    if remove > 0:
        activity.adverse_remove_notional += remove * price
        activity.adverse_update_events += 1
        activity.removed_adverse_qty[price_key] = (
            activity.removed_adverse_qty.get(price_key, 0.0) + remove
        )


def observe_delta(
    acc: WindowAccumulator,
    state: BookState,
    data: dict[str, Any],
) -> None:
    if not state.ready:
        return
    for book_side, levels, current in (
        ("bid", _levels(data.get("b")), state.bids),
        ("ask", _levels(data.get("a")), state.asks),
    ):
        role = _role(acc.window.direction, book_side)
        for price_key, new_qty in levels:
            try:
                price = float(price_key)
            except ValueError:
                continue
            old_qty = current.get(price_key, 0.0)
            for band in BANDS_BPS:
                if _distance_bps(price, acc.window.entry_price) <= band:
                    _record_level_change(
                        acc.bands[band],
                        role=role,
                        price_key=price_key,
                        price=price,
                        old_qty=old_qty,
                        new_qty=new_qty,
                    )


def _activity_fields(acc: WindowAccumulator) -> dict[str, Any]:
    output: dict[str, Any] = {
        "snapshot_during_window_30s": _bool_text(acc.snapshot_during_window)
    }
    for band, activity in acc.bands.items():
        suffix = f"{band}bps_30s"
        support_net = activity.support_add_notional - activity.support_remove_notional
        adverse_net = activity.adverse_add_notional - activity.adverse_remove_notional
        output[f"support_add_notional_{suffix}"] = activity.support_add_notional
        output[f"support_remove_notional_{suffix}"] = activity.support_remove_notional
        output[f"support_net_notional_{suffix}"] = support_net
        output[f"support_refill_notional_{suffix}"] = activity.support_refill_notional
        output[f"support_update_events_{suffix}"] = activity.support_update_events
        output[f"support_refill_events_{suffix}"] = activity.support_refill_events
        output[f"adverse_add_notional_{suffix}"] = activity.adverse_add_notional
        output[f"adverse_remove_notional_{suffix}"] = activity.adverse_remove_notional
        output[f"adverse_net_notional_{suffix}"] = adverse_net
        output[f"adverse_refill_notional_{suffix}"] = activity.adverse_refill_notional
        output[f"adverse_update_events_{suffix}"] = activity.adverse_update_events
        output[f"adverse_refill_events_{suffix}"] = activity.adverse_refill_events
        output[f"support_refill_ratio_to_removed_{suffix}"] = (
            activity.support_refill_notional / activity.support_remove_notional
            if activity.support_remove_notional > 0
            else None
        )
        start_support = acc.start_support_notional.get(band)
        touch_support = acc.touch_support_notional.get(band)
        start_adverse = acc.start_adverse_notional.get(band)
        touch_adverse = acc.touch_adverse_notional.get(band)
        output[f"support_depth_start_{suffix}"] = start_support
        output[f"support_depth_touch_{suffix}"] = touch_support
        output[f"adverse_depth_start_{suffix}"] = start_adverse
        output[f"adverse_depth_touch_{suffix}"] = touch_adverse
        output[f"support_depth_ratio_start_to_touch_{suffix}"] = (
            touch_support / start_support
            if start_support is not None and start_support > 0 and touch_support is not None
            else None
        )
    return output


def analyze_orderbook_activity(
    path: Path, windows: list[PilotWindow]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    accumulators = [WindowAccumulator(window=item) for item in windows]
    state = BookState.empty()
    records = snapshots = deltas = 0
    if not accumulators:
        return [], {"records": 0, "snapshots": 0, "deltas": 0}
    last_touch = max(item.touch_at for item in accumulators)

    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if not members:
            raise ValueError(f"orderbook archive has no file: {path}")
        with archive.open(members[0], "r") as handle:
            for raw_line in handle:
                try:
                    payload = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                event = _normalize_event(payload)
                if event is None:
                    continue
                record_type, event_at, data = event
                records += 1

                for acc in accumulators:
                    if not acc.start_captured and event_at >= acc.start_at:
                        _capture_state(acc, state, touch=False)
                    if not acc.touch_captured and event_at > acc.touch_at:
                        _capture_state(acc, state, touch=True)
                    if acc.start_at <= event_at <= acc.touch_at:
                        if record_type == "snapshot":
                            acc.snapshot_during_window = True
                        elif record_type == "delta":
                            observe_delta(acc, state, data)

                state.apply(record_type, data)
                if record_type == "snapshot":
                    snapshots += 1
                elif record_type == "delta":
                    deltas += 1

                for acc in accumulators:
                    if not acc.touch_captured and event_at == acc.touch_at:
                        _capture_state(acc, state, touch=True)

                if event_at > last_touch and all(item.touch_captured for item in accumulators):
                    break

    for acc in accumulators:
        if not acc.start_captured:
            _capture_state(acc, state, touch=False)
        if not acc.touch_captured:
            _capture_state(acc, state, touch=True)

    rows: list[dict[str, Any]] = []
    for acc in accumulators:
        row: dict[str, Any] = {
            "symbol": acc.window.symbol,
            "direction": acc.window.direction,
            "candidate_bar_at": acc.window.candidate_bar_at.isoformat(),
            "touch_at": acc.window.touch_at.isoformat(),
            "entry_price": acc.window.entry_price,
            "day": acc.window.day.isoformat(),
            "segment": acc.window.segment,
            "flow_state": acc.window.flow_state,
            "basis_accel_quartile": acc.window.basis_accel_quartile,
            "first_0_5_vs_1_0": acc.window.first_0_5_vs_1_0,
            "first_1_0_vs_1_0": acc.window.first_1_0_vs_1_0,
        }
        row.update(_activity_fields(acc))
        rows.append(row)
    return rows, {"records": records, "snapshots": snapshots, "deltas": deltas}


def _trade_archive_path(dataset_dir: Path, symbol: str, day: date) -> Path:
    return dataset_dir / "public_trades" / f"{symbol}{day.isoformat()}.csv.gz"


def analyze_trade_activity(
    path: Path, windows: list[PilotWindow]
) -> dict[tuple[Direction, str], dict[str, Any]]:
    accs: dict[tuple[Direction, str], TradeAccumulator] = {
        (window.direction, window.touch_at.isoformat()): TradeAccumulator()
        for window in windows
    }
    if not path.is_file():
        raise FileNotFoundError(f"public trade archive not found: {path}")
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"trade archive has no header: {path}")
        names = {name.strip().lower(): name for name in reader.fieldnames if name}
        required = {"timestamp", "side", "size", "price"}
        if not required.issubset(names):
            raise ValueError(f"unsupported trade archive header in {path.name}")
        for raw in reader:
            try:
                traded_at = _parse_trade_timestamp(str(raw[names["timestamp"]]))
                side = str(raw[names["side"]]).strip().title()
                size = float(Decimal(str(raw[names["size"]]).strip()))
                price = float(Decimal(str(raw[names["price"]]).strip()))
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise ValueError(f"invalid trade row in {path.name}: {raw}") from exc
            for window in windows:
                start = window.touch_at - timedelta(seconds=WINDOW_SECONDS)
                if traded_at < start or traded_at > window.touch_at:
                    continue
                item = accs[(window.direction, window.touch_at.isoformat())]
                notional = price * size
                if side == "Buy":
                    item.buy_notional += notional
                elif side == "Sell":
                    item.sell_notional += notional
                item.trades += 1
                trade_ts = traded_at.timestamp()
                if item.first_ts is None or trade_ts < item.first_ts:
                    item.first_ts = trade_ts
                    item.first_price = price
                if item.last_ts is None or trade_ts > item.last_ts:
                    item.last_ts = trade_ts
                    item.last_price = price

    output: dict[tuple[Direction, str], dict[str, Any]] = {}
    for window in windows:
        key: tuple[Direction, str] = (window.direction, window.touch_at.isoformat())
        item = accs[key]
        adverse = item.sell_notional if window.direction == "Long" else item.buy_notional
        favorable = item.buy_notional if window.direction == "Long" else item.sell_notional
        directional_change: float | None = None
        if item.first_price is not None and item.last_price is not None and item.first_price > 0:
            raw_change = (item.last_price / item.first_price - 1.0) * 10000.0
            directional_change = raw_change if window.direction == "Long" else -raw_change
        adverse_progress = (
            max(0.0, -directional_change) if directional_change is not None else None
        )
        resistance_proxy = (
            adverse / (0.25 + adverse_progress)
            if adverse_progress is not None
            else None
        )
        output[key] = {
            "buy_taker_notional_30s": item.buy_notional,
            "sell_taker_notional_30s": item.sell_notional,
            "adverse_taker_notional_30s": adverse,
            "favorable_taker_notional_30s": favorable,
            "directed_taker_delta_notional_30s": favorable - adverse,
            "trade_count_30s": item.trades,
            "trade_first_price_30s": item.first_price,
            "trade_last_price_30s": item.last_price,
            "directional_price_change_bps_30s": directional_change,
            "adverse_price_progress_bps_30s": adverse_progress,
            "adverse_flow_resistance_proxy_30s": resistance_proxy,
            "adverse_taker_dominant_30s": _bool_text(adverse > favorable),
            "price_favorable_or_flat_30s": (
                "" if directional_change is None else _bool_text(directional_change >= 0)
            ),
            "adverse_flow_but_price_holds_30s": (
                ""
                if directional_change is None
                else _bool_text(adverse > favorable and directional_change >= 0)
            ),
        }
    return output


def merge_trade_metrics(
    rows: list[dict[str, Any]],
    trade_metrics: dict[tuple[Direction, str], dict[str, Any]],
) -> None:
    for row in rows:
        key: tuple[Direction, str] = (
            cast(Direction, str(row["direction"])),
            str(row["touch_at"]),
        )
        row.update(trade_metrics.get(key, {}))
        adverse = _float_or_none(row.get("adverse_taker_notional_30s"))
        for band in BANDS_BPS:
            support_add = _float_or_none(row.get(f"support_add_notional_{band}bps_30s"))
            support_refill = _float_or_none(
                row.get(f"support_refill_notional_{band}bps_30s")
            )
            support_net = _float_or_none(row.get(f"support_net_notional_{band}bps_30s"))
            if adverse is not None and adverse > 0:
                row[f"support_add_to_adverse_taker_ratio_{band}bps_30s"] = (
                    support_add / adverse if support_add is not None else None
                )
                row[f"support_refill_to_adverse_taker_ratio_{band}bps_30s"] = (
                    support_refill / adverse if support_refill is not None else None
                )
                row[f"support_net_to_adverse_taker_ratio_{band}bps_30s"] = (
                    support_net / adverse if support_net is not None else None
                )
            else:
                row[f"support_add_to_adverse_taker_ratio_{band}bps_30s"] = None
                row[f"support_refill_to_adverse_taker_ratio_{band}bps_30s"] = None
                row[f"support_net_to_adverse_taker_ratio_{band}bps_30s"] = None
        net10 = _float_or_none(row.get("support_net_notional_10bps_30s"))
        refill10 = _float_or_none(row.get("support_refill_notional_10bps_30s"))
        refill25 = _float_or_none(row.get("support_refill_notional_25bps_30s"))
        row["support_net_positive_10bps_30s"] = (
            "" if net10 is None else _bool_text(net10 > 0)
        )
        row["support_refill_present_10bps_30s"] = (
            "" if refill10 is None else _bool_text(refill10 > 0)
        )
        row["support_refill_present_25bps_30s"] = (
            "" if refill25 is None else _bool_text(refill25 > 0)
        )


def _quartile_boundaries(
    rows: list[dict[str, Any]], feature: str
) -> tuple[float, float, float] | None:
    values = sorted(
        value
        for row in rows
        if (value := _float_or_none(row.get(feature))) is not None
    )
    if len(values) < 4:
        return None
    return (
        _percentile(values, 0.25),
        _percentile(values, 0.50),
        _percentile(values, 0.75),
    )


def _quartile(value: float, boundaries: tuple[float, float, float]) -> str:
    q1, q2, q3 = boundaries
    if value <= q1:
        return "Q1"
    if value <= q2:
        return "Q2"
    if value <= q3:
        return "Q3"
    return "Q4"


def build_quartile_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for feature in QUARTILE_FEATURES:
        boundaries = _quartile_boundaries(rows, feature)
        if boundaries is None:
            continue
        buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in ("Q1", "Q2", "Q3", "Q4")}
        for row in rows:
            value = _float_or_none(row.get(feature))
            if value is not None:
                buckets[_quartile(value, boundaries)].append(row)
        for name, bucket in buckets.items():
            record: dict[str, Any] = {
                "feature": feature,
                "quartile": name,
                "q25": boundaries[0],
                "q50": boundaries[1],
                "q75": boundaries[2],
            }
            for label in OUTCOME_LABELS:
                stats = _outcome_counts(bucket, label)
                record[f"{label}_count"] = stats["count"]
                record[f"{label}_favorable_percent"] = stats["favorable_percent"]
                record[f"{label}_decisive_favorable_percent"] = stats[
                    "decisive_favorable_percent"
                ]
            output.append(record)
    return output


def build_state_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for state in BINARY_STATES:
        for value in ("true", "false"):
            bucket = [row for row in rows if row.get(state) == value]
            record: dict[str, Any] = {"state": state, "value": value}
            for label in OUTCOME_LABELS:
                stats = _outcome_counts(bucket, label)
                record[f"{label}_count"] = stats["count"]
                record[f"{label}_favorable_percent"] = stats["favorable_percent"]
                record[f"{label}_decisive_favorable_percent"] = stats[
                    "decisive_favorable_percent"
                ]
            output.append(record)
    return output


def build_monthly_stability(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for segment in ("1", "2", "3"):
        segment_rows = [row for row in rows if str(row.get("segment")) == segment]
        base: dict[str, Any] = {"segment": segment, "group": "baseline", "count": len(segment_rows)}
        for label in OUTCOME_LABELS:
            base[f"{label}_favorable_percent"] = _outcome_counts(segment_rows, label)[
                "favorable_percent"
            ]
        output.append(base)
        for state in BINARY_STATES:
            bucket = [row for row in segment_rows if row.get(state) == "true"]
            record: dict[str, Any] = {
                "segment": segment,
                "group": f"{state}=true",
                "count": len(bucket),
            }
            for label in OUTCOME_LABELS:
                record[f"{label}_favorable_percent"] = _outcome_counts(bucket, label)[
                    "favorable_percent"
                ]
            output.append(record)
    return output


def _latest_dir(root: Path, report_name: str, required_file: str) -> Path:
    base = root / "reports" / report_name
    candidates = [path for path in base.glob("UNIUSDT_*") if (path / required_file).is_file()]
    if not candidates:
        raise FileNotFoundError(f"no completed {report_name} result found")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def resolve_output_dir(root: Path, symbol: str, p39_dir: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    base = root / "reports" / "entry_research_v13"
    if base.is_dir():
        incomplete: list[Path] = []
        for candidate in base.glob(f"{symbol}_*"):
            state = _read_state(candidate / "run_state.json")
            if (
                state is not None
                and state.get("complete") is False
                and state.get("p39_dir") == str(p39_dir)
            ):
                incomplete.append(candidate)
        if incomplete:
            return max(incomplete, key=lambda path: path.stat().st_mtime)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    return base / f"{symbol}_{stamp}"


def _day_cache_path(output_dir: Path, day: date) -> Path:
    return output_dir / "day_features" / f"{day.isoformat()}.csv"


def _day_stats_path(output_dir: Path, day: date) -> Path:
    return output_dir / "day_stats" / f"{day.isoformat()}.json"


def _orderbook_worker_count() -> int:
    raw = os.environ.get("BYBIT_RESEARCH_ORDERBOOK_WORKERS", "1").strip()
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(1, min(4, value))


def _analyze_absorption_day_task(
    archive_path: Path,
    trade_path: Path,
    day_windows: list[PilotWindow],
    archive_depth: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, orderbook_stats = analyze_orderbook_activity(archive_path, day_windows)
    trade_metrics = analyze_trade_activity(trade_path, day_windows)
    merge_trade_metrics(rows, trade_metrics)
    archive_bytes = archive_path.stat().st_size
    for row in rows:
        row["archive_depth"] = archive_depth
        row["archive_bytes"] = archive_bytes
    return rows, {
        "missing": False,
        "feature_rows": len(rows),
        "depth": archive_depth,
        **orderbook_stats,
    }


def run_absorption(
    *,
    p39_dir: Path,
    output_dir: Path,
    archive_dir: Path | None,
    keep_archives: bool,
    max_days: int | None,
) -> dict[str, Any]:
    p39_summary = json.loads((p39_dir / "summary.json").read_text(encoding="utf-8"))
    p37_dir = Path(str(p39_summary["p37_dir"]))
    p36_dir = Path(str(p39_summary["p36_dir"]))
    p36_summary = json.loads((p36_dir / "summary.json").read_text(encoding="utf-8"))
    dataset_dir = Path(str(p36_summary["dataset_dir"]))
    windows = load_windows(p37_dir, p36_dir, pilot_only=False)
    if not windows:
        raise ValueError("P39/P37 core orderbook sample has no windows")
    symbol = windows[0].symbol
    days = sorted({window.day for window in windows})
    if max_days is not None:
        days = days[:max_days]
    if archive_dir is None:
        archive_dir = dataset_dir / "orderbook_absorption"
    archive_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        output_dir / "run_state.json",
        {
            "architecture": "p40_orderbook_event_absorption",
            "p39_dir": str(p39_dir),
            "complete": False,
            "expected_days": len(days),
        },
    )

    all_rows: list[dict[str, Any]] = []
    day_stats: list[dict[str, Any]] = []
    pending: list[tuple[date, Path, Path, int, str, list[PilotWindow]]] = []
    for index, day in enumerate(days, start=1):
        cache = _day_cache_path(output_dir, day)
        stats_path = _day_stats_path(output_dir, day)
        if cache.is_file() and stats_path.is_file():
            all_rows.extend(_read_csv(cache))
            state = _read_state(stats_path)
            if state is not None:
                day_stats.append(state)
            print(f"P40 absorption day {index}/{len(days)}: {day} (reuse cache)")
            continue

        print(f"P40 absorption day {index}/{len(days)}: {day}")
        local_archive = find_local_orderbook_archive(
            archive_dir, symbol=symbol, day=day
        )
        if local_archive is not None:
            archive_path, archive_depth = local_archive
            filename = archive_path.name
            print(f"  reuse local orderbook archive: {archive_path}")
        else:
            if os.environ.get("BYBIT_RESEARCH_ORDERBOOK_LOCAL_ONLY") == "1":
                raise FileNotFoundError(
                    f"local orderbook archive missing for {symbol} {day} in {archive_dir}; "
                    "heavy downloads are disabled"
                )
            discovery = discover_archive(symbol, day)
            selected = discovery["selected"]
            if selected is None:
                missing = {
                    "day": day.isoformat(),
                    "missing": True,
                    "probes": discovery["probes"],
                }
                _write_csv(cache, [])
                _write_json(stats_path, missing)
                day_stats.append(missing)
                continue
            url = str(selected["url"])
            filename = url.rsplit("/", 1)[-1]
            target = archive_dir / filename
            size = cast(int | None, selected.get("content_length"))
            if size is not None:
                free = shutil.disk_usage(archive_dir).free
                if free < size + 512 * 1024 * 1024:
                    raise OSError(f"not enough free disk space for {filename}")
            print(f"  download remote orderbook archive: {filename}")
            archive_path = download_archive(url, target, expected_size=size)
            archive_depth = int(selected["depth"])

        day_windows = [window for window in windows if window.day == day]
        trade_path = _trade_archive_path(dataset_dir, symbol, day)
        pending.append(
            (day, archive_path, trade_path, archive_depth, filename, day_windows)
        )

    workers = _orderbook_worker_count()
    if workers > 1 and len(pending) > 1:
        print(f"P40 parallel analysis: workers={workers} pending_days={len(pending)}")
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _analyze_absorption_day_task,
                    archive_path,
                    trade_path,
                    day_windows,
                    archive_depth,
                ): (day, archive_path, filename)
                for (
                    day,
                    archive_path,
                    trade_path,
                    archive_depth,
                    filename,
                    day_windows,
                ) in pending
            }
            completed_pending = 0
            for future in as_completed(futures):
                day, archive_path, filename = futures[future]
                rows, stats = future.result()
                stats = {"day": day.isoformat(), **stats}
                _write_csv(_day_cache_path(output_dir, day), rows)
                _write_json(_day_stats_path(output_dir, day), stats)
                all_rows.extend(rows)
                day_stats.append(stats)
                completed_pending += 1  # noqa: SIM113
                print(
                    f"P40 parallel completed {completed_pending}/{len(pending)}: {day}",
                    flush=True,
                )
                if not keep_archives:
                    archive_path.unlink(missing_ok=True)
                    print(f"  processed and removed raw archive: {filename}")
    else:
        for pending_index, (
            day,
            archive_path,
            trade_path,
            archive_depth,
            filename,
            day_windows,
        ) in enumerate(pending, start=1):
            rows, stats = _analyze_absorption_day_task(
                archive_path, trade_path, day_windows, archive_depth
            )
            stats = {"day": day.isoformat(), **stats}
            _write_csv(_day_cache_path(output_dir, day), rows)
            _write_json(_day_stats_path(output_dir, day), stats)
            all_rows.extend(rows)
            day_stats.append(stats)
            print(
                f"P40 completed {pending_index}/{len(pending)}: {day}",
                flush=True,
            )
            if not keep_archives:
                archive_path.unlink(missing_ok=True)
                print(f"  processed and removed raw archive: {filename}")

    all_rows.sort(key=lambda row: str(row.get("touch_at", "")))
    _write_csv(output_dir / "absorption_features.csv", all_rows)
    _write_csv(output_dir / "absorption_quartiles.csv", build_quartile_rows(all_rows))
    _write_csv(output_dir / "absorption_states.csv", build_state_rows(all_rows))
    _write_csv(output_dir / "monthly_stability.csv", build_monthly_stability(all_rows))
    baseline = {label: _outcome_counts(all_rows, label) for label in OUTCOME_LABELS}
    result = {
        "architecture": "p40_orderbook_event_absorption",
        "p39_dir": str(p39_dir),
        "p37_dir": str(p37_dir),
        "p36_dir": str(p36_dir),
        "dataset_dir": str(dataset_dir),
        "archive_dir": str(archive_dir),
        "keep_archives": keep_archives,
        "window_seconds": WINDOW_SECONDS,
        "planned_days": len(days),
        "processed_days": len(day_stats),
        "feature_rows": len(all_rows),
        "missing_days": [item["day"] for item in day_stats if item.get("missing")],
        "baseline_outcomes": baseline,
        "notes": [
            "P40 changes no live trading, stop-loss, take-profit, exit, leverage, "
            "or risk-engine logic.",
            "P40 studies only the final 30 seconds before the already-defined exact touch.",
            "Orderbook add/remove is a visible-book update, not proof of an execution "
            "or cancellation cause.",
            "Same-price refill means quantity returned after a prior reduction at the "
            "same level inside the window.",
            "Public taker trades are joined to matching-engine orderbook activity "
            "without using post-touch data.",
            "All ratios, states, and quartiles are descriptive research outputs and "
            "are not trading gates.",
        ],
    }
    _write_json(output_dir / "summary.json", result)
    _write_json(
        output_dir / "run_state.json",
        {
            "architecture": "p40_orderbook_event_absorption",
            "p39_dir": str(p39_dir),
            "complete": len(day_stats) == len(days),
            "processed_days": len(day_stats),
            "expected_days": len(days),
            "feature_rows": len(all_rows),
        },
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P40 event-level orderbook absorption research")
    parser.add_argument("--p39-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--keep-archives", action="store_true")
    parser.add_argument("--max-days", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path.cwd()
    p39_dir = args.p39_dir or _latest_dir(root, "entry_research_v12", "summary.json")
    p39_summary = json.loads((p39_dir / "summary.json").read_text(encoding="utf-8"))
    p37_dir = Path(str(p39_summary["p37_dir"]))
    p36_dir = Path(str(p39_summary["p36_dir"]))
    windows = load_windows(p37_dir, p36_dir, pilot_only=False)
    symbol = windows[0].symbol if windows else "UNIUSDT"
    output_dir = resolve_output_dir(root, symbol, p39_dir, args.output_dir)
    result = run_absorption(
        p39_dir=p39_dir,
        output_dir=output_dir,
        archive_dir=args.archive_dir,
        keep_archives=args.keep_archives,
        max_days=args.max_days,
    )
    print(f"P39 source: {p39_dir}")
    print(f"Absorption days processed: {result['processed_days']}/{result['planned_days']}")
    print(f"Absorption feature rows: {result['feature_rows']}")
    print(f"Report: {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
