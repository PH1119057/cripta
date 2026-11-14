"""#470 R11: original optional PG frames read from one immutable source epoch.

Disposable tuple-row adapter only; no producer, schema install, market sockets,
Exchange requests or real PostgreSQL. Full MAYAK event parity stays UNKNOWN.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.market_data.public_source_event_codec import (
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.r11_optional_original_frame_journal import (
    R11OriginalPublicFrame,
    make_r11_original_optional_frame,
)
from bybit_workbench.mayak.optional_original_public_frame_reader import (
    R11OriginalFrameReadUnverified,
    read_r11_optional_original_frame_page,
)

SYMBOL = "APTUSDT"
SOURCE = "ms-bybit-mainnet-public-v1"
SESSION = "a" * 32
TRANSPORT = "c" * 32
RELEASE = "b" * 40
AT = datetime(2026, 11, 14, 7, 50, 0, tzinfo=UTC)
NOW = AT + timedelta(seconds=1)
MSEC = int(AT.timestamp() * 1000)


def original_frame(
    family: str = "SPOT_TRADE", *, number: int = 1,
    product: str = "SPOT",
) -> R11OriginalPublicFrame:
    if family == "SPOT_TRADE":
        raw = {
            "topic": f"publicTrade.{SYMBOL}", "type": "snapshot",
            "ts": MSEC,
            "data": [
                {
                    "s": SYMBOL, "T": MSEC, "p": "100.2", "v": "3",
                    "S": "Buy", "seq": 900, "i": "exec-a",
                },
                {
                    "s": SYMBOL, "T": MSEC + 1, "p": "100.3", "v": "2",
                    "S": "Sell", "seq": 900, "i": "exec-b",
                },
            ],
        }
    elif family == "ORDERBOOK_50":
        raw = {
            "topic": f"orderbook.50.{SYMBOL}", "type": "snapshot",
            "ts": MSEC,
            "data": {
                "s": SYMBOL, "seq": 101, "u": 88,
                "b": [["100.1", "2"]], "a": [["100.2", "3"]],
            },
        }
    else:
        raise AssertionError(family)
    facts = decode_bybit_public_frame(
        raw, product=product, allowed_symbols=frozenset((SYMBOL,)),
        received_at=NOW,
    )
    return make_r11_original_optional_frame(
        source_session_id=SESSION,
        transport_epoch_id=TRANSPORT,
        producer_release_commit=RELEASE,
        product=product,
        source_frame_number=number,
        message=raw, facts=facts, received_at=NOW,
    )


def physical_row(frame: R11OriginalPublicFrame) -> tuple[object, ...]:
    return (
        frame.source_session_id, frame.transport_epoch_id,
        frame.market_source_id, "venue-bybit",
        frame.instrument_id, frame.product_category,
        frame.external_symbol, frame.fact_family, frame.source_topic,
        frame.source_frame_number, frame.frame_type,
        frame.provider_frame_at, frame.source_event_first_at,
        frame.source_event_last_at, frame.received_at,
        frame.provider_frame, list(frame.decoded_original_facts),
        frame.frame_content_sha256, frame.producer_release_commit,
    )


class Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakePG:
    def __init__(self) -> None:
        self.isolation = "repeatable read"
        self.read_only = True
        self.session_state = "ACTIVE"
        self.session_producer = RELEASE
        self.physical_rows = [
            physical_row(original_frame()),
            physical_row(original_frame(
                "ORDERBOOK_50", number=3,
            )),
        ]
        self.queries: list[str] = []

    def execute(self, sql: str, params=()) -> Rows:
        self.queries.append(sql)
        if sql == "SHOW transaction_isolation":
            return Rows([(self.isolation,)])
        if sql == "SHOW transaction_read_only":
            return Rows([("on" if self.read_only else "off",)])
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            assert params == (SESSION,)
            return Rows([(SOURCE, self.session_producer, self.session_state)])
        if (
            "SELECT frame_content_sha256" in sql
            and "FROM analytics.public_source_original_event_frames" in sql
        ):
            session, transport, product, frame_id = params
            assert (session, transport, product) == (
                SESSION, TRANSPORT, "SPOT",
            )
            found = next((
                row for row in self.physical_rows
                if row[9] == frame_id
            ), None)
            return Rows([(found[17],)] if found else [])
        if "SELECT source_session_id" in sql and (
            "FROM analytics.public_source_original_event_frames" in sql
        ):
            session, transport, product, after, limit = params
            assert (session, transport, product) == (
                SESSION, TRANSPORT, "SPOT",
            )
            return Rows([
                row for row in self.physical_rows if row[9] > after
            ][:limit])
        raise AssertionError(f"unexpected SQL {sql}")


def read(
    db: FakePG,
    *,
    after: int = 0,
    after_digest: str | None = None,
    max_frames: int = 128,
):
    return read_r11_optional_original_frame_page(
        db, source_session_id=SESSION, transport_epoch_id=TRANSPORT,
        product_category="SPOT", after_frame=after,
        after_digest=after_digest, max_frames=max_frames,
    )


def test_bounded_pages_keep_real_spot_trade_twins_and_original_order() -> None:
    db = FakePG()
    one = read(db, max_frames=1)
    assert one.source_session_id == SESSION
    assert one.transport_epoch_id == TRANSPORT
    assert one.source_state == "ACTIVE"
    assert one.requested_after == 0
    assert one.next_cursor == 1
    assert one.more_pages is True
    assert one.frames[0].fact_family == "SPOT_TRADE"
    assert [x["exec_id"] for x in one.frames[0].decoded_original_facts] == [
        "exec-a", "exec-b",
    ]
    assert [x["exchange_sequence"] for x in one.frames[0].decoded_original_facts] == [
        900, 900,
    ]
    two = read(
        db, after=one.next_cursor,
        after_digest=one.next_digest, max_frames=1,
    )
    assert two.requested_after == 1
    assert two.next_cursor == 3  # global source frames need not be contiguous.
    assert two.more_pages is False
    assert two.frames[0].fact_family == "ORDERBOOK_50"
    assert two.frames[0].provider_frame["data"]["seq"] == 101
    assert two.frames[0].decoded_original_facts[0][
        "exchange_sequence"
    ] == 101
    assert two.next_digest == two.frames[-1].frame_content_sha256
    assert two.market_publication_cursor_verified is False
    assert two.provider_continuity_verified is False
    assert two.seven_symbol_parity_verified is False
    assert two.mayak_cutover_ready is False
    assert all(sql.lstrip().upper().startswith(("SHOW", "SELECT"))
               for sql in db.queries)


def test_gapped_session_and_silent_page_do_not_claim_no_trade() -> None:
    db = FakePG()
    db.session_state = "GAP"
    db.physical_rows = []
    page = read(db)
    assert page.source_state == "GAP"
    assert page.frames == ()
    assert page.next_cursor == 0
    assert page.next_digest is None
    assert page.more_pages is False
    assert page.provider_continuity_verified is False
    assert page.mayak_cutover_ready is False


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda db: setattr(db, "isolation", "read committed"),
         "IMMUTABLE_READ_ONLY"),
        (lambda db: setattr(db, "read_only", False),
         "IMMUTABLE_READ_ONLY"),
        (lambda db: setattr(db, "session_producer", "c" * 39),
         "SOURCE_SESSION_IDENTITY"),
        (lambda db: setattr(db, "session_state", "UNVERIFIED"),
         "SOURCE_SESSION_IDENTITY"),
        (lambda db: db.physical_rows.__setitem__(
            0, tuple(
                "wrong" if i == 4 else item
                for i, item in enumerate(db.physical_rows[0])
            ),
        ), "PHYSICAL_IDENTITY"),
        (lambda db: db.physical_rows.__setitem__(
            0, tuple(
                "BTCUSDT" if i == 6 else item
                for i, item in enumerate(db.physical_rows[0])
            ),
        ), "PHYSICAL_IDENTITY"),
        (lambda db: db.physical_rows.__setitem__(
            0, tuple(
                "f" * 64 if i == 17 else item
                for i, item in enumerate(db.physical_rows[0])
            ),
        ), "PHYSICAL_FACT_OR_HASH"),
        (lambda db: db.physical_rows.__setitem__(
            0, tuple(
                AT + timedelta(seconds=3) if i == 11 else item
                for i, item in enumerate(db.physical_rows[0])
            ),
        ), "PHYSICAL_FACT_OR_HASH"),
        (lambda db: db.physical_rows.__setitem__(
            0, tuple(
                "LINEAR" if i == 5 else item
                for i, item in enumerate(db.physical_rows[0])
            ),
        ), "PHYSICAL_IDENTITY"),
        (lambda db: db.physical_rows.reverse(),
         "PHYSICAL_IDENTITY_OR_ORDER"),
    ],
)
def test_physical_original_page_fails_closed_for_identity_or_hash(
    change, reason: str,
) -> None:
    db = FakePG()
    change(db)
    with pytest.raises(R11OriginalFrameReadUnverified, match=reason):
        read(db)


def test_resume_requires_real_row_and_matching_sha() -> None:
    db = FakePG()
    with pytest.raises(
        R11OriginalFrameReadUnverified, match="SCOPE_OR_CURSOR",
    ):
        read(db, after=1)
    with pytest.raises(
        R11OriginalFrameReadUnverified, match="CURSOR_HASH",
    ):
        read(db, after=1, after_digest="f" * 64)
    with pytest.raises(
        R11OriginalFrameReadUnverified, match="CURSOR_HASH",
    ):
        read(db, after=2, after_digest="f" * 64)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_session_id": "bad"},
        {"transport_epoch_id": "bad"},
        {"product_category": "INVERSE"},
        {"product_category": "OPTIONS"},
        {"max_frames": 129},
        {"max_frames": 0},
        {"after_frame": -1},
        {"after_frame": True},
        {"after_frame": 0, "after_digest": "a" * 64},
    ],
)
def test_reader_rejects_unapproved_product_epoch_or_unbounded_page(kwargs) -> None:
    request = {
        "source_session_id": SESSION,
        "transport_epoch_id": TRANSPORT,
        "product_category": "SPOT",
    }
    request.update(kwargs)
    with pytest.raises(
        R11OriginalFrameReadUnverified, match="SCOPE_OR_CURSOR",
    ):
        read_r11_optional_original_frame_page(FakePG(), **request)


def test_reader_is_strict_source_only_with_no_false_publication() -> None:
    code = Path(
        "src/bybit_workbench/mayak/optional_original_public_frame_reader.py"
    ).read_text()
    assert "psycopg.connect" not in code
    assert "urllib" not in code
    assert "websocket.create_connection" not in code
    assert "INSERT INTO" not in code
    assert "DELETE FROM" not in code
    assert "UPDATE " not in code
    assert "market_publication_cursor_verified: bool = False" in code
    assert "provider_continuity_verified: bool = False" in code
    assert "seven_symbol_parity_verified: bool = False" in code
    assert "mayak_cutover_ready: bool = False" in code
