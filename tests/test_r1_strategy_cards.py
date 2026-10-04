from decimal import Decimal

from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.r1_strategy import R1_STRATEGY_IDS, build_r1_cards
from bybit_workbench.universal_entry.readiness import assess_strategy_runtime_readiness


def test_r1_builds_five_per_symbol_bidirectional_cards() -> None:
    cards = build_r1_cards()
    assert len(cards) == 5
    assert {card.strategy_id for card in cards} == set(R1_STRATEGY_IDS.values())
    for card in cards:
        assert len(card.symbols) == 1
        assert {direction.value for direction in card.direction_policy} == {"LONG", "SHORT"}
        assert card.capital_policy.to_dict()["requested_amount"] == "10"
        assert card.capital_policy.to_dict()["leverage"] == 1


def test_r1_exact_entry_execution_contract() -> None:
    for card in build_r1_cards():
        entry = card.entry_policy.to_dict()
        geometry = entry["watch_policy"]["geometry"]
        execution = entry["execution_policy"]
        assert geometry == {
            "operator": "L53_STABLE_RANGE",
            "timeframes": ["5"],
            "lookback": 36,
            "atr_period": 200,
            "zone_half_width_atr": "0.5",
            "stable_states": 6,
            "working_width_min_pct": "1",
            "shock_reset_policy": {"enabled": False},
        }
        assert Decimal(execution["entry_offset_pct"]) == Decimal("0.10")
        assert execution["time_in_force"] == "POST_ONLY"
        assert execution["entry_lifetime_mode"] == "SIGNAL_VALIDITY"
        assert "entry_limit_ttl_seconds" not in execution


def test_r1_micro_live_card_is_policy_execution_ready() -> None:
    for card in build_r1_cards():
        readiness = assess_strategy_runtime_readiness(card, observer_ready=True)
        assert readiness.active_ready is True
        assert readiness.paper_ready is True
        assert readiness.execution_ready is True
        initial = card.protection_policy.to_dict()["initial_protection"]
        assert initial["role"] == "CATASTROPHIC_GUARD"
        assert initial["stop_loss_enabled"] is True
        assert Decimal(initial["stop_loss_pct"]) == Decimal("10.0")
        assert initial["take_profit_enabled"] is False
        emergency = card.lifecycle_policy.to_dict()["emergency_policy"]
        assert emergency["enabled"] is True
        assert emergency["terminal_loss_containment"] == "INITIAL_PROTECTION"
        assert Decimal(emergency["initial_stop_loss_pct"]) == Decimal("10.0")
        assert emergency["operator_kill"] == "MAINNET_GATE_OFF"


def test_r1_dynamic_exit_is_postonly_or_market_when_marketable() -> None:
    for card in build_r1_cards():
        exit_policy = card.exit_policy.to_dict()
        rule = exit_policy["rules"][0]
        mutation = rule["action"]["mutation"]
        assert mutation["order_type"] == "LIMIT"
        assert mutation["time_in_force"] == "POST_ONLY"
        assert mutation["marketable_action"] == "CLOSE_MARKET"
        assert exit_policy["hard_stop"]["enabled"] is False
        assert exit_policy["trailing"]["enabled"] is False


def test_r1_cards_materialize_exact_entry_and_exit_plans() -> None:
    cards = build_r1_cards()
    for card in cards:
        activation = StrategyActivation(
            activation_id=f"activation-{card.strategy_id}",
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            enabled=True,
            enabled_at=card.approved_at,
            scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
            operator="owner",
            source="test",
        )
        entry_plan, exit_plan = materialize_plans(card, activation)
        assert entry_plan.symbols == card.symbols
        assert {d.value for d in entry_plan.directions} == {"LONG", "SHORT"}
        watch = entry_plan.watch_policy.to_dict()
        assert watch["geometry"]["operator"] == "L53_STABLE_RANGE"
        exit_policy = exit_plan.exit_policy.to_dict()
        mutation = exit_policy["rules"][0]["action"]["mutation"]
        assert mutation["order_type"] == "LIMIT"
        assert mutation["time_in_force"] == "POST_ONLY"
        initial = exit_plan.protection_policy.to_dict()["initial_protection"]
        assert initial["stop_loss_pct"] == "10.0"
