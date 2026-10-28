"""Disposable PG16: all five actual P90 causal processors share one OHLC source.

This targets the candle-only bridge after a *proven empty native* checkpoint.
It does not claim live Bybit publicTrade/OOS or activation equivalence.
All fixtures and effects are rolled back, not production writes.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
)
from bybit_workbench.universal_entry.contracts import FrozenPolicy
from bybit_workbench.universal_entry.p90l90_atomic_paper_checkpoint import (
    P90L90AtomicPaperCheckpointStore,
)
from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalEntry
from bybit_workbench.universal_entry.p90l90_durable_paper_processor import (
    P90L90DurablePaperProcessor,
)
from bybit_workbench.universal_entry.p90l90_observer_paper_consumer import (
    P90L90ObserverPaperConsumer,
)
from bybit_workbench.universal_entry.p90l90_shared_candle_bridge import (
    P90SharedCandleLedger,
)

DSN = os.getenv("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PG16 only")
SYMBOLS = ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")
SOURCE = "ms-bybit-mainnet-public-v1"
VENUE = "venue-bybit"
ORIGIN = "a" * 40
RELEASE = "b" * 40
T = datetime(2026, 10, 28, 12, 15, tzinfo=UTC)


def _bar(symbol: str, tf: str, i: int) -> Candle:
    at = T + timedelta(minutes=int(tf) * i)
    return Candle(
        symbol, tf, at - timedelta(minutes=int(tf)), at,
        Decimal("100"), Decimal("101"), Decimal("99"),
        Decimal("100.5"), Decimal("10"),
    )


def _fixture(db: psycopg.Connection) -> tuple[
    P90L90ObserverPaperConsumer, str
]:
    """Fully real shared store + actual durable P90 join/Entry checkpoint.

    No private orders and no fabricated market trade. Only raw OHLC
    continuity is under this test's domain.
    """
    archive = object.__new__(P90SharedCandleLedger)
    archive._db = db
    archive._seed = {}
    archive._emitted = {}
    archive._key = {}
    archive.recompute_geometry = lambda **_kwargs: None
    atomic = P90L90AtomicPaperCheckpointStore(db, source_commit=ORIGIN)
    processors = {}
    for symbol in SYMBOLS:
        instrument_id = "inst-ci-p90-" + symbol.lower()
        db.execute(
            """INSERT INTO identity.instruments(
                 instrument_id,venue_id,product_category,external_symbol)
                 VALUES (%s,%s,'LINEAR',%s)""",
            (instrument_id, VENUE, symbol),
        )
        for tf in ("5", "15"):
            key = MarketCandleKey(
                SOURCE, VENUE, instrument_id, symbol, "LINEAR", tf,
            )
            archive._key[symbol, tf] = key
            archive._seed[symbol, tf] = T
            archive._emitted[symbol, tf] = T
            cached = (
                tuple(_bar(symbol, "5", i) for i in (1, 2, 3))
                if tf == "5" else (_bar(symbol, "15", 1),)
            )
            assert SharedClosedCandleStore(db, key).ingest(
                cached, producer_release_commit=RELEASE,
                transport="BYBIT_REST_KLINE",
                observed_at=T+timedelta(minutes=15),
            ) == len(cached)
        atomic.seed(
            symbol=symbol, last_closed_5m=T, last_closed_15m=T,
            entry=P90L90CausalEntry(), proven_geometry=None,
        )
        processors[symbol] = P90L90DurablePaperProcessor(
            db, source_commit=ORIGIN, producer_release_commit=RELEASE,
            bar_ledger=archive,
            on_native_exit=lambda *_args: pytest.fail("native trade synthesized"),
            on_closed_candle=lambda _fact: None,
        )
    # A different Strategy's OPEN PAPER position is deliberately present.
    # It must neither block the scoped P90 recovery nor be modified.
    r1_id = "paper-r1-other-" + uuid4().hex
    db.execute(
        """INSERT INTO strategy_entry.paper_positions(
             paper_position_id,paper_order_id,parent_position_id,
             leg_type,strategy_activation_id,strategy_id,
             strategy_version,strategy_config_fingerprint,
             entry_plan_fingerprint,exit_plan_fingerprint,
             signal_id,symbol,direction,state,opened_at,
             entry_price,stake_usdt,leverage,notional_usdt,
             quantity,best_price,payload)
             VALUES(%s,NULL,NULL,'PRIMARY',%s,'r1_five_symbols',
                    'r1-fixture-v1',%s,'entry-fixture','exit-fixture',
                    'signal-fixture','BTCUSDT','LONG','OPEN',%s,
                    100,10,1,10,0.1,100,'{}'::jsonb)""",
        (r1_id, "activation-" + r1_id, "fingerprint-" + r1_id, T),
    )
    consumer = object.__new__(P90L90ObserverPaperConsumer)
    consumer._connection = db
    consumer._archive = archive
    consumer._processors = processors
    consumer._recovered = True
    consumer._gap_recovery_poisoned = False
    consumer._provenance = FrozenPolicy.from_mapping({
        "source_commit": RELEASE,
    })
    return consumer, r1_id


def _targets() -> dict[str, tuple[datetime, datetime]]:
    end = T + timedelta(minutes=15)
    return {symbol: (end, end) for symbol in SYMBOLS}


def _snap(db: psycopg.Connection) -> tuple[list[tuple[str, int]], int]:
    return (
        db.execute(
            """SELECT symbol,generation FROM
                 strategy_entry.p90l90_causal_transport_checkpoints
                 ORDER BY symbol"""
        ).fetchall(),
        db.execute(
            "SELECT count(*) FROM strategy_entry.p90l90_candle_gap_recovery_audit"
        ).fetchone()[0],
    )


def test_pg16_five_symbol_atomic_candle_replay_and_r1_isolation() -> None:
    assert DSN is not None
    with (
        psycopg.connect(DSN) as db,
        pytest.raises(RuntimeError, match="ROLLBACK_P90_GAP_SUCCESS_TEST"),
        db.transaction(),
    ):
        consumer, r1_id = _fixture(db)
        assert _snap(db) == ([(s, 0) for s in sorted(SYMBOLS)], 0)
        assert consumer.catch_up_closed_histories(_targets()) == 20
        assert _snap(db) == ([(s, 4) for s in sorted(SYMBOLS)], 5)
        audits = db.execute(
            """SELECT symbol,old_generation,new_generation,recovered_bar_count,
                      old_5m_closed_at,through_15m_closed_at
                 FROM strategy_entry.p90l90_candle_gap_recovery_audit
                 ORDER BY symbol"""
        ).fetchall()
        assert all(
            old == 0 and new == 4 and count == 4 and earlier == T
            and later == T+timedelta(minutes=15)
            for _, old, new, count, earlier, later in audits
        )
        assert len(audits) == 5
        assert db.execute(
            """SELECT state,payload FROM strategy_entry.paper_positions
               WHERE paper_position_id=%s""", (r1_id,),
        ).fetchone() == ("OPEN", {})
        assert db.execute(
            "SELECT count(*) FROM strategy_entry.p90l90_causal_closed_bars"
        ).fetchone() == (0,)
        raise RuntimeError("ROLLBACK_P90_GAP_SUCCESS_TEST")


def test_pg16_mid_batch_failure_rolls_back_all_five_and_poison_epoch() -> None:
    assert DSN is not None
    with (
        psycopg.connect(DSN) as db,
        pytest.raises(RuntimeError, match="ROLLBACK_P90_GAP_FAILURE_TEST"),
        db.transaction(),
    ):
        consumer, r1_id = _fixture(db)
        saved = _snap(db)
        def inject_failure(_event: object) -> None:
            raise RuntimeError("INJECTED_THIRD_SYMBOL_FAILURE")
        consumer._processors["INJUSDT"].accept = inject_failure
        with pytest.raises(RuntimeError, match="INJECTED_THIRD_SYMBOL_FAILURE"):
            consumer.catch_up_closed_histories(_targets())
        assert _snap(db) == saved
        assert consumer._gap_recovery_poisoned is True
        with pytest.raises(RuntimeError, match="EPOCH_POISONED"):
            consumer.catch_up_closed_histories(_targets())
        assert db.execute(
            """SELECT state FROM strategy_entry.paper_positions
                WHERE paper_position_id=%s""", (r1_id,),
        ).fetchone() == ("OPEN",)
        raise RuntimeError("ROLLBACK_P90_GAP_FAILURE_TEST")
