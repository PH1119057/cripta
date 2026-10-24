from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.exchange.bybit.exposure_audit import ExposureInventoryUnavailable
from bybit_workbench.lifecycle_exposure_bridge import (
    unbound_strategy_claims,
    verified_account_exposure,
)

UID = "852741"
ACCOUNT = "ta-A"
CONNECTOR = "ac-A"
NOW = datetime(2026, 10, 24, 9, 30, tzinfo=UTC)
REF = "systemd-credential:bybit-mainnet"


def snapshot(*positions, uid=UID, observed_at=NOW):
    return {
        "source": "BYBIT_AUTHENTICATED_ACCOUNT_GET",
        "complete": True,
        "account_wide_pagination_complete": True,
        "account_ref": "BYBIT:UNIFIED",
        "account_user_id": uid,
        "observed_at": observed_at.isoformat(),
        "is_master": True,
        "parent_uid": "0",
        "uta": 1,
        "account_wide_positions": list(positions),
    }


def position(symbol="UNIUSDT", size="3", side="Buy", idx=0):
    return {"symbol": symbol, "positionIdx": idx, "size": size, "side": side}


def claim(account=ACCOUNT, symbol="UNIUSDT", idx=0, id="claim-A", pos="position-A", state="BOUND"):
    return {
        "trading_account_id": account,
        "identity_status": "PROVEN",
        "claim_state": state,
        "symbol": symbol,
        "position_idx": idx,
        "exchange_position_key": f"BYBIT:{account}:LINEAR:USDT:{symbol}:{idx}",
        "exchange_position_slot_claim_id": id,
        "strategy_position_id": pos,
    }


def owner(account=ACCOUNT, symbol="UNIUSDT", idx=0, id="position-A", side="Buy"):
    return {
        "trading_account_id": account,
        "identity_status": "PROVEN",
        "state": "OPEN",
        "symbol": symbol,
        "position_idx": idx,
        "side": side,
        "position_id": id,
        "exchange_position_key": f"BYBIT:{account}:LINEAR:USDT:{symbol}:{idx}",
    }


def registry(*, uid=UID, kind="MAIN"):
    return {
        "trading_account_id": ACCOUNT,
        "venue_id": "venue-bybit",
        "status": "ACTIVE",
        "environment": "MAINNET",
        "account_kind": kind,
        "account_type": "UNIFIED",
        "external_account_ref": uid,
        "parent_trading_account_id": None,
    }


def connector(account=ACCOUNT):
    return {
        "account_connector_id": CONNECTOR,
        "trading_account_id": account,
        "status": "ACTIVE",
        "adapter_id": "BYBIT_V5_PRIVATE",
        "credential_ref": REF,
    }


class DB:
    def __init__(self, *, accounts=None, connectors=None, claims=None, owners=None):
        self.data = {
            "accounts": [registry()] if accounts is None else accounts,
            "connectors": [connector()] if connectors is None else connectors,
            "active_claims": [] if claims is None else claims,
            "open_owners": [] if owners is None else owners,
        }
        self.queries = []

    def execute(self, query, params):
        self.queries.append((query, params))
        return self

    def fetchone(self):
        return self.data


def observe(db, snap=None, credential_ref=REF, now=NOW, connector_id=CONNECTOR):
    return verified_account_exposure(
        db,
        account_snapshot=snap if snap is not None else snapshot(position()),
        trading_account_id=ACCOUNT,
        account_connector_id=connector_id,
        verified_credential_ref=credential_ref,
        now=now,
    )


def test_verified_claim_unbound_produces_exact_anomaly():
    db = DB(claims=[claim()])
    res = observe(db)
    assert res.state == "VERIFIED" and len(res.verified_positions) == 1
    issues = unbound_strategy_claims(res.verified_positions, account_id=ACCOUNT)
    assert len(issues) == 1 and issues[0]["exchange_position_slot_claim_ids"] == ("claim-A",)
    assert db.queries[0][1] == (ACCOUNT, ACCOUNT, ACCOUNT)


def test_owner_exact_match_no_anomaly():
    db = DB(claims=[claim()], owners=[owner()])
    res = observe(db)
    assert res.state == "VERIFIED"
    assert unbound_strategy_claims(res.verified_positions, account_id=ACCOUNT) == ()


def test_claimless_exchange_exposure_kept_unknown_not_faulted():
    res = observe(DB())
    assert res.state == "VERIFIED" and res.unknown_open_count == 1
    assert res.verified_positions[0]["origin"] == "UNKNOWN"
    assert unbound_strategy_claims(res.verified_positions, account_id=ACCOUNT) == ()


def test_empty_verified_account_is_not_unknown():
    res = observe(DB(), snap=snapshot())
    assert res.state == "VERIFIED" and res.verified_positions == ()
    assert res.unknown_open_count == 0


def test_missing_registered_uid_fails_closed():
    res = observe(DB(accounts=[registry(uid=None)]))
    assert res.verified_positions is None and res.state == "IDENTITY_UNKNOWN"
    assert "REGISTERED_BYBIT_UID_MISSING" in res.reasons


def test_uid_mismatch_fails_closed():
    res = observe(DB(), snap=snapshot(position(), uid="111111"))
    assert res.verified_positions is None
    assert "REGISTERED_BYBIT_UID_MISMATCH" in res.reasons


def test_credential_provenance_mismatch_fails_closed():
    res = observe(DB(), credential_ref="unverified-value")
    assert res.verified_positions is None
    assert "VERIFIED_CONNECTOR_CREDENTIAL_PROVENANCE_MISSING" in res.reasons


def test_wrong_account_connector_fails_closed():
    res = observe(DB(connectors=[connector("ta-B")]))
    assert res.verified_positions is None


def test_missing_exchange_read_does_not_look_flat():
    assert (
        verified_account_exposure(
            DB(),
            account_snapshot=None,
            trading_account_id=ACCOUNT,
            account_connector_id=CONNECTOR,
            verified_credential_ref=REF,
            now=NOW,
        ).verified_positions
        is None
    )


def test_expired_snapshot_blocks_fault_generation():
    res = observe(
        DB(claims=[claim()]), snap=snapshot(position(), observed_at=NOW - timedelta(seconds=16))
    )
    assert res.verified_positions is None


def test_future_snapshot_blocks_fault_generation():
    res = observe(
        DB(claims=[claim()]), snap=snapshot(position(), observed_at=NOW + timedelta(seconds=5))
    )
    assert res.verified_positions is None


def test_incomplete_pagination_blocks_fault_generation():
    bad = {**snapshot(position()), "account_wide_pagination_complete": False}
    res = observe(DB(claims=[claim()]), snap=bad)
    assert res.verified_positions is None and res.state == "EVIDENCE_UNKNOWN"


def test_duplicate_slot_claims_are_critical_anomaly():
    res = observe(DB(claims=[claim(), claim(id="claim-B")], owners=[owner()]))
    problems = unbound_strategy_claims(res.verified_positions, account_id=ACCOUNT)
    assert len(problems) == 1
    assert len(problems[0]["exchange_position_slot_claim_ids"]) == 2


def test_another_account_can_hold_same_symbol_without_hiding_missing_owner():
    res = observe(DB(claims=[claim()], owners=[owner(account="ta-B")]))
    assert res.verified_positions is None and res.state == "EVIDENCE_UNKNOWN"


def test_explicit_mismatched_position_side_causes_fault():
    res = observe(DB(claims=[claim()], owners=[owner(side="Sell")]))
    assert len(unbound_strategy_claims(res.verified_positions, account_id=ACCOUNT)) == 1


@pytest.mark.parametrize(
    "row",
    [
        {
            "account_identity_binding": "NOT_VERIFIED_HERE",
            "trading_account_id": ACCOUNT,
            "symbol": "UNIUSDT",
            "position_idx": 0,
            "active_strategy_claim_ids": ("claim-A",),
            "matching_open_owner_ids": (),
        },
        {
            "account_identity_binding": "VERIFIED",
            "trading_account_id": "ta-B",
            "symbol": "UNIUSDT",
            "position_idx": 0,
            "active_strategy_claim_ids": ("claim-A",),
            "matching_open_owner_ids": (),
        },
    ],
)
def test_unverified_or_other_account_cannot_generate_anomaly(row):
    with pytest.raises(ExposureInventoryUnavailable):
        unbound_strategy_claims((row,), account_id=ACCOUNT)


def test_duplicate_exposure_physical_key_rejected():
    res = observe(DB(claims=[claim()]))
    with pytest.raises(ExposureInventoryUnavailable):
        unbound_strategy_claims(res.verified_positions * 2, account_id=ACCOUNT)


def test_single_mvcc_snapshot_and_proven_account_scoping():
    db = DB()
    observe(db)
    assert len(db.queries) == 1
    sql, p = db.queries[0]
    assert "jsonb_agg" in sql and "to_jsonb" in sql
    assert "identity_status='PROVEN'" in sql
    assert sql.count("FROM identity.trading_accounts") == 1
    assert "FROM runtime.exchange_position_slot_claims" in sql
    assert "FROM runtime.position_ownership" in sql
    assert p == (ACCOUNT, ACCOUNT, ACCOUNT)


def test_no_provider_uid_leak_from_anomaly_payload():
    res = observe(DB(claims=[claim()]))
    assert UID not in str(unbound_strategy_claims(res.verified_positions, account_id=ACCOUNT))


def test_no_sql_or_exchange_mutations_in_bridge():
    text = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("src/bybit_workbench/lifecycle_exposure_bridge.py")
        .read_text()
    )
    assert not any(
        t in text.upper()
        for t in (
            "INSERT INTO ",
            "UPDATE RUNTIME.",
            "DELETE FROM ",
            "API_POST(",
            "ORDER/CREATE",
            "TRADING-STOP",
        )
    )


def test_absent_service_connector_env_resolves_only_unique_registered_exact_credential():
    result = observe(DB(claims=[claim()]), connector_id="")
    assert result.state == "VERIFIED"
    assert len(unbound_strategy_claims(result.verified_positions, account_id=ACCOUNT)) == 1


def test_ambiguous_registered_connectors_fail_closed_without_default():
    other = {**connector(), "account_connector_id": "ac-other"}
    result = observe(DB(connectors=[connector(), other]), connector_id="")
    assert result.verified_positions is None
    assert "REGISTERED_CONNECTOR_NOT_UNIQUELY_RESOLVED" in result.reasons


def test_missing_exact_credential_ref_does_not_resolve_connector():
    result = observe(DB(), connector_id="", credential_ref=None)
    assert result.verified_positions is None


def test_supplied_wrong_connector_does_not_fallback_to_registered_one():
    result = observe(DB(), connector_id="ac-wrong")
    assert result.verified_positions is None


def test_second_open_owner_in_same_physical_slot_must_not_be_hidden_by_bound_owner():
    # A valid BOUND claim/owner pair does not excuse an extra OPEN owner
    # for the exact same account, symbol and positionIdx.
    db = DB(claims=[claim()], owners=[owner(), owner(id="position-B")])
    result = observe(db)
    assert result.state == "VERIFIED"
    issues = unbound_strategy_claims(result.verified_positions, account_id=ACCOUNT)
    assert len(issues) == 1
    assert issues[0]["matching_open_owner_ids"] == ("position-A",)
    assert issues[0]["open_owner_ids"] == ("position-A", "position-B")


def test_other_side_open_owner_must_not_be_hidden_by_bound_owner():
    db = DB(claims=[claim()], owners=[owner(), owner(id="position-B", side="Sell")])
    result = observe(db)
    assert len(unbound_strategy_claims(result.verified_positions, account_id=ACCOUNT)) == 1
