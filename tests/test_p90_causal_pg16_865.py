"""#865 C2: real PostgreSQL16 two-connection P90 source/Exit ownership tests.

Runs ONLY on a disposable independent PG16 cluster bound to a private Unix
socket under the dedicated CRIPTA testenv. No production database or Bybit.
The exact candidate runner must start and independently prove that cluster.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

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
from bybit_workbench.universal_exit.contracts import ExitActionKind, ExitEvaluationStatus
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.p90l90_exchange_safety import (
    verify_p90_real_exchange_mutation,
)
from bybit_workbench.universal_exit.p90l90_real_exit_execution import (
    lock_p90_exit_account,
)
from bybit_workbench.universal_exit.p90l90_real_pending import (
    p90_real_exit_no_pending,
)
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateIpcUnverified,
    P90PrivateProofClient,
    serve_private_proof,
)
from bybit_workbench.universal_exit.p90l90_real_trail_reconcile import (
    build_p90_trail_recovery_observation,
)
from bybit_workbench.universal_exit.p90l90_real_v3 import P90_TRAIL_RECOVERY_POLICY
from operations.connectivity.p90_real_persistent_trail import (
    P90PersistentTrailBlocked,
    _candidate,
    _source_quote,
)

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
    from test_p90_causal_release_preflight_865 import _case

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



def _fake_account_private_worker(account, connector, evidence, pipe, bad_binding):
    """One real separate Unix process, only synthetic approved test facts."""
    seen = [False]

    def handler(_request):
        seen[0] = True
        return replace(
            evidence, strategy_execution_binding_id=(
                "invalid-binding" if bad_binding
                else evidence.strategy_execution_binding_id
            ),
        )

    try:
        serve_private_proof(
            account=account, connector=connector,
            handler=handler, running=lambda: not seen[0],
            ready=lambda: pipe.send("READY"),
        )
    finally:
        pipe.close()


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
                               '{"trailing_recovery_policy":"P90_TRAIL035_PERSISTENT_V1"}'::jsonb)""",
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

    def test_source_writer_dml_grants_match_canonical_sql_subset(self):
        # Positive subset from 20261108 native sessions and 20261110
        # MarketPublication / compact seconds; never production ACL evidence.
        grants = (
            ("analytics.native_public_trade_source_sessions", "SELECT", True),
            ("analytics.native_public_trade_source_sessions", "INSERT", True),
            ("analytics.native_public_trade_source_sessions", "UPDATE", True),
            ("analytics.native_public_trade_source_sessions", "DELETE", False),
            ("analytics.market_publications", "SELECT", True),
            ("analytics.market_publications", "INSERT", True),
            ("analytics.market_publications", "UPDATE", False),
            ("analytics.market_publications", "DELETE", False),
            ("analytics.public_market_source_seconds", "SELECT", True),
            ("analytics.public_market_source_seconds", "INSERT", True),
            ("analytics.public_market_source_seconds", "UPDATE", False),
            ("strategy_exit.exit_decisions", "SELECT", True),
            ("strategy_exit.exit_decisions", "INSERT", True),
            ("strategy_exit.exit_decisions", "UPDATE", False),
        )
        with connect() as c:
            for table, privilege, expected in grants:
                with self.subTest(table=table, privilege=privilege):
                    result = c.execute(
                        "SELECT has_table_privilege(current_user,%s,%s) AS allowed",
                        (table, privilege),
                    ).fetchone()["allowed"]
                    self.assertIs(result, expected)
            # Runtime consumer/producer DML is intentionally not SELECT-only.
            ts = datetime.now(UTC)
            c.execute(
                """INSERT INTO analytics.native_public_trade_source_sessions
                   (source_session_id,market_source_id,producer_release_commit,
                    state,subscribed_at)
                   VALUES (%s,%s,%s,'ACTIVE',%s)""",
                ("c" * 32, BYBIT_PUBLIC_MARKET_SOURCE_ID, _COMMIT, ts),
            )
            c.execute(
                """UPDATE analytics.native_public_trade_source_sessions
                      SET state='INACTIVE'
                    WHERE source_session_id=%s""",
                ("c" * 32,),
            )
            c.execute(
                """INSERT INTO analytics.market_publications
                   (publication_id,market_source_id,source_session_id,
                    producer_release_commit,as_of_second,publication_status,
                    closed_instrument_refs,closed_instrument_coverage,
                    compact_second_refs,compact_field_coverage)
                   VALUES (867,%s,%s,%s,%s,'WATERMARKS_ONLY',
                           '{}'::jsonb,'{}'::jsonb,'{}'::jsonb,'{}'::jsonb)""",
                (
                    BYBIT_PUBLIC_MARKET_SOURCE_ID, "c" * 32, _COMMIT, ts,
                ),
            )
            self.assertEqual(
                c.execute(
                    """SELECT state
                         FROM analytics.native_public_trade_source_sessions
                        WHERE source_session_id=%s""",
                    ("c" * 32,),
                ).fetchone()["state"],
                "INACTIVE",
            )

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
                source, symbol="LTCUSDT", observed_at=now,
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
        with connect(tuples=True, autocommit=False) as source, source.transaction():
            source.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            with self.assertRaisesRegex(
                R10ExecutionPublicationUnavailable,
                "R10_EXECUTION_TICKER_FIELD_TTL_EXPIRED",
            ):
                read_r10_execution_quote(
                    source, symbol="LTCUSDT",
                    observed_at=now,
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
        with connect(tuples=True, autocommit=False) as source, self.assertRaises(
            P90PersistentTrailBlocked
        ):
            _source_quote(
                source, symbol="LTCUSDT", observed_at=now,
            )

    def test_reader_only_fixture_cannot_mutate_exchange_ownership(self):
        with connect() as c, self.assertRaises(
            psycopg.errors.InsufficientPrivilege
        ):
            c.execute(
                """UPDATE runtime.position_ownership
                     SET identity_status='UNVERIFIED'
                   WHERE position_id=%s""",
                (self.main.strategy_position_id,),
            )


    def _exchange_ipc_roundtrip(self, *, bad_binding: bool):
        from test_p90_causal_release_preflight_865 import _case

        position, plan, private, _, _ = _case(short=False)
        account = "ta-" + uuid4().hex
        connector = "connector-c2-synthetic"
        position = replace(
            position, strategy_position_id="position-c2-" + uuid4().hex,
            trading_account_id=account,
            exchange_position_key=exchange_position_key(
                account, position.symbol, 0
            ),
        )
        simulated_at = datetime.now(UTC)
        private = replace(
            private, trading_account_id=account,
            exchange_position_key=position.exchange_position_key,
            connector_id=connector, generation_id="synthetic-c2-generation",
            observed_at=simulated_at,
        )
        # Durable account/position and source facts from independent PG16,
        # not the live Bybit private worker or production database.
        with connect(admin=True) as admin:
            admin.execute(
                """INSERT INTO runtime.position_ownership
                   (position_id,trading_account_id,symbol,state,
                    strategy_id,strategy_version,identity_status,
                    strategy_execution_binding_id,exchange_position_key,
                    position_idx,initial_protection_confirmed_at,
                    initial_protection_evidence)
                   VALUES (%s,%s,%s,'OPEN',%s,%s,'PROVEN',%s,%s,0,%s,
                           %s::jsonb)""",
                (position.strategy_position_id, account, position.symbol,
                 position.strategy_id, position.strategy_version,
                 position.strategy_execution_binding_id,
                 position.exchange_position_key,
                 position.initial_protection_confirmed_at,
                 json.dumps({
                     "trailing_recovery_policy": P90_TRAIL_RECOVERY_POLICY,
                 })),
            )
        with connect() as runtime:
            self.assertTrue(p90_real_exit_no_pending(
                runtime, position=position, trading_account_id=account,
            ))

        ctx = multiprocessing.get_context("fork")
        reader, writer = ctx.Pipe(duplex=False)
        process = ctx.Process(
            target=_fake_account_private_worker,
            args=(account, connector, private, writer, bad_binding),
            daemon=True,
        )
        process.start()
        try:
            self.assertTrue(reader.poll(4), "C2 private IPC listener not ready")
            self.assertEqual(reader.recv(), "READY")
            client = P90PrivateProofClient(
                account=account, connector=connector, timeout_seconds=3,
            )
            if bad_binding:
                with self.assertRaises(P90PrivateIpcUnverified):
                    client.for_restart_recovery(
                        position, at=simulated_at,
                    )
                return
            signed = client.for_restart_recovery(
                position, at=simulated_at,
            )
            self.assertEqual(signed.trading_account_id, account)
            self.assertEqual(
                signed.strategy_execution_binding_id,
                position.strategy_execution_binding_id,
            )
            self.assertFalse(signed.pending_exit_mutation)
            with connect(admin=True) as admin:
                publication(admin, now=simulated_at)
            with connect(tuples=True, autocommit=False) as source:
                quote, session = _source_quote(
                    source, symbol=position.symbol,
                    observed_at=simulated_at,
                )
                source.rollback()
            observation = build_p90_trail_recovery_observation(
                position=position, private=signed, quote=quote,
                eligibility=P90_TRAIL_RECOVERY_POLICY,
                expected_source_session_id=session,
                received_at=simulated_at,
            )
            evaluated = UniversalExitEngine().evaluate(
                position, plan, observation,
            )
            self.assertIs(
                evaluated.status, ExitEvaluationStatus.DECISION_CREATED,
            )
            decision = evaluated.decision
            self.assertIsNotNone(decision)
            self.assertIs(decision.action_kind, ExitActionKind.SET_TRAILING)
            mutation = decision.requested_mutation.to_dict()
            verify_p90_real_exchange_mutation(
                payload={
                    "strategy_id": position.strategy_id,
                    "strategy_version": position.strategy_version,
                    "trading_account_id": account,
                    "strategy_execution_binding_id":
                        position.strategy_execution_binding_id,
                    "identity_status": "PROVEN",
                    "direction": position.direction.value,
                    "exchange_position_key": position.exchange_position_key,
                    "trail_recovery_policy": P90_TRAIL_RECOVERY_POLICY,
                },
                trading_account_id=account, symbol=position.symbol,
                position=dict(signed.position),
                action_kind=decision.action_kind,
                mutation=mutation, tick=signed.tick_size,
            )
            self.assertEqual(mutation["distance"], "0.35")
            # Real interprocess transport and real PG, yet synthetic "signed"
            # facts: NEVER call this proof of Bybit POST/readback.
            with connect() as runtime:
                self.assertEqual(
                    runtime.execute(
                        "SELECT count(*) AS n FROM runtime.trade_commands"
                    ).fetchone()["n"], 0,
                )
        finally:
            reader.close()
            writer.close()
            process.join(4)
            if process.is_alive():
                process.terminate()
                process.join(3)
            self.assertEqual(process.exitcode, 0)

    def test_real_unix_ipc_process_and_pg16_to_exit_engine(self):
        self._exchange_ipc_roundtrip(bad_binding=False)

    def test_real_unix_ipc_rejects_bad_binding_before_exit(self):
        self._exchange_ipc_roundtrip(bad_binding=True)



if __name__ == "__main__":
    unittest.main()
