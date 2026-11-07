"""#470: original native facts are durable before consumer checkpoint, not guessed.

Postgres tests use only CRIPTA_TRADE_LIFECYCLE_TEST_DSN (disposable PG16).
The synthetic source rows are always rolled back, never sent to Exchange.
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bybit_workbench.market_data.native_public_trade_journal import (
    NativePublicTradeJournal,
    NativeTradeEvidence,
    NativeTradeEvidenceConflict,
    NativeTradeEvidenceUnavailable,
    read_native_candidates,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")


def _event(exec_id: str = "synthetic-470-a") -> NativeTradeEvidence:
    return NativeTradeEvidence(
        market_source_id="ms-bybit-mainnet-public-v1",
        instrument_id="instrument-synthetic-470",
        external_symbol="LTCUSDT",
        exchange_sequence=470,
        exec_id=exec_id,
        event_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        price=Decimal("100.25"),
        quantity=Decimal("1.2"),
        taker_side="Buy",
        source_transport="BYBIT_PUBLIC_WS",
        producer_release_commit="a" * 40,
    )


def test_native_identity_and_source_semantics_are_strict() -> None:
    with pytest.raises(ValueError, match="SEQUENCE"):
        replace(_event(), exchange_sequence=-1)
    with pytest.raises(ValueError, match="TIME"):
        replace(_event(), event_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="AMOUNT"):
        replace(_event(), price=Decimal("NaN"))
    with pytest.raises(ValueError, match="SIDE"):
        replace(_event(), taker_side="unknown")
    with pytest.raises(ValueError, match="PRODUCER"):
        replace(_event(), producer_release_commit="not-a-release")


def test_native_journal_rejects_implicit_autocommit_without_source_transaction() -> None:
    connection = SimpleNamespace(
        autocommit=True,
        info=SimpleNamespace(transaction_status=SimpleNamespace(name="IDLE")),
    )
    with pytest.raises(RuntimeError, match="ATOMIC_SOURCE_TX_REQUIRED"):
        NativePublicTradeJournal(connection).append(_event())


@pytest.mark.skipif(not DSN, reason="requires disposable PostgreSQL test DSN")
def test_same_seq_native_ids_atomic_rollback_exact_replay_and_conflict() -> None:
    import psycopg

    assert DSN is not None
    with psycopg.connect(DSN, autocommit=True) as conn:
        # Owner must commit journal + dependent effects/cursor as ONE transaction.
        with pytest.raises(RuntimeError, match="SIMULATED_SOURCE_CRASH"), conn.transaction():
            conn.execute(
                "CREATE TEMP TABLE native_470_effects(exec_id text) ON COMMIT DROP"
            )
            store = NativePublicTradeJournal(conn)
            assert store.append(_event("synthetic-470-a"))
            assert store.append(_event("synthetic-470-b"))
            # Multiple exact IDs at the same sequence are independent.
            assert not store.append(replace(
                _event("synthetic-470-a"),
                source_transport="APPROVED_ARCHIVE_REPLAY",
                producer_release_commit="b" * 40,
            ))
            snapshot = read_native_candidates(
                conn,
                market_source_id="ms-bybit-mainnet-public-v1",
                instrument_id="instrument-synthetic-470",
                first_sequence=470,
                first_exec_ids=frozenset({"synthetic-470-a", "synthetic-470-b"}),
                last_sequence=470,
                last_exec_id="synthetic-470-b",
            )
            assert len(snapshot.events) == 2
            assert snapshot.coverage_proven is False
            conn.execute(
                "INSERT INTO pg_temp.native_470_effects VALUES('dependent-effect')"
            )
            with pytest.raises(
                NativeTradeEvidenceConflict, match="IDENTITY_CONFLICT"
            ):
                store.append(replace(_event("synthetic-470-a"), price=Decimal("99")))
            raise RuntimeError("SIMULATED_SOURCE_CRASH")

        # The outer crash rolled back both exact native rows and effects.
        assert conn.execute(
            """SELECT count(*) FROM analytics.native_public_trade_events
               WHERE market_source_id=%s AND instrument_id=%s
                 AND exchange_sequence=470
                 AND exec_id IN ('synthetic-470-a','synthetic-470-b')""",
            ("ms-bybit-mainnet-public-v1", "instrument-synthetic-470"),
        ).fetchone() == (0,)


def test_observer_normalized_native_evidence_preserves_exact_market_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
    from bybit_workbench.universal_entry.transport_continuity import TradeCursor
    from operations.monitoring import universal_entry_shadow as observer

    now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(observer, "LOADED_COMMIT", "a" * 40)
    fact = MarketFactEnvelope(
        fact_id="trade-synthetic-470-a",
        event_kind="PUBLIC_TRADE",
        symbol="LTCUSDT",
        event_at=now,
        observed_at=now,
        received_at=now,
        source_refs=("bybit:publicTrade:LTCUSDT:synthetic-470-a",),
        attributes=FrozenPolicy.from_mapping({
            "price": "100.25", "size": "1.2", "taker_side": "Buy",
        }),
    )
    cursor = TradeCursor("LTCUSDT", "synthetic-470-a", 470, now)
    source = observer._exact_native_evidence(
        fact, cursor, source_transport="BYBIT_WS_PUBLIC_TRADE",
    )
    assert source.market_source_id == "ms-bybit-mainnet-public-v1"
    assert source.instrument_id == "inst-bybit-linear-ltcusdt"
    assert source.exchange_sequence == 470
    assert source.exec_id == "synthetic-470-a"
    assert source.price == Decimal("100.25")
    assert source.quantity == Decimal("1.2")
    assert source.producer_release_commit == "a" * 40
    with pytest.raises(observer.ContinuityNotProvable, match="PROVENANCE_MISMATCH"):
        observer._exact_native_evidence(
            replace(fact, source_refs=("untrusted:synthetic",)), cursor,
            source_transport="BYBIT_WS_PUBLIC_TRADE",
        )


def test_observer_routes_native_write_before_causal_consumer_in_one_transaction() -> None:
    """Regression: a standalone side writer would break source/Exit atomicity."""
    import ast
    from pathlib import Path

    source = Path("operations/monitoring/universal_entry_shadow.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(source)
    epoch = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_observer_epoch"
    )
    nested = next(
        node for node in ast.walk(epoch)
        if isinstance(node, ast.FunctionDef) and node.name == "process_fact"
    )
    body = ast.get_source_segment(source, nested)
    assert body is not None
    assert "with connection.transaction():" in body
    assert "NativePublicTradeJournal(connection).append(" in body
    assert "_process_fact_body(fact, cursor)" in body
    assert body.index("NativePublicTradeJournal(connection).append(") < (
        body.index("_process_fact_body(fact, cursor)")
    )
    assert "dispatch_observer_fact_atomically(" in body
    assert 'native_transport="BYBIT_WS_MIRROR_REPLAY"' in source


def test_pg_first_read_has_bounded_exact_ids_but_never_certifies_gap() -> None:
    from types import SimpleNamespace

    first = _event("synthetic-470-a")
    second = _event("synthetic-470-b")
    rows = [
        (
            e.market_source_id, e.instrument_id, e.external_symbol,
            e.exchange_sequence, e.exec_id, e.event_at, e.price, e.quantity,
            e.taker_side, e.source_transport, e.producer_release_commit,
        )
        for e in (first, second)
    ]

    class ReadOnlySource:
        def __init__(self, data):
            self.data = data
            self.queries = []

        def execute(self, query, params):
            self.queries.append((query, params))
            return SimpleNamespace(fetchall=lambda: self.data)

    source = ReadOnlySource(rows)
    params = dict(
        market_source_id=first.market_source_id,
        instrument_id=first.instrument_id,
        first_sequence=470,
        first_exec_ids=frozenset({first.exec_id, second.exec_id}),
        last_sequence=470,
        last_exec_id=second.exec_id,
    )
    result = read_native_candidates(source, **params)
    assert [e.exec_id for e in result.events] == [
        "synthetic-470-a", "synthetic-470-b",
    ]
    assert result.coverage_proven is False
    assert len(source.queries) == 1
    assert source.queries[0][0].lstrip().startswith("SELECT ")
    with pytest.raises(NativeTradeEvidenceUnavailable, match="FIRST_SAME_SEQUENCE"):
        read_native_candidates(source, **{
            **params, "first_exec_ids": frozenset({first.exec_id, "evicted"}),
        })
    with pytest.raises(NativeTradeEvidenceUnavailable, match="LAST_CURSOR"):
        read_native_candidates(source, **{
            **params, "last_exec_id": "unknown",
        })
    with pytest.raises(NativeTradeEvidenceUnavailable, match="PERSISTED_RANGE_EMPTY"):
        read_native_candidates(ReadOnlySource([]), **params)
    with pytest.raises(ValueError, match="BOUNDARY_INVALID"):
        read_native_candidates(source, **{**params, "limit": 1})
    with pytest.raises(NativeTradeEvidenceUnavailable, match="EXCEEDS_BOUNDED_READ"):
        read_native_candidates(ReadOnlySource(rows + [rows[0]]), **{
            **params, "limit": 2,
        })
