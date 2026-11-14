"""R11 source-only: exact original optional frame refs for one publication.

A hash-pinned physical frame ref is NOT evidence of provider continuity,
complete topics, BTC/ETH, or replacement readiness. No read-model writer.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID

_EPOCH = re.compile(r"^[a-f0-9]{32}$")
_SHA40 = re.compile(r"^[a-f0-9]{40}$")
_SHA64 = re.compile(r"^[a-f0-9]{64}$")
_PRODUCTS = ("SPOT", "LINEAR")


class R11PublicationOriginalRefsUnverified(RuntimeError):
    """Original frame publication source / cursor identity not verified."""


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise R11PublicationOriginalRefsUnverified(
            "R11_PUBLICATION_ORIGINAL_" + code
        )


def _utc(value: object) -> datetime:
    _need(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0),
        "CLOCK_NOT_UTC",
    )
    assert isinstance(value, datetime)
    return value.astimezone(UTC)


def collect_r11_original_optional_publication_refs(
    connection: Any, *,
    source_session_id: str,
    producer_release_commit: str,
    transport_epochs: dict[str, str],
    as_of_second: datetime,
) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    """Pin one last *committed* physical frame in each current product epoch.

    Called within the same transaction that inserts MarketPublication, after
    the producer has committed original provider frames. A reset selects only
    the current transport epoch. Interleaved global frame numbers are NOT
    treated as per-product continuous sequences. Missing rows are explicit.
    """
    second = _utc(as_of_second)
    _need(second.microsecond == 0, "BOUNDARY_NOT_SECOND")
    _need(
        isinstance(source_session_id, str)
        and _EPOCH.fullmatch(source_session_id) is not None
        and isinstance(producer_release_commit, str)
        and _SHA40.fullmatch(producer_release_commit) is not None
        and type(transport_epochs) is dict
        and set(transport_epochs) == set(_PRODUCTS)
        and all(
            isinstance(epoch, str) and _EPOCH.fullmatch(epoch) is not None
            for epoch in transport_epochs.values()
        ),
        "SOURCE_OR_TRANSPORT_IDENTITY_INVALID",
    )
    refs: dict[str, dict[str, object]] = {}
    coverage: dict[str, str] = {}
    for product in _PRODUCTS:
        epoch = transport_epochs[product]
        row = connection.execute(
            """SELECT market_source_id,producer_release_commit,
                      source_frame_number,frame_content_sha256,
                      received_at,provider_frame_at
                 FROM analytics.public_source_original_event_frames
                WHERE source_session_id=%s AND transport_epoch_id=%s
                  AND product_category=%s AND received_at<%s
                ORDER BY source_frame_number DESC LIMIT 1""",
            (source_session_id, epoch, product, second),
        ).fetchone()
        if row is None:
            coverage[product] = "NO_ORIGINAL_FRAME_IN_CURRENT_EPOCH"
            continue
        _need(
            isinstance(row, (tuple, list)) and len(row) == 6,
            "PHYSICAL_ROW_SHAPE_INVALID",
        )
        source, commit, number, digest, received, provider = row
        arrived = _utc(received)
        native = _utc(provider)
        _need(
            source == BYBIT_PUBLIC_MARKET_SOURCE_ID
            and commit == producer_release_commit
            and type(number) is int and number > 0
            and isinstance(digest, str) and _SHA64.fullmatch(digest) is not None
            and native <= arrived + timedelta(seconds=5)
            and arrived < second and native <= second,
            "PHYSICAL_ORIGINAL_FRAME_INVALID",
        )
        refs[product] = {
            "source_session_id": source_session_id,
            "transport_epoch_id": epoch,
            "product_category": product,
            "source_frame_number": number,
            "frame_content_sha256": digest,
            "received_at": arrived.isoformat(),
            "producer_release_commit": producer_release_commit,
        }
        coverage[product] = "PHYSICAL_FRAME_REF_ONLY"
    return refs, coverage
