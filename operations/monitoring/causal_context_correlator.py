#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import time
from typing import Any

import psycopg

DSN = os.environ.get("CRIPTA_DSN", "dbname=cripta user=cripta host=/var/run/postgresql")

EVENT_SOURCE_SQL = """
SELECT 'SIGNAL' event_type, signal_id reference_id, signal_at_epoch_ms event_ms,
       symbol, direction side, jsonb_build_object('decision',decision,'state',state) payload
FROM monitoring.opportunities
UNION ALL
SELECT 'ENTRY_DECISION', signal_id, decided_at_epoch_ms, symbol, direction,
       jsonb_build_object('decision',decision,'reason',reason,'policy',entry_policy,
                          'policy_version',policy_version)
FROM runtime.entry_decisions
UNION ALL
SELECT 'TRADE_COMMAND', command_id, requested_at_epoch_ms, symbol, NULL,
       jsonb_build_object('command_type',command_type,'processing_state',state,
                          'exchange_filled',EXISTS(
                              SELECT 1 FROM runtime.executions e
                              WHERE e.order_link_id=coalesce(
                                  trade_commands.result_json::jsonb->>'orderLinkId',
                                  trade_commands.payload_json::jsonb->>'order_link_id')))
FROM runtime.trade_commands
UNION ALL
SELECT 'FILL', exec_id, COALESCE(exec_time_ms,received_at_epoch_ms), symbol, side,
       jsonb_build_object('order_id',order_id,'order_link_id',order_link_id,
                          'qty',exec_qty,'price',exec_price,'fee',exec_fee)
FROM runtime.executions
UNION ALL
SELECT 'POSITION_TRANSITION', id::text, observed_at_epoch_ms, symbol, NULL,
       jsonb_build_object('position_id',position_id,'old_state',old_state,
                          'new_state',new_state,'reason',reason,
                          'shadow_action',shadow_action)
FROM supervisor.transitions
UNION ALL
SELECT 'M3_CONSUMED_CONTEXT', signal_id,
       (extract(epoch from strategy_decision_at)*1000)::bigint,
       symbol,direction,payload
FROM runtime.m3_consumed_context
UNION ALL
SELECT 'DISPATCHER_HOLD', position_id||':'||assessment_id||':'||supervisor_state,
       (extract(epoch from consumed_at)*1000)::bigint,
       split_part(position_id,':',1),NULL,payload
FROM supervisor.dispatcher_hold_context
UNION ALL
SELECT 'EXIT_DECISION', decision_id,
       (extract(epoch from decided_at)*1000)::bigint,
       symbol,NULL,decision_json
FROM supervisor.exit_decisions
"""


def prepare_database() -> None:
    with psycopg.connect(DSN) as db:
        db.execute("CREATE SCHEMA IF NOT EXISTS research_context")
        db.execute("""CREATE TABLE IF NOT EXISTS research_context.event_links(
            event_type text NOT NULL,
            reference_id text NOT NULL,
            occurred_at timestamptz NOT NULL,
            symbol text,
            side text,
            event_payload jsonb NOT NULL,
            observed_mayak_snapshot_id bigint REFERENCES mayak_v2.snapshots(id),
            observed_mayak_at timestamptz,
            observed_dispatcher_snapshot_id text REFERENCES strategy_dispatcher.runs(snapshot_id),
            observed_dispatcher_at timestamptz,
            observed_context jsonb NOT NULL,
            consumed_context jsonb,
            link_quality text NOT NULL,
            provenance jsonb NOT NULL,
            linked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(event_type,reference_id))""")
        db.execute(
            "CREATE INDEX IF NOT EXISTS research_context_event_links_at "
            "ON research_context.event_links(occurred_at DESC)"
        )
        db.commit()


def correlate(*, cutoff_epoch_ms: int | None = None) -> int:
    cutoff_clause = "" if cutoff_epoch_ms is None else "WHERE source.event_ms >= %s"
    parameters = () if cutoff_epoch_ms is None else (cutoff_epoch_ms,)
    query = f"""
    WITH source AS ({EVENT_SOURCE_SQL}), eligible AS (
        SELECT source.* FROM source
        {cutoff_clause}
    )
    INSERT INTO research_context.event_links(
        event_type,reference_id,occurred_at,symbol,side,event_payload,
        observed_mayak_snapshot_id,observed_mayak_at,
        observed_dispatcher_snapshot_id,observed_dispatcher_at,
        observed_context,consumed_context,link_quality,provenance)
    SELECT e.event_type,e.reference_id,to_timestamp(e.event_ms/1000.0),e.symbol,e.side,e.payload,
           m.id,m.observed_at,d.snapshot_id,d.observed_at,
           jsonb_build_object(
               'mode','OBSERVED_CONTEXT',
               'mayak_snapshot_id',m.id,
               'mayak_observed_at',m.observed_at,
               'dispatcher_snapshot_id',d.snapshot_id,
               'dispatcher_observed_at',d.observed_at,
               'dispatcher_profile_count',d.profile_count,
               'dispatcher_trading_effect',d.trading_effect),
           CASE
             WHEN e.event_type IN ('M3_CONSUMED_CONTEXT','DISPATCHER_HOLD')
             THEN e.payload
             ELSE NULL
           END,
           CASE WHEN m.id IS NULL THEN 'NO_CAUSAL_MAYAK_SNAPSHOT'
                WHEN d.snapshot_id IS NULL THEN 'MAYAK_ONLY_CAUSAL_PRIOR'
                ELSE 'MAYAK_AND_DISPATCHER_CAUSAL_PRIOR' END,
           jsonb_build_object(
               'method','latest_snapshot_not_after_event',
               'linked_at',clock_timestamp(),
               'consumed_context',CASE
                   WHEN e.event_type IN ('M3_CONSUMED_CONTEXT','DISPATCHER_HOLD')
                   THEN 'CONSUMED_CONTEXT'
                   ELSE 'NOT_RECORDED'
               END)
    FROM eligible e
    LEFT JOIN LATERAL (
        SELECT id,observed_at FROM mayak_v2.snapshots
        WHERE observed_at <= to_timestamp(e.event_ms/1000.0)
        ORDER BY observed_at DESC LIMIT 1
    ) m ON true
    LEFT JOIN LATERAL (
        SELECT snapshot_id,observed_at,profile_count,trading_effect
        FROM strategy_dispatcher.runs
        WHERE observed_at <= to_timestamp(e.event_ms/1000.0)
        ORDER BY observed_at DESC LIMIT 1
    ) d ON true
    ON CONFLICT(event_type,reference_id) DO NOTHING
    """
    with psycopg.connect(DSN) as db:
        cursor = db.execute(query, parameters)
        inserted = cursor.rowcount
        db.commit()
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description="Причинный коррелятор рыночного контекста")
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--lookback-seconds", type=int, default=600)
    args = parser.parse_args()
    if args.poll_seconds <= 0 or args.lookback_seconds <= 0:
        raise ValueError("poll and lookback must be positive")
    prepare_database()
    correlate()
    stopped = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        correlate(cutoff_epoch_ms=int((time.time() - args.lookback_seconds) * 1000))
        end = time.monotonic() + args.poll_seconds
        while not stopped and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
