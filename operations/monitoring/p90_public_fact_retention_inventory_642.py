#!/usr/bin/env python3
"""Issue #642: bounded, read-only P90 public-fact retention inventory.

No DELETE, UPDATE, VACUUM, backup, temporary files, or trading actions.
The result is evidence for a *separate* reviewed R11/R12 release, never an
authorization to prune immutable MarketPublication or P90 causal facts.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
from typing import Any

TARGETS: tuple[tuple[str, str, int], ...] = (
    ("analytics.public_market_source_seconds", "observed_second", 24),
    ("analytics.market_publications", "as_of_second", 24),
    ("analytics.public_forming_minute_observations", "received_at", 24),
    ("dispatcher_v2.trading_capacity_snapshots", "observed_at", 168),
)
MAX_PINNED_PUBLICATIONS = 10_000
DATABASE = "cripta"
ROLE = "cripta"


def _one_table(cursor: Any, table: str, column: str, hours: int) -> dict[str, Any]:
    from psycopg import sql

    if (table, column, hours) not in TARGETS:
        raise RuntimeError("STORAGE_TARGET_NOT_ALLOWLISTED")
    name = sql.Identifier(*table.split("."))
    field = sql.Identifier(column)
    query = sql.SQL(
        "SELECT count(*),"
        " count(*) FILTER (WHERE {col} < now()-make_interval(hours=>%s)),"
        " min({col}),max({col}) FROM {table}"
    ).format(col=field, table=name)
    cursor.execute(query, (hours,))
    total, stale, first, last = cursor.fetchone()
    cursor.execute(
        """SELECT pg_total_relation_size(c.oid),pg_get_userbyid(c.relowner),
                  has_table_privilege(current_user,c.oid,'DELETE')
             FROM pg_class c WHERE c.oid=%s::regclass""",
        (table,),
    )
    meta = cursor.fetchone()
    if meta is None:
        raise RuntimeError("STORAGE_TABLE_MISSING")
    size, owner, can_delete = meta
    return {
        "table": table,
        "time_column": column,
        "cutoff_hours": hours,
        "rows": int(total),
        "older_than_cutoff_rows": int(stale),
        "oldest_at": first.isoformat() if first else None,
        "newest_at": last.isoformat() if last else None,
        "relation_bytes": int(size),
        "owner": owner,
        "runtime_delete_privilege": bool(can_delete),
        "deletable_rows": None,
        "physical_reclaim_bytes": None,
    }


def _publication_pins(cursor: Any) -> dict[str, Any]:
    """All *single-column* SQL FK references, not an owner-chosen sample."""
    from psycopg import sql

    cursor.execute(
        """SELECT f.conrelid::regclass::text,a.attname,
                  cardinality(f.conkey),cardinality(f.confkey)
             FROM pg_constraint f
             JOIN LATERAL unnest(f.conkey) AS key(attnum) ON true
             JOIN pg_attribute a ON a.attrelid=f.conrelid
                                 AND a.attnum=key.attnum
            WHERE f.contype='f'
              AND f.confrelid=%s::regclass
         ORDER BY 1,2""",
        ("analytics.market_publications",),
    )
    references = cursor.fetchall()
    if not references:
        raise RuntimeError("P90_PUBLICATION_FK_INVENTORY_EMPTY")
    ids: set[int] = set()
    names: list[dict[str, str]] = []
    for table, column, child_cols, parent_cols in references:
        if child_cols != 1 or parent_cols != 1:
            raise RuntimeError("P90_PUBLICATION_COMPOSITE_FK_UNHANDLED")
        if len(table.split(".")) != 2:
            raise RuntimeError("P90_PUBLICATION_SCHEMA_IDENTITY_INVALID")
        query = sql.SQL(
            "SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"
        ).format(
            table=sql.Identifier(*table.split(".")),
            column=sql.Identifier(column),
        )
        cursor.execute(query)
        ids.update(int(value) for (value,) in cursor.fetchall())
        if len(ids) > MAX_PINNED_PUBLICATIONS:
            raise RuntimeError("P90_PUBLICATION_PIN_BUDGET_EXCEEDED")
        names.append({"table": table, "column": column})
    if not ids:
        raise RuntimeError("P90_PUBLICATION_NO_PINNED_IDS")
    cursor.execute(
        """SELECT publication_id,as_of_second
             FROM analytics.market_publications
            WHERE publication_id=ANY(%s)
         ORDER BY publication_id""",
        (sorted(ids),),
    )
    records = cursor.fetchall()
    if len(records) != len(ids):
        raise RuntimeError("P90_PUBLICATION_FK_ORPHAN")
    cursor.execute(
        """WITH refs AS (
             SELECT p.publication_id,
                    item.value->>'source_session_id' AS session_id,
                    item.value->>'instrument_id' AS instrument_id,
                    (item.value->>'observed_second')::timestamptz
                        AS observed_second
               FROM analytics.market_publications p
              CROSS JOIN LATERAL jsonb_each(p.compact_second_refs)
                   AS instrument(key,value)
              CROSS JOIN LATERAL jsonb_each(instrument.value)
                   AS item(key,value)
              WHERE p.publication_id=ANY(%s)
           )
           SELECT count(*),
                  count(*) FILTER (WHERE seconds.source_session_id IS NULL),
                  count(DISTINCT (
                      refs.session_id,refs.instrument_id,refs.observed_second
                  ))
             FROM refs
             LEFT JOIN analytics.public_market_source_seconds seconds
               ON (seconds.source_session_id,seconds.instrument_id,
                   seconds.observed_second)
                = (refs.session_id,refs.instrument_id,
                   refs.observed_second)""",
        (sorted(ids),),
    )
    refs, missing, distinct_seconds = cursor.fetchone()
    if missing:
        raise RuntimeError("P90_PUBLICATION_COMPACT_REF_MISSING")
    return {
        "inbound_fk_columns": names,
        "pinned_publication_ids": sorted(ids),
        "pinned_publication_times": [
            {"id": int(identifier), "as_of_second": at.isoformat()}
            for identifier, at in records
        ],
        "pinned_compact_refs": int(refs),
        "missing_compact_refs": int(missing),
        "unique_compact_seconds": int(distinct_seconds),
        "other_json_or_consumer_pins": "NOT_AUDITED",
    }


def inspect(connection: Any) -> dict[str, Any]:
    """One read-only snapshot: no change to trading data or transaction state."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_user,current_database(),pg_is_in_recovery(),"
            " current_setting('transaction_read_only')"
        )
        actor, database, recovery, readonly = cursor.fetchone()
        if (
            actor != ROLE or database != DATABASE
            or recovery or readonly != "on"
        ):
            raise RuntimeError("STORAGE_READONLY_IDENTITY_GATE_FAILED")
        tables = [
            _one_table(cursor, table, col, hours)
            for table, col, hours in TARGETS
        ]
        pins = _publication_pins(cursor)
        cursor.execute(
            """SELECT count(*)
                 FROM dispatcher_v2.trading_capacity_snapshots capacity
                WHERE capacity.observed_at <
                      now()-make_interval(hours=>168)
                  AND NOT EXISTS (
                      SELECT 1 FROM research_context.dispatcher_v2_event_links
                       WHERE capacity_snapshot_id=capacity.capacity_snapshot_id
                  )"""
        )
        detached_capacity = int(cursor.fetchone()[0])
    return {
        "status": "READ_ONLY_EVIDENCE_ONLY",
        "checked_actor": actor,
        "database": database,
        "recovery": recovery,
        "transaction_read_only": readonly,
        "tables": tables,
        "publication_fk_pins": pins,
        "capacity_old_unreferenced_by_direct_fk": detached_capacity,
        "required_before_apply": [
            "source_and_loaded_consumer_inventory",
            "full_p90_and_json_reference_closure",
            "owner_reviewed_retention_canon_and_release",
            "financial_and_recovery_gates_for_affected_data",
            "exact_postgres_actor_and_maintenance_lock_budget",
            "R11_post_count_and_R12_independent_physical_QC",
        ],
        "sql_mutation": "NOT_IMPLEMENTED",
        "backup_created": False,
        "prepared_for_delete": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True)
    args = parser.parse_args()
    if not args.check or os.name != "posix":
        raise SystemExit("STORAGE_CHECK_LINUX_ONLY")
    if pwd.getpwuid(os.geteuid()).pw_name != ROLE:
        raise SystemExit("STORAGE_CHECK_UNIX_CRIPTA_REQUIRED")
    import psycopg

    with psycopg.connect(
        "dbname=cripta user=cripta host=/var/run/postgresql",
        options="-c default_transaction_read_only=on "
        "-c statement_timeout=40000",
    ) as connection:
        connection.read_only = True
        result = inspect(connection)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
