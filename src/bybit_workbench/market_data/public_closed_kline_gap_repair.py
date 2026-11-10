"""Bounded, producer-owned repair of exact missing Bybit LINEAR closed bars.

Source-only R03 capability. The existing Native MarketSource caller must hold
its one-producer lock; this module creates no independent job/service/socket,
does not touch Strategy state and never repairs native publicTrade seq/execId.
It inspects the approved shared PostgreSQL view first and uses public REST
only for missing, fully closed UTC slots. Production activation is separate.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.domain.models import Candle
from bybit_workbench.exchange.bybit.mappers import map_rest_klines
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

from .shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
    verify_closed_window,
)

_REST = "https://api.bybit.kz/v5/market/kline"
_FIVE = frozenset(("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT"))
_INTERVALS = frozenset(("1", "5", "15", "60"))
_SHA = re.compile(r"^[a-f0-9]{40}$")
_SESSION = re.compile(r"^[a-f0-9]{32}$")
_MAX_BARS = 3000
_PAGE = 200
_MAX_RESPONSE_BYTES = 262144


class ClosedGapRepairUnavailable(RuntimeError):
    """Unapproved scope, incomplete source proof or unverified REST history."""


@dataclass(frozen=True, slots=True)
class ClosedGapPage:
    first_closed_at: datetime
    last_closed_at: datetime
    count: int


@dataclass(frozen=True, slots=True)
class ClosedGapRepairResult:
    required_bars: int
    fetched_bars: int
    inserted_bars: int
    pages: int
    through: datetime


def _validate_scope(
    key: MarketCandleKey, *, native_symbols: tuple[str, ...],
    through: datetime, required: int, observed_at: datetime,
) -> timedelta:
    if (
        set(native_symbols) != _FIVE
        or len(native_symbols) != len(_FIVE)
        or key.external_symbol not in _FIVE
        or key.product_category != "LINEAR"
        or key.venue_id != "venue-bybit"
        or key.market_source_id != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or key.instrument_id
        != bybit_instrument_id("linear", key.external_symbol)
        or key.timeframe not in _INTERVALS
        or type(required) is not int or not 1 <= required <= _MAX_BARS
    ):
        raise ClosedGapRepairUnavailable("CLOSED_REPAIR_SCOPE_UNAUTHORIZED")
    if (
        through.tzinfo is None or through.utcoffset() != timedelta(0)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() != timedelta(0)
        or through > observed_at
        or through.second != 0 or through.microsecond != 0
    ):
        raise ClosedGapRepairUnavailable("CLOSED_REPAIR_UTC_BOUNDARY_INVALID")
    step = timedelta(minutes=int(key.timeframe))
    if int(through.timestamp()) % int(step.total_seconds()) != 0:
        raise ClosedGapRepairUnavailable("CLOSED_REPAIR_INTERVAL_BOUNDARY_INVALID")
    return step


def plan_missing_closed_pages(
    connection: Any, *, key: MarketCandleKey,
    native_symbols: tuple[str, ...],
    through: datetime, required: int, observed_at: datetime,
) -> tuple[ClosedGapPage, ...]:
    """READ ONLY: plan only missing slots, no query to Bybit and no PG write."""
    step = _validate_scope(
        key, native_symbols=native_symbols, through=through,
        required=required, observed_at=observed_at,
    )
    first = through - step * (required - 1)
    rows = connection.execute(
        """SELECT closed_at
             FROM analytics.closed_candles_effective
            WHERE market_source_id=%s AND instrument_id=%s
              AND timeframe=%s AND closed_at BETWEEN %s AND %s
            ORDER BY closed_at""",
        (
            key.market_source_id, key.instrument_id, key.timeframe,
            first, through,
        ),
    ).fetchall()
    present: set[datetime] = set()
    for (closed_at,) in rows:
        if (
            not isinstance(closed_at, datetime)
            or closed_at.tzinfo is None
            or closed_at.utcoffset() != timedelta(0)
            or closed_at < first or closed_at > through
            or (closed_at - first) % step != timedelta(0)
            or closed_at in present
        ):
            raise ClosedGapRepairUnavailable("CLOSED_REPAIR_PG_PROVENANCE_INVALID")
        present.add(closed_at)
    pages: list[ClosedGapPage] = []
    start: datetime | None = None
    last: datetime | None = None
    count = 0
    for index in range(required):
        closed = first + step * index
        if closed in present:
            if start is not None and last is not None:
                pages.append(ClosedGapPage(start, last, count))
            start, last, count = None, None, 0
            continue
        if start is not None and last is not None and count == _PAGE:
            pages.append(ClosedGapPage(start, last, count))
            start, last, count = None, None, 0
        if start is None:
            start = closed
        last = closed
        count += 1
    if start is not None and last is not None:
        pages.append(ClosedGapPage(start, last, count))
    return tuple(pages)


def fetch_verified_bybit_closed_page(
    key: MarketCandleKey, page: ClosedGapPage, *,
    received_at: datetime,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[Candle, ...]:
    """One bounded public Bybit Kazakhstan REST page: exact closed slots only."""
    step = timedelta(minutes=int(key.timeframe))
    if (
        not 1 <= page.count <= _PAGE
        or page.first_closed_at + step * (page.count - 1)
        != page.last_closed_at
        or received_at.tzinfo is None
        or received_at.utcoffset() != timedelta(0)
        or page.last_closed_at > received_at
    ):
        raise ClosedGapRepairUnavailable("CLOSED_REPAIR_PAGE_BOUNDARY_INVALID")
    opened_first = page.first_closed_at - step
    opened_last = page.last_closed_at - step
    params = urllib.parse.urlencode({
        "category": "linear", "symbol": key.external_symbol,
        "interval": key.timeframe,
        "start": int(opened_first.timestamp() * 1000),
        "end": int(opened_last.timestamp() * 1000),
        "limit": page.count,
    })
    request = urllib.request.Request(
        _REST + "?" + params,
        headers={"User-Agent": "Cripta-MarketSource-public-only"},
        method="GET",
    )
    try:
        with opener(request, timeout=8.0) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ClosedGapRepairUnavailable("CLOSED_REPAIR_REST_TOO_LARGE")
        payload = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ClosedGapRepairUnavailable(
            "CLOSED_REPAIR_REST_UNAVAILABLE_OR_INVALID"
        ) from exc
    if (
        not isinstance(payload, dict)
        or type(payload.get("retCode")) is not int
        or payload["retCode"] != 0
        or type(payload.get("time")) is not int
        or not isinstance(payload.get("result"), dict)
    ):
        raise ClosedGapRepairUnavailable("CLOSED_REPAIR_PROVIDER_ATTESTATION_INVALID")
    provider_time = datetime.fromtimestamp(payload["time"] / 1000, UTC)
    if (
        provider_time < page.last_closed_at
        or provider_time > received_at + timedelta(seconds=5)
    ):
        raise ClosedGapRepairUnavailable("CLOSED_REPAIR_PROVIDER_CLOCK_INVALID")
    result = payload["result"]
    if (
        result.get("category") != "linear"
        or result.get("symbol") != key.external_symbol
        or not isinstance(result.get("list"), list)
    ):
        raise ClosedGapRepairUnavailable("CLOSED_REPAIR_PROVIDER_IDENTITY_INVALID")
    try:
        bars = verify_closed_window(
            key, map_rest_klines(
                result["list"], symbol=key.external_symbol,
                interval=key.timeframe, observed_at=received_at,
            ),
            minimum=page.count, through=page.last_closed_at,
        )
    except (RuntimeError, TypeError, ValueError, KeyError) as exc:
        raise ClosedGapRepairUnavailable(
            "CLOSED_REPAIR_REST_BARS_UNVERIFIED"
        ) from exc
    if len(bars) != page.count or bars[0].closed_at != page.first_closed_at:
        raise ClosedGapRepairUnavailable("CLOSED_REPAIR_REST_PAGE_INCOMPLETE")
    return bars


def repair_source_closed_history(
    connection: Any, *, key: MarketCandleKey, native_symbols: tuple[str, ...],
    source_session_id: str, producer_release_commit: str,
    through: datetime, required: int,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> ClosedGapRepairResult:
    """Existing producer only: fetch outside a write transaction, then persist.

    Called only by the existing MarketSource source owner holding its producer
    advisory lock. No parallel process, Strategy or Exchange operation. Every
    page is verified BEFORE the single canonical write transaction.
    """
    observed_at = clock()
    _validate_scope(
        key, native_symbols=native_symbols, through=through,
        required=required, observed_at=observed_at,
    )
    if (
        _SESSION.fullmatch(source_session_id) is None
        or _SHA.fullmatch(producer_release_commit) is None
    ):
        raise ClosedGapRepairUnavailable("CLOSED_REPAIR_SOURCE_EPOCH_INVALID")

    def require_epoch() -> None:
        status = connection.execute(
            """SELECT state,market_source_id,producer_release_commit
                 FROM analytics.native_public_trade_source_sessions
                WHERE source_session_id=%s""",
            (source_session_id,),
        ).fetchone()
        if status != (
            "ACTIVE", key.market_source_id, producer_release_commit,
        ):
            raise ClosedGapRepairUnavailable(
                "CLOSED_REPAIR_SOURCE_EPOCH_NOT_ACTIVE"
            )

    require_epoch()
    pages = plan_missing_closed_pages(
        connection, key=key, native_symbols=native_symbols,
        through=through, required=required, observed_at=observed_at,
    )
    store = SharedClosedCandleStore(connection, key)
    if not pages:
        store.history(through=through, required=required)
        return ClosedGapRepairResult(required, 0, 0, 0, through)
    fetched = tuple(
        fetch_verified_bybit_closed_page(
            key, page, received_at=clock(), opener=opener,
        )
        for page in pages
    )
    # Verify source epoch a second time and make all repaired pages atomic.
    inserted = 0
    with connection.transaction():
        require_epoch()
        for bars in fetched:
            inserted += store.ingest(
                bars, producer_release_commit=producer_release_commit,
                transport="BYBIT_REST_KLINE", observed_at=clock(),
            )
        store.history(through=through, required=required)
    return ClosedGapRepairResult(
        required, sum(len(bars) for bars in fetched),
        inserted, len(pages), through,
    )
