"""REAL native Entry cannot bypass independently reconstructed durable SECOND48."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_observer_real_consumer import P90L90ObserverRealConsumer
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.p90l90_strategy_embargo import StrategyHardStopEmbargoPolicy
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

AT = datetime(2026, 10, 29, 2, 0, tzinfo=UTC)
POLICY = StrategyHardStopEmbargoPolicy(
    enabled=True, hardstop_count=2, rolling_hours=48,
    new_entry_pause_hours=48, close_existing_positions=False,
    domain="STRATEGY_VERSION",
)


def _consumer(
    calls: list[dict[str, object]],
    *,
    admissions: tuple[object, ...],
    proven: bool,
) -> P90L90ObserverRealConsumer:
    class _FakeOwner:
        def accept(self, *_args: object, **kwargs: object) -> None:
            calls.append(kwargs)

    consumer = object.__new__(P90L90ObserverRealConsumer)
    consumer._candidate_owners = {"LTCUSDT": _FakeOwner()}
    consumer._admission_provider = lambda _symbol, _at: (admissions, proven)
    consumer._connection = object()
    consumer._by_symbol = {
        "LTCUSDT": SimpleNamespace(card=SimpleNamespace(
            strategy_id=MAIN_ID,
            strategy_version=VERSION,
            strategy_config_fingerprint="exact-main-fp",
            lifecycle_policy=FrozenPolicy.from_mapping({
                "second_hardstop_embargo": POLICY.snapshot(),
            }),
        )),
    }
    return consumer


def _fact() -> tuple[MarketFactEnvelope, TradeCursor]:
    fact = MarketFactEnvelope(
        fact_id="synthetic-fact",
        event_kind="P90L90_RAW_H9_PREV7D_CROSSING",
        symbol="LTCUSDT",
        event_at=AT, observed_at=AT, received_at=AT,
        source_refs=("test:synthetic:no-exchange",),
        attributes=FrozenPolicy.from_mapping({}),
    )
    cursor = TradeCursor(
        symbol="LTCUSDT", exec_id="test-exec", seq=1, traded_at=AT,
    )
    return fact, cursor


def test_missing_exact_account_denied_before_any_embargo_or_entry() -> None:
    calls: list[dict[str, object]] = []
    consumer = _consumer(calls, admissions=(), proven=True)
    fact, cursor = _fact()
    with patch(
        "bybit_workbench.universal_entry.p90l90_observer_real_consumer.read_real_second48"
    ) as reader:
        with pytest.raises(RuntimeError, match="ACCOUNT_ADMISSION_NOT_VERIFIED"):
            consumer._on_candidate(fact, cursor)
        reader.assert_not_called()
    assert calls == []


def test_durable_second48_denial_cannot_be_overridden_by_admission() -> None:
    calls: list[dict[str, object]] = []
    account = SimpleNamespace(binding=SimpleNamespace(trading_account_id="ta-main"))
    consumer = _consumer(calls, admissions=(account,), proven=True)
    fact, cursor = _fact()
    with patch(
        "bybit_workbench.universal_entry.p90l90_observer_real_consumer.read_real_second48",
        return_value=(False, {"pause_until": AT.isoformat()}),
    ) as reader:
        consumer._on_candidate(fact, cursor)
    assert reader.call_count == 1
    assert reader.call_args.kwargs["strategy_id"] == MAIN_ID
    assert reader.call_args.kwargs["trading_account_id"] == "ta-main"
    assert len(calls) == 1
    assert calls[0]["allow_new_signals"] is False


def test_unproven_durable_exit_fails_before_entry_callback() -> None:
    calls: list[dict[str, object]] = []
    account = SimpleNamespace(binding=SimpleNamespace(trading_account_id="ta-main"))
    consumer = _consumer(calls, admissions=(account,), proven=True)
    fact, cursor = _fact()
    with patch(
        "bybit_workbench.universal_entry.p90l90_observer_real_consumer.read_real_second48",
        side_effect=RuntimeError("P90_REAL_SECOND48_EXACT_EXIT_UNPROVEN"),
    ):
        with pytest.raises(RuntimeError, match="SECOND48_EXACT_EXIT_UNPROVEN"):
            consumer._on_candidate(fact, cursor)
    assert calls == []
