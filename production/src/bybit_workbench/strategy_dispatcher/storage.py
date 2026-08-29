from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JsonlAssessmentStore:
    """Append-only passive storage plus atomic latest-status publication."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.root / "assessments.jsonl"
        self.status_path = self.root / "status.json"

    def append_batch(self, envelope: dict[str, Any]) -> None:
        line = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.publish_status(envelope)


    def latest_snapshot_id(self) -> str | None:
        try:
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
        if not isinstance(snapshot, dict):
            return None
        value = snapshot.get("snapshot_id")
        return str(value) if value else None

    def publish_status(self, envelope: dict[str, Any]) -> None:
        payload = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(prefix="status.", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.status_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
