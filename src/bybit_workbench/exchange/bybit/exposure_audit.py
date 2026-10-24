"""Pure, read-only account-wide exposure versus local lineage comparison.

This is diagnostic evidence, NOT Strategy attribution or trading authorization.
It must not create faults, transition slot claims, or authorize Exchange POST.
Authenticated Exchange UID -> CRIPTA TradingAccount binding is NOT proven here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite


class ExposureInventoryUnavailable(ValueError):
    """Incomplete, malformed or stale authenticated account inventory."""


@dataclass(frozen=True, slots=True)
class ObservedExposure:
    trading_account_id: str
    symbol: str
    position_idx: int
    side: str
    size: Decimal
    active_strategy_claim_ids: tuple[str, ...]
    matching_open_owner_ids: tuple[str, ...]
    open_owner_ids: tuple[str, ...] = ()
    origin: str = "UNKNOWN"
    account_identity_binding: str = "NOT_VERIFIED_HERE"


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExposureInventoryUnavailable(f"{field} missing")
    return value.strip()


def _index(value: object) -> int:
    if type(value) is not int or value not in (0, 1, 2):
        raise ExposureInventoryUnavailable("positionIdx unsupported")
    return value


def _qty(value: object) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ExposureInventoryUnavailable("size invalid") from exc
    if not number.is_finite() or number < 0:
        raise ExposureInventoryUnavailable("size invalid")
    return number


def _validated_snapshot(
    snapshot: Mapping[str, object] | None, now: datetime, max_age_seconds: float
) -> list[dict[str, object]]:
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("source") != "BYBIT_AUTHENTICATED_ACCOUNT_GET"
        or snapshot.get("complete") is not True
        or snapshot.get("account_wide_pagination_complete") is not True
        or snapshot.get("account_ref") != "BYBIT:UNIFIED"
        or not str(snapshot.get("account_user_id") or "").strip()
    ):
        raise ExposureInventoryUnavailable("independent complete Exchange account evidence missing")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ExposureInventoryUnavailable("scan time is not timezone-aware")
    try:
        observed = datetime.fromisoformat(str(snapshot["observed_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ExposureInventoryUnavailable("Exchange read timestamp unavailable") from exc
    if observed.tzinfo is None or not isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ExposureInventoryUnavailable("Exchange timestamp/age contract invalid")
    age = (now.astimezone(UTC) - observed.astimezone(UTC)).total_seconds()
    if age < -1 or age > max_age_seconds:
        raise ExposureInventoryUnavailable("Exchange account inventory stale or future-dated")
    positions = snapshot.get("account_wide_positions")
    if not isinstance(positions, list) or any(not isinstance(p, dict) for p in positions):
        raise ExposureInventoryUnavailable("account-wide position list malformed")
    seen: set[tuple[str, int]] = set()
    for p in positions:
        symbol = _required_text(p.get("symbol"), "Exchange symbol")
        idx = _index(p.get("positionIdx"))
        key = (symbol, idx)
        if key in seen:
            raise ExposureInventoryUnavailable("duplicate physical position key")
        seen.add(key)
        size = _qty(p.get("size"))
        if size > 0 and p.get("side") not in ("Buy", "Sell"):
            raise ExposureInventoryUnavailable("non-flat position direction invalid")
    return positions


def audit_account_exposure(
    *,
    account_snapshot: Mapping[str, object] | None,
    trading_account_id: str,
    active_slot_claims: Sequence[Mapping[str, object]],
    open_owner_rows: Sequence[Mapping[str, object]],
    now: datetime,
    max_age_seconds: float = 15,
) -> tuple[ObservedExposure, ...]:
    """Record Exchange exposure and local records without guessing provenance.

    Caller must independently fetch matching TradingAccount-scoped DB rows.
    This function rejects mixed-account/legacy rows; it cannot prove the UID
    binding, Exchange position cycle, snapshot atomicity or any policy action.
    """
    account = _required_text(trading_account_id, "TradingAccount")
    positions = _validated_snapshot(account_snapshot, now, max_age_seconds)
    claims: dict[tuple[str, int], list[Mapping[str, object]]] = {}
    for claim in active_slot_claims:
        if not isinstance(claim, Mapping):
            raise ExposureInventoryUnavailable("claim row invalid")
        if claim.get("trading_account_id") != account or claim.get("identity_status") != "PROVEN":
            raise ExposureInventoryUnavailable("cross-account/unproven claim row")
        state = claim.get("claim_state")
        if state not in {"CLAIMED", "BOUND", "RECONCILIATION_REQUIRED"}:
            raise ExposureInventoryUnavailable("inactive claim in active input")
        symbol = _required_text(claim.get("symbol"), "claim symbol")
        idx = _index(claim.get("position_idx"))
        canonical_key = f"BYBIT:{account}:LINEAR:USDT:{symbol}:{idx}"
        if claim.get("exchange_position_key") != canonical_key:
            raise ExposureInventoryUnavailable("claim physical key mismatch")
        _required_text(claim.get("exchange_position_slot_claim_id"), "claim ID")
        claims.setdefault((symbol, idx), []).append(claim)
    owners: dict[tuple[str, int], list[Mapping[str, object]]] = {}
    for owner in open_owner_rows:
        if not isinstance(owner, Mapping):
            raise ExposureInventoryUnavailable("owner row invalid")
        if (
            owner.get("trading_account_id") != account
            or owner.get("identity_status") != "PROVEN"
            or owner.get("state") != "OPEN"
        ):
            raise ExposureInventoryUnavailable("cross-account/unproven/non-open owner row")
        symbol = _required_text(owner.get("symbol"), "owner symbol")
        idx = _index(owner.get("position_idx"))
        canonical_key = f"BYBIT:{account}:LINEAR:USDT:{symbol}:{idx}"
        if owner.get("exchange_position_key") != canonical_key:
            raise ExposureInventoryUnavailable("owner physical key mismatch")
        _required_text(owner.get("position_id"), "owner position ID")
        owners.setdefault((symbol, idx), []).append(owner)
    observations: list[ObservedExposure] = []
    for p in positions:
        size = _qty(p["size"])
        if size == 0:
            continue
        symbol = str(p["symbol"]).strip()
        idx = _index(p["positionIdx"])
        side = str(p["side"])
        active = claims.get((symbol, idx), [])
        valid_owner_ids = sorted(
            {
                str(o["position_id"])
                for o in owners.get((symbol, idx), [])
                if o.get("side") == side
                and any(
                    c.get("strategy_position_id") == o.get("position_id")
                    and c.get("claim_state") == "BOUND"
                    for c in active
                )
            }
        )
        observations.append(
            ObservedExposure(
                trading_account_id=account,
                symbol=symbol,
                position_idx=idx,
                side=side,
                size=size,
                active_strategy_claim_ids=tuple(
                    sorted(str(c["exchange_position_slot_claim_id"]) for c in active)
                ),
                matching_open_owner_ids=tuple(valid_owner_ids),
                # Preserve ALL OPEN owners, not just claim-matched owners. A valid
                # bound pair does not excuse an extra competing position owner.
                open_owner_ids=tuple(
                    sorted(str(o["position_id"]) for o in owners.get((symbol, idx), []))
                ),
            )
        )
    return tuple(sorted(observations, key=lambda p: (p.symbol, p.position_idx)))
