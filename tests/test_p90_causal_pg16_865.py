"""#865 C2: real PostgreSQL16 two-connection P90 source/Exit ownership tests.

Runs ONLY on a disposable independent PG16 cluster bound to a private Unix
socket under the dedicated CRIPTA testenv. No production database or Bybit.
The exact candidate runner must start and independently prove that cluster.
"""
from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row, tuple_row

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.market_data.r10_execution_publication import (
    R10ExecutionPublicationUnavailable,
    read_r10_execution_quote,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.universal_exit.p90l90_real_exit_execution import (
    lock_p90_exit_account,
)
from bybit_workbench.universal_exit.p90l90_real_pending import (
    p90_real_exit_no_pending,
)
from operations.connectivity.p90_real_persistent_trail import (
    P90PersistentTrailBlocked,
    _candidate,
    _source_quote,
)
from test_p90_causal_release_preflight_865 import _case

_TESTENV = Path("/data/cripta/research/testenvs/p90_causal_pg16")
_SOCKET = _TESTENV / "socket"
_PORT = 6546
_EPOCH = "a" * 32
_COMMIT = "b" * 40


def connect(*, admin: bool = False, autocommit: bool = True, tuples: bool = False):
    # Never read production PGHOST/DSN or accept a caller-provided endpoint.
    if (
        os.getenv("CRIPTA_P90_C2_EXACT_ISOLATED") != "1"
        or not _SOCKET.is_dir()
        or _SOCKET.is_symlink()
    ):
        raise AssertionError("C2_ISOLATED_SOCKET_NOT_PROVEN")
    return psycopg.connect(
        host=str(_SOCKET), port=_PORT, dbname="cripta_integration",
        user="postgres" if admin else "cripta",
        connect_timeout=3, autocommit=autocommit,
        row_factory=tuple_row if tuples else dict_row,
    )


def positions():
    main, *_ = _case(short=False)
    sub, *_ = _case(short=True)
    main_id = "ta-" + "1" * 32
    sub_id = "ta-" + "2" * 32
    return (
        replace(
            main, strategy_position_id="pos-865-main",
            trading_account_id=main_id,
            exchange_position_key=exchange_position_key(main_id, main.symbol, 0),
        ),
        replace(
            sub, strategy_position_id="pos-865-sub",
            trading_account_id=sub_id,
            exchange_position_key=exchange_position_key(sub_id, sub.symbol, 0),
        ),
    )


def publication(admin: psycopg.Connection, *, now: datetime):
    instrument = bybit_instrument_id("linear", "LTCUSDT")
    t = now.replace(microsecond=0)
    iso = t.isoformat()
    ticker_ref = {
        "TICKER": {
            "source_session_id": _EPOCH,
            "instrument_id": instrument,
            "external_symbol": "LTCUSDT",
            "product_category": "LINEAR",
            "observed_second": iso,
            "source_time_max": iso,
        },
    }
    quality = {
        "TICKER": {
            "required": True,
            "status": "OBSERVED",
            "fields": {
                "lastPrice": "FRESH", "bid1Price": "FRESH",
                "ask1Price": "FRESH",
            },
        },
    }
    original = {
        "ticker": {
            "lastPrice": "102", "bid1Price": "101.99",
            "ask1Price": "102.01",
        },
        "ticker_field_times": {
            "lastPrice": iso, "bid1Price": iso, "ask1Price": iso,
        },
    }
    admin.execute(
        """INSERT INTO analytics.native_public_trade_source_sessions
            (source_session_id,market_source_id,producer_release_commit,
             state,subscribed_at)
           VALUES (%s,%s,%s,'ACTIVE',%s)""",
        (_EPOCH, BYBIT_PUBLIC_MARKET_SOURCE_ID, _COMMIT, t),
    )
    admin.execute(
        """INSERT INTO analytics.market_publications
            (publication_id,market_source_id,source_session_id,
             producer_release_commit,as_of_second,publication_status,
             closed_instrument_refs,closed_instrument_coverage,
             compact_second_refs,compact_field_coverage)
           VALUES (865,%s,%s,%s,%s,'WATERMARKS_ONLY',
                   '{}'::jsonb,'{}'::jsonb,%s::jsonb,%s::jsonb)""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID, _EPOCH, _COMMIT, t,
         json.dumps({instrument: ticker_ref}),
         json.dumps({instrument: quality})),
    )
    admin.execute(
        """INSERT INTO analytics.public_market_source_seconds
            (source_session_id,instrument_id,observed_second,
             market_source_id,producer_release_commit,product_category,
             external_symbol,source_time_max,compact_features)
           VALUES (%s,%s,%s,%s,%s,'LINEAR','LTCUSDT',%s,%s::jsonb)""",
        (_EPOCH, instrument, t, BYBIT_PUBLIC_MARKET_SOURCE_ID,
         _COMMIT, t, json.dumps(original)),
    )


@unittest.skipUnless(
    os.getenv("CRIPTA_P90_C2_EXACT_ISOLATED") == "1",
    "Only the explicitly provisioned independent PG16 cluster",
)
class P90CausalPostgresTests(unittest.TestCase):
    """C2 tests use actual candidate SQL queries and real PG locks."""

    def setUp(self):
        self.main, self.sub = positions()
        with connect(admin=True) as admin:
            for table in (
                "runtime.trade_commands", "strategy_exit.execution_dispatches",
                "strategy_exit.execution_requests", "strategy_exit.exit_decisions",
                "runtime.position_ownership", "analytics.market_publications",
                "analytics.public_market_source_seconds",
                "analytics.native_public_trade_source_sessions",
            ):
                admin.execute("TRUNCATE " + table)
            for p in (self.main, self.sub):
                admin.execute(
                    """INSERT INTO runtime.position_ownership
                       (position_id,trading_account_id,symbol,state,
                        strategy_id,strategy_version,identity_status,
                        strategy_execution_binding_id,exchange_position_key,
                        position_idx,initial_protection_confirmed_at,
                        initial_protection_evidence)
                       VALUES (%s,%s,%s,'OPEN',%s,%s,'PROVEN',%s,%s,0,%s,
                               '{"trailing_recovery_policy":"P90_REAL_TRAIL035_PERSISTENT_V1"}'::jsonb)""",
                    (p.strategy_position_id, p.trading_account_id, p.symbol,
                     p.strategy_id, p.strategy_version,
                     p.strategy_execution_binding_id, p.exchange_position_key,
                     p.initial_protection_confirmed_at),
                )

    def test_disposable_pg16_database_and_restricted_actor(self):
        with connect() as c:
            row = c.execute(
                "SELECT current_user,current_database(),inet_server_addr(),"
                "current_setting('server_version_num')::int"
            ).fetchone()
            self.assertEqual(row["current_user"], "cripta")
            self.assertEqual(row["current_database"], "cripta_integration")
            self.assertIsNone(row["inet_server_addr"])
            self.assertGreaterEqual(row["current_setting"], 160000)
            self.assertFalse(c.execute(
                "SELECT has_table_privilege('cripta',"
                "'runtime.position_ownership','UPDATE') AS allowed"
            ).fetchone()["allowed"])

    def test_immutable_new_fill_marker_and_multi_account_candidate(self):
        with connect(admin=True) as admin:
            admin.execute(
                """UPDATE runtime.position_ownership
                      SET initial_protection_evidence='{}'::jsonb
                    WHERE position_id=%s""",
                (self.sub.strategy_position_id,),
            )
        with connect() as c:
            candidate = _candidate(c)
            self.assertEqual(candidate["position_id"], self.main.strategy_position_id)
            self.assertTrue(p90_real_exit_no_pending(
                c, position=self.main,
                trading_account_id=self.main.trading_account_id,
            ))

    def test_p90_real_no_pending_uses_actual_pg_joins(self):
        with connect() as c:
            self.assertTrue(p90_real_exit_no_pending(
                c, position=self.main,
                trading_account_id=self.main.trading_account_id,
            ))
            self.assertTrue(p90_real_exit_no_pending(
                c, position=self.sub,
                trading_account_id=self.sub.trading_account_id,
            ))

    def test_unmaterialized_decision_blocks_only_main_account(self):
        with connect(admin=True) as admin:
            admin.execute(
                """INSERT INTO strategy_exit.exit_decisions
                   (exit_decision_id,strategy_position_id,strategy_id,
                    strategy_version,strategy_config_fingerprint,
                    exit_plan_fingerprint)
                   VALUES('decision-main',%s,%s,%s,%s,%s)""",
                (self.main.strategy_position_id,self.main.strategy_id,
                 self.main.strategy_version,
                 self.main.strategy_config_fingerprint,
                 self.main.exit_plan_fingerprint),
            )
        with connect() as c:
            self.assertFalse(p90_real_exit_no_pending(
                c, position=self.main,
                trading_account_id=self.main.trading_account_id,
            ))
            self.assertTrue(p90_real_exit_no_pending(
                c, position=self.sub,
                trading_account_id=self.sub.trading_account_id,
            ))
            self.assertTrue(p90_real_exit_no_pending(
                c, position=self.main,
                trading_account_id=self.main.trading_account_id,
                publishing_exit_decision_id="decision-main",
            ))

    def test_orphan_unknown_exit_command_blocks_new_trailing(self):
        with connect(admin=True) as admin:
            admin.execute(
                """INSERT INTO runtime.trade_commands
                   (command_id,command_type,state,trading_account_id,
                    strategy_execution_binding_id,identity_status,payload_json)
                   VALUES('unknown-865','strategy_exit','sending',%s,%s,
                          'PROVEN',%s::jsonb)""",
                (self.main.trading_account_id,
                 self.main.strategy_execution_binding_id,
                 json.dumps({"strategy_position_id":
                             self.main.strategy_position_id})),
            )
        with connect() as c:
            self.assertFalse(p90_real_exit_no_pending(
                c, position=self.main,
                trading_account_id=self.main.trading_account_id,
            ))
            self.assertTrue(p90_real_exit_no_pending(
                c, position=self.sub,
                trading_account_id=self.sub.trading_account_id,
            ))

    def test_account_advisory_xact_lock_serializes_while_sub_is_independent(self):
        with connect(autocommit=True) as a, connect(autocommit=True) as b:
            a.execute("BEGIN")
            try:
                lock_p90_exit_account(a, self.main.trading_account_id)
                b.execute("BEGIN")
                try:
                    b.execute("SET LOCAL lock_timeout='120ms'")
                    with self.assertRaises(psycopg.errors.LockNotAvailable):
                        lock_p90_exit_account(b, self.main.trading_account_id)
                finally:
                    b.execute("ROLLBACK")
                b.execute("BEGIN")
                try:
                    lock_p90_exit_account(b, self.sub.trading_account_id)
                finally:
                    b.execute("COMMIT")
            finally:
                a.execute("COMMIT")
            b.execute("BEGIN")
            try:
                lock_p90_exit_account(b, self.main.trading_account_id)
            finally:
                b.execute("COMMIT")

    def test_source_read_only_repeatable_read_real_publication(self):
        now = datetime.now(UTC)
        with connect(admin=True) as admin:
            publication(admin, now=now)
        with connect(tuples=True, autocommit=False) as source:
            quote, epoch = _source_quote(
                source, symbol="LTCUSDT", observed_at=datetime.now(UTC),
            )
            self.assertEqual(epoch, _EPOCH)
            self.assertEqual(quote.source_session_id, _EPOCH)
            self.assertEqual(str(quote.last_price), "102")
            self.assertEqual(str(quote.bid_price), "101.99")
            self.assertEqual(quote.producer_release_commit, _COMMIT)
            source.rollback()

    def test_bad_publication_fails_closed_no_fabricated_price(self):
        now = datetime.now(UTC)
        with connect(admin=True) as admin:
            publication(admin, now=now)
            admin.execute(
                """UPDATE analytics.public_market_source_seconds
                      SET compact_features=jsonb_set(
                        compact_features,'{ticker_field_times,bid1Price}',
                        to_jsonb((%s::timestamptz)::text))
                    WHERE source_session_id=%s""",
                (now-timedelta(seconds=12), _EPOCH),
            )
        with connect(tuples=True, autocommit=False) as source:
            with self.assertRaises(R10ExecutionPublicationUnavailable):
                read_r10_execution_quote(
                    source, symbol="LTCUSDT", observed_at=datetime.now(UTC),
                )

    def test_extra_active_source_session_blocks_recovery(self):
        now = datetime.now(UTC)
        with connect(admin=True) as admin:
            publication(admin, now=now)
            admin.execute(
                """INSERT INTO analytics.native_public_trade_source_sessions
                   (source_session_id,market_source_id,producer_release_commit,
                    state,subscribed_at)
                   VALUES (%s,%s,%s,'ACTIVE',%s)""",
                ("f"*32, BYBIT_PUBLIC_MARKET_SOURCE_ID, _COMMIT, now),
            )
        with connect(tuples=True, autocommit=False) as source:
            with self.assertRaises(P90PersistentTrailBlocked):
                _source_quote(
                    source, symbol="LTCUSDT", observed_at=datetime.now(UTC),
                )

    def test_runtime_db_role_cannot_mutate_exchange_ownership(self):
        with connect() as c:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute(
                    """UPDATE runtime.position_ownership
                         SET identity_status='UNVERIFIED'
                       WHERE position_id=%s""",
                    (self.main.strategy_position_id,),
                )


if __name__ == "__main__":
    unittest.main()
