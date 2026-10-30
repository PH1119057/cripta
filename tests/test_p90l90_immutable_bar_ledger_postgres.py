"""Disposable PG16 immutable P90L90 closed-bar archive and gap proof.

Every transaction is explicitly rolled back. No real market or runtime action.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.p90l90_causal_fact_codec import (
    encode_p90l90_fact,
)
from bybit_workbench.universal_entry.p90l90_immutable_bar_ledger import (
    P90L90ImmutableBarLedger,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PG16 DSN only")
B = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
SHA = "a" * 40


def closed(tf: str, minute: int = 0, price: str = "100.5"):
    at = B + timedelta(minutes=minute)
    fact = MarketFactEnvelope(
        fact_id=f"bybit-ws-{tf}-{minute}-{price}",
        event_kind="CANDLE_CLOSED", symbol="ARBUSDT",
        observed_at=at, event_at=at,
        received_at=at+timedelta(seconds=2),
        source_refs=(f"bybit:kline.{tf}:ARBUSDT:{at.isoformat()}",),
        attributes=FrozenPolicy.from_mapping({
            "timeframe": tf,
            "opened_at": (at-timedelta(minutes=int(tf))).isoformat(),
            "closed_at": at.isoformat(),
            "open": "100", "high": "101", "low": "99",
            "close": price, "volume": "12",
        }),
    )
    return encode_p90l90_fact(fact)


def test_immutable_candle_archive_dedup_gap_and_independent_asof_geometry():
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_BAR_ARCHIVE"),
        conn.transaction(),
    ):
        grant = conn.execute(
            """SELECT has_table_privilege(
                  'cripta','strategy_entry.p90l90_causal_closed_bars','SELECT,INSERT'),
                 has_table_privilege(
                  'cripta','strategy_entry.p90l90_causal_closed_bars','UPDATE'),
                 has_table_privilege(
                  'cripta','strategy_entry.p90l90_causal_closed_bars','DELETE')"""
        ).fetchone()
        assert grant == (True, False, False)
        store = P90L90ImmutableBarLedger(conn, source_commit=SHA)
        five = closed("5")
        assert store.append(five) is True
        assert store.append(five) is False
        assert store.append(closed("15")) is True
        assert store.history(
            symbol="ARBUSDT", timeframe="5", through=B, limit=20,
        )[0].close == 100.5
        assert store.recompute_geometry(symbol="ARBUSDT", effective_at=B) is None
        with pytest.raises(RuntimeError, match="conflicting immutable"):
            store.append(closed("5", price="100.6"))
        assert store.append(closed("5", minute=10)) is True
        with pytest.raises(RuntimeError, match="contains a gap"):
            store.history(
                symbol="ARBUSDT", timeframe="5",
                through=B+timedelta(minutes=10), limit=5,
            )
        assert store.append(closed("5", minute=5)) is True
        assert len(store.history(
            symbol="ARBUSDT", timeframe="5",
            through=B+timedelta(minutes=10), limit=5,
        )) == 3
        with pytest.raises(RuntimeError, match="incomplete at causal boundary"):
            store.history(
                symbol="ARBUSDT", timeframe="15",
                through=B+timedelta(minutes=15), limit=5,
            )
        with pytest.raises(ValueError, match="requires exact UTC"):
            store.history(
                symbol="ARBUSDT", timeframe="5",
                through=B+timedelta(minutes=3), limit=5,
            )
        with pytest.raises(ValueError, match="unbounded"):
            store.history(
                symbol="ARBUSDT", timeframe="5", through=B, limit=200000,
            )
        raise RuntimeError("ROLLBACK_BAR_ARCHIVE")


def test_source_version_change_cannot_silently_reinterpret_saved_bars():
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_BAR_SOURCE"),
        conn.transaction(),
    ):
        P90L90ImmutableBarLedger(conn, source_commit=SHA).append(closed("5"))
        changed = P90L90ImmutableBarLedger(conn, source_commit="f" * 40)
        with pytest.raises(RuntimeError, match="source release mismatch"):
            changed.history(
                symbol="ARBUSDT", timeframe="5", through=B, limit=2,
            )
        with pytest.raises(RuntimeError, match="conflicting immutable"):
            changed.append(closed("5"))
        raise RuntimeError("ROLLBACK_BAR_SOURCE")
