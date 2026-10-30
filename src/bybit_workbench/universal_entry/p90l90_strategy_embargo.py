"""Strategy-version-scoped configurable P90L90 hard-stop entry embargo.

No Exchange order, StrategyActivation, DB mutation or account-global state.
The immutable config belongs to the StrategyCard lifecycle policy, not the
TradingAccount. The caller persists the exact checkpoint in its own audited
transaction and supplies durably identified CLOSED Exit events.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

_SCHEMA = "STRATEGY_HARDSTOP_EMBARGO_V1"
_HARDSTOP = "HARD_STOP"
_STRATEGY_SCOPE = "STRATEGY_VERSION"


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _identity(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label} must be exact nonempty identity")
    return value


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{label} requires timezone")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class StrategyHardStopEmbargoPolicy:
    enabled: bool
    hardstop_count: int
    rolling_hours: int
    new_entry_pause_hours: int
    close_existing_positions: bool
    domain: str

    @classmethod
    def parse(cls, policy: Mapping[str, object]) -> StrategyHardStopEmbargoPolicy:
        expected = {
            "enabled", "hardstop_count", "rolling_hours",
            "new_entry_pause_hours", "close_existing_positions", "domain",
        }
        if set(policy) != expected:
            raise ValueError("P90L90 SECOND48 policy keys incomplete or unsupported")
        if type(policy["enabled"]) is not bool or (
            type(policy["close_existing_positions"]) is not bool
        ):
            raise ValueError("P90L90 SECOND48 policy toggles invalid")
        if policy["close_existing_positions"] is not False:
            raise ValueError("P90L90 SECOND48 cannot flatten existing positions")
        if policy["domain"] != _STRATEGY_SCOPE:
            # Old passive cards contain UNRESOLVED_OWNER_CONTRACT and are
            # deliberately *not* inferred into the new immutable contract.
            raise ValueError("P90L90 SECOND48 unresolved/wrong Strategy scope")
        return cls(
            enabled=policy["enabled"],
            hardstop_count=_positive_int(policy["hardstop_count"], "hardstop_count"),
            rolling_hours=_positive_int(policy["rolling_hours"], "rolling_hours"),
            new_entry_pause_hours=_positive_int(
                policy["new_entry_pause_hours"], "new_entry_pause_hours"
            ),
            close_existing_positions=False,
            domain=_STRATEGY_SCOPE,
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "hardstop_count": self.hardstop_count,
            "rolling_hours": self.rolling_hours,
            "new_entry_pause_hours": self.new_entry_pause_hours,
            "close_existing_positions": False,
            "domain": _STRATEGY_SCOPE,
        }


@dataclass(frozen=True, slots=True)
class HardStopEvidence:
    event_id: str
    at: datetime

    def __post_init__(self) -> None:
        _identity(self.event_id, "hard-stop event ID")
        _utc(self.at, "hard-stop event timestamp")


@dataclass(slots=True)
class StrategyHardStopEmbargo:
    """Exact identity / one durable ledger per immutable Strategy version."""

    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    policy: StrategyHardStopEmbargoPolicy
    hard_stops: deque[HardStopEvidence] = field(default_factory=deque)
    pause_until: datetime | None = None
    last_event_at: datetime | None = None

    def __post_init__(self) -> None:
        for label in (
            "strategy_id", "strategy_version", "strategy_config_fingerprint"
        ):
            _identity(getattr(self, label), label)
        if self.policy.domain != _STRATEGY_SCOPE:
            raise ValueError("P90L90 SECOND48 only accepts Strategy scope")

    def allows_new_entry(self, at: datetime) -> bool:
        when = _utc(at, "Entry evaluation")
        return not (
            self.policy.enabled and self.pause_until is not None
            and when < self.pause_until
        )

    def record_exit(
        self, *, exit_event_id: str, exit_reason: str, at: datetime
    ) -> bool:
        """Record one realized Exit event; returns whether ledger changed.

        No closure of open positions; protected trailing != HARD_STOP.
        Event source must be persistent and unique across retries.
        """
        _identity(exit_event_id, "Exit event ID")
        when = _utc(at, "Exit event")
        if self.last_event_at is not None and when < self.last_event_at:
            raise ValueError("SECOND48 noncausal Exit event order")
        if any(row.event_id == exit_event_id for row in self.hard_stops):
            return False
        self.last_event_at = when
        if exit_reason != _HARDSTOP or not self.policy.enabled:
            return False
        self.hard_stops.append(HardStopEvidence(exit_event_id, when))
        window = timedelta(hours=self.policy.rolling_hours)
        while self.hard_stops and when - self.hard_stops[0].at > window:
            self.hard_stops.popleft()
        if len(self.hard_stops) >= self.policy.hardstop_count:
            deadline = when + timedelta(hours=self.policy.new_entry_pause_hours)
            if self.pause_until is None or deadline > self.pause_until:
                self.pause_until = deadline
        return True

    def snapshot(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_config_fingerprint": self.strategy_config_fingerprint,
            "policy": self.policy.snapshot(),
            "hard_stops": [
                {"event_id": e.event_id, "at": e.at.astimezone(UTC).isoformat()}
                for e in self.hard_stops
            ],
            "pause_until": (
                None if self.pause_until is None
                else self.pause_until.astimezone(UTC).isoformat()
            ),
            "last_event_at": (
                None if self.last_event_at is None
                else self.last_event_at.astimezone(UTC).isoformat()
            ),
        }

    @classmethod
    def restore(
        cls,
        raw: Mapping[str, object],
        *,
        strategy_id: str,
        strategy_version: str,
        strategy_config_fingerprint: str,
        policy: StrategyHardStopEmbargoPolicy,
    ) -> StrategyHardStopEmbargo:
        if set(raw) != {
            "schema", "strategy_id", "strategy_version",
            "strategy_config_fingerprint", "policy", "hard_stops",
            "pause_until", "last_event_at",
        } or raw["schema"] != _SCHEMA:
            raise ValueError("SECOND48 checkpoint schema mismatch")
        for name, expected in (
            ("strategy_id", strategy_id),
            ("strategy_version", strategy_version),
            ("strategy_config_fingerprint", strategy_config_fingerprint),
        ):
            if raw[name] != _identity(expected, name):
                raise ValueError(f"SECOND48 checkpoint {name} identity mismatch")
        payload = raw["policy"]
        if not isinstance(payload, Mapping) or (
            StrategyHardStopEmbargoPolicy.parse(payload) != policy
        ):
            raise ValueError("SECOND48 checkpoint policy version mismatch")
        def timestamp(value: object, name: str) -> datetime | None:
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError(f"SECOND48 checkpoint {name} must be ISO UTC")
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"SECOND48 checkpoint {name} ISO invalid") from exc
            if parsed.tzinfo is None or parsed.utcoffset() != timedelta():
                raise ValueError(f"SECOND48 checkpoint {name} must be UTC")
            return parsed.astimezone(UTC)

        rows = raw["hard_stops"]
        if not isinstance(rows, list):
            raise ValueError("SECOND48 checkpoint events must be list")
        events: deque[HardStopEvidence] = deque()
        seen: set[str] = set()
        previous: datetime | None = None
        for item in rows:
            if not isinstance(item, Mapping) or set(item) != {"event_id", "at"}:
                raise ValueError("SECOND48 checkpoint event malformed")
            at = timestamp(item["at"], "hard-stop.at")
            assert at is not None
            event_id = _identity(item["event_id"], "hard-stop.event_id")
            if event_id in seen or (previous is not None and at < previous):
                raise ValueError("SECOND48 checkpoint duplicate/out-of-order event")
            seen.add(event_id)
            previous = at
            events.append(HardStopEvidence(event_id, at))
        last = timestamp(raw["last_event_at"], "last_event_at")
        pause = timestamp(raw["pause_until"], "pause_until")
        if (last is None and (events or pause is not None)) or (
            events and last is not None and events[-1].at > last
        ):
            raise ValueError("SECOND48 checkpoint temporal invariant broken")
        return cls(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_config_fingerprint=strategy_config_fingerprint,
            policy=policy,
            hard_stops=events,
            pause_until=pause,
            last_event_at=last,
        )
