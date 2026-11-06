"""Native disposable PostgreSQL atomic REAL/PAPER delivery with >512 execIDs.

The test intentionally uses a fake 2026-01 source epoch, synthetic prices,
no private Bybit keys, no Exchange calls, no LIVE permission or real position.
All writes are enclosed by one rollback-only disposable PG16 transaction.
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
from bybit_workbench.universal_entry.p90l90_atomic_real_epoch_checkpoint import (
    P90L90AtomicRealEpochCheckpointStore,
)
from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalEntry
from bybit_workbench.universal_entry.p90l90_causal_fact_codec import (
    encode_p90l90_fact,
    restore_p90l90_fact,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PG16 DSN only")
B = datetime(2026, 1, 1, tzinfo=UTC)
SOURCE = "a" * 40
EPOCH = "p90-real-epoch-" + "b" * 40
SYMBOL = "LTCUSDT"  # Existing whitelist, simulated source only.


def _native(i: int):
    traded_at = B + timedelta(milliseconds=i + 1)
    exec_id = f"synthetic-atomic-17-{i}"
    fact = MarketFactEnvelope(
        fact_id=f"synthetic-trade-{i}",
        event_kind="PUBLIC_TRADE",
        symbol=SYMBOL,
        event_at=traded_at,
        observed_at=traded_at,
        received_at=traded_at + timedelta(milliseconds=1),
        source_refs=(f"synthetic:publicTrade:{SYMBOL}:{exec_id}",),
        attributes=FrozenPolicy.from_mapping({
            "price": "1", "size": "1", "taker_side": "Buy",
        }),
    )
    return encode_p90l90_fact(
        fact, cursor=TradeCursor(SYMBOL, exec_id, 17, traded_at),
    )


@pytest.mark.parametrize("mode", ("REAL", "PAPER"))
def test_original_atomic_checkpoint_commits_more_than_512_and_rolls_back(
    mode: str,
) -> None:
    assert DSN is not None
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_ORIGINAL_ATOMIC_TEST"),
        conn.transaction(),
    ):
        conn.execute(
            "CREATE TEMP TABLE native_effects(exec_id text NOT NULL) ON COMMIT DROP"
        )
        if mode == "REAL":
            conn.execute(
                """INSERT INTO strategy_entry.p90l90_real_source_epochs(
                     epoch_id,source_commit,market_source_id,opened_at,
                     source_ws_ready_at,native_mirror_ready_at,status)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (EPOCH, SOURCE, "ms-bybit-mainnet-public-v1", B, B, B, "ACTIVE"),
            )
            store = P90L90AtomicRealEpochCheckpointStore(
                conn, source_commit=SOURCE, epoch_id=EPOCH,
            )
        else:
            store = P90L90AtomicPaperCheckpointStore(
                conn, source_commit=SOURCE,
            )
        store.seed(
            symbol=SYMBOL, last_closed_5m=B, last_closed_15m=B,
            entry=P90L90CausalEntry(), proven_geometry=None,
        )

        def geometry(_symbol, _checkpoint):
            return None

        def apply(entry, batch):
            for item in batch:
                original, cursor = restore_p90l90_fact(item)
                assert cursor is not None
                entry.on_trade(
                    price=Decimal(str(original.attributes.to_dict()["price"])),
                    event_at=item.event_at,
                )
                conn.execute(
                    "INSERT INTO pg_temp.native_effects(exec_id) VALUES(%s)",
                    (cursor.exec_id,),
                )

        for i in range(520):
            trade = _native(i)
            assert store.deliver(
                trade, recompute_geometry=geometry, apply_paper=apply,
            ) == (trade,)
        join, entry = store.inspect_verified(
            symbol=SYMBOL, recompute_geometry=geometry,
        )
        assert join.native_id_journal_count == 520
        assert len(join.last_emitted_exec_ids) == 512
        assert entry.previous_at == _native(519).event_at
        assert conn.execute(
            "SELECT count(*) FROM pg_temp.native_effects"
        ).fetchone() == (520,)

        if mode == "REAL":
            current = conn.execute(
                """SELECT checkpoint_json,entry_checkpoint_json,generation
                     FROM strategy_entry.p90l90_real_causal_checkpoints
                    WHERE epoch_id=%s AND symbol=%s""",
                (EPOCH, SYMBOL),
            ).fetchone()
        else:
            current = conn.execute(
                """SELECT checkpoint_json,entry_checkpoint_json,generation
                     FROM strategy_entry.p90l90_causal_transport_checkpoints
                    WHERE symbol=%s""",
                (SYMBOL,),
            ).fetchone()
        assert current is not None
        assert current[0]["schema"] == "P90L90_CAUSAL_TRANSPORT_JOIN_V2"

        def injected_failure(entry, batch):
            apply(entry, batch)
            raise RuntimeError("INJECTED_EXACT_NATIVE_DB_ROLLBACK")

        with pytest.raises(RuntimeError, match="INJECTED_EXACT_NATIVE_DB_ROLLBACK"):
            store.deliver(
                _native(520), recompute_geometry=geometry,
                apply_paper=injected_failure,
            )
        # Reopen from durable state after nested rollback; neither the
        # checkpoint nor newly inserted journal ID or dependent effect landed.
        if mode == "REAL":
            restored = conn.execute(
                """SELECT checkpoint_json,entry_checkpoint_json,generation
                     FROM strategy_entry.p90l90_real_causal_checkpoints
                    WHERE epoch_id=%s AND symbol=%s""",
                (EPOCH, SYMBOL),
            ).fetchone()
        else:
            restored = conn.execute(
                """SELECT checkpoint_json,entry_checkpoint_json,generation
                     FROM strategy_entry.p90l90_causal_transport_checkpoints
                    WHERE symbol=%s""",
                (SYMBOL,),
            ).fetchone()
        assert restored == current
        assert conn.execute(
            "SELECT count(*) FROM pg_temp.native_effects"
        ).fetchone() == (520,)
        assert store.deliver(
            _native(520), recompute_geometry=geometry, apply_paper=apply,
        ) == (_native(520),)
        assert conn.execute(
            "SELECT count(*) FROM pg_temp.native_effects"
        ).fetchone() == (521,)
        raise RuntimeError("ROLLBACK_ORIGINAL_ATOMIC_TEST")
