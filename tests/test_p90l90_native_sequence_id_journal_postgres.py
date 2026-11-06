"""Disposable PG16 exact native sequence dedup journal and rollback proof.

Only run with explicit CRIPTA_TRADE_LIFECYCLE_TEST_DSN. The tests create
invented publicTrade envelopes, never touch production positions/orders,
Exchange, original market bars, Strategy parameters or LIVE-arm.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from bybit_workbench.universal_entry.p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
    P90TransportEvent,
)
from bybit_workbench.universal_entry.p90l90_native_sequence_id_journal import (
    P90NativeSequenceIdJournal,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PG16 DSN only")
B = datetime(2026, 1, 1, tzinfo=UTC)
SYMBOL = "LTCUSDT"  # Existing whitelist; IDs, times and prices are synthetic.
SOURCE = "a" * 40
EPOCH = "p90-real-epoch-" + "b" * 40


def _trade(i: int, *, seq: int = 17) -> P90TransportEvent:
    return P90TransportEvent(
        event_id=f"synthetic-{seq}-{i}", symbol=SYMBOL,
        kind="PUBLIC_TRADE", event_at=B + timedelta(milliseconds=i),
        source_json='{"synthetic":true}', exchange_sequence=seq,
        exec_id=f"synthetic-exec-{seq}-{i}",
    )


def test_native_seq_journal_schema_is_additive_and_least_privilege() -> None:
    assert DSN is not None
    with psycopg.connect(DSN) as conn:
        present = conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL",
            ("strategy_entry.p90l90_native_sequence_exec_ids",),
        ).fetchone()
        assert present == (True,)
        privileges = conn.execute(
            """SELECT
                 has_table_privilege(
                   'cripta','strategy_entry.p90l90_native_sequence_exec_ids',
                   'SELECT,INSERT,DELETE'),
                 has_table_privilege(
                   'cripta','strategy_entry.p90l90_native_sequence_exec_ids',
                   'UPDATE'),
                 has_table_privilege(
                   'cripta','strategy_entry.p90l90_native_sequence_exec_ids',
                   'TRUNCATE'),
                 has_table_privilege(
                   'cripta','strategy_entry.p90l90_real_causal_checkpoints',
                   'DELETE')"""
        ).fetchone()
        assert privileges == (True, False, False, False)
        for name in ("p90l90_real_causal_checkpoints",
                     "p90l90_causal_transport_checkpoints"):
            check = conn.execute(
                """SELECT pg_get_constraintdef(c.oid)
                     FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
                     JOIN pg_namespace n ON n.oid=t.relnamespace
                    WHERE n.nspname=%s AND t.relname=%s
                      AND c.contype=%s AND
                      pg_get_constraintdef(c.oid) LIKE %s""",
                ("strategy_entry", name, "c",
                 "%P90L90_CAUSAL_TRANSPORT_JOIN_V2%"),
            ).fetchall()
            assert len(check) == 1


@pytest.mark.parametrize("mode,epoch", [
    ("REAL", EPOCH), ("PAPER", "PAPER"),
])
def test_native_seq_journal_1300_ids_restart_dedup_retention_and_rollback(
    mode: str, epoch: str,
) -> None:
    assert DSN is not None
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_NATIVE_SEQ_TEST"),
        conn.transaction(),
    ):
        journal = P90NativeSequenceIdJournal(
            conn, mode=mode, source_epoch_id=epoch,
            source_commit=SOURCE, symbol=SYMBOL,
        )
        join = P90L90CausalTransportJoin(
            symbol=SYMBOL, closed_5m_at=B, closed_15m_at=B,
        )
        journal.bind(join)
        all_facts = [_trade(i) for i in range(1300)]
        for fact in all_facts:
            assert join.accept(fact) == (fact,)
        journal.verify(join)
        assert join.native_id_journal_count == 1300
        assert len(join.last_emitted_exec_ids) == 512
        assert journal._group_counts() == [(17, 1300)]
        snapshot = join.snapshot()
        assert snapshot["schema"] == "P90L90_CAUSAL_TRANSPORT_JOIN_V2"

        restarted = P90L90CausalTransportJoin.restore(snapshot)
        with pytest.raises(RuntimeError, match="JOURNAL_UNBOUND"):
            restarted.accept(_trade(1299))
        journal.bind(restarted)
        for duplicate in (all_facts[0], all_facts[511], all_facts[1299]):
            assert restarted.accept(duplicate) == ()
        with pytest.raises(RuntimeError, match="EXCEED_REST_WINDOW"):
            journal.full_restart_ids(restarted)

        next_trade = _trade(1300, seq=18)
        assert restarted.accept(next_trade) == (next_trade,)
        journal.verify(restarted)
        assert journal._group_counts() == [(18, 1)]
        assert journal.full_restart_ids(restarted) == (
            next_trade.exec_id,
        )
        with pytest.raises(RuntimeError, match="regressed"):
            restarted.accept(_trade(1301, seq=17))

        # Inject an invalid out-of-band deletion in disposable DB.
        conn.execute(
            """DELETE FROM strategy_entry.p90l90_native_sequence_exec_ids
                WHERE mode=%s AND source_epoch_id=%s
                  AND source_commit=%s AND symbol=%s
                  AND exchange_sequence=%s""",
            (mode, epoch, SOURCE, SYMBOL, 18),
        )
        with pytest.raises(RuntimeError, match="COUNT_MISMATCH"):
            journal.verify(restarted)
        raise RuntimeError("ROLLBACK_NATIVE_SEQ_TEST")


def test_legacy_v1_native_ids_upgrade_exactly_once_under_checkpoint_lock() -> None:
    assert DSN is not None
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_V1_BRIDGE_TEST"),
        conn.transaction(),
    ):
        join = P90L90CausalTransportJoin(
            symbol=SYMBOL, closed_5m_at=B, closed_15m_at=B,
        )
        first = _trade(0)
        assert join.accept(first) == (first,)
        assert join.native_id_journal_count is None
        journal = P90NativeSequenceIdJournal(
            conn, mode="REAL", source_epoch_id=EPOCH,
            source_commit=SOURCE, symbol=SYMBOL,
        )
        journal.bind(join)
        journal.verify(join)
        assert journal._group_counts() == [(17, 1)]
        assert journal.full_restart_ids(join) == (first.exec_id,)
        assert join.accept(first) == ()
        assert join.accept(_trade(1)) == (_trade(1),)
        journal.verify(join)
        assert journal._group_counts() == [(17, 2)]
        raise RuntimeError("ROLLBACK_V1_BRIDGE_TEST")
