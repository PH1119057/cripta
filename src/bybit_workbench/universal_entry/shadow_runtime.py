from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from pathlib import Path

from .contracts import EntryPlan, FrozenPolicy, MarketFactEnvelope
from .parity import ParityPoint, V1DeterministicParityRunner, compare_parity_points


class ShadowComparability(StrEnum):
    WARMUP = "WARMUP"
    NOT_COMPARABLE = "NOT_COMPARABLE"
    PARITY_COMPARABLE = "PARITY_COMPARABLE"


def derive_unknown_prestart_horizon_seconds(plan: EntryPlan) -> int:
    """Derive the maximum unknown pre-start Entry-state influence from EntryPlan data."""

    candidates = [Decimal("0")]
    cooldown = plan.touch_policy.candidate_cooldown
    if cooldown.enabled:
        candidates.append(cooldown.as_seconds())
    outcome = plan.post_signal_outcome_policy
    if outcome.enabled:
        influence = outcome.horizon_seconds()
        if outcome.optional_embargo.enabled:
            influence += outcome.optional_embargo.as_seconds()
        candidates.append(influence)
    value = max(candidates)
    return int(value.to_integral_value(rounding=ROUND_CEILING))


class CausalFactFanout[TLegacy, TUniversal]:
    """Fan one immutable normalized fact to both comparison consumers."""

    def __init__(
        self,
        legacy_consumer: Callable[[MarketFactEnvelope], TLegacy],
        universal_consumer: Callable[[MarketFactEnvelope], TUniversal],
    ) -> None:
        self._legacy_consumer = legacy_consumer
        self._universal_consumer = universal_consumer

    def dispatch(self, fact: MarketFactEnvelope) -> tuple[TLegacy, TUniversal]:
        legacy = self._legacy_consumer(fact)
        universal = self._universal_consumer(fact)
        return legacy, universal


class ShadowComparabilityGate:
    def __init__(self, started_at: datetime, *, required_warmup_seconds: int) -> None:
        if required_warmup_seconds < 0:
            raise ValueError("required warmup seconds cannot be negative")
        self.started_at = started_at.astimezone(UTC)
        self.required_warmup_seconds = required_warmup_seconds
        self._seed_complete = False
        self._live_sensor_complete = False
        self._gap_reason: str | None = None

    @property
    def reason(self) -> str:
        if self._gap_reason is not None:
            return self._gap_reason
        if not self._seed_complete:
            return "causal history seed is incomplete"
        if not self._live_sensor_complete:
            return "required live sensor completeness is incomplete"
        return "waiting for unknown pre-start Entry lifecycle influence to expire"

    def mark_seed_complete(self) -> None:
        self._seed_complete = True

    def mark_live_sensor_complete(self) -> None:
        self._live_sensor_complete = True

    def mark_uncovered_gap(self, reason: str) -> None:
        rendered = reason.strip()
        if not rendered:
            raise ValueError("uncovered gap requires an explicit reason")
        self._gap_reason = rendered

    def state_at(self, now: datetime) -> ShadowComparability:
        observed = now.astimezone(UTC)
        if self._gap_reason is not None:
            return ShadowComparability.NOT_COMPARABLE
        if not self._seed_complete or not self._live_sensor_complete:
            return ShadowComparability.WARMUP
        elapsed = (observed - self.started_at).total_seconds()
        if elapsed < self.required_warmup_seconds:
            return ShadowComparability.WARMUP
        return ShadowComparability.PARITY_COMPARABLE


@dataclass(frozen=True, slots=True)
class ShadowRunIdentity:
    parity_run_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    entry_plan_fingerprint: str
    calibration_sha256: str
    calibration_size: int
    baseline_source_commit: str
    universal_source_commit: str
    fact_source_id: str
    service_instance_id: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class ShadowParityObservation:
    parity_run_id: str
    causal_key: str
    category: str
    observed_at: datetime
    source_refs: tuple[str, ...]
    strategy_config_fingerprint: str
    entry_plan_fingerprint: str
    legacy_payload: FrozenPolicy
    universal_payload: FrozenPolicy
    equivalent: bool
    difference: FrozenPolicy


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    rendered = getattr(value, "value", None)
    if isinstance(rendered, (str, int, float, bool)) or rendered is None:
        return value if rendered is None else rendered
    return str(value)


def _points_payload(points: tuple[ParityPoint, ...]) -> FrozenPolicy:
    return FrozenPolicy.from_mapping({point.category: _json_value(point.value) for point in points})


class OnlineParityComparator:
    """Live semantic comparator over U4 deterministic per-symbol parity runners."""

    def __init__(
        self,
        identity: ShadowRunIdentity,
        runners: Mapping[str, V1DeterministicParityRunner],
    ) -> None:
        self.identity = identity
        self._runners = dict(runners)
        self._point_offsets = {symbol: 0 for symbol in runners}
        self.comparable_events = 0
        self.matched_events = 0
        self.mismatches_by_category: dict[str, int] = {}
        self.first_mismatch: ShadowParityObservation | None = None

    def process(self, fact: MarketFactEnvelope) -> ShadowParityObservation:
        runner = self._runners.get(fact.symbol)
        if runner is None:
            raise KeyError(f"shadow parity has no runner for symbol: {fact.symbol}")
        result = runner.step(fact)
        offset = self._point_offsets[fact.symbol]
        legacy = result.legacy_points[offset:]
        universal = result.universal_points[offset:]
        self._point_offsets[fact.symbol] = len(result.legacy_points)
        report = compare_parity_points(legacy, universal)
        self.comparable_events += 1
        if report.passed:
            self.matched_events += 1
            category = "SEMANTIC_CHECKPOINT"
            difference = FrozenPolicy.from_mapping({})
        else:
            mismatch = report.first_mismatch
            if mismatch is None:
                raise RuntimeError("failed parity comparison has no first mismatch")
            category = mismatch.category
            self.mismatches_by_category[category] = self.mismatches_by_category.get(category, 0) + 1
            difference = FrozenPolicy.from_mapping(
                {
                    "category": mismatch.category,
                    "causal_key": mismatch.causal_key,
                    "index": mismatch.index,
                    "legacy_value": _json_value(mismatch.legacy_value),
                    "universal_value": _json_value(mismatch.universal_value),
                }
            )
        observation = ShadowParityObservation(
            parity_run_id=self.identity.parity_run_id,
            causal_key=fact.fact_id,
            category=category,
            observed_at=fact.observed_at.astimezone(UTC),
            source_refs=fact.source_refs,
            strategy_config_fingerprint=self.identity.strategy_config_fingerprint,
            entry_plan_fingerprint=self.identity.entry_plan_fingerprint,
            legacy_payload=_points_payload(legacy),
            universal_payload=_points_payload(universal),
            equivalent=report.passed,
            difference=difference,
        )
        if not observation.equivalent and self.first_mismatch is None:
            self.first_mismatch = observation
        return observation

    def summary(self) -> dict[str, object]:
        return {
            "comparable_events": self.comparable_events,
            "matched_events": self.matched_events,
            "mismatch_events": self.comparable_events - self.matched_events,
            "mismatches_by_category": dict(sorted(self.mismatches_by_category.items())),
            "first_mismatch": (
                None
                if self.first_mismatch is None
                else {
                    "causal_key": self.first_mismatch.causal_key,
                    "category": self.first_mismatch.category,
                    "observed_at": self.first_mismatch.observed_at.isoformat(),
                    "source_refs": list(self.first_mismatch.source_refs),
                }
            ),
        }


class DurableFactJournal:
    """Local recovery journal; PostgreSQL parity evidence remains canonical audit truth."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, fact: MarketFactEnvelope) -> None:
        payload = {
            "fact_id": fact.fact_id,
            "event_kind": fact.event_kind,
            "symbol": fact.symbol,
            "observed_at": fact.observed_at.astimezone(UTC).isoformat(),
            "event_at": fact.event_at.astimezone(UTC).isoformat(),
            "received_at": fact.received_at.astimezone(UTC).isoformat(),
            "source_refs": list(fact.source_refs),
            "attributes": fact.attributes.to_dict(),
            "direction": None if fact.direction is None else fact.direction.value,
        }
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        try:
            os.write(descriptor, (line + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def replay(self) -> Iterator[MarketFactEnvelope]:
        if not self.path.exists():
            return
        from .contracts import TradeDirection

        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = json.loads(line)
                direction_raw = raw.get("direction")
                yield MarketFactEnvelope(
                    fact_id=str(raw["fact_id"]),
                    event_kind=str(raw["event_kind"]),
                    symbol=str(raw["symbol"]),
                    observed_at=datetime.fromisoformat(str(raw["observed_at"])).astimezone(UTC),
                    event_at=datetime.fromisoformat(str(raw["event_at"])).astimezone(UTC),
                    received_at=datetime.fromisoformat(str(raw["received_at"])).astimezone(UTC),
                    source_refs=tuple(str(item) for item in raw["source_refs"]),
                    attributes=FrozenPolicy.from_mapping(raw.get("attributes") or {}),
                    direction=None if direction_raw is None else TradeDirection(str(direction_raw)),
                )
