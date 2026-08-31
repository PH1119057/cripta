from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Any, cast

from .contracts import DispatcherAssessment, DispatcherMarketSnapshot


def snapshot_to_dict(snapshot: DispatcherMarketSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "observed_at": snapshot.observed_at.isoformat(),
        "mayak_version": snapshot.mayak_version,
        "architecture_version": snapshot.architecture_version,
        "data_quality": snapshot.data_quality.value,
        "features": {
            feature_id: {
                "value": feature.value,
                "status": feature.status.value,
                "confidence": feature.confidence,
                "feature_confidence": feature.confidence,
                "transport_confidence": feature.transport_confidence,
                "coverage": (
                    {"valid": feature.coverage_valid, "total": feature.coverage_total}
                    if feature.coverage_valid is not None
                    else None
                ),
                "observed_at": feature.observed_at.isoformat() if feature.observed_at else None,
            }
            for feature_id, feature in snapshot.features.items()
        },
        "provenance": dict(snapshot.provenance),
    }


def assessment_to_dict(assessment: DispatcherAssessment) -> dict[str, Any]:
    return cast(dict[str, Any], _jsonable(asdict(assessment)))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
