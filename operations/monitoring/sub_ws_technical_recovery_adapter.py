"""Account-scoped technical recovery adapter owned by Runtime Recovery Supervisor.

Exactly one SUB account and one loaded release. No systemd action, trading
orders, gate/OWNER ARM/Strategy mutation, or direct table DELETE is permitted.
The independent credential-bound private IPC performs signed read-only GETs;
the restricted PostgreSQL control transition repeats all durable invariants.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from bybit_workbench.account_ws_technical_recovery import (
    WsTechnicalRecoveryAction,
    WsTechnicalRecoveryEvidence,
    plan_ws_technical_recovery,
)
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateProofClient,
)

ACCOUNT = "ta-853ac5fcd58185697396d33cd57459fa"
CONNECTOR = "ac-ec57819f875f1bfd929a382d6dc8a125"
SHA = re.compile(r"^[0-9a-f]{40}$")
STATE = Path("/var/lib/cripta-runtime-recovery/ws_technical.json")
PRIVATE = Path(
    f"/var/lib/cripta/private_runtime/sub-{ACCOUNT}/status-{ACCOUNT}.json"
)
LIFECYCLE = Path("/var/lib/cripta/lifecycle_supervisor/status.json")
SUB_LIFECYCLE = Path(
    f"/var/lib/cripta/lifecycle_account/account-{ACCOUNT}/status.json"
)
SUB_POSITION = Path(
    f"/var/lib/cripta/account_position/account-{ACCOUNT}/status.json"
)
INSTALLED = Path("/var/lib/cripta/release/INSTALLED_COMMIT")
CURRENT = Path("/srv/cripta/runtime/current")
DSN = (
    "dbname=cripta user=cripta host=/var/run/postgresql "
    "application_name=account-ws-technical-recovery"
)
TOPICS = {"order.linear", "execution.linear", "position.linear", "wallet"}


def _block(reason: str) -> dict[str, object]:
    return {"state": "BLOCKED", "reason": reason, "exchange_mutation": False,
            "owner_arm_change": False, "gate_change": False}


def _release() -> str:
    try:
        marker = INSTALLED.read_text(encoding="ascii").strip().lower()
        current = CURRENT.resolve(strict=True)
        inner = (CURRENT / "INSTALLED_COMMIT").read_text(encoding="ascii").strip()
    except OSError:
        return ""
    if not SHA.fullmatch(marker) or marker != current.name or marker != inner:
        return ""
    return marker


def _json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("RECOVERY_STATUS_NOT_OBJECT")
    return raw


def _fresh_iso(value: object, *, now: datetime, ttl: int) -> bool:
    try:
        observed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return False
    return (
        observed.tzinfo is not None
        and observed.utcoffset() is not None
        and 0 <= (now - observed.astimezone(UTC)).total_seconds() <= ttl
    )


def _sub_hard_faults_clear() -> bool:
    """A MAIN-account fault is not a SUB-account hard-safety fault."""
    with psycopg.connect(DSN, connect_timeout=3) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute(
            """SELECT count(*) FROM runtime.lifecycle_faults
               WHERE state='OPEN' AND severity='CRITICAL'
                 AND (
                   coalesce(nullif(exact_ids->>'fault_scope_kind',''),'GLOBAL')
                     <> 'ACCOUNT'
                   OR exact_ids->>'trading_account_id'=%s
                 )""",
            (ACCOUNT,),
        ).fetchone()
    return row == (0,)


def _independent_lifecycle(now: datetime) -> tuple[bool, bool]:
    """Prove local SUB Exit/protection, not unrelated MAIN positions."""
    global_service = _json(LIFECYCLE)
    local = _json(SUB_LIFECYCLE)
    position = _json(SUB_POSITION)
    if (
        global_service.get("state") != "RUNNING"
        or not _fresh_iso(global_service.get("observed_at"), now=now, ttl=15)
        or not _sub_hard_faults_clear()
    ):
        return False, False
    count = local.get("positions_seen")
    exchange_count = local.get("exchange_positions_seen")
    protections = local.get("protection_states")
    local_ready = (
        local.get("state") == "VERIFIED_ACCOUNT_SCAN"
        and _fresh_iso(local.get("observed_at"), now=now, ttl=30)
        and type(count) is int and count >= 0
        and type(exchange_count) is int and exchange_count == count
        and local.get("opened_faults") == 0
        and isinstance(protections, dict)
        and len(protections) == count
        and all(value == "VERIFIED" for value in protections.values())
    )
    sampled = position.get("sampled")
    position_ready = (
        position.get("state") == "SAMPLED_PARTIAL"
        and _fresh_iso(position.get("observed_at"), now=now, ttl=45)
        and type(sampled) is int and type(count) is int
        and sampled >= count
    )
    return local_ready, local_ready and position_ready


def _signed_inventory_matches(signed_positions: object, *, now: datetime) -> bool:
    """No fabricated flat precondition: verify every signed open slot/Exit."""
    from decimal import Decimal, InvalidOperation

    if not isinstance(signed_positions, (tuple, list)):
        return False
    with psycopg.connect(DSN, connect_timeout=3) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute(
            """SELECT p.position_id,p.symbol,p.position_idx,p.side,p.actual_qty,p.state,
                      p.close_link_status,p.initial_protection_confirmed_at,
                      s.claim_state,s.strategy_position_id,
                      x.status,x.exit_plan_fingerprint,x.last_seen_at,
                      p.exit_plan_fingerprint
                 FROM runtime.position_ownership p
                 LEFT JOIN runtime.exchange_position_slot_claims s
                   ON s.exchange_position_slot_claim_id=p.exchange_position_slot_claim_id
                  AND s.trading_account_id=p.trading_account_id
                  AND s.identity_status='PROVEN'
                  AND s.exchange_position_key=p.exchange_position_key
                 LEFT JOIN runtime.position_exit_claims x
                   ON x.strategy_position_id=p.position_id
                WHERE p.trading_account_id=%s
                  AND p.identity_status='PROVEN'
                  AND (p.state IN ('OPEN','RECONCILIATION_REQUIRED')
                       OR p.close_link_status='UNRESOLVED_EXACT_LINK')""",
            (ACCOUNT,),
        ).fetchall()
    if len(rows) != len(signed_positions):
        return False
    matching: dict[tuple[str, int, str], tuple[object, ...]] = {}
    for row in rows:
        (
            position_id, symbol, idx, side, qty, state, link, protected,
            slot, owner, claim, plan, seen, actual_plan,
        ) = row
        if (
            state != "OPEN" or link != "OPEN" or slot != "BOUND"
            or owner != position_id or not position_id
            or claim != "CLAIMED" or plan != actual_plan
            or protected is None or not isinstance(seen, datetime)
            or seen.tzinfo is None
            or not 0 <= (now - seen).total_seconds() <= 15
        ):
            return False
        key = (str(symbol), int(idx), str(side))
        if key in matching:
            return False
        matching[key] = row
    for signed in signed_positions:
        if not isinstance(signed, dict):
            return False
        try:
            key = (str(signed["symbol"]), int(signed["positionIdx"]), str(signed["side"]))
            signed_qty = Decimal(str(signed["size"]))
            local_qty = Decimal(str(matching[key][4]))
        except (KeyError, ValueError, TypeError, InvalidOperation):
            return False
        if not 0 < signed_qty <= local_qty:
            return False
    return True


def _ws_heartbeat(*, blocked_at: datetime, now: datetime) -> datetime | None:
    raw = _json(PRIVATE)
    identity = raw.get("identity")
    private = raw.get("private")
    if (
        not isinstance(identity, dict)
        or identity.get("state") != "READY"
        or identity.get("trading_account_id") != ACCOUNT
        or identity.get("account_connector_id") != CONNECTOR
        or not isinstance(private, dict)
        or private.get("state") != "connected"
        or not isinstance(private.get("subscriptions_topics"), list)
        or set(private["subscriptions_topics"]) != TOPICS
    ):
        return None
    try:
        heartbeat = datetime.fromtimestamp(
            int(private["last_verified_message_epoch"]), UTC
        )
        confirmed = datetime.fromtimestamp(
            int(private["subscriptions_confirmed_at_epoch"]), UTC
        )
        connected = datetime.fromtimestamp(
            int(private["connected_at_epoch"]), UTC
        )
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        return None
    if (
        confirmed < blocked_at
        or connected < blocked_at
        or not 0 <= (now - heartbeat).total_seconds() <= 15
        or heartbeat < connected
    ):
        return None
    return heartbeat


def _latch_and_reconnect() -> tuple[tuple[datetime, str] | None, tuple[int, datetime] | None]:
    with psycopg.connect(DSN, connect_timeout=3) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        block = conn.execute(
            """SELECT blocked_at,reason FROM runtime.account_entry_safety_blocks
               WHERE trading_account_id=%s AND account_connector_id=%s""",
            (ACCOUNT, CONNECTOR),
        ).fetchone()
        if not block:
            return None, None
        event = conn.execute(
            """SELECT id,at_epoch_ms FROM runtime.connection_events
                WHERE trading_account_id=%s AND account_connector_id=%s
                  AND channel='private' AND event='reconnect_verified'
                  AND identity_status='PROVEN'
                ORDER BY id DESC LIMIT 1""",
            (ACCOUNT, CONNECTOR),
        ).fetchone()
    candidate = (
        (int(event[0]), datetime.fromtimestamp(int(event[1]) / 1000, UTC))
        if event else None
    )
    return (block[0], str(block[1])), candidate


def _generation_and_uid(generation: str) -> tuple[object, ...] | None:
    with psycopg.connect(DSN, connect_timeout=3) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute(
            """SELECT a.external_account_ref,
                      g.generation_id,g.state,g.completed_at,
                      g.positions_count,g.active_orders_count
                 FROM identity.trading_accounts a
                 JOIN identity.account_connectors c
                   ON c.trading_account_id=a.trading_account_id
                 JOIN runtime.account_state_generations g
                   ON g.trading_account_id=a.trading_account_id
                  AND g.account_connector_id=c.account_connector_id
                WHERE a.trading_account_id=%s AND c.account_connector_id=%s
                  AND a.environment='MAINNET' AND a.status='ACTIVE'
                  AND c.status='ACTIVE' AND c.retired_at IS NULL
                  AND g.generation_id=%s AND g.identity_status='PROVEN'""",
            (ACCOUNT, CONNECTOR, generation),
        ).fetchone()
    return row


def _journal(blocked_at: datetime, release: str) -> dict[str, object]:
    if not STATE.parent.is_dir() or STATE.parent.is_symlink():
        raise RuntimeError("RECOVERY_STATE_DIRECTORY_UNPREPARED")
    if not STATE.exists():
        return {"attempts": 0, "phase": "IDLE"}
    raw = _json(STATE)
    if raw.get("release") != release or raw.get("blocked_at") != blocked_at.isoformat():
        return {"attempts": 0, "phase": "IDLE"}
    attempts = raw.get("attempts")
    if type(attempts) is not int or not 0 <= attempts <= 2:
        raise RuntimeError("RECOVERY_JOURNAL_ATTEMPTS_UNVERIFIED")
    return {"attempts": attempts, "phase": raw.get("phase", "BLOCKED")}


def _save(
    *, blocked_at: datetime, release: str, attempts: int, phase: str, reason: str,
) -> None:
    if not STATE.parent.is_dir() or STATE.parent.is_symlink():
        raise RuntimeError("RECOVERY_STATE_DIRECTORY_UNPREPARED")
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({
            "release": release, "blocked_at": blocked_at.isoformat(),
            "attempts": attempts, "phase": phase, "reason": reason,
        }, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(STATE)


def run_once(*, now: datetime) -> dict[str, object]:
    """Bounded independent WS proof; only the exact DB function can clear."""
    release = _release()
    if not release:
        return _block("RELEASE_IDENTITY_UNVERIFIED")
    block, event = _latch_and_reconnect()
    if block is None:
        return {"state": "NO_LATCH", "release": release}
    blocked_at, reason = block
    if reason != "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN":
        return _block("NONTRANSIENT_ACCOUNT_SAFETY_BLOCK")
    journal = _journal(blocked_at, release)
    attempts = int(journal["attempts"])
    if journal["phase"] not in {"IDLE", "COMPLETE"}:
        return _block("PRIOR_RECOVERY_OUTCOME_UNCERTAIN")
    if attempts >= 2:
        return _block("RECOVERY_ATTEMPT_BUDGET_EXHAUSTED")
    if event is None:
        return _block("POST_BLOCK_RECONNECT_UNVERIFIED")
    event_id, reconnect_at = event
    if reconnect_at < blocked_at:
        return _block("POST_BLOCK_RECONNECT_UNVERIFIED")
    # Hard gate before any signed remote read. Re-read after proof as well.
    protection, exit_owner = _independent_lifecycle(now)
    if not protection or not exit_owner:
        return _block("CRITICAL_OR_EXIT_HEALTH_UNVERIFIED")
    heartbeat = _ws_heartbeat(blocked_at=blocked_at, now=now)
    if heartbeat is None:
        return _block("PRIVATE_WS_HEARTBEAT_UNVERIFIED")
    # Signed exact-account inventory may legitimately contain OPEN positions.
    # Its producer checks protection/Exit-continuity and current in-process WS.
    proof = P90PrivateProofClient(
        account=ACCOUNT, connector=CONNECTOR, timeout_seconds=15.0,
    ).read_recovery_account()
    current = datetime.now(UTC)
    protection, exit_owner = _independent_lifecycle(current)
    latest_heartbeat = _ws_heartbeat(blocked_at=blocked_at, now=current)
    row = _generation_and_uid(proof.private_generation_id)
    if (
        row is None or latest_heartbeat is None
        or not _signed_inventory_matches(proof.positions, now=current)
    ):
        return _block("POST_PROOF_SCOPE_OR_EXPOSURE_UNVERIFIED")
    uid, gen, state, completed, positions, orders = row
    if type(positions) is not int or positions != len(proof.positions):
        return _block("SIGNED_EXCHANGE_GENERATION_COUNT_MISMATCH")
    evidence = WsTechnicalRecoveryEvidence(
        account_id=ACCOUNT, connector_id=CONNECTOR,
        expected_account_id=ACCOUNT, expected_connector_id=CONNECTOR,
        loaded_commit=release, expected_commit=release,
        safety_reason=reason, blocked_at=blocked_at,
        reconnect_verified_at=reconnect_at,
        ws_heartbeat_at=latest_heartbeat, private_topics_confirmed=True,
        independent_proof_verified=True,
        proof_source_ref=proof.source_ref,
        proof_account_id=proof.trading_account_id,
        signed_uid=proof.signed_uid, registered_uid=str(uid),
        private_generation_id=proof.private_generation_id,
        complete_generation_id=str(gen),
        generation_complete=state == "COMPLETE",
        proof_observed_at=proof.observed_at,
        generation_completed_at=completed,
        positions_count=positions, open_orders=proof.open_orders,
        pending_commands=proof.pending_commands,
        protection_verified=protection, exit_continuity_verified=exit_owner,
        ambiguous_exchange_mutation=False, critical_lifecycle_fault=False,
        attempts_used=attempts,
    )
    decision = plan_ws_technical_recovery(evidence, now=current)
    if decision.action is not WsTechnicalRecoveryAction.REQUEST_INDEPENDENT_CLEAR:
        return _block(decision.reason)
    # Durable write-ahead record: any crash/timeout => UNKNOWN, never retry
    # the same block blindly. Database function repeats identity/race/hard gates.
    _save(
        blocked_at=blocked_at, release=release, attempts=attempts + 1,
        phase="PENDING", reason="CONTROL_REQUEST_UNCERTAIN",
    )
    with psycopg.connect(DSN, connect_timeout=3) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        response = conn.execute(
            """SELECT control.clear_sub_ws_transient_latch(
                 %s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                ACCOUNT, CONNECTOR, release, proof.private_generation_id,
                proof.signed_uid, event_id, blocked_at, proof.observed_at,
            ),
        ).fetchone()
        if response != (ACCOUNT,):
            raise RuntimeError("RECOVERY_CONTROL_RESULT_UNVERIFIED")
        conn.commit()
    _save(
        blocked_at=blocked_at, release=release, attempts=attempts + 1,
        phase="COMPLETE", reason="SCOPED_TECHNICAL_LATCH_CLEARED",
    )
    return {
        "state": "RECOVERED", "reason": "INDEPENDENT_TECHNICAL_CLEAR_PROVEN",
        "owner_arm_change": False, "gate_change": False, "exchange_mutation": False,
    }
