"""Disposable PostgreSQL16 P90 native/kline causal checkpoint proof.

The test always rolls back. It never opens an order, changes a real strategy,
calls Bybit, or writes outside the disposable CI database.
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
from bybit_workbench.universal_entry.p90l90_causal_transport_store import (
    P90L90CausalJoinStore,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PG16 database only")
B0 = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
SOURCE = "0123456789abcdef" * 2 + "01234567"


def event(kind: str, *, minute: int = 0, seq: int = 10) -> P90TransportEvent:
    when = B0 + timedelta(minutes=minute)
    if kind == "PUBLIC_TRADE":
        return P90TransportEvent(
            event_id=f"p90-native-{seq}", kind="PUBLIC_TRADE",
            symbol="LTCUSDT", event_at=when+timedelta(seconds=1),
            source_json='{"price":"100","execId":"native"}',
            exchange_sequence=seq, exec_id=f"native-{seq}",
        )
    return P90TransportEvent(
        event_id=f"p90-close-{kind}-{minute}", kind="CANDLE_CLOSED",
        symbol="LTCUSDT", timeframe=kind,
        event_at=when, source_json='{"close":"100"}',
    )


def test_pg16_p90_transport_checkpoint_survives_restart_with_exact_rollback():
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_P90_TRANSPORT_DISPOSABLE"),
        conn.transaction(),
    ):
        grant = conn.execute(
            """SELECT has_table_privilege(
                      'cripta','strategy_entry.p90l90_causal_transport_checkpoints',
                      'SELECT,INSERT,UPDATE'),
                     has_table_privilege(
                      'cripta','strategy_entry.p90l90_causal_transport_checkpoints',
                      'DELETE')"""
        ).fetchone()
        assert grant == (True, False)
        store = P90L90CausalJoinStore(conn, source_commit=SOURCE)
        seed = store.initialize(
            symbol="LTCUSDT",
            last_closed_5m=B0-timedelta(minutes=5),
            last_closed_15m=B0-timedelta(minutes=15),
        )
        assert isinstance(seed, P90L90CausalTransportJoin)
        observed = []
        assert store.deliver(
            event("PUBLIC_TRADE"), dispatch=lambda batch: observed.extend(batch)
        ) == ()
        assert observed == []
        snap = conn.execute(
            """SELECT generation,checkpoint_json
                 FROM strategy_entry.p90l90_causal_transport_checkpoints
                WHERE symbol='LTCUSDT'"""
        ).fetchone()
        assert snap[0] == 1 and len(snap[1]["pending"]) == 1

        # A failing observer/position handler must not ACK the candle
        # or make buffered native trades disappear across a restart.
        with pytest.raises(RuntimeError, match="INJECTED_STOP"):
            store.deliver(
                event("5"), dispatch=lambda _: (_ for _ in ()).throw(
                    RuntimeError("INJECTED_STOP")
                ),
            )
        after_failure = conn.execute(
            """SELECT generation,checkpoint_json
                 FROM strategy_entry.p90l90_causal_transport_checkpoints
                WHERE symbol='LTCUSDT'"""
        ).fetchone()
        assert after_failure == snap

        assert store.deliver(
            event("5"), dispatch=lambda batch: observed.extend(batch)
        ) == (event("5"),)
        assert store.deliver(
            event("15"), dispatch=lambda batch: observed.extend(batch)
        ) == (event("15"), event("PUBLIC_TRADE"))
        assert [x.event_id for x in observed] == [
            event("5").event_id, event("15").event_id, event("PUBLIC_TRADE").event_id
        ]
        # The exact native source cursor is checkpointed in DB and an ACK
        # replay returns an empty batch; no duplicate Entry/Exit update.
        restarted = P90L90CausalJoinStore(conn, source_commit=SOURCE)
        assert restarted.deliver(
            event("PUBLIC_TRADE"), dispatch=lambda batch: observed.extend(batch)
        ) == ()
        assert len(observed) == 3
        row = conn.execute(
            """SELECT checkpoint_json,generation
                 FROM strategy_entry.p90l90_causal_transport_checkpoints
                WHERE symbol='LTCUSDT'"""
        ).fetchone()
        assert row[1] == 4 and row[0]["pending"] == []
        with pytest.raises(RuntimeError, match="source release mismatch"):
            P90L90CausalJoinStore(
                conn, source_commit="f" * 40
            ).initialize(
                symbol="LTCUSDT", last_closed_5m=B0,
                last_closed_15m=B0,
            )
        # Never leave one of these intentionally invented markets in CI DB.
        raise RuntimeError("ROLLBACK_P90_TRANSPORT_DISPOSABLE")
