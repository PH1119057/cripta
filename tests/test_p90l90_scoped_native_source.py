"""Native MarketSource scoped proof: RED before production implementation.

The new producer is physically separate from the legacy P90 Observer.
Only exact committed PostgreSQL facts are sufficient for MARKET SOURCE health,
never for OWNER ARM, Strategy activation, Execution or Universal Exit readiness.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    NativeSourceProofUnavailable,
    read_ordered_native_events,
    read_scoped_native_source,
)

RELEASE = "a" * 40
SYMBOLS = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")


class _Cursor:
    def __init__(self, records):
        self.records = records

    def fetchall(self):
        return self.records


class SourceDB:
    def __init__(self, now, *, sessions=None, missing=(), stale=(), forged=()):
        self.now = now
        self.queries = []
        self.sessions = sessions if sessions is not None else [
            ("session-1", BYBIT_PUBLIC_MARKET_SOURCE_ID, RELEASE, "ACTIVE",
             now-timedelta(minutes=2), now-timedelta(seconds=1), None),
        ]
        self.missing = set(missing)
        self.stale = set(stale)
        self.forged = set(forged)

    def execute(self, sql, params):
        self.queries.append((sql, params))
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            return _Cursor(self.sessions)
        if "FROM analytics.native_public_trade_source_cursors" in sql:
            result = []
            for symbol in SYMBOLS:
                if symbol in self.missing or symbol not in params[1]:
                    continue
                at = self.now - timedelta(seconds=50 if symbol in self.stale else 2)
                source = BYBIT_PUBLIC_MARKET_SOURCE_ID
                result.append((
                    symbol, source, bybit_instrument_id("linear", symbol),
                    1001, "exec-"+symbol, at, at+timedelta(milliseconds=100),
                    at + timedelta(seconds=1) if symbol in self.forged else at,
                    Decimal("51.75"), Decimal("0.1"), "Buy",
                ))
            return _Cursor(result)
        raise AssertionError(sql)


def _read(db, *, symbols=SYMBOLS, producers=frozenset((RELEASE,)), limit=20):
    return read_scoped_native_source(
        db, symbols=symbols, accepted_producer_commits=producers,
        observed_at=db.now, max_lag=timedelta(seconds=limit),
    )


def test_five_current_committed_source_symbols_are_independent_of_observer() -> None:
    now = datetime.now(UTC)
    conn = SourceDB(now)
    proof = _read(conn)
    assert proof.ready
    assert proof.session_id == "session-1"
    assert set(proof.by_symbol) == set(SYMBOLS)
    assert all(x.ready for x in proof.by_symbol.values())
    assert all(x.reason == "NATIVE_SOURCE_COMMITTED_CURRENT" for x in proof.by_symbol.values())
    assert len(conn.queries) == 2
    assert not any("UPDATE" in sql or "INSERT" in sql or "DELETE" in sql
                   for sql, _ in conn.queries)
    assert all("observer" not in sql.lower() for sql, _ in conn.queries)


@pytest.mark.parametrize(("missing", "stale", "forged", "reason"), [
    (("LTCUSDT",), (), (), "NATIVE_SOURCE_CURSOR_MISSING"),
    ((), ("LTCUSDT",), (), "NATIVE_SOURCE_CURSOR_NOT_FRESH"),
    ((), (), ("LTCUSDT",), "NATIVE_SOURCE_JOURNAL_IDENTITY_DRIFT"),
])
def test_broken_ltc_source_evidence_does_not_poison_unrelated_symbols(
    missing, stale, forged, reason,
) -> None:
    conn = SourceDB(datetime.now(UTC), missing=missing, stale=stale,
                    forged=forged)
    proof = _read(conn)
    assert not proof.ready
    assert proof.by_symbol["LTCUSDT"].reason == reason
    assert all(proof.by_symbol[s].ready for s in SYMBOLS if s != "LTCUSDT")


def test_unapproved_producer_release_never_grants_source_proof() -> None:
    conn = SourceDB(datetime.now(UTC))
    proof = _read(conn, producers=frozenset(("b"*40,)))
    assert not proof.ready
    assert all(v.reason == "NATIVE_SOURCE_RELEASE_NOT_APPROVED"
               for v in proof.by_symbol.values())


def test_multiple_active_sessions_are_not_a_single_current_source() -> None:
    conn = SourceDB(datetime.now(UTC))
    conn.sessions.append(("session-2", BYBIT_PUBLIC_MARKET_SOURCE_ID, RELEASE,
                          "ACTIVE", conn.now-timedelta(seconds=20),
                          conn.now-timedelta(seconds=1), None))
    proof = _read(conn)
    assert not proof.ready
    assert all(v.reason == "NATIVE_SOURCE_ACTIVE_SESSION_AMBIGUOUS"
               for v in proof.by_symbol.values())


def test_exact_symbols_and_current_utc_policy_are_mandatory() -> None:
    conn = SourceDB(datetime.now(UTC))
    with pytest.raises(NativeSourceProofUnavailable):
        _read(conn, symbols=("UNKNOWNUSDT",))
    with pytest.raises(NativeSourceProofUnavailable):
        _read(conn, producers=frozenset())
    with pytest.raises(NativeSourceProofUnavailable):
        _read(conn, limit=0)
    conn.now = datetime.now().replace(tzinfo=None)
    with pytest.raises(NativeSourceProofUnavailable):
        _read(conn)


def test_ordered_source_reader_uses_provider_frame_ordinal_not_execid_sort() -> None:
    now = datetime.now(UTC)
    proof = _read(SourceDB(now), symbols=("LTCUSDT",))
    assert proof.ready

    class OrderedDB:
        def __init__(self):
            self.queries = []

        def execute(self, sql, params):
            self.queries.append((sql, params))
            assert "native_public_trade_source_events" in sql
            assert "ORDER BY e.frame_number,e.frame_index" in sql
            return _Cursor([
                (7, 0, BYBIT_PUBLIC_MARKET_SOURCE_ID,
                 bybit_instrument_id("linear", "LTCUSDT"),
                 "LTCUSDT", 1000, "z-provider-first", now, now,
                 Decimal("52.1"), Decimal("0.1"), "Buy"),
                (7, 1, BYBIT_PUBLIC_MARKET_SOURCE_ID,
                 bybit_instrument_id("linear", "LTCUSDT"),
                 "LTCUSDT", 1000, "a-provider-second", now, now,
                 Decimal("52.2"), Decimal("0.2"), "Sell"),
            ])

    conn = OrderedDB()
    rows = read_ordered_native_events(
        conn, source=proof, after_frame=6, after_index=0, limit=100,
    )
    assert [(r.frame_number, r.frame_index, r.exec_id) for r in rows] == [
        (7, 0, "z-provider-first"), (7, 1, "a-provider-second"),
    ]
    assert conn.queries[0][1] == (
        "session-1", ["LTCUSDT"], 6, 0, 100,
    )


def test_ordered_reader_requires_verified_source_and_existing_boundary() -> None:
    now = datetime.now(UTC)
    proof = _read(SourceDB(now), symbols=("LTCUSDT",))
    with pytest.raises(NativeSourceProofUnavailable):
        read_ordered_native_events(
            SourceDB(now), source=proof, after_frame=-1,
            after_index=0, limit=100,
        )
    denied = _read(SourceDB(now, missing=("LTCUSDT",)),
                   symbols=("LTCUSDT",))
    with pytest.raises(NativeSourceProofUnavailable):
        read_ordered_native_events(
            SourceDB(now), source=denied, after_frame=0,
            after_index=0, limit=100,
        )
