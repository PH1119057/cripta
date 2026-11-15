from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class DispatcherContextProvider(Protocol):
    def current(self, profile_id: str, version: str) -> dict[str, Any] | None: ...


class FileDispatcherContextProvider:
    """Read-only D6 boundary. Existing trading code does not import or use it yet.

    The returned envelope preserves snapshot time and quality so a future consumer
    can apply its own fail-closed freshness contract instead of seeing a detached
    suitability score.
    """

    def __init__(self, status_path: Path) -> None:
        self.status_path = status_path

    def current(self, profile_id: str, version: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        assessments = payload.get("assessments")
        snapshot = payload.get("snapshot")
        if not isinstance(assessments, list) or not isinstance(snapshot, dict):
            return None
        for item in assessments:
            if not isinstance(item, dict):
                continue
            if item.get("profile_id") == profile_id and item.get("profile_version") == version:
                return {
                    "service_version": payload.get("service_version"),
                    "snapshot": {
                        "snapshot_id": snapshot.get("snapshot_id"),
                        "observed_at": snapshot.get("observed_at"),
                        "data_quality": snapshot.get("data_quality"),
                        "mayak_version": snapshot.get("mayak_version"),
                        "architecture_version": snapshot.get("architecture_version"),
                    },
                    "assessment": item,
                    "trading_effect": payload.get("trading_effect", "NONE"),
                }
        return None
