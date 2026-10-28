"""Synthetic REAL native MFE checkpoint: exact signed-fill lineage and restart."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import TradeDirection
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.transport_continuity import TradeCursor
from bybit_workbench.universal_exit.p90l90_real_mfe import (
    P90RealMfeCheckpoint,
    P90RealMfeUnverified,
)
from bybit_workbench.universal_exit.p90l90_real_mfe_ledger import P90RealMfeLedger

D = Decimal
AT = datetime(2026, 10, 29, 2, 0, tzinfo=UTC)
SHA = "a" * 40


def _position(side: TradeDirection = TradeDirection.LONG, *, sub: bool = False) -> StrategyPosition:
    strategy_id = SUB_ID if sub else MAIN_ID
    account_id = "ta-sub" if sub else "ta-main"
    symbol = "DOTUSDT" if sub else "LTCUSDT"
    return StrategyPosition(
        strategy_position_id="p90-real-position", account_ref="BYBIT:UNIFIED",
        exchange_position_key=exchange_position_key(account_id, symbol, 0),
        position_idx=0, strategy_id=strategy_id, strategy_version=VERSION,
        strategy_config_fingerprint="strategy-immutable-fp",
        strategy_activation_id="activation-exact",
        signal_id="entry-signal", strategy_attempt_id="entry-attempt",
        entry_decision_id="entry-decision",
        entry_execution_request_id="entry-request",
        entry_plan_fingerprint="entry-fp",
        exit_plan_fingerprint="exit-fp",
        entry_command_id="entry-command",
        symbol=symbol, direction=side,
        actual_avg_fill=D("100"), actual_qty=D("0.1"),
        fill_at=AT,
        initial_protection_confirmed_at=AT + timedelta(milliseconds=2),
        strategy_execution_binding_id="binding-exact",
        execution_leg="PRIMARY", trading_account_id=account_id,
        identity_status="PROVEN",
    )


def _cursor(
    position: StrategyPosition, *,
    seq: int = 100, exec_id: str = "first", millis: int = 3,
) -> TradeCursor:
    return TradeCursor(
        symbol=position.symbol, seq=seq, exec_id=exec_id,
        traded_at=AT + timedelta(milliseconds=millis),
    )


@pytest.mark.parametrize(("side","sub","one","two","expected"), (
    (TradeDirection.LONG,False,"100.3","100.9","100.9"),
    (TradeDirection.SHORT,True,"99.7","99.1","99.1"),
))
def test_actual_fill_native_mfe_roundtrip_and_monotone_best(
    side: TradeDirection, sub: bool, one: str, two: str, expected: str,
) -> None:
    pos = _position(side, sub=sub)
    start = P90RealMfeCheckpoint.begin(
        position=pos, source_commit=SHA, first_cursor=_cursor(pos),
        first_price=D(one), verified_fill_to_first_trade_coverage=True,
    )
    assert start.last_native_price == D(one)
    assert P90RealMfeCheckpoint.restore(start.snapshot()) == start
    next_cursor = _cursor(pos, seq=101, exec_id="second", millis=5)
    next_state = start.advance(
        position=pos, cursor=next_cursor, price=D(two),
        proven_contiguous_native_cursor=True,
    )
    assert next_state.best_favorable_price == D(expected)
    assert P90RealMfeCheckpoint.restore(next_state.snapshot()) == next_state
    assert start.cursor_exec_id == "first"
    assert next_state.advance(
        position=pos, cursor=next_cursor, price=D(two),
        proven_contiguous_native_cursor=True,
    ) is next_state


@pytest.mark.parametrize("flag", (False, None, "yes"))
def test_no_fill_forward_continuity_never_seeds_arbitrary_first_tick(
    flag: object,
) -> None:
    pos = _position()
    with pytest.raises(P90RealMfeUnverified, match="FILL_TO_NATIVE_GAP"):
        P90RealMfeCheckpoint.begin(
            position=pos, source_commit=SHA, first_cursor=_cursor(pos),
            first_price=D("100.2"),
            verified_fill_to_first_trade_coverage=flag,  # type: ignore[arg-type]
        )


def test_broken_exchange_protection_and_cross_account_are_rejected() -> None:
    pos = _position()
    broken = replace(pos, initial_protection_confirmed_at=None)
    with pytest.raises(P90RealMfeUnverified, match="PROTECTION_UNPROVEN"):
        P90RealMfeCheckpoint.begin(
            position=broken, source_commit=SHA, first_cursor=_cursor(pos),
            first_price=D("100.2"), verified_fill_to_first_trade_coverage=True,
        )
    bad_key = replace(pos, exchange_position_key="BYBIT:UNIFIED:LINEAR:USDT:LTCUSDT:0")
    with pytest.raises(P90RealMfeUnverified, match="PROTECTION_UNPROVEN"):
        P90RealMfeCheckpoint.begin(
            position=bad_key, source_commit=SHA, first_cursor=_cursor(pos),
            first_price=D("100.2"), verified_fill_to_first_trade_coverage=True,
        )


def test_after_fill_replay_never_rewinds_sequence_or_uses_same_time_ambiguity() -> None:
    pos = _position()
    state = P90RealMfeCheckpoint.begin(
        position=pos, source_commit=SHA, first_cursor=_cursor(pos),
        first_price=D("100.1"), verified_fill_to_first_trade_coverage=True,
    )
    for cursor in (
        _cursor(pos, seq=99, exec_id="old", millis=4),
        _cursor(pos, seq=100, exec_id="parallel", millis=3),
        _cursor(pos, seq=100, exec_id="late", millis=2),
    ):
        with pytest.raises(P90RealMfeUnverified, match="ORDER_NOT_PROVEN"):
            state.advance(
                position=pos, cursor=cursor, price=D("101"),
                proven_contiguous_native_cursor=True,
            )
    with pytest.raises(P90RealMfeUnverified, match="NATIVE_CURSOR_GAP"):
        state.advance(
            position=pos, cursor=_cursor(pos, seq=101, millis=5),
            price=D("101"), proven_contiguous_native_cursor=False,
        )


def test_tampered_restored_checkpoint_and_wrong_account_fail_closed() -> None:
    pos = _position()
    state = P90RealMfeCheckpoint.begin(
        position=pos, source_commit=SHA, first_cursor=_cursor(pos),
        first_price=D("100.1"), verified_fill_to_first_trade_coverage=True,
    )
    snapshot = state.snapshot()
    tampered = dict(snapshot)
    tampered["account_id"] = "ta-sub"
    with pytest.raises(P90RealMfeUnverified, match="SNAPSHOT_IDENTITY"):
        P90RealMfeCheckpoint.restore(tampered)
    tampered = dict(snapshot)
    tampered["cursor_seq"] = True
    with pytest.raises(P90RealMfeUnverified, match="SNAPSHOT_CURSOR"):
        P90RealMfeCheckpoint.restore(tampered)
    with pytest.raises(P90RealMfeUnverified, match="ACCOUNT_DRIFT"):
        state.advance(
            position=_position(sub=True),
            cursor=_cursor(pos, seq=101, millis=5),
            price=D("101"), proven_contiguous_native_cursor=True,
        )


class _Row:
    def __init__(self, item: dict[str, object] | None = None) -> None:
        self.item = item

    def fetchone(self) -> dict[str, object] | None:
        return self.item


class _MfeDB:
    def __init__(self) -> None:
        self.saved: dict[str, dict[str, object]] = {}
        self.locked = False
        self.audit: list[str] = []

    def execute(
        self, sql: str, params: tuple[object, ...] = (),
    ) -> _Row:
        if "pg_advisory_xact_lock" in sql:
            self.locked = True
            self.audit.append("lock")
            return _Row()
        assert self.locked, "the native MFE reader/writer must own the exact lock"
        if "ORDER BY" in sql and "exit_observations" in sql:
            self.audit.append("read")
            matching = [
                row for row in self.saved.values()
                if row["strategy_position_id"] == params[0]
                and row["exit_plan_fingerprint"] == params[1]
            ]
            if not matching:
                return _Row()
            latest = max(matching, key=lambda row: (
                row["attributes"]["checkpoint"]["cursor_seq"],
                row["event_at"],
            ))
            return _Row(latest)
        if "INSERT INTO strategy_exit.exit_observations" in sql:
            self.audit.append("write")
            from bybit_workbench.universal_entry.contracts import FrozenPolicy

            self.saved.setdefault(str(params[0]), {
                "observation_id": params[0],
                "strategy_position_id": params[1],
                "exit_plan_fingerprint": params[2],
                "symbol": params[3],
                "event_at": params[5],
                "attributes": FrozenPolicy(str(params[8])).to_dict(),
                "source_refs": list(json.loads(str(params[9]))),
            })
            return _Row()
        if "WHERE observation_id=%s" in sql:
            self.audit.append("readback")
            return _Row(self.saved.get(str(params[0])))
        raise AssertionError(f"unexpected MFE DB command: {sql}")


def test_atomic_native_ledger_restores_exact_mfe_after_restart() -> None:
    pos = _position()
    db = _MfeDB()
    first = _cursor(pos, seq=100, exec_id="first", millis=3)
    ledger = P90RealMfeLedger(db)
    args = {
        "position": pos, "source_commit": SHA, "fact_id": "fact-first",
        "cursor": first, "price": D("100.4"),
        "received_at": first.traded_at + timedelta(milliseconds=1),
        "source_refs": ("bybit:publicTrade:LTCUSDT:first",),
        "first_fill_forward_coverage_proven": True,
        "native_continuity_proven": True,
    }
    state = ledger.on_trade(**args)
    assert state.best_favorable_price == D("100.4")
    assert len(db.saved) == 1
    assert db.audit[:4] == ["lock", "read", "write", "readback"]
    assert P90RealMfeLedger(db).on_trade(**args) == state
    assert len(db.saved) == 1
    later = _cursor(pos, seq=101, exec_id="next", millis=4)
    state2 = P90RealMfeLedger(db).on_trade(
        **{
            **args, "fact_id": "fact-next", "cursor": later,
            "price": D("101.2"),
            "received_at": later.traded_at + timedelta(milliseconds=1),
            "source_refs": ("bybit:publicTrade:LTCUSDT:next",),
            "first_fill_forward_coverage_proven": False,
        }
    )
    assert state2.best_favorable_price == D("101.2")
    assert state2.cursor_seq == 101
    assert len(db.saved) == 2
    assert P90RealMfeCheckpoint.restore(state2.snapshot()) == state2
    # Duplicate cursor with different original native fact IDs fails closed.
    with pytest.raises(P90RealMfeUnverified, match="REPLAY_PROVENANCE_DRIFT"):
        P90RealMfeLedger(db).on_trade(
            **{**args, "fact_id": "tampered", "cursor": later,
               "price": D("101.2"),
               "received_at": later.traded_at + timedelta(milliseconds=1)}
        )


def test_real_mfe_ledger_fails_closed_before_unproven_fill_forward_seed() -> None:
    pos = _position()
    db = _MfeDB()
    cursor = _cursor(pos)
    with pytest.raises(P90RealMfeUnverified, match="FILL_TO_NATIVE_GAP"):
        P90RealMfeLedger(db).on_trade(
            position=pos, source_commit=SHA, fact_id="native",
            cursor=cursor, price=D("100.5"),
            received_at=cursor.traded_at + timedelta(milliseconds=1),
            source_refs=("native:exact",),
            first_fill_forward_coverage_proven=False,
            native_continuity_proven=True,
        )
    assert db.saved == {}


def test_real_5m_exit_recovers_latest_signed_scope_mfe_without_reseeding() -> None:
    pos = _position()
    db = _MfeDB()
    ledger = P90RealMfeLedger(db)
    first = _cursor(pos, seq=101, exec_id="native-first", millis=3)
    args = {
        "position": pos, "source_commit": SHA,
        "fact_id": "native-fact", "cursor": first,
        "price": D("100.8"),
        "received_at": first.traded_at + timedelta(milliseconds=1),
        "source_refs": ("bybit:publicTrade:proof-1",),
        "first_fill_forward_coverage_proven": True,
        "native_continuity_proven": True,
    }
    committed = ledger.on_trade(**args)
    restored, native_fact_id, refs = P90RealMfeLedger(db).load_latest(
        position=pos, source_commit=SHA,
    )
    assert restored == committed
    assert native_fact_id == "native-fact"
    assert refs == ("bybit:publicTrade:proof-1",)
    assert db.audit[-2:] == ["lock", "read"]


def test_real_5m_exit_rejects_missing_or_drifted_mfe_ledger() -> None:
    pos = _position()
    db = _MfeDB()
    with pytest.raises(P90RealMfeUnverified, match="STATE_MISSING"):
        P90RealMfeLedger(db).load_latest(position=pos, source_commit=SHA)
    cursor = _cursor(pos)
    ledger = P90RealMfeLedger(db)
    ledger.on_trade(
        position=pos, source_commit=SHA,
        fact_id="native", cursor=cursor, price=D("100.2"),
        received_at=cursor.traded_at + timedelta(milliseconds=1),
        source_refs=("native:original",),
        first_fill_forward_coverage_proven=True,
        native_continuity_proven=True,
    )
    with pytest.raises(P90RealMfeUnverified, match="SOURCE_RELEASE_DRIFT"):
        ledger.load_latest(position=pos, source_commit="b" * 40)
    with pytest.raises(P90RealMfeUnverified, match="ACCOUNT_DRIFT"):
        ledger.load_latest(position=_position(sub=True), source_commit=SHA)
