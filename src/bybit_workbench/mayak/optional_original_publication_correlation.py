"""R11: read-only correlation of original frame cursor with compact publication.

This is NOT an atomic publication original-event watermark, transport continuity
proof or permission to retire the existing seven-symbol MAYAK feed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakBatch,
)

_HASH = re.compile(r"^[0-9a-f]{64}$")


class R11OriginalPublicationCorrelationUnverified(RuntimeError):
    """Publication, original frame or temporal relation not proven."""


@dataclass(frozen=True, slots=True)
class R11OriginalPublicationCorrelation:
    publication_id: int
    source_session_id: str
    transport_epoch_id: str
    product_category: str
    original_frame_number: int
    original_frame_sha256: str
    publication_as_of: datetime
    source_state: str
    original_events_in_page: int
    physical_correlation_verified: bool = True
    immutable_original_watermark_verified: bool = False
    transport_continuity_verified: bool = False
    seven_symbol_mayak_parity_verified: bool = False
    legacy_mayak_retirement_allowed: bool = False


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise R11OriginalPublicationCorrelationUnverified(
            "R11_ORIGINAL_PUBLICATION_" + reason
        )


def _utc(value: object) -> datetime:
    _need(isinstance(value, datetime) and value.tzinfo is not None
          and value.utcoffset() == timedelta(0), "UTC_INVALID")
    assert isinstance(value, datetime)
    return value.astimezone(UTC)


def correlate_r11_original_page_with_publication(
    connection: Any, *, publication_id: int, batch: R11OptionalMayakBatch,
) -> R11OriginalPublicationCorrelation:
    """Validate one physical historical correlation under an immutable DB view.

    Caller owns REPEATABLE READ READ ONLY transaction. No DML, new socket or
    consumer hook. Interleaved SPOT/LINEAR frame numbers cannot infer gaps.
    An empty cursor is UNKNOWN, never zero market activity.
    """
    _need(type(publication_id) is int and publication_id > 0,
          "PUBLICATION_ID_INVALID")
    _need(isinstance(batch, R11OptionalMayakBatch), "BATCH_TYPE_INVALID")
    cursor = batch.cursor
    _need(
        cursor.product_category in ("SPOT", "LINEAR")
        and isinstance(cursor.source_session_id, str)
        and re.fullmatch(r"[0-9a-f]{32}", cursor.source_session_id) is not None
        and isinstance(cursor.transport_epoch_id, str)
        and re.fullmatch(r"[0-9a-f]{32}", cursor.transport_epoch_id) is not None
        and isinstance(cursor.producer_release_commit, str)
        and re.fullmatch(r"[0-9a-f]{40}", cursor.producer_release_commit) is not None
        and type(cursor.next_cursor) is int and cursor.next_cursor > 0
        and isinstance(cursor.next_digest, str)
        and _HASH.fullmatch(cursor.next_digest) is not None
        and batch.provider_transport_continuity_verified is False
        and batch.seven_symbol_panel_verified is False
        and batch.optional_rest_feature_parity_verified is False
        and batch.safe_to_switch_mayak_public_socket is False,
        "CURSOR_OR_FALSE_READINESS",
    )
    _need(
        connection.execute("SHOW transaction_isolation").fetchone()
        == ("repeatable read",)
        and connection.execute("SHOW transaction_read_only").fetchone()
        == ("on",),
        "READ_ONLY_REPEATABLE_READ_REQUIRED",
    )
    publication = connection.execute(
        """SELECT market_source_id,source_session_id,producer_release_commit,
                  as_of_second,publication_status
             FROM analytics.market_publications WHERE publication_id=%s""",
        (publication_id,),
    ).fetchone()
    _need(isinstance(publication, (tuple, list)) and len(publication) == 5,
          "PUBLICATION_NOT_FOUND")
    source, session, release, as_of, status = publication
    published = _utc(as_of)
    _need(
        source == BYBIT_PUBLIC_MARKET_SOURCE_ID
        and session == cursor.source_session_id
        and release == cursor.producer_release_commit
        and status == "WATERMARKS_ONLY",
        "PUBLICATION_IDENTITY_OR_STATE_CHANGED",
    )
    session_row = connection.execute(
        """SELECT state,market_source_id,producer_release_commit
             FROM analytics.native_public_trade_source_sessions
            WHERE source_session_id=%s""",
        (cursor.source_session_id,),
    ).fetchone()
    _need(
        isinstance(session_row, (tuple, list)) and len(session_row) == 3
        and session_row[0] in ("ACTIVE", "GAP")
        and session_row[1] == BYBIT_PUBLIC_MARKET_SOURCE_ID
        and session_row[2] == cursor.producer_release_commit,
        "SOURCE_SESSION_NOT_PROVEN",
    )
    physical = connection.execute(
        """SELECT frame_content_sha256,received_at,provider_frame_at
             FROM analytics.public_source_original_event_frames
            WHERE source_session_id=%s AND transport_epoch_id=%s
              AND product_category=%s AND source_frame_number=%s""",
        (cursor.source_session_id, cursor.transport_epoch_id,
         cursor.product_category, cursor.next_cursor),
    ).fetchone()
    _need(isinstance(physical, (tuple, list)) and len(physical) == 3,
          "ORIGINAL_FRAME_NOT_FOUND")
    digest, received, provider = physical
    _need(digest == cursor.next_digest, "ORIGINAL_FRAME_SHA_MISMATCH")
    _need(_utc(received) <= published and _utc(provider) <= published,
          "ORIGINAL_FRAME_AFTER_PUBLICATION")
    _need(
        all(
            event.product == cursor.product_category
            and type(event.source_frame_number) is int
            and 0 < event.source_frame_number <= cursor.next_cursor
            for event in batch.events
        ),
        "ORIGINAL_EVENT_OUT_OF_CURSOR_SCOPE",
    )
    return R11OriginalPublicationCorrelation(
        publication_id=publication_id,
        source_session_id=session,
        transport_epoch_id=cursor.transport_epoch_id,
        product_category=cursor.product_category,
        original_frame_number=cursor.next_cursor,
        original_frame_sha256=cursor.next_digest,
        publication_as_of=published,
        source_state=session_row[0],
        original_events_in_page=len(batch.events),
    )
