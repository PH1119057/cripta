"""Canonical StrategyExecutionBinding allocation-mode policy.

Normal REAL execution remains DEDICATED_STRATEGY.  The only current exception
is the exact owner-controlled full-contour diagnostic v1, whose temporary
StrategyActivation may use SUBORDINATE_DIAGNOSTIC on the same TradingAccount as
its already-dedicated P90 parent.  This module does not prove the parent scope;
callers still enforce the diagnostic-specific parent binding/OWNER ARM gates.
"""
from __future__ import annotations

DEDICATED_STRATEGY = "DEDICATED_STRATEGY"
SUBORDINATE_DIAGNOSTIC = "SUBORDINATE_DIAGNOSTIC"

OWNER_FULL_CONTOUR_VERSION = "1.0-owner-full-contour-20261101"
OWNER_FULL_CONTOUR_STRATEGY_IDS = frozenset(
    {"owner_full_contour_diag_main", "owner_full_contour_diag_sub"}
)


def is_owner_full_contour_subordinate_identity(
    strategy_id: str,
    strategy_version: str,
) -> bool:
    return (
        strategy_version == OWNER_FULL_CONTOUR_VERSION
        and strategy_id in OWNER_FULL_CONTOUR_STRATEGY_IDS
    )


def real_binding_allocation_allowed(
    *,
    strategy_id: str,
    strategy_version: str,
    allocation_mode: str,
) -> bool:
    if allocation_mode == DEDICATED_STRATEGY:
        return True
    return (
        allocation_mode == SUBORDINATE_DIAGNOSTIC
        and is_owner_full_contour_subordinate_identity(strategy_id, strategy_version)
    )
