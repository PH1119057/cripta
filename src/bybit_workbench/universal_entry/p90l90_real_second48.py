"""P90L90 REAL v3 SECOND48 reconstructed from exact, durable actual close facts.

This reader is READ ONLY. Its caller must keep one database transaction from
this proof through account Entry admission (including account-scoped lock).
It is not an owner-arm, Exchange credential proof, or release readiness gate.
Unlinked, partial or unknown actual Exit evidence never counts as a non-stop.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol

from .p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION
from .p90l90_strategy_embargo import (
    StrategyHardStopEmbargo,
    StrategyHardStopEmbargoPolicy,
)

_ALLOWED_EXITS = frozenset({
    "INITIAL_HARD_STOP", "PROFIT_PROTECTION_STOP", "TRAILING_STOP",
    "TAKE_PROFIT", "STRATEGY_EXIT", "OWNER_MANUAL_CLOSE",
    "OWNER_MODIFIED_STOP",
})


class _Rows(Protocol):
    def fetchall(self) -> Sequence[Mapping[str, object]]: ...


class _Connection(Protocol):
    def execute(
        self, statement: str, parameters: Sequence[object] = (),
    ) -> _Rows: ...


class P90RealSecond48Unverified(RuntimeError):
    """Exact scope, complete close proof or embargo lineage was not established."""


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise P90RealSecond48Unverified(f"P90_REAL_SECOND48_{label}_INVALID")
    return value.astimezone(UTC)


def _exact(value: object) -> str:
    return value if isinstance(value, str) else ""


def read_real_second48(
    connection: _Connection,
    *,
    strategy_id: str,
    strategy_version: str,
    strategy_config_fingerprint: str,
    trading_account_id: str,
    policy: StrategyHardStopEmbargoPolicy,
    evaluated_at: datetime,
) -> tuple[bool, dict[str, object]]:
    """Replay all exact persisted CLOSED positions, independently per Strategy.

    The query is intentionally not restricted to a recent 48h window: an old
    trigger can impose a still-active pause. Clock/order and missing-attribution
    ambiguity are fatal, never interpreted as zero hard stops.
    """
    now = _utc(evaluated_at, "EVALUATED_AT")
    if (
        strategy_id not in ALLOWED_SYMBOLS
        or strategy_version != VERSION
        or not strategy_config_fingerprint.strip()
        or not trading_account_id.strip()
        or not policy.enabled
        or policy.hardstop_count != 2
        or policy.rolling_hours != 48
        or policy.new_entry_pause_hours != 48
        or policy.close_existing_positions
        or policy.domain != "STRATEGY_VERSION"
    ):
        raise P90RealSecond48Unverified("P90_REAL_SECOND48_POLICY_OR_SCOPE_INVALID")
    expected_kind = (
        "MAIN" if strategy_id == "p90l90_legacy_main_ltc_inj" else "SUB"
    )
    # The same xact-scoped account lock used by private reconciliation and
    # Entry admission serializes actual CLOSED attribution vs a new Entry.
    # Caller MUST retain this exact transaction until capital/slot admission.
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (trading_account_id,),
    )
    rows = connection.execute(
        """SELECT p.position_id,p.state,p.closed_at,p.close_link_status,
                  p.identity_status,p.trading_account_id,
                  ta.account_kind,a.attribution_id,a.closed_at AS attribution_closed_at,
                  a.link_status AS attribution_link_status,
                  a.exit_owner,a.exit_mechanism,a.exit_order_id
             FROM runtime.position_ownership p
             LEFT JOIN identity.trading_accounts ta
               ON ta.trading_account_id=p.trading_account_id
             LEFT JOIN runtime.position_exit_attribution a
               ON a.position_id=p.position_id
            WHERE p.strategy_id=%s AND p.strategy_version=%s
              AND p.strategy_config_fingerprint=%s
            ORDER BY p.fill_at,p.position_id""",
        (strategy_id, strategy_version, strategy_config_fingerprint),
    ).fetchall()
    if not isinstance(rows, Sequence):
        raise P90RealSecond48Unverified("P90_REAL_SECOND48_ROWS_NOT_PROVEN")
    events: list[tuple[datetime, str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise P90RealSecond48Unverified("P90_REAL_SECOND48_ROW_NOT_PROVEN")
        position_id = _exact(row.get("position_id"))
        state = _exact(row.get("state"))
        if not position_id or position_id in seen:
            raise P90RealSecond48Unverified("P90_REAL_SECOND48_DUPLICATE_POSITION")
        seen.add(position_id)
        if (
            row.get("identity_status") != "PROVEN"
            or row.get("trading_account_id") != trading_account_id
            or row.get("account_kind") != expected_kind
        ):
            raise P90RealSecond48Unverified("P90_REAL_SECOND48_ACCOUNT_LINEAGE_INVALID")
        if state == "OPEN":
            if row.get("closed_at") is not None or row.get("attribution_id") is not None:
                raise P90RealSecond48Unverified("P90_REAL_SECOND48_OPEN_POSITION_CORRUPT")
            continue
        if state != "CLOSED":
            raise P90RealSecond48Unverified("P90_REAL_SECOND48_UNRESOLVED_POSITION")
        close_at = _utc(row.get("closed_at"), "CLOSED_AT")
        event_at = _utc(row.get("attribution_closed_at"), "ATTRIBUTION_AT")
        mechanism = _exact(row.get("exit_mechanism"))
        if (
            event_at != close_at or event_at > now
            or row.get("close_link_status") != "EXACT"
            or row.get("attribution_link_status") != "EXACT"
            or mechanism not in _ALLOWED_EXITS
            or not _exact(row.get("attribution_id"))
            or not _exact(row.get("exit_order_id"))
            or not _exact(row.get("exit_owner"))
        ):
            raise P90RealSecond48Unverified("P90_REAL_SECOND48_EXACT_EXIT_UNPROVEN")
        # Only a classified actual initial hard-stop is the Strategy hardstop
        # counter. Protected stops and ordinary closes are NOT hardstops.
        events.append((
            event_at, _exact(row["attribution_id"]),
            "HARD_STOP" if mechanism == "INITIAL_HARD_STOP" else mechanism,
        ))
    embargo = StrategyHardStopEmbargo(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_config_fingerprint=strategy_config_fingerprint,
        policy=policy,
    )
    for at, event_id, reason in sorted(events):
        embargo.record_exit(exit_event_id=event_id, exit_reason=reason, at=at)
    return embargo.allows_new_entry(now), embargo.snapshot()
