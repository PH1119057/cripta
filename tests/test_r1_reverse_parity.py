from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from bybit_workbench.universal_entry.contracts import (
    EntryDecisionCode,
    EntryExecutionIntent,
    FrozenPolicy,
    TradeDirection,
)
from bybit_workbench.universal_entry.r1_strategy import build_r1_cards
from bybit_workbench.universal_entry.reverse_intent import (
    build_reverse_transition_intent,
)
from operations.monitoring import universal_entry_shadow as observer

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)


def _intent(direction: TradeDirection) -> EntryExecutionIntent:
    card = build_r1_cards()[0]
    return EntryExecutionIntent(
        strategy_attempt_id="attempt-parity",
        signal_id="signal-parity",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        entry_plan_fingerprint="entry-plan-parity",
        exit_plan_fingerprint="exit-plan-parity",
        symbol=card.symbols[0],
        direction=direction,
        observed_at=NOW,
        payload=FrozenPolicy.from_mapping(
            {
                "signal_fact": {"attributes": {"price": "1"}},
                "execution_policy": {"order_type": "LIMIT_OFFSET"},
            }
        ),
    )


def test_r1_reverse_intent_is_mode_neutral_and_authorizes_exact_taker_flip() -> None:
    card = build_r1_cards()[0]
    intent = build_reverse_transition_intent(
        _intent(TradeDirection.SHORT),
        strategy_activation_id="activation-r1",
        from_direction=TradeDirection.LONG,
        lifecycle_policy=card.lifecycle_policy.to_dict(),
        capital_policy=card.capital_policy.to_dict(),
    )
    assert intent is not None
    assert intent.from_direction is TradeDirection.LONG
    assert intent.to_direction is TradeDirection.SHORT
    assert intent.reason == "OPPOSITE_ENTRY_FORCED_FLIP"
    assert intent.close_execution == "MARKET"
    assert intent.open_execution == "MARKET"
    assert intent.position_mode == "ONE_WAY"
    assert intent.position_idx == 0
    assert intent.execution_override == "OPPOSITE_FLIP_TAKER"
    assert (
        intent.entry_request_payload.to_dict()["execution_override"]
        == "OPPOSITE_FLIP_TAKER"
    )
    payload = intent.transition_payload().to_dict()
    assert payload["reverse_intent_id"] == intent.reverse_intent_id
    assert payload["entry_request_payload"] == intent.entry_request_payload.to_dict()


def test_same_direction_does_not_create_reverse_intent() -> None:
    card = build_r1_cards()[0]
    assert (
        build_reverse_transition_intent(
            _intent(TradeDirection.LONG),
            strategy_activation_id="activation-r1",
            from_direction=TradeDirection.LONG,
            lifecycle_policy=card.lifecycle_policy.to_dict(),
            capital_policy=card.capital_policy.to_dict(),
        )
        is None
    )


def test_paper_and_real_reverse_adapters_use_common_reverse_intent() -> None:
    paper = (
        ROOT / "src/bybit_workbench/universal_entry/paper_runtime.py"
    ).read_text(encoding="utf-8")
    observer = (
        ROOT / "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    worker = (
        ROOT / "operations/connectivity/r1_reverse_worker.py"
    ).read_text(encoding="utf-8")

    assert "build_reverse_transition_intent(" in paper
    assert "reverse_intent.reverse_intent_id" in paper
    assert "build_reverse_transition_intent(" in observer
    assert "reverse_intent.transition_payload().to_dict()" in observer
    assert 'request_payload["execution_override"] = "OPPOSITE_FLIP_TAKER"' not in worker
    assert "common intent execution_override mismatch" in worker


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _ReverseConnection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, parameters=()):
        params = tuple(parameters)
        self.calls.append((statement, params))
        if "FROM runtime.position_ownership" in statement:
            return _Cursor(("position-1", "Buy", "exit-plan-parity"))
        return _Cursor()


def test_real_observer_reverse_adapter_executes_common_intent_without_name_error() -> None:
    card = build_r1_cards()[0]
    signal = SimpleNamespace(
        direction=TradeDirection.SHORT,
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        entry_plan_fingerprint="entry-plan-parity",
        strategy_activation_id="activation-r1",
        signal_id="signal-parity",
        symbol=card.symbols[0],
        detected_at=NOW,
    )
    evaluation = SimpleNamespace(
        decision=SimpleNamespace(
            code=EntryDecisionCode.EXCHANGE_POSITION_OWNERSHIP_CONFLICT
        ),
        signal=signal,
        attempt=SimpleNamespace(strategy_attempt_id="attempt-parity"),
        execution_intent=_intent(TradeDirection.SHORT),
    )
    bundle = SimpleNamespace(card=card)
    connection = _ReverseConnection()

    transition_id = observer._maybe_record_reverse_transition(
        connection,
        evaluation,
        bundle,
    )

    assert transition_id is not None
    insert = next(
        params
        for statement, params in connection.calls
        if "INSERT INTO strategy_entry.reverse_transitions" in statement
    )
    assert insert[11] == "LONG"
    assert insert[12] == "SHORT"
    payload = json.loads(str(insert[14]))
    assert payload["execution_override"] == "OPPOSITE_FLIP_TAKER"
