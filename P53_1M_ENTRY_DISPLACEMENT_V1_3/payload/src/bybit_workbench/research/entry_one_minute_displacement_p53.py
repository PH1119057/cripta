from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import json
import math
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast

from bybit_workbench.domain.models import Candle
from bybit_workbench.research.exit_break_even_v13 import (
    CoreSignal,
    TradeDayCache,
    build_path_series,
)
from bybit_workbench.research.first_retest_stop_anatomy_p49 import (
    ALL_SYMBOLS,
    EXPECTED_SIGNALS,
    PERIOD_TAG,
    discover_sources,
    load_all_signals,
)
from bybit_workbench.research.flow_reversal_v1 import _archive_map
from bybit_workbench.research.mtf_entry import Direction, _fingerprint
from bybit_workbench.research.mtf_entry_v2 import _zone_gap_percent
from bybit_workbench.research.mtf_entry_v3 import (
    ZoneV3,
    _precompute_post_shock_zones,
    _read_candles,
)
from bybit_workbench.strategies.indicators import true_ranges, wilder_atr

P53_VERSION = "P53_1M_ENTRY_DISPLACEMENT_V1_3"
CACHE_VERSION = "P53_1M_OHLC_CACHE_V3"
PRIMARY_SNAPSHOT = "pre_touch"
EXPECTED_PERIOD_START = "2026-05-18T00:00:00+00:00"
EXPECTED_PERIOD_END = "2026-08-16T00:00:00+00:00"
SHIFT_ZERO_TOL_PCT = 1e-8
NEAR_SAME_PCT = 0.01
DEFAULT_HORIZON_HOURS = 3

SnapshotName = Literal["candidate", "pre_touch"]
ShiftClass = Literal["deeper", "same", "outward", "no_1m_zone"]
Availability = Literal[
    "touched_within_3h",
    "missed_within_3h",
    "right_censored",
    "not_applicable",
]


@dataclass(frozen=True, slots=True)
class P53Config:
    expected_signals: int = EXPECTED_SIGNALS
    horizon_hours: int = DEFAULT_HORIZON_HOURS
    day_cache_size: int = 4
    progress_interval_seconds: float = 25.0

    def __post_init__(self) -> None:
        if self.expected_signals != EXPECTED_SIGNALS:
            raise ValueError("P53 V1.3 is frozen to the 1063 Entry V1 ALL9 cohort")
        if self.horizon_hours != DEFAULT_HORIZON_HOURS:
            raise ValueError("P53 V1.3 deeper-entry availability horizon is frozen to 3 hours")
        if self.day_cache_size <= 0:
            raise ValueError("day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class FrozenZoneConfig:
    five_minute_lookback: int
    fifteen_minute_lookback: int
    atr_period: int
    zone_half_width_atr: Decimal
    confluence_max_gap_percent: Decimal
    shock_atr_period: int
    shock_atr_multiple: Decimal
    embargo_minutes_after_shock: int


@dataclass(frozen=True, slots=True)
class BaselineGeometry:
    candidate_bar_at: datetime
    reference_price: Decimal
    five_zone: ZoneV3
    fifteen_zone: ZoneV3
    recreated_entry: Decimal
    original_gap_pct: Decimal


@dataclass(frozen=True, slots=True)
class OneMinuteSnapshot:
    name: SnapshotName
    observed_at: datetime
    zone: ZoneV3 | None
    entry_price: Decimal | None
    raw_price_shift_pct: float | None
    directional_shift_pct: float | None
    shift_class: ShiftClass
    one_vs_five_gap_pct: float | None
    one_vs_fifteen_gap_pct: float | None
    confluent_with_five: bool | None
    confluent_with_fifteen: bool | None
    strict_three_tf_confluent: bool | None


@dataclass(frozen=True, slots=True)
class DisplacementRecord:
    symbol: str
    direction: Direction
    candidate_bar_at: datetime
    touch_at: datetime
    baseline_entry_price: float
    reference_price: float
    fifteen_zone_low: float
    fifteen_zone_high: float
    five_zone_low: float
    five_zone_high: float
    original_zone_gap_pct: float
    candidate: OneMinuteSnapshot
    pre_touch: OneMinuteSnapshot
    deeper_entry_availability: Availability
    seconds_to_deeper_entry: float | None
    complete_3h: bool | None


class Heartbeat:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self.last_emit = 0.0

    def emit(
        self,
        stage: str,
        *,
        processed: int,
        total: int,
        detail: str = "",
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and now - self.last_emit < self.interval_seconds:
            return
        elapsed = max(0.0, now - self.started)
        eta: float | None = None
        if processed > 0 and total > processed:
            eta = elapsed / processed * (total - processed)
        pct = 0.0 if total <= 0 else processed * 100.0 / total
        eta_text = "n/a" if eta is None else _duration(eta)
        suffix = f" | {detail}" if detail else ""
        print(
            f"[P53] stage={stage} processed={processed}/{total} ({pct:.1f}%) "
            f"elapsed={_duration(elapsed)} ETA={eta_text}{suffix}",
            flush=True,
        )
        self.last_emit = now


def _duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return cast(dict[str, Any], payload)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON type: {type(value)!r}")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty machine-truth CSV: {path}")
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _parse_datetime(raw: str) -> datetime:
    return datetime.fromisoformat(raw).astimezone(UTC)


def _required(row: dict[str, str], key: str) -> str:
    value = row.get(key, "").strip()
    if not value:
        raise ValueError(f"missing required field {key}")
    return value


def _parse_timestamp_seconds(raw: str) -> Decimal:
    value = Decimal(raw.strip())
    absolute = abs(value)
    if absolute >= Decimal("1e18"):
        return value / Decimal("1e9")
    if absolute >= Decimal("1e15"):
        return value / Decimal("1e6")
    if absolute >= Decimal("1e11"):
        return value / Decimal("1e3")
    return value


def _minute_from_timestamp(raw: str) -> datetime:
    seconds = _parse_timestamp_seconds(raw)
    epoch_minute = int(seconds // Decimal("60")) * 60
    return datetime.fromtimestamp(epoch_minute, UTC)


def _aggregate_trade_archive(
    archive_path: Path,
    *,
    symbol: str,
    day: date,
    seed_price: Decimal | None = None,
    heartbeat: Heartbeat | None = None,
) -> tuple[Candle, ...]:
    observed: list[Candle] = []
    current_minute: datetime | None = None
    current_open = Decimal("0")
    current_high = Decimal("0")
    current_low = Decimal("0")
    current_close = Decimal("0")
    current_volume = Decimal("0")
    previous_seconds: Decimal | None = None
    rows_seen = 0

    def flush() -> None:
        nonlocal current_minute
        if current_minute is None:
            return
        observed.append(
            Candle(
                symbol=symbol,
                timeframe="1",
                opened_at=current_minute,
                closed_at=current_minute + timedelta(minutes=1),
                open=current_open,
                high=current_high,
                low=current_low,
                close=current_close,
                volume=current_volume,
                is_closed=True,
            )
        )

    with gzip.open(archive_path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"trade archive has no header: {archive_path}")
        names = {name.strip().lower(): name for name in reader.fieldnames if name}
        required = {"timestamp", "price", "size"}
        if not required.issubset(names):
            raise ValueError(f"unsupported trade archive header: {archive_path}")
        for raw in reader:
            rows_seen += 1
            try:
                seconds = _parse_timestamp_seconds(str(raw[names["timestamp"]]))
                minute = _minute_from_timestamp(str(raw[names["timestamp"]]))
                price = Decimal(str(raw[names["price"]]).strip())
                size = Decimal(str(raw[names["size"]]).strip())
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise ValueError(f"invalid trade row in {archive_path.name}: {raw}") from exc
            if previous_seconds is not None and seconds < previous_seconds:
                raise ValueError(f"trade timestamps are not monotonic: {archive_path}")
            previous_seconds = seconds
            if minute.date() != day:
                raise ValueError(
                    f"trade timestamp escapes archive UTC day {day}: "
                    f"{minute.isoformat()} in {archive_path.name}"
                )
            if price <= 0 or size <= 0:
                raise ValueError(f"invalid trade price/size in {archive_path.name}")
            if current_minute != minute:
                flush()
                current_minute = minute
                current_open = price
                current_high = price
                current_low = price
                current_close = price
                current_volume = size
            else:
                current_high = max(current_high, price)
                current_low = min(current_low, price)
                current_close = price
                current_volume += size
            if heartbeat is not None and rows_seen % 100_000 == 0:
                heartbeat.emit(
                    "aggregate_1m",
                    processed=0,
                    total=1,
                    detail=f"{symbol} {day.isoformat()} rows={rows_seen}",
                )
    flush()
    if not observed:
        raise ValueError(f"trade archive contains no trades: {archive_path}")

    expected_start = datetime.combine(day, datetime.min.time(), UTC)
    observed_by_minute = {item.opened_at: item for item in observed}
    if len(observed_by_minute) != len(observed):
        raise ValueError(f"duplicate 1m trade buckets after aggregation: {archive_path}")

    candles: list[Candle] = []
    previous_close = seed_price
    for index in range(24 * 60):
        opened_at = expected_start + timedelta(minutes=index)
        candle = observed_by_minute.get(opened_at)
        if candle is not None:
            if previous_close is None:
                completed = candle
            else:
                completed = Candle(
                    symbol=symbol,
                    timeframe="1",
                    opened_at=opened_at,
                    closed_at=opened_at + timedelta(minutes=1),
                    open=previous_close,
                    high=max(previous_close, candle.high),
                    low=min(previous_close, candle.low),
                    close=candle.close,
                    volume=candle.volume,
                    is_closed=True,
                )
            candles.append(completed)
            previous_close = completed.close
            continue
        if previous_close is None:
            raise ValueError(
                "cannot causally represent leading zero-trade minute without previous close: "
                f"{symbol} {opened_at.isoformat()}"
            )
        candles.append(
            Candle(
                symbol=symbol,
                timeframe="1",
                opened_at=opened_at,
                closed_at=opened_at + timedelta(minutes=1),
                open=previous_close,
                high=previous_close,
                low=previous_close,
                close=previous_close,
                volume=Decimal("0"),
                is_closed=True,
            )
        )

    if len(candles) != 24 * 60:
        raise AssertionError("internal error: completed 1m grid must contain 1440 candles")
    for index, candle in enumerate(candles):
        expected = expected_start + timedelta(minutes=index)
        if candle.opened_at != expected:
            raise ValueError(
                f"1m candle cadence mismatch for {symbol} {day}: "
                f"expected={expected.isoformat()} got={candle.opened_at.isoformat()}"
            )
    return tuple(candles)


def _write_candle_cache(path: Path, candles: tuple[Candle, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8-sig", newline="") as handle:
        fields = ["opened_at", "closed_at", "open", "high", "low", "close", "volume"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in candles:
            writer.writerow(
                {
                    "opened_at": item.opened_at.isoformat(),
                    "closed_at": item.closed_at.isoformat(),
                    "open": item.open,
                    "high": item.high,
                    "low": item.low,
                    "close": item.close,
                    "volume": item.volume,
                }
            )


def _read_candle_cache(path: Path, *, symbol: str) -> tuple[Candle, ...]:
    items: list[Candle] = []
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            items.append(
                Candle(
                    symbol=symbol,
                    timeframe="1",
                    opened_at=_parse_datetime(str(row["opened_at"])),
                    closed_at=_parse_datetime(str(row["closed_at"])),
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=Decimal(str(row["volume"])),
                    is_closed=True,
                )
            )
    return tuple(items)


def _cache_day(
    *,
    archive_path: Path,
    expected_archive_sha256: str,
    manifest_sha256: str,
    cache_dir: Path,
    symbol: str,
    day: date,
    seed_price: Decimal | None,
    heartbeat: Heartbeat,
) -> tuple[tuple[Candle, ...], int]:
    cache_path = cache_dir / symbol / f"{day.isoformat()}.csv.gz"
    meta_path = cache_path.with_suffix(cache_path.suffix + ".json")
    if cache_path.is_file() and meta_path.is_file() and archive_path.is_file():
        meta = _read_json(meta_path)
        compatible = (
            meta.get("cache_version") == CACHE_VERSION
            and meta.get("manifest_sha256") == manifest_sha256
            and meta.get("source_archive_sha256") == expected_archive_sha256
            and meta.get("cache_sha256") == _sha256(cache_path)
            and int(meta.get("rows", 0)) == 1440
            and meta.get("candle_semantics") == "previous_close_continuous_open_flat_zero_volume"
        )
        if compatible:
            candles = _read_candle_cache(cache_path, symbol=symbol)
            return candles, int(meta.get("zero_trade_minutes", 0))

    if not archive_path.is_file():
        raise FileNotFoundError(f"frozen public trade archive missing: {archive_path}")
    actual_sha = _sha256(archive_path)
    if actual_sha != expected_archive_sha256:
        raise ValueError(
            f"frozen public trade archive SHA256 mismatch: {archive_path.name}; "
            f"manifest={expected_archive_sha256} actual={actual_sha}"
        )
    candles = _aggregate_trade_archive(
        archive_path,
        symbol=symbol,
        day=day,
        seed_price=seed_price,
        heartbeat=heartbeat,
    )
    zero_trade_minutes = sum(1 for item in candles if item.volume == 0)
    _write_candle_cache(cache_path, candles)
    _write_json(
        meta_path,
        {
            "cache_version": CACHE_VERSION,
            "symbol": symbol,
            "day": day,
            "manifest_sha256": manifest_sha256,
            "source_archive": archive_path.name,
            "source_archive_sha256": actual_sha,
            "cache_sha256": _sha256(cache_path),
            "rows": len(candles),
            "candle_semantics": "previous_close_continuous_open_flat_zero_volume",
            "zero_trade_minutes": zero_trade_minutes,
        },
    )
    return candles, zero_trade_minutes


def _load_one_minute_dataset(
    *,
    dataset_dir: Path,
    symbol: str,
    cache_dir: Path,
    heartbeat: Heartbeat,
) -> tuple[tuple[Candle, ...], dict[str, Any]]:
    manifest_path = dataset_dir / "dataset_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"dataset manifest missing: {manifest_path}")
    manifest = _read_json(manifest_path)
    if str(manifest.get("symbol", "")).upper() != symbol:
        raise ValueError(f"dataset symbol mismatch for {symbol}: {manifest_path}")
    if str(manifest.get("evaluation_start")) != EXPECTED_PERIOD_START:
        raise ValueError(f"unexpected frozen evaluation_start for {symbol}")
    if str(manifest.get("evaluation_end")) != EXPECTED_PERIOD_END:
        raise ValueError(f"unexpected frozen evaluation_end for {symbol}")
    archive_hashes = manifest.get("public_trade_archives")
    if not isinstance(archive_hashes, dict) or not archive_hashes:
        raise ValueError(
            "dataset manifest has no public trade archive fingerprints: "
            f"{manifest_path}"
        )
    expected_hashes = {str(key): str(value) for key, value in archive_hashes.items()}
    archive_by_day = _archive_map(dataset_dir)
    manifest_sha = _sha256(manifest_path)
    days: list[tuple[date, Path, str]] = []
    for filename, expected_sha in expected_hashes.items():
        prefix = symbol
        suffix = ".csv.gz"
        if not filename.startswith(prefix) or not filename.endswith(suffix):
            raise ValueError(f"unexpected archive name in manifest: {filename}")
        day_text = filename[len(prefix) : -len(suffix)]
        day = date.fromisoformat(day_text)
        archive_path = archive_by_day.get(day_text)
        if archive_path is None:
            raise FileNotFoundError(f"frozen public trade archive missing for {symbol} {day_text}")
        if archive_path.name != filename:
            raise ValueError(
                f"archive filename mismatch for {symbol} {day_text}: "
                f"{archive_path.name} != {filename}"
            )
        days.append((day, archive_path, expected_sha))
    days.sort(key=lambda item: item[0])

    all_candles: list[Candle] = []
    zero_trade_minutes_total = 0
    previous_close: Decimal | None = None
    total = len(days)
    for index, (day, archive_path, expected_sha) in enumerate(days, start=1):
        heartbeat.emit(
            "one_minute_cache",
            processed=index - 1,
            total=total,
            detail=f"symbol={symbol} day={day.isoformat()}",
            force=index == 1,
        )
        candles, zero_trade_minutes = _cache_day(
            archive_path=archive_path,
            expected_archive_sha256=expected_sha,
            manifest_sha256=manifest_sha,
            cache_dir=cache_dir,
            symbol=symbol,
            day=day,
            seed_price=previous_close,
            heartbeat=heartbeat,
        )
        all_candles.extend(candles)
        zero_trade_minutes_total += zero_trade_minutes
        previous_close = candles[-1].close
    heartbeat.emit(
        "one_minute_cache",
        processed=total,
        total=total,
        detail=f"symbol={symbol} complete",
        force=True,
    )
    if not all_candles:
        raise ValueError(f"no one-minute candles built for {symbol}")
    for previous, current in zip(all_candles, all_candles[1:], strict=False):
        if current.opened_at != previous.closed_at:
            raise ValueError(
                f"non-contiguous 1m dataset for {symbol}: "
                f"{previous.closed_at.isoformat()} -> {current.opened_at.isoformat()}"
            )
    return tuple(all_candles), {
        "dataset_manifest": str(manifest_path),
        "dataset_manifest_sha256": manifest_sha,
        "archive_days": len(days),
        "one_minute_rows": len(all_candles),
        "zero_trade_minutes": zero_trade_minutes_total,
        "candle_semantics": "previous_close_continuous_open_flat_zero_volume",
        "first_minute": all_candles[0].opened_at,
        "last_minute": all_candles[-1].opened_at,
    }


def _aggregate_five_from_one(candles: tuple[Candle, ...]) -> dict[datetime, tuple[Decimal, ...]]:
    buckets: dict[datetime, list[Candle]] = defaultdict(list)
    for item in candles:
        opened = item.opened_at
        bucket_minute = (opened.minute // 5) * 5
        bucket = opened.replace(minute=bucket_minute, second=0, microsecond=0)
        buckets[bucket].append(item)
    output: dict[datetime, tuple[Decimal, ...]] = {}
    for opened_at, rows in buckets.items():
        rows.sort(key=lambda item: item.opened_at)
        if len(rows) != 5:
            continue
        output[opened_at] = (
            rows[0].open,
            max(item.high for item in rows),
            min(item.low for item in rows),
            rows[-1].close,
            sum((item.volume for item in rows), Decimal("0")),
        )
    return output


def validate_five_minute_equivalence(
    one_minute: tuple[Candle, ...],
    five_minute: tuple[Candle, ...],
) -> dict[str, Any]:
    derived = _aggregate_five_from_one(one_minute)
    compared = 0
    mismatches: list[dict[str, str]] = []
    for candle in five_minute:
        values = derived.get(candle.opened_at)
        if values is None:
            continue
        compared += 1
        expected = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if values != expected:
            mismatches.append(
                {
                    "opened_at": candle.opened_at.isoformat(),
                    "derived": "|".join(str(value) for value in values),
                    "bybit_5m": "|".join(str(value) for value in expected),
                }
            )
            if len(mismatches) >= 10:
                break
    if compared <= 0:
        raise ValueError("1m->5m equivalence gate had zero comparable candles")
    if mismatches:
        raise ValueError(
            "trade-derived 1m OHLCV does not reproduce frozen Bybit 5m OHLCV; "
            f"first mismatches={mismatches}"
        )
    return {"compared_5m_candles": compared, "ohlcv_mismatches": 0}


def _frozen_zone_config(p31_summary: dict[str, Any]) -> FrozenZoneConfig:
    raw = p31_summary.get("base_p30_config")
    if not isinstance(raw, dict):
        raise ValueError("P31 summary has no frozen base_p30_config")
    return FrozenZoneConfig(
        five_minute_lookback=int(raw["five_minute_lookback"]),
        fifteen_minute_lookback=int(raw["fifteen_minute_lookback"]),
        atr_period=int(raw["atr_period"]),
        zone_half_width_atr=Decimal(str(raw["zone_half_width_atr"])),
        confluence_max_gap_percent=Decimal(str(raw["confluence_max_gap_percent"])),
        shock_atr_period=int(raw["shock_atr_period"]),
        shock_atr_multiple=Decimal(str(raw["shock_atr_multiple"])),
        embargo_minutes_after_shock=int(raw["embargo_minutes_after_shock"]),
    )


def _validate_frozen_dataset(
    *,
    dataset_dir: Path,
    symbol: str,
) -> tuple[tuple[Candle, ...], tuple[Candle, ...]]:
    manifest = _read_json(dataset_dir / "dataset_manifest.json")
    five = _read_candles(dataset_dir / "trade_5m.csv", symbol=symbol, timeframe="5")
    fifteen = _read_candles(dataset_dir / "trade_15m.csv", symbol=symbol, timeframe="15")
    checks = {
        "five_minute_fingerprint": _fingerprint(five),
        "fifteen_minute_fingerprint": _fingerprint(fifteen),
    }
    for key, actual in checks.items():
        if str(manifest.get(key)) != actual:
            raise ValueError(f"frozen dataset fingerprint mismatch for {symbol}: {key}")
    return five, fifteen


def _history_len(closed_times: list[datetime], observed_at: datetime) -> int:
    return bisect.bisect_right(closed_times, observed_at)


def _candidate_bar(candles: tuple[Candle, ...], opened_at: datetime) -> Candle:
    opens = [item.opened_at for item in candles]
    index = bisect.bisect_left(opens, opened_at)
    if index >= len(candles) or candles[index].opened_at != opened_at:
        raise ValueError(f"candidate 5m bar not found: {opened_at.isoformat()}")
    return candles[index]


def _direction_zone_bounds(zone: ZoneV3, direction: Direction) -> tuple[Decimal, Decimal]:
    if direction == "Long":
        return zone.support_bottom, zone.support_top
    return zone.resistance_bottom, zone.resistance_top


def _direction_entry(zone: ZoneV3, direction: Direction) -> Decimal:
    return zone.support_top if direction == "Long" else zone.resistance_bottom


def _baseline_geometry(
    signal: CoreSignal,
    *,
    five: tuple[Candle, ...],
    fifteen: tuple[Candle, ...],
    five_zones: tuple[ZoneV3 | None, ...],
    fifteen_zones: tuple[ZoneV3 | None, ...],
    config: FrozenZoneConfig,
) -> BaselineGeometry:
    candidate_at = _parse_datetime(_required(signal.source_row, "candidate_bar_at"))
    five_closed = [item.closed_at for item in five]
    fifteen_closed = [item.closed_at for item in fifteen]
    five_len = _history_len(five_closed, candidate_at)
    fifteen_len = _history_len(fifteen_closed, candidate_at)
    if five_len >= len(five_zones) or fifteen_len >= len(fifteen_zones):
        raise ValueError("baseline zone index out of range")
    five_zone = five_zones[five_len]
    fifteen_zone = fifteen_zones[fifteen_len]
    if five_zone is None or fifteen_zone is None:
        raise ValueError(
            f"frozen baseline zone cannot be recreated for {signal.symbol} {candidate_at}"
        )
    bar = _candidate_bar(five, candidate_at)
    recreated = _direction_entry(five_zone, signal.direction)
    original_gap = _zone_gap_percent(
        *_direction_zone_bounds(fifteen_zone, signal.direction),
        *_direction_zone_bounds(five_zone, signal.direction),
        bar.open,
    )
    if original_gap > config.confluence_max_gap_percent:
        raise ValueError(
            f"recreated frozen 15m/5m confluence fails for {signal.symbol} "
            f"{candidate_at.isoformat()}: {original_gap}"
        )
    baseline = Decimal(str(signal.entry_price))
    diff_pct = abs(recreated / baseline - Decimal("1")) * Decimal("100")
    if diff_pct > Decimal("0.00000001"):
        raise ValueError(
            f"frozen entry price mismatch for {signal.symbol} {candidate_at.isoformat()}: "
            f"P40={baseline} recreated={recreated} diff_pct={diff_pct}"
        )
    return BaselineGeometry(
        candidate_bar_at=candidate_at,
        reference_price=bar.open,
        five_zone=five_zone,
        fifteen_zone=fifteen_zone,
        recreated_entry=recreated,
        original_gap_pct=original_gap,
    )


class OneMinuteZoneEngine:
    def __init__(self, candles: tuple[Candle, ...], config: FrozenZoneConfig) -> None:
        self.candles = candles
        self.config = config
        self.closed_times = [item.closed_at for item in candles]
        self.ranges = true_ranges(candles)
        self.atr_values = wilder_atr(candles, config.atr_period)
        self.shock_flags = self._shock_flags()

    def _shock_flags(self) -> list[bool]:
        count = len(self.candles)
        flags = [False] * count
        period = self.config.shock_atr_period
        if period <= 0 or count <= period:
            return flags
        rolling = sum(self.ranges[:period], Decimal("0"))
        for index in range(period, count):
            baseline = rolling / Decimal(period)
            flags[index] = (
                baseline > 0
                and self.ranges[index] >= self.config.shock_atr_multiple * baseline
            )
            rolling += self.ranges[index] - self.ranges[index - period]
        return flags

    def history_len(self, observed_at: datetime) -> int:
        return _history_len(self.closed_times, observed_at)

    def zone_at(self, observed_at: datetime) -> ZoneV3 | None:
        history_len = self.history_len(observed_at)
        lookback = self.config.five_minute_lookback
        minimum_history = max(lookback, self.config.atr_period)
        if history_len < minimum_history:
            return None
        last_index = history_len - 1
        atr = self.atr_values[last_index]
        if atr is None or atr <= 0:
            return None
        window_start = history_len - lookback
        reset_index: int | None = None
        shock_period = self.config.shock_atr_period
        for index in range(last_index, max(window_start, shock_period) - 1, -1):
            if self.shock_flags[index]:
                reset_index = index
                break
        selected_start = window_start
        reset_at: datetime | None = None
        minimum_regime_bars = max(1, self.config.embargo_minutes_after_shock)
        if reset_index is not None:
            selected_start = reset_index + 1
            if history_len - selected_start < minimum_regime_bars:
                return None
            reset_at = self.candles[reset_index].closed_at
        selected = self.candles[selected_start:history_len]
        if not selected:
            return None
        range_high = max(item.high for item in selected)
        range_low = min(item.low for item in selected)
        width = self.config.zone_half_width_atr * atr
        support_top = range_low + width
        support_bottom = range_low - width
        resistance_top = range_high + width
        resistance_bottom = range_high - width
        if support_top >= resistance_bottom:
            return None
        return ZoneV3(
            timeframe="1",
            observed_at=self.candles[last_index].closed_at,
            range_high=range_high,
            range_low=range_low,
            atr=atr,
            resistance_top=resistance_top,
            resistance_bottom=resistance_bottom,
            support_top=support_top,
            support_bottom=support_bottom,
            effective_lookback=len(selected),
            regime_reset_at=reset_at,
        )


def directional_shift_pct(
    direction: Direction,
    baseline_entry: Decimal,
    hypothetical_entry: Decimal,
) -> float:
    raw = (hypothetical_entry / baseline_entry - Decimal("1")) * Decimal("100")
    directed = raw if direction == "Long" else -raw
    return float(directed)


def classify_shift(value: float | None) -> ShiftClass:
    if value is None:
        return "no_1m_zone"
    if abs(value) <= SHIFT_ZERO_TOL_PCT:
        return "same"
    return "deeper" if value < 0 else "outward"


def _snapshot(
    *,
    name: SnapshotName,
    observed_at: datetime,
    zone: ZoneV3 | None,
    direction: Direction,
    baseline_entry: Decimal,
    geometry: BaselineGeometry,
    config: FrozenZoneConfig,
) -> OneMinuteSnapshot:
    if zone is None:
        return OneMinuteSnapshot(
            name=name,
            observed_at=observed_at,
            zone=None,
            entry_price=None,
            raw_price_shift_pct=None,
            directional_shift_pct=None,
            shift_class="no_1m_zone",
            one_vs_five_gap_pct=None,
            one_vs_fifteen_gap_pct=None,
            confluent_with_five=None,
            confluent_with_fifteen=None,
            strict_three_tf_confluent=None,
        )
    entry = _direction_entry(zone, direction)
    raw = float((entry / baseline_entry - Decimal("1")) * Decimal("100"))
    directed = directional_shift_pct(direction, baseline_entry, entry)
    one_bounds = _direction_zone_bounds(zone, direction)
    five_bounds = _direction_zone_bounds(geometry.five_zone, direction)
    fifteen_bounds = _direction_zone_bounds(geometry.fifteen_zone, direction)
    one_five = _zone_gap_percent(*one_bounds, *five_bounds, geometry.reference_price)
    one_fifteen = _zone_gap_percent(*one_bounds, *fifteen_bounds, geometry.reference_price)
    five_ok = one_five <= config.confluence_max_gap_percent
    fifteen_ok = one_fifteen <= config.confluence_max_gap_percent
    return OneMinuteSnapshot(
        name=name,
        observed_at=observed_at,
        zone=zone,
        entry_price=entry,
        raw_price_shift_pct=raw,
        directional_shift_pct=directed,
        shift_class=classify_shift(directed),
        one_vs_five_gap_pct=float(one_five),
        one_vs_fifteen_gap_pct=float(one_fifteen),
        confluent_with_five=five_ok,
        confluent_with_fifteen=fifteen_ok,
        strict_three_tf_confluent=five_ok and fifteen_ok,
    )


def _touch_minute_start(touch_at: datetime) -> datetime:
    return touch_at.replace(second=0, microsecond=0)


def _zone_low_high(zone: ZoneV3, direction: Direction) -> tuple[float, float]:
    low, high = _direction_zone_bounds(zone, direction)
    return float(low), float(high)


def _make_record(
    signal: CoreSignal,
    *,
    geometry: BaselineGeometry,
    engine: OneMinuteZoneEngine,
    config: FrozenZoneConfig,
) -> DisplacementRecord:
    candidate_observed = geometry.candidate_bar_at
    pre_touch_observed = _touch_minute_start(signal.touch_at)
    candidate = _snapshot(
        name="candidate",
        observed_at=candidate_observed,
        zone=engine.zone_at(candidate_observed),
        direction=signal.direction,
        baseline_entry=geometry.recreated_entry,
        geometry=geometry,
        config=config,
    )
    pre_touch = _snapshot(
        name="pre_touch",
        observed_at=pre_touch_observed,
        zone=engine.zone_at(pre_touch_observed),
        direction=signal.direction,
        baseline_entry=geometry.recreated_entry,
        geometry=geometry,
        config=config,
    )
    fifteen_low, fifteen_high = _zone_low_high(geometry.fifteen_zone, signal.direction)
    five_low, five_high = _zone_low_high(geometry.five_zone, signal.direction)
    return DisplacementRecord(
        symbol=signal.symbol,
        direction=signal.direction,
        candidate_bar_at=geometry.candidate_bar_at,
        touch_at=signal.touch_at,
        baseline_entry_price=signal.entry_price,
        reference_price=float(geometry.reference_price),
        fifteen_zone_low=fifteen_low,
        fifteen_zone_high=fifteen_high,
        five_zone_low=five_low,
        five_zone_high=five_high,
        original_zone_gap_pct=float(geometry.original_gap_pct),
        candidate=candidate,
        pre_touch=pre_touch,
        deeper_entry_availability="not_applicable",
        seconds_to_deeper_entry=None,
        complete_3h=None,
    )


def _with_availability(
    record: DisplacementRecord,
    signal: CoreSignal,
    *,
    archive_by_day: dict[str, Path],
    cache: TradeDayCache,
    horizon_hours: int,
) -> DisplacementRecord:
    shift = record.pre_touch.directional_shift_pct
    if shift is None or record.pre_touch.shift_class != "deeper":
        return record
    path = build_path_series(
        signal,
        archive_by_day,
        horizon_hours=horizon_hours,
        cache=cache,
    )
    threshold = shift
    touch_ts = signal.touch_at.timestamp()
    seconds: float | None = None
    for timestamp, move in zip(path.timestamps, path.moves_pct, strict=True):
        if timestamp < touch_ts:
            continue
        if move <= threshold + SHIFT_ZERO_TOL_PCT:
            seconds = max(0.0, timestamp - touch_ts)
            break
    complete = path.complete_through >= signal.touch_at + timedelta(hours=horizon_hours)
    if seconds is not None:
        availability: Availability = "touched_within_3h"
    elif complete:
        availability = "missed_within_3h"
    else:
        availability = "right_censored"
    return replace(
        record,
        deeper_entry_availability=availability,
        seconds_to_deeper_entry=seconds,
        complete_3h=complete,
    )


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 6)
    fraction = position - lower
    value = ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction
    return round(value, 6)


def _pct(count: int, total: int) -> float:
    return 0.0 if total == 0 else round(count * 100.0 / total, 4)


def _shift_summary(
    records: list[DisplacementRecord], snapshot_name: SnapshotName
) -> dict[str, Any]:
    snapshots = [getattr(item, snapshot_name) for item in records]
    available = [item for item in snapshots if item.directional_shift_pct is not None]
    values = [cast(float, item.directional_shift_pct) for item in available]
    deeper = [item for item in available if item.shift_class == "deeper"]
    same = [item for item in available if item.shift_class == "same"]
    outward = [item for item in available if item.shift_class == "outward"]
    strict = [item for item in available if item.strict_three_tf_confluent is True]
    return {
        "cohort": len(records),
        "one_minute_zone_available": len(available),
        "one_minute_zone_unavailable": len(records) - len(available),
        "deeper": len(deeper),
        "deeper_pct_available": _pct(len(deeper), len(available)),
        "same_exact": len(same),
        "same_exact_pct_available": _pct(len(same), len(available)),
        "outward": len(outward),
        "outward_pct_available": _pct(len(outward), len(available)),
        "within_plus_minus_0p01_pct": sum(abs(value) <= NEAR_SAME_PCT for value in values),
        "strict_three_tf_confluent": len(strict),
        "strict_three_tf_confluent_pct_available": _pct(len(strict), len(available)),
        "shift_p10_pct": _quantile(values, 0.10),
        "shift_p25_pct": _quantile(values, 0.25),
        "shift_median_pct": _quantile(values, 0.50),
        "shift_p75_pct": _quantile(values, 0.75),
        "shift_p90_pct": _quantile(values, 0.90),
        "shift_min_pct": None if not values else round(min(values), 6),
        "shift_max_pct": None if not values else round(max(values), 6),
        "buckets": _shift_buckets(values),
    }


def _shift_buckets(values: list[float]) -> dict[str, int]:
    labels = {
        "lt_minus_0p50": 0,
        "minus_0p50_to_minus_0p25": 0,
        "minus_0p25_to_minus_0p10": 0,
        "minus_0p10_to_minus_0p01": 0,
        "within_plus_minus_0p01": 0,
        "plus_0p01_to_plus_0p10": 0,
        "plus_0p10_to_plus_0p25": 0,
        "plus_0p25_to_plus_0p50": 0,
        "gt_plus_0p50": 0,
    }
    for value in values:
        if value < -0.50:
            labels["lt_minus_0p50"] += 1
        elif value < -0.25:
            labels["minus_0p50_to_minus_0p25"] += 1
        elif value < -0.10:
            labels["minus_0p25_to_minus_0p10"] += 1
        elif value < -0.01:
            labels["minus_0p10_to_minus_0p01"] += 1
        elif value <= 0.01:
            labels["within_plus_minus_0p01"] += 1
        elif value <= 0.10:
            labels["plus_0p01_to_plus_0p10"] += 1
        elif value <= 0.25:
            labels["plus_0p10_to_plus_0p25"] += 1
        elif value <= 0.50:
            labels["plus_0p25_to_plus_0p50"] += 1
        else:
            labels["gt_plus_0p50"] += 1
    return labels


def _availability_summary(records: list[DisplacementRecord]) -> dict[str, Any]:
    deeper = [item for item in records if item.pre_touch.shift_class == "deeper"]
    touched = [item for item in deeper if item.deeper_entry_availability == "touched_within_3h"]
    missed = [item for item in deeper if item.deeper_entry_availability == "missed_within_3h"]
    censored = [item for item in deeper if item.deeper_entry_availability == "right_censored"]
    decisive = len(touched) + len(missed)
    times = [cast(float, item.seconds_to_deeper_entry) for item in touched]
    return {
        "deeper_candidates": len(deeper),
        "decisive_3h": decisive,
        "touched_within_3h": len(touched),
        "touched_pct_decisive": _pct(len(touched), decisive),
        "missed_within_3h": len(missed),
        "missed_pct_decisive": _pct(len(missed), decisive),
        "right_censored": len(censored),
        "seconds_to_deeper_entry_median": _quantile(times, 0.50),
        "seconds_to_deeper_entry_p75": _quantile(times, 0.75),
        "seconds_to_deeper_entry_p90": _quantile(times, 0.90),
    }


def _record_row(record: DisplacementRecord) -> dict[str, Any]:
    def snapshot_fields(prefix: str, item: OneMinuteSnapshot) -> dict[str, Any]:
        zone = item.zone
        return {
            f"{prefix}_observed_at": item.observed_at.isoformat(),
            f"{prefix}_zone_available": zone is not None,
            f"{prefix}_one_zone_low": (
                "" if zone is None else _direction_zone_bounds(zone, record.direction)[0]
            ),
            f"{prefix}_one_zone_high": (
                "" if zone is None else _direction_zone_bounds(zone, record.direction)[1]
            ),
            f"{prefix}_one_entry_price": "" if item.entry_price is None else item.entry_price,
            f"{prefix}_raw_price_shift_pct": item.raw_price_shift_pct,
            f"{prefix}_directional_shift_pct": item.directional_shift_pct,
            f"{prefix}_shift_class": item.shift_class,
            f"{prefix}_one_vs_five_gap_pct": item.one_vs_five_gap_pct,
            f"{prefix}_one_vs_fifteen_gap_pct": item.one_vs_fifteen_gap_pct,
            f"{prefix}_confluent_with_five": item.confluent_with_five,
            f"{prefix}_confluent_with_fifteen": item.confluent_with_fifteen,
            f"{prefix}_strict_three_tf_confluent": item.strict_three_tf_confluent,
            f"{prefix}_one_effective_lookback": "" if zone is None else zone.effective_lookback,
            f"{prefix}_one_regime_reset_at": (
                ""
                if zone is None or zone.regime_reset_at is None
                else zone.regime_reset_at.isoformat()
            ),
        }

    row: dict[str, Any] = {
        "symbol": record.symbol,
        "direction": record.direction,
        "candidate_bar_at": record.candidate_bar_at.isoformat(),
        "touch_at": record.touch_at.isoformat(),
        "baseline_entry_price": record.baseline_entry_price,
        "reference_price": record.reference_price,
        "fifteen_zone_low": record.fifteen_zone_low,
        "fifteen_zone_high": record.fifteen_zone_high,
        "five_zone_low": record.five_zone_low,
        "five_zone_high": record.five_zone_high,
        "original_zone_gap_pct": record.original_zone_gap_pct,
    }
    row.update(snapshot_fields("candidate", record.candidate))
    row.update(snapshot_fields("pre_touch", record.pre_touch))
    row.update(
        {
            "deeper_entry_availability": record.deeper_entry_availability,
            "seconds_to_deeper_entry": record.seconds_to_deeper_entry,
            "complete_3h": record.complete_3h,
        }
    )
    return row


def _summary_by_scope(records: list[DisplacementRecord]) -> dict[str, Any]:
    by_symbol: dict[str, Any] = {}
    for symbol in ALL_SYMBOLS:
        subset = [item for item in records if item.symbol == symbol]
        by_symbol[symbol] = {
            "candidate": _shift_summary(subset, "candidate"),
            "pre_touch": _shift_summary(subset, "pre_touch"),
            "deeper_availability": _availability_summary(subset),
        }
    by_direction: dict[str, Any] = {}
    for direction in ("Long", "Short"):
        subset = [item for item in records if item.direction == direction]
        by_direction[direction] = {
            "candidate": _shift_summary(subset, "candidate"),
            "pre_touch": _shift_summary(subset, "pre_touch"),
            "deeper_availability": _availability_summary(subset),
        }
    return {"by_symbol": by_symbol, "by_direction": by_direction}


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    primary = cast(dict[str, Any], summary["overall"][PRIMARY_SNAPSHOT])
    availability = cast(dict[str, Any], summary["overall"]["deeper_availability"])
    lines = [
        "# P53 — 1m Entry displacement",
        "",
        "Research only. Downloads: DISABLED. Entry/Exit/Risk/Execution are unchanged.",
        "",
        "## Primary answer",
        "",
        (
            f"Frozen cohort: **{summary['signals']}** Entry V1 signals. "
            f"1m zone available pre-touch: **{primary['one_minute_zone_available']}**."
        ),
        (
            f"Deeper (LONG lower / SHORT higher): **{primary['deeper']}** "
            f"({primary['deeper_pct_available']:.2f}% of available)."
        ),
        (
            f"Same exact: **{primary['same_exact']}**; outward: **{primary['outward']}** "
            f"({primary['outward_pct_available']:.2f}% of available)."
        ),
        f"Median side-normalized shift: **{primary['shift_median_pct']}%**.",
        (
            f"Strict 15m+5m+1m confluence at the same frozen threshold: "
            f"**{primary['strict_three_tf_confluent']}** signals."
        ),
        "",
        "## Deeper price availability",
        "",
        (
            f"Among deeper pre-touch levels with decisive 3h coverage: "
            f"touched **{availability['touched_within_3h']} / {availability['decisive_3h']}** "
            f"({availability['touched_pct_decisive']:.2f}%)."
        ),
        "",
        "## Sign convention",
        "",
        "- negative shift = deeper/adverse from current Entry (LONG lower, SHORT higher);",
        "- zero = same price;",
        "- positive shift = outward/in trade direction (LONG higher, SHORT lower).",
        "",
        "## Causal snapshots",
        "",
        "- candidate: only completed 1m candles before the frozen 5m candidate bar;",
        "- pre_touch: only completed 1m candles before the minute containing exact touch.",
        "",
        "The 1m zone reuses the frozen 5m bar-count/lookback, ATR width, shock multiple, "
        "and 60-minute post-shock maturity rule. No threshold was optimized in P53.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_p53(
    root: Path,
    *,
    output_dir: Path,
    config: P53Config,
) -> dict[str, Any]:
    root = root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / "cache_1m"
    heartbeat = Heartbeat(config.progress_interval_seconds)

    sources = discover_sources(root)
    signals = load_all_signals(sources)
    if len(signals) != config.expected_signals:
        raise ValueError(
            f"frozen Entry cohort mismatch: expected {config.expected_signals}, got {len(signals)}"
        )
    if set(item.symbol for item in signals) != set(ALL_SYMBOLS):
        raise ValueError("frozen Entry cohort symbol set mismatch")

    signals_by_symbol: dict[str, list[CoreSignal]] = defaultdict(list)
    for signal in signals:
        signals_by_symbol[signal.symbol].append(signal)

    records: list[DisplacementRecord] = []
    provenance: list[dict[str, Any]] = []
    total_symbols = len(ALL_SYMBOLS)
    for symbol_index, source in enumerate(sources, start=1):
        symbol = source.symbol
        heartbeat.emit(
            "symbol",
            processed=symbol_index - 1,
            total=total_symbols,
            detail=symbol,
            force=True,
        )
        validation_root = source.p40_dir.parent
        p31_summary_path = validation_root / "p31" / "summary.json"
        if not p31_summary_path.is_file():
            raise FileNotFoundError(f"P31 frozen summary missing: {p31_summary_path}")
        p31_summary = _read_json(p31_summary_path)
        zone_config = _frozen_zone_config(p31_summary)
        five, fifteen = _validate_frozen_dataset(dataset_dir=source.dataset_dir, symbol=symbol)
        one_minute, one_provenance = _load_one_minute_dataset(
            dataset_dir=source.dataset_dir,
            symbol=symbol,
            cache_dir=cache_dir,
            heartbeat=heartbeat,
        )
        equivalence = validate_five_minute_equivalence(one_minute, five)

        five_zones = _precompute_post_shock_zones(
            five,
            timeframe="5",
            lookback=zone_config.five_minute_lookback,
            atr_period=zone_config.atr_period,
            width_atr=zone_config.zone_half_width_atr,
            shock_atr_period=zone_config.shock_atr_period,
            shock_atr_multiple=zone_config.shock_atr_multiple,
            minimum_regime_bars=max(1, zone_config.embargo_minutes_after_shock // 5),
        )
        fifteen_zones = _precompute_post_shock_zones(
            fifteen,
            timeframe="15",
            lookback=zone_config.fifteen_minute_lookback,
            atr_period=zone_config.atr_period,
            width_atr=zone_config.zone_half_width_atr,
            shock_atr_period=zone_config.shock_atr_period,
            shock_atr_multiple=zone_config.shock_atr_multiple,
            minimum_regime_bars=max(1, zone_config.embargo_minutes_after_shock // 15),
        )
        engine = OneMinuteZoneEngine(one_minute, zone_config)
        symbol_signals = sorted(signals_by_symbol[symbol], key=lambda item: item.touch_at)
        symbol_records: list[DisplacementRecord] = []
        for index, signal in enumerate(symbol_signals, start=1):
            geometry = _baseline_geometry(
                signal,
                five=five,
                fifteen=fifteen,
                five_zones=five_zones,
                fifteen_zones=fifteen_zones,
                config=zone_config,
            )
            symbol_records.append(
                _make_record(signal, geometry=geometry, engine=engine, config=zone_config)
            )
            heartbeat.emit(
                "displacement",
                processed=index,
                total=len(symbol_signals),
                detail=f"symbol={symbol}",
            )

        archive_by_day = _archive_map(source.dataset_dir)
        trade_cache = TradeDayCache(max_days=config.day_cache_size)
        with_availability: list[DisplacementRecord] = []
        signal_map = {
            (item.direction, item.touch_at.isoformat()): item for item in symbol_signals
        }
        for index, record in enumerate(symbol_records, start=1):
            key = (record.direction, record.touch_at.isoformat())
            signal = signal_map[key]
            with_availability.append(
                _with_availability(
                    record,
                    signal,
                    archive_by_day=archive_by_day,
                    cache=trade_cache,
                    horizon_hours=config.horizon_hours,
                )
            )
            heartbeat.emit(
                "availability_3h",
                processed=index,
                total=len(symbol_records),
                detail=(
                    f"symbol={symbol} cache_hits={trade_cache.hits} "
                    f"cache_misses={trade_cache.misses}"
                ),
            )
        records.extend(with_availability)
        provenance.append(
            {
                "symbol": symbol,
                "p40_features": str(source.features_path),
                "p40_features_sha256": _sha256(source.features_path),
                "p40_summary_sha256": _sha256(source.summary_path),
                "p31_summary": str(p31_summary_path),
                "p31_summary_sha256": _sha256(p31_summary_path),
                "dataset_dir": str(source.dataset_dir),
                "frozen_zone_config": asdict(zone_config),
                "one_minute_parameter_transfer": {
                    "lookback_bars": zone_config.five_minute_lookback,
                    "atr_period_bars": zone_config.atr_period,
                    "zone_half_width_atr": zone_config.zone_half_width_atr,
                    "shock_atr_period_bars": zone_config.shock_atr_period,
                    "shock_atr_multiple": zone_config.shock_atr_multiple,
                    "post_shock_maturity_minutes": zone_config.embargo_minutes_after_shock,
                    "confluence_max_gap_percent": zone_config.confluence_max_gap_percent,
                },
                "one_minute": one_provenance,
                "five_minute_equivalence": equivalence,
            }
        )
        heartbeat.emit(
            "symbol",
            processed=symbol_index,
            total=total_symbols,
            detail=f"{symbol} complete",
            force=True,
        )

    records.sort(key=lambda item: (item.touch_at, item.symbol, item.direction))
    if len(records) != config.expected_signals:
        raise RuntimeError(f"P53 output signal mismatch: {len(records)}")

    overall = {
        "candidate": _shift_summary(records, "candidate"),
        "pre_touch": _shift_summary(records, "pre_touch"),
        "deeper_availability": _availability_summary(records),
    }
    summary: dict[str, Any] = {
        "research_version": P53_VERSION,
        "created_at": datetime.now(UTC),
        "downloads": "DISABLED",
        "period_tag": PERIOD_TAG,
        "evaluation_start": EXPECTED_PERIOD_START,
        "evaluation_end": EXPECTED_PERIOD_END,
        "symbols": list(ALL_SYMBOLS),
        "signals": len(records),
        "entry_fingerprint_status": (
            "frozen P40 exact-touch Entry prices recreated before 1m overlay"
        ),
        "primary_snapshot": PRIMARY_SNAPSHOT,
        "config": asdict(config),
        "sign_convention": (
            "negative = deeper/adverse (LONG lower, SHORT higher); "
            "positive = outward/in trade direction (LONG higher, SHORT lower)"
        ),
        "one_minute_semantics": {
            "source": "local frozen Bybit public-trade archives only",
            "candidate_snapshot": (
                "completed 1m candles with closed_at <= frozen candidate 5m bar open"
            ),
            "pre_touch_snapshot": (
                "completed 1m candles with closed_at <= start of exact-touch minute"
            ),
            "entry_edge": "1m support_top for LONG; 1m resistance_bottom for SHORT",
            "parameter_transfer": (
                "same bar-count/ATR/shock parameters as frozen 5m local zone; "
                "60-minute shock maturity converted to 60 one-minute bars"
            ),
            "no_optimizer": True,
        },
        "overall": overall,
        **_summary_by_scope(records),
        "provenance": provenance,
        "does_not_change": [
            "Entry V1 fingerprint",
            "Exit",
            "Risk",
            "Execution",
            "live runtime",
            "P46/NEW5 holdout",
        ],
    }
    rows = [_record_row(item) for item in records]
    _write_csv(output_dir / "entry_1m_displacement.csv", rows)
    _write_json(output_dir / "summary.json", summary)
    _write_markdown(output_dir / "SUMMARY_RU.md", summary)
    _write_json(
        output_dir / "provenance.json",
        {
            "research_version": P53_VERSION,
            "created_at": summary["created_at"],
            "sources": provenance,
            "machine_truth": {
                "entry_1m_displacement_csv_sha256": _sha256(
                    output_dir / "entry_1m_displacement.csv"
                ),
                "summary_json_sha256": _sha256(output_dir / "summary.json"),
            },
        },
    )
    print(f"P53 complete: {len(records)} frozen Entry signals")
    print(f"Output: {output_dir}")
    return summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "P53 research-only fixed-1063 1m Entry displacement overlay. "
            "Downloads are disabled."
        )
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir")
    parser.add_argument("--expected-signals", type=int, default=EXPECTED_SIGNALS)
    parser.add_argument("--horizon-hours", type=int, default=DEFAULT_HORIZON_HOURS)
    parser.add_argument("--day-cache-size", type=int, default=4)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else root / "reports" / "entry_1m_displacement_p53" / "ALL9_P53_WORKING"
    )
    config = P53Config(
        expected_signals=args.expected_signals,
        horizon_hours=args.horizon_hours,
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    run_p53(root, output_dir=output_dir, config=config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
