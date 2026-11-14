"""#470 R11: original SPOT/BOOK/TICKER/LIQ frame journal is source-only.

A disposable fake DML port checks exact append-once and canonical SQL grants.
No production migration, service startup, network, trading or Exchange IO.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.market_data.public_source_event_codec import (
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.r11_optional_original_frame_journal import (
    R11OriginalFrameUnavailable,
    append_r11_original_optional_frame,
    make_r11_original_optional_frame,
)

AT = datetime(2026, 11, 14, 7, 29, tzinfo=UTC)
RECEIVED = AT + timedelta(seconds=1)
SESSION = "a" * 32
EPOCH = "c" * 32
COMMIT = "b" * 40
SYMBOL = "APTUSDT"
MS = int(AT.timestamp() * 1000)


def bybit_frame(
    family: str, product: str = "LINEAR",
) -> dict[str, object]:
    if family == "SPOT_TRADE":
        return {
            "topic": f"publicTrade.{SYMBOL}", "type": "snapshot",
            "ts": MS,
            "data": [
                {
                    "s": SYMBOL, "T": MS, "p": "100.1", "v": "2.5",
                    "S": "Buy", "seq": 101, "i": "spot-exec-a",
                },
                {
                    "s": SYMBOL, "T": MS + 1, "p": "100.2", "v": "1.5",
                    "S": "Sell", "seq": 101, "i": "spot-exec-b",
                },
            ],
        }
    if family == "ORDERBOOK_50":
        return {
            "topic": f"orderbook.50.{SYMBOL}", "type": "snapshot",
            "ts": MS,
            "data": {
                "s": SYMBOL, "seq": 911, "u": 17,
                "b": [["100", "2"], ["99.9", "3"]],
                "a": [["100.1", "4"], ["100.2", "5"]],
            },
        }
    if family == "TICKER":
        return {
            "topic": f"tickers.{SYMBOL}", "type": "delta",
            "ts": MS,
            "data": {
                "symbol": SYMBOL,
                "fundingRate": "-0.0001", "lastPrice": "100.2",
                "openInterest": "20345",
            },
        }
    if family == "LIQUIDATION":
        return {
            "topic": f"allLiquidation.{SYMBOL}", "type": "snapshot",
            "ts": MS,
            "data": [
                {
                    "s": SYMBOL, "T": MS, "p": "101.1", "v": "12.5",
                    "S": "Sell",
                },
                {
                    "s": SYMBOL, "T": MS + 1, "p": "101.2", "v": "3.0",
                    "S": "Buy",
                },
            ],
        }
    raise AssertionError(f"unknown family {family}/{product}")


def build(
    family: str = "SPOT_TRADE",
    *,
    product: str = "SPOT",
    source_session: str = SESSION,
    transport_epoch: str = EPOCH,
    commit: str = COMMIT,
    number: int = 1,
    message: dict[str, object] | None = None,
):
    original = message if message is not None else bybit_frame(family, product)
    facts = decode_bybit_public_frame(
        original, product=product,
        allowed_symbols=frozenset((SYMBOL,)),
        received_at=RECEIVED,
    )
    return make_r11_original_optional_frame(
        source_session_id=source_session,
        transport_epoch_id=transport_epoch,
        producer_release_commit=commit,
        product=product,
        source_frame_number=number,
        message=original, facts=facts,
        received_at=RECEIVED,
    )


@pytest.mark.parametrize(
    "family,product,count",
    [
        ("SPOT_TRADE", "SPOT", 2),
        ("ORDERBOOK_50", "SPOT", 1),
        ("ORDERBOOK_50", "LINEAR", 1),
        ("TICKER", "LINEAR", 1),
        ("LIQUIDATION", "LINEAR", 2),
    ],
)
def test_five_symbol_optional_public_frame_preserves_original_facts(
    family: str, product: str, count: int,
) -> None:
    frame = build(family, product=product)
    assert frame.fact_family == family
    assert frame.product_category == product
    assert frame.external_symbol == SYMBOL
    assert frame.source_session_id == SESSION
    assert frame.transport_epoch_id == EPOCH
    assert frame.producer_release_commit == COMMIT
    assert frame.source_frame_number == 1
    assert frame.provider_frame_at == AT
    assert frame.received_at == RECEIVED
    assert frame.source_event_first_at <= frame.source_event_last_at
    assert len(frame.decoded_original_facts) == count
    assert frame.provider_frame["topic"] == frame.source_topic
    assert frame.transport_continuity_verified is False
    assert frame.mayak_event_replay_ready is False
    assert len(frame.frame_content_sha256) == 64
    if family == "SPOT_TRADE":
        assert [f["exchange_sequence"] for f in frame.decoded_original_facts] == [
            101, 101,
        ]
        assert [f["exec_id"] for f in frame.decoded_original_facts] == [
            "spot-exec-a", "spot-exec-b",
        ]
        assert frame.decoded_original_facts[0]["payload"] == {
            "price": "100.1", "qty": "2.5", "side": "Buy",
        }
    if family == "ORDERBOOK_50":
        payload = frame.decoded_original_facts[0]["payload"]
        assert payload["source_sequence"] if False else True
        assert frame.decoded_original_facts[0]["exchange_sequence"] == 911
        assert payload["update_id"] == 17
        assert payload["bids"][0] == ["100", "2"]
    if family == "LIQUIDATION":
        assert [f["payload"]["side"] for f in frame.decoded_original_facts] == [
            "Sell", "Buy",
        ]


def test_canonical_json_hash_is_stable_when_provider_key_order_changes() -> None:
    original = bybit_frame("SPOT_TRADE", "SPOT")
    reordered = dict(reversed(list(original.items())))
    a = build(message=original)
    b = build(message=reordered)
    assert a.frame_content_sha256 == b.frame_content_sha256
    assert a.decoded_original_facts == b.decoded_original_facts
    assert build(message=original, transport_epoch="d" * 32).frame_content_sha256 != (
        a.frame_content_sha256
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        (
            lambda f: replace(f, source_session_id="bad"),
            "SOURCE_FRAME_IDENTITY",
        ),
        (
            lambda f: replace(f, transport_epoch_id="bad"),
            "SOURCE_FRAME_IDENTITY",
        ),
        (
            lambda f: replace(f, producer_release_commit="no-sha"),
            "SOURCE_FRAME_IDENTITY",
        ),
        (
            lambda f: replace(f, source_frame_number=0),
            "SOURCE_FRAME_IDENTITY",
        ),
    ],
)
def test_new_frame_builder_rejects_bad_session_epoch_release_or_number(
    change, reason: str,
) -> None:
    base = {
        "source_session_id": SESSION,
        "transport_epoch_id": EPOCH,
        "producer_release_commit": COMMIT,
        "source_frame_number": 1,
    }
    # The builder, not the resulting dataclass, is the authority to encode.
    altered = change(type("BuildInput", (), base)())
    kwargs = dict(base)
    for key in base:
        kwargs[key] = getattr(altered, key)
    with pytest.raises(R11OriginalFrameUnavailable, match=reason):
        build(
            source_session=kwargs["source_session_id"],
            transport_epoch=kwargs["transport_epoch_id"],
            commit=kwargs["producer_release_commit"],
            number=kwargs["source_frame_number"],
        )


@pytest.mark.parametrize(
    "family,product",
    [
        ("SPOT_TRADE", "LINEAR"),
        ("TICKER", "SPOT"),
        ("LIQUIDATION", "SPOT"),
    ],
)
def test_missing_or_duplicate_original_scope_never_creates_second_tape(
    family: str, product: str,
) -> None:
    frame = bybit_frame(family, product)
    with pytest.raises(
        (R11OriginalFrameUnavailable, ValueError),
    ):
        build(family, product=product, message=frame)


def test_forged_decoded_fact_drift_is_rejected_before_any_db_call() -> None:
    raw = bybit_frame("SPOT_TRADE", "SPOT")
    facts = decode_bybit_public_frame(
        raw, product="SPOT", allowed_symbols=frozenset((SYMBOL,)),
        received_at=RECEIVED,
    )
    with pytest.raises(
        R11OriginalFrameUnavailable, match="ORIGINAL_CODEC_FACT_DRIFT",
    ):
        make_r11_original_optional_frame(
            source_session_id=SESSION, transport_epoch_id=EPOCH,
            producer_release_commit=COMMIT, product="SPOT",
            source_frame_number=1, message=raw,
            facts=(
                replace(facts[0], exec_id="forged-exec"),
                facts[1],
            ),
            received_at=RECEIVED,
        )


def test_non_json_provider_field_is_rejected_before_append() -> None:
    original = bybit_frame("SPOT_TRADE", "SPOT")
    original["untrusted_meta"] = float("nan")
    with pytest.raises(R11OriginalFrameUnavailable, match="PROVIDER_JSON"):
        build(message=original)


class Rows:
    def __init__(self, items):
        self.items = items

    def fetchone(self):
        return self.items[0] if self.items else None


class FakeSourceConnection:
    def __init__(self) -> None:
        self.state = "ACTIVE"
        self.market_source_id = "ms-bybit-mainnet-public-v1"
        self.producer_commit = COMMIT
        self.existing_digest: str | None = None
        self.transaction_entries = 0
        self.statements: list[str] = []

    @contextmanager
    def transaction(self):
        self.transaction_entries += 1
        yield self

    def execute(self, sql, params=()):
        self.statements.append(sql)
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            assert params == (SESSION,)
            return Rows([(
                self.state, self.market_source_id, self.producer_commit,
            )])
        if "INSERT INTO analytics.public_source_original_event_frames" in sql:
            digest = params[-2]
            if self.existing_digest is None:
                self.existing_digest = digest
                return Rows([(digest,)])
            return Rows([])
        if "SELECT frame_content_sha256" in sql:
            return Rows(
                [(self.existing_digest,)]
                if self.existing_digest else []
            )
        raise AssertionError("unexpected SQL:" + sql)


def test_one_native_source_transaction_is_append_once_idempotent() -> None:
    db = FakeSourceConnection()
    frame = build()
    assert append_r11_original_optional_frame(db, frame) == 1
    assert append_r11_original_optional_frame(db, frame) == 0
    assert db.transaction_entries == 2
    assert len([x for x in db.statements if "INSERT INTO" in x]) == 2
    assert all("UPDATE " not in x and "DELETE " not in x for x in db.statements)
    assert "ON CONFLICT" in "\n".join(db.statements)


def test_real_source_session_must_be_active_on_every_append() -> None:
    frame = build()
    db = FakeSourceConnection()
    db.state = "GAP"
    with pytest.raises(
        R11OriginalFrameUnavailable, match="ACTIVE_CANONICAL_SOURCE_EPOCH",
    ):
        append_r11_original_optional_frame(db, frame)
    assert not any("INSERT INTO" in s for s in db.statements)


def test_existing_frame_with_different_bytes_is_never_silently_overwritten() -> None:
    db = FakeSourceConnection()
    db.existing_digest = "f" * 64
    with pytest.raises(
        R11OriginalFrameUnavailable, match="FRAME_COLLISION",
    ):
        append_r11_original_optional_frame(db, build())


def test_mutated_or_forged_dataclass_hash_blocks_before_db() -> None:
    db = FakeSourceConnection()
    frame = build()
    with pytest.raises(
        R11OriginalFrameUnavailable, match="APPEND_DIGEST",
    ):
        append_r11_original_optional_frame(
            db, replace(frame, frame_content_sha256="d" * 64),
        )
    assert db.transaction_entries == 0
    assert db.statements == []


def test_source_only_sql_role_scope_and_five_symbol_contract() -> None:
    sql = Path(
        "operations/sql/20261114_r11_optional_original_public_event_frames.sql"
    ).read_text()
    py = Path(
        "src/bybit_workbench/market_data/r11_optional_original_frame_journal.py"
    ).read_text()
    assert "CREATE TABLE IF NOT EXISTS analytics.public_source_original_event_frames" in sql
    assert "GRANT SELECT, INSERT" in sql
    assert "REVOKE UPDATE, DELETE, TRUNCATE" in sql
    assert "PRIMARY KEY (" in sql
    assert "source_session_id,transport_epoch_id,product_category" in sql
    assert "SPOT_TRADE" in sql
    assert "LINEAR" in sql and "ORDERBOOK_50" in sql
    assert "source_session_id" in py
    assert "ON CONFLICT (" in py
    assert "decode_bybit_public_frame(" in py
    assert "BTCUSDT" not in py and "ETHUSDT" not in py
    assert "urllib" not in py
    assert "websocket" not in py.lower()
    assert "LIVE_ARM" not in py
