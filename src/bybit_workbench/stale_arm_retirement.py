"""Retirement of superseded Entry authorization after an application release.

This is NOT position or Exit lifecycle management. No Exchange snapshot,
current account binding, pending order or position flatness is required.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

RETIREMENT_REASON = "RELEASE_SUPERSEDED_ENTRY_AUTHORIZATION_ONLY"


def retire_stale_release_arms(
    connection: Any, *, loaded_commit: str, now: datetime
) -> tuple[str, ...]:
    """Close only historical Entry rights; never re-arm or mutate Exchange.

    The caller supplies a verified exact LOADED_COMMIT and owns the transaction.
    Mainnet new Entry must already be OFF. Exit/reconciliation is independent.
    """
    if now.tzinfo is None:
        return ()
    instant = now.astimezone(UTC)
    commit = loaded_commit.strip().lower()
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        return ()

    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates "
        "WHERE mode='mainnet' FOR UPDATE"
    ).fetchone()
    if gate is None or bool(gate["enabled"] if isinstance(gate, Mapping) else gate[0]):
        return ()

    rows = connection.execute(
        """SELECT live_arm_session_id,strategy_id,strategy_version,
                  symbol,release_commit,strategy_activation_id
             FROM control.live_arm_sessions
            WHERE state='ACTIVE' AND release_commit<>%s
            ORDER BY live_arm_session_id FOR UPDATE""",
        (commit,),
    ).fetchall()

    def value(row: Any, index: int, name: str) -> str:
        return str(row[name] if isinstance(row, Mapping) else row[index])

    retired: list[str] = []
    for row in rows:
        session_id = value(row, 0, "live_arm_session_id")
        old_commit = value(row, 4, "release_commit")
        updated = connection.execute(
            """UPDATE control.live_arm_sessions
                  SET state='CLOSED',deactivated_at=%s,
                      updated_at=clock_timestamp()
                WHERE live_arm_session_id=%s AND state='ACTIVE'
                  AND release_commit=%s""",
            (instant, session_id, old_commit),
        )
        if updated.rowcount != 1:
            continue

        # The surrounding transaction must roll back if durable audit fails.
        # No ON CONFLICT DO NOTHING: do not hide an already-retired/reopened arm.
        connection.execute(
            """INSERT INTO control.live_arm_retirement_events(
                   event_id,live_arm_session_id,previous_release_commit,
                   loaded_commit,reason,retired_at,evidence)
               VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (
                "stale-arm-retirement-" + session_id,
                session_id, old_commit, commit, RETIREMENT_REASON, instant,
                json.dumps({
                    "strategy_id": value(row, 1, "strategy_id"),
                    "strategy_version": value(row, 2, "strategy_version"),
                    "symbol": value(row, 3, "symbol"),
                    "strategy_activation_id": value(row, 5, "strategy_activation_id"),
                    "entry_authorization_only": True,
                    "mainnet_gate_was_closed": True,
                    "exchange_flatness_not_required": True,
                    "position_and_exit_ownership_untouched": True,
                    "trading_account_id": None,
                }, sort_keys=True),
            ),
        )
        retired.append(session_id)
    return tuple(retired)
