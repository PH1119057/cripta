from __future__ import annotations

from datetime import UTC, datetime
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
    text = open(source, encoding="utf-8").read()
    assert "second48_evidence = _second48_prearm_evidence(" in text
    assert '"second48": second48_evidence' in text
