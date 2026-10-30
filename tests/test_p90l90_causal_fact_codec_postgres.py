"""Real disposable PostgreSQL checkpoint + normalized native fact replay proof.

No production database, Bybit API, exchange order, or StrategyActivation.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.p90l90_causal_fact_codec import (
    encode_p90l90_fact,
    restore_p90l90_fact,
)
from bybit_workbench.universal_entry.p90l90_causal_transport_store import (
    P90L90CausalJoinStore,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="explicit disposable PG16 DSN required")
T = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
SHA = "3" * 40


def fact(kind: str) -> tuple[MarketFactEnvelope, TradeCursor | None]:
    if kind == "PUBLIC_TRADE":
        at = T+timedelta(seconds=1)
        return MarketFactEnvelope(
            fact_id="inj-native-88", event_kind=kind, symbol="INJUSDT",
            observed_at=at, event_at=at,
            received_at=at+timedelta(seconds=1),
            source_refs=("bybit:publicTrade:INJUSDT:88",),
            attributes=FrozenPolicy.from_mapping({
                "price":"42.125","size":"3","taker_side":"Sell",
            }),
        ), TradeCursor("INJUSDT","trade-exec-88",88,at)
    tf = kind
    return MarketFactEnvelope(
        fact_id=f"inj-closed-{tf}", event_kind="CANDLE_CLOSED",
        symbol="INJUSDT", observed_at=T, event_at=T,
        received_at=T+timedelta(seconds=2),
        source_refs=(f"bybit:kline.{tf}:INJUSDT:closed",),
        attributes=FrozenPolicy.from_mapping({
            "timeframe":tf,
            "opened_at":(T-timedelta(minutes=int(tf))).isoformat(),
            "closed_at":T.isoformat(),
            "open":"42","high":"43","low":"41","close":"42.5","volume":"50",
        }),
    ), None


def test_exact_normalized_native_cursor_and_delayed_bars_in_pg_transaction():
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_INJ_CODEC_TEST"),
        conn.transaction(),
    ):
        conn.execute(
            "CREATE TEMP TABLE p90_normalized_dispatch(event_id text NOT NULL,"
            " cursor_seq bigint NULL) ON COMMIT DROP"
        )
        store = P90L90CausalJoinStore(conn, source_commit=SHA)
        store.initialize(
            symbol="INJUSDT",
            last_closed_5m=T-timedelta(minutes=5),
            last_closed_15m=T-timedelta(minutes=15),
        )
        trade = encode_p90l90_fact(*fact("PUBLIC_TRADE")[:1], cursor=fact("PUBLIC_TRADE")[1])
        five_fact, five_cursor = fact("5")
        fifteen_fact, fifteen_cursor = fact("15")
        five = encode_p90l90_fact(five_fact, cursor=five_cursor)
        fifteen = encode_p90l90_fact(fifteen_fact, cursor=fifteen_cursor)

        def dispatch(batch):
            for item in batch:
                recovered, native_cursor = restore_p90l90_fact(item)
                conn.execute(
                    "INSERT INTO pg_temp.p90_normalized_dispatch VALUES (%s,%s)",
                    (recovered.fact_id, None if native_cursor is None else native_cursor.seq),
                )

        assert store.deliver(trade, dispatch=dispatch) == ()
        assert store.deliver(fifteen, dispatch=dispatch) == ()
        snapshot = conn.execute(
            "SELECT generation,checkpoint_json FROM "
            "strategy_entry.p90l90_causal_transport_checkpoints "
            "WHERE symbol='INJUSDT'"
        ).fetchone()
        assert snapshot[0] == 2 and len(snapshot[1]["pending"]) == 2

        # Failing DB handler cannot commit an acknowledged candle or a trade.
        def fail(batch):
            dispatch(batch)
            raise RuntimeError("INJECTED_ABORT")

        with pytest.raises(RuntimeError, match="INJECTED_ABORT"):
            store.deliver(five, dispatch=fail)
        assert conn.execute(
            "SELECT generation,checkpoint_json FROM "
            "strategy_entry.p90l90_causal_transport_checkpoints "
            "WHERE symbol='INJUSDT'"
        ).fetchone() == snapshot
        assert conn.execute(
            "SELECT COUNT(*) FROM pg_temp.p90_normalized_dispatch"
        ).fetchone() == (0,)

        recovered = P90L90CausalJoinStore(conn, source_commit=SHA)
        delivered = recovered.deliver(five, dispatch=dispatch)
        assert delivered == (five, fifteen, trade)
        assert conn.execute(
            "SELECT event_id,cursor_seq FROM pg_temp.p90_normalized_dispatch ORDER BY ctid"
        ).fetchall() == [
            ("inj-closed-5", None), ("inj-closed-15", None), ("inj-native-88", 88)
        ]
        assert recovered.deliver(trade, dispatch=dispatch) == ()
        assert conn.execute(
            "SELECT COUNT(*) FROM pg_temp.p90_normalized_dispatch"
        ).fetchone() == (3,)
        raise RuntimeError("ROLLBACK_INJ_CODEC_TEST")
