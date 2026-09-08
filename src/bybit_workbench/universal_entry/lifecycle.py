from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from .contracts import CooldownScope, EntryPlan, MarketFactEnvelope, TradeDirection
from .references import resolve_causal_timestamp, resolve_causal_value


@dataclass(slots=True)
class _TrackedOutcome:
    signal_id: str
    direction: TradeDirection
    reference_value: Decimal
    detected_at: datetime
    expires_at: datetime
    account_ref: str | None


@dataclass(frozen=True, slots=True)
class EntryLifecycleSnapshot:
    tracked_outcomes: int
    last_resolution: str | None
    last_resolution_at: datetime | None
    entry_embargo_until: datetime | None


class PostSignalLifecycleBook:
    """Plan-local Entry lifecycle state derived from causal post-signal observations.

    This class never owns a position and has no Exit/stop/take-profit API. Its only
    mutation is future-Entry lifecycle state declared by EntryPlan.
    """

    def __init__(self) -> None:
        self._tracked: dict[tuple[str, str], list[_TrackedOutcome]] = {}
        self._symbol_embargo: dict[tuple[str, str], datetime] = {}
        self._strategy_embargo: dict[str, datetime] = {}
        self._account_embargo: dict[tuple[str, str], datetime] = {}
        self._last_resolution: dict[tuple[str, str], tuple[str, datetime]] = {}

    @staticmethod
    def _decimal(value: object, label: str) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError(f"invalid {label}") from None
        if not result.is_finite():
            raise ValueError(f"invalid {label}")
        return result

    def register(
        self,
        plan: EntryPlan,
        *,
        signal_id: str,
        direction: TradeDirection,
        signal_fact: MarketFactEnvelope,
        account_ref: str | None,
    ) -> None:
        policy = plan.post_signal_outcome_policy
        if not policy.enabled:
            return
        assert policy.reference_value_path is not None
        raw_reference = resolve_causal_value(policy.reference_value_path, fact=signal_fact)
        reference = self._decimal(raw_reference, "post-signal reference value")
        if reference <= 0:
            raise ValueError("post-signal reference value must be positive")
        expires = signal_fact.observed_at + timedelta(seconds=float(policy.horizon_seconds()))
        key = (plan.entry_plan_fingerprint, signal_fact.symbol)
        self._tracked.setdefault(key, []).append(
            _TrackedOutcome(
                signal_id=signal_id,
                direction=direction,
                reference_value=reference,
                detected_at=signal_fact.observed_at,
                expires_at=expires,
                account_ref=account_ref,
            )
        )

    def observe(
        self,
        plan: EntryPlan,
        fact: MarketFactEnvelope,
        *,
        account_ref: str | None,
    ) -> None:
        policy = plan.post_signal_outcome_policy
        if (
            not policy.enabled
            or fact.event_kind.upper() != str(policy.observation_event_kind).upper()
        ):
            return
        key = (plan.entry_plan_fingerprint, fact.symbol)
        tracked = self._tracked.get(key)
        if not tracked:
            return
        assert policy.observation_value_path is not None
        value = self._decimal(
            resolve_causal_value(policy.observation_value_path, fact=fact),
            "post-signal observation value",
        )
        if value <= 0:
            raise ValueError("post-signal observation value must be positive")
        remaining: list[_TrackedOutcome] = []
        for item in tracked:
            if fact.observed_at > item.expires_at:
                continue
            raw_move = (value / item.reference_value - Decimal("1")) * Decimal("100")
            move = raw_move if item.direction is TradeDirection.LONG else -raw_move
            resolution: str | None = None
            assert policy.favorable_threshold is not None
            assert policy.adverse_threshold is not None
            if move >= policy.favorable_threshold:
                resolution = "FAVORABLE"
            elif move <= policy.adverse_threshold:
                resolution = "ADVERSE"
            if resolution is None:
                remaining.append(item)
                continue
            self._last_resolution[key] = (resolution, fact.observed_at)
            embargo = policy.optional_embargo
            if embargo.enabled and embargo.on_resolution == resolution:
                anchor = resolve_causal_timestamp(embargo.anchor or "", fact=fact)
                until = anchor + timedelta(seconds=float(embargo.as_seconds()))
                self._set_embargo(
                    plan,
                    symbol=fact.symbol,
                    account_ref=item.account_ref or account_ref,
                    scope=embargo.scope,
                    until=until,
                )
        if remaining:
            self._tracked[key] = remaining
        else:
            self._tracked.pop(key, None)

    def _set_embargo(
        self,
        plan: EntryPlan,
        *,
        symbol: str,
        account_ref: str | None,
        scope: CooldownScope | None,
        until: datetime,
    ) -> None:
        if scope is CooldownScope.PER_SYMBOL:
            self._symbol_embargo[(plan.entry_plan_fingerprint, symbol)] = until
            return
        if scope is CooldownScope.PER_STRATEGY:
            self._strategy_embargo[plan.strategy_activation_id] = until
            return
        if scope is CooldownScope.PER_ACCOUNT:
            if not account_ref:
                raise ValueError("PER_ACCOUNT post-signal embargo requires account_ref")
            self._account_embargo[(plan.strategy_activation_id, account_ref)] = until
            return
        raise ValueError(f"unsupported post-signal embargo scope: {scope}")

    def entry_allowed(
        self,
        plan: EntryPlan,
        *,
        symbol: str,
        observed_at: datetime,
        account_ref: str | None,
    ) -> tuple[bool, datetime | None]:
        if not plan.post_signal_outcome_policy.enabled:
            return True, None
        candidates = [self._symbol_embargo.get((plan.entry_plan_fingerprint, symbol))]
        candidates.append(self._strategy_embargo.get(plan.strategy_activation_id))
        if account_ref is not None:
            candidates.append(self._account_embargo.get((plan.strategy_activation_id, account_ref)))
        active = [value for value in candidates if value is not None and observed_at < value]
        return (not active, max(active) if active else None)

    def snapshot(
        self,
        plan: EntryPlan,
        symbol: str,
        *,
        account_ref: str | None = None,
    ) -> EntryLifecycleSnapshot:
        key = (plan.entry_plan_fingerprint, symbol)
        last = self._last_resolution.get(key)
        embargoes = [
            self._symbol_embargo.get(key),
            self._strategy_embargo.get(plan.strategy_activation_id),
        ]
        if account_ref is not None:
            embargoes.append(self._account_embargo.get((plan.strategy_activation_id, account_ref)))
        known = [value for value in embargoes if value is not None]
        return EntryLifecycleSnapshot(
            tracked_outcomes=len(self._tracked.get(key, ())),
            last_resolution=None if last is None else last[0],
            last_resolution_at=None if last is None else last[1],
            entry_embargo_until=max(known) if known else None,
        )
