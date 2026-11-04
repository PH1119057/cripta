from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench import p90l90_real_prearm_evidence as prearm
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.p90l90_real_second48 import (
    P90RealSecond48Unverified,
)

AT = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
SECOND48 = {
    "enabled": True,
    "hardstop_count": 2,
    "rolling_hours": 48,
    "new_entry_pause_hours": 48,
    "close_existing_positions": False,
    "domain": "STRATEGY_VERSION",
}


class _Lifecycle:
    def to_dict(self) -> dict[str, object]:
        return {"second_hardstop_embargo": SECOND48}


def _bundle(strategy_id: str) -> object:
    return SimpleNamespace(
        card=SimpleNamespace(
            strategy_id=strategy_id,
            strategy_version=VERSION,
            strategy_config_fingerprint="fp-" + strategy_id,
            lifecycle_policy=_Lifecycle(),
        )
    )


def test_prearm_reconstructs_second48_for_both_exact_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_reader(_connection: object, **kwargs: object):
        calls.append(kwargs)
        strategy_id = str(kwargs["strategy_id"])
        return (
            strategy_id == MAIN_ID,
            {"strategy_id": strategy_id, "pause_until": None},
        )

    monkeypatch.setattr(prearm, "read_real_second48", fake_reader)
    bundles = {MAIN_ID: _bundle(MAIN_ID), SUB_ID: _bundle(SUB_ID)}
    routes = {
        MAIN_ID: (object(), "ta-main", "ac-main"),
        SUB_ID: (object(), "ta-sub", "ac-sub"),
    }

    evidence = prearm._second48_prearm_evidence(
        object(),
        bundles=bundles,
        routes=routes,
        now=AT,
    )

    assert [call["strategy_id"] for call in calls] == [MAIN_ID, SUB_ID]
    assert [call["trading_account_id"] for call in calls] == [
        "ta-main",
        "ta-sub",
    ]
    assert evidence[MAIN_ID]["allows_new_entry"] is True
    # Active embargo is valid reconstructed Strategy state, not PREARM failure.
    assert evidence[SUB_ID]["allows_new_entry"] is False


def test_prearm_blocks_before_gate_when_second48_cannot_be_reconstructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_reader(_connection: object, **_kwargs: object):
        raise P90RealSecond48Unverified("P90_REAL_SECOND48_ROW_NOT_PROVEN")

    monkeypatch.setattr(prearm, "read_real_second48", broken_reader)
    bundles = {MAIN_ID: _bundle(MAIN_ID), SUB_ID: _bundle(SUB_ID)}
    routes = {
        MAIN_ID: (object(), "ta-main", "ac-main"),
        SUB_ID: (object(), "ta-sub", "ac-sub"),
    }

    with pytest.raises(
        prearm.P90PrearmEvidenceBlocked,
        match="SECOND48 reconstruction failed.*ROW_NOT_PROVEN",
    ):
        prearm._second48_prearm_evidence(
            object(),
            bundles=bundles,
            routes=routes,
            now=AT,
        )


def test_prearm_source_persists_second48_inside_live_equivalence() -> None:
    source = prearm.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    assert "second48_evidence = _second48_prearm_evidence(" in text
    assert '"second48": second48_evidence' in text


@pytest.mark.parametrize("pending", [0, 1, 115])
def test_historical_delivery_backlog_reaches_current_account_checks(
    monkeypatch: pytest.MonkeyPatch, pending: int,
) -> None:
    _exercise_delivery_prearm(monkeypatch, pending=pending, active_faults=[])


def test_current_lifecycle_fault_still_blocks_prearm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _exercise_delivery_prearm(
        monkeypatch, pending=0,
        active_faults=["EXCHANGE_STATE_DIVERGED"],
    )


def _exercise_delivery_prearm(
    monkeypatch: pytest.MonkeyPatch, *, pending: int,
    active_faults: list[str],
) -> None:
    class ReachedCurrentChecks(Exception):
        pass

    class Connection:
        def execute(self, sql: str):
            if sql == "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE":
                return None
            assert "SELECT enabled FROM control.execution_gates" in sql
            raise ReachedCurrentChecks

    at = datetime.now(UTC)
    critical = {"configured": False, "pending_or_unacknowledged": pending}

    def read_status(path: Path, _label: str):
        if "universal_entry_observer" in str(path):
            return {
                "updated_at": at.isoformat(), "state": "RUNNING",
                "observer_ready": True, "source_commit": "release",
                "active_strategies": 2,
                "symbols": list(set().union(*prearm.ALLOWED_SYMBOLS.values())),
                "active_entry_plans": ["entry-main", "entry-sub"],
                "active_exit_plans": ["exit-main", "exit-sub"],
            }
        if "lifecycle_supervisor" in str(path):
            return {
                "state": "RUNNING", "active_fault_codes": active_faults,
                "critical_delivery": critical,
            }
        if "lifecycle_account" in str(path):
            return {"state": "VERIFIED_ACCOUNT_SCAN", "trading_rights": "NONE"}
        return {"state": "SAMPLED_PARTIAL", "trading_rights": "NONE"}

    monkeypatch.setattr(prearm, "_read_json", read_status)
    monkeypatch.setattr(prearm, "_release_and_parity_evidence", lambda **_kw: ({}, {}))
    monkeypatch.setattr(prearm, "_SERVICE_UNITS", ())
    expected = prearm.P90PrearmEvidenceBlocked if active_faults else ReachedCurrentChecks
    with pytest.raises(expected):
        prearm.prepare_p90l90_real_v3_prearm_evidence(
            Connection(), release_commit="release", now=at,
        )
    # No manufactured ACK/resolve/delivery PASS.
    assert critical == {"configured": False, "pending_or_unacknowledged": pending}
