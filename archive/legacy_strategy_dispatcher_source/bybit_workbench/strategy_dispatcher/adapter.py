from __future__ import annotations

import hashlib
import statistics
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .contracts import (
    DispatcherDataQuality,
    DispatcherMarketSnapshot,
    FeatureKind,
    FeatureStatus,
    FeatureValue,
)
from .vocabulary import V1_FEATURE_INDEX


class MayakSnapshotAdapter:
    """Read-only bridge from a Mayak payload to the dispatcher contract.

    Preferred contract: Mayak publishes ``dispatcher_features`` keyed by the stable
    dispatcher vocabulary. Those values are copied without strategy context.

    The legacy bridge exists only so the dispatcher can be exercised against the
    frozen 2026-08-29 snapshot. Legacy mode deliberately caps data quality at LOW;
    it must not silently turn old Mayak heuristics into production-grade features.
    """

    VERSION = "mayak-dispatcher-adapter-0.2.0"

    def adapt(self, payload: Mapping[str, Any]) -> DispatcherMarketSnapshot:
        payload = self._normalise_input(payload)
        observed_at = _parse_utc(payload.get("observed_at"))
        if observed_at is None:
            raise ValueError("Mayak payload has no valid UTC observed_at")
        engine_version = str(payload.get("engine_version") or payload.get("mayak_version") or "unknown")
        architecture_version = str(payload.get("architecture_version") or "legacy-unversioned")
        canonical = payload.get("dispatcher_features")
        if isinstance(canonical, Mapping):
            features = self._canonical_features(canonical, observed_at)
            quality = self._canonical_quality(payload, features)
            mode = "canonical"
        else:
            features = self._legacy_features(payload, observed_at)
            quality = self._legacy_quality(payload)
            mode = "legacy_bridge"
        features["data.quality"] = FeatureValue(
            value=quality.value,
            status=FeatureStatus.VALID,
            confidence=1.0,
            observed_at=observed_at,
        )
        snapshot_id = str(payload.get("snapshot_id") or self._stable_snapshot_id(payload, observed_at))
        provenance = {
            "adapter_version": self.VERSION,
            "adapter_mode": mode,
            "mayak_engine_version": engine_version,
        }
        source_snapshot_id = payload.get("snapshot_id")
        if source_snapshot_id is not None:
            provenance["mayak_snapshot_id"] = str(source_snapshot_id)
        market_context_id = payload.get("market_context_id")
        if market_context_id is not None:
            provenance["market_context_id"] = str(market_context_id)
        context_schema = payload.get("market_context_schema_version")
        if context_schema is not None:
            provenance["market_context_schema_version"] = str(context_schema)
        return DispatcherMarketSnapshot(
            snapshot_id=snapshot_id,
            observed_at=observed_at,
            mayak_version=engine_version,
            architecture_version=architecture_version,
            data_quality=quality,
            features=features,
            provenance=provenance,
        )

    @classmethod
    def _normalise_input(cls, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        unwrapped = cls._unwrap_persisted_row(payload)
        handoff = unwrapped.get("dispatcher_handoff")
        if isinstance(handoff, Mapping):
            return handoff
        return unwrapped

    @staticmethod
    def _unwrap_persisted_row(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        nested = payload.get("payload")
        if not isinstance(nested, Mapping):
            return payload
        merged: dict[str, Any] = dict(nested)
        for key in (
            "observed_at",
            "engine_version",
            "architecture_version",
            "data_quality",
            "confidence",
            "state",
            "snapshot_kind",
        ):
            if key in payload:
                merged[key] = payload[key]
        if "snapshot_id" in payload:
            merged["snapshot_id"] = payload["snapshot_id"]
        elif "id" in payload:
            merged["snapshot_id"] = f"db-{payload['id']}"
        return merged

    def _canonical_features(
        self,
        raw: Mapping[str, Any],
        fallback_time: datetime,
    ) -> dict[str, FeatureValue]:
        features: dict[str, FeatureValue] = {}
        for feature_id, item in raw.items():
            if feature_id not in V1_FEATURE_INDEX or feature_id == "data.quality":
                continue
            if isinstance(item, Mapping):
                status = _feature_status(item.get("status"))
                value = item.get("value") if status is FeatureStatus.VALID else None
                confidence = _clamp01(item.get("confidence"), default=1.0)
                transport_confidence = _clamp01(
                    item.get("transport_confidence"), default=confidence
                )
                coverage = item.get("coverage")
                coverage_valid = None
                coverage_total = None
                if isinstance(coverage, Mapping):
                    coverage_valid = int(coverage.get("valid", 0))
                    coverage_total = int(coverage.get("total", 0))
                parsed_observed_at = _parse_utc(item.get("observed_at"))
                observed_at = (
                    parsed_observed_at or fallback_time
                    if status is FeatureStatus.VALID
                    else parsed_observed_at
                )
            else:
                status = FeatureStatus.VALID
                value = item
                confidence = 1.0
                transport_confidence = 1.0
                coverage_valid = None
                coverage_total = None
                observed_at = fallback_time
            if status is FeatureStatus.VALID and value is None:
                status = FeatureStatus.NO_DATA
            definition = V1_FEATURE_INDEX[feature_id]
            if (
                status is FeatureStatus.VALID
                and definition.kind in {FeatureKind.CATEGORICAL, FeatureKind.STATUS}
                and (not isinstance(value, str) or value not in definition.allowed_values)
            ):
                raise ValueError(f"invalid canonical value for {feature_id}: {value!r}")
            if (
                status is FeatureStatus.VALID
                and definition.kind is FeatureKind.NUMERIC
                and (isinstance(value, bool) or not isinstance(value, (int, float)))
            ):
                raise ValueError(f"numeric canonical feature required for {feature_id}")
            features[feature_id] = FeatureValue(
                value=value,
                status=status,
                confidence=confidence,
                observed_at=observed_at,
                transport_confidence=transport_confidence,
                coverage_valid=coverage_valid,
                coverage_total=coverage_total,
            )
        return features

    @staticmethod
    def _canonical_quality(
        payload: Mapping[str, Any], features: Mapping[str, FeatureValue]
    ) -> DispatcherDataQuality:
        explicit = str(payload.get("data_quality") or "").upper()
        if explicit in DispatcherDataQuality._value2member_map_:
            return DispatcherDataQuality(explicit)
        valid = sum(feature.usable for feature in features.values())
        if not features or valid == 0:
            return DispatcherDataQuality.INSUFFICIENT
        ratio = valid / len(features)
        if ratio >= 0.85:
            return DispatcherDataQuality.HIGH
        if ratio >= 0.60:
            return DispatcherDataQuality.MEDIUM
        if ratio >= 0.30:
            return DispatcherDataQuality.LOW
        return DispatcherDataQuality.INSUFFICIENT

    def _legacy_features(
        self,
        payload: Mapping[str, Any],
        observed_at: datetime,
    ) -> dict[str, FeatureValue]:
        """Expose only simple, directly observable legacy dimensions.

        This is intentionally conservative. Missing liquidation/event/normalised
        features remain missing instead of being guessed from unrelated values.
        """

        result: dict[str, FeatureValue] = {}
        confidence = _clamp01(payload.get("confidence"), default=0.0)
        feature_confidence = min(confidence, 0.49)

        breadth = _mapping(payload.get("price_breadth"))
        median = _as_float(breadth.get("median_return_pct"))
        if median is not None:
            result["market.direction"] = _feature(
                _direction(median), feature_confidence, observed_at
            )
            result["market.direction_strength"] = _feature(
                _direction_strength(abs(median)), feature_confidence, observed_at
            )
        up_share = _as_float(breadth.get("up_share"))
        down_share = _as_float(breadth.get("down_share"))
        if up_share is not None and down_share is not None:
            result["market.breadth"] = _feature(
                _breadth(up_share, down_share), feature_confidence, observed_at
            )

        sync = _mapping(payload.get("direction_synchronization"))
        agreement = _as_float(sync.get("agreement"))
        if agreement is not None:
            result["market.synchronization"] = _feature(
                _synchronization(agreement), feature_confidence, observed_at
            )

        money = _mapping(payload.get("money_breadth"))
        spot_sales = _as_float(money.get("spot_sales_share"))
        derivative_sales = _as_float(money.get("derivatives_sales_share"))
        if spot_sales is not None:
            result["money.spot_pressure"] = _feature(
                _sales_pressure(spot_sales), feature_confidence, observed_at
            )
        if derivative_sales is not None:
            result["money.derivatives_pressure"] = _feature(
                _sales_pressure(derivative_sales), feature_confidence, observed_at
            )
        if spot_sales is not None and derivative_sales is not None:
            result["money.spot_derivatives_alignment"] = _feature(
                _pressure_alignment(spot_sales, derivative_sales),
                feature_confidence,
                observed_at,
            )

        coins = _mapping(payload.get("coins"))
        oi_changes: list[float] = []
        for coin in coins.values():
            ticker = _mapping(_mapping(coin).get("ticker"))
            value = _as_float(ticker.get("open_interest_change_5m_pct"))
            if value is not None:
                oi_changes.append(value)
        median_oi = statistics.median(oi_changes) if oi_changes else None
        if median_oi is not None:
            result["positioning.oi_regime"] = _feature(
                _oi_regime(median_oi), feature_confidence, observed_at
            )
            if median is not None:
                result["positioning.price_oi_state"] = _feature(
                    _price_oi_state(median, median_oi), feature_confidence, observed_at
                )

        withdrawal = _as_float(money.get("buyer_liquidity_withdrawal_share"))
        if withdrawal is not None:
            result["liquidity.trend"] = _feature(
                _liquidity_trend(withdrawal), feature_confidence, observed_at
            )

        for symbol, feature_id in (("btc", "btc.state"), ("eth", "eth.state")):
            anchor = _mapping(payload.get(symbol))
            linear = _mapping(anchor.get("linear"))
            anchor_return = _as_float(linear.get("return_pct"))
            if anchor_return is not None:
                result[feature_id] = _feature(
                    _direction(anchor_return), feature_confidence, observed_at
                )

        return result

    @staticmethod
    def _legacy_quality(payload: Mapping[str, Any]) -> DispatcherDataQuality:
        confidence = _clamp01(payload.get("confidence"), default=0.0)
        if confidence <= 0.0:
            return DispatcherDataQuality.INSUFFICIENT
        # Legacy Mayak did not have the final source-health contract. Do not allow
        # this bridge to masquerade as HIGH/MEDIUM quality.
        return DispatcherDataQuality.LOW

    @staticmethod
    def _stable_snapshot_id(payload: Mapping[str, Any], observed_at: datetime) -> str:
        seed = "|".join(
            (
                str(payload.get("engine_version") or "unknown"),
                observed_at.isoformat(),
                str(payload.get("state") or ""),
            )
        )
        return "legacy-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _feature(value: str, confidence: float, observed_at: datetime) -> FeatureValue:
    return FeatureValue(
        value=value,
        status=FeatureStatus.VALID,
        confidence=confidence,
        observed_at=observed_at,
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _clamp01(value: Any, *, default: float) -> float:
    number = _as_float(value)
    if number is None:
        return default
    return max(0.0, min(number, 1.0))


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _feature_status(value: Any) -> FeatureStatus:
    normalized = str(value or "VALID").upper()
    aliases = {
        "FRESH": "VALID",
        "MISSING": "NO_DATA",
        "INSUFFICIENT": "NO_DATA",
        "UNAVAILABLE": "NO_DATA",
    }
    normalized = aliases.get(normalized, normalized)
    try:
        return FeatureStatus(normalized)
    except ValueError:
        return FeatureStatus.ERROR


def _direction(value_pct: float) -> str:
    if value_pct >= 0.25:
        return "STRONG_UP"
    if value_pct >= 0.05:
        return "UP"
    if value_pct <= -0.25:
        return "STRONG_DOWN"
    if value_pct <= -0.05:
        return "DOWN"
    return "NEUTRAL"


def _direction_strength(abs_pct: float) -> str:
    if abs_pct >= 0.40:
        return "EXTREME"
    if abs_pct >= 0.25:
        return "STRONG"
    if abs_pct >= 0.10:
        return "MODERATE"
    if abs_pct >= 0.03:
        return "WEAK"
    return "NONE"


def _breadth(up: float, down: float) -> str:
    if up >= 0.75:
        return "STRONGLY_BULLISH"
    if up >= 0.60:
        return "BULLISH"
    if down >= 0.75:
        return "STRONGLY_BEARISH"
    if down >= 0.60:
        return "BEARISH"
    return "BALANCED"


def _synchronization(agreement: float) -> str:
    if agreement >= 0.80:
        return "EXTREME"
    if agreement >= 0.60:
        return "HIGH"
    if agreement >= 0.35:
        return "NORMAL"
    return "LOW"


def _sales_pressure(sales_share: float) -> str:
    if sales_share >= 0.75:
        return "STRONG_SELL"
    if sales_share >= 0.58:
        return "SELL"
    if sales_share <= 0.25:
        return "STRONG_BUY"
    if sales_share <= 0.42:
        return "BUY"
    return "BALANCED"


def _pressure_alignment(spot_sales: float, derivative_sales: float) -> str:
    spot_side = -1 if spot_sales >= 0.58 else 1 if spot_sales <= 0.42 else 0
    derivative_side = -1 if derivative_sales >= 0.58 else 1 if derivative_sales <= 0.42 else 0
    gap = abs(spot_sales - derivative_sales)
    if spot_side != 0 and spot_side == derivative_side:
        return "STRONGLY_ALIGNED" if gap <= 0.10 else "ALIGNED"
    if spot_side != 0 and derivative_side != 0 and spot_side != derivative_side:
        return "STRONGLY_DIVERGING" if gap >= 0.35 else "DIVERGING"
    return "MIXED"


def _oi_regime(change_pct: float) -> str:
    if change_pct >= 0.50:
        return "STRONG_EXPANSION"
    if change_pct >= 0.05:
        return "EXPANDING"
    if change_pct <= -0.50:
        return "STRONG_CONTRACTION"
    if change_pct <= -0.05:
        return "CONTRACTING"
    return "STABLE"


def _price_oi_state(price_pct: float, oi_pct: float) -> str:
    price_side = 1 if price_pct >= 0.05 else -1 if price_pct <= -0.05 else 0
    oi_side = 1 if oi_pct >= 0.05 else -1 if oi_pct <= -0.05 else 0
    if price_side == 1 and oi_side == 1:
        return "PRICE_UP_OI_UP"
    if price_side == 1 and oi_side == -1:
        return "PRICE_UP_OI_DOWN"
    if price_side == -1 and oi_side == 1:
        return "PRICE_DOWN_OI_UP"
    if price_side == -1 and oi_side == -1:
        return "PRICE_DOWN_OI_DOWN"
    return "MIXED"


def _liquidity_trend(withdrawal_share: float) -> str:
    if withdrawal_share >= 0.75:
        return "WITHDRAWING_FAST"
    if withdrawal_share >= 0.55:
        return "WITHDRAWING"
    if withdrawal_share <= 0.15:
        return "REPLENISHING"
    return "STABLE"
