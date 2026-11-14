"""R11: READ-ONLY bounded, tamper-detecting original optional frame pages.

No consumer/live MAYAK cutover and no MarketPublication original optional
watermarks exist yet. A source epoch's ACTIVE/GAP label is NOT continuity
proof; after-cursor hash only verifies an exact byte-equivalent parsed JSON
row, not whether all provider frames were received. This reader never opens
a Bybit socket or writes PostgreSQL. Caller supplies a READ ONLY REPEATABLE
READ transaction and exact source+transport epoch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from bybit_workbench.market_data.public_source_event_codec import (
    PublicSourceFact,
    PublicSourceFrameInvalid,
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.r11_optional_original_frame_journal import (
    R11OriginalFrameUnavailable,
    R11OriginalPublicFrame,
    make_r11_original_optional_frame,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

_SESSION = re.compile(r"^[0-9a-f]{32}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_SEVEN = frozenset((
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
))
_PAGE_LIMIT = 128


class R11OriginalFrameReadUnverified(RuntimeError):
    """Original optional raw frame not proved for exact source/cursor scope."""


@dataclass(frozen=True, slots=True)
class R11OriginalFramePage:
    source_session_id: str
    transport_epoch_id: str
    product_category: str
    source_state: str
    producer_release_commit: str
    requested_after: int
    next_cursor: int
    next_digest: str | None
    frames: tuple[R11OriginalPublicFrame, ...]
    more_pages: bool
    # No original-event publication pointer/continuous transport has been
    # proved, so this page CANNOT be promoted to MAYAK live readiness.
    market_publication_cursor_verified: bool = False
    provider_continuity_verified: bool = False
    seven_symbol_parity_verified: bool = False
    mayak_cutover_ready: bool = False


def _need(value: bool, reason: str) -> None:
    if not value:
        raise R11OriginalFrameReadUnverified("R11_ORIGINAL_READ_" + reason)


def _utc(value: object, reason: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise R11OriginalFrameReadUnverified(
            "R11_ORIGINAL_READ_" + reason
        )
    return value.astimezone(UTC)


def read_r11_optional_original_frame_page(
    connection: Any,
    *,
    source_session_id: str,
    transport_epoch_id: str,
    product_category: str,
    after_frame: int = 0,
    after_digest: str | None = None,
    max_frames: int = _PAGE_LIMIT,
    through_frame: int | None = None,
) -> R11OriginalFramePage:
    """Fetch exact canonical original-frame rows without invented continuity.

    A nonzero after_frame requires a matching physical DB row+SHA256 in the
    same source/transport/product epoch. Per-product frame numbers may have
    gaps because a single native owner also records other product frames.
    Never treat empty pages as proof that no provider events occurred.
    """
    _need(
        isinstance(source_session_id, str)
        and _SESSION.fullmatch(source_session_id) is not None
        and isinstance(transport_epoch_id, str)
        and _SESSION.fullmatch(transport_epoch_id) is not None
        and product_category in ("SPOT", "LINEAR")
        and type(after_frame) is int and after_frame >= 0
        and type(max_frames) is int and 1 <= max_frames <= _PAGE_LIMIT
        and (
            through_frame is None
            or (type(through_frame) is int and through_frame >= after_frame)
        )
        and (
            (after_frame == 0 and after_digest is None)
            or (
                after_frame > 0 and isinstance(after_digest, str)
                and _SHA64.fullmatch(after_digest) is not None
            )
        ),
        "SCOPE_OR_CURSOR_INVALID",
    )
    isolation = connection.execute("SHOW transaction_isolation").fetchone()
    read_only = connection.execute("SHOW transaction_read_only").fetchone()
    _need(
        isolation == ("repeatable read",) and read_only == ("on",),
        "IMMUTABLE_READ_ONLY_SNAPSHOT_REQUIRED",
    )
    session = connection.execute(
        """SELECT market_source_id,producer_release_commit,state
             FROM analytics.native_public_trade_source_sessions
            WHERE source_session_id=%s""",
        (source_session_id,),
    ).fetchone()
    _need(
        isinstance(session, (tuple, list)) and len(session) == 3,
        "SESSION_NOT_FOUND",
    )
    market, producer, state = session
    _need(
        market == BYBIT_PUBLIC_MARKET_SOURCE_ID
        and isinstance(producer, str)
        and _SHA40.fullmatch(producer) is not None
        and state in ("ACTIVE", "GAP"),
        "SOURCE_SESSION_IDENTITY_INVALID",
    )
    if after_frame:
        anchor = connection.execute(
            """SELECT frame_content_sha256
                 FROM analytics.public_source_original_event_frames
                WHERE source_session_id=%s AND transport_epoch_id=%s
                  AND product_category=%s AND source_frame_number=%s""",
            (source_session_id, transport_epoch_id,
             product_category, after_frame),
        ).fetchone()
        _need(
            anchor == (after_digest,),
            "CURSOR_HASH_OR_TRANSPORT_EPOCH_NOT_FOUND",
        )
    # The bounded read variant cannot consume frames committed AFTER the
    # immutable MarketPublication's pinned source cursor.
    upper_filter = (
        " AND source_frame_number<=%s" if through_frame is not None else ""
    )
    sql = (
        """SELECT source_session_id,transport_epoch_id,market_source_id,
                  venue_id,instrument_id,product_category,external_symbol,
                  fact_family,source_topic,source_frame_number,frame_type,
                  provider_frame_at,source_event_first_at,source_event_last_at,
                  received_at,provider_frame,decoded_original_facts,
                  frame_content_sha256,producer_release_commit
             FROM analytics.public_source_original_event_frames
            WHERE source_session_id=%s AND transport_epoch_id=%s
              AND product_category=%s AND source_frame_number>%s"""
        + upper_filter
        + " ORDER BY source_frame_number LIMIT %s"
    )
    params: tuple[object, ...] = (
        (source_session_id, transport_epoch_id, product_category,
         after_frame, max_frames + 1)
        if through_frame is None
        else (source_session_id, transport_epoch_id, product_category,
              after_frame, through_frame, max_frames + 1)
    )
    rows = connection.execute(sql, params).fetchall()
    _need(isinstance(rows, list) and len(rows) <= max_frames + 1,
          "ORIGINAL_FRAME_PAGE_BUDGET_INVALID")
    more = len(rows) > max_frames
    confirmed: list[R11OriginalPublicFrame] = []
    previous = after_frame
    for row in rows[:max_frames]:
        _need(
            isinstance(row, (tuple, list)) and len(row) == 19,
            "PHYSICAL_ROW_SHAPE_INVALID",
        )
        (
            row_session, row_transport, row_source, venue, instrument,
            product, symbol, family, topic, frame_num, frame_type,
            provider_at, first_at, last_at, received_at,
            original_image, original_facts, digest, release,
        ) = row
        _need(
            row_session == source_session_id
            and row_transport == transport_epoch_id
            and row_source == BYBIT_PUBLIC_MARKET_SOURCE_ID
            and venue == "venue-bybit"
            and product == product_category
            and symbol in _SEVEN
            and instrument == bybit_instrument_id(
                product_category.lower(), symbol,
            )
            and isinstance(topic, str)
            and isinstance(frame_type, str)
            and type(frame_num) is int and frame_num > previous
            and isinstance(digest, str)
            and _SHA64.fullmatch(digest) is not None
            and release == producer
            and isinstance(original_image, dict)
            and isinstance(original_facts, list),
            "PHYSICAL_IDENTITY_OR_ORDER_INVALID",
        )
        original_received = _utc(received_at, "RECEIVED_UTC_INVALID")
        try:
            decoded: tuple[PublicSourceFact, ...] = decode_bybit_public_frame(
                original_image, product=cast(Any, product_category),
                allowed_symbols=frozenset((symbol,)),
                received_at=original_received,
            )
            computed = make_r11_original_optional_frame(
                source_session_id=source_session_id,
                transport_epoch_id=transport_epoch_id,
                producer_release_commit=producer,
                product=product_category,
                source_frame_number=frame_num,
                message=original_image, facts=decoded,
                received_at=original_received,
            )
        except (
            PublicSourceFrameInvalid, R11OriginalFrameUnavailable,
            ValueError, TypeError,
        ) as exc:
            raise R11OriginalFrameReadUnverified(
                "R11_ORIGINAL_READ_CODEC_OR_HASH_INVALID"
            ) from exc
        _need(
            computed.frame_content_sha256 == digest
            and computed.fact_family == family
            and computed.source_topic == topic
            and computed.frame_type == frame_type
            and computed.provider_frame_at == _utc(
                provider_at, "PROVIDER_UTC_INVALID",
            )
            and computed.source_event_first_at == _utc(
                first_at, "FIRST_EVENT_UTC_INVALID",
            )
            and computed.source_event_last_at == _utc(
                last_at, "LAST_EVENT_UTC_INVALID",
            )
            and list(computed.decoded_original_facts) == original_facts,
            "PHYSICAL_FACT_OR_HASH_MISMATCH",
        )
        confirmed.append(computed)
        previous = frame_num
    return R11OriginalFramePage(
        source_session_id=source_session_id,
        transport_epoch_id=transport_epoch_id,
        product_category=product_category,
        source_state=state,
        producer_release_commit=producer,
        requested_after=after_frame,
        next_cursor=previous,
        next_digest=(
            confirmed[-1].frame_content_sha256 if confirmed else after_digest
        ),
        frames=tuple(confirmed),
        more_pages=more,
    )
