from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.lifecycle_ack import (
    claim_exit_position,
    mark_exit_claims_stale,
)
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.loader import load_exit_binding

DB_DSN = os.environ.get(
    "CRIPTA_DATABASE_DSN",
    "dbname=cripta user=cripta host=/var/run/postgresql application_name=universal-exit-shadow",
)
MODE = os.environ.get("CRIPTA_UNIVERSAL_EXIT_SHADOW", "DISABLED").strip().upper()
CONSUMER_ID = os.environ.get(
    "CRIPTA_UNIVERSAL_EXIT_SHADOW_CONSUMER_ID",
    "universal-exit-shadow-v1",
).strip()
POLL_SECONDS = float(os.environ.get("CRIPTA_UNIVERSAL_EXIT_SHADOW_POLL_SECONDS", "1.0"))
STATUS_PATH = Path(
    os.environ.get(
        "CRIPTA_UNIVERSAL_EXIT_SHADOW_STATUS_PATH",
        "/var/lib/cripta/universal_exit_shadow/status.json",
    )
)
running = True


@dataclass(frozen=True, slots=True)
class ClaimCycleResult:
    claimed_positions: tuple[str, ...]
    blocked_positions: tuple[tuple[str, str], ...]


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def _status(payload: dict[str, object]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "service": "universal_exit_shadow",
        "consumer_instance_id": CONSUMER_ID,
        **payload,
    }
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str),
        encoding="utf-8",
    )
    temporary.replace(STATUS_PATH)


def _position_ids(connection: psycopg.Connection[Any]) -> tuple[str, ...]:
    rows = connection.execute(
        """SELECT position_id
             FROM runtime.position_ownership
            WHERE bot_instance_id='universal-entry'
              AND state IN ('OPEN','RECONCILIATION_REQUIRED')
            ORDER BY fill_at,position_id"""
    ).fetchall()
    return tuple(str(row["position_id"]) for row in rows)


def claim_cycle(
    connection: psycopg.Connection[Any],
    *,
    now: datetime,
    consumer_instance_id: str,
) -> ClaimCycleResult:
    if not consumer_instance_id:
        raise ValueError("consumer_instance_id is required")
    current = now.astimezone(UTC)
    claimed: list[str] = []
    blocked: list[tuple[str, str]] = []
    for position_id in _position_ids(connection):
        try:
            position, plan = load_exit_binding(
                connection,
                strategy_position_id=position_id,
            )
            if position.exit_plan_fingerprint != plan.exit_plan_fingerprint:
                raise RuntimeError("Universal Exit exact binding mismatch")
            claim_exit_position(
                connection,
                strategy_position_id=position_id,
                consumer_instance_id=consumer_instance_id,
                claimed_at=current,
                payload={
                    "runtime": "universal_exit_shadow",
                    "mode": "DECISION_ONLY_NO_EXECUTION",
                },
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            blocked.append((position_id, f"{type(exc).__name__}: {exc}"))
            continue
        claimed.append(position_id)
    return ClaimCycleResult(tuple(claimed), tuple(blocked))


def main() -> int:
    global running
    if MODE != "ENABLED":
        raise SystemExit("Universal Exit shadow runtime is explicitly disabled")
    if not CONSUMER_ID:
        raise SystemExit("Universal Exit shadow consumer id is empty")
    if POLL_SECONDS <= 0:
        raise SystemExit("Universal Exit shadow poll seconds must be positive")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    engine = UniversalExitEngine()
    connection = psycopg.connect(DB_DSN, autocommit=False, row_factory=dict_row)
    try:
        while running:
            now = datetime.now(UTC)
            cycle = claim_cycle(
                connection,
                now=now,
                consumer_instance_id=CONSUMER_ID,
            )
            _status(
                {
                    "state": "RUNNING",
                    "observed_at": now.isoformat(),
                    "claimed_positions": list(cycle.claimed_positions),
                    "claimed_count": len(cycle.claimed_positions),
                    "blocked_positions": [
                        {"strategy_position_id": position_id, "reason": reason}
                        for position_id, reason in cycle.blocked_positions
                    ],
                    "blocked_count": len(cycle.blocked_positions),
                    "engine": type(engine).__name__,
                    "execution_rights": "NONE",
                }
            )
            time.sleep(POLL_SECONDS)
    except Exception as exc:
        _status(
            {
                "state": "ERROR",
                "observed_at": datetime.now(UTC).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
                "execution_rights": "NONE",
            }
        )
        raise
    finally:
        try:
            mark_exit_claims_stale(
                connection,
                consumer_instance_id=CONSUMER_ID,
                seen_at=datetime.now(UTC),
                reason="Universal Exit shadow runtime stopped",
            )
        finally:
            connection.close()
    _status(
        {
            "state": "STOPPED",
            "observed_at": datetime.now(UTC).isoformat(),
            "execution_rights": "NONE",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
