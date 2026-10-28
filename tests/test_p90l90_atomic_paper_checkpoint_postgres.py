"""Disposable PostgreSQL proof of *atomic* native queue + PAPER Entry checkpoint.

No production DB, exchange orders, StrategyActivation or file writes.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    MarketFactEnvelope,
)
from bybit_workbench.universal_entry.p90l90_atomic_paper_checkpoint import (
    P90L90AtomicPaperCheckpointStore,
)
from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalEntry
from bybit_workbench.universal_entry.p90l90_causal_fact_codec import (
    encode_p90l90_fact,
    restore_p90l90_fact,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PG16 DSN only")
B = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
SHA = "2" * 40


def fact(kind: str):
    if kind == "PUBLIC_TRADE":
        at = B + timedelta(seconds=1)
        trade = MarketFactEnvelope(
            fact_id="ltc-exec-21", event_kind="PUBLIC_TRADE",
            symbol="LTCUSDT", event_at=at, observed_at=at,
            received_at=at+timedelta(seconds=1),
            source_refs=("bybit:publicTrade:LTCUSDT:exec-21",),
            attributes=FrozenPolicy.from_mapping({
                "price": "100.25", "size": "3", "taker_side": "Buy"
            }),
        )
        return encode_p90l90_fact(
            trade, cursor=TradeCursor("LTCUSDT", "exec-21", 21, at),
        )
    closed = MarketFactEnvelope(
        fact_id=f"ltc-{kind}-at-5am",
        event_kind="CANDLE_CLOSED", symbol="LTCUSDT",
        event_at=B, observed_at=B, received_at=B+timedelta(seconds=2),
        source_refs=(f"bybit:kline.{kind}:LTCUSDT:5am",),
        attributes=FrozenPolicy.from_mapping({
            "timeframe": kind,
            "opened_at": (B-timedelta(minutes=int(kind))).isoformat(),
            "closed_at": B.isoformat(),
            "open": "100", "high": "101", "low": "99",
            "close": "100.5", "volume": "12",
        }),
    )
    return encode_p90l90_fact(closed)


def test_pg_atomic_entry_and_native_transport_rollback_same_generation():
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_DISPOSABLE_ATOMIC_P90"),
        conn.transaction(),
    ):
        columns = conn.execute(
            """SELECT has_column_privilege(
                 'cripta','strategy_entry.p90l90_causal_transport_checkpoints',
                 'entry_checkpoint_json','UPDATE'),
                 has_table_privilege(
                 'cripta','strategy_entry.p90l90_causal_transport_checkpoints',
                 'DELETE')"""
        ).fetchone()
        assert columns == (True, False)
        conn.execute(
            "CREATE TEMP TABLE p90_atomic_effects(event_id text NOT NULL) ON COMMIT DROP"
        )
        store = P90L90AtomicPaperCheckpointStore(conn, source_commit=SHA)
        store.seed(
            symbol="LTCUSDT",
            last_closed_5m=B-timedelta(minutes=5),
            last_closed_15m=B-timedelta(minutes=15),
            entry=P90L90CausalEntry(), proven_geometry=None,
        )

        def apply(entry, released):
            for event in released:
                decoded, _ = restore_p90l90_fact(event)
                if event.kind == "PUBLIC_TRADE":
                    entry.on_trade(
                        price=Decimal(str(decoded.attributes.to_dict()["price"])),
                        event_at=event.event_at,
                    )
                conn.execute(
                    "INSERT INTO pg_temp.p90_atomic_effects VALUES (%s)",
                    (event.event_id,),
                )

        def geometry(_symbol, _saved):
            return None
        trade, close15, close5 = fact("PUBLIC_TRADE"), fact("15"), fact("5")
        assert store.deliver(
            trade, recompute_geometry=geometry, apply_paper=apply
        ) == ()
        assert store.deliver(
            close15, recompute_geometry=geometry, apply_paper=apply
        ) == ()
        before = conn.execute(
            """SELECT checkpoint_json,entry_checkpoint_json,generation
                 FROM strategy_entry.p90l90_causal_transport_checkpoints
                WHERE symbol='LTCUSDT'"""
        ).fetchone()
        assert before[2] == 2
        assert len(before[0]["pending"]) == 2
        join, saved_entry = store.inspect(
            symbol="LTCUSDT", recomputed_geometry=None,
        )
        assert len(join.pending) == 2
        assert saved_entry.previous_price is None

        def fail(entry, released):
            apply(entry, released)
            raise RuntimeError("INJECTED_ATOMIC_PAPER_FAILURE")

        with pytest.raises(RuntimeError, match="INJECTED_ATOMIC_PAPER_FAILURE"):
            store.deliver(
                close5, recompute_geometry=geometry, apply_paper=fail,
            )
        assert conn.execute(
            """SELECT checkpoint_json,entry_checkpoint_json,generation
                 FROM strategy_entry.p90l90_causal_transport_checkpoints
                WHERE symbol='LTCUSDT'"""
        ).fetchone() == before
        assert conn.execute(
            "SELECT COUNT(*) FROM pg_temp.p90_atomic_effects"
        ).fetchone() == (0,)

        restarted = P90L90AtomicPaperCheckpointStore(conn, source_commit=SHA)
        released = restarted.deliver(
            close5, recompute_geometry=geometry, apply_paper=apply,
        )
        assert released == (close5, close15, trade)
        assert conn.execute(
            "SELECT event_id FROM pg_temp.p90_atomic_effects ORDER BY ctid"
        ).fetchall() == [
            ("ltc-5-at-5am",), ("ltc-15-at-5am",), ("ltc-exec-21",),
        ]
        after_join, after_entry = restarted.inspect(
            symbol="LTCUSDT", recomputed_geometry=None,
        )
        assert after_join.pending == []
        assert after_entry.previous_price == Decimal("100.25")
        assert after_entry.previous_at == B+timedelta(seconds=1)
        assert restarted.deliver(
            trade, recompute_geometry=geometry, apply_paper=apply,
        ) == ()
        assert conn.execute(
            "SELECT COUNT(*) FROM pg_temp.p90_atomic_effects"
        ).fetchone() == (3,)
        with pytest.raises(RuntimeError, match="reset pending or previously"):
            restarted.seed(
                symbol="LTCUSDT",
                last_closed_5m=B-timedelta(minutes=5),
                last_closed_15m=B-timedelta(minutes=15),
                entry=P90L90CausalEntry(), proven_geometry=None,
            )
        raise RuntimeError("ROLLBACK_DISPOSABLE_ATOMIC_P90")


def test_unpersisted_entry_or_legacy_join_is_a_hard_failure():
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_NO_LEGACY_REARM"),
        conn.transaction(),
    ):
        # An existing native-transport-only checkpoint is *not* an
        # endorsement to create a new Entry cursor after restart.
        from bybit_workbench.universal_entry.p90l90_causal_transport_store import (
            P90L90CausalJoinStore,
        )
        P90L90CausalJoinStore(conn, source_commit=SHA).initialize(
            symbol="DOTUSDT",
            last_closed_5m=B-timedelta(minutes=5),
            last_closed_15m=B-timedelta(minutes=15),
        )
        atomic = P90L90AtomicPaperCheckpointStore(conn, source_commit=SHA)
        with pytest.raises(RuntimeError, match="reset pending or previously"):
            atomic.seed(
                symbol="DOTUSDT", last_closed_5m=B-timedelta(minutes=5),
                last_closed_15m=B-timedelta(minutes=15),
                entry=P90L90CausalEntry(), proven_geometry=None,
            )
        with pytest.raises(RuntimeError, match="missing matching durable state"):
            atomic.inspect(symbol="DOTUSDT", recomputed_geometry=None)
        raise RuntimeError("ROLLBACK_NO_LEGACY_REARM")
