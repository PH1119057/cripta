"""Independent P90 Strategy consumer never reuses an ordinary Strategy engine."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_observer_paper_consumer import (
    P90L90ObserverPaperConsumer,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards
from bybit_workbench.universal_entry.runtime_loader import ActiveStrategyBundle
from bybit_workbench.universal_entry.materializer import materialize_plans

B = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
SHA = "3" * 40


def _bundle(ident: str) -> ActiveStrategyBundle:
    embargo = StrategyHardStopEmbargoPolicy.parse({
        "enabled": True, "hardstop_count": 2,
        "rolling_hours": 48, "new_entry_pause_hours": 48,
        "close_existing_positions": False, "domain": "STRATEGY_VERSION",
    })
    card = next(
        c for c in new_version_cards(
            approved_at=B, policy_by_strategy_id={
                MAIN_ID: embargo, SUB_ID: embargo,
            },
        ) if c.strategy_id == ident
    )
    activation = StrategyActivation(
        activation_id="activation-"+ident,
        strategy_id=card.strategy_id, strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True, enabled_at=B,
    )
    entry, exit_ = materialize_plans(card, activation)
    return ActiveStrategyBundle(card, activation, entry, exit_, B)


def _consumer(bundles):
    return P90L90ObserverPaperConsumer(
        object(), bundles=bundles, source_commit=SHA,
        provenance=FrozenPolicy.from_mapping({"source": "test-no-exchange"}),
    )


def test_p90_has_unique_separate_entry_engine_and_five_symbol_owners():
    main, sub = _bundle(MAIN_ID), _bundle(SUB_ID)
    c = _consumer((main,sub))
    assert set(c._by_symbol) == set(main.card.symbols)|set(sub.card.symbols)
    assert len(c._processors) == 5
    assert c._recovered is False
    with pytest.raises(RuntimeError, match="NOT_RESTORED"):
        c.accept(object())


def test_p90_duplicate_strategy_symbol_or_disabled_activation_rejected():
    main = _bundle(MAIN_ID)
    with pytest.raises(RuntimeError, match="DUPLICATE_SYMBOL_OWNER"):
        _consumer((main, main))
    from dataclasses import replace
    disabled = replace(main, activation=replace(main.activation,enabled=False))
    with pytest.raises(RuntimeError, match="UNVERIFIED_EXACT_STRATEGY"):
        _consumer((disabled,))


def test_p90_consumer_cannot_be_created_without_owner_or_exact_version():
    with pytest.raises(ValueError, match="needs exact"):
        _consumer(())
    from dataclasses import replace
    main = _bundle(MAIN_ID)
    changed = replace(main, card=replace(main.card,strategy_version="historical-v1"))
    with pytest.raises(RuntimeError, match="UNVERIFIED_EXACT_STRATEGY"):
        _consumer((changed,))
