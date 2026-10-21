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
        assert initial["take_profit_enabled"] is True
        assert initial["take_profit_reference_path"] == "fact.r1_opposite_inner_target"
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


def test_single_r1_strategy_card_owns_five_coins_and_ten_monitor_lanes() -> None:
    from datetime import UTC, datetime

    from bybit_workbench.universal_entry.r1_strategy import (
        R1_SYMBOLS,
        R1_UNIFIED_STRATEGY_ID,
        R1_UNIFIED_VERSION,
        build_r1_unified_card,
    )

    card = build_r1_unified_card(approved_at=datetime(2026, 10, 21, tzinfo=UTC))
    assert card.strategy_id == R1_UNIFIED_STRATEGY_ID
    assert card.strategy_version == R1_UNIFIED_VERSION
    assert set(card.symbols) == set(R1_SYMBOLS)
    assert set(d.value for d in card.direction_policy) == {"LONG", "SHORT"}
    activation = StrategyActivation(
        activation_id="test-activation-r1-five",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True,
        enabled_at=card.approved_at,
        scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
        operator="owner",
        source="test",
    )
    entry, exit_plan = materialize_plans(card, activation)
    assert entry.strategy_activation_id == activation.activation_id
    assert exit_plan.strategy_activation_id == activation.activation_id
    assert entry.symbols == card.symbols
    assert len({(entry.entry_plan_fingerprint, symbol, d.value)
                for symbol in entry.symbols for d in entry.directions}) == 10


def test_unified_r1_does_not_rewrite_historical_policy_or_identity() -> None:
    from datetime import UTC, datetime

    from bybit_workbench.universal_entry.r1_strategy import build_r1_unified_card

    at = datetime(2026, 10, 21, tzinfo=UTC)
    unified = build_r1_unified_card(approved_at=at)
    historical = build_r1_cards(approved_at=at)
    assert len(historical) == 5
    for old in historical:
        assert old.strategy_id != unified.strategy_id
        assert len(old.symbols) == 1
        for key in (
            "entry_policy", "exit_policy", "capital_policy",
            "protection_policy", "lifecycle_policy", "touch_policy",
            "direction_policy", "market_sensor_policy",
            "mayak_context_policy", "dispatcher_context_policy",
        ):
            assert getattr(old, key) == getattr(unified, key)
    assert {card.strategy_id for card in historical} == set(R1_STRATEGY_IDS.values())
