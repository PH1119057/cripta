#!/usr/bin/env python3
from __future__ import annotations

import json
import signal
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

DSN = "dbname=cripta user=cripta host=/var/run/postgresql"
running = True


def document(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


def initialize(connection: psycopg.Connection[Any]) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS analyst")
    connection.execute("""CREATE TABLE IF NOT EXISTS analyst.trade_lifecycles(
        trade_id TEXT PRIMARY KEY, position_id TEXT, symbol TEXT NOT NULL, side TEXT,
        strategy_id TEXT NOT NULL, strategy_version TEXT,
        opened_at TIMESTAMPTZ, closed_at TIMESTAMPTZ, lifecycle_state TEXT NOT NULL,
        data_completeness TEXT NOT NULL, diagnosis_class TEXT NOT NULL,
        actual_net_pnl NUMERIC, actual_net_without_funding NUMERIC,
        bot_instance_id TEXT, entry_command_id TEXT, geometry_handoff_id TEXT,
        lifecycle_json JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
    connection.execute(
        "ALTER TABLE analyst.trade_lifecycles "
        "ADD COLUMN IF NOT EXISTS actual_net_without_funding NUMERIC"
    )
    connection.execute(
        "ALTER TABLE analyst.trade_lifecycles "
        "ADD COLUMN IF NOT EXISTS bot_instance_id TEXT"
    )
    connection.execute(
        "ALTER TABLE analyst.trade_lifecycles "
        "ADD COLUMN IF NOT EXISTS entry_command_id TEXT"
    )
    connection.execute(
        "ALTER TABLE analyst.trade_lifecycles "
        "ADD COLUMN IF NOT EXISTS geometry_handoff_id TEXT"
    )
    connection.execute("""CREATE TABLE IF NOT EXISTS analyst.lifecycle_events(
        trade_id TEXT NOT NULL, event_type TEXT NOT NULL, event_id TEXT NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL,
        PRIMARY KEY(trade_id,event_type,event_id))""")
    connection.commit()


def refresh(connection: psycopg.Connection[Any]) -> int:
    entries = connection.execute("""SELECT * FROM runtime.trade_commands
        WHERE command_type='entry' AND payload_json::jsonb->>'entry_policy'='m3_full_live_v1'
        ORDER BY requested_at_epoch_ms""").fetchall()
    updated = 0
    for entry in entries:
        entry_payload, entry_response = (
            document(entry["payload_json"]),
            document(entry["result_json"]),
        )
        entry_result = document(entry_response.get("result")) or entry_response
        order_id = str(entry_result.get("orderId") or "")
        link_id = str(entry_result.get("orderLinkId") or entry_payload.get("order_link_id") or "")
        fills = connection.execute(
            """SELECT * FROM runtime.executions
            WHERE (%s<>'' AND order_id=%s) OR (%s<>'' AND order_link_id=%s)
            ORDER BY exec_time_ms""",
            (order_id, order_id, link_id, link_id),
        ).fetchall()
        if not fills:
            continue
        qty = sum(decimal(row["exec_qty"]) for row in fills)
        weighted = sum(decimal(row["exec_qty"]) * decimal(row["exec_price"]) for row in fills)
        avg_fill = weighted / qty if qty else Decimal(0)
        entry_fee = sum(abs(decimal(row["exec_fee"])) for row in fills)
        opened_ms = min(int(row["exec_time_ms"] or row["received_at_epoch_ms"]) for row in fills)
        decision = connection.execute(
            """SELECT * FROM runtime.entry_decisions
            WHERE details_json::jsonb->>'command_id'=%s ORDER BY decided_at_epoch_ms LIMIT 1""",
            (entry["command_id"],),
        ).fetchone()
        context = None
        if decision:
            context = connection.execute(
                "SELECT * FROM runtime.m3_consumed_context WHERE signal_id=%s",
                (decision["signal_id"],),
            ).fetchone()
        ownership = connection.execute(
            """SELECT * FROM runtime.position_ownership WHERE entry_command_id=%s""",
            (entry["command_id"],),
        ).fetchone()
        position_id = None if ownership is None else str(ownership["position_id"])
        attribution = (
            None
            if position_id is None
            else connection.execute(
                """SELECT * FROM runtime.position_exit_attribution
                   WHERE position_id=%s AND link_status='EXACT'""",
                (position_id,),
            ).fetchone()
        )
        closes = (
            []
            if position_id is None
            else connection.execute(
                """SELECT * FROM runtime.trade_commands
                WHERE command_type='close'
                  AND payload_json::jsonb->>'position_id'=%s
                ORDER BY requested_at_epoch_ms""",
                (position_id,),
            ).fetchall()
        )
        close_command = None
        close_fills: list[dict[str, Any]] = []
        if attribution is not None:
            exact_exec_ids = list(attribution["exit_execution_ids"] or [])
            if exact_exec_ids:
                close_fills = connection.execute(
                    """SELECT * FROM runtime.executions
                       WHERE exec_id=ANY(%s) ORDER BY exec_time_ms,exec_id""",
                    (exact_exec_ids,),
                ).fetchall()
        for candidate in closes if attribution is None else []:
            response = document(candidate["result_json"])
            result = document(response.get("result")) or response
            candidate_order = str(result.get("orderId") or "")
            candidate_link = str(result.get("orderLinkId") or "")
            rows = connection.execute(
                """SELECT * FROM runtime.executions
                WHERE (%s<>'' AND order_id=%s) OR (%s<>'' AND order_link_id=%s)
                ORDER BY exec_time_ms""",
                (candidate_order, candidate_order, candidate_link, candidate_link),
            ).fetchall()
            if rows:
                close_command, close_fills = candidate, rows
                break
        exit_decision = None
        if close_command:
            exit_decision = connection.execute(
                "SELECT * FROM supervisor.exit_decisions WHERE close_command_id=%s",
                (close_command["command_id"],),
            ).fetchone()
        if exit_decision is not None and str(exit_decision["position_id"]) != position_id:
            raise RuntimeError("выход связан с другой позицией")
        hold_timeline = (
            []
            if position_id is None
            else [
                dict(row)
                for row in connection.execute(
                    """SELECT * FROM supervisor.dispatcher_hold_context
               WHERE position_id=%s ORDER BY consumed_at""",
                    (position_id,),
                ).fetchall()
            ]
        )
        supervisor_timeline = (
            []
            if position_id is None
            else [
                dict(row)
                for row in connection.execute(
                    """SELECT * FROM supervisor.snapshots
               WHERE position_id=%s ORDER BY observed_at_epoch_ms""",
                    (position_id,),
                ).fetchall()
            ]
        )
        close_qty = sum(decimal(row["exec_qty"]) for row in close_fills)
        close_weighted = sum(
            decimal(row["exec_qty"]) * decimal(row["exec_price"]) for row in close_fills
        )
        close_price = close_weighted / close_qty if close_qty else None
        close_fee = sum(abs(decimal(row["exec_fee"])) for row in close_fills)
        side = str(fills[0]["side"])
        direction = Decimal(1) if side == "Buy" else Decimal(-1)
        gross = (
            None
            if close_price is None
            else (close_price - avg_fill) * min(qty, close_qty) * direction
        )
        net = None if gross is None else gross - entry_fee - close_fee
        diagnosis = "UNRESOLVED"
        if net is not None:
            if gross is not None and gross > 0 and net <= 0:
                diagnosis = "FEE_DOMINATED_EXIT"
            elif abs(net) < Decimal("0.01"):
                diagnosis = "NO_MATERIAL_EFFECT"
            else:
                diagnosis = "UNRESOLVED"
        trade_id = (
            f"LEGACY:{entry['command_id']}"
            if ownership is None
            else str(ownership["trade_id"])
        )
        lifecycle = {
            "trade_id": trade_id,
            "strategy": {"id": "m3_full_live_v1", "version": "1.0.0-owner-live"},
            "signal": None
            if decision is None
            else {"id": decision["signal_id"], "at_ms": decision["signal_at_epoch_ms"]},
            "entry_decision": None if decision is None else dict(decision),
            "consumed_context": None if context is None else dict(context),
            "order": {
                "command_id": entry["command_id"],
                "processing_state": entry["state"],
                "orderId": order_id or None,
                "orderLinkId": link_id or None,
            },
            "fill": {
                "exec_ids": [row["exec_id"] for row in fills],
                "qty": str(qty),
                "avg_price": str(avg_fill),
                "entry_fee_actual": str(entry_fee),
            },
            "ownership": None if ownership is None else dict(ownership),
            "entry_geometry": None
            if ownership is None or ownership["geometry_handoff_id"] is None
            else dict(
                connection.execute(
                    """SELECT * FROM monitoring.entry_geometry_handoffs
                    WHERE geometry_handoff_id=%s""",
                    (ownership["geometry_handoff_id"],),
                ).fetchone()
            ),
            "protection": [
                dict(row)
                for row in connection.execute(
                    """SELECT * FROM runtime.trade_commands
                WHERE symbol=%s AND requested_at_epoch_ms>=%s
                AND (payload_json::jsonb->>'entry_command_id'=%s OR
                     payload_json::jsonb->>'source'='m3_full_live_v1_1')
                AND command_type IN
                ('initial_protection','break_even','current_stop','trailing_stop')
                ORDER BY requested_at_epoch_ms""",
                    (entry["symbol"], opened_ms, entry["command_id"]),
                ).fetchall()
            ],
            "hold_timeline": hold_timeline,
            "supervisor_timeline": supervisor_timeline,
            "exit_decision": None if exit_decision is None else dict(exit_decision),
            "exit_attribution": None if attribution is None else dict(attribution),
            "close_command": None if close_command is None else dict(close_command),
            "close_fill": {
                "exec_ids": [row["exec_id"] for row in close_fills],
                "qty": str(close_qty),
                "avg_price": None if close_price is None else str(close_price),
                "exit_fee_actual": str(close_fee),
            },
            "economics": {
                "gross_pnl": None if gross is None else str(gross),
                "entry_fee_actual": str(entry_fee),
                "exit_fee_actual": str(close_fee),
                "funding": None,
                "actual_net_without_funding": None if net is None else str(net),
                "actual_net_pnl": None,
                "net_completeness": "PARTIAL_NO_FUNDING",
            },
            "completeness_class": "PARTIAL",
            "diagnosis": diagnosis,
        }
        completeness = (
            "PARTIAL_NO_FUNDING"
        )
        closed_at = (
            None
            if not close_fills
            else datetime.fromtimestamp(
                max(int(row["exec_time_ms"] or row["received_at_epoch_ms"]) for row in close_fills)
                / 1000,
                UTC,
            )
        )
        connection.execute(
            """INSERT INTO analyst.trade_lifecycles(
            trade_id,symbol,side,strategy_id,strategy_version,opened_at,closed_at,
            lifecycle_state,data_completeness,diagnosis_class,actual_net_pnl,
            actual_net_without_funding,bot_instance_id,entry_command_id,
            geometry_handoff_id,lifecycle_json)
            VALUES(%s,%s,%s,'m3_full_live_v1','1.0.0-owner-live',%s,%s,%s,%s,%s,
                   %s,%s,%s,%s,%s,%s)
            ON CONFLICT(trade_id) DO UPDATE SET closed_at=excluded.closed_at,
            lifecycle_state=excluded.lifecycle_state,data_completeness=excluded.data_completeness,
            diagnosis_class=excluded.diagnosis_class,actual_net_pnl=excluded.actual_net_pnl,
            actual_net_without_funding=excluded.actual_net_without_funding,
            bot_instance_id=excluded.bot_instance_id,
            entry_command_id=excluded.entry_command_id,
            geometry_handoff_id=excluded.geometry_handoff_id,
            lifecycle_json=excluded.lifecycle_json,updated_at=clock_timestamp()""",
            (
                trade_id,
                entry["symbol"],
                side,
                datetime.fromtimestamp(opened_ms / 1000, UTC),
                closed_at,
                "CLOSED" if close_fills else "OPEN",
                completeness,
                diagnosis,
                None,
                net,
                None if ownership is None else ownership["bot_instance_id"],
                entry["command_id"],
                None if ownership is None else ownership["geometry_handoff_id"],
                json.dumps(lifecycle, ensure_ascii=False, default=str),
            ),
        )
        if position_id is not None:
            connection.execute(
                "UPDATE analyst.trade_lifecycles SET position_id=%s WHERE trade_id=%s",
                (position_id, trade_id),
            )
        updated += 1
    connection.commit()
    return updated


def stop(*_: object) -> None:
    global running
    running = False


def main() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        initialize(connection)
        while running:
            refresh(connection)
            time.sleep(15)


if __name__ == "__main__":
    main()
