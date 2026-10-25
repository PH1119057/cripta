"""Initial-protection observation must respect the exact Strategy policy.

These checks do not substitute for the independent authenticated Exchange
verification performed by Lifecycle Supervisor.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from bybit_workbench.exchange.bybit.protection_intent import (
    initial_protection_surfaces_observed,
)


def _policy(*, tp_required: bool = True) -> dict[str, object]:
    return {"stop_loss_enabled": True, "take_profit_enabled": tp_required}


def _observed(*, stop: str = "90", tp: str = "110") -> dict[str, object]:
    return {"stopLoss": stop, "takeProfit": tp}


def test_real_initial_stop_and_tp_surfaces_are_both_required_when_enabled() -> None:
    assert initial_protection_surfaces_observed(
        initial_protection=_policy(), position=_observed(),
    )
    assert not initial_protection_surfaces_observed(
        initial_protection=_policy(), position=_observed(stop="0"),
    )
    assert not initial_protection_surfaces_observed(
        initial_protection=_policy(), position=_observed(tp="0"),
    )


def test_strategy_may_approve_stop_only_without_inventing_tp() -> None:
    assert initial_protection_surfaces_observed(
        initial_protection=_policy(tp_required=False), position=_observed(tp="0"),
    )
    assert not initial_protection_surfaces_observed(
        initial_protection=_policy(tp_required=False),
        position=_observed(stop="0", tp="110"),
    )


def test_no_positive_marker_on_missing_ambiguous_or_invalid_policy() -> None:
    for policy in (
        None,
        {},
        {"stop_loss_enabled": False, "take_profit_enabled": True},
        {"stop_loss_enabled": True},
        {"stop_loss_enabled": True, "take_profit_enabled": "false"},
    ):
        assert not initial_protection_surfaces_observed(
            initial_protection=policy, position=_observed(),
        )


def test_nonfinite_and_malformed_exchange_prices_never_confirm() -> None:
    for value in ("NaN", "Infinity", "-1", "", "oops", None, Decimal("0")):
        assert not initial_protection_surfaces_observed(
            initial_protection=_policy(), position={"stopLoss": value, "takeProfit": "110"},
        )
        assert not initial_protection_surfaces_observed(
            initial_protection=_policy(), position={"stopLoss": "90", "takeProfit": value},
        )


def test_reconciler_requires_exact_proven_entry_and_keeps_supervisor_owner() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "operations/connectivity/private_runtime.py"
    ).read_text(encoding="utf-8")
    body = source.split("def reconcile_position_ownership(", 1)[1]
    assert "initial_protection_surfaces_observed(" in body
    assert "current_stop > 0" not in body.split("def ", 1)[0]
    assert "entry_command = connection.execute(" in body
    assert "command_id=%s AND command_type='entry'" in body
    assert "identity_status='PROVEN'" in body
    supervisor = (
        Path(__file__).resolve().parents[1] / "src/bybit_workbench/lifecycle_supervisor.py"
    ).read_text(encoding="utf-8")
    assert 'if protection_state != "VERIFIED":' in supervisor
