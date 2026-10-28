"""Synthetic signed-private shape + native MFE into an exact REAL ExitPlan."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import StrategyActivation, TradeDirection
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import real_v3_cards
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor
from bybit_workbench.universal_exit.contracts import ExitActionKind, ExitEvaluationStatus
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.p90l90_real_mfe import P90RealMfeCheckpoint
from bybit_workbench.universal_exit.p90l90_real_observation import (
    P90RealObservationUnverified,
    ScopedPrivatePositionEvidence,
    build_real_native_exit_observation,
)

D = Decimal
AT = datetime(2026, 10, 29, 2, 0, tzinfo=UTC)
POLICY = StrategyHardStopEmbargoPolicy(
    enabled=True, hardstop_count=2, rolling_hours=48,
    new_entry_pause_hours=48, close_existing_positions=False,
    domain="STRATEGY_VERSION",
)


def _scope(*, short: bool = False, sub: bool = False):
    cards = real_v3_cards(
        approved_at=AT, policy_by_strategy_id={MAIN_ID: POLICY, SUB_ID: POLICY},
    )
    card = cards[1] if sub else cards[0]
    activation = StrategyActivation(
        activation_id="exact-test-activation", strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True, enabled_at=AT,
    )
    _, plan = materialize_plans(card, activation)
    account = "ta-sub" if sub else "ta-main"
    symbol = "DOTUSDT" if sub else "LTCUSDT"
    side = TradeDirection.SHORT if short else TradeDirection.LONG
    position = StrategyPosition(
        strategy_position_id="position-p90-real-test",
        account_ref="BYBIT:UNIFIED",
        exchange_position_key=exchange_position_key(account, symbol, 0),
        position_idx=0, strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        strategy_activation_id=activation.activation_id,
        signal_id="signal", strategy_attempt_id="attempt",
        entry_decision_id="entry-decision",
        entry_execution_request_id="entry-request",
        entry_plan_fingerprint="entry-fp",
        exit_plan_fingerprint=plan.exit_plan_fingerprint,
        entry_command_id="entry-command", symbol=symbol, direction=side,
        actual_avg_fill=D("100"), actual_qty=D("0.1"), fill_at=AT,
        initial_protection_confirmed_at=AT + timedelta(milliseconds=1),
        strategy_execution_binding_id="exact-binding",
        execution_leg="PRIMARY", trading_account_id=account,
        identity_status="PROVEN",
    )
    native_price = D("99") if short else D("101")
    cursor = TradeCursor(
        symbol=symbol, seq=101, exec_id="bybit-public-trade-101",
        traded_at=AT + timedelta(milliseconds=3),
    )
    state = P90RealMfeCheckpoint.begin(
        position=position, source_commit="a" * 40, first_cursor=cursor,
        first_price=native_price, verified_fill_to_first_trade_coverage=True,
    )
    raw = {
        "symbol": symbol, "side": "Sell" if short else "Buy",
        "positionIdx": 0, "avgPrice": "100", "size": "0.1",
        "stopLoss": "110" if short else "90",
        "takeProfit": "90" if short else "110",
    }
    private = ScopedPrivatePositionEvidence(
        trading_account_id=account,
        strategy_execution_binding_id="exact-binding",
        connector_id="connector-main" if not sub else "connector-sub",
        signed_provider_uid="12345678",
        generation_id="exact-private-generation",
        exchange_position_key=position.exchange_position_key,
        symbol=symbol, position_idx=0,
        observed_at=AT + timedelta(milliseconds=4),
        source_ref="bybit:signed-private:exact-account",
        position=raw, tick_size=D("0.01"),
        pending_exit_mutation=False,
    )
    return position, plan, state, cursor, private, native_price


def _observation(position, state, cursor, private, price):
    return build_real_native_exit_observation(
        position=position, checkpoint=state, cursor=cursor, price=price,
        native_fact_id="native-fact-101",
        native_source_refs=("bybit:publicTrade:exact:101",),
        private=private, received_at=AT + timedelta(milliseconds=5),
    )


@pytest.mark.parametrize(("short", "sub", "expected"), (
    (False,False,"100.65"), (True,True,"99.35"),
))
def test_native_mfe_and_private_position_use_same_universal_exit_engine(
    short: bool, sub: bool, expected: str,
) -> None:
    position, plan, state, cursor, private, price = _scope(short=short, sub=sub)
    observation = _observation(position, state, cursor, private, price)
    result = UniversalExitEngine().evaluate(position, plan, observation)
    assert result.status is ExitEvaluationStatus.DECISION_CREATED
    assert result.decision is not None
    assert result.decision.action_kind is ExitActionKind.SET_STOP
    assert result.decision.requested_mutation.to_dict()["stop_price"] == expected
    assert result.observation.source_refs[0] == "bybit:publicTrade:exact:101"
    assert observation.attributes.to_dict()["p90l90_real"]["durable_cursor_verified"]


@pytest.mark.parametrize(("field", "value"), (
    ("trading_account_id", "ta-other"),
    ("strategy_execution_binding_id", "wrong-binding"),
    ("signed_provider_uid", "missing-uid"),
    ("generation_id", ""),
    ("pending_exit_mutation", True),
    ("observed_at", AT - timedelta(seconds=1)),
))
def test_private_account_or_stale_exchange_evidence_is_rejected(
    field: str, value: object,
) -> None:
    position, _, state, cursor, private, price = _scope()
    with pytest.raises(P90RealObservationUnverified):
        _observation(position, state, cursor, replace(private, **{field: value}))


@pytest.mark.parametrize(("field", "value"), (
    ("stopLoss", "0"), ("takeProfit", "0"),
    ("avgPrice", "99"), ("size", "0.05"),
    ("positionIdx", 1), ("symbol", "DOTUSDT"),
))
def test_missing_exchange_stop_tp_or_partial_fill_never_claims_real_exit(
    field: str, value: object,
) -> None:
    position, _, state, cursor, private, price = _scope()
    raw = dict(private.position)
    raw[field] = value
    with pytest.raises(P90RealObservationUnverified):
        _observation(
            position, state, cursor,
            replace(private, position=raw), price,
        )


def test_uncommitted_or_wrong_native_cursor_is_not_accepted_as_durable_mfe() -> None:
    position, _, state, cursor, private, price = _scope()
    later = replace(cursor, seq=102, exec_id="uncommitted-102")
    with pytest.raises(P90RealObservationUnverified, match="NATIVE_MFE_OR_TIME"):
        _observation(position, state, later, private, price)
