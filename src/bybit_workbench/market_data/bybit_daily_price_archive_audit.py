"""Bounded original trade identity audit against a downloaded Bybit daily CSV.gz.

The canonical public archive (trading/SYMBOL/SYMBOLYYYY-MM-DD.csv.gz) has
trdMatchID, timestamp, price, size and taker side, but no original WS seq.
This compares each archive trade in the required interval to the independent
immutable MarketSource journal. A complete local gzip stream (CRC checked),
matching IDs and a digest are *necessary* price evidence, not a trusted
issuer certificate or proof of WebSocket seq continuity.

The independent download/provenance owner MUST verify the actual official
Bybit object and finality separately. This module cannot grant Exit,
post-hoc TRAIL035, Strategy admission, READY or financial mutation.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import re
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

_BATCH = 512
_MAX_ARCHIVE_BYTES = 512_000_000
_MAX_ARCHIVE_ROWS = 4_000_000
_SYMBOL = re.compile(r"[A-Z0-9]{2,32}\Z")
_SESSION = re.compile(r"[a-f0-9]{32}\Z")
_PRODUCER = re.compile(r"[a-f0-9]{40}\Z")
_REQUIRED_FIELDS = frozenset((
    "timestamp", "symbol", "side", "size", "price", "tickDirection",
    "trdMatchID", "grossValue", "homeNotional", "foreignNotional",
))

# Original source lookup is an independent, read-only, exact-session port.
# The caller must enforce session, market source, instrument and UTC bounds
# at the SQL boundary. Returned UTC-ms are original WS event time, not CSV seq.
NativePriceFields = tuple[int, Decimal, Decimal, str]
NativeLookup = Callable[[tuple[str, ...]], Mapping[str, NativePriceFields]]


class BybitDailyPriceArchiveUnverified(RuntimeError):
    """Missing, ambiguous, truncated or non-original price history."""


@dataclass(frozen=True, slots=True)
class BybitPriceArchiveAudit:
    symbol: str
    market_source_id: str
    instrument_id: str
    source_session_id: str
    source_commit: str
    archive_day: date
    official_archive_url: str
    archive_gzip_sha256: str
    archive_total_rows: int
    interval_matched_rows: int
    interval_first_ms: int
    interval_last_ms: int
    interval_low: Decimal
    interval_high: Decimal
    original_trade_ids_matched: bool
    archive_issuer_verified: bool
    price_history_verified: bool
    native_continuity_verified: bool
    exit_ready: bool
    reason: str


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise BybitDailyPriceArchiveUnverified(
            "BYBIT_DAILY_PRICE_ARCHIVE_" + reason
        )


def _millis(value: datetime) -> int:
    _need(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(),
        "UTC_REQUIRED",
    )
    return (
        (value.date().toordinal() - date(1970, 1, 1).toordinal())
        * 86_400_000
        + value.hour * 3_600_000
        + value.minute * 60_000
        + value.second * 1_000
        + value.microsecond // 1_000
    )


def _decimal(raw: object) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (ValueError, TypeError, InvalidOperation):
        raise BybitDailyPriceArchiveUnverified(
            "BYBIT_DAILY_PRICE_ARCHIVE_VALUE_INVALID"
        ) from None
    _need(value.is_finite() and value > 0, "VALUE_INVALID")
    return value


def _row(
    raw: Mapping[str, str | None], *, symbol: str,
) -> tuple[int, str, Decimal, Decimal, str]:
    _need(raw.get("symbol") == symbol, "FOREIGN_SYMBOL")
    ident = raw.get("trdMatchID")
    side = raw.get("side")
    _need(
        isinstance(ident, str) and 1 <= len(ident) <= 128
        and isinstance(side, str) and side in ("Buy", "Sell"),
        "TRADE_IDENTITY_INVALID",
    )
    try:
        timestamp = _decimal(raw.get("timestamp"))
        ms = int(timestamp * 1000)
    except (OverflowError, ValueError):
        raise BybitDailyPriceArchiveUnverified(
            "BYBIT_DAILY_PRICE_ARCHIVE_TIMESTAMP_INVALID"
        ) from None
    _need(0 <= ms < 2**63, "TIMESTAMP_INVALID")
    return ms, ident, _decimal(raw.get("price")), _decimal(raw.get("size")), side


def _verify_batch(
    batch: list[tuple[int, str, Decimal, Decimal, str]],
    *, lookup: NativeLookup,
) -> None:
    if not batch:
        return
    keys = tuple(item[1] for item in batch)
    found = lookup(keys)
    _need(
        isinstance(found, Mapping) and set(found) == set(keys),
        "NATIVE_ID_SET_MISMATCH",
    )
    for ms, ident, price, qty, side in batch:
        native = found.get(ident)
        _need(
            isinstance(native, tuple) and len(native) == 4
            and type(native[0]) is int and native[0] == ms
            and native[1] == price and native[2] == qty
            and native[3] == side,
            "NATIVE_PRICE_OR_TIME_DRIFT",
        )


def audit_bybit_daily_price_archive(
    archive_path: Path,
    *,
    symbol: str,
    archive_day: date,
    source_session_id: str,
    source_commit: str,
    coverage_start: datetime,
    coverage_end: datetime,
    checked_at: datetime,
    native_interval_row_count: int,
    lookup_original_native: NativeLookup,
    max_archive_rows: int,
) -> BybitPriceArchiveAudit:
    """Audit all rows of the finalized UTC day's gzip, but grant NO Exit proof.

    Provider identity, authentic full-day completeness and archive signing
    must be checked by an approved separate issuer. This routine only
    validates the *candidate* and its exact immutable WS trade IDs. A live
    partial-day file or small subset cannot be issued as a complete archive.
    """
    _need(isinstance(symbol, str) and _SYMBOL.fullmatch(symbol) is not None,
          "SYMBOL_INVALID")
    _need(isinstance(archive_day, date) and not isinstance(archive_day, datetime),
          "DAY_INVALID")
    _need(isinstance(source_session_id, str)
          and _SESSION.fullmatch(source_session_id) is not None, "SESSION_INVALID")
    _need(isinstance(source_commit, str)
          and _PRODUCER.fullmatch(source_commit) is not None, "PRODUCER_INVALID")
    start = _millis(coverage_start)
    end = _millis(coverage_end)
    now = _millis(checked_at)
    midnight = datetime.combine(archive_day, time.min, tzinfo=UTC)
    day_start = _millis(midnight)
    day_end = _millis(midnight + timedelta(days=1))
    _need(
        day_start <= start <= end < day_end and now >= day_end,
        "UTC_DAY_NOT_FINALIZED_OR_SCOPE_INVALID",
    )
    _need(type(native_interval_row_count) is int
          and 0 < native_interval_row_count <= _MAX_ARCHIVE_ROWS,
          "NATIVE_COUNT_INVALID")
    _need(type(max_archive_rows) is int
          and 1 <= max_archive_rows <= _MAX_ARCHIVE_ROWS
          and native_interval_row_count <= max_archive_rows,
          "RESOURCE_BUDGET_INVALID")
    _need(callable(lookup_original_native), "LOOKUP_MISSING")
    _need(isinstance(archive_path, Path) and archive_path.is_file(),
          "ARCHIVE_FILE_MISSING")
    _need(
        0 < archive_path.stat().st_size <= _MAX_ARCHIVE_BYTES,
        "ARCHIVE_BYTES_OVER_BUDGET",
    )
    expected_name = f"{symbol}{archive_day.isoformat()}.csv.gz"
    _need(archive_path.name == expected_name, "ARCHIVE_FILENAME_INVALID")
    url = (
        f"https://public.bybit.com/trading/{symbol}/{expected_name}"
    )
    # Hash the exact unmodified compressed object and consume gzip to EOF
    # so corrupt/truncated CRC is detected; never trust only a prefix.
    digest = hashlib.sha256()
    with archive_path.open("rb") as raw_file:
        for chunk in iter(lambda: raw_file.read(1_048_576), b""):
            digest.update(chunk)
    compressed_sha = digest.hexdigest()
    total = 0
    selected = 0
    first_ms: int | None = None
    last_ms: int | None = None
    previous_ms: int | None = None
    low: Decimal | None = None
    high: Decimal | None = None
    # An explicit resource ceiling guards a single-day original-ID set.
    seen: set[str] = set()
    batch: list[tuple[int, str, Decimal, Decimal, str]] = []
    try:
        with gzip.open(archive_path, "rt", encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            _need(reader.fieldnames is not None
                  and _REQUIRED_FIELDS.issubset(reader.fieldnames)
                  and len(set(reader.fieldnames)) == len(reader.fieldnames),
                  "ARCHIVE_HEADER_INVALID")
            for raw in reader:
                total += 1
                _need(total <= max_archive_rows, "ARCHIVE_ROWS_OVER_BUDGET")
                ms, ident, price, qty, side = _row(raw, symbol=symbol)
                _need(day_start <= ms < day_end, "FOREIGN_UTC_DAY")
                _need(previous_ms is None or ms >= previous_ms,
                      "ARCHIVE_TIME_REGRESSION")
                _need(ident not in seen, "DUPLICATE_ORIGINAL_TRADE_ID")
                seen.add(ident)
                previous_ms = ms
                if start <= ms <= end:
                    first_ms = ms if first_ms is None else first_ms
                    last_ms = ms
                    low = price if low is None else min(low, price)
                    high = price if high is None else max(high, price)
                    selected += 1
                    batch.append((ms, ident, price, qty, side))
                    if len(batch) >= _BATCH:
                        _verify_batch(batch, lookup=lookup_original_native)
                        batch.clear()
            _verify_batch(batch, lookup=lookup_original_native)
    except (OSError, EOFError, UnicodeError, csv.Error, zlib.error):
        raise BybitDailyPriceArchiveUnverified(
            "BYBIT_DAILY_PRICE_ARCHIVE_COMPRESSED_FILE_INCOMPLETE"
        ) from None
    _need(
        selected == native_interval_row_count
        and first_ms is not None and last_ms is not None
        and low is not None and high is not None,
        "ARCHIVE_NATIVE_INTERVAL_COUNT_OR_COVERAGE_MISMATCH",
    )
    return BybitPriceArchiveAudit(
        symbol=symbol,
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        instrument_id=bybit_instrument_id("linear", symbol),
        source_session_id=source_session_id,
        source_commit=source_commit,
        archive_day=archive_day,
        official_archive_url=url,
        archive_gzip_sha256=compressed_sha,
        archive_total_rows=total,
        interval_matched_rows=selected,
        interval_first_ms=first_ms,
        interval_last_ms=last_ms,
        interval_low=low,
        interval_high=high,
        original_trade_ids_matched=True,
        archive_issuer_verified=False,
        price_history_verified=False,
        native_continuity_verified=False,
        exit_ready=False,
        reason="INDEPENDENT_OFFICIAL_ARCHIVE_ISSUER_AND_COVERAGE_ATTESTATION_MISSING",
    )
