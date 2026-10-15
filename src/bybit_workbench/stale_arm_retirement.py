"""Fail-closed retirement of old release arm sessions.

This closes historical authorization only. It never migrates, creates, or
authorizes new-release LIVE arm; no Exchange mutation is performed.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def retire_stale_release_arms(
    connection: Any,
    *,
    loaded_commit: str,
    account_snapshot: Mapping[str, object] | None,
    now: datetime,
) -> tuple[str, ...]:
    instant = now.astimezone(UTC)
    if len(loaded_commit) != 40 or account_snapshot is None:
        return ()
    if (
        account_snapshot.get("source") != "BYBIT_AUTHENTICATED_ACCOUNT_GET"
        or account_snapshot.get("complete") is not True
        or account_snapshot.get("account_ref") != "BYBIT:UNIFIED"
        or not str(account_snapshot.get("account_user_id") or "").strip()
    ):
        return ()
    try:
        observed = datetime.fromisoformat(str(account_snapshot["observed_at"]))
    except (KeyError, TypeError, ValueError):
        return ()
    if observed.tzinfo is None:
        return ()
    age = (instant - observed.astimezone(UTC)).total_seconds()
    if age < 0 or age > 15:
        return ()
    inventory = account_snapshot.get("symbols")
    if not isinstance(inventory, Mapping):
        return ()

    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates "
        "WHERE mode='mainnet' FOR UPDATE"
    ).fetchone()
    if gate is None or bool(gate["enabled"] if isinstance(gate, Mapping) else gate[0]):
        return ()

    rows = connection.execute(
        """SELECT live_arm_session_id,strategy_id,strategy_version,
                  symbol,release_commit
             FROM control.live_arm_sessions
            WHERE state='ACTIVE' AND release_commit<>%s
            ORDER BY live_arm_session_id FOR UPDATE""",
        (loaded_commit,),
    ).fetchall()

    def value(row: Any, index: int, key: str) -> str:
        return str(row[key] if isinstance(row, Mapping) else row[index])

    retired: list[str] = []
    for row in rows:
        session_id = value(row, 0, "live_arm_session_id")
        strategy_id = value(row, 1, "strategy_id")
        strategy_version = value(row, 2, "strategy_version")
        symbol = value(row, 3, "symbol")
        prior_release = value(row, 4, "release_commit")
        scope = inventory.get(symbol)
        if not isinstance(scope, Mapping):
            continue
        positions = scope.get("positions")
        orders = scope.get("orders")
        if not isinstance(positions, list) or not isinstance(orders, list) or orders:
            continue
        try:
            if any(
                not isinstance(p, Mapping)
                or float(p["size"]) != 0
                for p in positions
            ):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        blocked = connection.execute(
            """SELECT (
                EXISTS(SELECT 1 FROM runtime.position_ownership
                       WHERE symbol=%s AND state<>'CLOSED')
                OR EXISTS(SELECT 1 FROM runtime.exchange_position_slot_claims
                          WHERE symbol=%s AND claim_state<>'RELEASED')
                OR EXISTS(SELECT 1 FROM runtime.capital_reservations
                          WHERE strategy_id=%s AND strategy_version=%s
                            AND state<>'RELEASED')
                OR EXISTS(SELECT 1 FROM runtime.trade_commands
                          WHERE symbol=%s AND (
                              state NOT IN ('completed','failed')
                              OR (state='failed' AND
                                  coalesce(error,'') LIKE 'EXCHANGE_MUTATION_BARRIER%%')
                          ))
            )""",
            (symbol, symbol, strategy_id, strategy_version, symbol),
        ).fetchone()
        if blocked is None or bool(
            blocked[0] if not isinstance(blocked, Mapping)
            else next(iter(blocked.values()))
        ):
            continue
        updated = connection.execute(
            """UPDATE control.live_arm_sessions
                  SET state='CLOSED',deactivated_at=%s,
                      updated_at=clock_timestamp()
                WHERE live_arm_session_id=%s AND state='ACTIVE'
                  AND release_commit=%s""",
            (instant, session_id, prior_release),
        )
        if updated.rowcount != 1:
            continue
        connection.execute(
            """INSERT INTO control.live_arm_retirement_events(
                   event_id,live_arm_session_id,previous_release_commit,
                   loaded_commit,reason,retired_at,evidence)
               VALUES(%s,%s,%s,%s,
                      'EXACT_SCOPE_FLAT_AFTER_RELEASE_CHANGE',%s,%s::jsonb)
               ON CONFLICT(live_arm_session_id) DO NOTHING""",
            (
                "stale-arm-retirement-" + session_id,
                session_id, prior_release, loaded_commit, instant,
                json.dumps({
                    "symbol": symbol,
                    "strategy_id": strategy_id,
                    "strategy_version": strategy_version,
                    "authenticated_account_read": True,
                    "observed_at": observed.astimezone(UTC).isoformat(),
                    "positions_count": len(positions),
                    "orders_count": len(orders),
                    "mainnet_gate_was_closed": True,
                }, sort_keys=True),
            ),
        )
        retired.append(session_id)
    return tuple(retired)
