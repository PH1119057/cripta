from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .adapter import MayakSnapshotAdapter
from .engine import StrategyDispatcher
from .presentation import build_dispatcher_view
from .profile_io import load_profile_directory
from .registry import StrategyMarketProfileRegistry
from .serialization import assessment_to_dict, snapshot_to_dict
from .storage import JsonlAssessmentStore


class PassiveDispatcherService:
    """File-fed passive service. It has no trading dependency or mutation path."""

    VERSION = "strategy-dispatcher-service-0.2.0"

    def __init__(
        self,
        *,
        mayak_status_path: Path,
        profile_dir: Path,
        state_root: Path,
    ) -> None:
        self.mayak_status_path = mayak_status_path
        self.profile_dir = profile_dir
        self.adapter = MayakSnapshotAdapter()
        self.dispatcher = StrategyDispatcher()
        self.store = JsonlAssessmentStore(state_root)
        self._last_snapshot_id: str | None = self.store.latest_snapshot_id()

    def run_once(self) -> dict[str, Any]:
        raw = json.loads(self.mayak_status_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("Mayak status must be a JSON object")
        snapshot = self.adapter.adapt(raw)
        profiles = load_profile_directory(self.profile_dir, require_enabled=True)
        registry = StrategyMarketProfileRegistry()
        for profile in profiles:
            registry.register(profile)
        snapshot_dict = snapshot_to_dict(snapshot)
        created_at = datetime.now(UTC).isoformat()
        assessments = []
        for profile in registry.profiles():
            item = assessment_to_dict(self.dispatcher.evaluate(snapshot, profile))
            item.update(
                {
                    "mayak_snapshot_id": snapshot.provenance.get(
                        "mayak_snapshot_id", snapshot.snapshot_id
                    ),
                    "created_at": created_at,
                    "data_quality": snapshot.data_quality.value,
                    "coverage": {
                        feature_id: feature.get("coverage")
                        for feature_id, feature in snapshot_dict["features"].items()
                    },
                    "trading_effect": (
                        "FULL_LIVE_V1"
                        if profile.version == "1.0.0-owner-live"
                        else "NONE"
                    ),
                }
            )
            assessments.append(item)
        live_profile_ids = {profile.profile_id for profile in registry.profiles()}
        assessments.extend(
            self._research_required_assessments(
                snapshot_dict, excluded_profile_ids=live_profile_ids
            )
        )
        envelope = {
            "service_version": self.VERSION,
            "snapshot": snapshot_dict,
            "assessments": assessments,
            "profile_count": len(assessments),
            "trading_effect": "NONE",
        }
        envelope["view_ru"] = build_dispatcher_view(envelope)
        if snapshot.snapshot_id != self._last_snapshot_id:
            self.store.append_batch(envelope)
            self._last_snapshot_id = snapshot.snapshot_id
        return envelope

    @staticmethod
    def _research_required_assessments(
        snapshot: dict[str, Any], *, excluded_profile_ids: set[str] | None = None
    ) -> list[dict[str, Any]]:
        created_at = datetime.now(UTC).isoformat()
        profiles = (
            "M3_V1_LONG_ENTRY",
            "M3_V1_SHORT_ENTRY",
            "M3_V1_LONG_HOLD",
            "M3_V1_SHORT_HOLD",
        )
        excluded = excluded_profile_ids or set()
        return [
            {
                "assessment_id": hashlib.sha256(
                    f"{snapshot['snapshot_id']}:{profile}:shadow-v1".encode()
                ).hexdigest(),
                "snapshot_id": snapshot["snapshot_id"],
                "mayak_snapshot_id": snapshot.get("provenance", {}).get(
                    "mayak_snapshot_id", snapshot["snapshot_id"]
                ),
                "observed_at": snapshot["observed_at"],
                "created_at": created_at,
                "dispatcher_version": "strategy-dispatcher-raw-context-1.0",
                "profile_id": profile,
                "profile_version": "shadow-v1",
                "suitability": None,
                "confidence": min(
                    (
                        float(item["feature_confidence"])
                        for item in snapshot["features"].values()
                        if item["status"] == "VALID"
                    ),
                    default=0.0,
                ),
                "status": "RESEARCH_REQUIRED",
                "data_quality": snapshot["data_quality"],
                "coverage": {
                    feature_id: item.get("coverage")
                    for feature_id, item in snapshot["features"].items()
                },
                "features": snapshot["features"],
                "trading_effect": "NONE",
                "reason_ru": "Порог профиля не исследован; сохранён сырой контекст среды",
            }
            for profile in profiles
            if profile not in excluded
        ]

    def serve_forever(self, *, poll_seconds: float = 1.0) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        while True:
            try:
                self.run_once()
            except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
                self.store.publish_status(
                    {
                        "service_version": self.VERSION,
                        "status": "WAITING",
                        "reason": str(exc),
                        "assessments": [],
                        "trading_effect": "NONE",
                    }
                )
            time.sleep(poll_seconds)
