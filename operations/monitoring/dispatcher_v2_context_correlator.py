#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

DSN = os.environ.get("CRIPTA_DSN", "dbname=cripta user=cripta host=/var/run/postgresql")
STATUS_PATH = Path(
    os.environ.get(
        "CRIPTA_DISPATCHER_V2_CORRELATOR_STATUS",
        "/var/lib/cripta/dispatcher_v2/context_correlator_status.json",
    )
)
SOURCE_COMMIT = os.environ.get("DISPATCHER_V2_CORRELATOR_SOURCE_COMMIT", "UNSPECIFIED")
CORRELATOR_VERSION = "dispatcher-v2-context-correlator-v1"

EVENT_SOURCE_SQL = r"""
SELECT 'SIGNAL'::text AS event_type,
       o.signal_id::text AS reference_id,
       to_timestamp(o.signal_at_epoch_ms / 1000.0) AS occurred_at,
       o.signal_id::text AS signal_id,
       NULL::text AS strategy_attempt_id,
       NULL::text AS position_id,
       NULL::text AS trade_id,
       o.symbol::text AS symbol,
       o.direction::text AS side,
       jsonb_build_object(
           'source_table','monitoring.opportunities',
           'decision',o.decision,
           'state',o.state
       ) AS event_payload
FROM monitoring.opportunities o

UNION ALL

SELECT 'ENTRY_DECISION',
       e.signal_id,
       to_timestamp(e.decided_at_epoch_ms / 1000.0),
       e.signal_id,
       NULL::text,
       NULL::text,
       NULL::text,
       e.symbol,
       e.direction,
       jsonb_build_object(
           'source_table','runtime.entry_decisions',
           'decision',e.decision,
           'reason',e.reason,
           'entry_policy',e.entry_policy,
           'policy_version',e.policy_version,
           'settings_version',e.settings_version
       )
FROM runtime.entry_decisions e

UNION ALL

SELECT 'TRADE_COMMAND',
       c.command_id,
       to_timestamp(c.requested_at_epoch_ms / 1000.0),
       COALESCE(NULLIF(c.payload_json::jsonb ->> 'signal_id',''), p.signal_id),
       NULL::text,
       p.position_id,
       p.trade_id,
       c.symbol,
       c.payload_json::jsonb ->> 'side',
       jsonb_build_object(
           'source_table','runtime.trade_commands',
           'command_type',c.command_type,
           'processing_state',c.state
       )
FROM runtime.trade_commands c
LEFT JOIN runtime.position_ownership p
  ON p.entry_command_id=c.command_id
  OR p.entry_command_id=NULLIF(c.payload_json::jsonb ->> 'entry_command_id','')

UNION ALL

SELECT 'EXECUTION',
       x.exec_id,
       to_timestamp(COALESCE(x.exec_time_ms,x.received_at_epoch_ms) / 1000.0),
       COALESCE(p.signal_id, NULLIF(c.payload_json::jsonb ->> 'signal_id','')),
       NULL::text,
       p.position_id,
       p.trade_id,
       x.symbol,
       x.side,
       jsonb_build_object(
           'source_table','runtime.executions',
           'order_id',x.order_id,
           'order_link_id',x.order_link_id,
           'exec_type',x.payload_json::jsonb ->> 'execType'
       )
FROM runtime.executions x
LEFT JOIN runtime.trade_commands c ON c.command_id=x.order_link_id
LEFT JOIN runtime.position_ownership p
  ON p.execution_ids ? x.exec_id
  OR p.exit_execution_ids ? x.exec_id
  OR p.entry_command_id=c.command_id

UNION ALL

SELECT 'POSITION_LIFECYCLE',
       l.lifecycle_event_id,
       l.occurred_at,
       p.signal_id,
       NULL::text,
       l.position_id,
       l.trade_id,
       p.symbol,
       p.side,
       jsonb_build_object(
           'source_table','runtime.position_lifecycle_events',
           'event_type',l.event_type
       )
FROM runtime.position_lifecycle_events l
LEFT JOIN runtime.position_ownership p ON p.position_id=l.position_id

UNION ALL

SELECT 'SUPERVISOR_TRANSITION',
       s.id::text,
       to_timestamp(s.observed_at_epoch_ms / 1000.0),
       p.signal_id,
       NULL::text,
       s.position_id,
       p.trade_id,
       s.symbol,
       p.side,
       jsonb_build_object(
           'source_table','supervisor.transitions',
           'old_state',s.old_state,
           'new_state',s.new_state,
           'reason',s.reason
       )
FROM supervisor.transitions s
LEFT JOIN runtime.position_ownership p ON p.position_id=s.position_id

UNION ALL

SELECT 'EXIT_DECISION',
       d.decision_id,
       d.decided_at,
       p.signal_id,
       NULL::text,
       d.position_id,
       p.trade_id,
       d.symbol,
       p.side,
       jsonb_build_object(
           'source_table','supervisor.exit_decisions',
           'internal_reason',d.internal_reason,
           'close_command_id',d.close_command_id
       )
FROM supervisor.exit_decisions d
LEFT JOIN runtime.position_ownership p ON p.position_id=d.position_id
"""


def verify_database() -> None:
    with psycopg.connect(DSN) as connection:
        row = connection.execute(
            "SELECT to_regclass('research_context.dispatcher_v2_event_links')"
        ).fetchone()
        if row is None or row[0] is None:
            raise RuntimeError("dispatcher_v2 event link migration is not installed")


def correlate(*, batch_limit: int) -> int:
    query = f"""
    WITH source AS ({EVENT_SOURCE_SQL}),
    v2_floor AS (
        SELECT min(observed_at) AS first_v2_at
        FROM dispatcher_v2.global_market_contexts
    ),
    eligible AS (
        SELECT source.*
        FROM source CROSS JOIN v2_floor
        WHERE v2_floor.first_v2_at IS NOT NULL
          AND source.occurred_at >= v2_floor.first_v2_at
          AND NOT EXISTS (
              SELECT 1
              FROM research_context.dispatcher_v2_event_links existing
              WHERE existing.event_type=source.event_type
                AND existing.reference_id=source.reference_id
          )
        ORDER BY source.occurred_at,source.event_type,source.reference_id
        LIMIT %s
    )
    INSERT INTO research_context.dispatcher_v2_event_links(
        event_type,reference_id,occurred_at,signal_id,strategy_attempt_id,
        position_id,trade_id,symbol,side,
        global_context_id,global_observed_at,global_age_seconds,
        coin_context_id,coin_observed_at,coin_age_seconds,
        capacity_snapshot_id,capacity_observed_at,capacity_age_seconds,
        observed_context_mode,consumed_context_mode,link_quality,
        event_payload,provenance,trading_effect)
    SELECT e.event_type,e.reference_id,e.occurred_at,e.signal_id,e.strategy_attempt_id,
           e.position_id,e.trade_id,e.symbol,e.side,
           g.global_context_id,g.observed_at,
           extract(epoch FROM (e.occurred_at-g.observed_at)),
           c.coin_context_id,c.observed_at,
           CASE WHEN c.coin_context_id IS NULL THEN NULL
                ELSE extract(epoch FROM (e.occurred_at-c.observed_at)) END,
           a.capacity_snapshot_id,a.observed_at,
           CASE WHEN a.capacity_snapshot_id IS NULL THEN NULL
                ELSE extract(epoch FROM (e.occurred_at-a.observed_at)) END,
           'OBSERVED_CONTEXT','NOT_CONSUMED',
           CASE
             WHEN c.coin_context_id IS NOT NULL AND a.capacity_snapshot_id IS NOT NULL
               THEN 'GLOBAL_COIN_CAPACITY_CAUSAL_PRIOR'
             WHEN c.coin_context_id IS NOT NULL
               THEN 'GLOBAL_COIN_CAUSAL_PRIOR_NO_CAPACITY'
             WHEN a.capacity_snapshot_id IS NOT NULL
               THEN 'GLOBAL_CAPACITY_CAUSAL_PRIOR_NO_COIN'
             ELSE 'GLOBAL_ONLY_CAUSAL_PRIOR'
           END,
           e.event_payload,
           jsonb_build_object(
               'source','dispatcher_v2_context_correlator',
               'method','latest_context_not_after_event',
               'source_commit',%s::text,
               'coin_same_global_context',true,
               'strategy_attempt_id_status','NOT_AVAILABLE_CURRENT_RUNTIME',
               'trading_command',false
           ),
           'NONE'
    FROM eligible e
    LEFT JOIN LATERAL (
        SELECT global_context_id,observed_at
        FROM dispatcher_v2.global_market_contexts
        WHERE observed_at <= e.occurred_at
        ORDER BY observed_at DESC
        LIMIT 1
    ) g ON true
    LEFT JOIN LATERAL (
        SELECT coin_context_id,observed_at
        FROM dispatcher_v2.coin_market_contexts
        WHERE global_context_id=g.global_context_id
          AND symbol=e.symbol
        LIMIT 1
    ) c ON true
    LEFT JOIN LATERAL (
        SELECT capacity_snapshot_id,observed_at
        FROM dispatcher_v2.trading_capacity_snapshots
        WHERE observed_at <= e.occurred_at
        ORDER BY observed_at DESC
        LIMIT 1
    ) a ON true
    WHERE g.global_context_id IS NOT NULL
    ON CONFLICT(event_type,reference_id) DO NOTHING
    """
    with psycopg.connect(DSN) as connection:
        cursor = connection.execute(query, (batch_limit, SOURCE_COMMIT))
        inserted = cursor.rowcount
        connection.commit()
    return inserted


def status_snapshot(*, inserted: int) -> dict[str, Any]:
    with psycopg.connect(DSN) as connection:
        total_row = connection.execute(
            "SELECT count(*) FROM research_context.dispatcher_v2_event_links"
        ).fetchone()
        if total_row is None:
            raise RuntimeError("dispatcher_v2 event link count query returned no row")
        total = int(total_row[0])
        rows = connection.execute(
            """SELECT event_type,count(*)
               FROM research_context.dispatcher_v2_event_links
               GROUP BY event_type ORDER BY event_type"""
        ).fetchall()
        last = connection.execute(
            "SELECT max(occurred_at) FROM research_context.dispatcher_v2_event_links"
        ).fetchone()
    return {
        "status": "RUNNING",
        "correlator_version": CORRELATOR_VERSION,
        "source_commit": SOURCE_COMMIT,
        "trading_effect": "NONE",
        "observed_context": "YES",
        "consumed_context": "NO",
        "updated_at": datetime.now(UTC).isoformat(),
        "cycle_inserted": inserted,
        "linked_total": total,
        "by_event_type": {str(row[0]): int(row[1]) for row in rows},
        "latest_event_at": None if last is None or last[0] is None else last[0].isoformat(),
    }


def write_status(value: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(STATUS_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatcher V2 causal observed-context correlator")
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--batch-limit", type=int, default=1000)
    args = parser.parse_args()
    if args.poll_seconds <= 0 or args.batch_limit <= 0:
        raise ValueError("poll-seconds and batch-limit must be positive")
    verify_database()
    stopped = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        inserted = correlate(batch_limit=args.batch_limit)
        write_status(status_snapshot(inserted=inserted))
        end = time.monotonic() + args.poll_seconds
        while not stopped and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
