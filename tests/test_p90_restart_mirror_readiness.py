"""A subscribe ACK is not yet a complete five-symbol native cursor set."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry import p90l90_real_restart_recovery as module
from bybit_workbench.universal_entry.p90l90_observer_real_consumer import (
    P90L90ObserverRealConsumer,
)
from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from bybit_workbench.universal_exit.p90l90_real_runtime_ports import P90RealExitRuntimePorts

AT = datetime(2026, 11, 5, 16, tzinfo=UTC)
SYMBOLS = ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")


def _mirror():
    mirror = PublicTradeMirrorBuffer(SYMBOLS, retention_seconds=600)
    mirror.start_epoch(AT)
    return mirror


def _append(mirror, symbol):
    mirror.record_message({"topic": "publicTrade." + symbol, "data": [{
        "s": symbol, "i": "native-" + symbol, "seq": 1,
        "T": int(AT.timestamp() * 1000), "p": "100", "v": ".1", "S": "Buy",
    }]}, received_at=AT)


def _clock(monkeypatch, advance):
    clock = {"now": 0.0, "sleeps": 0}

    def sleep(delay):
        assert 0 < delay <= .1
        clock["now"] += delay
        clock["sleeps"] += 1
        advance(clock["sleeps"])

    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: clock["now"], sleep=sleep,
    ), raising=False)
    return clock


def _wait(mirror):
    wait = getattr(module, "_wait_for_live_mirror_cursors", None)
    assert wait is not None, "restart must wait for all five live cursors"
    return wait(mirror, mirror_serial=1, mirror_started_at=AT)


def test_later_symbol_ticks_complete_same_live_session(monkeypatch):
    mirror = _mirror()
    _append(mirror, SYMBOLS[0])
    clock = _clock(monkeypatch, lambda n: _append(mirror, SYMBOLS[n]))
    _wait(mirror)
    assert clock["sleeps"] == 4
    assert set(mirror.current_cursors()[0]) == set(SYMBOLS)


def test_complete_mirror_never_waits(monkeypatch):
    mirror = _mirror()
    for symbol in SYMBOLS:
        _append(mirror, symbol)
    clock = _clock(monkeypatch, lambda _: pytest.fail("unexpected wait"))
    _wait(mirror)
    assert clock["sleeps"] == 0


def test_partial_mirror_has_finite_budget_and_cannot_fabricate_missing_cursors(monkeypatch):
    mirror = _mirror()
    _append(mirror, SYMBOLS[0])
    clock = _clock(monkeypatch, lambda _: None)
    with pytest.raises(module.P90RealOpenSourceRecoveryUnverified, match="CURSORS_MISSING"):
        _wait(mirror)
    assert 0 < clock["now"] <= 10.001
    assert set(mirror.current_cursors()[0]) == {SYMBOLS[0]}


@pytest.mark.parametrize("change", ["gap", "new_epoch"])
def test_session_loss_during_wait_fails_without_accepting_new_session(monkeypatch, change):
    mirror = _mirror()
    _append(mirror, SYMBOLS[0])

    def advance(_):
        if change == "gap":
            mirror.mark_gap("test disconnect")
        else:
            mirror.start_epoch(AT)

    clock = _clock(monkeypatch, advance)
    with pytest.raises(module.P90RealOpenSourceRecoveryUnverified, match="SESSION_UNVERIFIED"):
        _wait(mirror)
    assert clock["sleeps"] == 1


def test_actual_recovery_waits_before_opening_source_transaction(monkeypatch):
    mirror = _mirror()
    _append(mirror, SYMBOLS[0])
    _clock(monkeypatch, lambda n: _append(mirror, SYMBOLS[n]))

    class TransactionReached(Exception):
        pass

    class DB:
        def transaction(self):
            assert set(mirror.current_cursors()[0]) == set(SYMBOLS)
            raise TransactionReached

    with pytest.raises(TransactionReached):
        module.recover_p90_real_open_source(
            DB(), bundles=(), source_commit="a" * 40,
            source_epoch_id="p90-real-epoch-" + "b" * 40,
            consumer=object.__new__(P90L90ObserverRealConsumer),
            exit_ports=object.__new__(P90RealExitRuntimePorts),
            mirror=mirror, mirror_serial=1, mirror_started_at=AT, source_ws_ready_at=AT,
            fetch_recent_trades=lambda _: pytest.fail("no REST before readiness"),
            fetch_closed_candles=lambda *_: pytest.fail("no candles before readiness"),
            bind_restart_bridge=lambda _: pytest.fail("no bridge before proof"),
        )
