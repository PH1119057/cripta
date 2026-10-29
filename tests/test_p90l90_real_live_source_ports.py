"""Source-level negative tests for the exact P90 REAL native mirror port.

No Exchange POST, fake signed account, or LIVE-arm. The production adapter
must deny stale/restored/forked original market source and missing prior MFE.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_p90l90_real_observation import AT, _scope

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.p90l90_real_shared_candle_ledger import (
    P90RealSharedCandleLedger,
)
from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from bybit_workbench.universal_entry.transport_continuity import TradeCursor
from operations.monitoring import p90l90_real_live_source_ports as module

SHA = "a" * 40
EPOCH = "p90-real-epoch-" + "b" * 40
STARTED = AT - timedelta(seconds=1)
NOW = AT + timedelta(milliseconds=8)


class StubLedger(P90RealSharedCandleLedger):
    def __init__(self) -> None:
        pass

    def restart_watermarks(self, _symbol: str):
        return {"5": AT + timedelta(minutes=5)}

    def history(self, *, symbol: str, timeframe: str, through: datetime, limit: int):
        assert timeframe == "5" and limit == 36
        return tuple(
            Candle(
                symbol=symbol, timeframe="5",
                opened_at=through - timedelta(minutes=5 * (36 - i)),
                closed_at=through - timedelta(minutes=5 * (35 - i)),
                open=Decimal("100"), high=Decimal("101"),
                low=Decimal("99"), close=Decimal("100"),
                volume=Decimal("10"), is_closed=True,
            )
            for i in range(36)
        )


class StubDB:
    def __init__(self, *, transaction: bool = True, latest=None):
        self.info = SimpleNamespace(
            transaction_status=SimpleNamespace(
                name="INTRANS" if transaction else "IDLE",
            ),
        )
        self.latest = latest
        self.queries: list[str] = []

    def execute(self, sql: str, _params=()):
        self.queries.append(sql)
        if "p90l90_real_source_epochs" in sql:
            value = (EPOCH,)
        elif "exit_observations" in sql:
            value = self.latest
        else:
            raise AssertionError(sql)
        return SimpleNamespace(fetchone=lambda: value)


def _real_mirror(*, symbol: str = "LTCUSDT") -> PublicTradeMirrorBuffer:
    mirror = PublicTradeMirrorBuffer(
        (symbol,), retention_seconds=600,
    )
    assert mirror.start_epoch(STARTED) == 1
    return mirror


def _trade(mirror: PublicTradeMirrorBuffer, cursor: TradeCursor):
    mirror.record_message({
        "topic": f"publicTrade.{cursor.symbol}",
        "data": [{
            "s": cursor.symbol, "i": cursor.exec_id,
            "seq": cursor.seq,
            "T": int(cursor.traded_at.timestamp() * 1000),
            "p": "100.2", "v": "0.1", "S": "Buy",
        }],
    }, received_at=cursor.traded_at + timedelta(milliseconds=1))


def _ports(db: StubDB, mirror: PublicTradeMirrorBuffer) -> module.P90RealLiveSourcePorts:
    return module.P90RealLiveSourcePorts(
        db, mirror=mirror, ledger=StubLedger(),
        source_epoch_id=EPOCH, source_commit=SHA,
        mirror_serial=1, mirror_started_at=STARTED,
    )


def test_live_native_trade_is_proven_from_original_mirror_not_synthetic_boolean(
    monkeypatch,
) -> None:
    position, _plan, _state, cursor, _private, _price = _scope()
    db = StubDB()
    mirror = _real_mirror()
    _trade(mirror, cursor)
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda _tz: NOW))
    ports = _ports(db, mirror)
    assert ports.native_coverage(position, cursor) == (True, True)
    assert len(db.queries) == 2


@pytest.mark.parametrize("bad", (
    "no-market-trade", "forked-epoch", "missing-transaction",
    "source-gap", "foreign-cursor", "stale-tick",
))
def test_native_source_proof_cannot_be_bypassed(
    monkeypatch, bad: str,
) -> None:
    position, _plan, _state, cursor, _private, _price = _scope()
    db = StubDB(transaction=bad != "missing-transaction")
    mirror = _real_mirror()
    ports = _ports(db, mirror)
    if bad != "no-market-trade":
        _trade(mirror, cursor)
    if bad == "forked-epoch":
        mirror.start_epoch(STARTED)
    elif bad == "source-gap":
        mirror.mark_gap("forced websocket continuity break")
    elif bad == "foreign-cursor":
        cursor = TradeCursor(
            symbol=cursor.symbol, seq=cursor.seq, exec_id="other-native-id",
            traded_at=cursor.traded_at,
        )
    elif bad == "stale-tick":
        monkeypatch.setattr(module, "datetime", SimpleNamespace(
            now=lambda _tz: NOW + timedelta(seconds=20),
        ))
    if bad != "stale-tick":
        monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda _tz: NOW))
    with pytest.raises(module.P90RealLiveSourceUnverified):
        ports.native_coverage(position, cursor)


def test_previous_durable_mfe_anchor_must_exist_in_same_current_mirror(
    monkeypatch,
) -> None:
    position, _plan, state, cursor, _private, _price = _scope()
    db = StubDB(latest={"attributes": {"checkpoint": state.snapshot()}})
    mirror = _real_mirror()
    later = TradeCursor(
        symbol=cursor.symbol, seq=102,
        exec_id="bybit-real-next-native-102",
        traded_at=cursor.traded_at + timedelta(milliseconds=2),
    )
    _trade(mirror, later)
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda _tz: NOW))
    with pytest.raises(
        module.P90RealLiveSourceUnverified,
        match="PREVIOUS_ANCHOR_EVICTED_OR_REORDERED",
    ):
        _ports(db, mirror).native_coverage(position, later)


def test_canonical_5m_closed_history_and_watermark_are_source_transaction_scoped() -> None:
    db = StubDB()
    mirror = _real_mirror()
    ports = _ports(db, mirror)
    boundary = AT + timedelta(minutes=5)
    assert len(ports.closed_history("LTCUSDT", boundary)) == 36
    assert ports.closed_continuity("LTCUSDT", boundary) is True
    with pytest.raises(module.P90RealLiveSourceUnverified):
        ports.closed_continuity("LTCUSDT", boundary + timedelta(minutes=5))



def test_first_postfill_native_trade_must_not_skip_earlier_original_tick(
    monkeypatch,
) -> None:
    position, _plan, _state, cursor, _private, _price = _scope()
    earlier = TradeCursor(
        symbol=cursor.symbol, seq=cursor.seq - 1,
        exec_id="real-after-fill-100",
        traded_at=cursor.traded_at - timedelta(milliseconds=1),
    )
    mirror = _real_mirror()
    _trade(mirror, earlier)
    _trade(mirror, cursor)
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda _tz: NOW))
    with pytest.raises(
        module.P90RealLiveSourceUnverified,
        match="FIRST_POSTFILL_TRADE_SKIPPED",
    ):
        _ports(StubDB(), mirror).native_coverage(position, cursor)


def test_durable_mfe_must_advance_each_original_native_trade_in_order(
    monkeypatch,
) -> None:
    position, _plan, state, anchor, _private, _price = _scope()
    intermediate = TradeCursor(
        symbol=anchor.symbol, seq=anchor.seq + 1,
        exec_id="real-unprocessed-intermediate",
        traded_at=anchor.traded_at + timedelta(milliseconds=1),
    )
    future = TradeCursor(
        symbol=anchor.symbol, seq=anchor.seq + 2,
        exec_id="real-mfe-would-skip-intermediate",
        traded_at=anchor.traded_at + timedelta(milliseconds=2),
    )
    mirror = _real_mirror()
    for item in (anchor, intermediate, future):
        _trade(mirror, item)
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda _tz: NOW))
    with pytest.raises(
        module.P90RealLiveSourceUnverified,
        match="INTERMEDIATE_NATIVE_TRADES_SKIPPED",
    ):
        _ports(
            StubDB(latest={"attributes": {"checkpoint": state.snapshot()}}),
            mirror,
        ).native_coverage(position, future)


@pytest.mark.parametrize("tuple_row", (False, True))
def test_next_ordered_trade_after_durable_mfe_is_accepted(
    monkeypatch, tuple_row: bool,
) -> None:
    position, _plan, state, anchor, _private, _price = _scope()
    following = TradeCursor(
        symbol=anchor.symbol, seq=anchor.seq + 1,
        exec_id="real-immediate-next-execution",
        traded_at=anchor.traded_at + timedelta(milliseconds=1),
    )
    mirror = _real_mirror()
    _trade(mirror, anchor)
    _trade(mirror, following)
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda _tz: NOW))
    attrs = {"attributes": {"checkpoint": state.snapshot()}}
    db = StubDB(latest=(attrs["attributes"],) if tuple_row else attrs)
    assert _ports(db, mirror).native_coverage(position, following) == (True, True)


def test_closed_5m_port_observes_the_exact_shared_ledgers_advanced_watermark() -> None:
    class AdvancingLedger(StubLedger):
        def __init__(self) -> None:
            self.watermark = AT

        def restart_watermarks(self, _symbol: str):
            return {"5": self.watermark}

        def history(self, *, symbol: str, timeframe: str, through: datetime, limit: int):
            if through > self.watermark:
                raise RuntimeError("SOURCE_OWNER_HAS_NOT_EMITTED_THIS_BOUNDARY")
            return super().history(
                symbol=symbol, timeframe=timeframe, through=through, limit=limit,
            )

    ledger = AdvancingLedger()
    mirror = _real_mirror()
    ports = module.P90RealLiveSourcePorts(
        StubDB(), mirror=mirror, ledger=ledger,
        source_epoch_id=EPOCH, source_commit=SHA,
        mirror_serial=1, mirror_started_at=STARTED,
    )
    next_boundary = AT + timedelta(minutes=5)
    with pytest.raises(module.P90RealLiveSourceUnverified):
        ports.closed_continuity("LTCUSDT", next_boundary)
    # This is the SAME object whose watermark the real P90 consumer advances
    # during _bars.append(), never a second prestart snapshot object.
    ledger.watermark = next_boundary
    assert ports._ledger is ledger
    assert ports.closed_continuity("LTCUSDT", next_boundary) is True
    assert ports.closed_history("LTCUSDT", next_boundary)[-1].closed_at == next_boundary
