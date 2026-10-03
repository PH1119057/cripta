from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def shared_market_context_record(
    snapshot_id: int,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    if snapshot_id <= 0:
        raise ValueError("snapshot_id must be positive")
    handoff = _mapping(snapshot.get("dispatcher_handoff"))
    canonical = json.dumps(
        handoff,
        ensure_ascii=False,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "market_context_id": str(handoff["market_context_id"]),
        "mayak_snapshot_id": snapshot_id,
        "observed_at": handoff["observed_at"],
        "mayak_version": str(snapshot["engine_version"]),
        "schema_version": str(handoff["market_context_schema_version"]),
        "config_fingerprint": str(snapshot["config_fingerprint"]),
        "data_quality": str(handoff["data_quality"]),
        "payload": dict(handoff),
        "provenance": dict(_mapping(handoff["provenance"])),
        "content_hash": content_hash,
    }


def coin_market_context_records(
    snapshot_id: int,
    snapshot: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    if snapshot_id <= 0:
        raise ValueError("snapshot_id must be positive")
    contexts = _mapping(snapshot.get("coin_market_contexts"))
    rows: list[dict[str, Any]] = []
    for key in sorted(contexts):
        context = _mapping(contexts[key])
        rows.append(
            {
                "coin_context_id": str(context["coin_context_id"]),
                "mayak_snapshot_id": snapshot_id,
                "observed_at": context["observed_at"],
                "symbol": str(context["symbol"]),
                "schema_version": str(context["schema_version"]),
                "engine_version": str(context["engine_version"]),
                "feature_version": str(context["feature_version"]),
                "config_fingerprint": str(context["config_fingerprint"]),
                "data_quality": str(context["data_quality"]),
                "payload": dict(_mapping(context["payload"])),
                "provenance": dict(_mapping(context["provenance"])),
                "content_hash": str(context["content_hash"]),
            }
        )
    return tuple(rows)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    raise TypeError("expected mapping")
