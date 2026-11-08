"""P90 real native MFE catch-up must not apply *current decision* age to history.

Original mirror continuity, source transaction and exact sequential execution
IDs are still required. No synthetic Bybit evidence or Exchange POST.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_p90l90_real_live_source_ports import AT, SHA, StubDB, _ports, _real_mirror, _trade
from test_p90l90_real_observation import _scope

from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.transport_continuity import TradeCursor
from bybit_workbench.universal_exit.contracts import ExitEvaluationStatus
from bybit_workbench.universal_exit.p90l90_real_exit_port import P90RealExitNativePort
from bybit_workbench.universal_exit.p90l90_real_mfe import P90RealMfeCheckpoint
from operations.monitoring import p90l90_real_live_source_ports as source_module

NOW = AT + timedelta(seconds=20, milliseconds=8)


def _original_source(monkeypatch):
    position, plan, _previous, old, private, _price = _scope()
    current = TradeCursor(
        symbol=old.symbol, seq=old.seq + 1,
        exec_id="actual-original-fresh-native-102",
        traded_at=AT + timedelta(seconds=20),
    )
    db = StubDB()
    mirror = _real_mirror()
    _trade(mirror, old)
    _trade(mirror, current)
    monkeypatch.setattr(
        source_module, "datetime", SimpleNamespace(now=lambda _tz: NOW),
    )
    return position, plan, old, current, replace(
        private, observed_at=NOW,
    ), db, mirror, _ports(db, mirror)


def test_old_prefix_has_independent_source_proof_but_cannot_be_current(monkeypatch):
    position, _plan, old, _current, _private, _db, _mirror, source = (
        _original_source(monkeypatch)
    )
    assert source.native_historical_coverage(position, old) == (True, True)
    with pytest.raises(
        source_module.P90RealLiveSourceUnverified,
        match="P90_REAL_LIVE_SOURCE_NATIVE_EVENT_STALE",
    ):
        source.native_coverage(position, old)


def test_historical_source_proof_does_not_accept_foreign_or_gapped_tick(
    monkeypatch,
):
    position, _plan, old, _current, _private, _db, mirror, source = (
        _original_source(monkeypatch)
    )
    fake = replace(old, exec_id="not-in-original-mirror")
    with pytest.raises(source_module.P90RealLiveSourceUnverified):
        source.native_historical_coverage(position, fake)
    mirror.mark_gap("source identity lost")
    with pytest.raises(source_module.P90RealLiveSourceUnverified):
        source.native_historical_coverage(position, old)


def test_historical_prefix_mfe_then_only_fresh_current_exit_decision(
    monkeypatch,
):
    position, plan, old, current, private, db, _mirror, source = (
        _original_source(monkeypatch)
    )
    seen: list[str] = []
    evaluations: list[object] = []

    class Ledger:
        def on_trade(self, **kwargs):
            cursor = kwargs["cursor"]
            seen.append(cursor.exec_id)
            checkpoint = P90RealMfeCheckpoint.begin(
                position=position, source_commit=SHA, first_cursor=cursor,
                first_price=Decimal("100.2"),
                verified_fill_to_first_trade_coverage=True,
            )
            db.latest = {"attributes": {"checkpoint": checkpoint.snapshot()}}
            return checkpoint

    class Store:
        def record(self, result, **_kwargs):
            evaluations.append(result)

    callback = P90RealExitNativePort(
        position_provider=lambda _sym: ((position, plan),),
        independently_verified_source_coverage=source.native_coverage,
        verified_historical_prefix_coverage=source.native_historical_coverage,
        independently_signed_private_evidence=lambda _pos, _cur: private,
        source_commit=SHA,
        mfe_ledger=Ledger(),
        exit_store=Store(),
        verified_native_prefix=source.native_prefix,
    )
    callback._engine = SimpleNamespace(
        evaluate=lambda *_args: SimpleNamespace(
            status=ExitEvaluationStatus.NO_MATCH, decision=None,
        ),
    )
    fact = MarketFactEnvelope(
        fact_id="trade-" + current.exec_id,
        event_kind="PUBLIC_TRADE", symbol=position.symbol,
        event_at=current.traded_at, observed_at=current.traded_at,
        received_at=current.traded_at + timedelta(milliseconds=1),
        source_refs=("bybit:publicTrade:LTCUSDT:" + current.exec_id,),
        attributes=FrozenPolicy.from_mapping({"price":"100.2"}),
    )
    result = callback(fact, current)
    assert [item for item in seen] == [old.exec_id, current.exec_id]
    assert len(result) == len(evaluations) == 1


def test_unverified_historical_prefix_callback_is_never_assumed_true():
    position, plan, state, _old, private, _price = _scope()
    with pytest.raises(ValueError, match="HISTORICAL"):
        P90RealExitNativePort(
            position_provider=lambda _sym: ((position, plan),),
            independently_verified_source_coverage=lambda _p, _c: (True, True),
            independently_signed_private_evidence=lambda _p, _c: private,
            source_commit=SHA,
            mfe_ledger=SimpleNamespace(on_trade=lambda **_kw: state),
            exit_store=SimpleNamespace(record=lambda *_a, **_kw: None),
            verified_native_prefix=lambda _p, _c: (),
        )
