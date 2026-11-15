from __future__ import annotations

from .contracts import StrategyMarketProfile


class StrategyMarketProfileRegistry:
    """Version-aware registry. It stores descriptions of needed markets, not strategies."""

    def __init__(self) -> None:
        self._profiles: dict[tuple[str, str], StrategyMarketProfile] = {}

    def register(self, profile: StrategyMarketProfile) -> None:
        key = (profile.profile_id, profile.version)
        if key in self._profiles:
            raise ValueError(f"market profile already registered: {profile.profile_id}@{profile.version}")
        self._profiles[key] = profile

    def get(self, profile_id: str, version: str) -> StrategyMarketProfile:
        try:
            return self._profiles[(profile_id, version)]
        except KeyError as exc:
            raise LookupError(f"unknown market profile: {profile_id}@{version}") from exc

    def profiles(self) -> tuple[StrategyMarketProfile, ...]:
        return tuple(self._profiles.values())
