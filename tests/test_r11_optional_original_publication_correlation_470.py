"""#470 R11: original cursor/publication correlation must stay fail-closed."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakBatch,
    R11OptionalMayakCursor,
)
from bybit_workbench.mayak.optional_original_publication_correlation import (
    R11OriginalPublicationCorrelationUnverified,
    correlate_r11_original_page_with_publication,
)

SESSION = "a" * 32
EPOCH = "b" * 32
RELEASE = "c" * 40
DIGEST = "d" * 64
AS_OF = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


class Result:
    def __init__(self, row: tuple[object, ...] | None):
        self.row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class Connection:
    def __init__(self) -> None:
        self.read_only = "on"
        self.isolation = "repeatable read"
        self.publication = (
            BYBIT_PUBLIC_MARKET_SOURCE_ID, SESSION, RELEASE,
            AS_OF, "WATERMARKS_ONLY",
        )
        self.source = ("ACTIVE", BYBIT_PUBLIC_MARKET_SOURCE_ID, RELEASE)
        self.frame = (DIGEST, AS_OF - timedelta(milliseconds=20),
                      AS_OF - timedelta(milliseconds=30))
        self.sql: list[str] = []

    def execute(self, query: str, params: object = None) -> Result:
        self.sql.append(query)
        if query == "SHOW transaction_isolation":
            return Result((self.isolation,))
        if query == "SHOW transaction_read_only":
            return Result((self.read_only,))
        if "FROM analytics.market_publications" in query:
            assert params == (7,)
            return Result(self.publication)
        if "FROM analytics.native_public_trade_source_sessions" in query:
            assert params == (SESSION,)
            return Result(self.source)
        if "FROM analytics.public_source_original_event_frames" in query:
            assert params == (SESSION, EPOCH, "SPOT", 9)
            return Result(self.frame)
        raise AssertionError(query)


def batch() -> R11OptionalMayakBatch:
    return R11OptionalMayakBatch(
        cursor=R11OptionalMayakCursor(
            source_session_id=SESSION,
            transport_epoch_id=EPOCH,
            product_category="SPOT",
            producer_release_commit=RELEASE,
            next_cursor=9,
            next_digest=DIGEST,
            books=(),
        ),
        events=(),
        has_more_pages=False,
    )


def test_readonly_physical_correlation_never_grants_cutover() -> None:
    conn = Connection()
    checked = correlate_r11_original_page_with_publication(
        conn, publication_id=7, batch=batch(),
    )
    assert checked.physical_correlation_verified
    assert checked.original_frame_number == 9
    assert checked.original_events_in_page == 0
    assert checked.source_state == "ACTIVE"
    assert not checked.immutable_original_watermark_verified
    assert not checked.transport_continuity_verified
    assert not checked.seven_symbol_mayak_parity_verified
    assert not checked.legacy_mayak_retirement_allowed
    assert all(not any(word in sql.upper().split() for word in
                       ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER"))
               for sql in conn.sql)


@pytest.mark.parametrize("mutation", [
    "not_read_only", "wrong_isolation", "wrong_session", "wrong_release",
    "wrong_status", "missing_pub", "source_gap", "missing_source",
    "wrong_sha", "missing_frame", "future_frame", "unknown_cursor",
    "false_ready",
])
def test_negative_fail_closed(mutation: str) -> None:
    conn = Connection()
    item = batch()
    if mutation == "not_read_only":
        conn.read_only = "off"
    elif mutation == "wrong_isolation":
        conn.isolation = "read committed"
    elif mutation == "wrong_session":
        assert conn.publication is not None
        conn.publication = (conn.publication[0], "e" * 32, *conn.publication[2:])
    elif mutation == "wrong_release":
        assert conn.publication is not None
        conn.publication = (*conn.publication[:2], "f" * 40,
                            *conn.publication[3:])
    elif mutation == "wrong_status":
        assert conn.publication is not None
        conn.publication = (*conn.publication[:4], "READY")
    elif mutation == "missing_pub":
        conn.publication = None
    elif mutation == "source_gap":
        conn.source = ("GAP", "wrong-source", RELEASE)
    elif mutation == "missing_source":
        conn.source = None
    elif mutation == "wrong_sha":
        conn.frame = ("f" * 64, *conn.frame[1:])
    elif mutation == "missing_frame":
        conn.frame = None
    elif mutation == "future_frame":
        conn.frame = (DIGEST, AS_OF + timedelta(milliseconds=1),
                      AS_OF - timedelta(milliseconds=1))
    elif mutation == "unknown_cursor":
        item = replace(item, cursor=replace(item.cursor,
                                            next_cursor=0, next_digest=None))
    elif mutation == "false_ready":
        item = replace(item, safe_to_switch_mayak_public_socket=True)
    with pytest.raises(R11OriginalPublicationCorrelationUnverified):
        correlate_r11_original_page_with_publication(
            conn, publication_id=7, batch=item,
        )


def test_gap_is_not_continuity_even_if_physical_frame_matches() -> None:
    conn = Connection()
    conn.source = ("GAP", BYBIT_PUBLIC_MARKET_SOURCE_ID, RELEASE)
    checked = correlate_r11_original_page_with_publication(
        conn, publication_id=7, batch=batch(),
    )
    assert checked.source_state == "GAP"
    assert not checked.transport_continuity_verified
    assert not checked.legacy_mayak_retirement_allowed
