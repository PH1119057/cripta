"""R06: one immutable PG Entry publication pin; no decision/Exchange switch."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bybit_workbench.market_data import entry_publication_reader as reader
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

SYMS = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")
RELEASE = "a" * 40
EPOCH = "b" * 32
AT = datetime(2026, 11, 11, 18, 25, 38, tzinfo=UTC)
OBSERVED = AT + timedelta(seconds=2)
CLOSES = {
    "1": "2026-11-11T18:25:00+00:00",
    "5": "2026-11-11T18:25:00+00:00",
    "15": "2026-11-11T18:15:00+00:00",
}
TTL = {"1": 75, "5": 315, "15": 915}


class Cursor:
    def __init__(self, value: object):
        self.value = value

    def fetchone(self) -> object:
        return self.value

    def fetchall(self) -> object:
        return self.value


class FakePG:
    def __init__(self) -> None:
        self.isolation = "repeatable read"
        self.readonly = "on"
        self.sessions = [(EPOCH, RELEASE)]
        self.original_matches = 1
        self.closed_matches = 1
        self.calls: list[str] = []
        native: dict[str, dict[str, object]] = {}
        candles: dict[str, dict[str, str]] = {}
        refs: dict[str, dict[str, dict[str, str]]] = {}
        quality: dict[str, dict[str, dict[str, object]]] = {}
        for symbol in SYMS:
            instrument = bybit_instrument_id("linear", symbol)
            native[symbol] = {
                "instrument_id": instrument,
                "frame_number": 123,
                "frame_index": 1,
                "exchange_sequence": 456,
                "exec_id": "provider-row-" + symbol,
                "accepted_at": AT.isoformat(),
            }
            candles[symbol] = dict(CLOSES)
            refs[instrument] = {
                tf: {
                    "closed_at": stamp,
                    "timeframe": tf,
                    "market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
                    "instrument_id": instrument,
                    "external_symbol": symbol,
                    "product_category": "LINEAR",
                }
                for tf, stamp in CLOSES.items()
            }
            quality[instrument] = {
                tf: {
                    "status": "LAST_CLOSED_OBSERVED_WINDOW_UNVERIFIED",
                    "required": True,
                    "history_window": "NOT_VERIFIED",
                    "freshness_seconds": TTL[tf],
                }
                for tf in CLOSES
            }
        self.publication: tuple[object, ...] = (
            1234, EPOCH, RELEASE, AT, "WATERMARKS_ONLY", native,
            candles, refs, quality,
        )

    def execute(self, sql: str, params: object = ()) -> Cursor:
        self.calls.append(sql)
        assert sql.lstrip().upper().startswith(("SELECT", "SHOW"))
        if sql == "SHOW transaction_isolation":
            return Cursor((self.isolation,))
        if sql == "SHOW transaction_read_only":
            return Cursor((self.readonly,))
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            assert params == (BYBIT_PUBLIC_MARKET_SOURCE_ID,)
            return Cursor(self.sessions)
        if "FROM analytics.market_publications" in sql:
            return Cursor(self.publication)
        if "FROM analytics.native_public_trade_source_events" in sql:
            return Cursor((self.original_matches,))
        if "FROM analytics.closed_candles_effective" in sql:
            return Cursor((self.closed_matches,))
        pytest.fail("unexpected SQL: " + sql)


def pin(db: FakePG, *, observed_at: datetime = OBSERVED):
    return reader.read_entry_publication_pin(
        db, observed_at=observed_at,
        accepted_producer_commits=frozenset((RELEASE,)),
        max_publication_age=timedelta(seconds=5),
    )


def test_pins_one_source_edition_and_verifies_all_physical_references() -> None:
    db = FakePG()
    x = pin(db)
    assert x.publication_id == 1234
    assert x.source_session_id == EPOCH
    assert x.producer_release_commit == RELEASE
    assert x.as_of_second == AT
    assert set(x.native_watermarks) == set(SYMS)
    assert len(x.closed_refs) == 5
    assert all(set(v) == {"1", "5", "15"} for v in x.closed_refs.values())
    assert x.history_verified is False
    assert sum("FROM analytics.native_public_trade_source_events" in q
               for q in db.calls) == 5
    assert sum("FROM analytics.closed_candles_effective" in q
               for q in db.calls) == 15
    assert all(q.lstrip().upper().startswith(("SELECT", "SHOW"))
               for q in db.calls)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("isolation", "read committed", "ATOMIC_READ_ONLY"),
        ("readonly", "off", "ATOMIC_READ_ONLY"),
        ("sessions", [], "ACTIVE_SOURCE"),
        ("sessions", [(EPOCH, RELEASE), ("c" * 32, RELEASE)], "ACTIVE_SOURCE"),
        ("original_matches", 0, "NATIVE_ORIGINAL"),
        ("closed_matches", 0, "CLOSED_PHYSICAL"),
    ],
)
def test_missing_transaction_or_original_proof_is_fail_closed(
    field: str, value: object, error: str,
) -> None:
    db = FakePG()
    setattr(db, field, value)
    with pytest.raises(reader.EntryPublicationUnavailable, match=error):
        pin(db)


def test_stale_and_future_publications_never_become_ready() -> None:
    db = FakePG()
    with pytest.raises(
        reader.EntryPublicationUnavailable, match="PUBLICATION_IDENTITY_OR_AGE",
    ):
        pin(db, observed_at=AT + timedelta(seconds=20))
    with pytest.raises(
        reader.EntryPublicationUnavailable, match="PUBLICATION_IDENTITY_OR_AGE",
    ):
        pin(db, observed_at=AT - timedelta(seconds=1))


def test_unlisted_producer_and_cross_epoch_publications_fail_closed() -> None:
    db = FakePG()
    with pytest.raises(
        reader.EntryPublicationUnavailable, match="ACTIVE_SOURCE_PROOF",
    ):
        reader.read_entry_publication_pin(
            db, observed_at=OBSERVED,
            accepted_producer_commits=frozenset(("c" * 40,)),
            max_publication_age=timedelta(seconds=5),
        )
    db.publication = (1234, "c" * 32, *db.publication[2:])
    with pytest.raises(
        reader.EntryPublicationUnavailable, match="PUBLICATION_IDENTITY",
    ):
        pin(db)


def test_missing_confirmed_interval_is_not_replaced_by_live_kline() -> None:
    db = FakePG()
    row = list(db.publication)
    refs = row[7]
    assert isinstance(refs, dict)
    refs[bybit_instrument_id("linear", "LTCUSDT")].pop("15")
    with pytest.raises(
        reader.EntryPublicationUnavailable, match="CLOSED_REF_SCOPE",
    ):
        pin(db)
    assert not any("INSERT" in sql for sql in db.calls)


def test_full_lookback_not_inferred_from_watermarks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    x = pin(FakePG())
    conn = object()

    def full(_conn: object, **kwargs: object) -> dict[str, tuple[object, ...]]:
        assert _conn is conn
        assert kwargs["symbol"] == "APTUSDT"
        assert kwargs["observed_at"] == AT
        assert kwargs["required_counts"] == {"5": 3, "15": 2}
        return {
            "5": tuple(SimpleNamespace(
                closed_at=datetime.fromisoformat(CLOSES["5"]),
            ) for _ in range(3)),
            "15": tuple(SimpleNamespace(
                closed_at=datetime.fromisoformat(CLOSES["15"]),
            ) for _ in range(2)),
        }

    monkeypatch.setattr(reader, "load_verified_r03_native_closed_seed", full)
    result = reader.read_entry_publication_seed(
        conn, pin=x, symbol="APTUSDT",
        required_counts={"5": 3, "15": 2},
    )
    assert len(result["5"]) == 3
    assert x.history_verified is False


def test_history_gap_and_cross_publication_boundary_block_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    x = pin(FakePG())

    def fail(_conn: object, **_kw: object) -> object:
        raise RuntimeError("CANDLE_HISTORY_GAP_OR_DUPLICATE")

    monkeypatch.setattr(reader, "load_verified_r03_native_closed_seed", fail)
    with pytest.raises(
        reader.EntryPublicationUnavailable, match="ENTRY_HISTORY_UNVERIFIED",
    ):
        reader.read_entry_publication_seed(
            object(), pin=x, symbol="LTCUSDT", required_counts={"5": 2600},
        )
    monkeypatch.setattr(
        reader, "load_verified_r03_native_closed_seed",
        lambda *_a, **_kw: {
            "5": (SimpleNamespace(
                closed_at=datetime(2026, 11, 11, 18, 20, tzinfo=UTC),
            ),),
        },
    )
    with pytest.raises(
        reader.EntryPublicationUnavailable, match="SEED_PUBLICATION_MISMATCH",
    ):
        reader.read_entry_publication_seed(
            object(), pin=x, symbol="LTCUSDT", required_counts={"5": 1},
        )


def test_no_atr_or_decision_permission_is_granted_by_pin() -> None:
    x = pin(FakePG())
    assert x.history_verified is False
    assert not hasattr(x, "entry_ready")
    assert not hasattr(x, "can_execute")
