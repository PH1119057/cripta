"""#470 R11: immutable MarketPublication-bound original MAYAK event pages.

This source-only reader MUST NOT be promoted to live MAYAK consumer without
seven-symbol coverage, optional REST, source continuity, durable checkpoint,
Dispatcher parity and controlled operator-owned physical cutover.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakBatch,
    R11OptionalMayakCursor,
    R11OptionalMayakReplayUnverified,
    project_r11_optional_mayak_page,
)
from bybit_workbench.mayak.optional_original_public_frame_reader import (
    R11OriginalFrameReadUnverified,
    read_r11_optional_original_frame_page,
)

_SHA32 = re.compile(r"^[0-9a-f]{32}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


class R11PinnedMayakPublicationUnverified(RuntimeError):
    """Cannot prove one immutable publication's original event scope."""


@dataclass(frozen=True, slots=True)
class R11PinnedMayakPublicationPage:
    publication_id: int
    source_session_id: str
    transport_epoch_id: str
    product_category: str
    publication_as_of: datetime
    pinned_frame_number: int
    pinned_frame_sha256: str
    batch: R11OptionalMayakBatch
    # Physical publication cursor is proven; original WS completeness is NOT.
    physical_published_cursor_verified: bool = True
    provider_transport_continuity_verified: bool = False
    seven_symbol_analytical_parity_verified: bool = False
    mayak_ws_retirement_allowed: bool = False


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise R11PinnedMayakPublicationUnverified(
            "R11_PINNED_MAYAK_" + code
        )


def _utc(value: object) -> datetime:
    _need(
        isinstance(value, datetime) and value.tzinfo is not None
        and value.utcoffset() == timedelta(0),
        "PUBLICATION_UTC_INVALID",
    )
    assert isinstance(value, datetime)
    return value.astimezone(UTC)


def read_r11_pinned_mayak_optional_page(
    connection: Any, *,
    publication_id: int,
    product_category: str,
    previous: R11OptionalMayakCursor | None = None,
    max_frames: int = 128,
) -> R11PinnedMayakPublicationPage:
    """Read one product page up to the exact original SHA-pinned publication.

    Caller owns a REPEATABLE READ READ ONLY database transaction. A silent
    page, source gap or transport-epoch reset never establishes continuity.
    """
    _need(
        type(publication_id) is int and publication_id > 0
        and product_category in ("SPOT", "LINEAR")
        and type(max_frames) is int and 1 <= max_frames <= 128
        and (previous is None
             or isinstance(previous, R11OptionalMayakCursor)),
        "ARGUMENTS_INVALID",
    )
    _need(
        connection.execute("SHOW transaction_isolation").fetchone()
        == ("repeatable read",)
        and connection.execute("SHOW transaction_read_only").fetchone()
        == ("on",),
        "ATOMIC_READ_ONLY_REQUIRED",
    )
    row = connection.execute(
        """SELECT market_source_id,source_session_id,producer_release_commit,
                  as_of_second,publication_status,original_optional_frame_refs,
                  original_optional_frame_coverage
             FROM analytics.market_publications
            WHERE publication_id=%s""",
        (publication_id,),
    ).fetchone()
    _need(isinstance(row, (tuple, list)) and len(row) == 7,
          "PUBLICATION_NOT_FOUND")
    source, session, release, at, status, refs, coverage = row
    published = _utc(at)
    _need(
        source == BYBIT_PUBLIC_MARKET_SOURCE_ID
        and isinstance(session, str) and _SHA32.fullmatch(session) is not None
        and isinstance(release, str) and _SHA40.fullmatch(release) is not None
        and status == "WATERMARKS_ONLY"
        and published.microsecond == 0
        and isinstance(refs, dict) and isinstance(coverage, dict),
        "PUBLICATION_SCOPE_UNVERIFIED",
    )
    ref = refs.get(product_category)
    _need(
        isinstance(ref, dict)
        and coverage.get(product_category) == "PHYSICAL_FRAME_REF_ONLY",
        "ORIGINAL_COVERAGE_MISSING",
    )
    epoch = ref.get("transport_epoch_id")
    number = ref.get("source_frame_number")
    digest = ref.get("frame_content_sha256")
    original_received = ref.get("received_at")
    _need(
        ref.get("source_session_id") == session
        and ref.get("product_category") == product_category
        and ref.get("producer_release_commit") == release
        and isinstance(epoch, str) and _SHA32.fullmatch(epoch) is not None
        and type(number) is int and number > 0
        and isinstance(digest, str) and _SHA64.fullmatch(digest) is not None
        and isinstance(original_received, str),
        "ORIGINAL_CURSOR_IDENTITY_INVALID",
    )
    try:
        received = datetime.fromisoformat(original_received)
    except ValueError as exc:
        raise R11PinnedMayakPublicationUnverified(
            "R11_PINNED_MAYAK_ORIGINAL_CLOCK_INVALID"
        ) from exc
    _need(_utc(received) < published, "ORIGINAL_AFTER_PUBLICATION")
    if previous is not None:
        _need(
            previous.source_session_id == session
            and previous.transport_epoch_id == epoch
            and previous.product_category == product_category
            and previous.producer_release_commit == release
            and previous.next_cursor <= number,
            "PREVIOUS_CURSOR_OR_EPOCH_DRIFT",
        )
    after = previous.next_cursor if previous is not None else 0
    old_digest = previous.next_digest if previous is not None else None
    try:
        page = read_r11_optional_original_frame_page(
            connection,
            source_session_id=session,
            transport_epoch_id=epoch,
            product_category=product_category,
            after_frame=after, after_digest=old_digest,
            through_frame=number, max_frames=max_frames,
        )
        batch = project_r11_optional_mayak_page(
            page, previous=previous,
        )
    except (
        R11OriginalFrameReadUnverified, R11OptionalMayakReplayUnverified,
    ) as exc:
        raise R11PinnedMayakPublicationUnverified(
            "R11_PINNED_MAYAK_PHYSICAL_REPLAY_REJECTED"
        ) from exc
    _need(
        batch.cursor.next_cursor <= number
        and (batch.has_more_pages or (
            batch.cursor.next_cursor == number
            and batch.cursor.next_digest == digest
        )),
        "UNREACHED_PUBLICATION_CURSOR",
    )
    return R11PinnedMayakPublicationPage(
        publication_id=publication_id,
        source_session_id=session,
        transport_epoch_id=epoch,
        product_category=product_category,
        publication_as_of=published,
        pinned_frame_number=number,
        pinned_frame_sha256=digest,
        batch=batch,
    )
