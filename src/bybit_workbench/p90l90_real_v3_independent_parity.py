"""Independent exchange-free P90L90 REAL v3 PAPER/REAL policy replay.

This proof never opens a StrategyActivation in PostgreSQL, never writes runtime
state and never calls Bybit.  It independently projects the owner-approved v3
policy into a PAPER model and compares that projection with the actual REAL
Universal Entry/Exit adapters on the same causal native facts.

The production PAPER v1/v2 strategies remain immutable and are not activated.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from types import SimpleNamespace
from typing import Any

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry import ActivePlanRegistry, UniversalEntryEngine
from bybit_workbench.universal_entry.contracts import (
    DataQuality,
    EntryDecisionCode,
    FrozenPolicy,
    StrategyActivation,
    StrategyExecutionBindingRef,
    TechnicalReadiness,
    TradeDirection,
    TradingCapacitySnapshot,
)
from bybit_workbench.universal_entry.engine import AccountAdmissionContext
from bybit_workbench.universal_entry.execution_bridge import (
    BridgePolicyBundle,
    prepare_runtime_entry_command,
)
from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90Crossing,
    P90L90EntryState,
)
from bybit_workbench.universal_entry.p90l90_crossing_fact import p90l90_crossing_fact
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    MAX_ACCOUNT_COMMITTED,
    REQUESTED_USDT,
    VERSION,
    real_v3_cards,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor
from bybit_workbench.universal_exit.contracts import ExitActionKind, ExitObservation
from bybit_workbench.universal_exit.execution_bridge import validate_exit_mutation
from bybit_workbench.universal_exit.p90l90_real_v3 import project_p90l90_real_exit

_AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
_D = Decimal
_SECOND48 = StrategyHardStopEmbargoPolicy(
    enabled=True,
    hardstop_count=2,
    rolling_hours=48,
    new_entry_pause_hours=48,
    close_existing_positions=False,
    domain="STRATEGY_VERSION",
)


class P90IndependentParityFailure(ValueError):
    """P90 v3 PAPER/REAL policy projection diverged."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise P90IndependentParityFailure(
            "P90 REAL v3 PAPER_REAL_DECISION_PARITY: " + reason
        )


class _MemoryAdmissionPort:
    """In-memory admission receipt only; there is no Exchange adapter."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    def admit(self, request: Any) -> Any:
        self.requests.append(request)
        _require(request.requested_amount == REQUESTED_USDT, "capital amount drift")
        return SimpleNamespace(
            capital_reservation=SimpleNamespace(reservation_id="parity-capital"),
            exchange_position_slot_claim_id="parity-slot",
            position_mode_state_ref="parity-one-way-0",
        )


def _cards() -> tuple[Any, Any]:
    cards = real_v3_cards(
        approved_at=_AT,
        policy_by_strategy_id={MAIN_ID: _SECOND48, SUB_ID: _SECOND48},
        approved_source="offline-p90-v3-parity",
    )
    _require(len(cards) == 2, "exact MAIN/SUB cards missing")
    _require({card.strategy_id for card in cards} == {MAIN_ID, SUB_ID}, "card scope drift")
    return cards


def _geometry() -> P90L90EntryState:
    return P90L90EntryState(
        effective_at=_AT,
        l53_low_raw=_D("90"),
        l53_high_raw=_D("110"),
        h9_5m_low_raw=_D("85"),
        h9_5m_high_raw=_D("115"),
        h9_15m_low_raw=_D("85"),
        h9_15m_high_raw=_D("115"),
        raw_h9_union_span=_D("30"),
        working_width_pct=_D("3"),
        previous_seven_day_median=_D("3"),
        long_depth_pct=_D("5"),
        short_depth_pct=_D("5"),
    )


def _native_fact(
    *, symbol: str, entry_plan_fingerprint: str, direction: TradeDirection, serial: int,
) -> tuple[Any, TradeCursor]:
    event_at = _AT + timedelta(seconds=serial + 1)
    long = direction is TradeDirection.LONG
    current = _D("89") if long else _D("111")
    previous = _D("91") if long else _D("109")
    boundary = _D("90") if long else _D("110")
    exec_id = f"p90-parity-{serial}"
    source = SimpleNamespace(
        fact_id=f"trade-{exec_id}",
        event_kind="PUBLIC_TRADE",
        symbol=symbol,
        observed_at=event_at,
        event_at=event_at,
        received_at=event_at + timedelta(milliseconds=20),
        source_refs=(f"bybit:publicTrade:{symbol}:{exec_id}",),
        attributes=FrozenPolicy.from_mapping({"price": current}),
    )
    crossing = P90L90Crossing(
        side=direction.value,
        event_at=event_at,
        effective_at=_AT,
        current_price=current,
        previous_price=previous,
        boundary=boundary,
        depth_pct=_D("5"),
    )
    fact = p90l90_crossing_fact(
        entry_plan_fingerprint=entry_plan_fingerprint,
        source_fact=source,
        crossing=crossing,
        geometry=_geometry(),
    )
    cursor = TradeCursor(symbol=symbol, exec_id=exec_id, seq=serial, traded_at=event_at)
    return fact, cursor


def _paper_entry_projection(
    *, card: Any, fact: Any, direction: TradeDirection, exit_plan_fingerprint: str,
) -> dict[str, object]:
    """Independent no-order PAPER projection of immutable v3 command semantics."""
    entry = card.entry_policy.to_dict()
    execution = dict(entry["execution_policy"])
    capital = card.capital_policy.to_dict()
    initial = dict(card.protection_policy.to_dict()["initial_protection"])
    lifecycle = card.lifecycle_policy.to_dict()
    attrs = fact.attributes.to_dict()
    nested = dict(attrs["p90l90"])
    _require(execution["order_type"] == "MARKET", "PAPER projection order type drift")
    _require(int(execution["max_request_age_seconds"]) == 5, "Entry TTL drift")
    _require(capital["requested_amount"] == "10", "PAPER amount drift")
    _require(int(capital["leverage"]) == 1, "PAPER leverage drift")
    _require(capital["margin_mode"] == "ISOLATED", "PAPER margin mode drift")
    _require(initial["stop_loss_pct"] == "10", "PAPER hard stop drift")
    _require(initial["take_profit_reference_path"] == "fact.p90l90.opposite_raw_l53_target",
             "PAPER initial TP source drift")
    _require(lifecycle["second_hardstop_embargo"] == {
        "enabled": True,
        "hardstop_count": 2,
        "rolling_hours": 48,
        "new_entry_pause_hours": 48,
        "close_existing_positions": False,
        "domain": "STRATEGY_VERSION",
    }, "SECOND48 drift")
    target = str(nested["opposite_raw_l53_target"])
    return {
        "stake_usdt": "10",
        "leverage": 1,
        "margin_mode": "ISOLATED",
        "side": "Buy" if direction is TradeDirection.LONG else "Sell",
        "price": str(nested["candidate_price"]),
        "entry_offset_pct": "0",
        "entry_limit_ttl_seconds": None,
        "entry_lifetime_mode": None,
        "entry_time_in_force": None,
        "initial_protection": {
            "stop_loss_enabled": True,
            "take_profit_enabled": True,
            "stop_loss_pct": "10",
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
            "strategy_id": card.strategy_id,
            "strategy_version": card.strategy_version,
            "strategy_config_fingerprint": card.strategy_config_fingerprint,
            "exit_plan_fingerprint": exit_plan_fingerprint,
            "take_profit_price": target,
            "take_profit_reference_path": "fact.p90l90.opposite_raw_l53_target",
        },
        "emergency_policy": dict(lifecycle["emergency_policy"]),
    }


def _aligned(value: Decimal, step: Decimal, *, up: bool) -> Decimal:
    return (value / step).to_integral_value(
        rounding=ROUND_CEILING if up else ROUND_FLOOR
    ) * step


def _exit_observation(
    position: StrategyPosition,
    *,
    best: Decimal,
    last: Decimal,
    trailing: Decimal,
    target: Decimal | None = None,
) -> ExitObservation:
    long = position.direction is TradeDirection.LONG
    raw: dict[str, object] = {
        "trading_account_id": position.trading_account_id,
        "strategy_execution_binding_id": position.strategy_execution_binding_id,
        "exchange_position_key": position.exchange_position_key,
        "actual_fill_confirmed": True,
        "protection_exchange_confirmed": True,
        "durable_cursor_verified": True,
        "pending_exit_mutation": False,
        "native_trade_price": str(last),
        "durable_best_favorable_price": str(best),
        "exchange_stop_price": "90" if long else "110",
        "exchange_tp_price": "110" if long else "90",
        "exchange_trailing_distance": str(trailing),
        "exchange_mark_price": "100.20" if long else "99.80",
        "tick_size": "0.01",
    }
    kind = "P90L90_REAL_NATIVE_TRADE"
    if target is not None:
        kind = "P90L90_REAL_CLOSED_5M"
        raw["opposite_raw_l53_target"] = str(target)
        raw["closed_geometry_proven"] = True
    when = position.fill_at + timedelta(minutes=5)
    return ExitObservation(
        observation_id=f"parity-exit-{position.symbol}-{position.direction.value}-{kind}",
        strategy_position_id=position.strategy_position_id,
        symbol=position.symbol,
        event_at=when,
        observed_at=when,
        received_at=when + timedelta(milliseconds=10),
        event_kind=kind,
        attributes=FrozenPolicy.from_mapping({"p90l90_real": raw}),
        source_refs=(f"offline-p90-parity:{position.symbol}",),
    )


def _paper_exit_projection(
    *, plan: Any, direction: TradeDirection, entry: Decimal,
    tick: Decimal, target: Decimal | None,
) -> tuple[ExitActionKind, dict[str, object]]:
    """Independent PAPER math from ExitPlan, not from REAL projector output."""
    policy = plan.exit_policy.to_dict()
    trailing = dict(policy["trailing"])
    local = dict(policy["local_zone_exit"])
    _require(
        trailing == {
            "enabled": True,
            "activation_profit_pct": "0.5",
            "distance_pct": "0.35",
            "reference": "BEST_FAVORABLE_PRICE_SINCE_ENTRY",
            "update_effective_from": "NEXT_ORDERED_TRADE",
            "never_loosen": True,
        },
        "TRAIL035 policy drift",
    )
    _require(
        local["operator"] == "P90L90_DYNAMIC_OPPOSITE_RAW_L53"
        and local["target"] == "OPPOSITE_RAW_BOUNDARY"
        and local["minimum_favorable_pct"] == "0.5"
        and local["marketable_action"] == "CLOSE_MARKET",
        "dynamic RAW L5-3 policy drift",
    )
    _require(
        int(policy["execution_policy"]["max_request_age_seconds"]) == 5,
        "Exit request TTL drift",
    )
    long = direction is TradeDirection.LONG
    if target is None:
        distance = _aligned(entry * _D("0.0035"), tick, up=False)
        active = _aligned(
            entry * (_D("1.005") if long else _D("0.995")),
            tick,
            up=long,
        )
        return ExitActionKind.SET_TRAILING, {
            "distance": str(distance),
            "active_price": str(active),
            "tpsl_mode": "Full",
        }
    chosen = _aligned(target, tick, up=long)
    return ExitActionKind.SET_TP, {
        "take_profit_price": str(chosen),
        "trigger_by": "LastPrice",
        "tpsl_mode": "Full",
        "order_type": "MARKET",
    }


def run_p90l90_real_v3_independent_decision_parity() -> dict[str, object]:
    """Run ten exact v3 cases with independent PAPER policy projection.

    This is deterministic release evidence only. It does not prove live account
    identity, current source freshness, delivery health, capital availability or
    Exchange acceptance; those remain independent LIVE-arm gates.
    """
    cards = _cards()
    cases = 0
    symbols_seen: list[str] = []
    for card in cards:
        _require(card.strategy_version == VERSION, "v3 version drift")
        _require(
            frozenset(card.symbols) == ALLOWED_SYMBOLS[card.strategy_id],
            "approved symbol cohort drift",
        )
        _require(
            card.capital_policy.to_dict()["max_simultaneous_committed_usdt"]
            == str(MAX_ACCOUNT_COMMITTED[card.strategy_id]),
            "Strategy capital ceiling drift",
        )
        activation = StrategyActivation(
            activation_id=f"parity-{card.strategy_id}",
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            enabled=True,
            enabled_at=_AT,
            scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
            operator="offline-parity",
            source="offline-p90-v3-parity",
        )
        registry = ActivePlanRegistry()
        registry.register_card(card)
        entry_plan = registry.activate(activation)
        exit_plan = registry.exact_plan_pair(activation.activation_id)[1]
        bridge = BridgePolicyBundle(
            asdict(card), asdict(entry_plan), asdict(exit_plan), asdict(activation)
        )
        account = "parity-main" if card.strategy_id == MAIN_ID else "parity-sub"
        binding = StrategyExecutionBindingRef(
            f"binding-{account}", activation.activation_id, "PRIMARY", account
        )

        for symbol in sorted(card.symbols):
            symbols_seen.append(symbol)
            for direction in (TradeDirection.LONG, TradeDirection.SHORT):
                serial = cases + 1
                fact, cursor = _native_fact(
                    symbol=symbol,
                    entry_plan_fingerprint=entry_plan.entry_plan_fingerprint,
                    direction=direction,
                    serial=serial,
                )
                admission = _MemoryAdmissionPort()
                context = AccountAdmissionContext(
                    binding=binding,
                    capacity=TradingCapacitySnapshot(
                        f"capacity-{serial}", fact.event_at, _D("100"),
                        DataQuality.HIGH,
                        f"offline:p90-parity:{account}",
                    ),
                    technical_readiness=TechnicalReadiness(
                        True, fact.event_at, "offline exact parity proof"
                    ),
                    account_ref="BYBIT:UNIFIED",
                    exchange_position_key=f"venue-bybit:{account}:{symbol}:0",
                    entry_admission_port=admission,  # type: ignore[arg-type]
                )
                engine = UniversalEntryEngine(registry)
                rows = engine.process(
                    fact,
                    proven_p90_native_cursor=cursor,
                    real_admission_required_for=frozenset({activation.activation_id}),
                    real_admission_ready_for=frozenset({activation.activation_id}),
                    account_admissions={activation.activation_id: (context,)},
                    admission_time=fact.event_at + timedelta(seconds=1),
                )
                _require(len(rows) == 1, f"{symbol}/{direction.value} Entry count")
                _require(len(rows[0].account_outcomes) == 1, "one account outcome required")
                outcome = rows[0].account_outcomes[0]
                _require(
                    outcome.decision.code is EntryDecisionCode.ACCEPTED
                    and outcome.execution_request is not None
                    and len(admission.requests) == 1,
                    f"{symbol}/{direction.value} REAL admission failed",
                )
                request = outcome.execution_request
                if request is None:
                    raise P90IndependentParityFailure("REAL request unexpectedly missing")
                real_command = prepare_runtime_entry_command(
                    request, bridge, now=fact.event_at + timedelta(seconds=1)
                )
                paper = _paper_entry_projection(
                    card=card,
                    fact=fact,
                    direction=direction,
                    exit_plan_fingerprint=exit_plan.exit_plan_fingerprint,
                )
                for key, expected in paper.items():
                    _require(
                        real_command.payload.get(key) == expected,
                        f"{symbol}/{direction.value} Entry adapter mismatch {key}",
                    )
                _require(
                    real_command.trading_account_id == account
                    and real_command.strategy_execution_binding_id == binding.strategy_execution_binding_id,
                    f"{symbol}/{direction.value} account lineage mismatch",
                )

                entry = _D("100")
                tick = _D("0.01")
                long = direction is TradeDirection.LONG
                position = StrategyPosition(
                    strategy_position_id=f"parity-position-{serial}",
                    account_ref="BYBIT:UNIFIED",
                    exchange_position_key=f"venue-bybit:{account}:{symbol}:0",
                    position_idx=0,
                    strategy_id=card.strategy_id,
                    strategy_version=card.strategy_version,
                    strategy_config_fingerprint=card.strategy_config_fingerprint,
                    strategy_activation_id=activation.activation_id,
                    signal_id=f"parity-signal-{serial}",
                    strategy_attempt_id=f"parity-attempt-{serial}",
                    entry_decision_id=f"parity-decision-{serial}",
                    entry_execution_request_id=f"parity-request-{serial}",
                    entry_plan_fingerprint=entry_plan.entry_plan_fingerprint,
                    exit_plan_fingerprint=exit_plan.exit_plan_fingerprint,
                    entry_command_id=f"parity-command-{serial}",
                    symbol=symbol,
                    direction=direction,
                    actual_avg_fill=entry,
                    actual_qty=_D("0.1"),
                    fill_at=fact.event_at,
                    initial_protection_confirmed_at=fact.event_at + timedelta(milliseconds=1),
                    strategy_execution_binding_id=binding.strategy_execution_binding_id,
                    execution_leg="PRIMARY",
                    trading_account_id=account,
                    identity_status="PROVEN",
                )
                best = _D("100.2") if long else _D("99.8")
                native = _exit_observation(
                    position, best=best, last=best, trailing=_D("0")
                )
                real_exit = project_p90l90_real_exit(position, exit_plan, native)
                expected_exit = _paper_exit_projection(
                    plan=exit_plan, direction=direction, entry=entry,
                    tick=tick, target=None,
                )
                _require(
                    real_exit is not None
                    and real_exit[0] is expected_exit[0]
                    and validate_exit_mutation(real_exit[0], real_exit[1]) == expected_exit[1],
                    f"{symbol}/{direction.value} TRAIL035 parity mismatch",
                )

                target = _D("109") if long else _D("91")
                closed = _exit_observation(
                    position,
                    best=_D("101") if long else _D("99"),
                    last=_D("100.8") if long else _D("99.2"),
                    trailing=_D("0.35"),
                    target=target,
                )
                real_target = project_p90l90_real_exit(position, exit_plan, closed)
                expected_target = _paper_exit_projection(
                    plan=exit_plan, direction=direction, entry=entry,
                    tick=tick, target=target,
                )
                _require(
                    real_target is not None
                    and real_target[0] is expected_target[0]
                    and validate_exit_mutation(real_target[0], real_target[1])
                    == expected_target[1],
                    f"{symbol}/{direction.value} dynamic RAW TP parity mismatch",
                )
                cases += 1

    _require(cases == 10, "incomplete five-symbol LONG/SHORT coverage")
    return {
        "status": "PASS",
        "proof_version": "P90L90_REAL_V3_INDEPENDENT_PAPER_REAL_POLICY_PARITY_V1",
        "cases": cases,
        "symbols": sorted(set(symbols_seen)),
        "directions": ["LONG", "SHORT"],
        "entry": "NATIVE_CAUSAL_FACT_TO_PAPER_PROJECTION_VS_REAL_EXECUTION_BRIDGE",
        "exit": "INDEPENDENT_TRAIL035_AND_DYNAMIC_RAW_TP_PROJECTION_VS_REAL_EXIT",
        "second48": "EXACT_STRATEGY_VERSION_POLICY",
        "old_paper_activation": False,
        "real_exchange_mutation": False,
        "database_mutation": False,
    }
