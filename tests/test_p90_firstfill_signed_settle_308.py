"""Regression: first-fill signed protection and R06 closed-5m settlement (#308).

No Exchange POST, financial grant, trading parameter or DB mutation.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bybit_workbench.market_data.entry_publication_reader import EntryPublicationPin
from operations.connectivity import p90_r06_exit_owner as continuous
from operations.connectivity import p90_real_independent_exit as firstfill

BAR = datetime(2026, 11, 19, 0, 15, tzinfo=UTC)
ACCOUNT = "ta-" + "1" * 32
STRATEGY = "p90l90_legacy_main_ltc_inj"


def _position(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "LTCUSDT",
        "side": "Buy",
        "positionIdx": 0,
        "size": "0.2",
        "avgPrice": "43.34",
        "stopLoss": "39.01",
        "takeProfit": "43.98",
        "trailingStop": "0",
        "markPrice": "43.80",
        "unrealisedPnl": "0.092",
        "positionIMByMp": "8.668",
    }
    base.update(changes)
    return base


def test_firstfill_recheck_ignores_only_volatile_bybit_market_telemetry() -> None:
    original = firstfill._firstfill_signed_protection_snapshot(_position())
    moving = firstfill._firstfill_signed_protection_snapshot(_position(
        markPrice="43.89", unrealisedPnl="0.110", positionIMByMp="8.640",
    ))
    assert original is not None
    assert moving == original


@pytest.mark.parametrize("change", [
    {"side": "Sell"},
    {"positionIdx": 1},
    {"size": "0.3"},
    {"avgPrice": "43.35"},
    {"stopLoss": "0"},
    {"takeProfit": "44.00"},
    {"trailingStop": "0.15"},
    {"symbol": "INJUSDT"},
])
def test_firstfill_recheck_still_rejects_changed_protection(
    change: dict[str, object],
) -> None:
    baseline = firstfill._firstfill_signed_protection_snapshot(_position())
    altered = firstfill._firstfill_signed_protection_snapshot(_position(**change))
    assert baseline is not None and altered != baseline


def test_firstfill_rejects_missing_or_untyped_signed_snapshot() -> None:
    incomplete = _position()
    del incomplete["stopLoss"]
    assert firstfill._firstfill_signed_protection_snapshot(incomplete) is None
    assert firstfill._firstfill_signed_protection_snapshot(None) is None
    assert firstfill._firstfill_signed_protection_snapshot([]) is None


def _pin(*, age_seconds: int, close: datetime = BAR) -> EntryPublicationPin:
    return EntryPublicationPin(
        publication_id=101,
        source_session_id="a" * 32,
        producer_release_commit="b" * 40,
        as_of_second=close + timedelta(seconds=age_seconds),
        native_watermarks={},
        closed_refs={"LTCUSDT": {"5": close.isoformat()}},
    )


@pytest.mark.parametrize(("seconds", "settling"), [
    (0, True), (5, True), (10, True), (14, True),
    (15, False), (16, False), (30, False), (-1, False),
])
def test_5m_settling_is_bounded_to_15_actual_seconds(
    seconds: int, settling: bool,
) -> None:
    assert continuous._pinned_closed_5m_is_settling(
        _pin(age_seconds=seconds), "LTCUSDT"
    ) is settling


def test_malformed_or_other_symbol_cannot_be_called_settling() -> None:
    assert not continuous._pinned_closed_5m_is_settling(
        _pin(age_seconds=10), "INJUSDT"
    )
    raw = _pin(age_seconds=10)
    raw.closed_refs["LTCUSDT"]["5"] = "invalid"
    assert not continuous._pinned_closed_5m_is_settling(raw, "LTCUSDT")


class _ReadOnlyConnection:
    def transaction(self):
        return nullcontext()

    def execute(self, query: str):
        assert query == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        return None


def test_source_snapshot_does_not_read_unsettled_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(continuous, "_exit_native_ttls",
                        lambda: {"LTCUSDT": timedelta(seconds=120)})
    monkeypatch.setattr(
        continuous, "read_scoped_native_source",
        lambda *_a, **_kw: SimpleNamespace(
            ready=True, session_id="a" * 32,
            producer_release_commit="b" * 40,
        ),
    )
    monkeypatch.setattr(
        continuous, "read_entry_publication_pin",
        lambda *_a, **_kw: _pin(age_seconds=10),
    )
    monkeypatch.setattr(
        continuous, "read_entry_publication_seed",
        lambda *_a, **_kw: pytest.fail("unsettled seed must not be consumed"),
    )
    with pytest.raises(
        continuous.P90R06ExitBlocked,
        match="P90_R06_EXIT_CLOSED_5M_SETTLING",
    ):
        continuous._source_snapshot(
            _ReadOnlyConnection(),
            scope=SimpleNamespace(
                identity=SimpleNamespace(approved_producers=frozenset(("b" * 40,)))
            ),
            position=SimpleNamespace(symbol="LTCUSDT"),
            latest=None,
            causal_boundary=BAR,
            now=BAR + timedelta(seconds=10),
        )


def _cycle(monkeypatch: pytest.MonkeyPatch, *, reason: str) -> str:
    position = SimpleNamespace(
        strategy_position_id="p", strategy_id=STRATEGY,
        trading_account_id=ACCOUNT, symbol="LTCUSDT",
        fill_at=BAR - timedelta(minutes=3),
    )
    monkeypatch.setattr(
        continuous, "_runtime_scope",
        lambda *_a, **_kw: SimpleNamespace(
            identity=SimpleNamespace(origin_commit="a" * 40),
            routes=SimpleNamespace(account_by_strategy={STRATEGY: ACCOUNT}),
        ),
    )
    monkeypatch.setattr(
        continuous, "_open_positions", lambda *_a, **_kw: (position,)
    )
    monkeypatch.setattr(
        continuous, "lock_p90_exit_account", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(continuous, "_refresh_exit_claim", lambda *_a, **_kw: None)
    monkeypatch.setattr(continuous, "_latest_mfe", lambda *_a, **_kw: None)
    monkeypatch.setattr(continuous, "_last_closed_boundary", lambda *_a, **_kw: None)

    def fail_snapshot(*_args: object, **_kw: object):
        raise continuous.P90R06ExitBlocked(reason)

    monkeypatch.setattr(continuous, "_source_snapshot", fail_snapshot)
    monkeypatch.setattr(
        continuous, "_append_mfe",
        lambda *_a, **_kw: pytest.fail("no MFE until settled source"),
    )
    return continuous.run_p90_r06_exit_once(
        object(), object(), loaded_commit="b" * 40, now=BAR,
    )


def test_exact_settling_only_keeps_continuous_owner_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _cycle(monkeypatch, reason="P90_R06_EXIT_CLOSED_5M_SETTLING")
    assert result == (
        "P90_R06_EXIT_OWNER_RUNNING|"
        "P90_R06_EXIT_CLOSED_5M_SETTLING:LTCUSDT"
    )


def test_other_source_mismatch_still_blocks_continuous_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(
        continuous.P90R06ExitBlocked,
        match="R06_ENTRY_SEED_PUBLICATION_MISMATCH",
    ):
        _cycle(monkeypatch, reason="R06_ENTRY_SEED_PUBLICATION_MISMATCH")


def test_existing_firstfill_15s_expiry_and_signed_exchange_proof_unchanged() -> None:
    assert firstfill._FIRST_FILL_WINDOW == timedelta(seconds=15)
    assert firstfill._FIRSTFILL_STABLE_POSITION_FIELDS == (
        "symbol", "side", "positionIdx", "size", "avgPrice",
        "stopLoss", "takeProfit", "trailingStop",
    )
