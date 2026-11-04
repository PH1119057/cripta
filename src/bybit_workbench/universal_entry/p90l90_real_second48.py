"""P90L90 REAL v3 SECOND48 reconstructed from exact, durable actual close facts.

This reader is READ ONLY. Its caller must keep one database transaction from
this proof through account Entry admission (including account-scoped lock).
It is not an owner-arm, Exchange credential proof, or release readiness gate.
Only actual INITIAL_HARD_STOP events participate in SECOND48. Ordinary,
protected, manual and UNKNOWN closes are repair/history data, never admission
blockers. No missing attribution is inferred to be a hard-stop. Exact evidence
is still required for events explicitly identified as INITIAL_HARD_STOP.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION
from .p90l90_strategy_embargo import (
    StrategyHardStopEmbargo,
    StrategyHardStopEmbargoPolicy,
)


class _Rows(Protocol):
    def fetchall(self) -> Sequence[object]: ...


_ROW_FIELDS = (
    "position_id",
    "state",
    "closed_at",
    "close_link_status",
    "identity_status",
    "trading_account_id",
    "account_kind",
    "attribution_id",
    "attribution_closed_at",
    "attribution_link_status",
    "exit_owner",
    "exit_mechanism",
    "exit_order_id",
    "position_side",
    "actual_avg_entry",
    "intended_initial_hard_stop",
    "actual_exit_avg_fill",
)


def _row_mapping(row: object) -> Mapping[str, object]:
    if isinstance(row, Mapping):
        return row
    if (
        isinstance(row, Sequence)
        and not isinstance(row, (str, bytes, bytearray))
        and len(row) == len(_ROW_FIELDS)
    ):
        return dict(zip(_ROW_FIELDS, row, strict=True))
    raise P90RealSecond48Unverified("P90_REAL_SECOND48_ROW_NOT_PROVEN")


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
    """Replay actual hard-stop events, independently per immutable Strategy.

    Earlier hard-stops can pair with a recent event and impose a current pause.
    Non-hard-stop and UNKNOWN closed rows cannot veto Entry (TC 4.13.5).
    Current OPEN/unresolved exposure still belongs to current safety checks.
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
                  a.exit_owner,a.exit_mechanism,a.exit_order_id,
                  p.side AS position_side,a.actual_avg_entry,
                  a.intended_initial_hard_stop,a.actual_exit_avg_fill
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
    for raw_row in rows:
        row = _row_mapping(raw_row)
        position_id = _exact(row.get("position_id"))
        state = _exact(row.get("state"))
        # Select eligibility BEFORE demanding historical identity/attribution.
        # A closed trade has no admission authority outside actual SECOND48.
        if state == "CLOSED" and row.get("exit_mechanism") != "INITIAL_HARD_STOP":
            continue
        if state == "CLOSED":
            # A trigger expires after pause_hours; its earliest possible paired
            # event is another rolling_hours earlier. Older history has no
            # authority over current Entry, even if its attribution is broken.
            closed_at = _utc(row.get("closed_at"), "CLOSED_AT")
            horizon = timedelta(hours=policy.rolling_hours + policy.new_entry_pause_hours)
            if closed_at <= now - horizon:
                continue
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
        attribution_id = _exact(row.get("attribution_id"))
        exit_order_id = _exact(row.get("exit_order_id"))
        exact_link = (
            event_at == close_at
            and event_at <= now
            and row.get("close_link_status") == "EXACT"
            and row.get("attribution_link_status") == "EXACT"
            and bool(attribution_id)
            and bool(exit_order_id)
        )
        if not exact_link:
            raise P90RealSecond48Unverified("P90_REAL_SECOND48_EXACT_EXIT_UNPROVEN")

        if not _exact(row.get("exit_owner")):
            raise P90RealSecond48Unverified("P90_REAL_SECOND48_EXACT_EXIT_UNPROVEN")
        events.append((event_at, attribution_id, "HARD_STOP"))
    embargo = StrategyHardStopEmbargo(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_config_fingerprint=strategy_config_fingerprint,
        policy=policy,
    )
    for at, event_id, reason in sorted(events):
        embargo.record_exit(exit_event_id=event_id, exit_reason=reason, at=at)
    return embargo.allows_new_entry(now), embargo.snapshot()
