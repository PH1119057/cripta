"""Account-local independent lifecycle/safety reader (inert until installed).

This process does not retire LIVE arms, deliver owner alerts, select strategies,
or mutate exchange orders. A separate singleton coordinates global duties.
Only exact authenticated Bybit GET evidence may certify account exposure.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.exchange.bybit.account_safety_reader import (
    AccountSafetyFacts, read_account_safety,
)
from bybit_workbench.exchange.bybit.protection_probe import direct_account_snapshot
from bybit_workbench.exchange.bybit.transport import LifecycleAccountReadTransport
from bybit_workbench.exchange.bybit.protection_supervisor_bridge import (
    account_protection_verdicts,
)
from bybit_workbench.lifecycle_exposure_bridge import verified_account_exposure
from bybit_workbench.lifecycle_supervisor import (
    LifecycleSupervisor, LifecycleSupervisorPolicy,
)

_ACCOUNT_ID = re.compile(r"^ta-[0-9a-f]{32}$")
_CREDENTIAL_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,90}$")
_running = True


def validated_scope(
    account_id: str, connector_id: str, credential_name: str,
    credential_directory: str, status_path: str,
) -> tuple[Path, Path, str]:
    """Validate exact isolation; no implicit MAIN account or credential fallback."""
    if not _ACCOUNT_ID.fullmatch(account_id):
        raise RuntimeError("ACCOUNT_LOCAL_TRADING_ACCOUNT_INVALID")
    if not connector_id.startswith("ac-") or len(connector_id) < 5:
        raise RuntimeError("ACCOUNT_LOCAL_CONNECTOR_MISSING")
    if not _CREDENTIAL_NAME.fullmatch(credential_name):
        raise RuntimeError("ACCOUNT_LOCAL_CREDENTIAL_NAME_INVALID")
    directory = Path(credential_directory)
    if (
        not directory.is_absolute()
        or directory.parent != Path("/run/credentials")
        or not directory.name.startswith("cripta-lifecycle-account@")
    ):
        raise RuntimeError("ACCOUNT_LOCAL_CREDENTIAL_MOUNT_UNTRUSTED")
    credential = directory / credential_name
    output = Path(status_path)
    if not output.is_absolute() or output.name != "status.json":
        raise RuntimeError("ACCOUNT_LOCAL_STATUS_PATH_INVALID")
    if output.parent.name != "account-" + account_id:
        raise RuntimeError("ACCOUNT_LOCAL_STATUS_NOT_ACCOUNT_SCOPED")
    if credential_name == "bybit-mainnet":
        # This is the existing MAIN-only credential. No SUB impersonation.
        raise RuntimeError("ACCOUNT_LOCAL_MAIN_CREDENTIAL_NOT_PERMITTED")
    return credential, output, "systemd-credential:" + credential_name


def account_positions(connection: Any, *, account_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT position_id,symbol,side,position_idx,actual_avg_fill,
                  entry_command_id,fill_at
             FROM runtime.position_ownership
            WHERE state='OPEN' AND identity_status='PROVEN'
              AND trading_account_id=%s ORDER BY position_id""",
        (account_id,),
    ).fetchall()
    return [dict(x) for x in rows]


def require_sub_account(connection: Any, *, account_id: str, connector_id: str) -> None:
    rows = connection.execute(
        """SELECT a.account_kind,a.status AS account_status,
                    c.status AS connector_status,c.trading_account_id
             FROM identity.trading_accounts a
             JOIN identity.account_connectors c
               ON c.trading_account_id=a.trading_account_id
            WHERE a.trading_account_id=%s AND c.account_connector_id=%s""",
        (account_id, connector_id),
    ).fetchall()
    if len(rows) != 1:
        raise RuntimeError("ACCOUNT_LOCAL_SUB_IDENTITY_NOT_READY")
    row = rows[0]
    if (
        row["account_kind"] != "SUB"
        or row["account_status"] != "ACTIVE"
        or row["connector_status"] != "ACTIVE"
        or row["trading_account_id"] != account_id
    ):
        raise RuntimeError("ACCOUNT_LOCAL_SUB_IDENTITY_NOT_READY")


def inspect_account_once(
    connection: Any, *, account_id: str, connector_id: str,
    credential_ref: str, account_snapshot: Mapping[str, object] | None,
    now: datetime, max_age_seconds: int = 15,
) -> dict[str, object]:
    """DB audit and scoped faults only after separately authenticated read."""
    require_sub_account(connection, account_id=account_id, connector_id=connector_id)
    positions = account_positions(connection, account_id=account_id)
    audit = verified_account_exposure(
        connection,
        account_snapshot=account_snapshot,
        trading_account_id=account_id,
        account_connector_id=connector_id,
        verified_credential_ref=credential_ref,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    # A missing Exchange read cannot resolve either a prior owner fault or a
    # currently unobserved physical position. Keep durable fault history open.
    if audit.verified_positions is None:
        return {
            "state": "UNKNOWN",
            "reasons": list(audit.reasons),
            "positions_seen": len(positions),
            "trading_rights": "NONE",
        }
    protection = account_protection_verdicts(
        connection, positions=positions, account_evidence=account_snapshot,
        now=now, trading_account_id=account_id,
    )
    outcome = LifecycleSupervisor(
        connection,
        policy=LifecycleSupervisorPolicy(
            exchange_state_max_age_seconds=max_age_seconds,
            exit_owner_max_age_seconds=10,
        ),
        exchange_protection=protection,
        exchange_exposure=audit.verified_positions,
        trading_account_id=account_id,
        owns_global_faults=False,
    ).scan(now=now)
    return {
        "state": "VERIFIED_ACCOUNT_SCAN",
        "positions_seen": len(positions),
        "exchange_positions_seen": len(audit.verified_positions),
        "protection_states": {k: v["state"] for k, v in protection.items()},
        "opened_faults": outcome.open_faults,
        "resolved_faults": outcome.resolved_faults,
        "trading_rights": "NONE",
    }


def record_proven_safety(
    connection: Any, *, account_id: str, connector_id: str,
    observed_at: datetime, snapshot: Mapping[str, object],
    lifecycle: Mapping[str, object], facts: AccountSafetyFacts,
) -> None:
    if lifecycle.get("state") != "VERIFIED_ACCOUNT_SCAN":
        raise RuntimeError("ACCOUNT_SAFETY_SIGNED_UID_NOT_VERIFIED")
    positions = snapshot.get("account_wide_positions")
    if snapshot.get("account_wide_pagination_complete") is not True or not isinstance(positions, list):
        raise RuntimeError("ACCOUNT_SAFETY_POSITION_INVENTORY_INCOMPLETE")
    count = 0
    for item in positions:
        if not isinstance(item, Mapping):
            raise RuntimeError("ACCOUNT_SAFETY_POSITION_ROW_INVALID")
        size = Decimal(str(item.get("size")))
        if not size.is_finite() or size < 0:
            raise RuntimeError("ACCOUNT_SAFETY_POSITION_SIZE_INVALID")
        count += int(size > 0)
    protections = lifecycle.get("protection_states")
    if not isinstance(protections, dict):
        raise RuntimeError("ACCOUNT_SAFETY_PROTECTION_STATES_INVALID")
    connection.execute(
        """INSERT INTO safety.account_exchange_snapshots(
            trading_account_id,account_connector_id,observed_at,source,
            evidence_status,total_equity,available_balance,wallet_balance,
            open_positions,open_orders,protection_checked_count,
            protection_verified_count
        ) VALUES (%s,%s,%s,'SIGNED_BYBIT_ACCOUNT_GET','VERIFIED',
                  %s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (account_id, connector_id, observed_at,
         facts.total_equity, facts.available_balance, facts.wallet_balance,
         count, facts.open_orders, len(protections),
         sum(x == "VERIFIED" for x in protections.values())),
    )


def _status(path: Path, data: Mapping[str, object]) -> None:
    # Writable parent must be prepared by exact cripta actor before start.
    if not path.parent.is_dir():
        raise RuntimeError("ACCOUNT_LOCAL_STATUS_DIRECTORY_NOT_PREPARED")
    tmp = path.with_name("status.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _stop(_signum: int, _frame: object) -> None:
    global _running
    _running = False


def main() -> int:
    if os.environ.get("CRIPTA_LIFECYCLE_ACCOUNT_WORKER") != "ENABLED":
        raise SystemExit("ACCOUNT_LOCAL_WORKER_DISABLED")
    account_id = os.environ.get("CRIPTA_TRADING_ACCOUNT_ID", "")
    connector_id = os.environ.get("CRIPTA_ACCOUNT_CONNECTOR_ID", "")
    credential_name = os.environ.get("CRIPTA_LIFECYCLE_CREDENTIAL_NAME", "")
    credential, status, credential_ref = validated_scope(
        account_id, connector_id, credential_name,
        os.environ.get("CREDENTIALS_DIRECTORY", ""),
        os.environ.get("CRIPTA_LIFECYCLE_ACCOUNT_STATUS_PATH", ""),
    )
    poll = int(os.environ.get("CRIPTA_LIFECYCLE_ACCOUNT_POLL_SECONDS", "15"))
    if poll < 10:
        raise SystemExit("ACCOUNT_LOCAL_POLL_INTERVAL_INVALID")
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    with psycopg.connect(
        os.environ.get(
            "CRIPTA_DATABASE_DSN",
            "dbname=cripta user=cripta host=/var/run/postgresql application_name=account-lifecycle",
        ),
        row_factory=dict_row,
    ) as connection:
        while _running:
            try:
                with connection.transaction():
                    require_sub_account(
                        connection, account_id=account_id, connector_id=connector_id
                    )
                    positions = account_positions(connection, account_id=account_id)
                symbols = tuple(sorted({str(x["symbol"]) for x in positions}))
                # No credential values, UID or Bybit response are persisted/logged.
                fields = json.loads(credential.read_text(encoding="utf-8"))
                base_url = os.environ.get("BYBIT_REST", "https://api.bybit.kz")
                snapshot = asyncio.run(direct_account_snapshot(
                    symbols, api_key=str(fields["api_key"]),
                    api_secret=str(fields["api_secret"]), base_url=base_url,
                ))
                safety_facts = asyncio.run(read_account_safety(
                    LifecycleAccountReadTransport(
                        api_key=str(fields["api_key"]),
                        api_secret=str(fields["api_secret"]), base_url=base_url,
                    )
                ))
                with connection.transaction():
                    result = inspect_account_once(
                        connection, account_id=account_id, connector_id=connector_id,
                        credential_ref=credential_ref,
                        account_snapshot=snapshot, now=datetime.now(UTC),
                    )
                    if result.get("state") == "VERIFIED_ACCOUNT_SCAN":
                        record_proven_safety(
                            connection, account_id=account_id,
                            connector_id=connector_id,
                            observed_at=datetime.now(UTC), snapshot=snapshot,
                            lifecycle=result, facts=safety_facts,
                        )
                state = {"observed_at": datetime.now(UTC).isoformat(), **result}
            except Exception as exc:
                connection.rollback()
                # Never serialize raw exception text: SDKs may include private data.
                state = {
                    "observed_at": datetime.now(UTC).isoformat(),
                    "state": "UNKNOWN", "error_class": type(exc).__name__,
                    "trading_rights": "NONE",
                }
            _status(status, state)
            time.sleep(poll)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
