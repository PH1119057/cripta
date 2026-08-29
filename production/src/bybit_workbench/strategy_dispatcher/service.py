from __future__ import annotations

import json
import time
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
        assessments = [
            assessment_to_dict(self.dispatcher.evaluate(snapshot, profile))
            for profile in registry.profiles()
        ]
        envelope = {
            "service_version": self.VERSION,
            "snapshot": snapshot_to_dict(snapshot),
            "assessments": assessments,
            "profile_count": len(assessments),
            "trading_effect": "NONE",
        }
        envelope["view_ru"] = build_dispatcher_view(envelope)
        if snapshot.snapshot_id != self._last_snapshot_id:
            self.store.append_batch(envelope)
            self._last_snapshot_id = snapshot.snapshot_id
        return envelope

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
