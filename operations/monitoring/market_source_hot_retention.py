#!/usr/bin/env python3
"""Bounded retention for canonical public MarketSource hot read-models.

Issue #642 storage contract:
- PostgreSQL is not a raw market-data lake;
- current publications/second/forming/REST facts keep a short live buffer;
- any publication referenced by a durable causal FK is pinned indefinitely;
- raw optional frames keep only retained publication refs plus the unconsumed
  current MAYAK cursor tail. Closed/GAP source epochs are not historical tape.

This script never touches native public-trade causal journals, closed candles,
market_minutes, account/private state, Entry/Exit/Execution or financial gates.
Production --apply requires the postgres Unix actor and PostgreSQL role.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SOURCE_RETENTION_MINUTES = 15
DEFAULT_BATCH_LIMIT = 10_000
MAX_BATCH_LIMIT = 50_000
_CONSUMER_ID = "R11_PG_ORIGINAL_MAYAK_V1"
_STATE_MAGIC = b"CRIPTA_R11_MAYAK_CHECKPOINT_V1\n"

PUBLICATIONS = "analytics.market_publications"
SECONDS = "analytics.public_market_source_seconds"
FORMING = "analytics.public_forming_minute_observations"
REST = "analytics.public_market_rest_observations"
RAW = "analytics.public_source_original_event_frames"


class SourceRetentionBlocked(RuntimeError):
    """Schema/lineage/pin drift must stop cleanup before unsafe deletion."""


@dataclass(frozen=True)
class Result:
    mode: str
    cutoff_utc: str
    rows: dict[str, int]
    database_bytes: int


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise SourceRetentionBlocked("MARKET_SOURCE_RETENTION_" + code)


def _postgres_unix_uid() -> int:
    import pwd

    return int(pwd.getpwnam("postgres").pw_uid)


def _publication_fk_pins(connection: Any) -> tuple[int, ...]:
    """Discover every scalar FK into market_publications.publication_id."""
    rows = connection.execute(
        """SELECT f.conrelid::regclass::text,
                  local_col.attname,
                  remote_col.attname,
                  cardinality(f.conkey),
                  cardinality(f.confkey)
             FROM pg_constraint f
             JOIN pg_attribute local_col
               ON local_col.attrelid=f.conrelid
              AND local_col.attnum=f.conkey[1]
             JOIN pg_attribute remote_col
               ON remote_col.attrelid=f.confrelid
              AND remote_col.attnum=f.confkey[1]
            WHERE f.contype='f'
              AND f.confrelid='analytics.market_publications'::regclass
            ORDER BY 1,2"""
    ).fetchall()
    _need(bool(rows), "PUBLICATION_FK_INVENTORY_EMPTY")
    pins: set[int] = set()
    from psycopg import sql  # type: ignore[import-not-found]

    for table, local_column, remote_column, local_count, remote_count in rows:
        _need(
            local_count == 1
            and remote_count == 1
            and remote_column == "publication_id"
            and isinstance(table, str)
            and "." in table
            and isinstance(local_column, str),
            "PUBLICATION_FK_SHAPE_CHANGED",
        )
        schema, name = table.split(".", 1)
        query = sql.SQL(
            "SELECT DISTINCT {column} FROM {schema}.{table} "
            "WHERE {column} IS NOT NULL"
        ).format(
            column=sql.Identifier(local_column),
            schema=sql.Identifier(schema),
            table=sql.Identifier(name),
        )
        for row in connection.execute(query).fetchall():
            _need(
                isinstance(row, (tuple, list))
                and len(row) == 1
                and type(row[0]) is int
                and row[0] > 0,
                "PUBLICATION_PIN_VALUE_INVALID",
            )
            pins.add(int(row[0]))
    _need(1 <= len(pins) <= 10_000, "PUBLICATION_PIN_SET_INVALID")
    return tuple(sorted(pins))


def _decode_optional_cursors(
    state: bytes, *, expected_session: str, expected_release: str,
) -> dict[str, tuple[str, int]]:
    """Return product -> (transport_epoch_id, processed_frame_cursor)."""
    _need(
        isinstance(state, bytes) and state.startswith(_STATE_MAGIC),
        "MAYAK_STATE_MAGIC_INVALID",
    )
    try:
        digest, packed = state[len(_STATE_MAGIC):].split(b"\n", 1)
        original = zlib.decompress(packed)
        _need(
            hashlib.sha256(original).hexdigest().encode("ascii") == digest,
            "MAYAK_STATE_DIGEST_INVALID",
        )
        payload = json.loads(original)
    except (ValueError, zlib.error, json.JSONDecodeError) as exc:
        raise SourceRetentionBlocked(
            "MARKET_SOURCE_RETENTION_MAYAK_STATE_DECODE_INVALID"
        ) from exc
    checkpoint = payload.get("checkpoint")
    _need(isinstance(checkpoint, dict), "MAYAK_CHECKPOINT_MISSING")
    _need(
        checkpoint.get("source_session_id") == expected_session
        and checkpoint.get("release_commit") == expected_release,
        "MAYAK_CHECKPOINT_IDENTITY_DRIFT",
    )
    optional = checkpoint.get("optional_cursors")
    _need(isinstance(optional, list), "MAYAK_OPTIONAL_CURSORS_INVALID")
    result: dict[str, tuple[str, int]] = {}
    for row in optional:
        _need(isinstance(row, dict), "MAYAK_OPTIONAL_CURSOR_ROW_INVALID")
        product = row.get("product_category")
        session = row.get("source_session_id")
        epoch = row.get("transport_epoch_id")
        cursor = row.get("next_cursor")
        _need(
            product in ("SPOT", "LINEAR")
            and session == expected_session
            and isinstance(epoch, str)
            and len(epoch) == 32
            and type(cursor) is int
            and cursor > 0,
            "MAYAK_OPTIONAL_CURSOR_IDENTITY_INVALID",
        )
        _need(product not in result, "MAYAK_OPTIONAL_CURSOR_DUPLICATE")
        result[str(product)] = (epoch, int(cursor))
    _need(set(result) == {"SPOT", "LINEAR"}, "MAYAK_OPTIONAL_CURSOR_SCOPE_INVALID")
    return result


def _active_source(connection: Any) -> tuple[str, str] | None:
    rows = connection.execute(
        """SELECT source_session_id,producer_release_commit
             FROM analytics.native_public_trade_source_sessions
            WHERE state='ACTIVE'
            ORDER BY subscribed_at DESC
            LIMIT 2"""
    ).fetchall()
    _need(len(rows) <= 1, "MULTIPLE_ACTIVE_SOURCE_EPOCHS")
    if not rows:
        return None
    session, release = rows[0]
    _need(
        isinstance(session, str)
        and len(session) == 32
        and isinstance(release, str)
        and len(release) == 40,
        "ACTIVE_SOURCE_IDENTITY_INVALID",
    )
    return str(session), str(release)


def _mayak_cursor_head(
    connection: Any,
) -> tuple[str, str, dict[str, tuple[str, int]]] | None:
    row = connection.execute(
        """SELECT source_session_id,producer_release_commit,state_checkpoint
             FROM mayak_v2.r11_consumer_commit
            WHERE consumer_id=%s""",
        (_CONSUMER_ID,),
    ).fetchone()
    if row is None:
        return None
    _need(
        isinstance(row, (tuple, list))
        and len(row) == 3
        and isinstance(row[0], str)
        and isinstance(row[1], str)
        and isinstance(row[2], bytes),
        "MAYAK_CONSUMER_HEAD_INVALID",
    )
    session, release, state = str(row[0]), str(row[1]), row[2]
    return (
        session,
        release,
        _decode_optional_cursors(
            state, expected_session=session, expected_release=release,
        ),
    )


def _create_retained_ref_tables(
    connection: Any, *, cutoff: datetime, pins: tuple[int, ...],
) -> None:
    """Materialize refs from recent + causal-pinned publications."""
    connection.execute(
        """CREATE TEMP TABLE retained_publication_ids(
               publication_id bigint PRIMARY KEY
           ) ON COMMIT DROP"""
    )
    connection.execute(
        """INSERT INTO retained_publication_ids(publication_id)
           SELECT publication_id
             FROM analytics.market_publications
            WHERE as_of_second >= %s OR publication_id=ANY(%s)""",
        (cutoff, list(pins)),
    )
    pinned_found = connection.execute(
        """SELECT count(*) FROM retained_publication_ids
            WHERE publication_id=ANY(%s)""",
        (list(pins),),
    ).fetchone()[0]
    _need(pinned_found == len(pins), "PINNED_PUBLICATION_MISSING")

    connection.execute(
        """CREATE TEMP TABLE retained_second_refs(
             source_session_id text,
             instrument_id text,
             observed_second timestamptz,
             PRIMARY KEY(source_session_id,instrument_id,observed_second)
           ) ON COMMIT DROP"""
    )
    connection.execute(
        """INSERT INTO retained_second_refs
           SELECT DISTINCT item.value->>'source_session_id',
                           item.value->>'instrument_id',
                           (item.value->>'observed_second')::timestamptz
             FROM analytics.market_publications p
             JOIN retained_publication_ids keep USING(publication_id)
             CROSS JOIN LATERAL jsonb_each(p.compact_second_refs) inst
             CROSS JOIN LATERAL jsonb_each(inst.value) item"""
    )
    connection.execute(
        """CREATE TEMP TABLE retained_forming_refs(
             forming_observation_id bigint PRIMARY KEY
           ) ON COMMIT DROP"""
    )
    connection.execute(
        """INSERT INTO retained_forming_refs
           SELECT DISTINCT
                  (item.value->>'forming_observation_id')::bigint
             FROM analytics.market_publications p
             JOIN retained_publication_ids keep USING(publication_id)
             CROSS JOIN LATERAL jsonb_each(p.forming_minute_refs) inst
             CROSS JOIN LATERAL jsonb_each(inst.value) item"""
    )
    connection.execute(
        """CREATE TEMP TABLE retained_rest_refs(
             rest_observation_id bigint PRIMARY KEY
           ) ON COMMIT DROP"""
    )
    connection.execute(
        """INSERT INTO retained_rest_refs
           SELECT DISTINCT
                  (item.value->>'rest_observation_id')::bigint
             FROM analytics.market_publications p
             JOIN retained_publication_ids keep USING(publication_id)
             CROSS JOIN LATERAL jsonb_each(p.rest_observation_refs) inst
             CROSS JOIN LATERAL jsonb_each(inst.value) item"""
    )
    connection.execute(
        """CREATE TEMP TABLE retained_raw_refs(
             source_session_id text,
             transport_epoch_id text,
             product_category text,
             source_frame_number bigint,
             PRIMARY KEY(
               source_session_id,transport_epoch_id,
               product_category,source_frame_number
             )
           ) ON COMMIT DROP"""
    )
    connection.execute(
        """INSERT INTO retained_raw_refs
           SELECT DISTINCT
                  item.value->>'source_session_id',
                  item.value->>'transport_epoch_id',
                  item.value->>'product_category',
                  (item.value->>'source_frame_number')::bigint
             FROM analytics.market_publications p
             JOIN retained_publication_ids keep USING(publication_id)
             CROSS JOIN LATERAL jsonb_each(p.original_optional_frame_refs) item"""
    )

    missing = {
        "seconds": connection.execute(
            """SELECT count(*)
                 FROM retained_second_refs r
                 LEFT JOIN analytics.public_market_source_seconds s
                   ON (s.source_session_id,s.instrument_id,s.observed_second)
                    =(r.source_session_id,r.instrument_id,r.observed_second)
                WHERE s.source_session_id IS NULL"""
        ).fetchone()[0],
        "forming": connection.execute(
            """SELECT count(*)
                 FROM retained_forming_refs r
                 LEFT JOIN analytics.public_forming_minute_observations f
                   USING(forming_observation_id)
                WHERE f.forming_observation_id IS NULL"""
        ).fetchone()[0],
        "rest": connection.execute(
            """SELECT count(*)
                 FROM retained_rest_refs r
                 LEFT JOIN analytics.public_market_rest_observations o
                   USING(rest_observation_id)
                WHERE o.rest_observation_id IS NULL"""
        ).fetchone()[0],
        "raw": connection.execute(
            """SELECT count(*)
                 FROM retained_raw_refs r
                 LEFT JOIN analytics.public_source_original_event_frames f
                   ON (f.source_session_id,f.transport_epoch_id,
                       f.product_category,f.source_frame_number)
                    =(r.source_session_id,r.transport_epoch_id,
                      r.product_category,r.source_frame_number)
                WHERE f.source_session_id IS NULL"""
        ).fetchone()[0],
    }
    _need(not any(int(v) for v in missing.values()), "RETAINED_JSON_REF_DANGLING")


def _delete_publications(
    connection: Any, *, cutoff: datetime, pins: tuple[int, ...], limit: int,
) -> int:
    return int(connection.execute(
        """WITH stale AS (
             SELECT publication_id
               FROM analytics.market_publications
              WHERE as_of_second < %s
                AND NOT (publication_id=ANY(%s))
              ORDER BY as_of_second,publication_id
              LIMIT %s
              FOR UPDATE SKIP LOCKED
           )
           DELETE FROM analytics.market_publications p
           USING stale
           WHERE p.publication_id=stale.publication_id""",
        (cutoff, list(pins), limit),
    ).rowcount)


def _delete_compact_facts(
    connection: Any, *, cutoff: datetime, limit: int,
) -> dict[str, int]:
    rows: dict[str, int] = {}
    rows[SECONDS] = int(connection.execute(
        """WITH stale AS (
             SELECT s.ctid
               FROM analytics.public_market_source_seconds s
              WHERE s.observed_second < %s
                AND NOT EXISTS (
                  SELECT 1 FROM retained_second_refs r
                   WHERE (r.source_session_id,r.instrument_id,r.observed_second)
                        =(s.source_session_id,s.instrument_id,s.observed_second)
                )
              ORDER BY s.observed_second
              LIMIT %s
              FOR UPDATE SKIP LOCKED
           )
           DELETE FROM analytics.public_market_source_seconds target
           USING stale WHERE target.ctid=stale.ctid""",
        (cutoff, limit),
    ).rowcount)
    rows[FORMING] = int(connection.execute(
        """WITH stale AS (
             SELECT f.ctid
               FROM analytics.public_forming_minute_observations f
              WHERE f.received_at < %s
                AND NOT EXISTS (
                  SELECT 1 FROM retained_forming_refs r
                   WHERE r.forming_observation_id=f.forming_observation_id
                )
              ORDER BY f.received_at,f.forming_observation_id
              LIMIT %s
              FOR UPDATE SKIP LOCKED
           )
           DELETE FROM analytics.public_forming_minute_observations target
           USING stale WHERE target.ctid=stale.ctid""",
        (cutoff, limit),
    ).rowcount)
    rows[REST] = int(connection.execute(
        """WITH stale AS (
             SELECT o.ctid
               FROM analytics.public_market_rest_observations o
              WHERE o.received_at < %s
                AND NOT EXISTS (
                  SELECT 1 FROM retained_rest_refs r
                   WHERE r.rest_observation_id=o.rest_observation_id
                )
              ORDER BY o.received_at,o.rest_observation_id
              LIMIT %s
              FOR UPDATE SKIP LOCKED
           )
           DELETE FROM analytics.public_market_rest_observations target
           USING stale WHERE target.ctid=stale.ctid""",
        (cutoff, limit),
    ).rowcount)
    return rows


def _raw_delete_predicate(
    active: tuple[str, str] | None,
    head: tuple[str, str, dict[str, tuple[str, int]]] | None,
    cutoff: datetime,
) -> tuple[str, list[object]]:
    """Bound raw originals by age; consumed frames may expire even sooner."""
    if active is None:
        return "TRUE", []
    active_session, active_release = active
    clauses = [
        "r.source_session_id <> %s",
        "(r.source_session_id=%s AND r.committed_at < %s)",
    ]
    params: list[object] = [active_session, active_session, cutoff]
    if head is not None and head[0:2] == (active_session, active_release):
        cursors = head[2]
        for product in ("SPOT", "LINEAR"):
            epoch, cursor = cursors[product]
            clauses.append(
                "(r.source_session_id=%s AND r.transport_epoch_id=%s "
                "AND r.product_category=%s AND r.source_frame_number < %s)"
            )
            params.extend((active_session, epoch, product, cursor))
    return "(" + " OR ".join(clauses) + ")", params


def _delete_raw(
    connection: Any, *,
    active: tuple[str, str] | None,
    head: tuple[str, str, dict[str, tuple[str, int]]] | None,
    cutoff: datetime,
    limit: int,
) -> int:
    predicate, params = _raw_delete_predicate(active, head, cutoff)
    return int(connection.execute(
        f"""WITH stale AS (
             SELECT r.ctid
               FROM analytics.public_source_original_event_frames r
              WHERE {predicate}
                AND NOT EXISTS (
                  SELECT 1 FROM retained_raw_refs keep
                   WHERE (keep.source_session_id,keep.transport_epoch_id,
                          keep.product_category,keep.source_frame_number)
                        =(r.source_session_id,r.transport_epoch_id,
                          r.product_category,r.source_frame_number)
                )
              ORDER BY r.committed_at,r.source_frame_number
              LIMIT %s
              FOR UPDATE SKIP LOCKED
           )
           DELETE FROM analytics.public_source_original_event_frames target
           USING stale WHERE target.ctid=stale.ctid""",
        tuple(params + [limit]),
    ).rowcount)


def _candidate_counts(
    connection: Any, *, cutoff: datetime, pins: tuple[int, ...],
) -> dict[str, int]:
    """Pure SELECT planning; no TEMP/DDL so --check stays transaction read-only."""
    keep_publications = (
        "SELECT publication_id FROM analytics.market_publications "
        "WHERE as_of_second >= %s OR publication_id=ANY(%s)"
    )
    params = (cutoff, list(pins))
    counts: dict[str, int] = {}
    counts[PUBLICATIONS] = int(connection.execute(
        """SELECT count(*) FROM analytics.market_publications
            WHERE as_of_second < %s AND NOT (publication_id=ANY(%s))""",
        params,
    ).fetchone()[0])
    counts[SECONDS] = int(connection.execute(
        f"""WITH keep AS ({keep_publications}), refs AS (
             SELECT DISTINCT item.value->>'source_session_id' sid,
                    item.value->>'instrument_id' iid,
                    (item.value->>'observed_second')::timestamptz sec
               FROM analytics.market_publications p
               JOIN keep USING(publication_id)
               CROSS JOIN LATERAL jsonb_each(p.compact_second_refs) inst
               CROSS JOIN LATERAL jsonb_each(inst.value) item
           )
           SELECT count(*) FROM analytics.public_market_source_seconds s
            WHERE s.observed_second < %s
              AND NOT EXISTS (
                SELECT 1 FROM refs r
                 WHERE (r.sid,r.iid,r.sec)
                      =(s.source_session_id,s.instrument_id,s.observed_second))""",
        params + (cutoff,),
    ).fetchone()[0])
    counts[FORMING] = int(connection.execute(
        f"""WITH keep AS ({keep_publications}), refs AS (
             SELECT DISTINCT
                    (item.value->>'forming_observation_id')::bigint id
               FROM analytics.market_publications p
               JOIN keep USING(publication_id)
               CROSS JOIN LATERAL jsonb_each(p.forming_minute_refs) inst
               CROSS JOIN LATERAL jsonb_each(inst.value) item
           )
           SELECT count(*)
             FROM analytics.public_forming_minute_observations f
            WHERE f.received_at < %s
              AND NOT EXISTS (
                SELECT 1 FROM refs r
                 WHERE r.id=f.forming_observation_id)""",
        params + (cutoff,),
    ).fetchone()[0])
    counts[REST] = int(connection.execute(
        f"""WITH keep AS ({keep_publications}), refs AS (
             SELECT DISTINCT
                    (item.value->>'rest_observation_id')::bigint id
               FROM analytics.market_publications p
               JOIN keep USING(publication_id)
               CROSS JOIN LATERAL jsonb_each(p.rest_observation_refs) inst
               CROSS JOIN LATERAL jsonb_each(inst.value) item
           )
           SELECT count(*)
             FROM analytics.public_market_rest_observations o
            WHERE o.received_at < %s
              AND NOT EXISTS (
                SELECT 1 FROM refs r
                 WHERE r.id=o.rest_observation_id)""",
        params + (cutoff,),
    ).fetchone()[0])
    active = _active_source(connection)
    head = _mayak_cursor_head(connection)
    predicate, raw_params = _raw_delete_predicate(active, head, cutoff)
    counts[RAW] = int(connection.execute(
        f"""WITH keep AS ({keep_publications}), refs AS (
             SELECT DISTINCT item.value->>'source_session_id' sid,
                    item.value->>'transport_epoch_id' eid,
                    item.value->>'product_category' product,
                    (item.value->>'source_frame_number')::bigint frame
               FROM analytics.market_publications p
               JOIN keep USING(publication_id)
               CROSS JOIN LATERAL jsonb_each(
                    p.original_optional_frame_refs) item
           )
           SELECT count(*)
             FROM analytics.public_source_original_event_frames r
            WHERE {predicate}
              AND NOT EXISTS (
                SELECT 1 FROM refs keep
                 WHERE (keep.sid,keep.eid,keep.product,keep.frame)
                      =(r.source_session_id,r.transport_epoch_id,
                        r.product_category,r.source_frame_number))""",
        params + tuple(raw_params),
    ).fetchone()[0])
    return counts

def collect_check(connection: Any, cutoff: datetime) -> Result:
    pins = _publication_fk_pins(connection)
    rows = _candidate_counts(connection, cutoff=cutoff, pins=pins)
    size = int(
        connection.execute("SELECT pg_database_size(current_database())")
        .fetchone()[0]
    )
    return Result("CHECK", cutoff.isoformat(), rows, size)


def run_apply(connection: Any, cutoff: datetime, limit: int) -> Result:
    if sys.platform != "linux":
        raise RuntimeError("MARKET_SOURCE_RETENTION_LINUX_REQUIRED")
    if os.geteuid() != _postgres_unix_uid():
        raise RuntimeError("MARKET_SOURCE_RETENTION_POSTGRES_UNIX_ACTOR_REQUIRED")
    rows: dict[str, int] = {}
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout='3000ms'")
        connection.execute("SET LOCAL statement_timeout='60000ms'")
        identity = connection.execute(
            "SELECT session_user,current_user,current_database()"
        ).fetchone()
        if identity != ("postgres", "postgres", "cripta"):
            raise RuntimeError(
                "MARKET_SOURCE_RETENTION_POSTGRES_DB_ROLE_REQUIRED"
            )
        got = connection.execute(
            "SELECT pg_try_advisory_xact_lock("
            "hashtext('cripta-market-source-hot-retention-v1'))"
        ).fetchone()[0]
        if got is not True:
            raise RuntimeError("MARKET_SOURCE_RETENTION_JOB_ALREADY_RUNNING")

        pins = _publication_fk_pins(connection)
        rows[PUBLICATIONS] = _delete_publications(
            connection, cutoff=cutoff, pins=pins, limit=limit,
        )
        _create_retained_ref_tables(connection, cutoff=cutoff, pins=pins)
        rows.update(
            _delete_compact_facts(connection, cutoff=cutoff, limit=limit)
        )
        rows[RAW] = _delete_raw(
            connection,
            active=_active_source(connection),
            head=_mayak_cursor_head(connection),
            cutoff=cutoff,
            limit=limit,
        )
        size = int(
            connection.execute("SELECT pg_database_size(current_database())")
            .fetchone()[0]
        )
    return Result("APPLIED", cutoff.isoformat(), rows, size)


def vacuum_reusable_pages(connection: Any, rows: dict[str, int]) -> None:
    """Normal VACUUM only: make deleted HOT pages reusable, never rewrite."""
    from psycopg import sql  # type: ignore[import-not-found]

    for table in (RAW, SECONDS, FORMING, REST, PUBLICATIONS):
        if rows.get(table, 0) <= 0:
            continue
        schema, name = table.split(".", 1)
        connection.execute(
            sql.SQL("VACUUM (ANALYZE) {}.{}").format(
                sql.Identifier(schema), sql.Identifier(name)
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bounded current MarketSource hot retention"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--batch-limit", type=int, default=DEFAULT_BATCH_LIMIT,
    )
    args = parser.parse_args()
    if not 1 <= args.batch_limit <= MAX_BATCH_LIMIT:
        raise SystemExit("MARKET_SOURCE_RETENTION_BATCH_LIMIT_INVALID")

    import psycopg  # type: ignore[import-not-found]

    role = "postgres" if args.apply else "cripta"
    dsn = (
        f"dbname=cripta user={role} host=/var/run/postgresql "
        "application_name=market-source-hot-retention"
    )
    try:
        with psycopg.connect(
            dsn, autocommit=True, connect_timeout=5,
        ) as connection:
            cutoff = connection.execute(
                "SELECT clock_timestamp() - "
                "make_interval(mins => %s)",
                (SOURCE_RETENTION_MINUTES,),
            ).fetchone()[0]
            if args.apply:
                done = run_apply(connection, cutoff, args.batch_limit)
                vacuum_reusable_pages(connection, done.rows)
            else:
                connection.execute("BEGIN READ ONLY")
                try:
                    done = collect_check(connection, cutoff)
                finally:
                    connection.rollback()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error_class": type(exc).__name__,
                    "reason": str(exc)[:240],
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": done.mode,
                "cutoff_utc": done.cutoff_utc,
                "rows": done.rows,
                "database_bytes": done.database_bytes,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
