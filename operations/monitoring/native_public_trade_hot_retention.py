#!/usr/bin/env python3
"""Bounded retention for native publicTrade causal journals.

Issue #642 / ARCH §61 production-storage contract:
- PostgreSQL is operational storage, not a historical public-market archive;
- keep current native Entry recovery only as a bounded hot suffix;
- keep exact durable publication/checkpoint/cursor points;
- keep every PROVEN real-position fill->close interval plus a 60s prefill anchor;
- GAP source sessions retain a 15-minute handoff window, then only pinned rows;
- parent trade rows are removed only when no source-event or source-cursor
  still owns the exact immutable market/instrument/sequence/exec identity.

This never touches closed candles, market_minutes, Strategy/Entry/Exit decisions,
orders/fills/account truth, LIVE/ARM controls, or Exchange state. Production
--apply requires the postgres Unix actor and PostgreSQL role.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any

RETENTION_MINUTES = 15
DEFAULT_BATCH_LIMIT = 50_000
MAX_BATCH_LIMIT = 50_000

SOURCE_EVENTS = "analytics.native_public_trade_source_events"
PARENT_EVENTS = "analytics.native_public_trade_events"
CURSORS = "analytics.native_public_trade_source_cursors"

POINT_PIN_TABLES = (
    "analytics.native_public_trade_observer_delivery",
    "monitoring.opportunity_source_checkpoints",
    "strategy_entry.p90_r06_checkpoint_release_bridges",
    "strategy_entry.p90_r06_publication_observations",
    "strategy_entry.p90_scoped_native_consumer_checkpoints",
)


class NativeTradeRetentionBlocked(RuntimeError):
    """Lineage, financial or actor drift must stop native-journal deletion."""


@dataclass(frozen=True)
class Result:
    mode: str
    cutoff_utc: str
    rows: dict[str, int]
    database_bytes: int
    active_session: str | None
    active_frontier: tuple[int, int] | None


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise NativeTradeRetentionBlocked("NATIVE_TRADE_RETENTION_" + code)


def _postgres_unix_uid() -> int:
    import pwd

    return int(pwd.getpwnam("postgres").pw_uid)


def _active_source(connection: Any) -> str | None:
    rows = connection.execute(
        """SELECT source_session_id
             FROM analytics.native_public_trade_source_sessions
            WHERE state='ACTIVE'
            ORDER BY subscribed_at DESC
            LIMIT 2"""
    ).fetchall()
    _need(len(rows) <= 1, "MULTIPLE_ACTIVE_SOURCE_EPOCHS")
    if not rows:
        return None
    session = rows[0][0]
    _need(isinstance(session, str) and len(session) == 32,
          "ACTIVE_SOURCE_IDENTITY_INVALID")
    return str(session)


def _active_frontier(
    connection: Any, active_session: str | None,
) -> tuple[int, int] | None:
    if active_session is None:
        return None
    row = connection.execute(
        """SELECT frame_number,frame_index
             FROM strategy_entry.p90_scoped_native_consumer_checkpoints
            WHERE source_session_id=%s
            ORDER BY frame_number,frame_index
            LIMIT 1""",
        (active_session,),
    ).fetchone()
    if row is None:
        return None
    frame, index = row
    _need(
        type(frame) is int and frame >= 1
        and type(index) is int and index >= 0,
        "ACTIVE_CONSUMER_FRONTIER_INVALID",
    )
    return int(frame), int(index)


def _financial_consistency(connection: Any) -> None:
    hot_positions = int(connection.execute(
        "SELECT count(*) FROM runtime.hot_positions"
    ).fetchone()[0])
    hot_orders = int(connection.execute(
        "SELECT count(*) FROM runtime.hot_orders"
    ).fetchone()[0])
    open_rows = connection.execute(
        """SELECT position_id,symbol,fill_at,closed_at,identity_status
             FROM runtime.position_ownership
            WHERE state<>'CLOSED'"""
    ).fetchall()
    _need(hot_orders == 0, "HOT_ORDER_PRESENT")
    _need(hot_positions == len(open_rows), "OPEN_POSITION_STATE_DRIFT")
    for position_id, symbol, fill_at, closed_at, identity in open_rows:
        _need(
            isinstance(position_id, str) and bool(position_id)
            and isinstance(symbol, str) and bool(symbol)
            and isinstance(fill_at, datetime)
            and fill_at.utcoffset() is not None
            and closed_at is None
            and identity == "PROVEN",
            "OPEN_POSITION_PIN_INCOMPLETE",
        )


def _validate_point_pins(connection: Any) -> None:
    for table in POINT_PIN_TABLES:
        missing = int(connection.execute(
            f"""SELECT count(*)
                  FROM {table} r
                  LEFT JOIN analytics.native_public_trade_source_events e
                    ON (e.source_session_id,e.frame_number,e.frame_index)
                     =(r.source_session_id,r.frame_number,r.frame_index)
                 WHERE e.source_session_id IS NULL"""
        ).fetchone()[0])
        _need(missing == 0, "DURABLE_POINT_PIN_DANGLING")

    publication_missing = int(connection.execute(
        """SELECT count(*)
             FROM (
               SELECT p.source_session_id,
                      (w.value->>'frame_number')::bigint AS frame_number,
                      (w.value->>'frame_index')::integer AS frame_index
                 FROM analytics.market_publications p
                 CROSS JOIN LATERAL jsonb_each(p.native_watermarks) w
             ) r
             LEFT JOIN analytics.native_public_trade_source_events e
               ON (e.source_session_id,e.frame_number,e.frame_index)
                =(r.source_session_id,r.frame_number,r.frame_index)
            WHERE e.source_session_id IS NULL"""
    ).fetchone()[0])
    _need(publication_missing == 0, "PUBLICATION_NATIVE_PIN_DANGLING")

    parent_missing = int(connection.execute(
        """SELECT count(*)
             FROM analytics.native_public_trade_source_cursors c
             LEFT JOIN analytics.native_public_trade_events n
               ON n.market_source_id=c.market_source_id
              AND n.instrument_id=c.instrument_id
              AND n.exchange_sequence=c.exchange_sequence
              AND n.exec_id=c.exec_id
            WHERE n.market_source_id IS NULL"""
    ).fetchone()[0])
    _need(parent_missing == 0, "CURSOR_PARENT_EVENT_DANGLING")

    first_source = connection.execute(
        "SELECT min(accepted_at) FROM analytics.native_public_trade_source_events"
    ).fetchone()[0]
    cursor_bad = int(connection.execute(
        """SELECT count(*)
             FROM analytics.native_public_trade_source_cursors c
             JOIN analytics.native_public_trade_source_sessions s
               USING(source_session_id)
             LEFT JOIN analytics.native_public_trade_source_events e
               ON e.source_session_id=c.source_session_id
              AND e.market_source_id=c.market_source_id
              AND e.instrument_id=c.instrument_id
              AND e.exchange_sequence=c.exchange_sequence
              AND e.exec_id=c.exec_id
            WHERE e.source_session_id IS NULL
              AND NOT (
                s.state='GAP'
                AND %s IS NOT NULL
                AND c.accepted_at < %s
              )""",
        (first_source, first_source),
    ).fetchone()[0])
    _need(cursor_bad == 0, "CURRENT_CURSOR_SOURCE_EVENT_DANGLING")


def _create_retained_points(connection: Any) -> None:
    connection.execute(
        """CREATE TEMP TABLE retained_native_points(
             source_session_id text NOT NULL,
             frame_number bigint NOT NULL,
             frame_index integer NOT NULL,
             PRIMARY KEY(source_session_id,frame_number,frame_index)
           ) ON COMMIT DROP"""
    )
    for table in POINT_PIN_TABLES:
        connection.execute(
            f"""INSERT INTO retained_native_points
                SELECT DISTINCT source_session_id,frame_number,frame_index
                  FROM {table}
                ON CONFLICT DO NOTHING"""
        )

    connection.execute(
        """INSERT INTO retained_native_points
           SELECT DISTINCT p.source_session_id,
                  (w.value->>'frame_number')::bigint,
                  (w.value->>'frame_index')::integer
             FROM analytics.market_publications p
             CROSS JOIN LATERAL jsonb_each(p.native_watermarks) w
           ON CONFLICT DO NOTHING"""
    )

    # Historical pre-source-event cursors are legitimate parent-only anchors.
    # Only cursors that physically map to source_events pin a source-event row.
    connection.execute(
        """INSERT INTO retained_native_points
           SELECT DISTINCT c.source_session_id,e.frame_number,e.frame_index
             FROM analytics.native_public_trade_source_cursors c
             JOIN analytics.native_public_trade_source_events e
               ON e.source_session_id=c.source_session_id
              AND e.market_source_id=c.market_source_id
              AND e.instrument_id=c.instrument_id
              AND e.exchange_sequence=c.exchange_sequence
              AND e.exec_id=c.exec_id
           ON CONFLICT DO NOTHING"""
    )


_POSITION_EXCLUSION = """
AND NOT EXISTS (
  SELECT 1
    FROM runtime.position_ownership p
   WHERE p.identity_status='PROVEN'
     AND p.fill_at IS NOT NULL
     AND p.symbol=e.external_symbol
     AND e.event_at >= p.fill_at - interval '60 seconds'
     AND e.event_at <= COALESCE(p.closed_at,clock_timestamp())
)
"""

_POINT_EXCLUSION = """
AND NOT EXISTS (
  SELECT 1
    FROM retained_native_points keep
   WHERE (keep.source_session_id,keep.frame_number,keep.frame_index)
        =(e.source_session_id,e.frame_number,e.frame_index)
)
"""


def _create_deleted_keys(connection: Any) -> None:
    connection.execute(
        """CREATE TEMP TABLE deleted_native_keys(
             market_source_id text NOT NULL,
             instrument_id text NOT NULL,
             exchange_sequence bigint NOT NULL,
             exec_id text NOT NULL
           ) ON COMMIT DROP"""
    )


def _delete_active_prefix(
    connection: Any, *,
    active_session: str | None,
    frontier: tuple[int, int] | None,
    cutoff: datetime,
    limit: int,
) -> int:
    if active_session is None or limit <= 0:
        return 0
    frontier_sql = ""
    params: list[object] = [active_session, cutoff]
    if frontier is not None:
        frontier_sql = "AND (e.frame_number,e.frame_index) <= (%s,%s)"
        params.extend(frontier)
    params.append(limit)
    return int(connection.execute(
        f"""WITH stale AS (
              SELECT e.ctid
                FROM analytics.native_public_trade_source_events e
               WHERE e.source_session_id=%s
                 AND e.accepted_at < %s
                 {frontier_sql}
                 {_POINT_EXCLUSION}
                 {_POSITION_EXCLUSION}
               ORDER BY e.frame_number,e.frame_index
               LIMIT %s
               FOR UPDATE SKIP LOCKED
            ), gone AS (
              DELETE FROM analytics.native_public_trade_source_events e
               USING stale
               WHERE e.ctid=stale.ctid
               RETURNING e.market_source_id,e.instrument_id,
                         e.exchange_sequence,e.exec_id
            )
            INSERT INTO deleted_native_keys
            SELECT market_source_id,instrument_id,exchange_sequence,exec_id
              FROM gone""",
        tuple(params),
    ).rowcount)


def _delete_gap_batch(
    connection: Any, *, cutoff: datetime, limit: int,
) -> int:
    if limit <= 0:
        return 0
    return int(connection.execute(
        f"""WITH stale AS (
              SELECT e.ctid
                FROM analytics.native_public_trade_source_sessions s
                JOIN analytics.native_public_trade_source_events e
                  USING(source_session_id)
               WHERE s.state='GAP'
                 AND s.ended_at IS NOT NULL
                 AND s.ended_at < %s
                 {_POINT_EXCLUSION}
                 {_POSITION_EXCLUSION}
               ORDER BY s.ended_at,s.source_session_id,
                        e.frame_number,e.frame_index
               LIMIT %s
               FOR UPDATE OF e SKIP LOCKED
            ), gone AS (
              DELETE FROM analytics.native_public_trade_source_events e
               USING stale
               WHERE e.ctid=stale.ctid
               RETURNING e.market_source_id,e.instrument_id,
                         e.exchange_sequence,e.exec_id
            )
            INSERT INTO deleted_native_keys
            SELECT market_source_id,instrument_id,exchange_sequence,exec_id
              FROM gone""",
        (cutoff, limit),
    ).rowcount)


def _delete_unowned_parents(connection: Any) -> int:
    return int(connection.execute(
        """WITH keys AS (
             SELECT DISTINCT market_source_id,instrument_id,
                             exchange_sequence,exec_id
               FROM deleted_native_keys
           )
           DELETE FROM analytics.native_public_trade_events n
           USING keys k
           WHERE (n.market_source_id,n.instrument_id,
                  n.exchange_sequence,n.exec_id)
                =(k.market_source_id,k.instrument_id,
                  k.exchange_sequence,k.exec_id)
             AND NOT EXISTS (
               SELECT 1
                 FROM analytics.native_public_trade_source_events e
                WHERE (e.market_source_id,e.instrument_id,
                       e.exchange_sequence,e.exec_id)
                     =(n.market_source_id,n.instrument_id,
                       n.exchange_sequence,n.exec_id)
             )
             AND NOT EXISTS (
               SELECT 1
                 FROM analytics.native_public_trade_source_cursors c
                WHERE (c.market_source_id,c.instrument_id,
                       c.exchange_sequence,c.exec_id)
                     =(n.market_source_id,n.instrument_id,
                       n.exchange_sequence,n.exec_id)
             )"""
    ).rowcount)


def _inline_point_exclusion(alias: str = "e") -> str:
    clauses = [
        f"""NOT EXISTS (
              SELECT 1 FROM {table} r
               WHERE (r.source_session_id,r.frame_number,r.frame_index)
                    =({alias}.source_session_id,{alias}.frame_number,{alias}.frame_index)
            )"""
        for table in POINT_PIN_TABLES
    ]
    clauses.append(
        f"""NOT EXISTS (
              SELECT 1
                FROM analytics.market_publications p
                CROSS JOIN LATERAL jsonb_each(p.native_watermarks) w
               WHERE p.source_session_id={alias}.source_session_id
                 AND (w.value->>'frame_number')::bigint={alias}.frame_number
                 AND (w.value->>'frame_index')::integer={alias}.frame_index
            )"""
    )
    clauses.append(
        f"""NOT EXISTS (
              SELECT 1
                FROM analytics.native_public_trade_source_cursors c
               WHERE c.source_session_id={alias}.source_session_id
                 AND c.frame_number={alias}.frame_number
                 AND c.frame_index={alias}.frame_index
            )"""
    )
    return " AND ".join(clauses)


def _next_candidate_counts(
    connection: Any, *,
    active_session: str | None,
    frontier: tuple[int, int] | None,
    cutoff: datetime,
    limit: int,
) -> tuple[int, int]:
    point_filter = _inline_point_exclusion("e")
    position_filter = """
      NOT EXISTS (
        SELECT 1 FROM runtime.position_ownership p
         WHERE p.identity_status='PROVEN'
           AND p.fill_at IS NOT NULL
           AND p.symbol=e.external_symbol
           AND e.event_at >= p.fill_at - interval '60 seconds'
           AND e.event_at <= COALESCE(p.closed_at,clock_timestamp())
      )
    """
    active_count = 0
    if active_session is not None:
        frontier_sql = ""
        params: list[object] = [active_session, cutoff]
        if frontier is not None:
            frontier_sql = "AND (e.frame_number,e.frame_index) <= (%s,%s)"
            params.extend(frontier)
        params.append(limit)
        active_count = int(connection.execute(
            f"""SELECT count(*) FROM (
                  SELECT 1
                    FROM analytics.native_public_trade_source_events e
                   WHERE e.source_session_id=%s
                     AND e.accepted_at < %s
                     {frontier_sql}
                     AND {point_filter}
                     AND {position_filter}
                   ORDER BY e.frame_number,e.frame_index
                   LIMIT %s
                ) q""",
            tuple(params),
        ).fetchone()[0])

    gap_count = int(connection.execute(
        f"""SELECT count(*) FROM (
              SELECT 1
                FROM analytics.native_public_trade_source_sessions s
                JOIN analytics.native_public_trade_source_events e
                  USING(source_session_id)
               WHERE s.state='GAP'
                 AND s.ended_at IS NOT NULL
                 AND s.ended_at < %s
                 AND {point_filter}
                 AND {position_filter}
               ORDER BY s.ended_at,s.source_session_id,
                        e.frame_number,e.frame_index
               LIMIT %s
            ) q""",
        (cutoff, limit),
    ).fetchone()[0])
    return active_count, gap_count


def collect_check(connection: Any, cutoff: datetime, limit: int) -> Result:
    _financial_consistency(connection)
    _validate_point_pins(connection)
    active = _active_source(connection)
    frontier = _active_frontier(connection, active)
    active_count, gap_count = _next_candidate_counts(
        connection,
        active_session=active,
        frontier=frontier,
        cutoff=cutoff,
        limit=limit,
    )
    size = int(connection.execute(
        "SELECT pg_database_size(current_database())"
    ).fetchone()[0])
    return Result(
        "CHECK",
        cutoff.isoformat(),
        {
            "active_source_events_next_batch": active_count,
            "gap_source_events_next_batch": gap_count,
            "parent_events_deleted": 0,
        },
        size,
        active,
        frontier,
    )


def run_apply(connection: Any, cutoff: datetime, limit: int) -> Result:
    if sys.platform != "linux":
        raise RuntimeError("NATIVE_TRADE_RETENTION_LINUX_REQUIRED")
    if os.geteuid() != _postgres_unix_uid():
        raise RuntimeError("NATIVE_TRADE_RETENTION_POSTGRES_UNIX_ACTOR_REQUIRED")

    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout='3000ms'")
        connection.execute("SET LOCAL statement_timeout='90000ms'")
        identity = connection.execute(
            "SELECT session_user,current_user,current_database()"
        ).fetchone()
        _need(identity == ("postgres", "postgres", "cripta"),
              "POSTGRES_DB_ROLE_REQUIRED")
        got = connection.execute(
            "SELECT pg_try_advisory_xact_lock("
            "hashtext('cripta-native-public-trade-hot-retention-v1'))"
        ).fetchone()[0]
        _need(got is True, "JOB_ALREADY_RUNNING")

        _financial_consistency(connection)
        _validate_point_pins(connection)
        active = _active_source(connection)
        frontier = _active_frontier(connection, active)
        _create_retained_points(connection)
        _create_deleted_keys(connection)

        active_deleted = _delete_active_prefix(
            connection,
            active_session=active,
            frontier=frontier,
            cutoff=cutoff,
            limit=limit,
        )
        gap_deleted = _delete_gap_batch(
            connection,
            cutoff=cutoff,
            limit=max(0, limit - active_deleted),
        )
        parent_deleted = _delete_unowned_parents(connection)
        size = int(connection.execute(
            "SELECT pg_database_size(current_database())"
        ).fetchone()[0])

    return Result(
        "APPLIED",
        cutoff.isoformat(),
        {
            "active_source_events_deleted": active_deleted,
            "gap_source_events_deleted": gap_deleted,
            "parent_events_deleted": parent_deleted,
        },
        size,
        active,
        frontier,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bounded native publicTrade causal-journal retention"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-limit", type=int, default=DEFAULT_BATCH_LIMIT)
    args = parser.parse_args()
    if not 1 <= args.batch_limit <= MAX_BATCH_LIMIT:
        raise SystemExit("NATIVE_TRADE_RETENTION_BATCH_LIMIT_INVALID")

    import psycopg  # type: ignore[import-not-found]

    role = "postgres" if args.apply else "cripta"
    dsn = (
        f"dbname=cripta user={role} host=/var/run/postgresql "
        "application_name=native-public-trade-hot-retention"
    )
    try:
        with psycopg.connect(
            dsn, autocommit=True, connect_timeout=5,
        ) as connection:
            cutoff = connection.execute(
                "SELECT clock_timestamp() - make_interval(mins => %s)",
                (RETENTION_MINUTES,),
            ).fetchone()[0]
            if args.apply:
                done = run_apply(connection, cutoff, args.batch_limit)
            else:
                connection.execute("BEGIN READ ONLY")
                try:
                    done = collect_check(
                        connection, cutoff, args.batch_limit
                    )
                finally:
                    connection.rollback()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error_class": type(exc).__name__,
                    "reason": str(exc)[:320],
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(
        {
            "status": done.mode,
            "cutoff_utc": done.cutoff_utc,
            "rows": done.rows,
            "database_bytes": done.database_bytes,
            "active_session": done.active_session,
            "active_frontier": done.active_frontier,
        },
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
