"""Disposable PostgreSQL proof of physical account-scoped private keys.

Never runs against production. Must be executed only after applying the exact
versioned cutover migration to the ephemeral cripta_integration database.
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import UniqueViolation

from operations.dashboard import archive_v2

DSN = os.getenv("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PostgreSQL DSN required")

SCOPES = (
    ("runtime.wallet_latest", "trading_account_id,singleton"),
    ("runtime.hot_positions", "trading_account_id,symbol,position_idx"),
    ("runtime.hot_orders", "trading_account_id,order_id"),
    ("runtime.executions", "trading_account_id,exec_id"),
    ("runtime.exchange_order_history", "trading_account_id,order_id"),
)


def test_duplicate_provider_local_keys_preserve_two_accounts_and_legacy_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            """SELECT trading_account_id,total_equity
                 FROM runtime.wallet_latest WHERE trading_account_id=ANY(%s)""",
            ([accounts[0][0],accounts[1][0]],),
        ).fetchall()
        assert sorted(values) == [(accounts[0][0],"100"),(accounts[1][0],"200")]

        # Two CEX accounts may reuse the same exchange orderLinkId and execId.
        # The causal correlator must only count fills for the same proven account.
        matched = c.execute(
            """WITH command_scope(trading_account_id,order_link_id) AS (
                   VALUES (%s::text,'local'),(%s::text,'local')
               )
               SELECT cmd.trading_account_id,count(e.exec_id)
                 FROM command_scope cmd LEFT JOIN runtime.executions e
                   ON e.order_link_id=cmd.order_link_id
                  AND e.trading_account_id=cmd.trading_account_id
                  AND e.identity_status='PROVEN'
                GROUP BY cmd.trading_account_id ORDER BY cmd.trading_account_id""",
            (accounts[0][0], accounts[1][0]),
        ).fetchall()
        assert matched == [(accounts[0][0], 1), (accounts[1][0], 1)]

        # Exercise the *real* M3 analyst's proven account reader predicate on
        # colliding exchange exec_id. This is the report's identity selector,
        # not a copied hand-authored SQL scope. No report tables are written.
        from operations.monitoring import m3_trade_analyst

        for trading_account_id, _connector, _kind in accounts:
            status, account = m3_trade_analyst.private_identity(
                {"identity_status": "PROVEN", "trading_account_id": trading_account_id}
            )
            scope, params = m3_trade_analyst.identity_predicate(
                "x", status, account
            )
            report_fills = c.execute(
                f"""SELECT x.trading_account_id,x.exec_id
                    FROM runtime.executions x
                   WHERE {scope} AND x.exec_id=%s""",
                (*params, execution),
            ).fetchall()
            assert report_fills == [(trading_account_id, execution)]
        with pytest.raises(RuntimeError, match="requires PROVEN"):
            m3_trade_analyst.private_identity(
                {"identity_status": "LEGACY_UNKNOWN", "trading_account_id": None}
            )
        with pytest.raises(RuntimeError, match="requires PROVEN"):
            m3_trade_analyst.identity_predicate("x", "LEGACY_UNKNOWN", None)

        # An unscoped legacy Supervisor comparison must not join MAIN's
        # position to SUB's same symbol and position index.
        scoped_hot = c.execute(
            """SELECT p.trading_account_id,count(h.trading_account_id)
                 FROM (VALUES (%s::text),(%s::text)) p(trading_account_id)
                 LEFT JOIN runtime.hot_positions h
                   ON h.symbol=%s AND h.position_idx=0
                  AND h.trading_account_id IS NOT DISTINCT FROM p.trading_account_id
                  AND h.identity_status='PROVEN'
                GROUP BY p.trading_account_id ORDER BY p.trading_account_id""",
            (accounts[0][0], accounts[1][0], symbol),
        ).fetchall()
        assert scoped_hot == [(accounts[0][0], 1), (accounts[1][0], 1)]

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
        with pytest.raises(UniqueViolation), c.transaction():
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
            """SELECT count(*) FROM runtime.hot_orders
                WHERE order_id=%s AND identity_status='LEGACY_UNKNOWN'""",
            (legacy_order,),
        ).fetchone()[0] == 1
        # Exercise the real archive identity validator on PostgreSQL's
        # canonical to_jsonb(t) export representation. Same provider orderId
        # must remain distinct across proven accounts; legacy stays unassigned.
        exported = c.execute(
            """SELECT to_jsonb(t) FROM runtime.hot_orders t
               WHERE trading_account_id=ANY(%s)
                  OR (identity_status='LEGACY_UNKNOWN' AND order_id=%s)
               ORDER BY identity_status,trading_account_id NULLS FIRST""",
            ([accounts[0][0], accounts[1][0]], legacy_order),
        ).fetchall()
        archive_rows = [
            json.loads(json.dumps(row[0], default=str)) for row in exported
        ]
        assert len(archive_rows) == 3
        scope_keys = {
            archive_v2._validate_account_identity(row)
            for row in archive_rows
        }
        assert scope_keys == {
            ("PROVEN", accounts[0][0]),
            ("PROVEN", accounts[1][0]),
            ("LEGACY_UNKNOWN", None),
        }
        account_rows = [
            row for row in archive_rows if row["identity_status"] == "PROVEN"
        ]
        assert len({row["order_id"] for row in account_rows}) == 1
        assert account_rows[0]["order_id"] == order
        assert len({row["trading_account_id"] for row in account_rows}) == 2
        legacy_rows = [
            row for row in archive_rows
            if row["identity_status"] == "LEGACY_UNKNOWN"
        ]
        assert len(legacy_rows) == 1
        assert legacy_rows[0]["trading_account_id"] is None
        assert legacy_rows[0]["order_id"] == legacy_order

        # Run the actual archive_v2._statistics writer on this same disposable
        # PostgreSQL transaction; no secondary production DB connection.
        class _BorrowedConnection:
            def __enter__(self) -> psycopg.Connection:
                return c

            def __exit__(self, *_args: object) -> None:
                return None

        target = tmp_path / "account_private_export.zip"
        with monkeypatch.context() as patch:
            patch.setattr(
                archive_v2, "TABLE_EXPORTS",
                tuple(
                    spec for spec in archive_v2.TABLE_EXPORTS
                    if spec.table == "runtime.hot_orders"
                ),
            )
            patch.setattr(
                psycopg, "connect",
                lambda *_args, **_kwargs: _BorrowedConnection(),
            )
            export = archive_v2._statistics(
                target, datetime(2100, 1, 1, tzinfo=UTC), None
            )
        assert export["tables"] == 1
        with zipfile.ZipFile(target) as bundle:
            jsonl = bundle.read("execution/hot_orders.jsonl")
            manifest = json.loads(bundle.read("STATISTICS_MANIFEST.json"))
        assert len(manifest["tables"]) == 1
        table_meta = manifest["tables"][0]
        assert table_meta["table"] == "runtime.hot_orders"
        assert table_meta["sha256"] == hashlib.sha256(jsonl).hexdigest()
        assert table_meta["identity_provenance"]["scope"] == "TRADING_ACCOUNT"
        archived = [json.loads(line) for line in jsonl.splitlines()]
        relevant = [
            row for row in archived
            if row["order_id"] in (order, legacy_order)
        ]
        assert len(relevant) == 3
        assert {
            archive_v2._validate_account_identity(row) for row in relevant
        } == scope_keys
        assert {
            row["trading_account_id"]
            for row in relevant if row["identity_status"] == "PROVEN"
        } == {accounts[0][0], accounts[1][0]}
        assert len([
            row for row in relevant
            if row["identity_status"] == "LEGACY_UNKNOWN"
            and row["trading_account_id"] is None
        ]) == 1
        c.rollback()


def test_account_wallet_state_survives_new_database_session_without_cross_update() -> None:
    """Simulate persisted private state re-opened after a worker restart.

    This is not an actual systemd/Bybit restart, which must be verified
    separately before the real two-account release can be called ready.
    """
    assert DSN is not None
    p = uuid4().hex[:12]
    accounts = [
        (f"restart-{p}-main", f"restart-{p}-cm", "MAIN", str(int(p, 16))),
        (f"restart-{p}-sub", f"restart-{p}-cs", "SUB", str(int(p, 16) + 1)),
    ]
    ids = [entry[0] for entry in accounts]
    try:
        with psycopg.connect(DSN) as c:
            dbname = c.execute("SELECT current_database()").fetchone()[0]
            if dbname != "cripta_integration":
                pytest.skip("requires only disposable cripta_integration")
            for account_id, connector_id, kind, provider_uid in accounts:
                c.execute(
                    """INSERT INTO identity.trading_accounts(
                           trading_account_id,venue_id,account_kind,account_type,
                           environment,status,external_account_ref)
                       VALUES (%s,'venue-bybit',%s,'UNIFIED','MAINNET','ACTIVE',%s)""",
                    (account_id, kind, provider_uid),
                )
                c.execute(
                    """INSERT INTO identity.account_connectors(
                           account_connector_id,trading_account_id,connector_kind,
                           adapter_id,credential_ref,status)
                       VALUES (%s,%s,'CEX_PRIVATE_RUNTIME','BYBIT_V5_PRIVATE',
                               'DISPOSABLE_TEST_REFERENCE','ACTIVE')""",
                    (connector_id, account_id),
                )
                amount = "100" if kind == "MAIN" else "200"
                c.execute(
                    """INSERT INTO runtime.wallet_latest(
                           singleton,refreshed_at_epoch_ms,total_equity,
                           wallet_balance,available_balance,payload_json,
                           trading_account_id,account_connector_id,identity_status)
                       VALUES (1,1000,%s,%s,%s,'{}',%s,%s,'PROVEN')
                       ON CONFLICT(trading_account_id,singleton) DO UPDATE
                         SET total_equity=EXCLUDED.total_equity""",
                    (amount, amount, amount, account_id, connector_id),
                )
        # The initial connection has exited and committed; this is a new DB
        # session, as after worker restart. Reconcile MAIN without modifying SUB.
        with psycopg.connect(DSN) as c:
            before = c.execute(
                """SELECT trading_account_id,total_equity,account_connector_id
                     FROM runtime.wallet_latest WHERE trading_account_id=ANY(%s)
                     ORDER BY trading_account_id""",
                (ids,),
            ).fetchall()
            assert len(before) == 2
            c.execute(
                """INSERT INTO runtime.wallet_latest(
                       singleton,refreshed_at_epoch_ms,total_equity,
                       wallet_balance,available_balance,payload_json,
                       trading_account_id,account_connector_id,identity_status)
                   VALUES (1,2000,'150','150','150','{}',%s,%s,'PROVEN')
                   ON CONFLICT(trading_account_id,singleton) DO UPDATE SET
                     refreshed_at_epoch_ms=EXCLUDED.refreshed_at_epoch_ms,
                     total_equity=EXCLUDED.total_equity,
                     wallet_balance=EXCLUDED.wallet_balance,
                     available_balance=EXCLUDED.available_balance,
                     account_connector_id=EXCLUDED.account_connector_id,
                     identity_status='PROVEN'""",
                (accounts[0][0], accounts[0][1]),
            )
            after = c.execute(
                """SELECT trading_account_id,total_equity,account_connector_id
                     FROM runtime.wallet_latest WHERE trading_account_id=ANY(%s)
                     ORDER BY trading_account_id""",
                (ids,),
            ).fetchall()
            assert after == [
                (accounts[0][0], "150", accounts[0][1]),
                (accounts[1][0], "200", accounts[1][1]),
            ]
    finally:
        # Never touch any other account. This block runs only in the named
        # disposable database even if an assertion above fails.
        if DSN is not None:
            with psycopg.connect(DSN) as c:
                if c.execute("SELECT current_database()").fetchone()[0] != "cripta_integration":
                    raise RuntimeError("CLEANUP_TARGET_NOT_DISPOSABLE")
                c.execute(
                    "DELETE FROM runtime.wallet_latest WHERE trading_account_id=ANY(%s)",
                    (ids,),
                )
                c.execute(
                    "DELETE FROM identity.account_connectors WHERE trading_account_id=ANY(%s)",
                    (ids,),
                )
                c.execute(
                    "DELETE FROM identity.trading_accounts WHERE trading_account_id=ANY(%s)",
                    (ids,),
                )


def test_actual_private_runtime_writers_keep_two_accounts_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use production writer functions against disposable R10 schema.

    No credentials/network access: the isolated test only invokes three
    SQL-writing functions and rolls back its synthetic TradingAccounts.
    """
    assert DSN is not None
    from importlib import import_module

    root = Path(__file__).resolve().parents[1]
    for folder in (
        root,
        root / "src",
        root / "operations/connectivity",
        root / "operations/monitoring",
        root / "research/server/connectivity",
    ):
        monkeypatch.syspath_prepend(str(folder))
    writer = import_module("operations.connectivity.private_runtime")
    prefix = "actual-writer-" + uuid4().hex[:11]
    accounts = [
        (prefix + "-main", prefix + "-cmain", "MAIN"),
        (prefix + "-sub", prefix + "-csub", "SUB"),
    ]
    symbol = "CROSSUSDT"
    order_id = prefix + "-shared-provider-order"

    with psycopg.connect(DSN) as c:
        if c.execute("SELECT current_database()").fetchone()[0] != "cripta_integration":
            pytest.skip("production database prohibited")
        for n, (trading_account_id, connector_id, kind) in enumerate(accounts):
            c.execute(
                """INSERT INTO identity.trading_accounts(
                       trading_account_id,venue_id,account_kind,account_type,
                       environment,status,external_account_ref)
                   VALUES (%s,'venue-bybit',%s,'UNIFIED','MAINNET','ACTIVE',%s)""",
                (trading_account_id, kind, str(700000000 + n)),
            )
            c.execute(
                """INSERT INTO identity.account_connectors(
                       account_connector_id,trading_account_id,connector_kind,
                       adapter_id,credential_ref,status)
                   VALUES (%s,%s,'CEX_PRIVATE_RUNTIME','BYBIT_V5_PRIVATE',
                           'DISPOSABLE_TEST_REFERENCE','ACTIVE')""",
                (connector_id, trading_account_id),
            )
            monkeypatch.setattr(writer, "TRADING_ACCOUNT_ID", trading_account_id)
            monkeypatch.setattr(writer, "ACCOUNT_CONNECTOR_ID", connector_id)
            amount = "100" if kind == "MAIN" else "200"
            writer.upsert_wallet(
                c,
                {
                    "totalEquity": amount,
                    "totalWalletBalance": amount,
                    "totalAvailableBalance": amount,
                },
                1000,
            )
            writer.upsert_position(
                c,
                {
                    "symbol": symbol,
                    "positionIdx": 0,
                    "side": "Buy",
                    "size": "1",
                    "entryPrice": "100",
                    "leverage": "1",
                },
                1000,
            )
            writer.upsert_order(
                c,
                {
                    "orderId": order_id,
                    "orderLinkId": "shared-local",
                    "symbol": symbol,
                    "side": "Buy",
                    "orderStatus": "New",
                    "qty": "1",
                    "price": "100",
                    "leavesQty": "1",
                },
                1000,
            )
            writer.upsert_exchange_order_history(
                c,
                {
                    "orderId": order_id,
                    "orderLinkId": "shared-local",
                    "symbol": symbol,
                    "side": "Buy",
                    "orderStatus": "Filled",
                },
                1000,
            )

            class _NoCommitConnection:
                # Keep the real execution handler's SQL inside this test's
                # explicit rollback transaction, without changing its code.
                def execute(self, sql: str, params: object = None) -> object:
                    return c.execute(sql, params)

                def commit(self) -> None:
                    return None

            writer.handle_private(
                _NoCommitConnection(),
                {
                    "topic": "execution.linear",
                    "id": prefix + "-shared-event",
                    "creationTime": 1000,
                    "data": [{
                        "execId": prefix + "-shared-exec",
                        "orderId": order_id,
                        "orderLinkId": "shared-local",
                        "symbol": symbol,
                        "side": "Buy",
                        "execQty": "1",
                        "execPrice": "100",
                        "execFee": "0.1",
                        "execTime": 1000,
                    }],
                },
            )
        for relation in (
            "runtime.wallet_latest",
            "runtime.hot_positions",
            "runtime.hot_orders",
            "runtime.exchange_order_history",
            "runtime.executions",
        ):
            found = c.execute(
                f"""SELECT trading_account_id,account_connector_id
                      FROM {relation} WHERE trading_account_id=ANY(%s)
                      ORDER BY trading_account_id""",
                ([x[0] for x in accounts],),
            ).fetchall()
            assert found == [(a[0], a[1]) for a in accounts], relation

        # Reconcile one account again through its actual runtime writer.
        monkeypatch.setattr(writer, "TRADING_ACCOUNT_ID", accounts[0][0])
        monkeypatch.setattr(writer, "ACCOUNT_CONNECTOR_ID", accounts[0][1])
        writer.upsert_wallet(
            c,
            {
                "totalEquity": "150",
                "totalWalletBalance": "150",
                "totalAvailableBalance": "150",
            },
            2000,
        )
        values = c.execute(
            """SELECT trading_account_id,total_equity
                 FROM runtime.wallet_latest WHERE trading_account_id=ANY(%s)
                 ORDER BY trading_account_id""",
            ([x[0] for x in accounts],),
        ).fetchall()
        assert values == [(accounts[0][0], "150"), (accounts[1][0], "200")]
        c.rollback()
