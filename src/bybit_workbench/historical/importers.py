from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from bybit_workbench.domain.models import Candle

from .market_data import FundingEvent
from .quality import require_contiguous as validate_contiguous
from .validation import HistoricalDataset


@dataclass(frozen=True, slots=True)
class CsvCandleSchema:
    opened_at: str = "opened_at"
    closed_at: str = "closed_at"
    open: str = "open"
    high: str = "high"
    low: str = "low"
    close: str = "close"
    volume: str = "volume"

    def __post_init__(self) -> None:
        if len(self.required_columns) != len(set(self.required_columns)):
            raise ValueError("candle schema columns must be unique")

    @property
    def required_columns(self) -> tuple[str, ...]:
        return (
            self.opened_at,
            self.closed_at,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
        )


@dataclass(frozen=True, slots=True)
class CsvFundingSchema:
    occurred_at: str = "occurred_at"
    rate: str = "rate"
    mark_price: str = "mark_price"

    @property
    def required_columns(self) -> tuple[str, ...]:
        return (self.occurred_at, self.rate, self.mark_price)


def load_candles_csv(
    path: Path | str,
    *,
    symbol: str,
    timeframe: str,
    schema: CsvCandleSchema | None = None,
    require_contiguous: bool = True,
) -> HistoricalDataset:
    """Load exact Decimal OHLCV data without silently sorting or repairing it."""
    if not symbol.strip() or not timeframe.strip():
        raise ValueError("symbol and timeframe are required")
    selected = schema or CsvCandleSchema()
    candles: list[Candle] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError("CSV header is required")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("CSV header contains duplicate columns")
        missing = sorted(set(selected.required_columns) - set(reader.fieldnames))
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            try:
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=timeframe,
                        opened_at=_parse_timestamp(row[selected.opened_at], "opened_at"),
                        closed_at=_parse_timestamp(row[selected.closed_at], "closed_at"),
                        open=_parse_decimal(row[selected.open], "open"),
                        high=_parse_decimal(row[selected.high], "high"),
                        low=_parse_decimal(row[selected.low], "low"),
                        close=_parse_decimal(row[selected.close], "close"),
                        volume=_parse_decimal(row[selected.volume], "volume"),
                    )
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"invalid historical CSV row {row_number}: {exc}") from exc
    dataset = HistoricalDataset(tuple(candles))
    if require_contiguous:
        validate_contiguous(dataset)
    return dataset


def load_candles_parquet(
    path: Path | str,
    *,
    symbol: str,
    timeframe: str,
    schema: CsvCandleSchema | None = None,
    require_contiguous: bool = True,
) -> HistoricalDataset:
    """Load Parquet through optional pyarrow while preserving Decimal text values."""
    if not symbol.strip() or not timeframe.strip():
        raise ValueError("symbol and timeframe are required")
    selected = schema or CsvCandleSchema()
    parquet = _load_pyarrow_parquet()
    table = parquet.read_table(Path(path))
    columns = list(table.column_names)
    if len(columns) != len(set(columns)):
        raise ValueError("Parquet contains duplicate columns")
    missing = sorted(set(selected.required_columns) - set(columns))
    if missing:
        raise ValueError(f"Parquet is missing required columns: {', '.join(missing)}")
    candles: list[Candle] = []
    for row_number, row in enumerate(table.to_pylist(), start=1):
        try:
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    opened_at=_parse_timestamp_value(row[selected.opened_at], "opened_at"),
                    closed_at=_parse_timestamp_value(row[selected.closed_at], "closed_at"),
                    open=_parse_decimal_value(row[selected.open], "open"),
                    high=_parse_decimal_value(row[selected.high], "high"),
                    low=_parse_decimal_value(row[selected.low], "low"),
                    close=_parse_decimal_value(row[selected.close], "close"),
                    volume=_parse_decimal_value(row[selected.volume], "volume"),
                )
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid historical Parquet row {row_number}: {exc}") from exc
    dataset = HistoricalDataset(tuple(candles))
    if require_contiguous:
        validate_contiguous(dataset)
    return dataset


def load_funding_csv(
    path: Path | str,
    *,
    symbol: str,
    schema: CsvFundingSchema | None = None,
) -> tuple[FundingEvent, ...]:
    if not symbol.strip():
        raise ValueError("symbol is required")
    selected = schema or CsvFundingSchema()
    events: list[FundingEvent] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError("CSV header is required")
        missing = sorted(set(selected.required_columns) - set(reader.fieldnames))
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            try:
                event = FundingEvent(
                    symbol,
                    _parse_timestamp(row[selected.occurred_at], "occurred_at"),
                    _parse_decimal(row[selected.rate], "rate"),
                    _parse_decimal(row[selected.mark_price], "mark_price"),
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"invalid funding CSV row {row_number}: {exc}") from exc
            if events and event.occurred_at <= events[-1].occurred_at:
                raise ValueError("funding CSV rows must be strictly chronological")
            events.append(event)
    return tuple(events)


def _parse_decimal(raw: str | None, field: str) -> Decimal:
    value = "" if raw is None else raw.strip()
    if not value:
        raise ValueError(f"{field} is blank")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not a decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def _parse_decimal_value(raw: object, field: str) -> Decimal:
    return _parse_decimal(None if raw is None else str(raw), field)


def _parse_timestamp(raw: str | None, field: str) -> datetime:
    value = "" if raw is None else raw.strip()
    if not value:
        raise ValueError(f"{field} is blank")
    try:
        if value.lstrip("+-").isdigit():
            timestamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(value))
        else:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} is not ISO-8601 or epoch milliseconds") from exc
    if timestamp.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return timestamp.astimezone(UTC)


def _parse_timestamp_value(raw: object, field: str) -> datetime:
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raise ValueError(f"{field} must include a timezone")
        return raw.astimezone(UTC)
    return _parse_timestamp(None if raw is None else str(raw), field)


def _load_pyarrow_parquet() -> Any:
    try:
        import pyarrow.parquet as parquet  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Parquet import requires the optional 'history' dependency (pyarrow)"
        ) from exc
    return parquet
