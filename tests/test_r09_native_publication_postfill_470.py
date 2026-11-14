"""#470 R09 source-first: original native MarketPublication postfill proof.

This suite proves only the read-only adapter, not signed Exchange OPEN/SL/TP,
first-fill trailing or LIVE readiness. No PostgreSQL/Exchange writes.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest

from bybit_workbench.market_data.p90l90_real_publication_postfill import (
    P90PGPostfillUnverified,
    read_r09_native_publication_postfill,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import TradeDirection
from bybit_workbench.universal_entry.p90l90_legacy_strategy import SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION

AT = datetime(2026, 11, 9, 4, 7, 30, tzinfo=UTC)
SESSION = "a" * 32
PRODUCER = "b" * 40
STRATEGY_COMMIT = "c" * 40
EPOCH = "p90-real-epoch-" + "d" * 40
SYMBOL = "ARBUSDT"
INSTRUMENT = bybit_instrument_id("linear", SYMBOL)
SOURCE = BYBIT_PUBLIC_MARKET_SOURCE_ID
FIRST = "original-one"
LATEST = "original-two"
PUB_AT = AT + timedelta(seconds=5)
NOW = AT + timedelta(seconds=6)


def position() -> StrategyPosition:
    return cast(StrategyPosition, SimpleNamespace(
        strategy_position_id="POS-source-r09",
        strategy_id=SUB_ID,
        strategy_version=VERSION,
        symbol=SYMBOL,
        identity_status="PROVEN",
        trading_account_id="ta-" + "e" * 32,
        strategy_execution_binding_id="binding-original",
        fill_at=AT,
        initial_protection_confirmed_at=AT + timedelta(seconds=2),
        actual_avg_fill=Decimal("0.1736275"),
        direction=TradeDirection.SHORT,
    ))


def native(
    index: int,
    exec_id: str,
    seq: int,
    at: datetime,
    price: object,
) -> dict[str, object]:
    return {
        "frame_number": 5 + index,
        "frame_index": 0,
        "market_source_id": SOURCE,
        "instrument_id": INSTRUMENT,
        "external_symbol": SYMBOL,
        "exchange_sequence": seq,
        "exec_id": exec_id,
        "event_at": at,
        "accepted_at": at + timedelta(milliseconds=100),
        "journal_at": at,
        "price": price,
        "quantity": Decimal("0.1"),
        "taker_side": "Buy",
        "source_transport": "BYBIT_WS_PUBLIC_TRADE",
        "producer_release_commit": PRODUCER,
    }


FIELDS = (
    "frame_number", "frame_index", "market_source_id", "instrument_id",
    "external_symbol", "exchange_sequence", "exec_id", "event_at",
    "accepted_at", "journal_at", "price", "quantity", "taker_side",
    "source_transport", "producer_release_commit",
)


class Cursor:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def fetchone(self) -> object | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[object]:
        return list(self.rows)


class FakePG:
    def __init__(self) -> None:
        self.isolation = "repeatable read"
        self.read_only = True
        self.strategy_epoch = (STRATEGY_COMMIT, "ACTIVE")
        self.session_rows = [(
            SESSION, SOURCE, PRODUCER, "ACTIVE", AT - timedelta(seconds=40),
            PUB_AT, None, None,
        )]
        self.pub_status = "WATERMARKS_ONLY"
        self.pub_at = PUB_AT
        self.publication_commit = PRODUCER
        self.native_ref: dict[str, object] = {
            "instrument_id": INSTRUMENT,
            "frame_number": 6,
            "frame_index": 0,
            "exchange_sequence": 102,
            "exec_id": LATEST,
            "event_at": AT + timedelta(seconds=3),
            "accepted_at": AT + timedelta(seconds=3, milliseconds=100),
        }
        self.anchor = (4, 0, 100, AT - timedelta(seconds=1))
        self.rows = [
            native(0, FIRST, 101, AT + timedelta(seconds=1), Decimal("0.1730")),
            native(1, LATEST, 102, AT + timedelta(seconds=3), Decimal("0.1720")),
        ]
        self.row_style = "dict"
        self.queries: list[str] = []

    def execute(self, sql: str, params: object = ()) -> Cursor:
        self.queries.append(sql)
        if sql == "SHOW transaction_isolation":
            return Cursor([(self.isolation,)])
        if sql == "SHOW transaction_read_only":
            return Cursor([("on" if self.read_only else "off",)])
        if "FROM strategy_entry.p90l90_real_source_epochs" in sql:
            assert params == (EPOCH,)
            return Cursor([self.strategy_epoch])
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            assert params == (SOURCE,)
            return Cursor(self.session_rows)
        if "FROM analytics.market_publications" in sql:
            assert params == (SOURCE, SESSION)
            return Cursor([(
                21, SESSION, self.publication_commit, self.pub_at,
                self.pub_status, {SYMBOL: self.native_ref},
            )])
        if "ORDER BY frame_number DESC" in sql:
            assert params == (SESSION, SOURCE, INSTRUMENT, SYMBOL, AT)
            return Cursor([self.anchor] if self.anchor is not None else [])
        if "JOIN analytics.native_public_trade_events" in sql:
            assert params == (
                SESSION, SOURCE, INSTRUMENT, SYMBOL, AT, self.pub_at, 257,
            )
            if self.row_style == "tuple":
                return Cursor([tuple(row[name] for name in FIELDS) for row in self.rows])
            return Cursor(self.rows)
        raise AssertionError(f"Unexpected R09 statement: {sql}")


def proof(db: FakePG, now: datetime = NOW):
    return read_r09_native_publication_postfill(
        db,
        position=position(),
        source_epoch_id=EPOCH,
        source_commit=STRATEGY_COMMIT,
        accepted_producer_commits=frozenset((PRODUCER,)),
        observed_at=now,
    )


@pytest.mark.parametrize("style", ["dict", "tuple"])
def test_original_postfill_uses_proven_pinned_epoch_and_mfe(style: str) -> None:
    db = FakePG()
    db.row_style = style
    source = proof(db)
    assert source.complete_native_window is True
    assert source.no_new_native_trade_rest_verified is False
    assert source.covered_through == PUB_AT
    assert source.source_commit == STRATEGY_COMMIT
    assert source.last_native_price == Decimal("0.1720")
    assert source.best_favorable_price == Decimal("0.1720")
    assert source.source_refs == (
        f"bybit:publicTrade:{SYMBOL}:{FIRST}",
        f"bybit:publicTrade:{SYMBOL}:{LATEST}",
    )
    assert all("INSERT" not in q and "UPDATE" not in q for q in db.queries)
    assert any("market_publications" in q for q in db.queries)
    assert any("native_public_trade_events" in q for q in db.queries)


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda db: setattr(db, "isolation", "read committed"), "SNAPSHOT_REQUIRED"),
        (lambda db: setattr(db, "read_only", False), "SNAPSHOT_REQUIRED"),
        (lambda db: setattr(db, "strategy_epoch", (STRATEGY_COMMIT, "GAP")),
         "STRATEGY_SOURCE_EPOCH"),
        (lambda db: setattr(db, "session_rows", []), "SOURCE_EPOCH_AMBIGUOUS"),
        (lambda db: setattr(db, "session_rows", db.session_rows * 2),
         "SOURCE_EPOCH_AMBIGUOUS"),
        (lambda db: setattr(
            db, "session_rows",
            [(SESSION, SOURCE, PRODUCER, "GAP", AT, PUB_AT, PUB_AT, "disconnected")],
        ), "SOURCE_SESSION_INVALID"),
        (lambda db: setattr(db, "publication_commit", "a" * 40),
         "PUBLICATION_EPOCH_OR_AGE"),
        (lambda db: setattr(db, "pub_status", "INVALID"),
         "PUBLICATION_EPOCH_OR_AGE"),
        (lambda db: setattr(db, "pub_at", AT + timedelta(seconds=1)),
         "PUBLICATION_EPOCH_OR_AGE"),
        (lambda db: db.native_ref.update(instrument_id="wrong-linear-market"),
         "LATEST_NATIVE_CURSOR"),
        (lambda db: db.native_ref.update(frame_number=8),
         "PUBLICATION_LATEST_ORIGINAL"),
        (lambda db: db.native_ref.update(exchange_sequence=103),
         "PUBLICATION_LATEST_ORIGINAL"),
        (lambda db: db.native_ref.update(exec_id="wrong"),
         "PUBLICATION_LATEST_ORIGINAL"),
        (lambda db: setattr(db, "anchor", None),
         "PREFILL_ORIGINAL_ANCHOR"),
        (lambda db: setattr(db, "anchor", (
            4, 0, 100, AT - timedelta(minutes=20),
        )), "PREFILL_ORIGINAL_ANCHOR_STALE"),
        (lambda db: setattr(db, "rows", []),
         "POSTFILL_ORIGINAL_TRADE_MISSING"),
        (lambda db: db.rows[0].update(instrument_id="spot-collision"),
         "ORIGINAL_NATIVE_SEQUENCE"),
        (lambda db: db.rows[0].update(producer_release_commit="a" * 40),
         "ORIGINAL_NATIVE_SEQUENCE"),
        (lambda db: db.rows[0].update(source_transport="BYBIT_REST_KLINE"),
         "ORIGINAL_NATIVE_SEQUENCE"),
        (lambda db: db.rows[0].update(price=Decimal("NaN")),
         "PRICE_INVALID"),
        (lambda db: db.rows[0].update(quantity=Decimal("0")),
         "PRICE_INVALID"),
        (lambda db: db.rows[1].update(exchange_sequence=99),
         "ORIGINAL_NATIVE_SEQUENCE"),
        (lambda db: db.rows[1].update(frame_number=4),
         "ORIGINAL_NATIVE_SEQUENCE"),
        (lambda db: db.rows[1].update(exec_id=FIRST),
         "ORIGINAL_NATIVE_SEQUENCE"),
        (lambda db: db.rows[1].update(journal_at=AT),
         "ORIGINAL_NATIVE_SEQUENCE"),
    ],
)
def test_r09_never_claims_mfe_when_source_proof_has_gap(change, reason: str) -> None:
    db = FakePG()
    change(db)
    with pytest.raises(P90PGPostfillUnverified, match=reason):
        proof(db)


def test_publication_stale_or_future_blocks_even_with_a_valid_native_tape() -> None:
    db = FakePG()
    with pytest.raises(P90PGPostfillUnverified, match="PUBLICATION_EPOCH_OR_AGE"):
        proof(db, NOW + timedelta(seconds=10))
    with pytest.raises(P90PGPostfillUnverified, match="PUBLICATION_EPOCH_OR_AGE"):
        proof(db, PUB_AT - timedelta(seconds=3))


def test_no_trade_is_not_relabelled_as_verified_zero_or_rest_gap() -> None:
    db = FakePG()
    db.rows = []
    with pytest.raises(
        P90PGPostfillUnverified, match="POSTFILL_ORIGINAL_TRADE_MISSING",
    ):
        proof(db)


def test_r09_read_module_makes_no_exchange_request_or_live_mutation() -> None:
    from pathlib import Path

    code = Path(
        "src/bybit_workbench/market_data/p90l90_real_publication_postfill.py"
    ).read_text()
    assert "urllib" not in code
    assert "requests." not in code
    assert "read_real_postfill_source_window" not in code
    assert "complete_native_window=True" in code
    assert "no_new_native_trade_rest_verified=False" in code
    assert "SHOW transaction_read_only" in code
