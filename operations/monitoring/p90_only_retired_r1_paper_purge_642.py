#!/usr/bin/env python3
"""Issue #642: one-time retirement of obsolete nonfinancial R1 PAPER simulation.

Owner approved retiring R1 PAPER, including the four abandoned OPEN virtual
positions dated 2026-10-25. Proven separate from runtime real positions and
Exchange execution requests. No actual trading/Bybit mutation or backup.

This executor is scoped to THREE exact parent/child PAPER tables.
Postgres TRANSACTIONAL TRUNCATE lists all three explicitly, with no CASCADE,
no real Position/Execution/MarketSource table, and no schema/trigger change.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

EVENTS = "strategy_entry.paper_position_events"
POSITIONS = "strategy_entry.paper_positions"
ORDERS = "strategy_entry.paper_orders"
COUNTS = {EVENTS: 5097, POSITIONS: 102, ORDERS: 178}
ROOT_FREE_REQUIRED = 512 * 1024 * 1024
RETIRED_OPEN_SYMBOLS = ["APTUSDT", "ARBUSDT", "INJUSDT", "LTCUSDT"]
INBOUND_FOREIGN_KEYS = [
    (EVENTS, POSITIONS, "paper_position_events_paper_position_id_fkey"),
    (POSITIONS, ORDERS, "paper_positions_paper_order_id_fkey"),
    (POSITIONS, POSITIONS, "paper_positions_parent_position_id_fkey"),
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("R1_PAPER_642_BLOCKED:" + message)


def inspect(cursor: Any, *, empty: bool) -> dict[str, Any]:
    cursor.execute(
        "SELECT current_user,session_user,current_database(),"
        " pg_is_in_recovery()"
    )
    actor, session, database, recovery = cursor.fetchone()
    require(database == "cripta" and not recovery, "DATABASE_IDENTITY")
    cursor.execute(
        """SELECT conrelid::regclass::text,confrelid::regclass::text,conname
             FROM pg_constraint
            WHERE contype='f' AND confrelid=ANY(
              ARRAY[%s::regclass,%s::regclass,%s::regclass])
         ORDER BY 1,2,3""",
        (EVENTS, POSITIONS, ORDERS),
    )
    require(
        cursor.fetchall() == sorted(INBOUND_FOREIGN_KEYS),
        "EXTERNAL_FOREIGN_KEY_DRIFT",
    )
    cursor.execute(
        """SELECT tgrelid::regclass::text,tgname
             FROM pg_trigger
            WHERE tgrelid=ANY(
              ARRAY[%s::regclass,%s::regclass,%s::regclass])
              AND NOT tgisinternal""",
        (EVENTS, POSITIONS, ORDERS),
    )
    require(cursor.fetchall() == [], "PAPER_TRIGGER_DRIFT")
    cursor.execute(
        """SELECT oid::regclass::text,pg_get_userbyid(relowner),
                  pg_total_relation_size(oid)
             FROM pg_class
            WHERE oid=ANY(ARRAY[%s::regclass,%s::regclass,%s::regclass])
         ORDER BY 1""",
        (EVENTS, POSITIONS, ORDERS),
    )
    relations = cursor.fetchall()
    require(
        len(relations) == 3
        and all(owner == "postgres" for _, owner, _ in relations),
        "PAPER_SCHEMA_OWNER_DRIFT",
    )
    actual: dict[str, int] = {}
    for table in (EVENTS, POSITIONS, ORDERS):
        # A closed literal table set, no user-controlled SQL identifiers.
        cursor.execute("SELECT count(*) FROM " + table)
        actual[table] = int(cursor.fetchone()[0])
    if empty:
        require(all(n == 0 for n in actual.values()), "POST_TRUNCATE_NONEMPTY")
    else:
        require(actual == COUNTS, "PAPER_ROW_COUNT_DRIFT")
        cursor.execute(
            """SELECT count(*) FILTER(WHERE state='OPEN'),
                      count(*) FILTER(WHERE state='CLOSED'),
                      count(*) FILTER(WHERE leg_type<>'PRIMARY'),
                      count(*) FILTER(WHERE strategy_id NOT LIKE 'r1_%'),
                      count(*) FILTER(WHERE opened_at >=
                        '2026-10-26'::timestamptz)
               FROM strategy_entry.paper_positions"""
        )
        open_rows, closed_rows, nonprimary, unrelated, newer = cursor.fetchone()
        require(
            (open_rows, closed_rows, nonprimary, unrelated, newer)
            == (4, 98, 0, 0, 0),
            "PAPER_POSITION_CLASS_DRIFT",
        )
        cursor.execute(
            """SELECT external_symbol FROM (
                 SELECT symbol AS external_symbol
                   FROM strategy_entry.paper_positions
                  WHERE state='OPEN' AND
                        strategy_id='r1_five_symbols' AND
                        strategy_version='1.0-five-symbols-20261021'
                        AND direction='SHORT'
               ) x ORDER BY external_symbol"""
        )
        require(
            [r[0] for r in cursor.fetchall()] == RETIRED_OPEN_SYMBOLS,
            "ABANDONED_PAPER_OPEN_FOUR_DRIFT",
        )
        cursor.execute(
            """SELECT count(*) FILTER(WHERE execution_request_id IS NOT NULL),
                      count(*) FILTER(WHERE strategy_id NOT LIKE 'r1_%'),
                      count(*) FILTER(WHERE state='FILLED'),
                      count(*) FILTER(WHERE state='CANCELLED'),
                      count(*) FILTER(WHERE state NOT IN ('FILLED','CANCELLED'))
               FROM strategy_entry.paper_orders"""
        )
        require(
            cursor.fetchone() == (0, 0, 102, 76, 0),
            "PAPER_REAL_EXECUTION_OR_PENDING_ORDER",
        )
        cursor.execute(
            """SELECT count(*)
                 FROM strategy_entry.paper_positions p
                 JOIN runtime.position_ownership r
                   ON r.position_id=p.paper_position_id"""
        )
        require(cursor.fetchone() == (0,), "PAPER_REAL_POSITION_ID_LINK")
        cursor.execute(
            """SELECT count(*)
                 FROM strategy_entry.paper_position_events e
                 LEFT JOIN strategy_entry.paper_positions p
                   ON p.paper_position_id=e.paper_position_id
                WHERE p.paper_position_id IS NULL"""
        )
        require(cursor.fetchone() == (0,), "PAPER_EVENT_ORPHAN")
    return {
        "actor": actor,
        "session_user": session,
        "rows": actual,
        "relation_bytes": {t: int(b) for t, _, b in relations},
        "inbound_foreign_keys": len(INBOUND_FOREIGN_KEYS),
        "user_triggers": 0,
        "abandoned_open_virtual_positions": 0 if empty else 4,
        "real_exchange_execution_links": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require(os.name == "posix", "LINUX_REQUIRED")
    import pwd
    import psycopg

    username = pwd.getpwuid(os.geteuid()).pw_name
    require(
        username == "postgres" if args.apply else
        username in {"cripta", "postgres"},
        "UNIX_ROLE",
    )
    root = os.statvfs("/")
    require(
        root.f_bavail * root.f_frsize >= ROOT_FREE_REQUIRED,
        "ROOT_FREE_CAPACITY",
    )
    with psycopg.connect(
        "dbname=cripta user=" + username + " host=/var/run/postgresql",
        autocommit=True,
        options="-c statement_timeout=30000 -c lock_timeout=2000",
    ) as conn:
        if args.check:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                before = inspect(conn.cursor(), empty=False)
            print(json.dumps({
                "status": "READ_ONLY_READY_NO_MUTATION", "evidence": before,
            }, sort_keys=True))
            return 0
        with conn.transaction():
            conn.execute("SET LOCAL lock_timeout='2000ms'")
            conn.execute("SET LOCAL statement_timeout='30000ms'")
            conn.execute(
                "LOCK TABLE strategy_entry.paper_position_events,"
                " strategy_entry.paper_positions,"
                " strategy_entry.paper_orders IN ACCESS EXCLUSIVE MODE NOWAIT"
            )
            locked = conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
                ("CRIPTA_642_RETIRED_R1_PAPER",),
            ).fetchone()
            require(locked == (True,), "ADVISORY_LOCK_BUSY")
            before = inspect(conn.cursor(), empty=False)
            require(
                (before["actor"], before["session_user"])
                == ("postgres", "postgres"),
                "DB_MIGRATION_ROLE",
            )
            conn.execute(
                "TRUNCATE TABLE strategy_entry.paper_position_events,"
                " strategy_entry.paper_positions,"
                " strategy_entry.paper_orders"
            )
            after = inspect(conn.cursor(), empty=True)
        print(json.dumps({
            "status": "APPLIED_COMMITTED",
            "before": before,
            "after": after,
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
