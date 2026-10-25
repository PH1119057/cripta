"""Disposable PostgreSQL proof of physical account-scoped private keys.

Never runs against production. Must be executed only after applying the exact
versioned cutover migration to the ephemeral cripta_integration database.
"""
from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import UniqueViolation

DSN = os.getenv("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PostgreSQL DSN required")

SCOPES = (
    ("runtime.wallet_latest", "trading_account_id,singleton"),
    ("runtime.hot_positions", "trading_account_id,symbol,position_idx"),
    ("runtime.hot_orders", "trading_account_id,order_id"),
    ("runtime.executions", "trading_account_id,exec_id"),
    ("runtime.exchange_order_history", "trading_account_id,order_id"),
)


def test_duplicate_provider_local_keys_preserve_two_accounts_and_legacy_unknown() -> None:
    assert DSN is not None
    with psycopg.connect(DSN) as c:
        db = c.execute("SELECT current_database()").fetchone()
        if db is None or db[0] != "cripta_integration":
            pytest.skip("prohibited database: requires exact disposable cripta_integration")

        for relation, cols in SCOPES:
            assert c.execute(
                "SELECT count(*) FROM pg_constraint WHERE conrelid=%s::regclass AND contype='p'",
                (relation,),
            ).fetchone()[0] == 0, relation
            index = c.execute(
                """SELECT ix.indisunique,ix.indisvalid,ix.indnullsnotdistinct
                     FROM pg_index ix
                    WHERE ix.indexrelid=(
                        SELECT i.indexrelid FROM pg_index i
                         WHERE i.indrelid=%s::regclass AND i.indisunique
                           AND i.indnullsnotdistinct
                           AND i.indnkeyatts=%s
                         ORDER BY i.indexrelid LIMIT 1)""",
                (relation, len(cols.split(","))),
            ).fetchone()
            assert index == (True, True, True), relation

        prefix = "physical-keys-" + uuid4().hex[:12]
        accounts = [(prefix + "-main", prefix + "-cmain", "MAIN"),
                    (prefix + "-sub", prefix + "-csub", "SUB")]
        symbol = "COLLIDEUSDT"
        order = prefix + "-provider-order"
        execution = prefix + "-provider-exec"
        for ta, ac, kind in accounts:
            c.execute(
                """INSERT INTO identity.trading_accounts (
                    trading_account_id,venue_id,account_kind,account_type,
                    environment,status,external_account_ref)
                   VALUES (%s,'venue-bybit',%s,'UNIFIED','MAINNET','ACTIVE',%s)""",
                (ta,kind,"987654321" if kind=="MAIN" else "987654322"),
            )
            c.execute(
                """INSERT INTO identity.account_connectors (
                    account_connector_id,trading_account_id,connector_kind,
                    adapter_id,credential_ref,status)
                   VALUES (%s,%s,'CEX_PRIVATE_RUNTIME','BYBIT_V5_PRIVATE',
                           'DISPOSABLE_TEST_REFERENCE','ACTIVE')""",
                (ac,ta),
            )
            c.execute(
                """INSERT INTO runtime.wallet_latest (
                    singleton,refreshed_at_epoch_ms,total_equity,wallet_balance,
                    available_balance,payload_json,trading_account_id,
                    account_connector_id,identity_status)
                   VALUES (1,1000,%s,%s,%s,'{}',%s,%s,'PROVEN')
                   ON CONFLICT (trading_account_id,singleton)
                   DO UPDATE SET total_equity=EXCLUDED.total_equity""",
                ("100" if kind=="MAIN" else "200","100","100",ta,ac),
            )
            c.execute(
                """INSERT INTO runtime.hot_positions (
                    symbol,position_idx,side,size,entry_price,leverage,
                    refreshed_at_epoch_ms,payload_json,trading_account_id,
                    account_connector_id,identity_status)
                   VALUES (%s,0,'Buy','1','100','1',1000,'{}',%s,%s,'PROVEN')
                   ON CONFLICT (trading_account_id,symbol,position_idx)
                   DO UPDATE SET size=EXCLUDED.size""",
                (symbol,ta,ac),
            )
            c.execute(
                """INSERT INTO runtime.hot_orders (
                    order_id,order_link_id,symbol,side,order_status,qty,
                    price,leaves_qty,refreshed_at_epoch_ms,payload_json,
                    trading_account_id,account_connector_id,identity_status)
                   VALUES (%s,'local',%s,'Buy','New','1','100','1',1000,
                           '{}'::jsonb,%s,%s,'PROVEN')
                   ON CONFLICT (trading_account_id,order_id)
                   DO UPDATE SET order_status=EXCLUDED.order_status""",
                (order,symbol,ta,ac),
            )
            c.execute(
                """INSERT INTO runtime.executions (
                    exec_id,order_id,order_link_id,symbol,side,
                    exec_qty,exec_price,exec_fee,received_at_epoch_ms,
                    payload_json,trading_account_id,account_connector_id,
                    identity_status)
                   VALUES (%s,%s,'local',%s,'Buy','1','100','0.01',
                           1000,'{}',%s,%s,'PROVEN')
                   ON CONFLICT (trading_account_id,exec_id)
                   DO UPDATE SET exec_fee=EXCLUDED.exec_fee""",
                (execution,order,symbol,ta,ac),
            )
            c.execute(
                """INSERT INTO runtime.exchange_order_history (
                    order_id,order_link_id,symbol,side,order_status,
                    refreshed_at_epoch_ms,payload_json,
                    trading_account_id,account_connector_id,identity_status)
                   VALUES (%s,'local',%s,'Buy','Filled',1000,
                           '{}'::jsonb,%s,%s,'PROVEN')
                   ON CONFLICT (trading_account_id,order_id)
                   DO UPDATE SET order_status=EXCLUDED.order_status""",
                (order,symbol,ta,ac),
            )

        # Each physical/provider-local ID is shared while each account retains
        # its independent authoritative rows and wallet equity.
        for relation,_ in SCOPES:
            rows = c.execute(
                "SELECT trading_account_id,count(*) FROM "+relation+
                " WHERE trading_account_id=ANY(%s) GROUP BY trading_account_id",
                ([accounts[0][0],accounts[1][0]],),
            ).fetchall()
            assert sorted(rows) == [(accounts[0][0],1),(accounts[1][0],1)], relation
        values = c.execute(
            "SELECT trading_account_id,total_equity FROM runtime.wallet_latest WHERE trading_account_id=ANY(%s)",
            ([accounts[0][0],accounts[1][0]],),
        ).fetchall()
        assert sorted(values) == [(accounts[0][0],"100"),(accounts[1][0],"200")]

        # Explicit LEGACY_UNKNOWN remains one unassigned physical identity.
        # This does not attribute historical records to a guessed account.
        legacy_order = prefix + "-legacy"
        c.execute(
            """INSERT INTO runtime.hot_orders (
                order_id,order_link_id,symbol,side,order_status,qty,price,
                leaves_qty,refreshed_at_epoch_ms,payload_json,identity_status)
               VALUES (%s,'',%s,'Buy','New','1','100','1',1000,
                       '{}'::jsonb,'LEGACY_UNKNOWN')""",
            (legacy_order,symbol),
        )
        with pytest.raises(UniqueViolation):
            with c.transaction():
                c.execute(
                    """INSERT INTO runtime.hot_orders (
                        order_id,order_link_id,symbol,side,order_status,
                        qty,price,leaves_qty,refreshed_at_epoch_ms,
                        payload_json,identity_status)
                       VALUES (%s,'',%s,'Buy','New','1','100','1',1000,
                               '{}'::jsonb,'LEGACY_UNKNOWN')""",
                    (legacy_order,symbol),
                )
        assert c.execute(
            "SELECT count(*) FROM runtime.hot_orders WHERE order_id=%s AND identity_status='LEGACY_UNKNOWN'",
            (legacy_order,),
        ).fetchone()[0] == 1
        c.rollback()
