"""One-pass R06 future-publication source/seed/safety/rollback contracts."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.monitoring import r06_fresh_entry_boundary as mod

AT = datetime(2026, 11, 12, 1, 5, 1, tzinfo=UTC)
DETACHED = datetime(2026, 11, 12, 0, 59, 59, tzinfo=UTC)
BOUNDARY = datetime(2026, 11, 12, 1, 5, tzinfo=UTC)
SYMBOLS = mod.FIVE
SOURCE_SHA = "a" * 40
CONSUMER_SHA = "b" * 40
EPOCH = "c" * 32
PLANS = (
    (mod.MAIN_ID, "approved", "d" * 64, "e" * 64),
    (mod.SUB_ID, "approved", "f" * 64, "0" * 64),
)


def _bundles() -> tuple[object, ...]:
    return tuple(
        SimpleNamespace(
            card=SimpleNamespace(strategy_id=name,strategy_version=version),
            entry_plan=SimpleNamespace(entry_plan_fingerprint=entry_fp),
            exit_plan=SimpleNamespace(exit_plan_fingerprint=exit_fp),
        )
        for name,version,entry_fp,exit_fp in PLANS
    )


@pytest.fixture(autouse=True)
def signed_private_no_order_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    from operations.monitoring import p90l90_real_source_bootstrap as private

    # Disposable test only. Production import uses the exact real MAIN+SUB
    # credential-local reader; no injectable callback is accepted there.
    monkeypatch.setattr(
        private, "preflight_p90_real_private_flat_source",
        lambda *_args, **_kwargs: None,
    )


def _pin() -> SimpleNamespace:
    return SimpleNamespace(
        publication_id=15200,
        source_session_id=EPOCH,
        producer_release_commit=SOURCE_SHA,
        as_of_second=AT,
        closed_refs={
            s: {"5": BOUNDARY.isoformat(), "15": BOUNDARY.isoformat()}
            for s in SYMBOLS
        },
        native_watermarks={
            s: {"frame_number": i + 100, "frame_index": 1}
            for i, s in enumerate(SYMBOLS)
        },
    )


class One:
    def __init__(self, result: object):
        self.result = result

    def fetchone(self) -> object:
        return self.result

    def fetchall(self) -> object:
        return self.result


class ReadOnlyDB:
    def execute(self, sql: str, params: object = ()) -> One:
        assert sql.lstrip().upper().startswith("SELECT")
        assert "FROM analytics.native_public_trade_source_sessions" in sql
        return One([(EPOCH, SOURCE_SHA)])


def test_current_new_publication_h9_atr_ready_for_all_five(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pin = _pin()
    calls: list[object] = []
    monkeypatch.setattr(
        mod, "verify_current_entry_plan_scope", lambda _db: SYMBOLS,
    )
    monkeypatch.setattr(
        mod, "verify_native_producer_code_compatibility",
        lambda **_kw: frozenset((SOURCE_SHA,)),
    )

    def inspected(_db: object, **kw: object) -> object:
        assert kw["intervals"] == ("5", "15")
        assert kw["accepted_producer_commits"] == frozenset((SOURCE_SHA,))
        calls.append("PIN")
        return pin

    def full(_db: object, **kw: object) -> dict[str, tuple[int, ...]]:
        assert kw["required_counts"] == {"5": 2600, "15": 1000}
        calls.append(kw["symbol"])
        return {"5": (1,) * 2600, "15": (1,) * 1000}

    monkeypatch.setattr(mod, "read_entry_publication_pin", inspected)
    monkeypatch.setattr(mod, "read_entry_publication_seed", full)
    monkeypatch.setattr(
        mod, "causal_entry_state",
        lambda *_a, **_kw: SimpleNamespace(effective_at=BOUNDARY),
    )
    result = mod.verify_r06_fresh_publication(
        ReadOnlyDB(), legacy_detached_at=DETACHED,
        observed_at=AT + timedelta(seconds=1),
        source_checkout=Path("/source_checkout"),
        installed_consumer_commit=CONSUMER_SHA,
    )
    assert result.new_epoch_only
    assert result.pin.publication_id == 15200
    assert result.first_cursor == (104, 1)
    assert set(result.geometry) == set(SYMBOLS)
    assert result.exact_lookback == {
        sym: {"5": 2600, "15": 1000} for sym in SYMBOLS
    }
    assert calls == ["PIN", *SYMBOLS]


def test_pre_detach_or_not_yet_closed_publication_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mod, "verify_current_entry_plan_scope", lambda _db: SYMBOLS,
    )
    monkeypatch.setattr(
        mod, "verify_native_producer_code_compatibility",
        lambda **_kw: frozenset((SOURCE_SHA,)),
    )
    monkeypatch.setattr(mod, "read_entry_publication_pin", lambda *_a, **_k: _pin())
    with pytest.raises(mod.R06FreshCutoverBlocked, match="DETACH_FENCE"):
        mod.verify_r06_fresh_publication(
            ReadOnlyDB(), legacy_detached_at=AT+timedelta(seconds=1),
            observed_at=AT, source_checkout=Path("/source"),
            installed_consumer_commit=CONSUMER_SHA,
        )
    with pytest.raises(mod.R06FreshCutoverBlocked, match="FUTURE_CLOSED_BOUNDARY"):
        mod.verify_r06_fresh_publication(
            ReadOnlyDB(), legacy_detached_at=BOUNDARY,
            observed_at=AT, source_checkout=Path("/source"),
            installed_consumer_commit=CONSUMER_SHA,
        )


def _proof() -> mod.R06FreshEntryProof:
    pin = _pin()
    return mod.R06FreshEntryProof(
        pin=pin, closed_5m_at=BOUNDARY,
        first_cursor=(104, 1),
        geometry={s: SimpleNamespace(effective_at=BOUNDARY) for s in SYMBOLS},
        exact_lookback={s: {"5": 2600, "15": 1000} for s in SYMBOLS},
        legacy_detached_at=DETACHED,
    )


class WriterDB:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.now = AT + timedelta(seconds=1)
        self.gate: tuple[bool, ...] = (False,)
        self.selected = (0,)
        self.open = (0,)
        self.pending_entry = (0,)
        self.pending_exit = (0,)
        self.source = ("ACTIVE", SOURCE_SHA)
        pin = _pin()
        self.pub = (
            pin.source_session_id, SOURCE_SHA, pin.as_of_second,
            pin.native_watermarks,
            {s: {"5": BOUNDARY.isoformat(), "15": BOUNDARY.isoformat()}
             for s in SYMBOLS},
        )
        self.inserted: list[str] = []
        self.fail_second = False
        self.expected_pub = 15200
        self.expected_cursor = (104, 1)

    @contextmanager
    def transaction(self):
        self.calls.append("BEGIN")
        try:
            yield self
        except Exception:
            self.inserted.clear()
            self.calls.append("ROLLBACK")
            raise
        else:
            self.calls.append("COMMIT")

    def execute(self, sql: str, params: object = ()) -> One:
        clean = sql.lstrip()
        self.calls.append(clean.split()[0].upper())
        if clean.startswith("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"):
            return One(None)
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            return One(self.source)
        if "FROM analytics.market_publications" in sql:
            return One(self.pub)
        if clean == "SELECT clock_timestamp()":
            return One((self.now,))
        if "FROM control.execution_gates" in sql:
            return One(self.gate)
        if "FROM strategy_entry.execution_permissions" in sql:
            return One(self.selected)
        if "FROM runtime.position_ownership" in sql:
            return One(self.open)
        if "FROM strategy_entry.execution_requests" in sql:
            return One(self.pending_entry)
        if "FROM strategy_exit.execution_requests" in sql:
            return One(self.pending_exit)
        if clean.startswith("INSERT INTO strategy_entry"):
            name = params[0]
            if self.fail_second and len(self.inserted) == 1:
                return One(None)
            self.inserted.append(name)
            assert params[-3:] == (
                self.expected_pub, self.expected_pub, BOUNDARY,
            )
            assert params[7:9] == self.expected_cursor
            return One((name,))
        pytest.fail("unexpected SQL")


def _bootstrap(
    db: WriterDB, *,
    refreshed: mod.R06FreshEntryProof | None = None,
    on_refresh: object = None,
) -> tuple[str, ...]:
    # The production implementation ALWAYS rechecks the pinned source AFTER
    # credential-local signing. Test snapshots must pin the same provenance.
    with pytest.MonkeyPatch.context() as patch:
        def read_fresh(_db: object, **_kw: object) -> mod.R06FreshEntryProof:
            if callable(on_refresh):
                on_refresh()
            return refreshed if refreshed is not None else _proof()
        patch.setattr(mod, "verify_r06_fresh_publication", read_fresh)
        return mod.bootstrap_future_entry_checkpoints(
            db, proof=_proof(), strategies=PLANS,
            consumer_release_commit=CONSUMER_SHA,
            active_p90_bundles=_bundles(),
        )


def test_one_signed_preflight_then_one_atomic_dual_strategy_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from operations.monitoring import p90l90_real_source_bootstrap as private

    db = WriterDB()
    auth: list[str] = []
    monkeypatch.setattr(
        private, "preflight_p90_real_private_flat_source",
        lambda *_a, **_k: auth.append("SIGNED_MAIN_SUB"),
    )
    result = _bootstrap(db, on_refresh=lambda: auth.append("FRESH_PIN"))
    assert result == (mod.MAIN_ID, mod.SUB_ID)
    assert auth == ["SIGNED_MAIN_SUB", "FRESH_PIN"]
    assert db.inserted == [mod.MAIN_ID, mod.SUB_ID]
    assert db.calls[0] == "BEGIN"
    assert db.calls[-1] == "COMMIT"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gate", (True,)), ("selected", (1,)), ("open", (1,)),
        ("pending_entry", (1,)), ("pending_exit", (1,)),
    ],
)
def test_pending_signed_disarmed_gates_prevent_any_bootstrap(
    field: str, value: object,
) -> None:
    db = WriterDB()
    setattr(db, field, value)
    with pytest.raises(
        mod.R06FreshCutoverBlocked, match="OPEN_OR_PENDING_OR_ARMED",
    ):
        _bootstrap(db)
    assert "ROLLBACK" in db.calls
    assert "INSERT" not in db.calls


def test_signed_proof_failure_does_not_open_a_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from operations.monitoring import p90l90_real_source_bootstrap as private

    db = WriterDB()

    def missing(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("SIGNED_MAIN_SUB_FLAT_PROOF_UNAVAILABLE")

    monkeypatch.setattr(
        private, "preflight_p90_real_private_flat_source", missing,
    )
    with pytest.raises(RuntimeError, match="SIGNED_MAIN_SUB"):
        _bootstrap(db)
    assert db.calls == []


def test_racing_publication_or_partial_insert_rolls_back_both() -> None:
    db = WriterDB()
    db.pub = ("f" * 32, *db.pub[1:])
    with pytest.raises(
        mod.R06FreshCutoverBlocked, match="STALE_OR_CHANGED",
    ):
        _bootstrap(db)
    assert "INSERT" not in db.calls
    db = WriterDB()
    db.fail_second = True
    with pytest.raises(
        mod.R06FreshCutoverBlocked, match="ALREADY_EXISTS",
    ):
        _bootstrap(db)
    assert db.inserted == []
    assert db.calls[-1] == "ROLLBACK"


def test_legacy_technical_ack_not_used_as_start_cursor() -> None:
    assert "native_public_trade_observer_delivery" not in (
        Path(mod.__file__).read_text(encoding="utf-8")
    )
    assert "ON CONFLICT DO NOTHING" in Path(mod.__file__).read_text(
        encoding="utf-8"
    )



def test_signed_slow_then_new_publication_fresh_snapshot_commits_atomically():
    db = WriterDB()
    # Original proof is stale AFTER a >5s signed MAIN/SUB call.
    db.now = AT + timedelta(seconds=12)
    latest = _pin()
    latest.publication_id = 15201
    latest.as_of_second = AT + timedelta(seconds=11)
    db.expected_pub = 15201
    db.expected_cursor = (222, 3)
    db.pub = (
        EPOCH, SOURCE_SHA, latest.as_of_second, latest.native_watermarks,
        {s: dict(latest.closed_refs[s]) for s in SYMBOLS},
    )
    refreshed = replace(
        _proof(), pin=latest, first_cursor=(222, 3),
    )
    result = _bootstrap(db, refreshed=refreshed)
    assert result == (mod.MAIN_ID, mod.SUB_ID)
    assert db.inserted == [mod.MAIN_ID, mod.SUB_ID]
    assert db.calls[0] == "BEGIN"
    assert db.calls[-1] == "COMMIT"


def test_signed_refresh_cannot_cross_source_epoch_or_rewind_closed_boundary():
    db = WriterDB()
    foreign = _pin()
    foreign.source_session_id = "f" * 32
    with pytest.raises(
        mod.R06FreshCutoverBlocked, match="SIGNED_REFRESH_EPOCH",
    ):
        _bootstrap(db, refreshed=replace(_proof(), pin=foreign))
    assert "INSERT" not in db.calls

    db = WriterDB()
    older = replace(
        _proof(), closed_5m_at=DETACHED,
    )
    with pytest.raises(
        mod.R06FreshCutoverBlocked, match="SIGNED_REFRESH_EPOCH",
    ):
        _bootstrap(db, refreshed=older)
    assert "INSERT" not in db.calls


def test_refresh_still_requires_five_second_freshness_without_exception():
    db = WriterDB()
    db.now = AT + timedelta(seconds=8)
    with pytest.raises(
        mod.R06FreshCutoverBlocked, match="START_PUBLICATION_STALE_OR_CHANGED",
    ):
        _bootstrap(db)
    assert "INSERT" not in db.calls
