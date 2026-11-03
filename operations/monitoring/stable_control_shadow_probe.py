#!/usr/bin/env python3
"""Read-only P90 legacy-versus-Stable-Core shadow probe.

Actor contract: cripta.  PostgreSQL is READ ONLY; status files are read only;
stdout JSON is the only output.  This script has no service or Exchange control.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import psycopg  # noqa: E402

from bybit_workbench.runtime_control_shadow import (  # noqa: E402
    LegacyP90Snapshot,
    evaluate_p90_shadow,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import (  # noqa: E402
    MAIN_ID,
    SUB_ID,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (  # noqa: E402
    ALLOWED_SYMBOLS,
    OWNER_ARM_STAGE_GATE_REASON,
    VERSION,
)

RUNTIME_CURRENT = Path("/srv/cripta/runtime/current")
OBSERVER_STATUS = Path("/var/lib/cripta/universal_entry_observer/status.json")
LIFECYCLE_STATUS = Path("/var/lib/cripta/lifecycle_supervisor/status.json")

EXPECTED_ROUTES = {
    MAIN_ID: (
        "ta-2122c81ecff14f3b85b102db605212f7",
        "ac-ed73de20410548d6841aeafa5f196f70",
    ),
    SUB_ID: (
        "ta-853ac5fcd58185697396d33cd57459fa",
        "ac-ec57819f875f1bfd929a382d6dc8a125",
    ),
}

DIAGNOSTIC_GATE_PREFIX = "owner full-contour diagnostic Entry window"


def _loaded_commit() -> str:
    commit = RUNTIME_CURRENT.resolve().name
    if len(commit) != 40:
        return ""
    try:
        int(commit, 16)
    except ValueError:
        return ""
    return commit


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}:{exc}"
    if not isinstance(raw, dict):
        return None, "NOT_OBJECT"
    return raw, "OK"


def _fresh_iso(value: object, *, now: datetime, max_age_seconds: float) -> bool:
    try:
        observed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return False
    if observed.tzinfo is None or observed.utcoffset() is None:
        return False
    age = (now - observed.astimezone(UTC)).total_seconds()
    return 0 <= age <= max_age_seconds


def _observer_health(*, loaded_commit: str, now: datetime) -> tuple[bool, str]:
    payload, detail = _read_json(OBSERVER_STATUS)
    if payload is None:
        return False, "OBSERVER_STATUS_" + detail
    ready = (
        payload.get("state") == "RUNNING"
        and payload.get("observer_ready") is True
        and str(payload.get("source_commit") or "") == loaded_commit
        and _fresh_iso(payload.get("updated_at"), now=now, max_age_seconds=15)
    )
    if ready:
        return True, "READY"
    return False, (
        "OBSERVER_NOT_READY:"
        + str(payload.get("state") or "UNKNOWN")
        + ":"
        + str(payload.get("reason") or "")
    )


def _lifecycle_hard_block(*, now: datetime) -> tuple[bool, str]:
    payload, detail = _read_json(LIFECYCLE_STATUS)
    if payload is None:
        return True, "LIFECYCLE_STATUS_" + detail
    faults = payload.get("active_fault_codes")
    if not isinstance(faults, list):
        return True, "LIFECYCLE_FAULT_LIST_INVALID"
    healthy = (
        payload.get("state") == "RUNNING"
        and _fresh_iso(payload.get("observed_at"), now=now, max_age_seconds=15)
        and not faults
    )
    if healthy:
        return False, "CLEAR"
    return True, "LIFECYCLE_NOT_READY:" + ",".join(str(item) for item in faults)


def _expected_permission_scopes() -> set[tuple[str, str]]:
    return {(MAIN_ID, VERSION), (SUB_ID, VERSION)}


def _expected_generic_arms() -> set[tuple[str, str, str]]:
    return {
        (strategy_id, VERSION, symbol)
        for strategy_id, symbols in ALLOWED_SYMBOLS.items()
        for symbol in symbols
    }


def _expected_account_arms() -> set[tuple[str, str, str, str]]:
    return {
        (strategy_id, VERSION, symbol, EXPECTED_ROUTES[strategy_id][0])
        for strategy_id, symbols in ALLOWED_SYMBOLS.items()
        for symbol in symbols
    }


def collect_shadow_snapshot(
    connection: psycopg.Connection[Any],
    *,
    loaded_commit: str,
    now: datetime,
) -> tuple[LegacyP90Snapshot, dict[str, object]]:
    """Collect one serial read-only P90 control snapshot."""

    connection.execute("SET TRANSACTION READ ONLY")

    gate = connection.execute(
        "SELECT enabled,reason FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    gate_open = bool(gate and gate[0])
    gate_reason = str(gate[1] if gate else "")

    permission_rows = connection.execute(
        """SELECT strategy_id,strategy_version
             FROM strategy_entry.execution_permissions
            WHERE enabled=true
            ORDER BY strategy_id,strategy_version"""
    ).fetchall()
    permission_scopes = {(str(row[0]), str(row[1])) for row in permission_rows}
    expected_permissions = _expected_permission_scopes()
    strategy_authorized_exact = permission_scopes == expected_permissions

    generic_rows = connection.execute(
        """SELECT strategy_id,strategy_version,symbol,release_commit
             FROM control.live_arm_sessions
            WHERE state='ACTIVE'
            ORDER BY strategy_id,symbol"""
    ).fetchall()
    generic_identities = {
        (str(row[0]), str(row[1]), str(row[2])) for row in generic_rows
    }
    expected_generic = _expected_generic_arms()
    generic_exact = (
        generic_identities == expected_generic
        and len(generic_rows) == len(expected_generic)
        and all(str(row[3]) == loaded_commit for row in generic_rows)
    )

    account_rows = connection.execute(
        """SELECT strategy_id,strategy_version,symbol,trading_account_id,release_commit
             FROM control.account_live_arm_sessions
            WHERE state='ACTIVE'
            ORDER BY strategy_id,symbol,trading_account_id"""
    ).fetchall()
    account_identities = {
        (str(row[0]), str(row[1]), str(row[2]), str(row[3]))
        for row in account_rows
    }
    expected_account = _expected_account_arms()
    account_exact = (
        account_identities == expected_account
        and len(account_rows) == len(expected_account)
        and all(str(row[4]) == loaded_commit for row in account_rows)
    )
    owner_scope_exact = generic_exact and account_exact

    consumer_rows = connection.execute(
        """SELECT trading_account_id,account_connector_id,release_commit,state,observed_at
             FROM runtime.entry_consumer_health
            WHERE trading_account_id = ANY(%s)
            ORDER BY trading_account_id""",
        (sorted(route[0] for route in EXPECTED_ROUTES.values()),),
    ).fetchall()
    consumers_by_account = {str(row[0]): row for row in consumer_rows}
    consumer_failures: list[str] = []
    for account_id, connector_id in EXPECTED_ROUTES.values():
        row = consumers_by_account.get(account_id)
        if row is None:
            consumer_failures.append(account_id + ":MISSING")
            continue
        if str(row[1]) != connector_id:
            consumer_failures.append(account_id + ":CONNECTOR")
        elif str(row[2]) != loaded_commit:
            consumer_failures.append(account_id + ":RELEASE")
        elif str(row[3]) != "READY":
            consumer_failures.append(account_id + ":STATE")
        elif not (
            isinstance(row[4], datetime)
            and row[4].tzinfo is not None
            and 0 <= (now - row[4].astimezone(UTC)).total_seconds() <= 8
        ):
            consumer_failures.append(account_id + ":STALE")
    consumers_ready = not consumer_failures

    observer_ready, observer_detail = _observer_health(
        loaded_commit=loaded_commit,
        now=now,
    )
    hard_block, lifecycle_detail = _lifecycle_hard_block(now=now)

    release_scope_exact = bool(loaded_commit)
    active_non_p90 = (
        bool(permission_scopes - expected_permissions)
        or bool(generic_identities - expected_generic)
        or bool(account_identities - expected_account)
    )
    non_p90_control_scope = (
        active_non_p90 or gate_reason.startswith(DIAGNOSTIC_GATE_PREFIX)
    )
    p90_staging = gate_reason == OWNER_ARM_STAGE_GATE_REASON

    snapshot = LegacyP90Snapshot(
        gate_open=gate_open,
        gate_reason=gate_reason,
        owner_scope_exact=owner_scope_exact,
        strategy_authorized_exact=strategy_authorized_exact,
        release_scope_exact=release_scope_exact,
        observer_ready=observer_ready,
        consumers_ready=consumers_ready,
        hard_safety_block=hard_block,
        p90_staging=p90_staging,
        non_p90_control_scope=non_p90_control_scope,
    )
    evidence: dict[str, object] = {
        "loaded_commit": loaded_commit,
        "permission_scopes": sorted(permission_scopes),
        "generic_arm_count": len(generic_rows),
        "account_arm_count": len(account_rows),
        "observer_detail": observer_detail,
        "consumer_failures": consumer_failures,
        "lifecycle_detail": lifecycle_detail,
    }
    return snapshot, evidence


def result_json(
    snapshot: LegacyP90Snapshot,
    evidence: dict[str, object],
) -> dict[str, object]:
    evaluation = evaluate_p90_shadow(snapshot)
    core = evaluation.core_state
    return {
        "classification": evaluation.classification.value,
        "legacy_effective_new_entry": evaluation.legacy_effective_new_entry,
        "core_effective_new_entry": evaluation.core_effective_new_entry,
        "snapshot": {
            "gate_open": snapshot.gate_open,
            "gate_reason": snapshot.gate_reason,
            "owner_scope_exact": snapshot.owner_scope_exact,
            "strategy_authorized_exact": snapshot.strategy_authorized_exact,
            "release_scope_exact": snapshot.release_scope_exact,
            "observer_ready": snapshot.observer_ready,
            "consumers_ready": snapshot.consumers_ready,
            "hard_safety_block": snapshot.hard_safety_block,
            "p90_staging": snapshot.p90_staging,
            "non_p90_control_scope": snapshot.non_p90_control_scope,
        },
        "core_state": {
            "owner_authorized": core.owner_authorized,
            "strategy_authorized": core.strategy_authorized,
            "technical_readiness": core.technical_readiness.value,
            "exact_scope": core.exact_scope,
            "hard_safety_block": core.hard_safety_block,
        },
        "evidence": evidence,
    }


def main() -> int:
    now = datetime.now(UTC)
    loaded_commit = _loaded_commit()
    with psycopg.connect("dbname=cripta") as connection:
        snapshot, evidence = collect_shadow_snapshot(
            connection,
            loaded_commit=loaded_commit,
            now=now,
        )
    print(json.dumps(result_json(snapshot, evidence), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
