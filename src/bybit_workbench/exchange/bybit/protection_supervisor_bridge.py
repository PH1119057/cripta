"""Bridge between exact StrategyPosition lineage and independent Bybit account evidence.

No credentials and no Exchange mutation rights: caller supplies a fresh account
snapshot from a separately authorized GET-only reader.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from bybit_workbench.exchange.bybit.protection_intent import verify_approved_exchange_protection


def _json_dict(value: object) -> Mapping[str, object]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, Mapping):
        raise ValueError("invalid command payload or result")
    return parsed


def _command_lineage(connection: Any, *, position_id: str, symbol: str,
                     entry_command_id: str, fill_at: datetime) -> list[dict[str, object]]:
    rows = connection.execute(
        """SELECT command_id, command_type, payload_json, result_json
             FROM runtime.trade_commands
            WHERE symbol=%s AND state='completed'
              AND requested_at_epoch_ms >= %s
              AND command_type IN
                  ('strategy_exit','break_even','current_stop',
                   'trailing_stop','initial_protection')
            ORDER BY requested_at_epoch_ms,command_id LIMIT 1001""",
        (symbol, int(fill_at.timestamp() * 1000)),
    ).fetchall()
    if len(rows) > 1000:
        raise ValueError("protection command lineage truncated")
    commands: list[dict[str, object]] = []
    for row in rows:
        payload = _json_dict(row["payload_json"])
        kind = str(row["command_type"])
        if kind == "strategy_exit":
            if payload.get("strategy_position_id") != position_id:
                continue
        elif kind == "initial_protection":
            if payload.get("entry_command_id") != entry_command_id:
                continue
        else:
            # A legacy stop/break-even/trailing command without exact lineage
            # might have changed this symbol's position. Do not guess.
            if payload.get("strategy_position_id") != position_id:
                raise ValueError("unattributed legacy protection mutation on symbol")
        commands.append({
            "command_type": kind,
            "payload": payload,
            "result": _json_dict(row["result_json"]),
        })
    return commands


def account_protection_verdicts(
    connection: Any,
    *,
    positions: list[Mapping[str, object]],
    account_evidence: Mapping[str, object] | None,
    now: datetime,
) -> dict[str, dict[str, object]]:
    """Each open position receives VERIFIED, NOT_VERIFIED, or UNKNOWN."""
    scopes = account_evidence.get("symbols") if isinstance(account_evidence, Mapping) else None
    answer: dict[str, dict[str, object]] = {}
    for position in positions:
        position_id = str(position["position_id"])
        try:
            symbol = str(position["symbol"])
            if not isinstance(scopes, Mapping):
                raise ValueError("fresh authenticated Exchange scope unavailable")
            scope = scopes.get(symbol)
            if not isinstance(scope, Mapping) or not scope.get("tick"):
                raise ValueError("instrument tick size unavailable")
            entry_command_id = str(position["entry_command_id"])
            entry_row = connection.execute(
                "SELECT payload_json FROM runtime.trade_commands "
                "WHERE command_id=%s AND command_type='entry'",
                (entry_command_id,),
            ).fetchone()
            if not entry_row:
                raise ValueError("exact Entry command unavailable")
            commands = _command_lineage(
                connection, position_id=position_id, symbol=symbol,
                entry_command_id=entry_command_id, fill_at=position["fill_at"],
            )
            state, reason = verify_approved_exchange_protection(
                account_evidence=account_evidence, position_id=position_id,
                entry_command_id=entry_command_id, symbol=symbol,
                position_idx=int(position["position_idx"]),
                side=str(position["side"]),
                entry_price=position["actual_avg_fill"],
                tick=scope["tick"], entry_payload=_json_dict(entry_row["payload_json"]),
                completed_commands=commands, now=now,
            )
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            state, reason = "UNKNOWN", "exact protection proof unavailable: " + type(exc).__name__
        answer[position_id] = {"state": state, "reason": reason}
    return answer
