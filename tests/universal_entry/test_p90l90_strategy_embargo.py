"""Independent immutable Strategy-scoped SECOND48 rules; no DB or Exchange."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargo,
    StrategyHardStopEmbargoPolicy,
)

T0 = datetime(2026, 10, 28, 0, 0, tzinfo=UTC)
DEFAULT = {
    "enabled": True,
    "hardstop_count": 2,
    "rolling_hours": 48,
    "new_entry_pause_hours": 48,
    "close_existing_positions": False,
    "domain": "STRATEGY_VERSION",
}


def tracker(strategy: str, config=None) -> StrategyHardStopEmbargo:
    return StrategyHardStopEmbargo(
        strategy_id=strategy,
        strategy_version="1.1-owner-20261028",
        strategy_config_fingerprint=strategy + "-fingerprint",
        policy=StrategyHardStopEmbargoPolicy.parse(DEFAULT if config is None else config),
    )


def test_p90l90_two_hardstops_pause_new_entry_only_with_independent_strategies():
    main = tracker("p90l90_legacy_main_ltc_inj")
    sub = tracker("p90l90_legacy_sub_dot_arb_apt")
    assert main.allows_new_entry(T0)
    main.record_exit(exit_event_id="main-1", exit_reason="HARD_STOP", at=T0)
    main.record_exit(
        exit_event_id="trailing", exit_reason="PROTECTED_STOP",
        at=T0+timedelta(hours=1),
    )
    assert main.allows_new_entry(T0+timedelta(hours=1))
    main.record_exit(
        exit_event_id="main-2", exit_reason="HARD_STOP",
        at=T0+timedelta(hours=2),
    )
    assert not main.allows_new_entry(T0+timedelta(hours=3))
    assert main.pause_until == T0+timedelta(hours=50)
    assert sub.allows_new_entry(T0+timedelta(hours=3))
    sub.record_exit(
        exit_event_id="sub-1", exit_reason="HARD_STOP",
        at=T0+timedelta(hours=3),
    )
    assert sub.allows_new_entry(T0+timedelta(hours=4))
    assert main.allows_new_entry(T0+timedelta(hours=50))


def test_strategy_embargo_restores_exact_identity_and_policy():
    main = tracker("MAIN")
    main.record_exit(exit_event_id="one", exit_reason="HARD_STOP", at=T0)
    main.record_exit(
        exit_event_id="two", exit_reason="HARD_STOP",
        at=T0+timedelta(seconds=10),
    )
    raw = main.snapshot()
    after = StrategyHardStopEmbargo.restore(
        raw, strategy_id="MAIN", strategy_version=main.strategy_version,
        strategy_config_fingerprint=main.strategy_config_fingerprint,
        policy=main.policy,
    )
    assert after.snapshot() == raw
    assert after.record_exit(
        exit_event_id="two", exit_reason="HARD_STOP",
        at=T0+timedelta(seconds=10),
    ) is False
    assert after.pause_until == main.pause_until
    with pytest.raises(ValueError, match="identity mismatch"):
        StrategyHardStopEmbargo.restore(
            raw, strategy_id="SUB", strategy_version=main.strategy_version,
            strategy_config_fingerprint=main.strategy_config_fingerprint,
            policy=main.policy,
        )
    changed = {**DEFAULT, "new_entry_pause_hours": 12}
    with pytest.raises(ValueError, match="policy version mismatch"):
        StrategyHardStopEmbargo.restore(
            raw, strategy_id="MAIN", strategy_version=main.strategy_version,
            strategy_config_fingerprint=main.strategy_config_fingerprint,
            policy=StrategyHardStopEmbargoPolicy.parse(changed),
        )
    duplicate = deepcopy(raw)
    duplicate["hard_stops"].append(duplicate["hard_stops"][0])
    with pytest.raises(ValueError, match="duplicate"):
        StrategyHardStopEmbargo.restore(
            duplicate, strategy_id="MAIN", strategy_version=main.strategy_version,
            strategy_config_fingerprint=main.strategy_config_fingerprint,
            policy=main.policy,
        )


def test_configurable_threshold_window_pause_disabled_and_no_silent_old_contract():
    custom = {**DEFAULT, "hardstop_count": 3, "rolling_hours": 6,
              "new_entry_pause_hours": 2}
    state = tracker("strategy-custom", custom)
    for index, offset in enumerate((0, 5, 11)):
        state.record_exit(
            exit_event_id=f"hard-{index}", exit_reason="HARD_STOP",
            at=T0+timedelta(hours=offset),
        )
    assert state.allows_new_entry(T0+timedelta(hours=12))
    state.record_exit(
        exit_event_id="hard-3", exit_reason="HARD_STOP",
        at=T0+timedelta(hours=12),
    )
    assert not state.allows_new_entry(T0+timedelta(hours=13))
    assert state.allows_new_entry(T0+timedelta(hours=14))
    disabled = tracker("strategy-off", {**DEFAULT, "enabled": False})
    for i in range(4):
        disabled.record_exit(
            exit_event_id=str(i), exit_reason="HARD_STOP",
            at=T0+timedelta(minutes=i),
        )
    assert disabled.allows_new_entry(T0+timedelta(hours=1))
    for invalid in (
        {**DEFAULT, "domain": "UNRESOLVED_OWNER_CONTRACT"},
        {**DEFAULT, "domain": "MAIN_PLUS_SUB"},
        {**DEFAULT, "hardstop_count": 0},
        {**DEFAULT, "rolling_hours": True},
        {**DEFAULT, "new_entry_pause_hours": 0},
        {**DEFAULT, "close_existing_positions": True},
    ):
        with pytest.raises(ValueError):
            StrategyHardStopEmbargoPolicy.parse(invalid)
