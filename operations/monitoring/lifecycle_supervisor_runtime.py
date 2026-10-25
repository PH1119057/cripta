from __future__ import annotations

import asyncio
import json
import os
import signal
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.exchange.bybit.protection_probe import direct_account_snapshot
from bybit_workbench.exchange.bybit.protection_supervisor_bridge import account_protection_verdicts
from bybit_workbench.fault_delivery import (
    escalate_missing_dashboard_display,
    process_due_deliveries,
)
from bybit_workbench.lifecycle_exposure_bridge import verified_account_exposure
from bybit_workbench.lifecycle_supervisor import (
    LifecycleScanResult,
    LifecycleSupervisor,
    LifecycleSupervisorPolicy,
)
from bybit_workbench.release_arm_safety import check_release_arm_invariant
from bybit_workbench.stale_arm_retirement import retire_stale_release_arms

DB_DSN = os.environ.get(
    "CRIPTA_DATABASE_DSN",
    "dbname=cripta user=cripta host=/var/run/postgresql application_name=lifecycle-supervisor",
)
MODE = os.environ.get("CRIPTA_LIFECYCLE_SUPERVISOR", "DISABLED").strip().upper()
POLL_SECONDS = float(os.environ.get("CRIPTA_LIFECYCLE_SUPERVISOR_POLL_SECONDS", "2.0"))
TRADING_ACCOUNT_ID = os.environ.get("CRIPTA_TRADING_ACCOUNT_ID", "").strip()
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


def build_policy() -> LifecycleSupervisorPolicy:
    return LifecycleSupervisorPolicy(
        exchange_state_max_age_seconds=_optional_positive_int(
            "CRIPTA_LIFECYCLE_EXCHANGE_STATE_MAX_AGE_SECONDS"
        ),
        exit_owner_max_age_seconds=_optional_positive_int(
            "CRIPTA_LIFECYCLE_EXIT_OWNER_MAX_AGE_SECONDS"
        ),
    )


def _account_positions(connection: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with connection.transaction():
        return list(connection.execute(
            """SELECT position_id,symbol,side,position_idx,actual_avg_fill,
                      entry_command_id,fill_at
                 FROM runtime.position_ownership
                WHERE state='OPEN'
                  AND (
                       (trading_account_id=%s AND identity_status='PROVEN')
                  )
                ORDER BY position_id""",
            (TRADING_ACCOUNT_ID,),
        ).fetchall())


def _independent_exchange_snapshot(symbols: tuple[str, ...]) -> dict[str, object]:
    # Always perform an authenticated account-identity GET, even while flat.
    # Otherwise a freshly deployed Supervisor could look healthy without ever
    # proving that its credential delivery or Bybit account read path works.
    directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
    if not directory:
        raise RuntimeError("Supervisor read credential is not mounted")
    credential = json.loads(
        (Path(directory) / "bybit-mainnet").read_text(encoding="utf-8")
    )
    return asyncio.run(direct_account_snapshot(
        symbols,
        api_key=str(credential["api_key"]),
        api_secret=str(credential["api_secret"]),
        base_url=os.environ.get("BYBIT_REST", "https://api.bybit.kz"),
    ))


def scan_once(
    connection: psycopg.Connection[Any],
    *,
    now: datetime,
    policy: LifecycleSupervisorPolicy,
    protection: dict[str, dict[str, object]] | None = None,
    account_exposure: tuple[dict[str, object], ...] | None = None,
) -> LifecycleScanResult:
    with connection.transaction():
        return LifecycleSupervisor(
            connection,
            policy=policy,
            exchange_protection=protection,
            exchange_exposure=account_exposure,
            trading_account_id=TRADING_ACCOUNT_ID,
        ).scan(now=now.astimezone(UTC))


def main() -> int:
    global running
    if MODE != "ENABLED":
        raise SystemExit("Lifecycle Supervisor runtime is explicitly disabled")
    if not TRADING_ACCOUNT_ID:
        raise SystemExit("CRIPTA_TRADING_ACCOUNT_ID is required for Lifecycle Supervisor")
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
    next_read = 0.0
    last_scope: tuple[str, ...] = ()
    last_position_ids: frozenset[str] = frozenset()
    account: dict[str, object] | None = None
    read_error = ""
    try:
        while running:
            now = datetime.now(UTC)
            positions = _account_positions(connection)
            # Audit old-release sessions in the existing Supervisor cycle.
            # A complete per-symbol authenticated Exchange snapshot is required
            # before any session is retired; the installed release is never
            # inferred from the previous approval row.
            with connection.transaction():
                session_rows = connection.execute(
                    """SELECT DISTINCT symbol FROM control.live_arm_sessions
                       WHERE state='ACTIVE' ORDER BY symbol"""
                ).fetchall()
            arm_symbols = {
                str(r["symbol"] if isinstance(r, dict) else r[0])
                for r in session_rows
            }
            symbols = tuple(sorted(
                {str(p["symbol"]) for p in positions} | arm_symbols
            ))
            position_ids = frozenset(str(p["position_id"]) for p in positions)
            # Arm scopes can include the symbol before its Entry fills.  A
            # cached account read from before fill must NEVER be used as the
            # first independent protection verdict for the new position.
            # Force a fresh authenticated GET on each position lifecycle
            # transition even when the symbol set has not changed.
            new_position_identity = position_ids != last_position_ids
            if new_position_identity:
                account = None
            if (
                symbols != last_scope
                or new_position_identity
                or time.monotonic() >= next_read
            ):
                try:
                    account = _independent_exchange_snapshot(symbols)
                    read_error = ""
                except Exception as exc:
                    account = None
                    read_error = type(exc).__name__
                last_scope = symbols
                last_position_ids = position_ids
                next_read = time.monotonic() + 10.0
            # The authenticated GET may take seconds. Evaluate freshness using
            # the clock AFTER the read, never the pre-request scan timestamp.
            # Keep the 15-second evidence expiry and fail-closed UNKNOWN.
            now = datetime.now(UTC)
            with connection.transaction():
                protection = account_protection_verdicts(
                    connection,
                    positions=positions,
                    account_evidence=account,
                    now=now,
                    trading_account_id=TRADING_ACCOUNT_ID,
                )
            account_read_complete = bool(
                account and account.get("complete") is True
            )
            account_observed_at = (
                str(account.get("observed_at"))
                if account_read_complete else None
            )
            account_identity_present = bool(
                account_read_complete
                and str(account.get("account_user_id") or "").strip()
            )
            # Credential provenance and registry lineage are independent gates.
            credential_dir = os.environ.get("CREDENTIALS_DIRECTORY", "")
            mounted_here = (
                bool(credential_dir)
                and Path(credential_dir).resolve()
                == Path("/run/credentials/cripta-lifecycle-supervisor.service")
            )
            credential_ref = (
                "systemd-credential:bybit-mainnet" if mounted_here else None
            )
            with connection.transaction():
                exposure_audit = verified_account_exposure(
                    connection,
                    account_snapshot=account,
                    trading_account_id=TRADING_ACCOUNT_ID,
                    account_connector_id=os.environ.get(
                        "CRIPTA_ACCOUNT_CONNECTOR_ID", ""
                    ).strip(),
                    verified_credential_ref=credential_ref,
                    now=now,
                )
            result = scan_once(
                connection, now=now, policy=policy, protection=protection,
                account_exposure=exposure_audit.verified_positions,
            )
            retired_arms: tuple[str, ...] = ()
            # This code never creates new sessions, changes permissions,
            # or enables mainnet. Unknown Exchange truth leaves old sessions
            # untouched and the entry guard fail-closed.
            installed_file = Path(
                os.environ.get(
                    "CRIPTA_RUNTIME_INSTALLED_COMMIT_PATH",
                    "/srv/cripta/runtime/current/INSTALLED_COMMIT",
                )
            )
            loaded_commit = (
                installed_file.read_text(encoding="utf-8").strip()
                if installed_file.is_file() else ""
            )
            if len(loaded_commit) == 40:
                with connection.transaction():
                    retired_arms = retire_stale_release_arms(
                        connection,
                        loaded_commit=loaded_commit,
                        now=now,
                    )
                    if retired_arms:
                        check_release_arm_invariant(
                            connection, loaded_commit=loaded_commit, now=now
                        )
            with connection.transaction():
                delivery = process_due_deliveries(
                    connection,
                    now=now,
                    sender=_owner_alert_sender if OWNER_ALERT_WEBHOOK_URL else None,
                    max_attempts=OWNER_ALERT_MAX_ATTEMPTS,
                    retry_seconds=OWNER_ALERT_RETRY_SECONDS,
                    acknowledgement_timeout_seconds=OWNER_ALERT_ACK_TIMEOUT_SECONDS,
                )
                unseen_dashboard = escalate_missing_dashboard_display(
                    connection,
                    now=now,
                    display_timeout_seconds=OWNER_ALERT_ACK_TIMEOUT_SECONDS,
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
                    "dashboard_inbox": {
                        "unseen_escalated_this_pass": unseen_dashboard,
                    },
                    "historical_live_arm_retired": list(retired_arms),
                    "critical_delivery": {
                        "configured": bool(OWNER_ALERT_WEBHOOK_URL),
                        "attempted": delivery.attempted,
                        "delivered": delivery.delivered,
                        "escalated": delivery.escalated,
                        "pending_or_unacknowledged": delivery.pending,
                    },
                    "exposure_audit": {
                        "state": exposure_audit.state,
                        "reasons": list(exposure_audit.reasons),
                        "unknown_open_count": exposure_audit.unknown_open_count,
                        "verified_position_count": (
                            None if exposure_audit.verified_positions is None
                            else len(exposure_audit.verified_positions)
                        ),
                    },
                    "protection_audit": {
                        "account_read_error": read_error,
                        "account_read_complete": account_read_complete,
                        "account_observed_at": account_observed_at,
                        "account_identity_present": account_identity_present,
                        "positions_checked": len(protection),
                        "states": {k: v["state"] for k, v in protection.items()},
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