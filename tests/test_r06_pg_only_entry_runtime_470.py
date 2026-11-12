"""R06 physical PG-only no-order Entry automaton durability and fail-closed tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.p90_r06_no_order_state import (
    R06NoOrderStateBlocked,
    advance_symbol,
    dump_symbol,
    restore_symbol,
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
    {"previous_price":"NaN","previous_at":BOUNDARY.isoformat(),
     "geometry_at":BOUNDARY.isoformat(),"seen":[]},
    {"previous_price":"100","previous_at":BOUNDARY.isoformat(),
     "geometry_at":BOUNDARY.isoformat(),"seen":["LONG","LONG"]},
    {"previous_price":"100","previous_at":"not-a-date",
     "geometry_at":BOUNDARY.isoformat(),"seen":[]},
    {"previous_price":"100","previous_at":BOUNDARY.isoformat(),
     "geometry_at":(BOUNDARY+timedelta(minutes=5)).isoformat(),"seen":[]},
])
def test_corrupt_persisted_native_state_never_restores(history,bad):
    f,q=history
    with pytest.raises(R06NoOrderStateBlocked):
        restore_symbol(bad,five=f,fifteen=q,published_closed_at=BOUNDARY)

def test_recovery_rejects_changed_historical_h9_bars(history):
    f,q=history
    state=restore_symbol(None,five=f,fifteen=q,published_closed_at=BOUNDARY)
    advance_symbol(
        state,price=D("91"),event_at=BOUNDARY+timedelta(seconds=1),
        five=f,fifteen=q,published_closed_at=BOUNDARY,
    )
    saved=dump_symbol(state)
    corrupt=f[:-1]+(replace(f[-1],high=D("110.25")),)
    with pytest.raises(R06NoOrderStateBlocked,match="GEOMETRY_CHANGED"):
        restore_symbol(saved,five=corrupt,fifteen=q,published_closed_at=BOUNDARY)


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


def test_pg_only_cutover_cannot_be_launched_from_ordinary_shell(monkeypatch):
    monkeypatch.setattr(
        mod.Path, "read_text",
        lambda _self, **_kw: "0::/user.slice/user-1000.slice",
    )
    monkeypatch.setattr(mod, "_runtime_actor_verified", lambda: True)
    monkeypatch.setenv("INVOCATION_ID", "test")
    with pytest.raises(mod.R06PublishedEntryBlocked,match="EXCLUSIVITY"):
        mod._require_exclusive_systemd_owner()


def test_same_unit_cgroup_is_required_before_source_only_start(monkeypatch):
    monkeypatch.setattr(
        mod.Path, "read_text",
        lambda _self, **_kw:
            "0::/system.slice/cripta-universal-entry-observer.service",
    )
    monkeypatch.setattr(mod, "_runtime_actor_verified", lambda: True)
    monkeypatch.setenv("INVOCATION_ID", "test")
    mod._require_exclusive_systemd_owner()


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
    assert sum(
        "INSERT INTO strategy_entry.p90_r06_publication_observations" in sql
        for sql, _ in writer.queries
    ) == 1
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



@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (mod.EntryPublicationUnavailable("PUBLICATION_STALE"), True),
        (mod.psycopg.errors.SerializationFailure("PG_CONFLICT"), True),
        (mod.R06FreshCutoverBlocked("R06_NEW_PUBLICATION_UNVERIFIED"), True),
        (mod.R06FreshCutoverBlocked("R06_START_PUBLICATION_STALE_OR_CHANGED"), True),
        (mod.R06FreshCutoverBlocked("R06_FUTURE_CLOSED_BOUNDARY_NOT_REACHED"), True),
        (mod.R06FreshCutoverBlocked("R06_BOOTSTRAP_CONTRACT_INVALID"), False),
        (ValueError("program defect"), False),
    ],
)
def test_pre_cursor_transient_retries_only_preapproved_source_wait(error, expected):
    assert mod._bootstrap_transient_retryable(
        error, detached_at=BASE, observed_at=BASE + timedelta(minutes=2),
    ) is expected


@pytest.mark.parametrize("err", [
    mod.EntryPublicationUnavailable("PUBLICATION_STALE"),
    mod.psycopg.errors.SerializationFailure("PG_CONFLICT"),
])
def test_pre_cursor_transient_must_expire_at_existing_10_minute_budget(err):
    assert mod._bootstrap_transient_retryable(
        err, detached_at=BASE, observed_at=BASE + timedelta(minutes=10),
    )
    assert not mod._bootstrap_transient_retryable(
        err, detached_at=BASE,
        observed_at=BASE + timedelta(minutes=10, microseconds=1),
    )
    assert not mod._bootstrap_transient_retryable(
        err, detached_at=BASE, observed_at=BASE - timedelta(seconds=1),
    )



def test_worker_failure_classification_never_leaks_private_exception_detail():
    assert mod._worker_block_code(
        mod.R06FreshCutoverBlocked("R06_NEW_PUBLICATION_UNVERIFIED"),
    ) == "R06_NEW_PUBLICATION_UNVERIFIED"
    assert mod._worker_block_code(
        ValueError("API_SECRET=DO_NOT_LOG"),
    ) == "R06_UNCLASSIFIED_RUNTIME_EXCEPTION"
    assert mod._worker_block_code(
        mod.psycopg.errors.SerializationFailure("sensitive DB params"),
    ) == "R06_PG_SERIALIZATION_CONFLICT"
    assert mod._worker_block_code(
        mod.R06PublishedEntryBlocked(
            "R06_PUBLISHED_NATIVE_SOURCE_NOT_CURRENT:private-name"
        ),
    ) == "R06_PUBLISHED_NATIVE_SOURCE_NOT_CURRENT"



@pytest.mark.parametrize("phase", ["pin", "proof", "bootstrap", "after_commit", "expired"])
def test_worker_retries_conflict_with_fresh_proof_only_before_commit(monkeypatch, phase):
    """Drive the real worker loop; never depend on its source text."""
    from contextlib import contextmanager

    class Finished(BaseException):
        pass

    calls = []
    committed = []
    conflicted = False
    loaded = "d" * 40

    class Clock:
        @classmethod
        def now(cls, _zone):
            if calls and phase == "expired":
                return BASE + timedelta(minutes=11)
            return BASE

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, _params=()):
            assert "pg_try_advisory_lock" in sql
            return Row((True,))

        @contextmanager
        def transaction(self):
            calls.append("begin")
            try:
                yield self
            except BaseException:
                calls.append("rollback")
                raise
            else:
                calls.append("commit")

    writer, reader = Connection(), Connection()
    connections = iter((writer, reader))
    pin = SimpleNamespace(
        publication_id=45, source_session_id="a" * 32,
        closed_refs={symbol: {"5": (BASE + timedelta(minutes=5)).isoformat()}
                     for symbol in mod.FIVE},
    )

    def conflict_once(where):
        nonlocal conflicted
        calls.append(where)
        if not conflicted and (
            phase == where or phase == "expired" and where == "bootstrap"
        ):
            conflicted = True
            raise mod.psycopg.errors.SerializationFailure("private DB detail")

    def one_pin(*_args, **_kwargs):
        conflict_once("pin")
        if phase == "after_commit" and committed:
            raise mod.psycopg.errors.SerializationFailure("private DB detail")
        return pin, {}, frozenset()

    def proof(*_args, **_kwargs):
        conflict_once("proof")
        return object()

    def bootstrap(db, **kwargs):
        assert db is writer and kwargs["readonly_connection"] is reader
        with db.transaction():
            conflict_once("bootstrap")
            committed.extend((MAIN_ID, "sub"))
        return tuple(committed)

    def checkpoints(*_args, **_kwargs):
        if committed:
            raise Finished()
        return ()

    def sleep(delay):
        assert delay == 1.0
        calls.append("retry")

    def safety(*_args):
        calls.append("safety")

    monkeypatch.setenv("CRIPTA_R06_PG_ONLY", "ENABLED")
    monkeypatch.setattr(mod, "_require_exclusive_systemd_owner", lambda: None)
    monkeypatch.setattr(mod.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(mod, "datetime", Clock)
    monkeypatch.setattr(mod.psycopg, "connect", lambda *_args, **_kw: next(connections))
    monkeypatch.setattr(mod, "_bundles", lambda *_args: ())
    monkeypatch.setattr(mod, "_engines", lambda *_args: {})
    monkeypatch.setattr(mod, "_require_no_exposure_or_requests", safety)
    monkeypatch.setattr(mod, "_one_pin", one_pin)
    monkeypatch.setattr(mod, "_checkpoints", checkpoints)
    monkeypatch.setattr(mod, "verify_r06_fresh_publication", proof)
    monkeypatch.setattr(mod, "bootstrap_future_entry_checkpoints", bootstrap)
    monkeypatch.setattr(mod.time, "sleep", sleep)
    # Reader's SET TRANSACTION is the only statement inside its proof.
    reader.execute = lambda sql, _params=(): Row()
    statuses = []
    terminal = SystemExit if phase in ("after_commit", "expired") else Finished
    with pytest.raises(terminal) as result:
        mod.run_pg_only(
            loaded_commit=loaded, dsn="fake",
            write_status=lambda **state: statuses.append(state),
        )
    if phase in ("after_commit", "expired"):
        assert result.value.code == 42
        assert calls.count("retry") == 0
        assert statuses[-1]["reason"].endswith("R06_PG_SERIALIZATION_CONFLICT")
        assert "private DB detail" not in statuses[-1]["reason"]
        assert len(committed) == (2 if phase == "after_commit" else 0)
    else:
        assert calls.count("retry") == 1
        retry = calls.index("retry")
        # Every retry must revisit safety and select a new immutable pin.
        assert calls[retry + 1:retry + 3] == ["safety", "pin"]
        assert len(committed) == 2
        if phase in ("proof", "bootstrap"):
            assert "rollback" in calls[:retry]
        assert not any(state["state"] == "BLOCKED" for state in statuses)
