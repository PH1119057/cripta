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
    protected_json_refs: dict[str, dict[str, int]] = {}
    for field, table, pk in (
        (
            "forming_minute_refs",
            "analytics.public_forming_minute_observations",
            "forming_observation_id",
        ),
        (
            "rest_observation_refs",
            "analytics.public_market_rest_observations",
            "rest_observation_id",
        ),
    ):
        query = sql.SQL(
            "WITH refs AS ("
            " SELECT (item.value->>{key})::bigint AS uid"
            " FROM analytics.market_publications p"
            " CROSS JOIN LATERAL jsonb_each(p.{field}) inst"
            " CROSS JOIN LATERAL jsonb_each(inst.value) item"
            " WHERE p.publication_id=ANY(%s)"
            ")"
            " SELECT count(*),count(DISTINCT refs.uid),"
            " count(*) FILTER(WHERE target.{pk} IS NULL)"
            " FROM refs LEFT JOIN {table} target ON(target.{pk}=refs.uid)"
        ).format(
            key=sql.Literal(pk),
            field=sql.Identifier(field),
            pk=sql.Identifier(pk),
            table=sql.Identifier(*table.split(".")),
        )
        cursor.execute(query, (sorted(ids),))
        total, unique, not_found = cursor.fetchone()
        if not_found:
            raise RuntimeError("P90_PUBLICATION_JSON_REF_MISSING")
        protected_json_refs[field] = {
            "refs": int(total),
            "unique_rows": int(unique),
            "missing": int(not_found),
        }
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
        "protected_json_refs": protected_json_refs,
        "other_json_or_consumer_pins": "NOT_AUDITED",
    }


def _possible_retention_plan(
    cursor: Any,
    pins: dict[str, Any],
    tables: list[dict[str, Any]],
) -> dict[str, Any]:
    """Exact provisional candidates, *not* permission to delete originals.

    Every immutable publication retained by age OR a P90 SQL FK keeps all
    referenced compact and forming facts, even beyond the 24-hour window.
    Every row still requires independent consumer and recovery proof.
    """
    ids = pins["pinned_publication_ids"]
    by_table = {table["table"]: table for table in tables}
    cutoff_seconds = 24
    cursor.execute("SELECT now()")
    snapshot_at = cursor.fetchone()[0]
    cursor.execute(
        """SELECT count(*) FROM analytics.market_publications
            WHERE as_of_second < now()-make_interval(hours=>24)
              AND NOT (publication_id=ANY(%s))""",
        (ids,),
    )
    publication_candidates = int(cursor.fetchone()[0])

    cursor.execute(
        """WITH kept AS (
            SELECT compact_second_refs
              FROM analytics.market_publications
             WHERE as_of_second >= now()-make_interval(hours=>24)
                OR publication_id=ANY(%s)
           ), refs AS (
            SELECT item.value->>'source_session_id' AS sid,
                   item.value->>'instrument_id' AS iid,
                   (item.value->>'observed_second')::timestamptz AS sec
              FROM kept p
             CROSS JOIN LATERAL jsonb_each(p.compact_second_refs) inst
             CROSS JOIN LATERAL jsonb_each(inst.value) item
           )
           SELECT count(*),
                  count(*) FILTER(WHERE s.source_session_id IS NULL),
                  count(DISTINCT (refs.sid,refs.iid,refs.sec))
                    FILTER(WHERE refs.sec <
                           now()-make_interval(hours=>24))
             FROM refs
             LEFT JOIN analytics.public_market_source_seconds s
               ON (s.source_session_id,s.instrument_id,s.observed_second)
                = (refs.sid,refs.iid,refs.sec)""",
        (ids,),
    )
    compact_refs, missing_compact, old_compact = cursor.fetchone()
    if missing_compact:
        raise RuntimeError("RETAINED_PUBLICATION_COMPACT_DANGLING_REF")

    cursor.execute(
        """WITH kept AS (
            SELECT forming_minute_refs
              FROM analytics.market_publications
             WHERE as_of_second >= now()-make_interval(hours=>24)
                OR publication_id=ANY(%s)
           ), refs AS (
            SELECT (item.value->>'forming_observation_id')::bigint AS rid
              FROM kept p
             CROSS JOIN LATERAL jsonb_each(p.forming_minute_refs) inst
             CROSS JOIN LATERAL jsonb_each(inst.value) item
           )
           SELECT count(*),
                  count(*) FILTER(WHERE f.forming_observation_id IS NULL),
                  count(DISTINCT refs.rid)
                     FILTER(WHERE f.received_at <
                            now()-make_interval(hours=>24))
             FROM refs
             LEFT JOIN analytics.public_forming_minute_observations f
               ON f.forming_observation_id=refs.rid""",
        (ids,),
    )
    forming_refs, missing_forming, old_forming = cursor.fetchone()
    if missing_forming:
        raise RuntimeError("RETAINED_PUBLICATION_FORMING_DANGLING_REF")

    old_seconds = by_table[
        "analytics.public_market_source_seconds"
    ]["older_than_cutoff_rows"]
    old_forming_total = by_table[
        "analytics.public_forming_minute_observations"
    ]["older_than_cutoff_rows"]
    if int(old_compact) > old_seconds:
        raise RuntimeError("RETENTION_SECOND_PIN_COUNT_INVALID")
    if int(old_forming) > old_forming_total:
        raise RuntimeError("RETENTION_FORMING_PIN_COUNT_INVALID")
    return {
        "snapshot_at_utc": snapshot_at.isoformat(),
        "retention_hours": cutoff_seconds,
        "old_unpinned_publication_candidates": publication_candidates,
        "old_unreferenced_second_candidates": (
            old_seconds - int(old_compact)
        ),
        "old_unreferenced_forming_candidates": (
            old_forming_total - int(old_forming)
        ),
        "retained_publication_compact_ref_count": int(compact_refs),
        "retained_publication_forming_ref_count": int(forming_refs),
        "retained_publication_missing_refs": 0,
        "capacity_financial_retention_gate": "NOT_PROVEN",
        "candidate_status": "PRELIMINARY_ONLY_NO_DELETE_APPROVAL",
        "actual_reclaim_bytes": None,
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
        potential = _possible_retention_plan(cursor, pins, tables)
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
        "provisional_retention_candidates": potential,
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
    import pwd

    if pwd.getpwuid(os.geteuid()).pw_name != ROLE:
        raise SystemExit("STORAGE_CHECK_UNIX_CRIPTA_REQUIRED")
    import psycopg

    with psycopg.connect(
        "dbname=cripta user=cripta host=/var/run/postgresql",
        options="-c default_transaction_read_only=on "
        "-c statement_timeout=40000",
    ) as connection:
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.read_only = True
        result = inspect(connection)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
