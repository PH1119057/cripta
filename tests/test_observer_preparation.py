from datetime import UTC, datetime, timedelta

import pytest

from operations.monitoring.observer_preparation import (
    PreparationBudget,
    PreparationWaiting,
    cold_preparation_wait,
    prepare_closed_tails,
)


def test_preparation_budget_is_finite_and_success_resets_it():
    budget = PreparationBudget(max_attempts=2, timeout_seconds=10)
    assert budget.wait(PreparationWaiting("TAIL"), now=1)["state"] == "PREPARING"
    assert budget.wait(PreparationWaiting("TAIL"), now=2)["state"] == "BLOCKED"
    budget.reset()
    assert budget.wait(PreparationWaiting("TAIL"), now=30)["state"] == "PREPARING"
    assert budget.wait(PreparationWaiting("TAIL"), now=41)["state"] == "BLOCKED"


def test_boundary_crossing_repairs_only_tail_before_attestation():
    start = datetime(2026, 11, 4, 15, 19, 59, tzinfo=UTC)
    times = iter([start, start+timedelta(seconds=2),
                  start+timedelta(seconds=3), start+timedelta(seconds=4)])
    present = {}
    fetched = []
    def check(symbol, tf, through):
        return present.get((symbol, tf)) == through
    def fetch(symbol, tf, through):
        fetched.append((symbol, tf, through))
        present[symbol, tf] = through
    progress = []
    prepare_closed_tails(
        symbols=("A", "B"), check=check, fetch=fetch,
        clock=lambda: next(times), progress=progress.append,
    )
    assert present["A", "5"].minute == 20
    assert present["B", "5"].minute == 20
    assert present["A", "15"].minute == 15
    assert len(fetched) == 6  # two 5m tails advance; 15m never fetched twice
    assert progress and all(x["state"] == "PREPARING" for x in progress)


def test_provider_lag_is_waiting_but_integrity_error_is_not_suppressed():
    now = datetime(2026, 11, 4, 15, 20, tzinfo=UTC)
    with pytest.raises(PreparationWaiting) as caught:
        prepare_closed_tails(
            symbols=("LTCUSDT",), check=lambda *_: False, fetch=lambda *_: None,
            clock=lambda: now,
        )
    assert caught.value.detail["symbol"] == "LTCUSDT"
    assert caught.value.detail["timeframe"] == "5"
    def invalid(*_):
        raise RuntimeError("CANDLE_IMMUTABLE_SOURCE_CONFLICT")
    with pytest.raises(RuntimeError, match="IMMUTABLE_SOURCE_CONFLICT"):
        prepare_closed_tails(
            symbols=("LTCUSDT",), check=invalid, fetch=lambda *_: None,
            clock=lambda: now,
        )


@pytest.mark.parametrize("code", [
    "CANDLE_HISTORY_GAP_OR_DUPLICATE", "CANDLE_IMMUTABLE_SOURCE_CONFLICT",
    "P90_REAL_ACCOUNT_UID_MISMATCH", "AMBIGUOUS_MUTATION",
])
def test_cold_wait_never_masks_integrity_or_safety_faults(code):
    original = RuntimeError(code)
    with pytest.raises(RuntimeError) as caught:
        cold_preparation_wait(original)
    assert caught.value is original


@pytest.mark.parametrize("waiting,expected,max_attempts", [
    (True, "PREPARING", 5), (True, "BLOCKED", 1), (False, "ERROR", 5),
])
def test_observer_loop_distinguishes_preparation_from_durable_fault(
    monkeypatch, tmp_path, waiting, expected, max_attempts,
):
    from types import SimpleNamespace

    from operations.monitoring import universal_entry_shadow as observer

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def rollback(self): pass

    signals = []
    statuses = []
    faults = []
    bundle = SimpleNamespace(card=SimpleNamespace(scope=SimpleNamespace(to_dict=lambda: {})))
    monkeypatch.setattr(observer, "OBSERVER_STATE_ROOT", tmp_path)
    monkeypatch.setattr(observer, "LOADED_COMMIT", "a"*40)
    monkeypatch.setattr(observer, "FACT_SOURCE_ID", observer.REST_OI30S_SOURCE_ID)
    monkeypatch.setattr(observer, "OI_SAMPLE_SECONDS", 30)
    monkeypatch.setattr(observer, "PreparationBudget", lambda: PreparationBudget(
        max_attempts=max_attempts,
    ))
    monkeypatch.setattr(observer.psycopg, "connect", lambda *_a, **_k: DB())
    monkeypatch.setattr(observer.signal, "signal", lambda _sig, fn: signals.append(fn))
    monkeypatch.setattr(observer, "load_active_strategy_bundles", lambda _db: [bundle])
    monkeypatch.setattr(observer, "_atomic_json", lambda _p, data: statuses.append(data))
    monkeypatch.setattr(observer, "_record_observer_runtime_fault",
                        lambda *_a, **_k: faults.append(True) or "fault-id")
    monkeypatch.setattr(observer.time, "sleep", lambda _: signals[0](None, None))
    def epoch(*_a, **_k):
        if waiting:
            raise PreparationWaiting("CANDLE_TAIL_NOT_YET_AVAILABLE", symbol="LTCUSDT")
        raise RuntimeError("CANDLE_IMMUTABLE_SOURCE_CONFLICT")
    monkeypatch.setattr(observer, "_run_observer_epoch", epoch)
    observer._run_multi_strategy_observer()
    assert statuses[0]["state"] == expected
    assert statuses[0]["observer_ready"] is False
    assert bool(faults) is not waiting


def test_db_adapter_fetches_only_missing_tail_using_existing_source(monkeypatch):
    from types import SimpleNamespace

    from operations.monitoring import observer_preparation as prep

    now = datetime(2026, 11, 4, 15, 20, tzinfo=UTC)
    latest = {"5": now-timedelta(minutes=5), "15": now-timedelta(minutes=5)}
    calls = []
    class Store:
        def __init__(self, db, key): self.key = key
        def history(self, *, through, required):
            assert required == (2600 if self.key.timeframe == "5" else 999)
            if latest[self.key.timeframe] != through:
                raise RuntimeError("CANDLE_WARMUP_END_NOT_PROVEN")
    class DB:
        def execute(self, _sql, args):
            assert args[:2] == ("ms-bybit-mainnet-public-v1", "inst-bybit-linear-ltcusdt")
            return SimpleNamespace(fetchone=lambda: (latest[args[2]],))
    def fetch(symbol, end, intervals, *, required_counts):
        calls.append((symbol, end, intervals, required_counts))
        return {intervals[0]: (end,)}
    def ingest(_db, *, symbol, history, **_kwargs):
        for tf, bars in history.items():
            latest[tf] = bars[-1]
    monkeypatch.setattr(prep, "SharedClosedCandleStore", Store)
    monkeypatch.setattr(prep, "cache_approved_bybit_history", ingest)
    prep.prepare_p90_closed_tails(
        DB(), symbols=("LTCUSDT",), source_fetch=fetch,
        source_commit="a"*40, clock=lambda: now, progress=lambda _: None,
    )
    assert calls == [("LTCUSDT", now, ("5",), {"5": 2})]
