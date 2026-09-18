from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.contracts import (
    CandidateCooldown,
    FrozenPolicy,
    StrategyCard,
    TouchPolicy,
    TradeDirection,
)
from bybit_workbench.universal_entry.dashboard_control import (
    build_new_strategy_version,
    card_from_editable,
    card_to_editable,
    strategy_authoring_template,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "operations/implementation/examples/STRATEGY_SETTINGS_CLASSIC_EXPERIMENT_V1.json"


def _minimal_payload() -> dict[str, object]:
    raw = deepcopy(strategy_authoring_template())
    raw["strategy_id"] = "settings-example"
    raw["strategy_version"] = "1"
    raw["name"] = "Settings example"
    raw["description"] = "No trading-number defaults"
    raw["symbols"] = ["UNIUSDT"]
    raw["scope"] = {"kind": "symbols", "symbols": ["UNIUSDT"]}
    raw["direction_policy"] = ["LONG"]
    return raw


def _legacy_card(
    *,
    exit_policy: dict[str, object] | None = None,
    protection_policy: dict[str, object] | None = None,
) -> StrategyCard:
    return StrategyCard.build(
        strategy_id="legacy-settings",
        strategy_version="1",
        name="Legacy settings",
        description="Compatibility authoring source",
        scope=FrozenPolicy.from_mapping({"kind": "symbols", "symbols": ["UNIUSDT"]}),
        symbols=("UNIUSDT",),
        direction_policy=(TradeDirection.LONG,),
        entry_policy=FrozenPolicy.from_mapping(
            {
                "entry_plan_version": "entry-1",
                "predicate": {"op": "TOUCH"},
                "watch_policy": {"enabled": False},
                "context_feature_policy": [],
                "context_ranking_policy": {"enabled": False},
            }
        ),
        exit_policy=FrozenPolicy.from_mapping(
            exit_policy or {"exit_plan_version": "legacy-exit-1"}
        ),
        capital_policy=FrozenPolicy.from_mapping({"require_capacity": False}),
        protection_policy=FrozenPolicy.from_mapping(protection_policy or {"enabled": False}),
        lifecycle_policy=FrozenPolicy.from_mapping(
            {"post_signal_outcome_policy": {"enabled": False}}
        ),
        touch_policy=TouchPolicy(candidate_cooldown=CandidateCooldown(enabled=False)),
        approved_at=datetime(2026, 9, 18, tzinfo=UTC),
        approved_source="legacy-test",
    )


def test_authoring_template_has_explicit_strategy_setting_slots_without_numbers() -> None:
    raw = strategy_authoring_template()
    exit_policy = raw["exit_policy"]
    assert isinstance(exit_policy, dict)
    assert exit_policy["rules"] == []
    for field in (
        "hard_stop",
        "take_profit",
        "break_even",
        "trailing",
        "geometry_exit",
        "local_zone_exit",
        "time_exit",
    ):
        value = exit_policy[field]
        assert isinstance(value, dict)
        assert value["enabled"] is False

    assert "percent" not in exit_policy["hard_stop"]
    assert "percent" not in exit_policy["take_profit"]
    assert "activation_profit_pct" not in exit_policy["break_even"]
    assert "buffer_pct" not in exit_policy["break_even"]
    assert "activation_profit_pct" not in exit_policy["trailing"]
    assert "distance_pct" not in exit_policy["trailing"]

    protection = raw["protection_policy"]
    assert isinstance(protection, dict)
    initial = protection["initial_protection"]
    assert initial["stop_loss_enabled"] is False
    assert initial["take_profit_enabled"] is False
    assert "stop_loss_pct" not in initial
    assert "take_profit_pct" not in initial


def test_minimal_new_strategy_card_accepts_explicit_disabled_setting_slots() -> None:
    card = card_from_editable(
        _minimal_payload(),
        approved_at=datetime(2026, 9, 19, tzinfo=UTC),
        approved_source="test",
    )
    assert card.exit_policy.to_dict()["geometry_exit"] == {"enabled": False}
    assert card.protection_policy.to_dict()["initial_protection"]["stop_loss_enabled"] is False


def test_geometry_exit_is_reserved_fail_closed_until_exact_consumer_exists() -> None:
    raw = _minimal_payload()
    raw["exit_policy"]["geometry_exit"] = {
        "enabled": True,
        "geometry": "H3",
    }
    with pytest.raises(ValueError, match="reserved but not executable"):
        card_from_editable(
            raw,
            approved_at=datetime(2026, 9, 19, tzinfo=UTC),
            approved_source="test",
        )


def test_initial_safety_envelope_must_match_exit_authoring_fields() -> None:
    raw = _minimal_payload()
    raw["exit_policy"]["hard_stop"] = {"enabled": True, "percent": "2.00"}
    raw["protection_policy"]["initial_protection"].update(
        {"stop_loss_enabled": True, "stop_loss_pct": "1.00"}
    )
    with pytest.raises(ValueError, match="hard_stop percent must match"):
        card_from_editable(
            raw,
            approved_at=datetime(2026, 9, 19, tzinfo=UTC),
            approved_source="test",
        )


def test_classic_experiment_example_is_non_executable_and_preserves_research_labels() -> None:
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert payload["status"] == "EXAMPLE_ONLY_RESEARCH_NOT_STRATEGYCARD"
    assert payload["trading_rights"] == "NONE"
    assert payload["strategy_activation"] == "NONE"
    fragment = payload["strategy_card_fragment_example"]
    assert fragment["exit_policy"]["hard_stop"] == {"enabled": True, "percent": "2.00"}
    assert fragment["exit_policy"]["take_profit"] == {"enabled": True, "percent": "3.00"}
    assert fragment["exit_policy"]["rules"] == []
    research = payload["research_reference_only"]
    assert research["fee_aware_break_even_candidate"]["fee_cover_pct"] == "0.12"
    assert research["fee_aware_break_even_candidate"]["enabled"] is False
    assert research["h3_trailing_candidate"]["geometry"]["depth_minutes"] == 180
    assert research["h3_trailing_candidate"]["touch_number"] == 2
    assert research["h3_trailing_candidate"]["enabled"] is False


def test_new_version_from_legacy_card_adds_only_inert_missing_setting_slots() -> None:
    legacy = _legacy_card()
    legacy_fingerprint = legacy.strategy_config_fingerprint
    editable = card_to_editable(legacy)
    assert "hard_stop" not in editable["exit_policy"]

    editable["strategy_version"] = "2"
    created = build_new_strategy_version(
        legacy,
        editable,
        approved_at=datetime(2026, 9, 19, tzinfo=UTC),
        approved_source="test",
    )

    assert legacy.strategy_config_fingerprint == legacy_fingerprint
    exit_policy = created.exit_policy.to_dict()
    for field in (
        "hard_stop",
        "take_profit",
        "trailing",
        "geometry_exit",
        "local_zone_exit",
        "time_exit",
    ):
        assert exit_policy[field] == {"enabled": False}
    assert exit_policy["break_even"] == {
        "enabled": False,
        "economic_basis": "STRATEGY_BUFFER_OVER_ENTRY",
    }
    assert exit_policy["rules"] == []
    assert "percent" not in exit_policy["hard_stop"]
    assert "percent" not in exit_policy["take_profit"]
    initial = created.protection_policy.to_dict()["initial_protection"]
    assert initial == {
        "stop_loss_enabled": False,
        "take_profit_enabled": False,
    }


def test_new_version_preserves_explicit_legacy_safety_envelope_without_inventing_values() -> None:
    legacy = _legacy_card(
        exit_policy={
            "exit_plan_version": "legacy-exit-1",
            "hard_stop": {"enabled": True, "percent": "2.00"},
            "take_profit": {"enabled": True, "percent": "3.00"},
        },
        protection_policy={
            "initial_protection": {
                "stop_loss_pct": "2.00",
                "take_profit_pct": "3.00",
                "trigger_by": "LastPrice",
                "tpsl_mode": "Full",
            }
        },
    )
    editable = card_to_editable(legacy)
    editable["strategy_version"] = "2"
    created = build_new_strategy_version(
        legacy,
        editable,
        approved_at=datetime(2026, 9, 19, tzinfo=UTC),
        approved_source="test",
    )

    exit_policy = created.exit_policy.to_dict()
    assert exit_policy["hard_stop"] == {"enabled": True, "percent": "2.00"}
    assert exit_policy["take_profit"] == {"enabled": True, "percent": "3.00"}
    initial = created.protection_policy.to_dict()["initial_protection"]
    assert initial["stop_loss_enabled"] is True
    assert initial["take_profit_enabled"] is True
    assert initial["stop_loss_pct"] == "2.00"
    assert initial["take_profit_pct"] == "3.00"
