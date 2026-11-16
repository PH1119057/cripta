"""Release-only readiness barrier for disarmed P90 private runtimes.

Read-only PostgreSQL plus credential-local signed IPC proof. No ARM, gate,
permission or Exchange mutation. The installer uses this only while P90 is
fully disarmed, after starting every previously-active MAIN/SUB private runtime
and before starting the observer.
"""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import psycopg

from bybit_workbench.universal_entry.p90l90_legacy_strategy import (
    MAIN_ID,
    SUB_ID,
)
from bybit_workbench.universal_entry.p90l90_real_account_routes import (
    P90RealAccountRoutesUnverified,
    read_p90_real_account_routes,
    read_p90_real_signed_flat_accounts,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.runtime_loader import load_active_strategy_bundles

DSN = "dbname=cripta user=cripta host=/var/run/postgresql"
WAIT_SECONDS = 120.0
POLL_SECONDS = 0.5
MAX_GENERATION_AGE_SECONDS = 15.0
IPC_ATTEMPT_SECONDS = 8.0


class P90ReleasePrivateRuntimeNotReady(RuntimeError):
    """Release must not start R06 before credential-local owners are ready."""


def _aware(value: object, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise P90ReleasePrivateRuntimeNotReady(
            "P90_RELEASE_PRIVATE_" + name.upper() + "_INVALID"
        )
    return value.astimezone(UTC)


def _not_before_from_env() -> datetime:
    raw = os.environ.get("CRIPTA_RELEASE_PRIVATE_RUNTIME_NOT_BEFORE", "").strip()
    if not raw:
        raise P90ReleasePrivateRuntimeNotReady(
            "P90_RELEASE_PRIVATE_NOT_BEFORE_MISSING"
        )
    try:
        return _aware(
            datetime.fromisoformat(raw.replace("Z", "+00:00")),
            "not_before",
        )
    except ValueError as exc:
        raise P90ReleasePrivateRuntimeNotReady(
            "P90_RELEASE_PRIVATE_NOT_BEFORE_INVALID"
        ) from exc


def _generation_ready(
    connection: Any,
    *,
    account_id: str,
    connector_id: str,
    not_before: datetime,
    now: datetime,
) -> bool:
    row = connection.execute(
        """SELECT generation_id,state,completed_at,positions_count,
                  active_orders_count,identity_status
             FROM runtime.account_state_generations
            WHERE trading_account_id=%s
              AND account_connector_id=%s
              AND state IN ('COMPLETE','FAILED')
            ORDER BY started_at DESC,generation_id DESC
            LIMIT 1""",
        (account_id, connector_id),
    ).fetchone()
    if (
        not isinstance(row, (tuple, list))
        or len(row) != 6
        or not isinstance(row[0], str)
        or not row[0]
        or row[1] != "COMPLETE"
        or not isinstance(row[2], datetime)
        or row[2].tzinfo is None
        or row[2].utcoffset() is None
        or type(row[3]) is not int
        or row[3] != 0
        or type(row[4]) is not int
        or row[4] != 0
        or row[5] != "PROVEN"
    ):
        return False
    completed = row[2].astimezone(UTC)
    age = (now - completed).total_seconds()
    return completed >= not_before and 0 <= age <= MAX_GENERATION_AGE_SECONDS


def wait_until_ready(
    connection: Any,
    *,
    not_before: datetime,
    timeout_seconds: float = WAIT_SECONDS,
    sleeper: Any = time.sleep,
    clock: Any = lambda: datetime.now(UTC),
) -> dict[str, object]:
    if timeout_seconds <= 0:
        raise P90ReleasePrivateRuntimeNotReady(
            "P90_RELEASE_PRIVATE_TIMEOUT_INVALID"
        )
    bundles = load_active_strategy_bundles(connection)
    p90 = tuple(
        bundle
        for bundle in bundles
        if bundle.card.strategy_id in {MAIN_ID, SUB_ID}
        and bundle.card.strategy_version == VERSION
    )
    if not p90:
        return {"status": "NOT_APPLICABLE", "strategies": 0}
    if len(p90) != 2 or {bundle.card.strategy_id for bundle in p90} != {
        MAIN_ID,
        SUB_ID,
    }:
        raise P90ReleasePrivateRuntimeNotReady(
            "P90_RELEASE_PRIVATE_P90_SCOPE_INVALID"
        )
    routes = read_p90_real_account_routes(
        connection,
        bundles=p90,
        at=_aware(clock(), "clock"),
    )
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    while True:
        attempts += 1
        current = _aware(clock(), "clock")
        generation_ready = all(
            _generation_ready(
                connection,
                account_id=route.trading_account_id,
                connector_id=route.account_connector_id,
                not_before=not_before,
                now=current,
            )
            for route in (routes.main, routes.sub)
        )
        if generation_ready:
            try:
                proofs = read_p90_real_signed_flat_accounts(
                    routes,
                    timeout_seconds=IPC_ATTEMPT_SECONDS,
                )
            except P90RealAccountRoutesUnverified as exc:
                if str(exc) != "P90_REAL_FLAT_ACCOUNT_PRIVATE_PROOF_MISSING":
                    raise P90ReleasePrivateRuntimeNotReady(
                        "P90_RELEASE_PRIVATE_PROOF_UNVERIFIED:" + str(exc)
                    ) from exc
            else:
                if len(proofs) != 2:
                    raise P90ReleasePrivateRuntimeNotReady(
                        "P90_RELEASE_PRIVATE_PROOF_COUNT_INVALID"
                    )
                return {
                    "status": "PASS",
                    "strategies": 2,
                    "accounts": 2,
                    "attempts": attempts,
                }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise P90ReleasePrivateRuntimeNotReady(
                "P90_RELEASE_PRIVATE_RUNTIME_READY_TIMEOUT"
            )
        sleeper(min(POLL_SECONDS, remaining))


def main() -> int:
    not_before = _not_before_from_env()
    with psycopg.connect(DSN) as connection:
        connection.execute("BEGIN READ ONLY")
        result = wait_until_ready(connection, not_before=not_before)
        connection.rollback()
    print(
        "P90_RELEASE_PRIVATE_RUNTIME_BARRIER="
        + str(result["status"])
        + " STRATEGIES="
        + str(result["strategies"])
        + " ACCOUNTS="
        + str(result.get("accounts", 0))
        + " ATTEMPTS="
        + str(result.get("attempts", 0))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
