from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from .contracts import StrategyCard
from .dashboard_control import card_from_editable

R1_VERSION: Final = "1.0-micro-live"
R1_SYMBOLS: Final = (
    "APTUSDT",
    "INJUSDT",
    "DOTUSDT",
    "LTCUSDT",
    "ARBUSDT",
)
R1_STRATEGY_IDS: Final = {
    "APTUSDT": "r1_aptusdt",
    "INJUSDT": "r1_injusdt",
    "DOTUSDT": "r1_dotusdt",
    "LTCUSDT": "r1_ltcusdt",
    "ARBUSDT": "r1_arbusdt",
}


def r1_strategy_payload(symbol: str) -> dict[str, object]:
    symbol = symbol.upper()
    if symbol not in R1_STRATEGY_IDS:
        raise ValueError(f"unsupported R1 symbol: {symbol}")
    strategy_id = R1_STRATEGY_IDS[symbol]
    return {
        "strategy_id": strategy_id,
        "strategy_version": R1_VERSION,
        "name": f"R1 · {symbol} · LONG+SHORT · MICRO_LIVE",
        "description": (
            "Owner-approved R1 2026-10-04. Per-symbol bidirectional ping-pong; "
            "Owner-approved MICRO_LIVE candidate; activation and execution remain "
            "fail-closed until explicit arm."
        ),
        "scope": {"kind": "symbols", "symbols": [symbol]},
        "symbols": [symbol],
        "direction_policy": ["LONG", "SHORT"],
        "entry_policy": {
            "entry_plan_version": "r1-entry-v1",
            "predicate": {"op": "TOUCH"},
            "entry_reference_policy": {
                "enabled": False,
                "reference": "CALCULATED_ENTRY",
            },
            "local_entry_policy": {"enabled": False},
            "watch_policy": {
                "enabled": True,
                "candidate_timeframe_minutes": 5,
                "required_closed_timeframes": ["5"],
                "events": {
                    "bar_open": "BAR_OPEN",
                    "candle_closed": "CANDLE_CLOSED",
                    "open_interest": "OPEN_INTEREST",
                    "trade": "PUBLIC_TRADE",
                },
                "geometry": {
                    "operator": "L53_STABLE_RANGE",
                    "timeframes": ["5"],
                    "lookback": 36,
                    "atr_period": 200,
                    "zone_half_width_atr": "0.5",
                    "stable_states": 6,
                    "working_width_min_pct": "1",
                    "shock_reset_policy": {"enabled": False},
                },
                "hourly_swing": {"enabled": False},
                "direction_rules": {
                    "LONG": {
                        "entry_zone_field": "support_top",
                        "touch_comparator": "LTE",
                    },
                    "SHORT": {
                        "entry_zone_field": "resistance_bottom",
                        "touch_comparator": "GTE",
                    },
                },
                "direction_precedence": ["LONG", "SHORT"],
                "candidate_lifecycle": {"clear_on_touch": True},
                "flow": {"enabled": False},
                "oi": {"enabled": False},
                "derived_event_kind": "R1_TOUCH",
            },
            "execution_policy": {
                "order_type": "LIMIT_OFFSET",
                "entry_offset_pct": "0.10",
                "entry_lifetime_mode": "SIGNAL_VALIDITY",
                "entry_validity_operator": "R1_EXACT_SIGNAL",
                "time_in_force": "POST_ONLY",
                "max_request_age_seconds": 30,
                "reference_value_path": "fact.calculated_entry_price",
            },
            "context_feature_policy": [],
            "context_ranking_policy": {"enabled": False},
        },
        "exit_policy": {
            "exit_plan_version": "r1-exit-v1",
            "conflict_policy": {
                "mode": "PRIORITY",
                "priority_order": "HIGHER_WINS",
                "tie_break": "FAIL_CLOSED",
            },
            "execution_policy": {"max_request_age_seconds": 30},
            "rules": [
                {
                    "rule_id": "r1_dynamic_opposite_inner_tp",
                    "priority": 100,
                    "repeat_policy": "EACH_MATCH",
                    "required_fact_paths": [
                        "fact.geometry.l5_3.target_inner",
                    ],
                    "predicate": {
                        "op": "COMPARE",
                        "params": {
                            "path": "event_kind",
                            "comparator": "EQ",
                            "value": "GEOMETRY_L5_3",
                        },
                    },
                    "action": {
                        "kind": "SET_TP",
                        "mutation": {
                            "take_profit_price": {
                                "fact_path": "fact.geometry.l5_3.target_inner"
                            },
                            "order_type": "LIMIT",
                            "time_in_force": "POST_ONLY",
                            "quantity": "ALL",
                            "marketable_action": "CLOSE_MARKET",
                        },
                    },
                }
            ],
            "hard_stop": {"enabled": False},
            "take_profit": {"enabled": False},
            "break_even": {
                "enabled": False,
                "economic_basis": "STRATEGY_BUFFER_OVER_ENTRY",
            },
            "trailing": {"enabled": False},
            "geometry_exit": {"enabled": False},
            "local_zone_exit": {
                "enabled": True,
                "operator": "R1_DYNAMIC_OPPOSITE_INNER",
                "geometry": "L5-3",
                "target": "OPPOSITE_INNER_BOUNDARY",
                "update_mode": "EACH_CAUSAL_GEOMETRY_CHANGE",
                "target_fact_path": "fact.geometry.l5_3.target_inner",
            },
            "time_exit": {"enabled": False},
            "context_feature_policy": [],
            "context_ranking_policy": {"enabled": False},
        },
        "capital_policy": {
            "require_capacity": True,
            "capacity_max_age_seconds": 15,
            "capacity_min_quality": "HIGH",
            "amount_currency": "USDT",
            "requested_amount": "10",
            "leverage": 1,
        },
        "protection_policy": {
            "initial_protection": {
                "role": "CATASTROPHIC_GUARD",
                "stop_loss_enabled": True,
                "stop_loss_pct": "10.0",
                "take_profit_enabled": False,
                "trigger_by": "LastPrice",
                "tpsl_mode": "Full",
            }
        },
        "lifecycle_policy": {
            "post_signal_outcome_policy": {"enabled": False},
            "hedge_policy": {"enabled": False},
            "reverse_on_opposite_signal": {
                "enabled": True,
                "same_strategy_only": True,
                "close_execution": "MARKET",
                "open_execution": "MARKET",
                "position_mode": "ONE_WAY",
                "position_idx": 0,
            },
            "emergency_policy": {
                "enabled": True,
                "terminal_loss_containment": "INITIAL_PROTECTION",
                "initial_stop_loss_pct": "10.0",
                "operator_kill": "MAINNET_GATE_OFF",
            },
        },
        "touch_policy": {
            "accepted_touch_numbers": [],
            "accept_touch_from": 1,
            "require_exit_from_zone": False,
            "minimum_exit_distance": {
                "enabled": False,
                "value": None,
                "unit": None,
                "scope": None,
            },
            "minimum_time_between_touches": {
                "enabled": False,
                "value": None,
                "unit": None,
                "scope": None,
            },
            "maximum_touch_count": None,
            "reset_on": [],
            "candidate_cooldown": {
                "enabled": False,
                "duration": None,
                "unit": None,
                "scope": None,
                "trigger_event": None,
                "anchor": None,
            },
        },
        "market_sensor_policy": [],
        "mayak_context_policy": [],
        "dispatcher_context_policy": [],
    }


def build_r1_cards(
    *,
    approved_at: datetime | None = None,
    approved_source: str = "owner-r1-2026-10-04",
) -> tuple[StrategyCard, ...]:
    when = (approved_at or datetime.now(UTC)).astimezone(UTC)
    return tuple(
        card_from_editable(
            r1_strategy_payload(symbol),
            approved_at=when,
            approved_source=approved_source,
        )
        for symbol in R1_SYMBOLS
    )
