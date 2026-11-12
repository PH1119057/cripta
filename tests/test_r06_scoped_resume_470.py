"""R06 manifest, affected-only waits and preserved causal recovery contracts."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.market_identity import bybit_instrument_id
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from operations.monitoring import r06_entry_resume as resume
from operations.monitoring import r06_pg_only_entry as worker

NOW = datetime(2026, 11, 12, 6, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_the_source_ttl_authority_and_signal_ttl_is_separate():
    ttls = resume.entry_native_ttls(
        ROOT / "operations/monitoring/required_public_feeds.json", worker.FIVE,
    )
    assert ttls == {symbol: timedelta(seconds=90) for symbol in worker.FIVE}
    assert worker._FRESH == worker._MAX_LATE_SIGNAL == timedelta(seconds=5)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "optional", "wrong_product"])
def test_manifest_scope_cannot_be_guessed(tmp_path, defect):
    rows = [dict(
        consumer_id="ENTRY_P90", market_source_id="ms-bybit-mainnet-public-v1",
        instrument_id=bybit_instrument_id("linear", symbol), product="LINEAR",
        symbol=symbol, dataset="PUBLIC_TRADE", freshness_seconds=90,
    ) for symbol in worker.FIVE]
    if defect == "missing":
        rows.pop()
    elif defect == "duplicate":
        rows.append(rows[0])
    elif defect == "optional":
        rows[0]["required"] = False
    else:
        rows[0]["product"] = "SPOT"
        rows[0]["instrument_id"] = bybit_instrument_id("spot", rows[0]["symbol"])
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(resume.R06ResumeBlocked):
        resume.entry_native_ttls(path, worker.FIVE)


class StateReader:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, *_args):
        return SimpleNamespace(fetchall=lambda: [(NOW-timedelta(minutes=45), self.raw)])


def test_backlog_reads_extra_original_geometry_without_moving_the_cursor():
    old = NOW - timedelta(minutes=45)
    db = StateReader({"version": 1, "by_symbol": {"LTCUSDT": {
        "geometry_at": old.isoformat(),
    }}})
    assert resume.recovery_counts(
        db, session="s", symbol="LTCUSDT", closed_at=NOW,
        counts={"5": 2600, "15": 1000},
    ) == {"5": 2609, "15": 1003}


def test_unbounded_or_future_recovery_is_never_silently_accepted():
    for at in (NOW - timedelta(days=2), NOW + timedelta(minutes=5)):
        db = StateReader({"version": 1, "by_symbol": {"LTCUSDT": {
            "geometry_at": at.isoformat(),
        }}})
        with pytest.raises(resume.R06ResumeBlocked):
            resume.recovery_counts(
                db, session="s", symbol="LTCUSDT", closed_at=NOW,
                counts={"5": 2600, "15": 1000},
            )


class Transaction:
    @contextmanager
    def transaction(self):
        yield self


@pytest.mark.parametrize("stale", [None, MAIN_ID, SUB_ID, "both"])
def test_quiet_scope_waits_without_advancing_or_blocking_another(monkeypatch, stale):
    observed = []
    ttls = {symbol: timedelta(seconds=90) for symbol in worker.FIVE}
    bundles = tuple(SimpleNamespace(card=SimpleNamespace(strategy_id=s))
                    for s in (MAIN_ID, SUB_ID))

    def source(_db, *, symbols, max_lag, **_kw):
        assert max_lag == timedelta(seconds=90)
        name = MAIN_ID if "LTCUSDT" in symbols else SUB_ID
        ready = stale not in (name, "both")
        return SimpleNamespace(ready=ready, by_symbol={s: SimpleNamespace(
            ready=ready, reason="NATIVE_SOURCE_CURSOR_NOT_FRESH",
        ) for s in symbols})

    def observe(_db, *, bundle, **_kw):
        observed.append(bundle.card.strategy_id)
        return 0

    monkeypatch.setattr(worker, "read_scoped_native_source", source)
    monkeypatch.setattr(worker, "_require_no_exposure_or_requests", lambda _db: None)
    monkeypatch.setattr(worker, "_observe_one", observe)
    monkeypatch.setattr(resume, "scope_waits_at_bootstrap", lambda *_a, **_kw: False)
    _, blocked = worker._process_publication(
        Transaction(), pin=object(), history={}, bundles=bundles,
        engines={MAIN_ID: None, SUB_ID: None}, commits=frozenset(), loaded="d"*40,
        ttls=ttls,
    )
    expected = {MAIN_ID, SUB_ID} if stale == "both" else ({stale} if stale else set())
    assert set(blocked) == expected
    assert set(observed) == {MAIN_ID, SUB_ID} - expected
    # New source evidence can resume the SAME checkpoint on a later publication.
    stale = None
    _, blocked = worker._process_publication(
        Transaction(), pin=object(), history={}, bundles=bundles,
        engines={MAIN_ID: None, SUB_ID: None}, commits=frozenset(), loaded="d"*40,
        ttls=ttls,
    )
    assert not blocked


@pytest.mark.parametrize("reason", [
    "NATIVE_SOURCE_JOURNAL_IDENTITY_DRIFT", "NATIVE_SOURCE_CURSOR_MISSING",
    "NATIVE_SOURCE_RELEASE_NOT_APPROVED", "NATIVE_SOURCE_ACTIVE_SESSION_AMBIGUOUS",
])
def test_provenance_failure_is_never_treated_as_quiet_wait(monkeypatch, reason):
    monkeypatch.setattr(worker, "_require_no_exposure_or_requests", lambda _db: None)
    monkeypatch.setattr(worker, "read_scoped_native_source", lambda *_a, **_kw:
                        SimpleNamespace(ready=False, by_symbol={"x":
                            SimpleNamespace(ready=False, reason=reason)}))
    with pytest.raises(worker.R06PublishedEntryBlocked):
        worker._process_publication(
            Transaction(), pin=object(), history={},
            bundles=(SimpleNamespace(card=SimpleNamespace(strategy_id=MAIN_ID)),),
            engines={}, commits=frozenset(), loaded="d"*40,
            ttls={s: timedelta(seconds=90) for s in worker.FIVE},
        )


def test_semantic_bridge_refuses_unapproved_release_or_changed_engine(monkeypatch):
    with pytest.raises(resume.R06ResumeBlocked, match="RELEASE_NOT_APPROVED"):
        resume.verify_semantic_compatibility(Path("."), "a"*40, "b"*40)
    monkeypatch.setattr(resume.subprocess, "check_output", lambda argv, **_kw:
                        (b"a"*40 if resume.APPROVED_BASELINE in argv[-1] else b"b"*40))
    with pytest.raises(resume.R06ResumeBlocked, match="SEMANTICS_CHANGED"):
        resume.verify_semantic_compatibility(Path("."), resume.APPROVED_BASELINE, "b"*40)


@pytest.mark.parametrize('defect', [None, 'cursor', 'epoch', 'producer', 'older_pub', 'nonempty'])
def test_quiet_bootstrap_wait_requires_exact_untouched_original_fence(defect):
    watermarks = {'LTCUSDT': {'frame_number': 98, 'frame_index': 0},
                  'APTUSDT': {'frame_number': 100, 'frame_index': 2}}
    saved = [100, 2, 10, {'version': 1, 'by_symbol': {}}]
    original = ['session', 'producer', watermarks]
    pin = SimpleNamespace(source_session_id='session', producer_release_commit='producer',
                          publication_id=20, native_watermarks=watermarks)
    if defect == 'cursor':
        saved[0] = 101
    elif defect == 'epoch':
        original[0] = 'other'
    elif defect == 'producer':
        original[1] = 'other'
    elif defect == 'older_pub':
        pin.publication_id = 9
    elif defect == 'nonempty':
        saved[3]['by_symbol']['LTCUSDT'] = {'previous_price': '1'}
    class Reader:
        def execute(self, sql, params):
            assert sql.startswith('SELECT')
            row = tuple(original if 'FROM analytics.market_publications' in sql else saved)
            return SimpleNamespace(fetchone=lambda: row)
    kwargs = dict(pin=pin, strategy=MAIN_ID, version='v3', symbols=('LTCUSDT',))
    if defect in ('cursor', 'epoch', 'producer', 'older_pub'):
        with pytest.raises(resume.R06ResumeBlocked, match='FENCE_UNVERIFIED'):
            resume.scope_waits_at_bootstrap(Reader(), **kwargs)
    else:
        assert resume.scope_waits_at_bootstrap(Reader(), **kwargs) is (defect is None)
