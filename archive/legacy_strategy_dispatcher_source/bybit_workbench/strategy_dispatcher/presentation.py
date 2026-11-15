from __future__ import annotations

from typing import Any

from .vocabulary import V1_FEATURE_INDEX

_STATUS_RU = {
    "EXCELLENT_MATCH": "отлично подходит",
    "GOOD_MATCH": "подходит",
    "PARTIAL_MATCH": "подходит частично",
    "POOR_MATCH": "скорее не подходит",
    "INCOMPATIBLE": "не подходит",
    "INSUFFICIENT_DATA": "недостаточно данных",
}

_QUALITY_RU = {
    "HIGH": "высокое",
    "MEDIUM": "среднее",
    "LOW": "низкое",
    "INSUFFICIENT": "недостаточно данных",
}


def build_dispatcher_view(envelope: dict[str, Any]) -> dict[str, Any]:
    raw_snapshot = envelope.get("snapshot")
    snapshot: dict[str, Any] = raw_snapshot if isinstance(raw_snapshot, dict) else {}
    raw_assessments = envelope.get("assessments")
    assessments: list[Any] = raw_assessments if isinstance(raw_assessments, list) else []
    rows = []
    for item in assessments:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "профиль": item.get("profile_id"),
                "версия_профиля": item.get("profile_version"),
                "статус": _STATUS_RU.get(str(item.get("status")), "неизвестно"),
                "пригодность_процентов": _percent(item.get("suitability")),
                "уверенность_процентов": _percent(item.get("confidence")),
                "подтверждает": _labels(item.get("matched_preferred")),
                "противоречит": _labels(item.get("conflicting")),
                "запрещающий_фактор": _labels(item.get("rejected_triggered")),
                "не_хватает": _labels(item.get("missing_factors")),
            }
        )
    return {
        "наблюдалось_в": snapshot.get("observed_at"),
        "снимок_маяка": snapshot.get("snapshot_id"),
        "качество_данных": _QUALITY_RU.get(str(snapshot.get("data_quality")), "неизвестно"),
        "профили": rows,
        "торговое_влияние": "отсутствует",
    }


def _labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for feature_id in value:
        definition = V1_FEATURE_INDEX.get(str(feature_id))
        result.append(definition.label_ru if definition else str(feature_id))
    return result


def _percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value) * 100.0, 1)
