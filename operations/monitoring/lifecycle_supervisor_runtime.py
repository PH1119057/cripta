from __future__ import annotations

import json
import os
import asyncio
import signal
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.fault_delivery import process_due_deliveries
from bybit_workbench.exchange.bybit.protection_probe import direct_account_snapshot
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
OWNER_ALERT_WEBHOOK_URL = os.environ.get("CRIPTA_OWNER_ALERT_WEBHOOK_URL", "").strip()
OWNER_ALERT_MAX_ATTEMPTS = int(os.environ.get("CRIPTA_OWNER_ALERT_MAX_ATTEMPTS", "3"))
OWNER_ALERT_RETRY_SECONDS = int(os.environ.get("CRIPTA_OWNER_ALERT_RETRY_SECONDS", "30"))
OWNER_ALERT_ACK_TIMEOUT_SECONDS = int(
    os.environ.get("CRIPTA_OWNER_ALERT_ACK_TIMEOUT_SECONDS", "300")
)
OWNER_ALERT_HTTP_TIMEOUT_SECONDS = float(
    os.environ.get("CRIPTA_OWNER_ALERT_HTTP_TIMEOUT_SECONDS", "5")
)
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


def _owner_alert_sender(payload: dict[str, object]) -> None:
    if not OWNER_ALERT_WEBHOOK_URL:
        raise RuntimeError("owner alert webhook is not configured")
    request = urllib.request.Request(
        OWNER_ALERT_WEBHOOK_URL,
        data=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "cripta-lifecycle-supervisor/1",
        },
    )
    with urllib.request.urlopen(request, timeout=OWNER_ALERT_HTTP_TIMEOUT_SECONDS) as response:
        status = int(getattr(response, "status", 0) or 0)
        if status < 200 or status >= 300:
            raise RuntimeError(f"owner alert webhook returned HTTP {status}")


def _direct_exchange_protection(connection: psycopg.Connection[Any], now: datetime) -> dict[str, dict[str, object]]:
    """Read account truth independently; no Exchange mutation capability."""
    from decimal import Decimal
    from bybit_workbench.exchange.bybit.protection_evidence import evaluate_protection
    from bybit_workbench.exchange.bybit.protection_intent import expected_protection

    positions = connection.execute(
        "SELECT position_id,symbol,side,position_idx,actual_avg_fill,entry_command_id,fill_at "
        "FROM runtime.position_ownership WHERE bot_instance_id='universal-entry' AND state='OPEN'"
    ).fetchall()
    if not positions:
        return {}
    result = {str(p["position_id"]): {"state":"UNKNOWN", "reason":"independent exchange audit pending"} for p in positions}
    directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
    if not directory:
        return result
    try:
        credentials = json.loads((Path(directory) / "bybit-mainnet").read_text(encoding="utf-8"))
        snapshot = asyncio.run(direct_account_snapshot(
            tuple(sorted({str(p["symbol"]) for p in positions})),
            api_key=str(credentials["api_key"]),
            api_secret=str(credentials["api_secret"]),
            base_url=os.environ.get("BYBIT_REST", "https://api.bybit.kz"),
        ))
    except Exception:
        return result
    # Exact approved effective prices are not yet reconstructable for all
    # live Exit mutation paths. Fail closed UNKNOWN, never claim VERIFIED.
    return result


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
    with connection.transaction():
        return LifecycleSupervisor(connection, policy=policy, exchange_protection=_direct_exchange_protection(connection, now)).scan(
            now=now.astimezone(UTC)
        )


def main() -> int:
    global running
    if MODE != "ENABLED":
        raise SystemExit("Lifecycle Supervisor runtime is explicitly disabled")
    if POLL_SECONDS <= 0:
        raise SystemExit("Lifecycle Supervisor poll seconds must be positive")
    if (
        OWNER_ALERT_MAX_ATTEMPTS <= 0
        or OWNER_ALERT_RETRY_SECONDS <= 0
        or OWNER_ALERT_ACK_TIMEOUT_SECONDS <= 0
        or OWNER_ALERT_HTTP_TIMEOUT_SECONDS <= 0
    ):
        raise SystemExit("Lifecycle Supervisor owner-alert policy must be positive")
    policy = build_policy()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    connection = psycopg.connect(DB_DSN, autocommit=False, row_factory=dict_row)
    try:
        while running:
            now = datetime.now(UTC)
            result = scan_once(connection, now=now, policy=policy)
            with connection.transaction():
                delivery = process_due_deliveries(
                    connection,
                    now=now,
                    sender=_owner_alert_sender if OWNER_ALERT_WEBHOOK_URL else None,
                    max_attempts=OWNER_ALERT_MAX_ATTEMPTS,
                    retry_seconds=OWNER_ALERT_RETRY_SECONDS,
                    acknowledgement_timeout_seconds=OWNER_ALERT_ACK_TIMEOUT_SECONDS,
                )
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
                    "critical_delivery": {
                        "configured": bool(OWNER_ALERT_WEBHOOK_URL),
                        "attempted": delivery.attempted,
                        "delivered": delivery.delivered,
                        "escalated": delivery.escalated,
                        "pending_or_unacknowledged": delivery.pending,
                    },
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
