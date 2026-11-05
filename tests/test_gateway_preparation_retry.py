from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from operations.monitoring import observer_preparation as prep


def test_blocked_preparation_requires_new_active_exact_release_request():
    at = datetime(2026, 11, 5, tzinfo=UTC)

    class DB:
        def execute(self, sql, args):
            assert "SELECT request_id" in sql
            assert "created_at > %s" in sql
            assert "release_commit=%s" in sql
            assert "P90_REAL_V3" in sql
            assert "VALIDATING" in sql
            assert args == ("a" * 40, at)
            return SimpleNamespace(fetchone=lambda: ("new-request",))

    assert prep.preparation_retry_request(DB(), release_commit="a" * 40, blocked_at=at)


def test_no_request_does_not_reset_budget():
    at = datetime(2026, 11, 5, tzinfo=UTC)
    db = SimpleNamespace(execute=lambda *_: SimpleNamespace(fetchone=lambda: None))
    assert not prep.preparation_retry_request(db, release_commit="a" * 40, blocked_at=at)


def test_rest_provisional_tail_waits_and_is_confirmed_before_any_ingestion():
    from decimal import Decimal

    from bybit_workbench.domain.models import Candle

    end = datetime(2026, 11, 5, tzinfo=UTC)
    candle = Candle("APTUSDT", "5", end-timedelta(minutes=5), end,
                    Decimal("1"), Decimal("2"), Decimal("0.5"),
                    Decimal("1.1"), Decimal("10"))
    sleeps, requests = [], []
    prep.confirm_rest_closed_tail(
        candle, fetch=lambda: requests.append(True) or (candle,),
        clock=lambda: end+timedelta(milliseconds=172), sleep=sleeps.append,
    )
    assert requests == [True]
    assert 4 < sleeps[0] <= 5


def test_changed_rest_tail_is_waiting_not_an_immutable_insert():
    from dataclasses import replace
    from decimal import Decimal

    import pytest

    from bybit_workbench.domain.models import Candle

    end = datetime(2026, 11, 5, tzinfo=UTC)
    candle = Candle("APTUSDT", "5", end-timedelta(minutes=5), end,
                    Decimal("1"), Decimal("2"), Decimal("0.5"),
                    Decimal("1.1"), Decimal("10"))
    with pytest.raises(prep.PreparationWaiting, match="CANDLE_REST_TAIL_NOT_FINAL"):
        prep.confirm_rest_closed_tail(
            candle, fetch=lambda: (replace(candle, close=Decimal("1.2")),),
            clock=lambda: end+timedelta(seconds=8), sleep=lambda _: None,
        )


def test_running_observer_retries_blocked_preparation_without_restart_or_arm(monkeypatch, tmp_path):
    from operations.monitoring import universal_entry_shadow as observer

    budget = prep.PreparationBudget(max_attempts=1)
    signals, statuses, epochs, sql = [], [], [], []

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def rollback(self): pass
        def execute(self, statement, args):
            sql.append(statement)
            assert statement.lstrip().startswith("SELECT request_id")
            return SimpleNamespace(fetchone=lambda: ("fresh-authenticated-request",))

    bundle = SimpleNamespace(card=SimpleNamespace(scope=SimpleNamespace(to_dict=lambda: {})))
    monkeypatch.setattr(observer, "OBSERVER_STATE_ROOT", tmp_path)
    monkeypatch.setattr(observer, "LOADED_COMMIT", "a"*40)
    monkeypatch.setattr(observer, "FACT_SOURCE_ID", observer.REST_OI30S_SOURCE_ID)
    monkeypatch.setattr(observer, "OI_SAMPLE_SECONDS", 30)
    monkeypatch.setattr(observer, "PreparationBudget", lambda: budget)
    monkeypatch.setattr(observer.psycopg, "connect", lambda *_a, **_k: DB())
    monkeypatch.setattr(observer.signal, "signal", lambda _sig, fn: signals.append(fn))
    monkeypatch.setattr(observer, "load_active_strategy_bundles", lambda _db: [bundle])
    monkeypatch.setattr(observer, "_atomic_json", lambda _p, data: statuses.append(data))
    monkeypatch.setattr(observer.time, "sleep", lambda _: None)

    def epoch(*_args, **_kwargs):
        epochs.append(True)
        if len(epochs) == 1:
            raise prep.PreparationWaiting("CANDLE_TAIL_NOT_YET_AVAILABLE")
        assert budget.attempts == 0 and budget.started is None
        return "STOP"

    monkeypatch.setattr(observer, "_run_observer_epoch", epoch)
    observer._run_multi_strategy_observer()
    assert len(epochs) == 2 and len(sql) == 1
    assert statuses[0]["state"] == "BLOCKED"
    assert all(item["observer_ready"] is False for item in statuses)
