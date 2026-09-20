from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

GLOBAL_LIVE_ARM_CHECKS = (
    "CANON_CURRENT",
    "REMOTE_COMMIT_VERIFIED",
    "SOURCE_LIVE_IDENTITY",
    "TESTS",
    "LIVE_EQUIVALENCE",
    "EXCHANGE_ACCOUNT_IDENTITY",
    "PHYSICAL_SLOT_CLAIM_CONTRACT",
    "CAPITAL_RESERVATION_CONTRACT",
    "LIFECYCLE_SUPERVISOR_BEHAVIOR",
    "CRITICAL_FAULT_DELIVERY",
    "RECONCILIATION_PATH",
    "ROLLBACK_OR_KILL_PATH",
)

STRATEGY_LIVE_ARM_CHECKS = (
    "EXACT_STRATEGY_ACTIVATION",
    "ENTRY_PLAN_EXECUTABLE",
    "EXIT_PLAN_EXECUTABLE",
    "INITIAL_PROTECTION_EXECUTABLE",
    "TERMINAL_LOSS_CONTAINMENT_PATH",
    "EMERGENCY_POLICY_SUPPORTED",
)

STRATEGY_SYMBOL_LIVE_ARM_CHECKS = (
    "POSITION_MODE_FRESH",
    "POSITION_IDX_EXPECTED",
    "MICRO_LIVE_LIMITS",
    "MAINNET_GATE_EXPLICIT_OWNER_APPROVAL",
)

REQUIRED_LIVE_ARM_CHECKS = (
    *GLOBAL_LIVE_ARM_CHECKS,
    *STRATEGY_LIVE_ARM_CHECKS,
    *STRATEGY_SYMBOL_LIVE_ARM_CHECKS,
)

_LIVE_ARM_STATUSES = {"PASS", "FAIL", "UNKNOWN", "STALE", "NOT_CHECKED_HERE"}


class CursorLike(Protocol):
    def fetchall(self) -> Sequence[Mapping[str, object] | Sequence[object]]: ...
    def fetchone(self) -> Mapping[str, object] | Sequence[object] | None: ...


class ConnectionLike(Protocol):
    def execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> CursorLike: ...


@dataclass(frozen=True, slots=True)
class LiveArmContext:
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    strategy_activation_id: str
    symbol: str
    release_commit: str

    def __post_init__(self) -> None:
        for field in (
            "strategy_id",
            "strategy_version",
            "strategy_config_fingerprint",
            "strategy_activation_id",
            "symbol",
            "release_commit",
        ):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"live arm context requires {field}")
        commit = self.release_commit.strip().lower()
        if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
            raise ValueError("release_commit must be a full Git SHA")


@dataclass(frozen=True, slots=True)
class LiveArmCheck:
    code: str
    scope_type: str
    scope_key: str
    status: str
    checked_at: datetime | None
    valid_until: datetime | None
    release_commit: str | None
    source: str | None
    detail: str

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


@dataclass(frozen=True, slots=True)
class LiveArmDecision:
    checks: tuple[LiveArmCheck, ...]

    @property
    def ready(self) -> bool:
        return bool(self.checks) and all(item.passed for item in self.checks)

    @property
    def failed_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.checks if not item.passed)


def strategy_scope_key(context: LiveArmContext) -> str:
    return "|".join(
        (
            context.strategy_id,
            context.strategy_version,
            context.strategy_config_fingerprint,
            context.strategy_activation_id,
        )
    )


def strategy_symbol_scope_key(context: LiveArmContext) -> str:
    return strategy_scope_key(context) + "|" + context.symbol.upper()


def scope_for_check(code: str, context: LiveArmContext) -> tuple[str, str]:
    if code in GLOBAL_LIVE_ARM_CHECKS:
        return ("GLOBAL", "GLOBAL")
    if code in STRATEGY_LIVE_ARM_CHECKS:
        return ("STRATEGY", strategy_scope_key(context))
    if code in STRATEGY_SYMBOL_LIVE_ARM_CHECKS:
        return ("STRATEGY_SYMBOL", strategy_symbol_scope_key(context))
    raise ValueError(f"unknown LIVE-arm check code: {code}")


def _value(
    row: Mapping[str, object] | Sequence[object],
    key: str,
    index: int,
) -> object:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]




def active_live_arm_session(
    connection: ConnectionLike,
    *,
    context: LiveArmContext,
) -> str | None:
    row = connection.execute(
        """SELECT live_arm_session_id
             FROM control.live_arm_sessions
            WHERE strategy_id=%s
              AND strategy_version=%s
              AND strategy_config_fingerprint=%s
              AND strategy_activation_id=%s
              AND symbol=%s
              AND release_commit=%s
              AND state='ACTIVE'
            ORDER BY activated_at DESC,created_at DESC
            LIMIT 1""",
        (
            context.strategy_id,
            context.strategy_version,
            context.strategy_config_fingerprint,
            context.strategy_activation_id,
            context.symbol.upper(),
            context.release_commit,
        ),
    ).fetchone()
    if row is None:
        return None
    return str(_value(row, "live_arm_session_id", 0))


def evaluate_live_arm(
    connection: ConnectionLike,
    *,
    context: LiveArmContext,
    now: datetime,
    require_owner_approval: bool = True,
) -> LiveArmDecision:
    current = now.astimezone(UTC)
    required_codes = tuple(
        code
        for code in REQUIRED_LIVE_ARM_CHECKS
        if require_owner_approval or code != "MAINNET_GATE_EXPLICIT_OWNER_APPROVAL"
    )
    rows = connection.execute(
        """SELECT DISTINCT ON (check_code,scope_type,scope_key)
                  check_code,scope_type,scope_key,status,checked_at,valid_until,
                  release_commit,source,evidence
             FROM control.live_arm_evidence
            WHERE check_code = ANY(%s)
            ORDER BY check_code,scope_type,scope_key,checked_at DESC,created_at DESC""",
        (list(required_codes),),
    ).fetchall()
    by_identity: dict[tuple[str, str, str], Mapping[str, object] | Sequence[object]] = {}
    for row in rows:
        key = (
            str(_value(row, "check_code", 0)),
            str(_value(row, "scope_type", 1)),
            str(_value(row, "scope_key", 2)),
        )
        by_identity[key] = row

    checks: list[LiveArmCheck] = []
    for code in required_codes:
        scope_type, scope_key = scope_for_check(code, context)
        row = by_identity.get((code, scope_type, scope_key))
        if row is None:
            checks.append(
                LiveArmCheck(
                    code=code,
                    scope_type=scope_type,
                    scope_key=scope_key,
                    status="NOT_CHECKED_HERE",
                    checked_at=None,
                    valid_until=None,
                    release_commit=None,
                    source=None,
                    detail="required durable LIVE-arm evidence is missing",
                )
            )
            continue

        status = str(_value(row, "status", 3))
        checked_at_raw = _value(row, "checked_at", 4)
        valid_until_raw = _value(row, "valid_until", 5)
        release_commit_raw = _value(row, "release_commit", 6)
        source_raw = _value(row, "source", 7)
        checked_at = checked_at_raw if isinstance(checked_at_raw, datetime) else None
        valid_until = valid_until_raw if isinstance(valid_until_raw, datetime) else None
        release_commit = None if release_commit_raw is None else str(release_commit_raw)
        source = None if source_raw is None else str(source_raw)
        detail = "durable evidence status=" + status

        if status not in _LIVE_ARM_STATUSES:
            status = "UNKNOWN"
            detail = "unknown durable evidence status"
        elif checked_at is None or checked_at.tzinfo is None:
            status = "UNKNOWN"
            detail = "evidence timestamp is invalid"
        elif checked_at.astimezone(UTC) > current:
            status = "STALE"
            detail = "evidence timestamp is from the future"
        elif valid_until is not None and (
            valid_until.tzinfo is None or valid_until.astimezone(UTC) < current
        ):
            status = "STALE"
            detail = "evidence validity expired"
        elif release_commit != context.release_commit:
            status = "STALE"
            detail = (
                "evidence release_commit differs from loaded release: "
                f"{release_commit!r}"
            )

        checks.append(
            LiveArmCheck(
                code=code,
                scope_type=scope_type,
                scope_key=scope_key,
                status=status,
                checked_at=checked_at,
                valid_until=valid_until,
                release_commit=release_commit,
                source=source,
                detail=detail,
            )
        )

    return LiveArmDecision(tuple(checks))
