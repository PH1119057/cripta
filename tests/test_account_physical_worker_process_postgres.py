"""Two independent CRIPTA private writer processes on disposable R10 PostgreSQL.

This is an offline process/reconnect rehearsal, not a signed Bybit/systemd
restart. No API secrets, sockets to exchanges, real orders or production DB.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PostgreSQL required")

# The child is the real source runtime imported in a separate Python process.
# No application startup or API method is called. Every row gets the exact
# process's independently configured TradingAccount and AccountConnector.
WORKER = """
import os,sys,psycopg
from operations.connectivity import private_runtime as runtime
assert runtime.TRADING_ACCOUNT_ID==sys.argv[1]
assert runtime.ACCOUNT_CONNECTOR_ID==sys.argv[2]
assert os.environ.get('BYBIT_API_KEY') is None
account_id=sys.argv[1]
amount=sys.argv[3]
symbol=sys.argv[4]
order_id=sys.argv[5]
exec_id=sys.argv[6]
with psycopg.connect(os.environ['CRIPTA_TRADE_LIFECYCLE_TEST_DSN']) as db:
    if db.execute('SELECT current_database()').fetchone()[0]!='cripta_integration':
        raise RuntimeError('NOT_DISPOSABLE')
    runtime.upsert_wallet(db,{
        'totalEquity':amount,
        'totalWalletBalance':amount,
        'totalAvailableBalance':amount,
    },1000)
    runtime.upsert_position(db,{
        'symbol':symbol, 'positionIdx':0,'side':'Buy','size':'1',
        'entryPrice':'100','leverage':'1',
    },1000)
    runtime.upsert_order(db,{
        'orderId':order_id,'orderLinkId':'same-provider-link',
        'symbol':symbol,'side':'Buy','orderStatus':'New','qty':'1',
        'price':'100','leavesQty':'1',
    },1000)
    runtime.upsert_exchange_order_history(db,{
        'orderId':order_id,'orderLinkId':'same-provider-link',
        'symbol':symbol,'side':'Buy','orderStatus':'Filled',
    },1000)
    runtime.handle_private(db,{
        'topic':'execution.linear',
        'id':'same-provider-event',
        'creationTime':1000,
        'data':[{
            'execId':exec_id,'orderId':order_id,
            'orderLinkId':'same-provider-link','symbol':symbol,
            'side':'Buy','execQty':'1','execPrice':'100',
            'execFee':'0.01','execTime':1000,
        }],
    })
    db.commit()
print('ACCOUNT_WRITER_COMPLETED')
"""


def test_two_private_worker_processes_share_provider_ids_without_cross_account(
) -> None:
    assert DSN is not None
    name = "multi-proc-" + uuid4().hex[:12]
    identities = (
        (name + "-main", name + "-cm", "MAIN", "100"),
        (name + "-sub", name + "-cs", "SUB", "200"),
    )
    ids = [a[0] for a in identities]
    symbol = "COLLIDEUSDT"
    order = name + "-shared-id"
    execution = name + "-shared-exec"

    with psycopg.connect(DSN) as db:
        if db.execute("SELECT current_database()").fetchone()[0] != "cripta_integration":
            pytest.skip("exact disposable database name required")
        for index, (account, connector, kind, _amount) in enumerate(identities):
            db.execute(
                """INSERT INTO identity.trading_accounts(
                       trading_account_id,venue_id,account_kind,account_type,
                       environment,status,external_account_ref)
                   VALUES (%s,'venue-bybit',%s,'UNIFIED','MAINNET','ACTIVE',%s)""",
                (account, kind, str(700100001 + index)),
            )
            db.execute(
                """INSERT INTO identity.account_connectors(
                       account_connector_id,trading_account_id,connector_kind,
                       adapter_id,credential_ref,status)
                   VALUES (%s,%s,'CEX_PRIVATE_RUNTIME','BYBIT_V5_PRIVATE',
                           'DISPOSABLE_TEST_REFERENCE','ACTIVE')""",
                (connector, account),
            )
    # The initial identity transaction is committed before launching children.
    env = dict(os.environ)
    env.pop("BYBIT_API_KEY", None)
    env.pop("BYBIT_API_SECRET", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        str(p) for p in (
            ROOT, ROOT / "src", ROOT / "operations/connectivity",
            ROOT / "operations/monitoring", ROOT / "research/server/connectivity",
        )
    )
    def launch(account: str, connector: str, amount: str) -> subprocess.Popen[str]:
        scoped_env = dict(env)
        scoped_env["CRIPTA_TRADING_ACCOUNT_ID"] = account
        scoped_env["CRIPTA_ACCOUNT_CONNECTOR_ID"] = connector
        return subprocess.Popen(
            [sys.executable, "-B", "-c", WORKER,
             account, connector, amount, symbol, order, execution],
            cwd=ROOT, env=scoped_env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    processes: list[subprocess.Popen[str]] = []
    try:
        for account, connector, _kind, amount in identities:
            processes.append(launch(account, connector, amount))
        for process in processes:
            out, err = process.communicate(timeout=30)
            assert process.returncode == 0, err[-2500:]
            assert "ACCOUNT_WRITER_COMPLETED" in out
        with psycopg.connect(DSN) as db:
            for table in (
                "runtime.wallet_latest", "runtime.hot_positions",
                "runtime.hot_orders", "runtime.executions",
                "runtime.exchange_order_history",
            ):
                result = db.execute(
                    f"""SELECT trading_account_id,account_connector_id
                          FROM {table}
                         WHERE trading_account_id=ANY(%s)
                         ORDER BY trading_account_id""",
                    (ids,),
                ).fetchall()
                assert result == [(a[0], a[1]) for a in identities], table

        # A new MAIN process simulates worker restart/reconnect; SUB remains
        # independent and all provider-local keys intentionally collide again.
        restart = launch(identities[0][0], identities[0][1], "150")
        processes.append(restart)
        out, err = restart.communicate(timeout=30)
        assert restart.returncode == 0, err[-2500:]
        assert "ACCOUNT_WRITER_COMPLETED" in out
        with psycopg.connect(DSN) as db:
            wallets = db.execute(
                """SELECT trading_account_id,total_equity,account_connector_id
                     FROM runtime.wallet_latest
                    WHERE trading_account_id=ANY(%s)
                    ORDER BY trading_account_id""",
                (ids,),
            ).fetchall()
            assert wallets == [
                (identities[0][0], "150", identities[0][1]),
                (identities[1][0], "200", identities[1][1]),
            ]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
        with psycopg.connect(DSN) as db:
            if db.execute("SELECT current_database()").fetchone()[0] != "cripta_integration":
                raise RuntimeError("CLEANUP_PRODUCTION_PROHIBITED")
            for table in (
                "runtime.private_events", "runtime.exchange_order_history",
                "runtime.executions", "runtime.hot_orders",
                "runtime.hot_positions", "runtime.wallet_latest",
            ):
                db.execute(
                    f"DELETE FROM {table} WHERE trading_account_id=ANY(%s)",
                    (ids,),
                )
            db.execute(
                "DELETE FROM identity.account_connectors WHERE trading_account_id=ANY(%s)",
                (ids,),
            )
            db.execute(
                "DELETE FROM identity.trading_accounts WHERE trading_account_id=ANY(%s)",
                (ids,),
            )
