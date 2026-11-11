"""R06 physical PG-only no-order Entry automaton durability and fail-closed tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.p90_r06_no_order_state import (
    R06NoOrderStateBlocked, advance_symbol, dump_symbol, restore_symbol,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import ALLOWED_SYMBOLS
from bybit_workbench.universal_entry.p90l90_scoped_native_source import OrderedNativeTrade
from operations.monitoring import r06_pg_only_entry as mod

BASE = datetime(2026, 1, 1, tzinfo=UTC)
BOUNDARY = BASE + timedelta(days=9)
D = Decimal
SYMBOL = "LTCUSDT"

def candles(minutes: int, count: int, symbol: str = SYMBOL) -> tuple[Candle, ...]:
    delta = timedelta(minutes=minutes)
    return tuple(
        Candle(
            symbol=symbol,timeframe=str(minutes),
            opened_at=BASE + i*delta,
            closed_at=BASE + (i+1)*delta,
            open=D("100"),high=D("110"),low=D("90"),
            close=D("100"),volume=D("1"),
        ) for i in range(count)
    )

@pytest.fixture(scope="module")
def history():
    return candles(5,2592),candles(15,864)

@pytest.mark.parametrize(("previous","price","side"),[
    ("91","90","LONG"),("109","110","SHORT"),
])
def test_exact_first_crossing_durable_after_restart(history,previous,price,side):
    f,q=history
    state=restore_symbol(None,five=f,fifteen=q,published_closed_at=BOUNDARY)
    assert advance_symbol(
        state,price=D(previous),event_at=BOUNDARY+timedelta(seconds=1),
        five=f,fifteen=q,published_closed_at=BOUNDARY,
    ) == ()
    out=advance_symbol(
        state,price=D(price),event_at=BOUNDARY+timedelta(seconds=2),
        five=f,fifteen=q,published_closed_at=BOUNDARY,
    )
    assert len(out)==1 and out[0].side==side
    saved=dump_symbol(state)
    assert saved["seen"]==[side]
    restarted=restore_symbol(saved,five=f,fifteen=q,published_closed_at=BOUNDARY)
    advance_symbol(
        restarted,price=D(previous),event_at=BOUNDARY+timedelta(seconds=3),
        five=f,fifteen=q,published_closed_at=BOUNDARY,
    )
    assert advance_symbol(
        restarted,price=D(price),event_at=BOUNDARY+timedelta(seconds=4),
        five=f,fifteen=q,published_closed_at=BOUNDARY,
    ) == ()
    assert dump_symbol(restarted)["seen"]==[side]

@pytest.mark.parametrize("bad",[
    {"previous_price":"NaN","previous_at":BOUNDARY.isoformat(),"geometry_at":BOUNDARY.isoformat(),"seen":[]},
    {"previous_price":"100","previous_at":BOUNDARY.isoformat(),"geometry_at":BOUNDARY.isoformat(),"seen":["LONG","LONG"]},
    {"previous_price":"100","previous_at":"not-a-date","geometry_at":BOUNDARY.isoformat(),"seen":[]},
    {"previous_price":"100","previous_at":BOUNDARY.isoformat(),"geometry_at":(BOUNDARY+timedelta(minutes=5)).isoformat(),"seen":[]},
])
def test_corrupt_persisted_native_state_never_restores(history,bad):
    f,q=history
    with pytest.raises(R06NoOrderStateBlocked):
        restore_symbol(bad,five=f,fifteen=q,published_closed_at=BOUNDARY)

def test_missing_prev7d_never_creates_causal_signal(history):
    f,q=history
    model=restore_symbol(None,five=f,fifteen=q,published_closed_at=BOUNDARY)
    with pytest.raises(R06NoOrderStateBlocked,match="GEOMETRY_UNVERIFIED"):
        advance_symbol(
            model,price=D("91"),event_at=BOUNDARY+timedelta(seconds=1),
            five=f[-200:],fifteen=q,published_closed_at=BOUNDARY,
        )

def test_no_future_or_out_of_order_native_event(history):
    f,q=history
    model=restore_symbol(None,five=f,fifteen=q,published_closed_at=BOUNDARY)
    with pytest.raises(R06NoOrderStateBlocked,match="UNPUBLISHED"):
        advance_symbol(
            model,price=D("91"),event_at=BOUNDARY+timedelta(minutes=5),
            five=f,fifteen=q,published_closed_at=BOUNDARY,
        )
    advance_symbol(
        model,price=D("91"),event_at=BOUNDARY+timedelta(seconds=3),
        five=f,fifteen=q,published_closed_at=BOUNDARY,
    )
    with pytest.raises(R06NoOrderStateBlocked,match="CAUSAL_INVALID"):
        advance_symbol(
            model,price=D("90"),event_at=BOUNDARY+timedelta(seconds=2),
            five=f,fifteen=q,published_closed_at=BOUNDARY,
        )

class Row:
    def __init__(self,data=None,rowcount=1):
        self.data=data
        self.rowcount=rowcount
    def fetchone(self):
        return self.data

class FakeWriter:
    def __init__(self):
        self.queries=[]
        self.state={"version":1,"by_symbol":{}}
        self.last_pub=44
    def execute(self,sql,params=()):
        self.queries.append((sql,params))
        if "SELECT causal_state,last_publication_id" in sql:
            return Row((self.state,self.last_pub))
        if "INSERT INTO strategy_entry.p90_r06_publication_observations" in sql:
            return Row()
        if "UPDATE strategy_entry.p90_scoped_native_consumer_checkpoints" in sql:
            self.state=__import__("json").loads(params[0])
            self.last_pub=params[1]
            return Row()
        raise AssertionError(sql)

@pytest.fixture
def controlled():
    pin=SimpleNamespace(
        publication_id=45,source_session_id="a"*32,
        closed_refs={sym:{"5":BOUNDARY.isoformat()}
                     for sym in ALLOWED_SYMBOLS[MAIN_ID]},
    )
    bundle=SimpleNamespace(
        card=SimpleNamespace(strategy_id=MAIN_ID),
        entry_plan=SimpleNamespace(entry_plan_fingerprint="b"*64),
        exit_plan=SimpleNamespace(exit_plan_fingerprint="c"*64),
    )
    return pin,bundle

def scoped_history(history):
    f,q=history
    return {sym:{"5":f if sym==SYMBOL else candles(5,2592,sym),
                 "15":q if sym==SYMBOL else candles(15,864,sym)}
            for sym in ALLOWED_SYMBOLS[MAIN_ID]}


def test_source_only_native_callback_and_cursor_share_publication_atomic_state(
    monkeypatch,history,controlled,
):
    f,q=history
    pin,bundle=controlled
    writer=FakeWriter()
    batch=(
        OrderedNativeTrade(10,0,SYMBOL,100,"p1",BOUNDARY+timedelta(seconds=1),
                           D("91"),D("1"),"Buy",BOUNDARY+timedelta(seconds=1)),
        OrderedNativeTrade(10,1,SYMBOL,101,"p2",BOUNDARY+timedelta(seconds=2),
                           D("90"),D("1"),"Sell",BOUNDARY+timedelta(seconds=2)),
    )
    def deliver(_writer,*,on_publication_trade,**_kw):
        for event in batch:
            on_publication_trade(event,pin.publication_id)
        return batch
    monkeypatch.setattr(mod,"consume_pinned_entry_native_batch",deliver)
    signal=SimpleNamespace(
        strategy_id=MAIN_ID,entry_plan_fingerprint=bundle.entry_plan.entry_plan_fingerprint,
        signal_id="s1",direction=SimpleNamespace(value="LONG"),
    )
    evaluated=SimpleNamespace(
        decision=None,execution_request=None,notifications=(),
        account_outcomes=(),signal=signal,
    )
    engine=SimpleNamespace(process=lambda *_a,**_kw:(evaluated,))
    # Source-only test has no real execution side effects, just audit records.
    count=mod._observe_one(
        writer,pin=pin,history=scoped_history(history),
        source=object(),bundle=bundle,engine=engine,loaded="d"*40,
        at=BOUNDARY+timedelta(seconds=3),
    )
    assert count==1
    assert writer.last_pub==45
    assert writer.state["by_symbol"][SYMBOL]["seen"]==["LONG"]
    assert sum("INSERT INTO strategy_entry.p90_r06_publication_observations" in s for s,_ in writer.queries)==1
    # A restart reconstructs current previous/seen; never reopens a crossing.
    recovered=restore_symbol(
        writer.state["by_symbol"][SYMBOL],five=f,fifteen=q,
        published_closed_at=BOUNDARY,
    )
    assert recovered.previous_price==D("90")
    assert recovered.seen=={"LONG"}

def test_idle_publication_advances_lineage_without_inventing_signal(
    monkeypatch,history,controlled,
):
    f,q=history
    pin,bundle=controlled
    writer=FakeWriter()
    monkeypatch.setattr(mod,"consume_pinned_entry_native_batch",
                        lambda *_a,**_kw: ())
    engine=SimpleNamespace(process=lambda *_a,**_kw:pytest.fail("ghost Entry"))
    assert mod._observe_one(
        writer,pin=pin,history=scoped_history(history),
        source=object(),bundle=bundle,engine=engine,loaded="d"*40,
        at=BOUNDARY+timedelta(seconds=3),
    ) == 0
    assert writer.last_pub==45
    assert writer.state["by_symbol"]=={}

def test_any_financial_effect_blocks_before_no_order_state_commit(
    monkeypatch,history,controlled,
):
    f,q=history
    pin,bundle=controlled
    writer=FakeWriter()
    batch=(
        OrderedNativeTrade(10,0,SYMBOL,100,"p1",BOUNDARY+timedelta(seconds=1),
                           D("91"),D("1"),"Buy",BOUNDARY+timedelta(seconds=1)),
        OrderedNativeTrade(10,1,SYMBOL,101,"p2",BOUNDARY+timedelta(seconds=2),
                           D("90"),D("1"),"Sell",BOUNDARY+timedelta(seconds=2)),
    )
    def deliver(_writer,*,on_publication_trade,**_kw):
        for event in batch:
            on_publication_trade(event,pin.publication_id)
        return batch
    monkeypatch.setattr(mod,"consume_pinned_entry_native_batch",deliver)
    unsafe=SimpleNamespace(
        decision=object(),execution_request=None,notifications=(),
        account_outcomes=(),signal=SimpleNamespace(
            strategy_id=MAIN_ID,
            entry_plan_fingerprint=bundle.entry_plan.entry_plan_fingerprint,
        ),
    )
    with pytest.raises(mod.R06PublishedEntryBlocked,match="FINANCIAL"):
        mod._observe_one(
            writer,pin=pin,history=scoped_history(history),
            source=object(),bundle=bundle,
            engine=SimpleNamespace(process=lambda *_a,**_kw:(unsafe,)),
            loaded="d"*40,at=BOUNDARY+timedelta(seconds=3),
        )
    assert not any("UPDATE strategy_entry" in sql for sql,_ in writer.queries)
