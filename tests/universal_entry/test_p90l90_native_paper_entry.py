"""Strict, zero-Exchange P90L90 native SHADOW Entry and replay guards."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from bybit_workbench.universal_entry.contracts import (
    EntryEvaluation,
    EntryExecutionIntent,
    FrozenPolicy,
    StrategyActivation,
    StrategyAttempt,
    StrategySignal,
    TradeDirection,
)
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards
from bybit_workbench.universal_entry.paper_runtime import PaperTradeRuntime
from bybit_workbench.universal_entry.runtime_loader import ActiveStrategyBundle

T0 = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)
DEFAULT = StrategyHardStopEmbargoPolicy.parse({
    "enabled": True, "hardstop_count": 2,
    "rolling_hours": 48, "new_entry_pause_hours": 48,
    "close_existing_positions": False, "domain": "STRATEGY_VERSION",
})


class _Cursor:
    def __init__(self, one=None, rows=None):
        self.one = one
        self.rows = [] if rows is None else rows

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class _ShadowDb:
    def __init__(self):
        self.rows = {}
        self.events = []
        self.active_account = "ta-main"
        self.lock_count = 0

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, args=()):
        if "pg_advisory_xact_lock" in sql:
            self.lock_count += 1
            return _Cursor()
        if "FROM strategy_entry.strategy_execution_bindings b" in sql:
            return _Cursor(rows=[(self.active_account,)])
        if "SELECT paper_position_id,payload FROM strategy_entry.paper_positions" in sql:
            return _Cursor(rows=[
                (k, v) for k, v in self.rows.items()
            ])
        if "SELECT paper_position_id FROM strategy_entry.paper_positions" in sql:
            return _Cursor()
        if "INSERT INTO strategy_entry.paper_positions" in sql:
            position_id = args[0]
            if position_id in self.rows:
                return _Cursor()
            self.rows[position_id] = json.loads(args[-1])
            return _Cursor(one=(position_id,))
        if "INSERT INTO strategy_entry.paper_position_events" in sql:
            self.events.append(tuple(args))
            return _Cursor()
        raise AssertionError(f"Unexpected P90 SQL: {sql[:80]}")


def setup(ident=MAIN_ID):
    card = next(
        c for c in new_version_cards(
            approved_at=T0,
            policy_by_strategy_id={MAIN_ID: DEFAULT, SUB_ID: DEFAULT},
        ) if c.strategy_id == ident
    )
    a = StrategyActivation(
        activation_id="shadow-only-" + ident,
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True, enabled_at=T0,
    )
    entry, exit_ = materialize_plans(card, a)
    bundle = ActiveStrategyBundle(card, a, entry, exit_, T0)
    symbol = card.symbols[0]
    signal = StrategySignal(
        signal_id="signal-e1",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        entry_plan_fingerprint=entry.entry_plan_fingerprint,
        strategy_activation_id=a.activation_id,
        symbol=symbol,
        direction=TradeDirection.LONG,
        detected_at=T0+timedelta(seconds=1),
        source_refs=("bybit-native-id",),
        fact_id="derived-p90-event-e1",
    )
    attempt = StrategyAttempt(
        strategy_attempt_id="attempt-e1",
        signal_id=signal.signal_id,
        strategy_id=signal.strategy_id,
        strategy_version=signal.strategy_version,
        strategy_config_fingerprint=signal.strategy_config_fingerprint,
        entry_plan_fingerprint=entry.entry_plan_fingerprint,
        strategy_activation_id=a.activation_id,
        created_at=T0+timedelta(seconds=1),
    )
    raw = {
        "event_kind": "P90L90_RAW_H9_PREV7D_CROSSING",
        "attributes": {
            "modeled_exchange_fill": False, "trade_fact_id": "native-fact",
            "p90l90": {
                "candidate_price": "100",
                "state_effective_at": T0.isoformat(),
            },
        },
    }
    intent = EntryExecutionIntent(
        strategy_attempt_id=attempt.strategy_attempt_id,
        signal_id=signal.signal_id,
        strategy_id=signal.strategy_id,
        strategy_version=signal.strategy_version,
        strategy_config_fingerprint=signal.strategy_config_fingerprint,
        entry_plan_fingerprint=entry.entry_plan_fingerprint,
        exit_plan_fingerprint=exit_.exit_plan_fingerprint,
        symbol=symbol, direction=TradeDirection.LONG,
        observed_at=T0+timedelta(seconds=1),
        payload=FrozenPolicy.from_mapping({"signal_fact": raw}),
    )
    evaluation = EntryEvaluation(
        signal, attempt, None, None, intent, (), (), (),
    )
    return bundle, evaluation


def invoke(runtime, evaluation, bundle, *, source="native-fact", exec_id="e1"):
    return runtime.create_p90l90_native_paper_entry(
        evaluation, bundle, exec_id=exec_id, exchange_sequence=123,
        event_at=T0+timedelta(seconds=1),
        native_trade_price=D("100"), raw_trade_fact_id=source,
    )


def test_paper_entry_has_no_exchange_order_and_durable_checkpoint():
    db = _ShadowDb()
    runtime = PaperTradeRuntime(db)
    bundle, evaluation = setup()
    paper_id = invoke(runtime, evaluation, bundle)
    assert paper_id.startswith("paper-p90-")
    assert len(db.rows) == 1
    assert len(db.events) == 1
    state = db.rows[paper_id]
    assert state["modeled_exchange_fill"] is False
    assert state["notional_usdt"] == "10"
    assert state["p90l90_exit_checkpoint"]["trading_account_id"] == "ta-main"
    assert state["p90l90_exit_checkpoint"]["last_exchange_sequence"] == 123
    assert state["p90l90_exit_checkpoint"]["seen_exec_ids"] == ["e1"]
    assert runtime.create_p90l90_native_paper_entry(
        evaluation, bundle, exec_id="e1", exchange_sequence=123,
        event_at=T0+timedelta(seconds=1), native_trade_price=D("100"),
        raw_trade_fact_id="native-fact",
    ) is None
    assert len(db.events) == 1
    assert db.lock_count == 2


def test_paper_entry_refuses_spoofed_native_trade_before_db_mutation():
    db = _ShadowDb()
    runtime = PaperTradeRuntime(db)
    bundle, eval_ = setup()
    with pytest.raises(RuntimeError, match="NOT_PROVEN_NATIVE"):
        invoke(runtime, eval_, bundle, source="not-the-native-fact")
    assert db.lock_count == 0
    assert not db.rows and not db.events


def test_v1_unresolved_contract_cannot_enter_paper():
    from bybit_workbench.universal_entry.p90l90_legacy_strategy import (
        build_passive_legacy_cards,
    )
    old = build_passive_legacy_cards(approved_at=T0)[0]
    _, eval_ = setup()
    new_bundle, _ = setup()
    mutated_bundle = ActiveStrategyBundle(
        old, new_bundle.activation, new_bundle.entry_plan,
        new_bundle.exit_plan, T0,
    )
    with pytest.raises(ValueError, match="EXACT_STRATEGY_IDENTITY"):
        invoke(PaperTradeRuntime(_ShadowDb()), eval_, mutated_bundle)
