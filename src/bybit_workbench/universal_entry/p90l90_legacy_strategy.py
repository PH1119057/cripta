"""Owner-approved TWO independent P90L90 LEGACY passive authoring snapshots.

Only source of strategy semantics: TRADING_CONTOUR §7.6 and research
RESEARCH_COMPUTE §§22.1–22.2. No real account, LIVE or Exchange mutation.
The runtime consumer is explicitly NOT_READY until proven separately.
"""
from __future__ import annotations

from datetime import UTC, datetime

from .contracts import StrategyCard
from .dashboard_control import card_from_editable, strategy_authoring_template

VERSION = "1.0-p90l90-legacy-passive-20261027"
OWNER_SOURCE = "owner-2026-10-27-issue-243"
MAIN_ID = "p90l90_legacy_main_ltc_inj"
SUB_ID = "p90l90_legacy_sub_dot_arb_apt"
MAIN_SYMBOLS = ("LTCUSDT", "INJUSDT")
SUB_SYMBOLS = ("DOTUSDT", "ARBUSDT", "APTUSDT")


def exact_legacy_policy(
    *, strategy_id: str, symbols: tuple[str, ...],
) -> dict[str, object]:
    if strategy_id not in (MAIN_ID, SUB_ID):
        raise ValueError("Unknown owner-approved Strategy identity")
    expected = MAIN_SYMBOLS if strategy_id == MAIN_ID else SUB_SYMBOLS
    if symbols != expected:
        raise ValueError("Exact Strategy universe mismatch")
    payload = strategy_authoring_template()
    payload["strategy_id"] = strategy_id
    payload["strategy_version"] = VERSION
    payload["name"] = (
        "P90L90 LEGACY · LTC/INJ" if strategy_id == MAIN_ID
        else "P90L90 LEGACY · DOT/ARB/APT"
    )
    payload["description"] = (
        "Owner-approved independent passive P90L90 PREV7D TRAIL035 / "
        "RAW L5-3 target, SECOND48; LIVE and monitoring fail closed "
        "until exact producer, Exit and binding validation."
    )
    payload["symbols"] = list(symbols)
    payload["scope"] = {"kind": "symbols", "symbols": list(symbols)}
    payload["direction_policy"] = ["LONG", "SHORT"]
    payload["entry_policy"] = {
        "entry_plan_version": "p90l90-raw-h9-prev7d-v1",
        "predicate": {
            "op": "COMPARE",
            "params": {
                "path": "event_kind",
                "comparator": "EQ",
                "value": "P90L90_RAW_H9_PREV7D_CROSSING",
            },
        },
        "entry_reference_policy": {"enabled": False},
        "local_entry_policy": {"enabled": False},
        "watch_policy": {
            "enabled": True,
            "events": {"trade": "PUBLIC_TRADE", "candle_closed": "CANDLE_CLOSED"},
            "geometry": {
                "operator": "P90L90_RAW_H9_UNION_CROSSING",
                "timeframes": ["5", "15"],
                "lookback_bars": {"l53_5m": 36, "h9_5m": 108, "h9_15m": 36},
                "atr_period": 200,
                "zone_half_width_atr": "0.5",
                "h9_equality": "EXACT_RAW_SIDE",
                "depth_denominator": "RAW_H9_UNION_SPAN",
                "depth_min_pct": "0",
                "depth_max_pct": "15",
                "crossing": "FIRST_ADJACENT_NATIVE_RAW_TRADE",
                "width_filter": {
                    "source": "PREV7D_COMPLETED_UTC",
                    "previous_days": 7,
                    "tolerance_pp": "0.5",
                },
            },
        },
        # Research public trade proxy ≠ a confirmed Bybit order. Missing
        # execution settings remain explicit NOT_READY instead of defaults.
        "execution_policy": {
            "order_type": "NOT_APPROVED",
            "reference_value_path": "fact.p90l90.candidate_price",
        },
        "context_feature_policy": [],
        "context_ranking_policy": {"enabled": False},
    }
    payload["exit_policy"] = {
        "exit_plan_version": "p90l90-trail035-raw-tp-second48-v1",
        "conflict_policy": {"mode": "PRIORITY", "priority_order": "HIGHER_WINS",
                            "tie_break": "FAIL_CLOSED"},
        "rules": [],
        "hard_stop": {"enabled": False},
        "take_profit": {"enabled": False},
        "break_even": {"enabled": False, "economic_basis": "STRATEGY_BUFFER_OVER_ENTRY"},
        "trailing": {
            "enabled": True,
            "activation_profit_pct": "0.5",
            "distance_pct": "0.35",
            "reference": "BEST_FAVORABLE_PRICE_SINCE_ENTRY",
            "update_effective_from": "NEXT_ORDERED_TRADE",
            "never_loosen": True,
        },
        "geometry_exit": {"enabled": False},
        "local_zone_exit": {
            "enabled": True,
            "operator": "P90L90_DYNAMIC_OPPOSITE_RAW_L53",
            "geometry": "L5-3",
            "target": "OPPOSITE_RAW_BOUNDARY",
            "update_mode": "EACH_CAUSAL_GEOMETRY_CHANGE",
            "target_fact_path": "fact.p90l90.opposite_raw_l53_target",
            "minimum_favorable_pct": "0.5",
            "maker_only_if_previously_resting": True,
            "marketable_action": "CLOSE_MARKET",
            "priority": ["EXISTING_STOP", "EXISTING_RESTING_TP",
                         "NEW_MARKETABLE_TP", "TIGHTEN_STOP"],
        },
        "time_exit": {"enabled": False},
        "context_feature_policy": [],
        "context_ranking_policy": {"enabled": False},
    }
    payload["capital_policy"] = {
        "require_capacity": False,
        "amount_currency": "USDT",
        "modeled_research_notional_usdt": "10",
        # Real capital/leverage must be independently owner-approved.
    }
    payload["protection_policy"] = {
        "initial_protection": {
            "role": "CATASTROPHIC_GUARD",
            "stop_loss_enabled": True,
            "stop_loss_pct": "10",
            "take_profit_enabled": True,
            "take_profit_reference_path": "fact.p90l90.opposite_raw_l53_target",
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
        }
    }
    payload["lifecycle_policy"] = {
        "post_signal_outcome_policy": {"enabled": False},
        "hedge_policy": {"enabled": False},
        "reverse_on_opposite_signal": {"enabled": False},
        "emergency_policy": {
            "enabled": True,
            "terminal_loss_containment": "INITIAL_PROTECTION",
            "initial_stop_loss_pct": "10",
            "operator_kill": "MAINNET_GATE_OFF",
        },
        "second_hardstop_embargo": {
            "enabled": True,
            "hardstop_count": 2,
            "rolling_hours": 48,
            "new_entry_pause_hours": 48,
            "close_existing_positions": False,
            "cross_strategy_domain": "UNRESOLVED_OWNER_CONTRACT",
        },
        "position_mode": "ONE_WAY",
        "max_open_positions_per_symbol": 1,
        "no_holding_cap": True,
    }
    return payload


def build_passive_legacy_cards(
    *, approved_at: datetime, approved_source: str = OWNER_SOURCE,
) -> tuple[StrategyCard, StrategyCard]:
    if approved_at.tzinfo is None:
        raise ValueError("Approval timestamp must be timezone-aware")
    when = approved_at.astimezone(UTC)
    cards = tuple(
        card_from_editable(
            exact_legacy_policy(strategy_id=ident, symbols=symbols),
            approved_at=when,
            approved_source=approved_source,
        )
        for ident, symbols in ((MAIN_ID, MAIN_SYMBOLS), (SUB_ID, SUB_SYMBOLS))
    )
    assert len({card.strategy_config_fingerprint for card in cards}) == 2
    return cards  # type: ignore[return-value]
