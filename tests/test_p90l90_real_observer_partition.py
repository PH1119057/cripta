"""P90L90 REAL v3 is a separate observer owner, never PAPER-v2 fallback."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.contracts import StrategyActivation
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_observer_strategy_partition import (
    ObserverStrategySplit,
    partition_observer_strategies,
    require_p90_atomic_observer_connection,
    require_p90_policy_before_registry,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    VERSION as REAL_VERSION,
    real_v3_cards,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards

from datetime import UTC, datetime

AT = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
SECOND48 = StrategyHardStopEmbargoPolicy(
    enabled=True,
    hardstop_count=2,
    rolling_hours=48,
    new_entry_pause_hours=48,
    close_existing_positions=False,
    domain="STRATEGY_VERSION",
)


def _bundles(*, real: bool) -> tuple[object, ...]:
    make = real_v3_cards if real else new_version_cards
    cards = make(
        approved_at=AT,
        policy_by_strategy_id={MAIN_ID: SECOND48, SUB_ID: SECOND48},
    )
    bundles = []
    for card in cards:
        activation = StrategyActivation(
            activation_id=f"activation-{card.strategy_id}-{card.strategy_version}",
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            enabled=True,
            enabled_at=AT,
        )
        entry, exit_plan = materialize_plans(card, activation)
        bundles.append(SimpleNamespace(
            card=card,
            activation=activation,
            entry_plan=entry,
            exit_plan=exit_plan,
        ))
    return tuple(bundles)


def test_new_real_strategy_is_routed_to_an_explicitly_separate_owner() -> None:
    split = partition_observer_strategies(_bundles(real=True))
    assert split.ordinary == ()
    assert split.p90_paper == ()
    assert len(split.p90_real) == 2
    assert all(item.card.strategy_version == REAL_VERSION for item in split.p90_real)
    require_p90_policy_before_registry(split)
    with pytest.raises(RuntimeError, match="REAL_NATIVE_EXECUTION_OWNER_NOT_CONNECTED"):
        require_p90_atomic_observer_connection(split)


def test_historical_paper_strategy_remains_paper_only() -> None:
    split = partition_observer_strategies(_bundles(real=False))
    assert len(split.p90_paper) == 2
    assert split.p90_real == ()
    require_p90_policy_before_registry(split)


def test_same_symbol_cannot_be_owned_by_paper_and_real_simultaneously() -> None:
    v2 = _bundles(real=False)
    v3 = _bundles(real=True)
    with pytest.raises(RuntimeError, match="DUPLICATE_PAPER_REAL_SYMBOL_OWNER"):
        partition_observer_strategies((v2[0], v3[0]))


def test_ordinary_only_split_cannot_accidentally_route_real() -> None:
    split = ObserverStrategySplit((), (), ())
    assert split.p90_real == ()
    require_p90_atomic_observer_connection(split)
