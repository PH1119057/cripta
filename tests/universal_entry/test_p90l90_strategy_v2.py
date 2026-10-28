"""Immutable P90L90 v2 test: each Strategy owns its own SECOND48."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy
from bybit_workbench.universal_entry.p90l90_legacy_strategy import (
    MAIN_ID,
    SUB_ID,
    build_passive_legacy_cards,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import (
    VERSION,
    new_version_cards,
    v2_authoring_payload,
)
from bybit_workbench.universal_entry.readiness import assess_strategy_runtime_readiness

WHEN = datetime(2026, 10, 28, 0, 0, tzinfo=UTC)


def embargo(hours: int):
    return StrategyHardStopEmbargoPolicy.parse({
        "enabled": True,
        "hardstop_count": 2,
        "rolling_hours": 48,
        "new_entry_pause_hours": hours,
        "close_existing_positions": False,
        "domain": "STRATEGY_VERSION",
    })


def test_two_strategy_versions_are_distinct_and_v1_unchanged():
    old = build_passive_legacy_cards(approved_at=WHEN)
    created = new_version_cards(
        approved_at=WHEN, policy_by_strategy_id={
            MAIN_ID: embargo(48), SUB_ID: embargo(12)
        },
    )
    assert {x.strategy_id for x in created} == {MAIN_ID, SUB_ID}
    assert all(x.strategy_version == VERSION for x in created)
    assert all(x.strategy_config_fingerprint != old[i].strategy_config_fingerprint
               for i, x in enumerate(created))
    assert created[0].lifecycle_policy.to_dict()[
        "second_hardstop_embargo"
    ]["new_entry_pause_hours"] == 48
    assert created[1].lifecycle_policy.to_dict()[
        "second_hardstop_embargo"
    ]["new_entry_pause_hours"] == 12
    assert old[0].lifecycle_policy.to_dict()[
        "second_hardstop_embargo"
    ]["cross_strategy_domain"] == "UNRESOLVED_OWNER_CONTRACT"
    assert old[1].lifecycle_policy.to_dict()[
        "second_hardstop_embargo"
    ]["cross_strategy_domain"] == "UNRESOLVED_OWNER_CONTRACT"
    assert v2_authoring_payload(
        strategy_id=MAIN_ID, policy=embargo(48)
    )["entry_policy"]["execution_policy"]["order_type"] == "NOT_APPROVED"


def test_strategy_v2_refuses_missing_or_cross_account_policy():
    with pytest.raises(ValueError, match="both exact"):
        new_version_cards(
            approved_at=WHEN, policy_by_strategy_id={MAIN_ID: embargo(48)}
        )
    with pytest.raises(ValueError, match="not owner-approved"):
        v2_authoring_payload(strategy_id="historical-r1", policy=embargo(48))


def test_exact_v2_model_remains_paper_and_real_blocked_until_ordered_boundary_proof():
    main, sub = new_version_cards(
        approved_at=WHEN, policy_by_strategy_id={
            MAIN_ID: embargo(48), SUB_ID: embargo(48)
        },
    )
    for card in (main, sub):
        checked = assess_strategy_runtime_readiness(card, observer_ready=True)
        assert checked.policy_ready is True
        assert checked.paper_ready is False
        assert checked.active_ready is False
        assert checked.execution_ready is False
        assert "P90L90_NATIVE_BAR_BOUNDARY_ORDER_NOT_PROVEN" in {
            reason.code for reason in checked.reasons
        }
        assert "P90L90_EXCHANGE_ORDER_NOT_APPROVED" in {
            reason.code for reason in checked.reasons
        }
        without_observer = assess_strategy_runtime_readiness(
            card, observer_ready=False
        )
        assert not without_observer.paper_ready
        assert not without_observer.active_ready
        assert not without_observer.execution_ready

    old = build_passive_legacy_cards(approved_at=WHEN)
    assert all(
        not assess_strategy_runtime_readiness(
            card, observer_ready=True
        ).active_ready
        for card in old
    )


def test_tampered_v2_paper_capital_remains_fail_closed():
    main, _ = new_version_cards(
        approved_at=WHEN, policy_by_strategy_id={
            MAIN_ID: embargo(48), SUB_ID: embargo(48)
        },
    )
    policy = main.capital_policy.to_dict()
    policy["paper_simulation"]["notional_usdt"] = "100"
    broken = replace(
        main, capital_policy=FrozenPolicy.from_mapping(policy)
    )
    state = assess_strategy_runtime_readiness(broken, observer_ready=True)
    assert state.active_ready is False
    assert state.execution_ready is False
    assert "P90L90_PAPER_MODEL_OR_PRODUCER_NOT_PROVEN" in {
        reason.code for reason in state.reasons
    }


def test_p90l90_tampered_exit_distance_or_geometry_never_ready():
    main, _ = new_version_cards(
        approved_at=WHEN, policy_by_strategy_id={
            MAIN_ID: embargo(48), SUB_ID: embargo(48)
        },
    )
    exit_policy = main.exit_policy.to_dict()
    exit_policy["trailing"]["distance_pct"] = "0.2"
    changed_exit = replace(
        main, exit_policy=FrozenPolicy.from_mapping(exit_policy)
    )
    assert not assess_strategy_runtime_readiness(
        changed_exit, observer_ready=True
    ).paper_ready
    entry = main.entry_policy.to_dict()
    entry["watch_policy"]["geometry"]["depth_max_pct"] = "25"
    changed_entry = replace(
        main, entry_policy=FrozenPolicy.from_mapping(entry)
    )
    assert not assess_strategy_runtime_readiness(
        changed_entry, observer_ready=True
    ).active_ready
