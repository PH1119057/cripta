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
    with (
        patch(
            "bybit_workbench.universal_entry.p90l90_observer_real_consumer.read_real_second48"
        ) as reader,
        pytest.raises(RuntimeError, match="ACCOUNT_ADMISSION_NOT_VERIFIED"),
    ):
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
    with (
        patch(
            "bybit_workbench.universal_entry.p90l90_observer_real_consumer.read_real_second48",
            side_effect=RuntimeError("P90_REAL_SECOND48_EXACT_EXIT_UNPROVEN"),
        ),
        pytest.raises(RuntimeError, match="SECOND48_EXACT_EXIT_UNPROVEN"),
    ):
        consumer._on_candidate(fact, cursor)
    assert calls == []


def test_real_closed_15m_is_geometry_only_and_5m_routes_to_exit() -> None:
    calls: list[str] = []
    consumer = object.__new__(P90L90ObserverRealConsumer)
    consumer._verified_closed_candle_port = (
        lambda fact: calls.append(fact.symbol)
    )
    for timeframe in ("15", "5"):
        fact = MarketFactEnvelope(
            fact_id=f"closed-{timeframe}",
            event_kind="CANDLE_CLOSED",
            symbol="LTCUSDT",
            event_at=AT, observed_at=AT, received_at=AT,
            source_refs=("test:exact-synthetic-closed",),
            attributes=FrozenPolicy.from_mapping({"timeframe": timeframe}),
        )
        consumer._on_closed_candle(fact)
    assert calls == ["LTCUSDT"]


def test_real_gap_cannot_borrow_paper_ohlc_only_replay_even_without_exposure() -> None:
    class _EmptyRow:
        def fetchone(self):
            return (0,)

    class _EmptyDB:
        def execute(self, _sql, _params=()):
            return _EmptyRow()

    consumer = object.__new__(P90L90ObserverRealConsumer)
    consumer._connection = _EmptyDB()
    consumer._recovered = True
    consumer._gap_recovery_poisoned = False
    consumer._processors = {"LTCUSDT": object()}
    consumer._verified_closed_watermarks = {"LTCUSDT": (AT, AT)}
    with pytest.raises(RuntimeError, match="REQUIRES_EXACT_SOURCE_EPOCH"):
        consumer.catch_up_closed_histories({})


class _CountDB:
    def __init__(self, *, exposed: int = 0, pending: int = 0) -> None:
        self._answers = [exposed, pending]
        self.reads = 0

    def execute(self, sql: str, params: object = ()) -> object:
        assert "SELECT count(*)" in sql
        assert params == (MAIN_ID, "p90l90_legacy_sub_dot_arb_apt", VERSION)
        result = self._answers[self.reads]
        self.reads += 1

        class _Row:
            def fetchone(self):
                return (result,)

        return _Row()


def _recovered_real(*, db: _CountDB) -> P90L90ObserverRealConsumer:
    consumer = object.__new__(P90L90ObserverRealConsumer)
    consumer._connection = db
    consumer._recovered = True
    consumer._gap_recovery_poisoned = False
    consumer._processors = {"LTCUSDT": object(), "DOTUSDT": object()}
    consumer._verified_closed_watermarks = {
        "LTCUSDT": (AT, AT),
        "DOTUSDT": (AT, AT),
    }
    return consumer


def test_real_exact_two_symbol_zero_gap_needs_no_ohlc_paper_replay() -> None:
    db = _CountDB()
    consumer = _recovered_real(db=db)
    targets = dict(consumer._verified_closed_watermarks)
    assert consumer.catch_up_closed_histories(targets) == 0
    assert db.reads == 2
    assert consumer._gap_recovery_poisoned is False


@pytest.mark.parametrize("case", ("future", "past", "extra", "missing", "naive"))
def test_real_candle_gap_does_not_look_like_proven_native_history(
    case: str,
) -> None:
    from datetime import timedelta

    consumer = _recovered_real(db=_CountDB())
    targets = dict(consumer._verified_closed_watermarks)
    if case == "future":
        targets["LTCUSDT"] = (AT + timedelta(minutes=5), AT)
    elif case == "past":
        targets["LTCUSDT"] = (AT - timedelta(minutes=5), AT)
    elif case == "extra":
        targets["BTCUSDT"] = (AT, AT)
    elif case == "missing":
        del targets["DOTUSDT"]
    else:
        targets["LTCUSDT"] = (AT.replace(tzinfo=None), AT)
    with pytest.raises(RuntimeError, match="GAP_REQUIRES_EXACT_SOURCE_EPOCH"):
        consumer.catch_up_closed_histories(targets)


@pytest.mark.parametrize(("exposed", "pending"), ((1, 0), (0, 1)))
def test_real_restart_with_open_position_or_unowned_command_cannot_fast_forward(
    exposed: int, pending: int,
) -> None:
    consumer = _recovered_real(db=_CountDB(exposed=exposed, pending=pending))
    with pytest.raises(RuntimeError, match="GAP_WITH_EXPOSURE_OR_PENDING_ORDER"):
        consumer.catch_up_closed_histories(
            dict(consumer._verified_closed_watermarks)
        )
