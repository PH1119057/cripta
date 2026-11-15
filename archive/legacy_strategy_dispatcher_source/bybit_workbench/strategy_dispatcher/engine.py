from __future__ import annotations

import hashlib
from datetime import UTC

from .contracts import (
    DispatcherAssessment,
    DispatcherDataQuality,
    DispatcherMarketSnapshot,
    FeatureKind,
    FeatureValue,
    MatchOperator,
    ProfileRule,
    RequirementMode,
    RuleAssessment,
    StrategyMarketProfile,
    SuitabilityStatus,
)
from .vocabulary import V1_FEATURE_INDEX


class StrategyDispatcher:
    """Pure market-environment matcher. It has no trading or execution dependency."""

    VERSION = "strategy-dispatcher-0.2.0"

    def evaluate(
        self,
        snapshot: DispatcherMarketSnapshot,
        profile: StrategyMarketProfile,
    ) -> DispatcherAssessment:
        if snapshot.observed_at.utcoffset() != UTC.utcoffset(snapshot.observed_at):
            raise ValueError("snapshot time must be UTC")
        self._validate_profile(profile)

        rule_rows: list[RuleAssessment] = []
        matched_required: list[str] = []
        missing_required: list[str] = []
        matched_preferred: list[str] = []
        conflicting: list[str] = []
        rejected_triggered: list[str] = []
        missing_factors: list[str] = []
        soft_total = 0.0
        soft_score = 0.0
        confidence_total = 0.0
        confidence_weight = 0.0

        for rule in profile.rules:
            feature = snapshot.features.get(rule.feature_id)
            available = bool(feature and feature.usable)
            matched = self._matches(feature, rule) if available else None
            actual = feature.value if available and feature is not None else None
            row_confidence = feature.confidence if available and feature is not None else 0.0
            reason = rule.reason_ru or rule.feature_id
            rule_rows.append(
                RuleAssessment(
                    feature_id=rule.feature_id,
                    mode=rule.mode,
                    matched=matched,
                    available=available,
                    actual=actual,
                    expected=rule.expected,
                    confidence=row_confidence,
                    reason_ru=reason,
                )
            )
            confidence_weight += rule.weight
            confidence_total += row_confidence * rule.weight

            if not available:
                missing_factors.append(rule.feature_id)
                if rule.mode is RequirementMode.REQUIRED:
                    missing_required.append(rule.feature_id)
                continue

            assert matched is not None
            if rule.mode is RequirementMode.REQUIRED:
                if matched:
                    matched_required.append(rule.feature_id)
                else:
                    conflicting.append(rule.feature_id)
            elif rule.mode is RequirementMode.REJECTED:
                if matched:
                    rejected_triggered.append(rule.feature_id)
            elif rule.mode is RequirementMode.PREFERRED:
                soft_total += rule.weight
                if matched:
                    soft_score += rule.weight
                    matched_preferred.append(rule.feature_id)
            elif rule.mode is RequirementMode.TOLERATED:
                soft_total += rule.weight
                if matched:
                    soft_score += rule.weight * 0.5

        confidence = confidence_total / confidence_weight if confidence_weight else 0.0
        confidence *= self._quality_multiplier(snapshot.data_quality)
        confidence = max(0.0, min(confidence, 1.0))

        if snapshot.data_quality is DispatcherDataQuality.INSUFFICIENT or missing_required:
            status = SuitabilityStatus.INSUFFICIENT_DATA
            suitability: float | None = None
        elif conflicting or rejected_triggered:
            status = SuitabilityStatus.INCOMPATIBLE
            suitability = 0.0
        else:
            suitability = soft_score / soft_total if soft_total else 1.0
            status = self._status_for_score(suitability)

        return DispatcherAssessment(
            assessment_id=self._assessment_id(snapshot, profile),
            observed_at=snapshot.observed_at,
            snapshot_id=snapshot.snapshot_id,
            dispatcher_version=self.VERSION,
            profile_id=profile.profile_id,
            profile_version=profile.version,
            suitability=suitability,
            confidence=confidence,
            status=status,
            matched_required=tuple(matched_required),
            missing_required=tuple(missing_required),
            matched_preferred=tuple(matched_preferred),
            conflicting=tuple(conflicting),
            rejected_triggered=tuple(rejected_triggered),
            missing_factors=tuple(missing_factors),
            rules=tuple(rule_rows),
        )

    def _validate_profile(self, profile: StrategyMarketProfile) -> None:
        unknown = sorted({rule.feature_id for rule in profile.rules} - set(V1_FEATURE_INDEX))
        if unknown:
            raise ValueError(f"profile references unknown dispatcher features: {', '.join(unknown)}")
        for rule in profile.rules:
            definition = V1_FEATURE_INDEX[rule.feature_id]
            if definition.kind in {FeatureKind.CATEGORICAL, FeatureKind.STATUS}:
                if rule.operator not in {MatchOperator.ONE_OF, MatchOperator.NOT_ONE_OF}:
                    raise ValueError(f"categorical feature {rule.feature_id} requires ONE_OF/NOT_ONE_OF")
                invalid = [value for value in rule.expected if value not in definition.allowed_values]
                if invalid:
                    raise ValueError(
                        f"profile has invalid values for {rule.feature_id}: {invalid!r}"
                    )
            elif (
                definition.kind is FeatureKind.NUMERIC
                and rule.operator in {MatchOperator.ONE_OF, MatchOperator.NOT_ONE_OF}
                and not all(
                    isinstance(value, (int, float)) and not isinstance(value, bool)
                    for value in rule.expected
                )
            ):
                raise ValueError(f"numeric feature {rule.feature_id} requires numeric values")

    @staticmethod
    def _matches(feature: FeatureValue | None, rule: ProfileRule) -> bool:
        if feature is None or not feature.usable:
            raise ValueError("cannot match unavailable feature")
        actual = feature.value
        assert actual is not None
        if rule.operator is MatchOperator.ONE_OF:
            return actual in rule.expected
        if rule.operator is MatchOperator.NOT_ONE_OF:
            return actual not in rule.expected
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            raise TypeError(f"numeric operator requires numeric feature: {rule.feature_id}")
        numeric = float(actual)
        if rule.operator is MatchOperator.AT_LEAST:
            return numeric >= float(rule.expected[0])
        if rule.operator is MatchOperator.AT_MOST:
            return numeric <= float(rule.expected[0])
        if rule.operator is MatchOperator.BETWEEN:
            return float(rule.expected[0]) <= numeric <= float(rule.expected[1])
        raise TypeError(f"unsupported match operator: {rule.operator}")

    @staticmethod
    def _quality_multiplier(quality: DispatcherDataQuality) -> float:
        return {
            DispatcherDataQuality.HIGH: 1.0,
            DispatcherDataQuality.MEDIUM: 0.8,
            DispatcherDataQuality.LOW: 0.5,
            DispatcherDataQuality.INSUFFICIENT: 0.0,
        }[quality]

    @staticmethod
    def _status_for_score(score: float) -> SuitabilityStatus:
        if score >= 0.85:
            return SuitabilityStatus.EXCELLENT_MATCH
        if score >= 0.65:
            return SuitabilityStatus.GOOD_MATCH
        if score >= 0.40:
            return SuitabilityStatus.PARTIAL_MATCH
        return SuitabilityStatus.POOR_MATCH

    def _assessment_id(
        self,
        snapshot: DispatcherMarketSnapshot,
        profile: StrategyMarketProfile,
    ) -> str:
        payload = (
            f"{self.VERSION}|{snapshot.snapshot_id}|{snapshot.observed_at.isoformat()}|"
            f"{profile.profile_id}|{profile.version}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
