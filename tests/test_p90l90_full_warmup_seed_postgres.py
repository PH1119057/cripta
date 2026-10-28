"""Real PG16 full 7d/ATR200 P90 seed → restart → native crossing proof.

Disposable PostgreSQL only, all rows intentionally rolled back. Unlike a
small transport fixture this reconstructs an entire causal seed of 2600
closed 5m and 1000 closed 15m bars with source-bound JSON checkpoints.
No StrategyActivation, real exchange orders or production database access.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    MarketFactEnvelope,
)
from bybit_workbench.universal_entry.fingerprint import canonical_json
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
from bybit_workbench.universal_entry.p90l90_offline_seed_plan import (
    plan_p90l90_offline_seed,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="requires disposable PG16")
B = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SHA = "a" * 40


def _bars(
    tf: str, count: int, *, symbol: str = "LTCUSDT"
) -> tuple[Candle, ...]:
    period = timedelta(minutes=int(tf))
    return tuple(
        Candle(
            symbol=symbol, timeframe=tf,
            opened_at=B-period*(count-i),
            closed_at=B-period*(count-i-1),
            open=Decimal("100"), high=Decimal("101"),
            low=Decimal("99"), close=Decimal("100"),
            volume=Decimal("2"), is_closed=True,
        )
        for i in range(count)
    )


def _seed_event(candle: Candle):
    at = candle.closed_at
    fact = MarketFactEnvelope(
        fact_id=f"rest-seed:{candle.timeframe}:{at.isoformat()}",
        event_kind="CANDLE_CLOSED", symbol=candle.symbol,
        observed_at=at, event_at=at, received_at=B+timedelta(seconds=1),
        source_refs=(
            f"bybit:REST:linear:kline:{candle.symbol}:{candle.timeframe}:"
            f"{candle.opened_at.isoformat()}",
        ),
        attributes=FrozenPolicy.from_mapping({
            "timeframe":candle.timeframe,
            "opened_at":candle.opened_at.isoformat(),
            "closed_at":candle.closed_at.isoformat(),
            "open":str(candle.open), "high":str(candle.high),
            "low":str(candle.low), "close":str(candle.close),
            "volume":str(candle.volume),
        }),
    )
    return encode_p90l90_fact(fact)


def _trade(seq: int, price: str):
    at = B+timedelta(seconds=seq-100)
    fact = MarketFactEnvelope(
        fact_id=f"trade-exec-{seq}", event_kind="PUBLIC_TRADE",
        symbol="LTCUSDT", observed_at=at, event_at=at,
        received_at=at+timedelta(milliseconds=100),
        source_refs=(f"bybit:publicTrade:LTCUSDT:exec-{seq}",),
        attributes=FrozenPolicy.from_mapping({
            "price":price, "size":"1", "taker_side":"Sell",
        }),
    )
    return encode_p90l90_fact(
        fact, cursor=TradeCursor("LTCUSDT", f"exec-{seq}", seq, at),
    )


def test_pg16_complete_history_seed_restores_and_emits_one_native_entry():
    if not DSN:
        pytest.skip("disposable PostgreSQL required")
    fives, fifteens = _bars("5",2600), _bars("15",1000)
    plan = plan_p90l90_offline_seed(
        "LTCUSDT", {"5":fives, "15":fifteens},
    )
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_FULL_P90_SEED_PROOF"),
        conn.transaction(),
    ):
        rows = []
        for candle in (*plan.fives, *plan.fifteens):
            event = _seed_event(candle)
            rows.append((
                "LTCUSDT",candle.timeframe,candle.closed_at,
                canonical_json(event.record()),SHA,
            ))
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO strategy_entry.p90l90_causal_closed_bars
                    (symbol,timeframe,closed_at,event_json,source_commit)
                    VALUES(%s,%s,%s,%s::jsonb,%s)""",
                rows,
            )
        assert conn.execute(
            """SELECT count(*) FROM strategy_entry.p90l90_causal_closed_bars
                WHERE symbol='LTCUSDT'"""
        ).fetchone() == (3600,)
        ledger = P90L90ImmutableBarLedger(conn,source_commit=SHA)
        recomputed = ledger.recompute_geometry(
            symbol="LTCUSDT",effective_at=B,
        )
        assert recomputed == plan.initial_geometry
        atomic = P90L90AtomicPaperCheckpointStore(conn,source_commit=SHA)
        atomic.seed(
            symbol="LTCUSDT",last_closed_5m=B,last_closed_15m=B,
            entry=P90L90CausalEntry(state=recomputed),
            proven_geometry=recomputed,
        )
        seen = []
        processor = P90L90DurablePaperProcessor(
            conn,source_commit=SHA,
            on_native_exit=lambda fact,cursor:seen.append(("exit",cursor.seq)),
            on_closed_candle=lambda fact:None,
            candidate_entry_plan_fingerprint="exact-immutable-p90-entry",
            on_proven_candidate=lambda fact,cursor:seen.append((
                "entry",cursor.seq,fact.attributes.to_dict(),
            )),
        )
        before_join,before_entry=processor.recover(symbol="LTCUSDT")
        assert before_join.pending == []
        assert before_entry.state == plan.initial_geometry
        assert before_entry.previous_price is None
        assert processor.accept(_trade(101,"100")) != ()
        assert processor.accept(_trade(102,"98")) != ()
        assert [item[0] for item in seen] == ["exit","exit","entry"]
        assert seen[-1][2]["modeled_exchange_fill"] is False
        assert seen[-1][2]["trade_fact_id"] == "trade-exec-102"
        assert seen[-1][2]["raw_entry_boundary"] == "99"
        restarted = P90L90DurablePaperProcessor(
            conn,source_commit=SHA,
            on_native_exit=lambda fact,cursor:seen.append(("replayed-exit",cursor.seq)),
            on_closed_candle=lambda fact:None,
            candidate_entry_plan_fingerprint="exact-immutable-p90-entry",
            on_proven_candidate=lambda fact,cursor:seen.append(
                ("replayed-entry",cursor.seq)
            ),
        )
        joined,entry=restarted.recover(symbol="LTCUSDT")
        assert joined.pending == []
        assert entry.previous_price == Decimal("98")
        assert entry.seen == {"LONG"}
        assert restarted.accept(_trade(102,"98")) == ()
        assert len(seen) == 3
        raise RuntimeError("ROLLBACK_FULL_P90_SEED_PROOF")


def test_pg16_live_shape_native_join_to_real_two_symbol_main_paper_and_restart():
    """Exact P90 consumer: full source archive -> Engine -> PAPER -> Exit/DB restart.

    The original v1/R1 never participates. Every SQL effect is rolled back
    on the disposable CI database, not production or Bybit.
    """
    from uuid import uuid4

    from bybit_workbench.universal_entry.contracts import (
        FrozenPolicy, StrategyActivation,
    )
    from bybit_workbench.universal_entry.materializer import materialize_plans
    from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
    from bybit_workbench.universal_entry.p90l90_observer_paper_consumer import (
        P90L90ObserverPaperConsumer,
    )
    from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
        StrategyHardStopEmbargoPolicy,
    )
    from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards
    from bybit_workbench.universal_entry.runtime_loader import ActiveStrategyBundle
    from bybit_workbench.universal_entry.storage import StrategyEntryStore

    policy = StrategyHardStopEmbargoPolicy.parse({
        "enabled": True, "hardstop_count": 2, "rolling_hours": 48,
        "new_entry_pause_hours": 48, "close_existing_positions": False,
        "domain": "STRATEGY_VERSION",
    })
    card = next(
        card for card in new_version_cards(
            approved_at=B-timedelta(minutes=1),
            policy_by_strategy_id={MAIN_ID: policy},
        ) if card.strategy_id == MAIN_ID
    )
    activation = StrategyActivation(
        activation_id="full-p90-main-" + uuid4().hex,
        strategy_id=card.strategy_id, strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True, enabled_at=B-timedelta(minutes=1),
    )
    entry_plan, exit_plan = materialize_plans(card, activation)
    bundle = ActiveStrategyBundle(card, activation, entry_plan, exit_plan, B)
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_P90_FULL_CONSUMER_MAIN"),
        conn.transaction(),
    ):
        account = "ta-p90-full-consumer-" + uuid4().hex
        conn.execute(
            """INSERT INTO identity.trading_accounts(
                trading_account_id,venue_id,account_kind,account_type,
                environment,status,external_account_ref)
               VALUES(%s,'venue-bybit','MAIN','UNIFIED','MAINNET','ACTIVE',
                      '9000000000')""",
            (account,),
        )
        store = StrategyEntryStore(conn)
        store.insert_strategy_card(card)
        store.insert_activation(activation, reason="CI-full-consumer")
        store.insert_entry_plan(entry_plan)
        store.insert_exit_plan(exit_plan)
        conn.execute(
            """INSERT INTO strategy_entry.strategy_execution_bindings(
                strategy_execution_binding_id,strategy_activation_id,
                execution_leg,execution_role,trading_account_id,
                allocation_mode,binding_state,effective_from,operator,source,reason)
               VALUES(%s,%s,'PRIMARY','PRIMARY',%s,'DEDICATED_STRATEGY',
                      'INACTIVE',%s,'CI','disposable-test','paper-only')""",
            ("binding-ci-"+uuid4().hex, activation.activation_id, account, B),
        )
        rows = []
        for symbol in card.symbols:
            fives, fifteens = _bars("5",2600,symbol=symbol), _bars(
                "15",1000,symbol=symbol,
            )
            plan = plan_p90l90_offline_seed(
                symbol, {"5": fives, "15": fifteens},
            )
            rows.extend(
                (
                    symbol, candle.timeframe, candle.closed_at,
                    canonical_json(_seed_event(candle).record()), SHA,
                )
                for candle in (*plan.fives, *plan.fifteens)
            )
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO strategy_entry.p90l90_causal_closed_bars
                    (symbol,timeframe,closed_at,event_json,source_commit)
                    VALUES(%s,%s,%s,%s::jsonb,%s)""",
                rows,
            )
        ledger = P90L90ImmutableBarLedger(conn, source_commit=SHA)
        atomic = P90L90AtomicPaperCheckpointStore(conn, source_commit=SHA)
        for symbol in card.symbols:
            state = ledger.recompute_geometry(symbol=symbol, effective_at=B)
            assert state is not None
            atomic.seed(
                symbol=symbol, last_closed_5m=B,last_closed_15m=B,
                entry=P90L90CausalEntry(state=state), proven_geometry=state,
            )
        before_orders = conn.execute(
            "SELECT count(*) FROM strategy_entry.paper_orders"
        ).fetchone()[0]
        consumer = P90L90ObserverPaperConsumer(
            conn, bundles=(bundle,), source_commit=SHA,
            provenance=FrozenPolicy.from_mapping({
                "source":"p90-full-consumer-real-PG-no-orders",
            }),
        )
        consumer.recover_all()
        assert consumer.recovered_symbols == frozenset(card.symbols)
        symbol = card.symbols[0]

        def native(seq: int, price: str):
            at = B+timedelta(seconds=seq-100)
            fact = MarketFactEnvelope(
                fact_id=f"trade-full-{symbol}-{seq}",
                event_kind="PUBLIC_TRADE", symbol=symbol,
                observed_at=at,event_at=at,
                received_at=at+timedelta(milliseconds=100),
                source_refs=(f"bybit:publicTrade:{symbol}:full-{seq}",),
                attributes=FrozenPolicy.from_mapping({
                    "price":price,"size":"1","taker_side":"Sell",
                }),
            )
            return encode_p90l90_fact(
                fact,cursor=TradeCursor(symbol,f"full-{seq}",seq,at),
            )

        # Exact neighboring native ticks first cross fully restored RAW L5-3.
        assert len(consumer.accept(native(101,"100"))) == 1
        assert len(consumer.accept(native(102,"98"))) == 1
        current = conn.execute(
            """SELECT paper_position_id,paper_order_id,notional_usdt,state,
                      payload FROM strategy_entry.paper_positions
                 WHERE strategy_id=%s AND strategy_version=%s""",
            (MAIN_ID,card.strategy_version),
        ).fetchall()
        assert len(current) == 1
        position_id, order_id, nominal, state, payload = current[0]
        assert order_id is None and nominal == Decimal("10") and state == "OPEN"
        assert payload["p90l90_exit_checkpoint"]["trading_account_id"] == account
        assert payload["modeled_exchange_fill"] is False

        # Recreate full consumer + its exact Engine and PAPER state from DB:
        # original execId/seq is idempotent, and native trailing Exit persists.
        restarted = P90L90ObserverPaperConsumer(
            conn,bundles=(bundle,),source_commit=SHA,
            provenance=FrozenPolicy.from_mapping({"source":"p90-restarted"}),
        )
        restarted.recover_all()
        assert restarted.accept(native(102,"98")) == ()
        restarted.accept(native(103,"99"))
        restarted.accept(native(104,"98.7"))
        assert conn.execute(
            "SELECT count(*) FROM strategy_entry.paper_orders"
        ).fetchone()[0] == before_orders
        assert conn.execute(
            """SELECT count(*) FROM strategy_entry.paper_positions
                 WHERE strategy_id=%s""", (MAIN_ID,),
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled"
        ).fetchone() == (0,)
        raise RuntimeError("ROLLBACK_P90_FULL_CONSUMER_MAIN")
