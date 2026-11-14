"""#470 R11 original native LINEAR event pages, never compact 1s trade replay."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.mayak.native_trade_publication_reader import (
    R11OriginalNativeUnverified,
    read_r11_original_linear_page,
)

SYMBOL = "APTUSDT"
SOURCE = BYBIT_PUBLIC_MARKET_SOURCE_ID
INST = bybit_instrument_id("linear", SYMBOL)
SESSION = "a" * 32
COMMIT = "b" * 40
AS_OF = datetime(2026, 11, 14, 7, 2, 0, tzinfo=UTC)
NOW = AS_OF + timedelta(seconds=2)
EVENT1 = AS_OF - timedelta(seconds=3)
EVENT2 = AS_OF - timedelta(seconds=2)


def event(
    frame: int, index: int, identity: str, price: object,
    traded: datetime,
) -> tuple[object, ...]:
    return (
        frame, index, SOURCE, INST, SYMBOL, 1001, identity, traded,
        traded + timedelta(milliseconds=90), traded, price,
        Decimal("2"), "Buy", "BYBIT_WS_PUBLIC_TRADE", COMMIT,
    )


class Result:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class NativeFakePG:
    def __init__(self) -> None:
        self.isolation = "repeatable read"
        self.read_only = True
        self.sessions: list[tuple[str, str]] = [(SESSION, COMMIT)]
        self.pub_at = AS_OF
        self.pub_state = "WATERMARKS_ONLY"
        self.pub_commit = COMMIT
        self.pub_ref = {
            "instrument_id": INST, "frame_number": 5,
            "frame_index": 1, "exchange_sequence": 1001,
            "exec_id": "other-execution",
            "event_at": EVENT2.isoformat(),
            "accepted_at": (
                EVENT2 + timedelta(milliseconds=90)
            ).isoformat(),
        }
        self.rows = [
            event(5, 0, "first-execution", Decimal("100"), EVENT1),
            event(5, 1, "other-execution", Decimal("101"), EVENT2),
        ]
        self.anchor_present = True
        self.queries: list[str] = []

    def execute(self, sql: str, params=()) -> Result:
        self.queries.append(sql)
        if sql == "SHOW transaction_isolation":
            return Result([(self.isolation,)])
        if sql == "SHOW transaction_read_only":
            return Result([("on" if self.read_only else "off",)])
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            assert params == (SOURCE,)
            return Result(self.sessions)
        if "FROM analytics.market_publications" in sql:
            assert params == (SOURCE, SESSION)
            return Result([(
                101, SESSION, self.pub_commit, self.pub_at,
                self.pub_state, {SYMBOL: self.pub_ref},
            )])
        if (
            "FROM analytics.native_public_trade_source_events" in sql
            and "JOIN" not in sql
        ):
            session, market, inst, symbol, fnum, index = params
            assert (session, market, inst, symbol) == (
                SESSION, SOURCE, INST, SYMBOL,
            )
            found = next((
                row for row in self.rows if (row[0], row[1]) == (fnum, index)
            ), None)
            return Result([(found[5], found[6])] if (
                found is not None and self.anchor_present
            ) else [])
        if "JOIN analytics.native_public_trade_events" in sql:
            (
                session, market, inst, symbol,
                previous_frame, previous_idx,
                current_frame, current_idx, limit,
            ) = params
            assert (session, market, inst, symbol) == (
                SESSION, SOURCE, INST, SYMBOL,
            )
            assert limit >= 2
            matched = [
                row for row in self.rows
                if (previous_frame, previous_idx) < (row[0], row[1])
                <= (current_frame, current_idx)
            ]
            return Result(matched[:limit])
        raise AssertionError(f"Unexpected R11 SQL {sql}")


def read(
    db: NativeFakePG,
    *,
    symbol: str = SYMBOL,
    after: tuple[int, int] = (0, 0),
    max_events: int = 512,
    now: datetime = NOW,
):
    return read_r11_original_linear_page(
        db,
        symbol=symbol, observed_at=now,
        read_after=after, max_events=max_events,
    )


def test_original_same_sequence_siblings_preserved_in_native_frame_order() -> None:
    db = NativeFakePG()
    page = read(db)
    assert page.publication_id == 101
    assert page.source_session_id == SESSION
    assert page.publication_cursor == (5, 1)
    assert page.next_cursor == (5, 1)
    assert page.more_pages is False
    assert page.last_event_at_publication_cursor is True
    assert [x.exec_id for x in page.original_events] == [
        "first-execution", "other-execution",
    ]
    assert [x.seq for x in page.original_events] == [1001, 1001]
    assert [x.price for x in page.original_events] == [
        Decimal("100"), Decimal("101"),
    ]
    assert [x.source_ref for x in page.original_events] == [
        f"bybit:publicTrade:{SYMBOL}:first-execution",
        f"bybit:publicTrade:{SYMBOL}:other-execution",
    ]
    assert page.source_continuity_verified is False
    assert page.mayak_event_parity_verified is False
    assert page.mayak_cutover_ready is False
    assert all("UPDATE" not in sql and "INSERT" not in sql for sql in db.queries)


def test_read_after_must_be_real_exact_epoch_cursor_to_page() -> None:
    db = NativeFakePG()
    first = read(db, max_events=1)
    assert first.next_cursor == (5, 0)
    assert first.more_pages is True
    assert first.last_event_at_publication_cursor is False
    second = read(db, max_events=1, after=first.next_cursor)
    assert len(second.original_events) == 1
    assert second.original_events[0].exec_id == "other-execution"
    assert second.next_cursor == (5, 1)
    assert second.more_pages is False
    assert second.last_event_at_publication_cursor is True


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda db: setattr(db, "isolation", "read committed"),
         "ATOMIC_READ_ONLY"),
        (lambda db: setattr(db, "read_only", False),
         "ATOMIC_READ_ONLY"),
        (lambda db: setattr(db, "sessions", []), "ONE_ACTIVE_SOURCE"),
        (lambda db: setattr(db, "sessions", [(SESSION, COMMIT)] * 2),
         "ONE_ACTIVE_SOURCE"),
        (lambda db: setattr(db, "sessions", [("bad", COMMIT)]),
         "ONE_ACTIVE_SOURCE"),
        (lambda db: setattr(db, "pub_state", "GAP"),
         "PUBLICATION_IDENTITY_OR_AGE"),
        (lambda db: setattr(db, "pub_commit", "c" * 40),
         "PUBLICATION_IDENTITY_OR_AGE"),
        (lambda db: setattr(db, "pub_at", AS_OF - timedelta(seconds=40)),
         "PUBLICATION_IDENTITY_OR_AGE"),
        (lambda db: db.pub_ref.update(instrument_id="spot-collision"),
         "PUBLICATION_NATIVE_IDENTITY"),
        (lambda db: db.pub_ref.update(exec_id="wrong"),
         "PUBLICATION_LATEST_NATIVE"),
        (lambda db: db.pub_ref.update(exchange_sequence=2001),
         "PUBLICATION_LATEST_NATIVE"),
        (lambda db: db.rows.__setitem__(
            0, event(5, 0, "first-execution", Decimal("0"), EVENT1),
        ), "PRICE_OR_QUANTITY"),
        (lambda db: db.rows.__setitem__(
            0, event(5, 0, "first-execution", Decimal("NaN"), EVENT1),
        ), "PRICE_OR_QUANTITY"),
        (lambda db: db.rows.__setitem__(
            1, event(5, 1, "first-execution", Decimal("101"), EVENT2),
        ), "ORIGINAL_SEQUENCE"),
        (lambda db: db.rows.__setitem__(
            0, event(5, 0, "first-execution", Decimal("100"),
                     EVENT2 + timedelta(seconds=1)),
        ), "ORIGINAL_SEQUENCE"),
        (lambda db: db.rows.__setitem__(
            1, event(5, 1, "other-execution", Decimal("101"),
                     AS_OF + timedelta(seconds=1)),
        ), "ORIGINAL_SEQUENCE"),
        (lambda db: db.rows.__setitem__(
            0, (*db.rows[0][0:13], "BYBIT_REST_KLINE", COMMIT),
        ), "ORIGINAL_SEQUENCE"),
        (lambda db: db.rows.__setitem__(
            0, (*db.rows[0][0:14], "f" * 40),
        ), "ORIGINAL_SEQUENCE"),
        (lambda db: setattr(db, "rows", []), "ORIGINAL_EVENT"),
    ],
)
def test_r11_native_page_fails_closed_on_wrong_epoch_or_identity(
    change, reason: str,
) -> None:
    db = NativeFakePG()
    change(db)
    if reason == "ORIGINAL_EVENT":
        result = read(db)
        assert not result.original_events
        assert not result.last_event_at_publication_cursor
        assert not result.source_continuity_verified
    else:
        with pytest.raises(R11OriginalNativeUnverified, match=reason):
            read(db)


def test_source_is_five_not_seven_and_never_approves_mayak_replacement() -> None:
    for symbol in ("BTCUSDT", "ETHUSDT", "DOGEUSDT"):
        with pytest.raises(
            R11OriginalNativeUnverified, match="OUTSIDE_FIVE_NATIVE_CAPTURE",
        ):
            read(NativeFakePG(), symbol=symbol)
    db = NativeFakePG()
    db.anchor_present = False
    with pytest.raises(
        R11OriginalNativeUnverified, match="CURSOR_NOT_IN_ORIGINAL_EPOCH",
    ):
        read(db, after=(5, 0))


def test_unbounded_or_forged_pagination_cannot_be_accepted() -> None:
    for cursor, limit in [
        ((-1, 0), 512), ((1, -2), 512), ((9, 9), 512),
        ((0, 0), 0), ((0, 0), 513),
    ]:
        with pytest.raises(R11OriginalNativeUnverified):
            read(NativeFakePG(), after=cursor, max_events=limit)


def test_no_secondary_market_feed_and_no_financial_actions() -> None:
    source = Path(
        "src/bybit_workbench/mayak/native_trade_publication_reader.py"
    ).read_text()
    for forbidden in (
        "urllib.request", "websocket.create_connection", "requests.get(",
        "INSERT INTO", "UPDATE analytics.", "DELETE FROM",
        "SET ROLE", "ExchangeMutation", "COMPLETE_NATIVE_WINDOW=True",
    ):
        assert forbidden not in source
    assert "native_public_trade_source_events" in source
    assert "native_public_trade_events" in source
    assert "source_continuity_verified: bool = False" in source
    assert "mayak_cutover_ready: bool = False" in source
