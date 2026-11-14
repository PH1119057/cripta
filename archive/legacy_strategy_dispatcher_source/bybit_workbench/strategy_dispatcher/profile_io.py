from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import (
    MatchOperator,
    ProfileRule,
    RequirementMode,
    StrategyMarketProfile,
)


def load_profile_file(path: Path, *, require_enabled: bool = False) -> StrategyMarketProfile | None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"profile must be a JSON object: {path}")
    enabled = raw.get("enabled", False)
    if require_enabled and enabled is not True:
        return None
    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise ValueError(f"profile rules must be a non-empty array: {path}")
    rules = tuple(_parse_rule(item, path) for item in rules_raw)
    return StrategyMarketProfile(
        profile_id=_required_text(raw, "profile_id", path),
        version=_required_text(raw, "version", path),
        display_name_ru=_required_text(raw, "display_name_ru", path),
        description_ru=_required_text(raw, "description_ru", path),
        rules=rules,
    )


def load_profile_directory(path: Path, *, require_enabled: bool = True) -> tuple[StrategyMarketProfile, ...]:
    if not path.exists():
        return ()
    profiles = []
    for item in sorted(path.glob("*.json")):
        profile = load_profile_file(item, require_enabled=require_enabled)
        if profile is not None:
            profiles.append(profile)
    return tuple(profiles)


def _parse_rule(raw: Any, path: Path) -> ProfileRule:
    if not isinstance(raw, dict):
        raise TypeError(f"profile rule must be an object: {path}")
    expected = raw.get("expected")
    if not isinstance(expected, list) or not expected:
        raise ValueError(f"profile rule expected must be a non-empty array: {path}")
    return ProfileRule(
        feature_id=_required_text(raw, "feature_id", path),
        mode=RequirementMode(_required_text(raw, "mode", path)),
        operator=MatchOperator(_required_text(raw, "operator", path)),
        expected=tuple(expected),
        weight=float(raw.get("weight", 1.0)),
        reason_ru=str(raw.get("reason_ru") or ""),
    )


def _required_text(raw: dict[str, Any], key: str, path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"profile field {key!r} is required: {path}")
    return value.strip()
