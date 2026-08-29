from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .adapter import MayakSnapshotAdapter
from .engine import StrategyDispatcher
from .profile_io import load_profile_file
from .serialization import assessment_to_dict


def replay_jsonl(
    *,
    input_path: Path,
    profile_path: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    profile = load_profile_file(profile_path, require_enabled=False)
    if profile is None:
        raise ValueError("profile could not be loaded")
    adapter = MayakSnapshotAdapter()
    dispatcher = StrategyDispatcher()
    counts: Counter[str] = Counter()
    total = 0
    invalid = 0
    output_handle = None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_handle = output_path.open("w", encoding="utf-8")
    try:
        with input_path.open(encoding="utf-8") as input_handle:
            for line in input_handle:
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    if not isinstance(raw, dict):
                        raise TypeError("row is not an object")
                    snapshot = adapter.adapt(raw)
                    assessment = dispatcher.evaluate(snapshot, profile)
                except (json.JSONDecodeError, ValueError, TypeError):
                    invalid += 1
                    continue
                total += 1
                counts[assessment.status.value] += 1
                if output_handle is not None:
                    output_handle.write(
                        json.dumps(
                            assessment_to_dict(assessment),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
    finally:
        if output_handle is not None:
            output_handle.close()
    return {
        "profile_id": profile.profile_id,
        "profile_version": profile.version,
        "processed": total,
        "invalid_rows": invalid,
        "status_counts": dict(sorted(counts.items())),
        "trading_effect": "NONE",
    }
