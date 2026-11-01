from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .contracts import ContextMode, StrategyCard


@dataclass(frozen=True, slots=True)
class RuntimeReadinessReason:
    code: str
    contour: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "contour": self.contour, "message": self.message}


@dataclass(frozen=True, slots=True)
class StrategyRuntimeReadiness:
    policy_ready: bool
    observer_ready: bool
    paper_ready: bool
    monitor_ready: bool
    execution_ready: bool
    active_ready: bool
    reasons: tuple[RuntimeReadinessReason, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_ready": self.policy_ready,
            "observer_ready": self.observer_ready,
            "paper_ready": self.paper_ready,
            "monitor_ready": self.monitor_ready,
            "execution_ready": self.execution_ready,
            "active_ready": self.active_ready,
            "reasons": [item.as_dict() for item in self.reasons],
        }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()


def _enabled(value: object) -> bool:
    return bool(_mapping(value).get("enabled", False))


def _positive_decimal(value: object) -> bool:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return number.is_finite() and number > 0


def _positive_int(value: object) -> bool:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return False
    return number > 0


def _non_off_features(value: object) -> tuple[Mapping[str, object], ...]:
    rows: list[Mapping[str, object]] = []
    for item in _sequence(value):
        raw = _mapping(item)
        if str(raw.get("mode") or "OFF") != ContextMode.OFF.value:
            rows.append(raw)
    return tuple(rows)


def _p90l90_exact_no_order_paper_model(card: StrategyCard) -> bool:
    """Identify the reviewed immutable no-order v2; never infer REAL approval."""
    if card.strategy_version != "1.1-p90l90-strategy-second48-20261028":
        return False
    universes = {
        "p90l90_legacy_main_ltc_inj": ("INJUSDT", "LTCUSDT"),
        "p90l90_legacy_sub_dot_arb_apt": ("APTUSDT", "ARBUSDT", "DOTUSDT"),
    }
    if card.strategy_id not in universes or card.symbols != universes[card.strategy_id]:
        return False
    if {side.value for side in card.direction_policy} != {"LONG", "SHORT"}:
        return False
    entry = card.entry_policy.to_dict()
    watch = _mapping(entry.get("watch_policy"))
    geometry = _mapping(watch.get("geometry"))
    execution = _mapping(entry.get("execution_policy"))
    exit_policy = card.exit_policy.to_dict()
    local = _mapping(exit_policy.get("local_zone_exit"))
    capital = card.capital_policy.to_dict()
    simulation = _mapping(capital.get("paper_simulation"))
    protection = _mapping(
        card.protection_policy.to_dict().get("initial_protection")
    )
    lifecycle = card.lifecycle_policy.to_dict()
    embargo = _mapping(lifecycle.get("second_hardstop_embargo"))
    trailing = _mapping(exit_policy.get("trailing"))
    width_filter = _mapping(geometry.get("width_filter"))
    if (
        watch.get("enabled") is not True
        or geometry.get("lookback_bars") != {
            "l53_5m": 36, "h9_5m": 108, "h9_15m": 36
        }
        or geometry.get("atr_period") != 200
        or geometry.get("zone_half_width_atr") != "0.5"
        or geometry.get("h9_equality") != "EXACT_RAW_SIDE"
        or geometry.get("crossing") != "FIRST_ADJACENT_NATIVE_RAW_TRADE"
        or geometry.get("depth_denominator") != "RAW_H9_UNION_SPAN"
        or geometry.get("depth_min_pct") != "0"
        or geometry.get("depth_max_pct") != "15"
        or width_filter != {
            "source": "PREV7D_COMPLETED_UTC",
            "previous_days": 7,
            "tolerance_pp": "0.5",
        }
        or trailing != {
            "enabled": True,
            "activation_profit_pct": "0.5",
            "distance_pct": "0.35",
            "reference": "BEST_FAVORABLE_PRICE_SINCE_ENTRY",
            "update_effective_from": "NEXT_ORDERED_TRADE",
            "never_loosen": True,
        }
        or local.get("target") != "OPPOSITE_RAW_BOUNDARY"
        or local.get("minimum_favorable_pct") != "0.5"
        or local.get("target_fact_path") != "fact.p90l90.opposite_raw_l53_target"
        or local.get("priority") != [
            "EXISTING_STOP", "EXISTING_RESTING_TP",
            "NEW_MARKETABLE_TP", "TIGHTEN_STOP",
        ]
        or lifecycle.get("position_mode") != "ONE_WAY"
        or lifecycle.get("max_open_positions_per_symbol") != 1
        or geometry.get("operator") != "P90L90_RAW_H9_UNION_CROSSING"
        or execution.get("order_type") != "NOT_APPROVED"
        or execution.get("reference_value_path") != "fact.p90l90.candidate_price"
        or local.get("enabled") is not True
        or local.get("operator") != "P90L90_DYNAMIC_OPPOSITE_RAW_L53"
        or not _positive_decimal(protection.get("stop_loss_pct"))
        or str(protection.get("stop_loss_pct")) != "10"
        or capital.get("amount_currency") != "USDT"
        or capital.get("require_capacity") is not False
        or str(capital.get("modeled_research_notional_usdt")) != "10"
    ):
        return False
    if dict(simulation) != {
        "enabled": True,
        "mode": "NO_ORDER_NATIVE_PUBLIC_TRADE_PROXY",
        "notional_usdt": "10",
        "schema_leverage": 1,
        "fees": "TAKER_ENTRY_RESEARCH_PROXY",
        "exchange_fill_confirmed": False,
    }:
        return False
    return (
        set(embargo) == {
            "enabled", "hardstop_count", "rolling_hours",
            "new_entry_pause_hours", "close_existing_positions", "domain",
        }
        and type(embargo["enabled"]) is bool
        and type(embargo["hardstop_count"]) is int
        and embargo["hardstop_count"] > 0
        and type(embargo["rolling_hours"]) is int
        and embargo["rolling_hours"] > 0
        and type(embargo["new_entry_pause_hours"]) is int
        and embargo["new_entry_pause_hours"] > 0
        and embargo["close_existing_positions"] is False
        and embargo["domain"] == "STRATEGY_VERSION"
    )


def _p90l90_approved_real_v3_policy(card: StrategyCard) -> bool:
    """Exact owner-specified REAL capital contract, not runtime proof."""
    if card.strategy_version != "2.0-p90l90-real-main-sub-20261028":
        return False
    scopes = {
        "p90l90_legacy_main_ltc_inj": (("INJUSDT", "LTCUSDT"), "20"),
        "p90l90_legacy_sub_dot_arb_apt": (
            ("APTUSDT", "ARBUSDT", "DOTUSDT"), "30",
        ),
    }
    if card.strategy_id not in scopes:
        return False
    symbols, ceiling = scopes[card.strategy_id]
    if card.symbols != symbols:
        return False
    entry = card.entry_policy.to_dict()
    watch = _mapping(entry.get("watch_policy"))
    geometry = _mapping(watch.get("geometry"))
    execution = _mapping(entry.get("execution_policy"))
    capital = card.capital_policy.to_dict()
    initial = _mapping(card.protection_policy.to_dict().get("initial_protection"))
    exit_policy = card.exit_policy.to_dict()
    exit_execution = _mapping(exit_policy.get("execution_policy"))
    trailing = _mapping(exit_policy.get("trailing"))
    local = _mapping(exit_policy.get("local_zone_exit"))
    lifecycle = card.lifecycle_policy.to_dict()
    emergency = _mapping(lifecycle.get("emergency_policy"))
    embargo = _mapping(lifecycle.get("second_hardstop_embargo"))
    return (
        {side.value for side in card.direction_policy} == {"LONG", "SHORT"}
        and watch.get("enabled") is True
        and geometry.get("operator") == "P90L90_RAW_H9_UNION_CROSSING"
        and geometry.get("lookback_bars") == {
            "l53_5m": 36, "h9_5m": 108, "h9_15m": 36,
        }
        and geometry.get("atr_period") == 200
        and geometry.get("zone_half_width_atr") == "0.5"
        and geometry.get("h9_equality") == "EXACT_RAW_SIDE"
        and geometry.get("crossing") == "FIRST_ADJACENT_NATIVE_RAW_TRADE"
        and execution.get("order_type") == "MARKET"
        and execution.get("reference_value_path") == "fact.p90l90.candidate_price"
        and execution.get("max_request_age_seconds") == 5
        and capital.get("require_capacity") is True
        and capital.get("capacity_max_age_seconds") == 15
        and capital.get("capacity_min_quality") == "HIGH"
        and capital.get("amount_currency") == "USDT"
        and capital.get("requested_amount") == "10"
        and capital.get("leverage") == 1
        and capital.get("margin_mode") == "ISOLATED"
        and capital.get("max_simultaneous_committed_usdt") == ceiling
        and capital.get("max_open_positions_per_symbol") == 1
        and capital.get("count_pending_reservations") is True
        and capital.get("exchange_min_notional_if_exceeds_requested") == "REJECT"
        and initial.get("stop_loss_enabled") is True
        and initial.get("stop_loss_pct") == "10"
        and initial.get("exchange_resident_required") is True
        and initial.get("stop_loss_price_basis") == "ACTUAL_EXECUTED_AVERAGE_ENTRY"
        and initial.get("take_profit_enabled") is True
        and initial.get("take_profit_reference_path") == "fact.p90l90.opposite_raw_l53_target"
        and initial.get("tpsl_mode") == "Full"
        and initial.get("trigger_by") == "LastPrice"
        and exit_execution == {"max_request_age_seconds": 5}
        and trailing == {
            "enabled": True,
            "activation_profit_pct": "0.5",
            "distance_pct": "0.35",
            "reference": "BEST_FAVORABLE_PRICE_SINCE_ENTRY",
            "update_effective_from": "NEXT_ORDERED_TRADE",
            "never_loosen": True,
        }
        and local.get("enabled") is True
        and local.get("operator") == "P90L90_DYNAMIC_OPPOSITE_RAW_L53"
        and local.get("target") == "OPPOSITE_RAW_BOUNDARY"
        and local.get("target_fact_path") == "fact.p90l90.opposite_raw_l53_target"
        and local.get("minimum_favorable_pct") == "0.5"
        and lifecycle.get("position_mode") == "ONE_WAY"
        and lifecycle.get("max_open_positions_per_symbol") == 1
        and emergency == {
            "enabled": True,
            "terminal_loss_containment": "INITIAL_PROTECTION",
            "initial_stop_loss_pct": "10",
            "operator_kill": "MAINNET_GATE_OFF",
        }
        and set(embargo) == {
            "enabled", "hardstop_count", "rolling_hours",
            "new_entry_pause_hours", "close_existing_positions", "domain",
        }
        and embargo.get("enabled") is True
        and embargo.get("hardstop_count") == 2
        and embargo.get("rolling_hours") == 48
        and embargo.get("new_entry_pause_hours") == 48
        and embargo.get("close_existing_positions") is False
        and embargo.get("domain") == "STRATEGY_VERSION"
    )


def _owner_full_contour_diagnostic_policy(card: StrategyCard) -> bool:
    """Exact owner-triggered diagnostic policy; never a market-observer Strategy."""
    diagnostic_contracts = {
        "1.0-owner-full-contour-20261101": (
            {"OWNER_DIAGNOSTIC_TP_0_5", "OWNER_DIAGNOSTIC_SL_0_5"},
            "0.5",
        ),
        "2.0-owner-full-contour-e2e-20261101": (
            {
                "OWNER_DIAGNOSTIC_FORCE_CLOSE",
                "OWNER_DIAGNOSTIC_TP_0_5",
                "OWNER_DIAGNOSTIC_SL_0_5",
            },
            "0.5",
        ),
        "3.0-owner-full-contour-e2e-20261101": (
            {
                "OWNER_DIAGNOSTIC_FORCE_CLOSE",
                "OWNER_DIAGNOSTIC_TP_0_2",
                "OWNER_DIAGNOSTIC_SL_0_2",
            },
            "0.2",
        ),
    }
    diagnostic_contract = diagnostic_contracts.get(card.strategy_version)
    if diagnostic_contract is None:
        return False
    expected_rule_ids, expected_stop_loss_pct = diagnostic_contract
    scopes = {
        "owner_full_contour_diag_main": ("INJUSDT", "LTCUSDT"),
        "owner_full_contour_diag_sub": ("APTUSDT", "ARBUSDT", "DOTUSDT"),
    }
    expected = scopes.get(card.strategy_id)
    if expected is None or card.symbols != expected:
        return False
    entry = card.entry_policy.to_dict()
    predicate = _mapping(entry.get("predicate"))
    params = _mapping(predicate.get("params"))
    execution = _mapping(entry.get("execution_policy"))
    capital = card.capital_policy.to_dict()
    initial = _mapping(card.protection_policy.to_dict().get("initial_protection"))
    exit_policy = card.exit_policy.to_dict()
    rules = exit_policy.get("rules")
    lifecycle = card.lifecycle_policy.to_dict()
    emergency = _mapping(lifecycle.get("emergency_policy"))
    return (
        {side.value for side in card.direction_policy} == {"LONG", "SHORT"}
        and _mapping(entry.get("watch_policy")).get("enabled") is False
        and predicate.get("op") == "COMPARE"
        and params == {
            "path": "event_kind",
            "comparator": "EQ",
            "value": "OWNER_FULL_CONTOUR_TRIGGER",
        }
        and execution == {
            "order_type": "MARKET",
            "reference_value_path": "fact.price",
            "max_request_age_seconds": 15,
        }
        and capital.get("require_capacity") is True
        and capital.get("capacity_max_age_seconds") == 15
        and capital.get("capacity_min_quality") == "HIGH"
        and capital.get("amount_currency") == "USDT"
        and capital.get("requested_amount") == "10"
        and capital.get("leverage") == 1
        and capital.get("margin_mode") == "ISOLATED"
        and capital.get("max_simultaneous_committed_usdt") == "10"
        and capital.get("max_open_positions_per_symbol") == 1
        and initial.get("stop_loss_enabled") is True
        and initial.get("stop_loss_pct") == expected_stop_loss_pct
        and initial.get("exchange_resident_required") is True
        and initial.get("take_profit_enabled") is False
        and initial.get("trigger_by") == "LastPrice"
        and initial.get("tpsl_mode") == "Full"
        and isinstance(rules, list)
        and {str(item.get("rule_id") or "") for item in rules if isinstance(item, Mapping)}
            == expected_rule_ids
        and _mapping(exit_policy.get("execution_policy"))
            == {"max_request_age_seconds": 10}
        and lifecycle.get("position_mode") == "ONE_WAY"
        and lifecycle.get("max_open_positions_per_symbol") == 1
        and emergency == {
            "enabled": True,
            "terminal_loss_containment": "INITIAL_PROTECTION",
            "initial_stop_loss_pct": expected_stop_loss_pct,
            "operator_kill": "MAINNET_GATE_OFF",
        }
    )


def assess_strategy_runtime_readiness(
    card: StrategyCard,
    *,
    observer_ready: bool,
    p90_native_bar_runtime_proven: bool = False,
) -> StrategyRuntimeReadiness:
    """Prove monitoring and live-execution readiness independently.

    ACTIVE means real-market monitoring + paper lifecycle only.  It must never be
    coupled to permission for real exchange mutation.  execution_ready is a
    separate proof consumed by Strategy ExecutionPermission.
    """

    policy_reasons: list[RuntimeReadinessReason] = []
    paper_reasons: list[RuntimeReadinessReason] = []
    execution_reasons: list[RuntimeReadinessReason] = []
    observer_reasons: list[RuntimeReadinessReason] = []

    entry = card.entry_policy.to_dict()
    watch = _mapping(entry.get("watch_policy"))
    owner_diagnostic = _owner_full_contour_diagnostic_policy(card)
    if not bool(watch.get("enabled", False)) and not owner_diagnostic:
        policy_reasons.append(
            RuntimeReadinessReason(
                "ENTRY_WATCH_DISABLED",
                "ENTRY",
                "Entry Watch выключен; ACTIVE Strategy должна явно включить Entry.",
            )
        )

    geometry = _mapping(watch.get("geometry"))
    p90_operator = str(geometry.get("operator") or "") == (
        "P90L90_RAW_H9_UNION_CROSSING"
    )
    p90_paper_exact = p90_operator and _p90l90_exact_no_order_paper_model(card)
    p90_real_v3_policy = (
        p90_operator and _p90l90_approved_real_v3_policy(card)
    )
    if p90_operator and not p90_paper_exact and not p90_real_v3_policy:
        legacy_v1 = card.strategy_version == "1.0-p90l90-legacy-passive-20261027"
        policy_reasons.append(
            RuntimeReadinessReason(
                (
                    "P90L90_RAW_ENTRY_PRODUCER_NOT_WIRED" if legacy_v1
                    else "P90L90_PAPER_MODEL_OR_PRODUCER_NOT_PROVEN"
                ),
                "ENTRY",
                "P90 RAW strategy needs the exact reviewed no-order v2 producer.",
            )
        )
        paper_reasons.append(
            RuntimeReadinessReason(
                (
                    "P90L90_RAW_EXIT_CONSUMER_NOT_WIRED" if legacy_v1
                    else "P90L90_PAPER_EXIT_IDENTITY_NOT_PROVEN"
                ),
                "PAPER_EXIT",
                "P90 paper Exit requires exact v2 native-trade checkpoint model.",
            )
        )

    # Entry reference/local/context policies are materialized and consumed by the
    # Universal Entry engine.  Authoring validation owns their field completeness.
    typed_contexts = card.mayak_context_policy + card.dispatcher_context_policy
    for item in typed_contexts:
        if item.mode in {ContextMode.CONDITION, ContextMode.RANKING} and (
            item.max_age_seconds is None or item.min_quality is None
        ):
            policy_reasons.append(
                RuntimeReadinessReason(
                    "TYPED_CONTEXT_POLICY_INCOMPLETE",
                    "ENTRY",
                    "Decision-affecting typed context требует freshness и quality.",
                )
            )
            break

    exit_policy = card.exit_policy.to_dict()
    # Paper lifecycle supports deterministic price/time/context rules.  Local-zone
    # actions remain blocked until the Strategy explicitly defines an executable
    # zone operator rather than three ambiguous booleans.
    if _enabled(exit_policy.get("local_zone_exit")):
        zone = _mapping(exit_policy.get("local_zone_exit"))
        if not str(zone.get("operator") or ""):
            paper_reasons.append(
                RuntimeReadinessReason(
                    "EXIT_LOCAL_ZONE_OPERATOR_NOT_SET",
                    "PAPER_EXIT",
                    "Локальный Exit включён, но Strategy не задаёт точный оператор зоны.",
                )
            )

    capital = card.capital_policy.to_dict()
    require_capacity = bool(capital.get("require_capacity", False))
    if str(capital.get("amount_currency") or "").upper() != "USDT":
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_CAPITAL_CURRENCY_UNSUPPORTED",
                "PAPER",
                "Текущий paper/runtime contract использует USDT amount.",
            )
        )
    if not _positive_decimal(capital.get("requested_amount")):
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_AMOUNT_NOT_SET",
                "PAPER",
                "requested_amount должен быть положительным.",
            )
        )
    if not _positive_int(capital.get("leverage")):
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_LEVERAGE_NOT_SET",
                "PAPER",
                "leverage должен быть положительным целым.",
            )
        )

    execution = _mapping(entry.get("execution_policy"))
    order_type = str(execution.get("order_type") or "").upper()
    if order_type not in {"MARKET", "LIMIT_OFFSET"}:
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_ORDER_TYPE_NOT_SET",
                "PAPER",
                "Нужно явно выбрать MARKET или LIMIT_OFFSET.",
            )
        )
    if not _positive_int(execution.get("max_request_age_seconds")):
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_REQUEST_MAX_AGE_NOT_SET",
                "PAPER",
                "max_request_age_seconds должен быть положительным.",
            )
        )
    reference_path = str(execution.get("reference_value_path") or "")
    if not reference_path.startswith("fact."):
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_REFERENCE_PATH_UNSUPPORTED",
                "PAPER",
                "Execution reference_value_path должен быть причинным fact.* path.",
            )
        )
    if order_type == "LIMIT_OFFSET":
        if not _positive_decimal(execution.get("entry_offset_pct")):
            paper_reasons.append(
                RuntimeReadinessReason(
                    "PAPER_LIMIT_OFFSET_NOT_SET",
                    "PAPER",
                    "LIMIT_OFFSET требует положительный execution entry_offset_pct.",
                )
            )
        lifetime_mode = str(
            execution.get("entry_lifetime_mode") or "TIME_TTL"
        ).upper()
        if lifetime_mode == "TIME_TTL":
            if not _positive_int(execution.get("entry_limit_ttl_seconds")):
                paper_reasons.append(
                    RuntimeReadinessReason(
                        "PAPER_LIMIT_TTL_NOT_SET",
                        "PAPER",
                        "TIME_TTL LIMIT_OFFSET требует положительный TTL.",
                    )
                )
        elif lifetime_mode == "SIGNAL_VALIDITY":
            if execution.get("entry_limit_ttl_seconds") not in (None, ""):
                paper_reasons.append(
                    RuntimeReadinessReason(
                        "PAPER_SIGNAL_VALIDITY_HAS_TTL",
                        "PAPER",
                        "SIGNAL_VALIDITY не допускает numeric Entry TTL.",
                    )
                )
            if str(execution.get("entry_validity_operator") or "") != "R1_EXACT_SIGNAL":
                paper_reasons.append(
                    RuntimeReadinessReason(
                        "PAPER_SIGNAL_VALIDITY_OPERATOR_UNSUPPORTED",
                        "PAPER",
                        "R1 SIGNAL_VALIDITY требует entry_validity_operator=R1_EXACT_SIGNAL.",
                    )
                )
        else:
            paper_reasons.append(
                RuntimeReadinessReason(
                    "PAPER_ENTRY_LIFETIME_UNSUPPORTED",
                    "PAPER",
                    "LIMIT_OFFSET lifetime должен быть TIME_TTL или SIGNAL_VALIDITY.",
                )
            )
        time_in_force = str(execution.get("time_in_force") or "GTC").upper()
        if time_in_force not in {"GTC", "POST_ONLY"}:
            paper_reasons.append(
                RuntimeReadinessReason(
                    "PAPER_LIMIT_TIME_IN_FORCE_UNSUPPORTED",
                    "PAPER",
                    "LIMIT_OFFSET time_in_force должен быть GTC или POST_ONLY.",
                )
            )

    protection = card.protection_policy.to_dict()
    initial = _mapping(protection.get("initial_protection"))
    stop_enabled = bool(initial.get("stop_loss_enabled", True))
    target_enabled = bool(initial.get("take_profit_enabled", True))
    if stop_enabled and not _positive_decimal(initial.get("stop_loss_pct")):
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_INITIAL_STOP_NOT_SET",
                "PAPER",
                "Включённый initial stop требует положительный stop_loss_pct.",
            )
        )
    target_pct_ready = _positive_decimal(initial.get("take_profit_pct"))
    target_path = str(initial.get("take_profit_reference_path") or "").strip()
    target_path_ready = target_path.startswith("fact.")
    if target_enabled and not target_pct_ready and not target_path_ready:
        paper_reasons.append(
            RuntimeReadinessReason(
                "PAPER_INITIAL_TAKE_PROFIT_NOT_SET",
                "PAPER",
                "Включённый initial TP требует percent или causal fact reference.",
            )
        )

    if p90_paper_exact:
        # Default is CLOSED. A positive runtime proof is supplied only by
        # the dedicated Observer after source-pinned PostgreSQL checkpoint
        # recovery and LIVE Bybit publicTrade/5m/15m subscription + mirror
        # verification. This cannot be inferred from generic observer_ready,
        # from a historical CSV order, or merely from a StrategyCard.
        if not p90_native_bar_runtime_proven:
            paper_reasons.append(
                RuntimeReadinessReason(
                    "P90L90_NATIVE_BAR_BOUNDARY_ORDER_NOT_PROVEN",
                    "PAPER_EXIT",
                    "P90L90 SHADOW needs exact restart-durable publicTrade/bar ordering.",
                )
            )
        # The exact v2 never creates an exchange/paper order. Its own native
        # tick consumer creates a directly modeled paper_position with fixed
        # 10 USDT research notional and schema-only leverage=1. Bypass ONLY
        # generic paper-ORDER fields that do not apply to this separate rail.
        exempt_paper_order_codes = {
            "PAPER_AMOUNT_NOT_SET", "PAPER_LEVERAGE_NOT_SET",
            "PAPER_ORDER_TYPE_NOT_SET", "PAPER_REQUEST_MAX_AGE_NOT_SET",
        }
        paper_reasons = [
            item for item in paper_reasons
            if item.code not in exempt_paper_order_codes
        ]
    if p90_operator and not p90_real_v3_policy:
        # Historical/no-order P90 remains non-executable.  Exact REAL v3 is
        # recognized only by the complete immutable policy contract above;
        # current release identity, tests, LIVE equivalence and owner arm stay
        # independent durable gates in TRADING_CONTOUR §4.7.
        execution_reasons.append(
            RuntimeReadinessReason(
                "P90L90_EXCHANGE_ORDER_NOT_APPROVED",
                "EXECUTION",
                "P90L90 policy is not the exact executable REAL v3 contract.",
            )
        )

    # Live Execution requires a fresh account-capacity contract even though PAPER
    # monitoring is allowed to simulate a fixed Strategy amount without tying the
    # experiment to current wallet availability.
    execution_reasons.extend(paper_reasons)
    if not stop_enabled:
        execution_reasons.append(
            RuntimeReadinessReason(
                "LIVE_INITIAL_LOSS_CONTAINMENT_REQUIRED",
                "EXECUTION",
                "Real Strategy требует owner-approved initial loss-containment.",
            )
        )
    if not require_capacity:
        execution_reasons.append(
            RuntimeReadinessReason(
                "LIVE_CAPACITY_POLICY_REQUIRED",
                "EXECUTION",
                (
                    "Execution ON требует явную проверку доступного капитала; "
                    "PAPER может работать без неё."
                ),
            )
        )
    elif not _positive_int(capital.get("capacity_max_age_seconds")) or not str(
        capital.get("capacity_min_quality") or ""
    ):
        execution_reasons.append(
            RuntimeReadinessReason(
                "LIVE_CAPACITY_POLICY_INCOMPLETE",
                "EXECUTION",
                "Execution ON требует freshness и quality TradingCapacitySnapshot.",
            )
        )
    if str(initial.get("trigger_by") or "") != "LastPrice":
        execution_reasons.append(
            RuntimeReadinessReason(
                "INITIAL_TRIGGER_BY_UNSUPPORTED",
                "EXECUTION",
                "Текущий live Execution поддерживает initial protection только по LastPrice.",
            )
        )
    if str(initial.get("tpsl_mode") or "") != "Full":
        execution_reasons.append(
            RuntimeReadinessReason(
                "INITIAL_TPSL_MODE_UNSUPPORTED",
                "EXECUTION",
                "Текущий live Execution поддерживает initial protection только в Full mode.",
            )
        )
    for field, code, label in (
        ("break_even", "LIVE_EXIT_BREAK_EVEN_NOT_WIRED", "безубыточность"),
        ("trailing", "LIVE_EXIT_TRAILING_NOT_WIRED", "trailing"),
        ("time_exit", "LIVE_EXIT_TIME_NOT_WIRED", "выход по времени"),
    ):
        if (
            field == "trailing"
            and p90_real_v3_policy
        ):
            # Exact v3 TRAIL035 is implemented by the dedicated account-scoped
            # Bybit-native trading-stop/readback path, not generic local Exit.
            continue
        if _enabled(exit_policy.get(field)):
            execution_reasons.append(
                RuntimeReadinessReason(
                    code,
                    "EXECUTION",
                    f"Strategy-specific {label} пока не исполняется live Exit runtime.",
                )
            )
    if _non_off_features(exit_policy.get("context_feature_policy")):
        execution_reasons.append(
            RuntimeReadinessReason(
                "LIVE_EXIT_CONTEXT_NOT_WIRED",
                "EXECUTION",
                "Strategy-specific context Exit пока не подключён к live Exit runtime.",
            )
        )
    lifecycle = card.lifecycle_policy.to_dict()
    emergency = _mapping(lifecycle.get("emergency_policy"))
    if not emergency:
        execution_reasons.append(
            RuntimeReadinessReason(
                "LIVE_EMERGENCY_POLICY_REQUIRED",
                "EXECUTION",
                "Real Strategy требует exact lifecycle_policy.emergency_policy.",
            )
        )
    if _enabled(lifecycle.get("hedge_policy")):
        execution_reasons.append(
            RuntimeReadinessReason(
                "LIVE_HEDGE_EXECUTION_NOT_WIRED",
                "EXECUTION",
                "Paper hedge поддерживается отдельно; live Execution hedge ещё не разрешён.",
            )
        )

    if not observer_ready:
        observer_reasons.append(
            RuntimeReadinessReason(
                "MULTI_STRATEGY_OBSERVER_NOT_READY",
                "OBSERVER",
                "Multi-Strategy observer не подтверждён как доступный.",
            )
        )

    policy_ready = not policy_reasons
    paper_ready = not paper_reasons
    monitor_ready = policy_ready and paper_ready and observer_ready
    execution_ready = policy_ready and paper_ready and not execution_reasons
    reasons = tuple(policy_reasons + paper_reasons + observer_reasons + execution_reasons)
    return StrategyRuntimeReadiness(
        policy_ready=policy_ready,
        observer_ready=observer_ready,
        paper_ready=paper_ready,
        monitor_ready=monitor_ready,
        execution_ready=execution_ready,
        active_ready=monitor_ready,
        reasons=reasons,
    )
