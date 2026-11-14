"""Compact, immutable second-level market publication for one producer epoch.

A publication pins *references* to immutable original publicTrade events and
closed OHLCV bars. It does not duplicate native tape, synthesize missing
events, store Account private facts, or authorize an Entry/Exit/Execution.
The native producer commits its event/journal/cursor transaction BEFORE this
separate transaction makes a read-model publication visible.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.market_data.public_closed_candle_refs import (
    collect_instrument_closed_candle_references,
)
from bybit_workbench.market_data.public_forming_minute_source import (
    collect_forming_minute_references,
)
from bybit_workbench.market_data.public_rest_observations import (
    collect_public_rest_references,
)
from bybit_workbench.market_data.publication_compact_refs import (
    collect_compact_public_references,
)
from bybit_workbench.market_data.r11_original_publication_refs import (
    collect_r11_original_optional_publication_refs,
)
from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID

_RELEASE = re.compile(r"^[a-f0-9]{40}$")
_SESSION = re.compile(r"^[a-f0-9]{32}$")


class MarketPublicationUnavailable(RuntimeError):
    """Source epoch, DB schema or publication provenance is not verified."""


def publish_market_source_second(
    connection: Any, *,
    source_session_id: str,
    producer_release_commit: str,
    symbols: tuple[str, ...],
    timeframes: tuple[str, ...] = ("1", "5", "15"),
    public_requirements: tuple[RequiredPublicFeed, ...] = (),
    original_optional_transport_epochs: dict[str, str] | None = None,
    observed_at: datetime | None = None,
) -> int | None:
    """Publish at most ONE frozen snapshot per UTC second and source epoch.

    Returns its newly allocated publication ID or None if this second was
    already published. The caller is the one existing Native MarketSource
    process holding the independent producer's advisory lock.

    Consumers must use a REPEATABLE READ database snapshot to join the
    publication's source cursors to raw events and closed bars. Historical
    lookback/ATR seed completeness is deliberately NOT asserted here.
    """
    now = observed_at or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise MarketPublicationUnavailable("MARKET_PUBLICATION_TIME_NOT_UTC")
    if _SESSION.fullmatch(source_session_id) is None:
        raise MarketPublicationUnavailable("MARKET_PUBLICATION_EPOCH_INVALID")
    if _RELEASE.fullmatch(producer_release_commit) is None:
        raise MarketPublicationUnavailable("MARKET_PUBLICATION_RELEASE_INVALID")
    if not symbols or len(symbols) > 40 or len(symbols) != len(set(symbols)):
        raise MarketPublicationUnavailable("MARKET_PUBLICATION_SYMBOLS_INVALID")
    if any(not isinstance(x, str) or not x for x in symbols):
        raise MarketPublicationUnavailable("MARKET_PUBLICATION_SYMBOLS_INVALID")
    if not timeframes or len(timeframes) != len(set(timeframes)):
        raise MarketPublicationUnavailable("MARKET_PUBLICATION_INTERVALS_INVALID")
    if any(x not in {"1", "5", "15", "60"} for x in timeframes):
        raise MarketPublicationUnavailable("MARKET_PUBLICATION_INTERVALS_INVALID")

    second = now.replace(microsecond=0)
    with connection.transaction():
        source = connection.execute(
            """SELECT state,market_source_id,producer_release_commit
               FROM analytics.native_public_trade_source_sessions
               WHERE source_session_id=%s""",
            (source_session_id,),
        ).fetchone()
        if source != (
            "ACTIVE", BYBIT_PUBLIC_MARKET_SOURCE_ID, producer_release_commit,
        ):
            raise MarketPublicationUnavailable(
                "MARKET_PUBLICATION_SOURCE_EPOCH_NOT_ACTIVE"
            )

        cursors = connection.execute(
            """SELECT external_symbol,instrument_id,frame_number,frame_index,
                      exchange_sequence,exec_id,event_at,accepted_at
               FROM analytics.native_public_trade_source_cursors
               WHERE source_session_id=%s AND external_symbol=ANY(%s)
               ORDER BY external_symbol""",
            (source_session_id, list(symbols)),
        ).fetchall()
        if any(row[0] not in symbols for row in cursors):
            raise MarketPublicationUnavailable(
                "MARKET_PUBLICATION_UNEXPECTED_INSTRUMENT"
            )
        native: dict[str, dict[str, object]] = {}
        for row in cursors:
            sym, instrument, frame, index, seq, eid, event_at, accepted_at = row
            if event_at > now or accepted_at > now + timedelta(seconds=5):
                raise MarketPublicationUnavailable(
                    "MARKET_PUBLICATION_FUTURE_NATIVE_CURSOR"
                )
            native[str(sym)] = {
                "instrument_id": str(instrument),
                "frame_number": int(frame),
                "frame_index": int(index),
                "exchange_sequence": int(seq),
                "exec_id": str(eid),
                "event_at": event_at.isoformat(),
                "accepted_at": accepted_at.isoformat(),
            }

        candles = connection.execute(
            """SELECT DISTINCT ON (instrument_id,timeframe)
                      external_symbol,timeframe,closed_at
               FROM analytics.closed_candles_effective
               WHERE market_source_id=%s
                 AND product_category='LINEAR'
                 AND external_symbol=ANY(%s)
                 AND timeframe=ANY(%s)
                 AND closed_at<=%s
               ORDER BY instrument_id,timeframe,closed_at DESC""",
            (
                BYBIT_PUBLIC_MARKET_SOURCE_ID, list(symbols),
                list(timeframes), second,
            ),
        ).fetchall()
        candle_refs: dict[str, dict[str, str]] = {}
        for symbol, tf, close in candles:
            if close > now or close.utcoffset() != timedelta(0):
                raise MarketPublicationUnavailable(
                    "MARKET_PUBLICATION_UNVERIFIED_CLOSE"
                )
            candle_refs.setdefault(str(symbol), {})[str(tf)] = close.isoformat()

        coverage = {
            symbol: {
                "native": (
                    "PRESENT_IN_SOURCE_EPOCH" if symbol in native
                    else "MISSING_IN_SOURCE_EPOCH"
                ),
                "closed": {
                    tf: (
                        "PRESENT" if tf in candle_refs.get(symbol, {})
                        else "MISSING"
                    )
                    for tf in timeframes
                },
            }
            for symbol in symbols
        }
        # Compact spot/linear references belong to the *same source epoch*.
        # Capture only finalized, committed source seconds preceding this
        # publication boundary; absence/staleness is explicit, never invented.
        compact_refs, compact_coverage = collect_compact_public_references(
            connection, source_session_id=source_session_id,
            as_of_second=second, requirements=tuple(
                item for item in public_requirements
                if item.dataset in (
                    "PUBLIC_TRADE", "TICKER", "ORDERBOOK_50", "LIQUIDATION",
                )
            ),
        )
        rest_refs, rest_coverage = collect_public_rest_references(
            connection, source_session_id=source_session_id,
            as_of_second=second, requirements=tuple(
                item for item in public_requirements
                if item.dataset in ("ACCOUNT_RATIO", "INSTRUMENT_INFO")
            ),
        )
        forming_refs, forming_coverage = collect_forming_minute_references(
            connection, source_session_id=source_session_id,
            as_of_second=second, requirements=tuple(
                item for item in public_requirements
                if item.dataset == "FORMING_KLINE"
            ),
        )
        closed_instrument_refs, closed_instrument_coverage = (
            collect_instrument_closed_candle_references(
                connection, as_of_second=second,
                requirements=tuple(
                    item for item in public_requirements
                    if item.dataset == "CLOSED_KLINE"
                ),
            )
        )
        from psycopg.types.json import Jsonb  # type: ignore[import-not-found]

        # Source-only R11: the publisher pins physical original optional
        # frame refs INSIDE the immutable publication insertion transaction.
        # Empty/missing refs are honest coverage, never MAYAK readiness.
        original_refs: dict[str, dict[str, object]] = {}
        original_coverage: dict[str, str] = {}
        if original_optional_transport_epochs is not None:
            original_refs, original_coverage = (
                collect_r11_original_optional_publication_refs(
                    connection, source_session_id=source_session_id,
                    producer_release_commit=producer_release_commit,
                    transport_epochs=original_optional_transport_epochs,
                    as_of_second=second,
                )
            )
        if original_optional_transport_epochs is not None:
            row = connection.execute(
                """INSERT INTO analytics.market_publications
                    (market_source_id,source_session_id,producer_release_commit,
                     as_of_second,native_watermarks,closed_candle_watermarks,
                     field_coverage,compact_second_refs,compact_field_coverage,
                     rest_observation_refs,rest_field_coverage,
                     forming_minute_refs,forming_field_coverage,
                     closed_instrument_refs,closed_instrument_coverage,
                     original_optional_frame_refs,original_optional_frame_coverage,
                     publication_status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'WATERMARKS_ONLY')
                   ON CONFLICT (market_source_id,source_session_id,as_of_second)
                   DO NOTHING RETURNING publication_id""",
                (
                    BYBIT_PUBLIC_MARKET_SOURCE_ID, source_session_id,
                    producer_release_commit, second, Jsonb(native),
                    Jsonb(candle_refs), Jsonb(coverage),
                    Jsonb(compact_refs), Jsonb(compact_coverage),
                    Jsonb(rest_refs), Jsonb(rest_coverage),
                    Jsonb(forming_refs), Jsonb(forming_coverage),
                    Jsonb(closed_instrument_refs),
                    Jsonb(closed_instrument_coverage),
                    Jsonb(original_refs), Jsonb(original_coverage),
                ),
            ).fetchone()
        else:
            row = connection.execute(
                """INSERT INTO analytics.market_publications
                    (market_source_id,source_session_id,producer_release_commit,
                     as_of_second,native_watermarks,closed_candle_watermarks,
                     field_coverage,compact_second_refs,compact_field_coverage,
                     rest_observation_refs,rest_field_coverage,
                     forming_minute_refs,forming_field_coverage,
                     closed_instrument_refs,closed_instrument_coverage,
                     publication_status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'WATERMARKS_ONLY')
                   ON CONFLICT (market_source_id,source_session_id,as_of_second)
                   DO NOTHING RETURNING publication_id""",
                (
                    BYBIT_PUBLIC_MARKET_SOURCE_ID, source_session_id,
                    producer_release_commit, second, Jsonb(native),
                    Jsonb(candle_refs), Jsonb(coverage),
                    Jsonb(compact_refs), Jsonb(compact_coverage),
                    Jsonb(rest_refs), Jsonb(rest_coverage),
                    Jsonb(forming_refs), Jsonb(forming_coverage),
                    Jsonb(closed_instrument_refs), Jsonb(closed_instrument_coverage),
                ),
            ).fetchone()
    return int(row[0]) if row is not None else None
