from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.p90_readiness_view import observer_arm_ready, technical_preparation_fresh

NOW = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)


def observer():
    return dict(state="RUNNING", observer_ready=True, source_commit="release",
                runtime_mode="MULTI_STRATEGY_OBSERVER", updated_at=NOW.isoformat())


@pytest.mark.parametrize("change", [
    {"state": "PREPARING"}, {"state": "RECONNECTING"}, {"state": "ERROR"},
    {"state": "RELOADING"}, {"observer_ready": False}, {"source_commit": "old"},
    {"updated_at": (NOW-timedelta(seconds=11)).isoformat()},
    {"updated_at": (NOW+timedelta(seconds=1)).isoformat()},
    {"updated_at": "bad"}, {"updated_at": "2026-11-04T18:00:00"},
])
def test_observer_not_ready_disables_arm(change):
    assert observer_arm_ready(observer(), "release", NOW)
    assert not observer_arm_ready(observer() | change, "release", NOW)


def test_preparation_requires_current_release_success_and_finite_freshness():
    op = dict(state="READY_FOR_OWNER_ARM", release_commit="release", completed_at=NOW)
    assert technical_preparation_fresh(op, "release", NOW)
    assert technical_preparation_fresh(op, "release", NOW+timedelta(seconds=90))
    assert not technical_preparation_fresh(op, "release", NOW+timedelta(seconds=91))
    assert not technical_preparation_fresh(op, "new", NOW)
    assert not technical_preparation_fresh(op | {"state": "BLOCKED"}, "release", NOW)
    assert not technical_preparation_fresh(op | {"completed_at": None}, "release", NOW)
    assert not technical_preparation_fresh(None, "release", NOW)
