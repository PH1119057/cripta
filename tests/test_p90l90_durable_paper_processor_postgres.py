"""Disposable PG16 end-to-end P90 causal PAPER state and callback rollback.

No exchange access, real orders, production database or StrategyActivation.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.p90l90_atomic_paper_checkpoint import (
    P90L90AtomicPaperCheckpointStore,
)
from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalEntry
from bybit_workbench.universal_entry.p90l90_causal_fact_codec import (
    encode_p90l90_fact,
)
from bybit_workbench.universal_entry.p90l90_durable_paper_processor import (
    P90L90DurablePaperProcessor,
)
from bybit_workbench.universal_entry.p90l90_immutable_bar_ledger import (
    P90L90ImmutableBarLedger,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PG16 DSN only")
B = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
SHA = "6" * 40


def bar(tf: str, boundary: datetime):
    normalized = MarketFactEnvelope(
        fact_id=f"apt-bar:{tf}:{boundary.isoformat()}",
        symbol="APTUSDT", event_kind="CANDLE_CLOSED",
        event_at=boundary, observed_at=boundary,
        received_at=boundary+timedelta(seconds=3),
        source_refs=(f"bybit:kline.{tf}:APTUSDT:{boundary.isoformat()}",),
        attributes=FrozenPolicy.from_mapping({
            "timeframe":tf,
            "opened_at":(boundary-timedelta(minutes=int(tf))).isoformat(),
            "closed_at":boundary.isoformat(),
            "open":"12","high":"13","low":"11","close":"12","volume":"50",
        }),
    )
    return encode_p90l90_fact(normalized)


def native():
    at = B+timedelta(seconds=1)
    fact = MarketFactEnvelope(
        fact_id="apt-native-101", symbol="APTUSDT",
        event_kind="PUBLIC_TRADE",
        event_at=at, observed_at=at,
        received_at=at+timedelta(seconds=1),
        source_refs=("bybit:publicTrade:APTUSDT:101",),
        attributes=FrozenPolicy.from_mapping({
            "price":"12.5", "size":"1", "taker_side":"Buy",
        }),
    )
    return encode_p90l90_fact(
        fact, cursor=TradeCursor("APTUSDT", "exec-apt-101", 101, at),
    )


def test_bar_reconstruction_and_native_paper_db_effects_roll_back_together():
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_DURABLE_PAPER_TEST"),
        conn.transaction(),
    ):
        conn.execute(
            "CREATE TEMP TABLE p90_durable_effects(id text NOT NULL) ON COMMIT DROP"
        )
        archive = P90L90ImmutableBarLedger(conn, source_commit=SHA)
        archive.append(bar("5", B-timedelta(minutes=5)))
        archive.append(bar("15", B-timedelta(minutes=15)))
        atomic = P90L90AtomicPaperCheckpointStore(conn, source_commit=SHA)
        atomic.seed(
            symbol="APTUSDT", entry=P90L90CausalEntry(), proven_geometry=None,
            last_closed_5m=B-timedelta(minutes=5),
            last_closed_15m=B-timedelta(minutes=15),
        )

        def on_candle(fact):
            conn.execute(
                "INSERT INTO pg_temp.p90_durable_effects VALUES (%s)",
                (fact.fact_id,),
            )

        def on_native_exit(fact, cursor):
            assert cursor.seq == 101
            conn.execute(
                "INSERT INTO pg_temp.p90_durable_effects VALUES (%s)",
                ("exit-"+fact.fact_id,),
            )

        processor = P90L90DurablePaperProcessor(
            conn, source_commit=SHA,
            on_native_exit=on_native_exit,
            on_closed_candle=on_candle,
        )
        assert processor.accept(native()) == ()
        assert processor.accept(bar("15", B)) == ()
        unresolved, old_entry = processor.recover(symbol="APTUSDT")
        assert len(unresolved.pending) == 2 and old_entry.previous_price is None
        before = conn.execute(
            """SELECT checkpoint_json,entry_checkpoint_json,generation
                 FROM strategy_entry.p90l90_causal_transport_checkpoints
                WHERE symbol='APTUSDT'"""
        ).fetchone()
        assert before[2] == 2

        def failing_exit(fact, cursor):
            on_native_exit(fact, cursor)
            raise RuntimeError("INJECTED_PAPER_EXIT_FAILURE")

        poisoned = P90L90DurablePaperProcessor(
            conn, source_commit=SHA,
            on_native_exit=failing_exit,
            on_closed_candle=on_candle,
        )
        with pytest.raises(RuntimeError, match="INJECTED_PAPER_EXIT_FAILURE"):
            poisoned.accept(bar("5", B))
        with pytest.raises(RuntimeError, match="poisoned epoch"):
            poisoned.accept(bar("5", B))
        assert conn.execute(
            """SELECT checkpoint_json,entry_checkpoint_json,generation
                 FROM strategy_entry.p90l90_causal_transport_checkpoints
                WHERE symbol='APTUSDT'"""
        ).fetchone() == before
        assert conn.execute(
            "SELECT COUNT(*) FROM pg_temp.p90_durable_effects"
        ).fetchone() == (0,)
        assert archive.closed_at_or_before(
            symbol="APTUSDT", timeframe="5", through=B,
        ) == B-timedelta(minutes=5)

        restarted = P90L90DurablePaperProcessor(
            conn, source_commit=SHA,
            on_native_exit=on_native_exit,
            on_closed_candle=on_candle,
        )
        delivered = restarted.accept(bar("5", B))
        assert [item.event_id for item in delivered] == [
            bar("5", B).event_id, bar("15", B).event_id, native().event_id,
        ]
        resolved, entry = restarted.recover(symbol="APTUSDT")
        assert resolved.pending == []
        assert str(entry.previous_price) == "12.5"
        assert restarted.accept(native()) == ()
        assert conn.execute(
            "SELECT id FROM pg_temp.p90_durable_effects ORDER BY ctid"
        ).fetchall() == [
            (bar("5", B).event_id,),
            (bar("15", B).event_id,),
            ("exit-apt-native-101",),
        ]
        raise RuntimeError("ROLLBACK_DURABLE_PAPER_TEST")
