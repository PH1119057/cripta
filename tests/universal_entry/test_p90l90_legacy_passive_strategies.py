"""Passive P90L90 twin card authoring: exact identity, no LIVE readiness."""
from datetime import UTC, datetime
from copy import deepcopy

import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.dashboard_control import card_from_editable
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import (
    MAIN_ID, MAIN_SYMBOLS, SUB_ID, SUB_SYMBOLS,
    build_passive_legacy_cards, exact_legacy_policy,
)
from bybit_workbench.universal_entry.readiness import assess_strategy_runtime_readiness

NOW = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)


def test_two_independent_cards_and_no_account_credentials_in_policy():
    main, sub = build_passive_legacy_cards(approved_at=NOW)
    assert (main.strategy_id, sub.strategy_id) == (MAIN_ID, SUB_ID)
    assert main.symbols == tuple(sorted(MAIN_SYMBOLS))
    assert sub.symbols == tuple(sorted(SUB_SYMBOLS))
    assert main.strategy_config_fingerprint != sub.strategy_config_fingerprint
    for card in (main, sub):
        assert {v.value for v in card.direction_policy} == {"LONG", "SHORT"}
        assert "trading_account_id" not in card.entry_policy.payload_json
        assert "account_connector_id" not in card.entry_policy.payload_json
        assert card.entry_policy.to_dict()["watch_policy"]["geometry"][
            "operator"
        ] == "P90L90_RAW_H9_UNION_CROSSING"
        exit_policy = card.exit_policy.to_dict()
        assert exit_policy["trailing"]["activation_profit_pct"] == "0.5"
        assert exit_policy["trailing"]["distance_pct"] == "0.35"
        assert exit_policy["local_zone_exit"]["target"] == "OPPOSITE_RAW_BOUNDARY"
        assert card.protection_policy.to_dict()["initial_protection"][
            "stop_loss_pct"
        ] == "10"


def test_passive_cards_can_materialize_but_cannot_activate():
    for card in build_passive_legacy_cards(approved_at=NOW):
        activation = StrategyActivation(
            activation_id="passive-" + card.strategy_id,
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            enabled=False,
            enabled_at=NOW,
            disabled_at=NOW,
            scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
            operator="owner",
            source="test",
        )
        entry, exit_plan = materialize_plans(card, activation)
        assert entry.strategy_id == exit_plan.strategy_id == card.strategy_id
        assert entry.strategy_config_fingerprint == card.strategy_config_fingerprint
        assert exit_plan.strategy_config_fingerprint == card.strategy_config_fingerprint
        readiness = assess_strategy_runtime_readiness(card, observer_ready=True)
        assert not readiness.active_ready
        assert not readiness.execution_ready
        codes = {r.code for r in readiness.reasons}
        assert "P90L90_RAW_ENTRY_PRODUCER_NOT_WIRED" in codes
        assert "P90L90_RAW_EXIT_CONSUMER_NOT_WIRED" in codes


@pytest.mark.parametrize("section,field,value", [
    ("geometry", "h9_equality", "TOLERANCE_025"),
    ("geometry", "depth_denominator", "H9_WORKING_SPAN"),
    ("geometry", "atr_period", 100),
    ("width_filter", "previous_days", 5),
    ("width_filter", "tolerance_pp", "0.25"),
])
def test_authoring_rejects_mutated_research_geometry(section, field, value):
    raw = deepcopy(exact_legacy_policy(strategy_id=MAIN_ID, symbols=MAIN_SYMBOLS))
    geo = raw["entry_policy"]["watch_policy"]["geometry"]
    target = geo if section == "geometry" else geo["width_filter"]
    target[field] = value
    with pytest.raises(ValueError):
        card_from_editable(raw, approved_at=NOW, approved_source="test")


def test_rejects_incorrect_universe_and_research_inner_tp_substitution():
    with pytest.raises(ValueError, match="universe"):
        exact_legacy_policy(strategy_id=MAIN_ID, symbols=SUB_SYMBOLS)
    raw = exact_legacy_policy(strategy_id=MAIN_ID, symbols=MAIN_SYMBOLS)
    raw["exit_policy"]["local_zone_exit"]["target"] = "OPPOSITE_INNER_BOUNDARY"
    with pytest.raises(ValueError, match="OPPOSITE_RAW_BOUNDARY"):
        card_from_editable(raw, approved_at=NOW, approved_source="test")
