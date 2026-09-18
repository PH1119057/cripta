from __future__ import annotations

import json
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.lifecycle_supervisor import (
    LifecycleScanResult,
    LifecycleSupervisor,
    LifecycleSupervisorPolicy,
)

DB_DSN = os.environ.get(
    "CRIPTA_DATABASE_DSN",
    "dbname=cripta user=cripta host=/var/run/postgresql application_name=lifecycle-supervisor",
)
MODE = os.environ.get("CRIPTA_LIFECYCLE_SUPERVISOR", "DISABLED").strip().upper()
POLL_SECONDS = float(os.environ.get("CRIPTA_LIFECYCLE_SUPERVISOR_POLL_SECONDS", "2.0"))
STATUS_PATH = Path(
    os.environ.get(
        "CRIPTA_LIFECYCLE_SUPERVISOR_STATUS_PATH",
        "/var/lib/cripta/lifecycle_supervisor/status.json",
    )
)
running = True


def _optional_positive_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def _status(payload: dict[str, object]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    body = {"service": "lifecycle_supervisor", **payload}
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str),
        encoding="utf-8",
    )
    temporary.replace(STATUS_PATH)


def build_policy() -> LifecycleSupervisorPolicy:
    return LifecycleSupervisorPolicy(
        exchange_state_max_age_seconds=_optional_positive_int(
            "CRIPTA_LIFECYCLE_EXCHANGE_STATE_MAX_AGE_SECONDS"
        ),
        exit_owner_max_age_seconds=_optional_positive_int(
            "CRIPTA_LIFECYCLE_EXIT_OWNER_MAX_AGE_SECONDS"
        ),
    )


def scan_once(
    connection: psycopg.Connection[Any],
    *,
    now: datetime,
    policy: LifecycleSupervisorPolicy,
) -> LifecycleScanResult:
    return LifecycleSupervisor(connection, policy=policy).scan(now=now.astimezone(UTC))


def main() -> int:
    global running
    if MODE != "ENABLED":
        raise SystemExit("Lifecycle Supervisor runtime is explicitly disabled")
    if POLL_SECONDS <= 0:
        raise SystemExit("Lifecycle Supervisor poll seconds must be positive")
    policy = build_policy()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    connection = psycopg.connect(DB_DSN, autocommit=False, row_factory=dict_row)
    try:
        while running:
            now = datetime.now(UTC)
            result = scan_once(connection, now=now, policy=policy)
            _status(
                {
                    "state": "RUNNING",
                    "observed_at": now.isoformat(),
                    "projected_events": result.projected_events,
                    "opened_faults": result.open_faults,
                    "resolved_faults": result.resolved_faults,
                    "active_fault_codes": list(result.active_fault_codes),
                    "exit_owner_max_age_seconds": (policy.exit_owner_max_age_seconds),
                    "exchange_state_max_age_seconds": (policy.exchange_state_max_age_seconds),
                    "trading_rights": "NONE",
                }
            )
            time.sleep(POLL_SECONDS)
    except Exception as exc:
        _status(
            {
                "state": "ERROR",
                "observed_at": datetime.now(UTC).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
                "trading_rights": "NONE",
            }
        )
        raise
    finally:
        connection.close()
    _status(
        {
            "state": "STOPPED",
            "observed_at": datetime.now(UTC).isoformat(),
            "trading_rights": "NONE",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
