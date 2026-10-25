"""Release regression: one valid BOUND owner never conceals an extra OPEN owner.

Pure, read-only synthetic test. No Exchange credential or database required.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bybit_workbench.exchange.bybit.exposure_audit import (
    ExposureInventoryUnavailable,
    audit_account_exposure,
)
from bybit_workbench.lifecycle_exposure_bridge import unbound_strategy_claims

_ACCOUNT = "ta-multiowner-test"
_SYMBOL = "BTCUSDT"
_KEY = f"BYBIT:{_ACCOUNT}:LINEAR:USDT:{_SYMBOL}:0"
_NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


def _exchange() -> dict[str, object]:
    return {
        "source": "BYBIT_AUTHENTICATED_ACCOUNT_GET",
        "account_ref": "BYBIT:UNIFIED",
        "account_user_id": "123456789",
        "observed_at": _NOW.isoformat(),
        "complete": True,
        "account_wide_pagination_complete": True,
        "account_wide_positions": [
            {"symbol": _SYMBOL, "positionIdx": 0, "side": "Buy", "size": "1"},
        ],
    }


def _claim() -> dict[str, object]:
    return {
        "exchange_position_slot_claim_id": "claim-a",
        "trading_account_id": _ACCOUNT,
        "identity_status": "PROVEN",
        "claim_state": "BOUND",
        "symbol": _SYMBOL,
        "position_idx": 0,
        "exchange_position_key": _KEY,
        "strategy_position_id": "position-a",
    }


def _owner(pid: str, side: str = "Buy") -> dict[str, object]:
    return {
        "position_id": pid,
        "trading_account_id": _ACCOUNT,
        "identity_status": "PROVEN",
        "state": "OPEN",
        "symbol": _SYMBOL,
        "position_idx": 0,
        "exchange_position_key": _KEY,
        "side": side,
    }


def _faults(owners: list[dict[str, object]], *, with_claim: bool = True):
    observed = audit_account_exposure(
        account_snapshot=_exchange(),
        trading_account_id=_ACCOUNT,
        active_slot_claims=[_claim()] if with_claim else [],
        open_owner_rows=owners,
        now=_NOW,
    )
    exposure = [
        {
            "account_identity_binding": "VERIFIED",  # synthetic-only provenance
            "trading_account_id": row.trading_account_id,
            "symbol": row.symbol,
            "position_idx": row.position_idx,
            "active_strategy_claim_ids": row.active_strategy_claim_ids,
            "matching_open_owner_ids": row.matching_open_owner_ids,
            "open_owner_ids": row.open_owner_ids,
        }
        for row in observed
    ]
    return unbound_strategy_claims(exposure, account_id=_ACCOUNT)


def test_exactly_one_bound_claim_and_one_open_owner_is_healthy():
    assert _faults([_owner("position-a")]) == ()


@pytest.mark.parametrize("second_side", ["Buy", "Sell"])
def test_extra_open_owner_must_be_detected_even_when_one_bound_pair_matches(second_side: str):
    faults = _faults([_owner("position-a"), _owner("position-b", second_side)])
    assert len(faults) == 1
    assert faults[0]["matching_open_owner_ids"] == ("position-a",)
    assert faults[0]["open_owner_ids"] == ("position-a", "position-b")


def test_claimless_exchange_position_without_local_owner_is_not_strategy_fault():
    assert _faults([], with_claim=False) == ()


def test_claimless_position_with_one_local_owner_does_not_prove_conflict():
    assert _faults([_owner("position-a")], with_claim=False) == ()


@pytest.mark.parametrize("second_side", ["Buy", "Sell"])
def test_claimless_slot_with_two_open_owners_is_still_invariant_broken(second_side: str):
    faults = _faults([_owner("position-a"), _owner("position-b", second_side)], with_claim=False)
    assert len(faults) == 1
    assert faults[0]["exchange_position_slot_claim_ids"] == ()
    assert faults[0]["open_owner_ids"] == ("position-a", "position-b")


def test_missing_all_open_owners_field_is_not_silently_accepted():
    # An incomplete/older bridge row must not be treated as verified healthy.
    with pytest.raises(ExposureInventoryUnavailable):
        unbound_strategy_claims(
            [
                {
                    "account_identity_binding": "VERIFIED",
                    "trading_account_id": _ACCOUNT,
                    "symbol": _SYMBOL,
                    "position_idx": 0,
                    "active_strategy_claim_ids": ("claim-a",),
                    "matching_open_owner_ids": ("position-a",),
                }
            ],
            account_id=_ACCOUNT,
        )
