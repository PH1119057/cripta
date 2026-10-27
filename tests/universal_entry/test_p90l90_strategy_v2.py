"""Immutable P90L90 v2 test: each Strategy owns its own SECOND48."""
from datetime import UTC, datetime

import pytest

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
