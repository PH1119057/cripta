"""Disposable PostgreSQL proof for account-scoped owner arm (R04).

Never run this fixture against production or an arbitrary configured database.
SQL migration and OWNER ARM rows stay confined to cripta_integration CI database.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.account_entry_safety import (
    account_entry_safety_ready,
    latch_account_entry_safety,
)
from bybit_workbench.account_live_arm import account_live_arm_ready

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PostgreSQL DSN required")
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
RELEASE = "a" * 40


@pytest.fixture
def disposable_db() -> psycopg.Connection:
    if not DSN:
        pytest.skip("No disposable PostgreSQL DSN")
    connection = psycopg.connect(DSN, row_factory=dict_row, autocommit=False)
    try:
        name = connection.execute("SELECT current_database() AS name").fetchone()
        if name is None or name["name"] != "cripta_integration":
            pytest.skip("R04 test is permitted only on cripta_integration")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _seed(disposable_db: psycopg.Connection) -> dict[str, str]:
    prefix = f"test-arm-{uuid4().hex[:12]}"
    strategy = f"{prefix}-strategy"
    activation = f"{prefix}-activation"
    fp = f"{prefix}-fingerprint"
    disposable_db.execute(
        """INSERT INTO strategy_entry.strategy_cards(
               strategy_id,strategy_version,strategy_config_fingerprint,
               name,description,card_json,approved_at,approved_source
           ) VALUES(%s,'1',%s,'R04 test','disposable','{}'::jsonb,%s,'disposable')""",
        (strategy, fp, NOW - timedelta(minutes=5)),
    )
    disposable_db.execute(
        """INSERT INTO strategy_entry.strategy_activations(
               activation_id,strategy_id,strategy_version,
               strategy_config_fingerprint,enabled,enabled_at,
               scope,operator,source,change_reason
           ) VALUES(%s,%s,'1',%s,true,%s,'{}'::jsonb,'test','test','')""",
        (activation, strategy, fp, NOW - timedelta(minutes=5)),
    )
    result = {
        "activation": activation, "strategy": strategy, "fingerprint": fp
    }
    for number, account_kind in ((1, "MAIN"), (2, "SUB")):
        account = f"{prefix}-account-{number}"
        connector = f"{prefix}-connector-{number}"
        binding = f"{prefix}-binding-{number}"
        uid = str(int.from_bytes(hashlib.sha256(account.encode()).digest()[:7], "big"))
        parent = result.get("account-1") if account_kind == "SUB" else None
        disposable_db.execute(
            """INSERT INTO identity.trading_accounts(
                   trading_account_id,venue_id,account_kind,account_type,
                   environment,status,external_account_ref,parent_trading_account_id
               ) VALUES(%s,'venue-bybit',%s,'UNIFIED','MAINNET','ACTIVE',%s,%s)""",
            (account, account_kind, uid, parent),
        )
        disposable_db.execute(
            """INSERT INTO identity.account_connectors(
                   account_connector_id,trading_account_id,connector_kind,
                   adapter_id,credential_ref,status
               ) VALUES(%s,%s,'CEX_PRIVATE_RUNTIME','BYBIT_V5_PRIVATE',
                        'DISPOSABLE_TEST_CREDENTIAL_REFERENCE','ACTIVE')""",
            (connector, account),
        )
        disposable_db.execute(
            """INSERT INTO strategy_entry.strategy_execution_bindings(
                   strategy_execution_binding_id,strategy_activation_id,
                   execution_leg,execution_role,trading_account_id,
                   allocation_mode,binding_state,effective_from,
                   operator,source,reason
               ) VALUES(%s,%s,%s,'PRIMARY',%s,'DEDICATED_STRATEGY',
                        'ACTIVE',%s,'disposable','test','R04 isolated test')""",
            (binding, activation, f"LEG_{number}", account, NOW - timedelta(minutes=5)),
        )
        result[f"account-{number}"] = account
        result[f"connector-{number}"] = connector
        result[f"binding-{number}"] = binding
    return result


def _arm(
    db: psycopg.Connection, ids: dict[str, str], number: int,
    *, state: str = "ACTIVE", symbol: str = "BTCUSDT",
) -> None:
    db.execute(
        """INSERT INTO control.account_live_arm_sessions(
               account_live_arm_session_id,strategy_activation_id,
               strategy_execution_binding_id,trading_account_id,
               account_connector_id,strategy_id,strategy_version,
               strategy_config_fingerprint,symbol,release_commit,state,
               owner_approved_at,activated_at,deactivated_at,owner_approval_source
           ) VALUES(%s,%s,%s,%s,%s,%s,'1',%s,%s,%s,%s,%s,%s,%s,'disposable-test')""",
        (
            f"{ids['activation']}-arm-{number}",
            ids["activation"], ids[f"binding-{number}"], ids[f"account-{number}"],
            ids[f"connector-{number}"], ids["strategy"], ids["fingerprint"],
            symbol, RELEASE, state, NOW - timedelta(minutes=3),
            NOW - timedelta(minutes=2),
            None if state == "ACTIVE" else NOW - timedelta(minutes=1),
        ),
    )


def _check(
    db: psycopg.Connection, ids: dict[str, str], number: int,
    *, connector_override: str | None = None, release: str = RELEASE,
    symbol: str = "BTCUSDT",
) -> tuple[bool, tuple[str, ...]]:
    return account_live_arm_ready(
        db,
        strategy_activation_id=ids["activation"],
        strategy_execution_binding_id=ids[f"binding-{number}"],
        trading_account_id=ids[f"account-{number}"],
        account_connector_id=(
            ids[f"connector-{number}"]
            if connector_override is None else connector_override
        ),
        strategy_id=ids["strategy"],
        strategy_version="1",
        strategy_config_fingerprint=ids["fingerprint"],
        symbol=symbol,
        release_commit=release,
        now=NOW,
    )


def test_real_postgres_arm_is_per_account_connector_symbol_and_release(
    disposable_db: psycopg.Connection,
) -> None:
    ids = _seed(disposable_db)
    assert _check(disposable_db, ids, 1)[0] is False
    assert _check(disposable_db, ids, 2)[0] is False
    _arm(disposable_db, ids, 1)
    assert _check(disposable_db, ids, 1) == (True, ())
    assert _check(disposable_db, ids, 2)[0] is False
    assert _check(
        disposable_db, ids, 1, connector_override=ids["connector-2"]
    )[0] is False
    assert _check(disposable_db, ids, 1, release="b" * 40)[0] is False
    assert _check(disposable_db, ids, 1, symbol="ETHUSDT")[0] is False
    _arm(disposable_db, ids, 2)
    assert _check(disposable_db, ids, 1) == (True, ())
    assert _check(disposable_db, ids, 2) == (True, ())
    disposable_db.execute(
        """UPDATE control.account_live_arm_sessions
           SET state='DISARMED',deactivated_at=%s
         WHERE trading_account_id=%s""",
        (NOW - timedelta(minutes=1), ids["account-1"]),
    )
    assert _check(disposable_db, ids, 1)[0] is False
    assert _check(disposable_db, ids, 2) == (True, ())


def test_r04_migration_runtime_actor_has_read_only_arm_registry(
    disposable_db: psycopg.Connection,
) -> None:
    read = disposable_db.execute(
        """SELECT has_table_privilege('cripta',%s,'SELECT') AS allowed""",
        ("control.account_live_arm_sessions",),
    ).fetchone()
    write = disposable_db.execute(
        """SELECT has_table_privilege('cripta',%s,'INSERT') AS allowed""",
        ("control.account_live_arm_sessions",),
    ).fetchone()
    assert read is not None and read["allowed"] is True
    assert write is not None and write["allowed"] is False


def test_account_entry_safety_latch_is_durable_isolation_not_live_permission(
    disposable_db: psycopg.Connection,
) -> None:
    ids = _seed(disposable_db)
    one = {
        "trading_account_id": ids["account-1"],
        "account_connector_id": ids["connector-1"],
    }
    two = {
        "trading_account_id": ids["account-2"],
        "account_connector_id": ids["connector-2"],
    }
    assert account_entry_safety_ready(disposable_db, **one) == (True, ())
    assert account_entry_safety_ready(disposable_db, **two) == (True, ())
    latch_account_entry_safety(disposable_db, **two, reason="SUB_WS_RECONNECT_UNPROVEN")
    latch_account_entry_safety(disposable_db, **two, reason="RETRY_MUST_NOT_OVERWRITE_FIRST")
    assert account_entry_safety_ready(disposable_db, **one) == (True, ())
    assert account_entry_safety_ready(disposable_db, **two) == (
        False, ("ACCOUNT_ENTRY_SAFETY_BLOCKED",),
    )
    reason_row = disposable_db.execute(
        """SELECT reason FROM runtime.account_entry_safety_blocks
           WHERE trading_account_id=%s AND account_connector_id=%s""",
        (ids["account-2"], ids["connector-2"]),
    ).fetchone()
    assert reason_row is not None
    assert reason_row["reason"] == "SUB_WS_RECONNECT_UNPROVEN"
    # A separately authorized owner-control actor may remove a block after
    # a complete authenticated recovery; runtime 'cripta' cannot do so.
    disposable_db.execute(
        """DELETE FROM runtime.account_entry_safety_blocks
           WHERE trading_account_id=%s AND account_connector_id=%s""",
        (ids["account-2"], ids["connector-2"]),
    )
    assert account_entry_safety_ready(disposable_db, **two) == (True, ())
    assert account_entry_safety_ready(disposable_db, **one) == (True, ())


def test_entry_safety_runtime_actor_may_latch_but_not_clear(
    disposable_db: psycopg.Connection,
) -> None:
    table = "runtime.account_entry_safety_blocks"
    for permission, expected in (
        ("SELECT", True),
        ("INSERT", True),
        ("UPDATE", False),
        ("DELETE", False),
        ("TRUNCATE", False),
    ):
        row = disposable_db.execute(
            "SELECT has_table_privilege(%s,%s,%s) AS allowed",
            ("cripta", table, permission),
        ).fetchone()
        assert row is not None and row["allowed"] is expected
