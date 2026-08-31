from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
CANDIDATES = ROOT / "config" / "strategy_dispatcher" / "candidates"


def test_four_m3_candidates_are_non_executable_research_specs() -> None:
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in CANDIDATES.glob("*.json")]
    assert {row["candidate_id"] for row in rows} == {
        "M3_V1_LONG_ENTRY",
        "M3_V1_SHORT_ENTRY",
        "M3_V1_LONG_HOLD",
        "M3_V1_SHORT_HOLD",
    }
    assert all(row["status"] == "RESEARCH_REQUIRED" for row in rows)
    assert all(row["enabled"] is False for row in rows)
    assert all(row["trading_effect"] == "NONE" for row in rows)
    assert all(row["rules"] == [] for row in rows)


def test_owner_approved_m3_profiles_are_exactly_the_live_version() -> None:
    runtime_profiles = ROOT / "config" / "strategy_dispatcher" / "profiles"
    rows = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in runtime_profiles.glob("m3_v1_*.json")
    ]
    assert {row["profile_id"] for row in rows} == {
        "M3_V1_LONG_ENTRY", "M3_V1_SHORT_ENTRY",
        "M3_V1_LONG_HOLD", "M3_V1_SHORT_HOLD",
    }
    assert all(row["enabled"] is True for row in rows)
    assert all(row["version"] == "1.0.0-owner-live" for row in rows)


def test_removed_unreachable_states_are_not_advertised() -> None:
    roots = (
        ROOT / "src" / "bybit_workbench" / "mayak",
        ROOT / "config" / "strategy_dispatcher",
    )
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".py", ".json", ".md"}
    )
    for unreachable in (
        "COUNTER_SPIKE",
        "FALSE_REVERSAL",
        "WHIPSAW",
        "POSITION_BUILDUP",
        "POSITION_REDUCTION",
    ):
        assert unreachable not in source
