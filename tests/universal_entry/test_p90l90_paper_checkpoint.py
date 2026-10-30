"""No-order P90L90 paper Exit restart and account-isolation contracts."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalExit
from bybit_workbench.universal_entry.p90l90_paper_checkpoint import (
    P90L90PaperExitCheckpoint,
)

D = Decimal
T0 = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)


def _checkpoint(account: str = "MAIN", position: str = "pos-main") -> P90L90PaperExitCheckpoint:
    return P90L90PaperExitCheckpoint(
        trading_account_id=account,
        strategy_id="p90l90_legacy_main_ltc_inj" if account == "MAIN"
        else "p90l90_legacy_sub_dot_arb_apt",
        paper_position_id=position,
        symbol="LTCUSDT",
        exit_state=P90L90CausalExit(
            side="LONG", entry_price=D("100"), entry_sequence=0,
            state_bucket=int(T0.timestamp() // 300),
        ),
    )


def _restore(cp: P90L90PaperExitCheckpoint) -> P90L90PaperExitCheckpoint:
    return P90L90PaperExitCheckpoint.restore(
        cp.snapshot(),
        trading_account_id=cp.trading_account_id,
        strategy_id=cp.strategy_id,
        paper_position_id=cp.paper_position_id,
        symbol=cp.symbol,
        side="LONG",
        entry_price=D("100"),
    )


def _tick(
    cp: P90L90PaperExitCheckpoint,
    exec_id: str,
    seq: int,
    price: str,
    *,
    at: datetime = T0 + timedelta(seconds=1),
    target: str | None = None,
    geometry_at: datetime | None = None,
):
    return cp.on_native_trade(
        exec_id=exec_id,
        exchange_sequence=seq,
        event_at=at,
        price=D(price),
        geometry_effective_at=geometry_at,
        candidate_opposite_raw_tp=None if target is None else D(target),
    )


def test_checkpoint_restart_preserves_trail_and_no_same_tick_stop() -> None:
    uninterrupted = _checkpoint()
    restarted = _checkpoint()
    for cp in (uninterrupted, restarted):
        accepted, closed = _tick(cp, "exec-1", 100, "100.6")
        assert accepted and closed is None
        assert cp.exit_state.stop == D("100.25")
        assert cp.exit_state.protected
    restarted = _restore(restarted)
    assert _tick(restarted, "exec-1", 100, "100.6") == (False, None)
    for cp in (uninterrupted, restarted):
        accepted, closed = _tick(
            cp, "exec-2", 101, "100.2", at=T0 + timedelta(seconds=2)
        )
        assert accepted and closed is not None
        assert closed.reason == "PROTECTED_STOP"
        assert closed.modeled_price == D("100.2")
    assert uninterrupted.snapshot() == restarted.snapshot()


def test_resting_raw_tp_survives_restart_and_models_maker_only_after_reprice() -> None:
    cp = _checkpoint()
    _tick(cp, "exec-1", 100, "100.6")
    accepted, closed = _tick(
        cp, "exec-2", 101, "100.7",
        at=T0 + timedelta(minutes=5, seconds=1),
        target="101", geometry_at=T0 + timedelta(minutes=5),
    )
    assert accepted and closed is None
    assert cp.exit_state.target == D("101")
    cp = _restore(cp)
    accepted, closed = _tick(
        cp, "exec-3", 102, "101.2",
        at=T0 + timedelta(minutes=5, seconds=2),
    )
    assert accepted and closed is not None
    assert closed.reason == "RESTING_TP"
    assert closed.resting_maker_proxy is True
    assert closed.modeled_price == D("101")


def test_same_exchange_sequence_multiple_ids_and_restart_dedupe() -> None:
    cp = _checkpoint()
    _tick(cp, "exec-a", 7, "100.1")
    _tick(cp, "exec-b", 7, "100.2", at=T0 + timedelta(seconds=2))
    cp = _restore(cp)
    assert _tick(cp, "exec-a", 7, "100.1") == (False, None)
    assert _tick(cp, "exec-b", 7, "100.2") == (False, None)
    assert cp.exit_state.last_sequence == 2
    with pytest.raises(ValueError, match="sequence regressed"):
        _tick(cp, "exec-old", 6, "100.1")
    with pytest.raises(ValueError, match="event time regressed"):
        _tick(cp, "exec-c", 7, "100.1", at=T0 + timedelta(seconds=1))


def test_account_position_identity_is_never_inferred_or_shared() -> None:
    main = _checkpoint()
    sub = _checkpoint("SUB", "pos-sub")
    _tick(main, "main-trade", 1, "100.6")
    assert sub.exit_state.stop == D("90")
    assert sub.exit_state.last_sequence == 0
    with pytest.raises(ValueError, match="trading_account_id mismatch"):
        P90L90PaperExitCheckpoint.restore(
            main.snapshot(),
            trading_account_id="SUB",
            strategy_id=main.strategy_id,
            paper_position_id=main.paper_position_id,
            symbol=main.symbol,
            side="LONG",
            entry_price=D("100"),
        )
    assert _restore(sub).snapshot() == sub.snapshot()


def test_missing_or_stale_geometry_fails_closed_without_state_mutation() -> None:
    cp = _checkpoint()
    before = cp.snapshot()
    with pytest.raises(ValueError, match="missing causal closed"):
        _tick(
            cp, "exec-a", 1, "100.2",
            at=T0 + timedelta(minutes=5, seconds=1),
        )
    assert cp.snapshot() == before
    with pytest.raises(ValueError, match="stale/future"):
        _tick(
            cp, "exec-a", 1, "100.2",
            at=T0 + timedelta(minutes=5, seconds=1),
            geometry_at=T0, target="101",
        )
    assert cp.snapshot() == before


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(schema="UNKNOWN"),
    lambda d: d.update(trading_account_id="SUB"),
    lambda d: d["exit"].update(stop="NaN"),
    lambda d: d["exit"].update(stop="90.1"),
    lambda d: d["exit"].update(best_favorable_pct="0.6"),
    lambda d: d["exit"].update(last_sequence=-1),
    lambda d: d["exit"].update(target="101"),
    lambda d: d.update(last_exchange_sequence=42),
    lambda d: d.update(seen_exec_ids=["duplicate", "duplicate"]),
])
def test_corrupt_checkpoint_rejected(mutation) -> None:
    cp = _checkpoint()
    payload = deepcopy(cp.snapshot())
    mutation(payload)
    with pytest.raises(ValueError):
        P90L90PaperExitCheckpoint.restore(
            payload,
            trading_account_id=cp.trading_account_id,
            strategy_id=cp.strategy_id,
            paper_position_id=cp.paper_position_id,
            symbol=cp.symbol,
            side="LONG",
            entry_price=D("100"),
        )
