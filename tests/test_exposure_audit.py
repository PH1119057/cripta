"""Synthetic offline examples. Not authorization, not PostgreSQL/Bybit E2E."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.exchange.bybit.exposure_audit import (
    ExposureInventoryUnavailable,
    audit_account_exposure,
)

NOW = datetime(2026, 10, 24, 0, 0, 0, tzinfo=UTC)


def p(symbol="TRXUSDT", *, size="2", side="Buy", idx=0):
    return {"symbol": symbol, "positionIdx": idx, "size": size, "side": side}


def snapshot(rows, *, age=0, full=True, uid="4242"):
    return {
        "source": "BYBIT_AUTHENTICATED_ACCOUNT_GET",
        "complete": True,
        "account_wide_pagination_complete": full,
        "account_ref": "BYBIT:UNIFIED",
        "account_user_id": uid,
        "observed_at": (NOW - timedelta(seconds=age)).isoformat(),
        "account_wide_positions": rows,
    }


def claim(symbol="TRXUSDT", account="A", pos="position-1", state="BOUND"):
    return {
        "trading_account_id": account,
        "identity_status": "PROVEN",
        "symbol": symbol,
        "position_idx": 0,
        "exchange_position_key": f"BYBIT:{account}:LINEAR:USDT:{symbol}:0",
        "exchange_position_slot_claim_id": f"claim-{account}",
        "strategy_position_id": pos,
        "claim_state": state,
    }


def owner(symbol="TRXUSDT", account="A", pos="position-1", side="Buy"):
    return {
        "trading_account_id": account,
        "identity_status": "PROVEN",
        "state": "OPEN",
        "position_id": pos,
        "symbol": symbol,
        "position_idx": 0,
        "side": side,
        "exchange_position_key": f"BYBIT:{account}:LINEAR:USDT:{symbol}:0",
    }


def audit(rows, *, claims=(), owners=(), account="A", **snapshot_args):
    return audit_account_exposure(
        account_snapshot=snapshot(rows, **snapshot_args),
        trading_account_id=account,
        active_slot_claims=claims,
        open_owner_rows=owners,
        now=NOW,
    )


def test_unknown_position_outside_strategy_or_arm_scope_is_seen_without_attribution():
    result = audit([p("TRXUSDT")])
    assert len(result) == 1
    assert result[0].symbol == "TRXUSDT"
    assert result[0].origin == "UNKNOWN"
    assert result[0].account_identity_binding == "NOT_VERIFIED_HERE"
    assert not result[0].active_strategy_claim_ids
    assert not result[0].matching_open_owner_ids


def test_claim_without_open_owner_is_observed_but_not_attributed():
    result = audit([p()], claims=[claim()])
    assert result[0].active_strategy_claim_ids == ("claim-A",)
    assert result[0].matching_open_owner_ids == ()
    assert result[0].origin == "UNKNOWN"


def test_claim_and_owner_row_are_not_exchange_cycle_proof():
    result = audit([p()], claims=[claim()], owners=[owner()])
    assert result[0].matching_open_owner_ids == ("position-1",)
    assert result[0].origin == "UNKNOWN"


def test_old_owner_with_other_claim_is_not_matched():
    result = audit([p()], claims=[claim(pos="position-new")], owners=[owner()])
    assert result[0].matching_open_owner_ids == ()


def test_wrong_side_owner_is_not_matched():
    result = audit([p()], claims=[claim()], owners=[owner(side="Sell")])
    assert result[0].matching_open_owner_ids == ()


def test_claimed_state_does_not_count_as_bound_owner():
    result = audit([p()], claims=[claim(state="CLAIMED")], owners=[owner()])
    assert result[0].matching_open_owner_ids == ()


def test_flat_slot_is_not_exposure():
    result = audit([p(size="0", side="")], claims=[claim()])
    assert result == ()


def test_mixed_flat_and_open_are_observed_correctly():
    result = audit([p("UNIUSDT", size="0", side=""), p("LTCUSDT", size="1")])
    assert len(result) == 1 and result[0].symbol == "LTCUSDT"


def test_cross_account_claim_rejected():
    with pytest.raises(ExposureInventoryUnavailable, match="cross-account"):
        audit([p()], claims=[claim(account="B")])


def test_cross_account_owner_rejected():
    with pytest.raises(ExposureInventoryUnavailable, match="cross-account"):
        audit([p()], owners=[owner(account="B")])


@pytest.mark.parametrize(
    "bad",
    [
        {"full": False},
        {"uid": ""},
        {"age": 16},
        {"age": -5},
    ],
)
def test_snapshot_missing_uid_or_fresh_complete_evidence_rejected(bad):
    with pytest.raises(ExposureInventoryUnavailable):
        audit([p()], **bad)


@pytest.mark.parametrize(
    "row",
    [
        p(size="NaN"),
        p(size="Infinity"),
        p(size="-1"),
        p(size="Oops"),
        p(idx="0"),
        p(idx=True),
        p(side=""),
    ],
)
def test_invalid_live_inventory_rejected(row):
    with pytest.raises(ExposureInventoryUnavailable):
        audit([row])


def test_duplicate_slot_rejected_even_if_payload_claimed_complete():
    with pytest.raises(ExposureInventoryUnavailable, match="duplicate"):
        audit([p(), p()])


def test_nonfinite_invalid_claim_key_rejected():
    c = claim()
    c["exchange_position_key"] = "BYBIT:OTHER:LINEAR:USDT:TRXUSDT:0"
    with pytest.raises(ExposureInventoryUnavailable, match="physical key"):
        audit([p()], claims=[c])


def test_empty_complete_account_is_fine():
    assert audit([]) == ()
