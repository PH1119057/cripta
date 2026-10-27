"""Owner-approved immutable P90L90 Strategy v2 authoring after SECOND48 decision.

Safe construction ONLY. Does not overwrite existing passive v1 DB snapshots,
install an activation, select leverage or execution order type, arm Exchange,
or claim full 90-day cross-portfolio replay equivalence.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from .contracts import StrategyCard
from .dashboard_control import card_from_editable
from .p90l90_legacy_strategy import (
    MAIN_ID,
    MAIN_SYMBOLS,
    SUB_ID,
    SUB_SYMBOLS,
    exact_legacy_policy,
)
from .p90l90_strategy_embargo import StrategyHardStopEmbargoPolicy

VERSION = "1.1-p90l90-strategy-second48-20261028"
OWNER_SOURCE = "owner-2026-10-28-second48-issue-243"


def v2_authoring_payload(
    *, strategy_id: str, policy: StrategyHardStopEmbargoPolicy
) -> dict[str, object]:
    if strategy_id not in {MAIN_ID, SUB_ID}:
        raise ValueError("P90L90 v2 Strategy identity not owner-approved")
    symbols = MAIN_SYMBOLS if strategy_id == MAIN_ID else SUB_SYMBOLS
    payload = exact_legacy_policy(strategy_id=strategy_id, symbols=symbols)
    payload["strategy_version"] = VERSION
    payload["description"] = (
        "P90L90 immutable independent LEGACY v2, Strategy-specific "
        "configurable hard-stop embargo, no REAL capital/order approval. "
        "No activation until complete SHADOW causal/parity proof."
    )
    lifecycle = payload["lifecycle_policy"]
    assert isinstance(lifecycle, dict)
    lifecycle["second_hardstop_embargo"] = policy.snapshot()
    return payload


def new_version_cards(
    *,
    approved_at: datetime,
    policy_by_strategy_id: Mapping[str, StrategyHardStopEmbargoPolicy],
    approved_source: str = OWNER_SOURCE,
) -> tuple[StrategyCard, StrategyCard]:
    if approved_at.tzinfo is None:
        raise ValueError("P90L90 v2 owner decision must be timestamp aware")
    if set(policy_by_strategy_id) != {MAIN_ID, SUB_ID}:
        raise ValueError("P90L90 v2 requires both exact independent Strategy policies")
    when = approved_at.astimezone(UTC)
    cards = tuple(
        card_from_editable(
            v2_authoring_payload(strategy_id=ident, policy=policy_by_strategy_id[ident]),
            approved_at=when,
            approved_source=approved_source,
        )
        for ident in (MAIN_ID, SUB_ID)
    )
    assert len({card.strategy_config_fingerprint for card in cards}) == 2
    return cards  # type: ignore[return-value]
