"""Disposable PostgreSQL: independent MAIN/SUB safety & sampled position rows."""
from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="requires explicitly disposable trade lifecycle PostgreSQL DB",
)


def test_independent_safety_and_position_projections_survive_same_symbol() -> None:
    assert DSN is not None
    token = uuid4().hex
    a, b = "ta-a-" + token, "ta-b-" + token
    ca, cb = "ac-a-" + token, "ac-b-" + token
    stamp = datetime(2026, 10, 27, 12, tzinfo=UTC)
    with psycopg.connect(DSN) as connection:
        try:
            connection.execute(
                """INSERT INTO identity.venues(venue_id,venue_kind,display_name)
                   VALUES('venue-bybit','CEX','Bybit') ON CONFLICT DO NOTHING"""
            )
            for account, connector, kind in ((a, ca, "MAIN"), (b, cb, "SUB")):
                connection.execute(
                    """INSERT INTO identity.trading_accounts(
                       trading_account_id,venue_id,account_kind,environment,account_type
                    ) VALUES(%s,'venue-bybit',%s,'MAINNET','UNIFIED')""",
                    (account, kind),
                )
                connection.execute(
                    """INSERT INTO identity.account_connectors(
                        account_connector_id,trading_account_id,connector_kind,adapter_id
                    ) VALUES(%s,%s,'CEX_PRIVATE_RUNTIME','BYBIT_V5_PRIVATE')""",
                    (connector, account),
                )
                connection.execute(
                    """INSERT INTO safety.account_exchange_snapshots(
                        trading_account_id,account_connector_id,observed_at,
                        source,evidence_status,total_equity,available_balance,wallet_balance,
                        open_positions,open_orders,protection_checked_count,
                        protection_verified_count
                    ) VALUES(%s,%s,%s,'SIGNED_BYBIT_ACCOUNT_GET','VERIFIED',
                             21,20,21,1,0,1,1)""",
                    (account, connector, stamp),
                )
                connection.execute(
                    """INSERT INTO supervisor.account_position_snapshots(
                        trading_account_id,position_id,symbol,position_idx,side,
                        observed_at,source,completeness,entry_price,mark_price,
                        return_pct,sampled_mfe_pct,sampled_mae_pct
                    ) VALUES(%s,%s,'BTCUSDT',0,'Buy',%s,'ACCOUNT_SCOPED_HOT_MARK',
                             'SAMPLED_PARTIAL',100,101,1,1,0)""",
                    (account, "same-position", stamp),
                )
            rows = connection.execute(
                """SELECT trading_account_id,count(*)
                     FROM safety.account_exchange_snapshots
                    WHERE trading_account_id=ANY(%s)
                    GROUP BY trading_account_id ORDER BY trading_account_id""",
                ([a, b],),
            ).fetchall()
            positions = connection.execute(
                """SELECT trading_account_id,count(*)
                     FROM supervisor.account_position_snapshots
                    WHERE trading_account_id=ANY(%s)
                    GROUP BY trading_account_id ORDER BY trading_account_id""",
                ([a, b],),
            ).fetchall()
            assert rows == [(a, 1), (b, 1)]
            assert positions == [(a, 1), (b, 1)]
        finally:
            connection.rollback()
