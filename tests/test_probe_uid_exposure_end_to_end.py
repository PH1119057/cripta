"""In-process read-only integration of exact-source probe, registry UID check, and exposure audit.

Synthetic identity and Exchange responses only; no real credentials or DB access.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from bybit_workbench.account_uid_registration_gate import registered_uid_match
from bybit_workbench.exchange.bybit import protection_probe
from bybit_workbench.exchange.bybit.exposure_audit import audit_account_exposure

UID = "426789"
NOW = datetime.now(UTC)
ACCOUNT = {
    "trading_account_id": "ta-test",
    "venue_id": "venue-bybit",
    "environment": "MAINNET",
    "status": "ACTIVE",
    "account_kind": "MAIN",
    "account_type": "UNIFIED",
    "external_account_ref": UID,
    "parent_trading_account_id": None,
}
CONNECTOR = {
    "account_connector_id": "ac-test",
    "trading_account_id": "ta-test",
    "status": "ACTIVE",
    "adapter_id": "BYBIT_V5_PRIVATE",
    "credential_ref": "systemd-credential:bybit-mainnet",
}


class FakeReader:
    def __init__(self, *, query=None, position=None):
        self.identity = (
            query
            if query is not None
            else {
                "userID": UID,
                "isMaster": True,
                "parentUid": "0",
                "uta": 1,
            }
        )
        self.position = (
            position
            if position is not None
            else [
                {"symbol": "TRXUSDT", "positionIdx": 0, "size": "2", "side": "Buy"},
            ]
        )
        self.calls = []

    async def get(self, endpoint, params, *, authenticated):
        self.calls.append((endpoint, authenticated))
        if endpoint == "/v5/user/query-api":
            return {"retCode": 0, "result": self.identity}
        if endpoint == "/v5/position/list":
            return {"retCode": 0, "result": {"list": self.position, "nextPageCursor": ""}}
        raise AssertionError("Unsupported endpoint: " + endpoint)


def audit(
    monkeypatch, *, reader=None, account=None, credential_ref="systemd-credential:bybit-mainnet"
):
    reader = reader or FakeReader()
    monkeypatch.setattr(protection_probe, "LifecycleAccountReadTransport", lambda **_: reader)
    snapshot = asyncio.run(
        protection_probe.direct_account_snapshot((), api_key="fake", api_secret="fake")
    )
    outcome = registered_uid_match(
        snapshot=snapshot,
        trading_account_id="ta-test",
        account_connector_id="ac-test",
        accounts=[ACCOUNT if account is None else account],
        connectors=[CONNECTOR],
        now=datetime.now(UTC),
        max_age_seconds=15,
        verified_credential_ref=credential_ref,
    )
    exposures = audit_account_exposure(
        account_snapshot=snapshot,
        trading_account_id="ta-test",
        active_slot_claims=[],
        open_owner_rows=[],
        now=datetime.now(UTC),
        max_age_seconds=15,
    )
    return snapshot, outcome, exposures, reader.calls


def test_main_uta_registration_and_unknown_exposure(monkeypatch):
    snapshot, outcome, exposures, calls = audit(monkeypatch)
    assert outcome.matches and not outcome.reasons
    assert snapshot["is_master"] is True
    assert snapshot["parent_uid"] == "0"
    assert snapshot["uta"] == 1
    assert len(exposures) == 1 and exposures[0].symbol == "TRXUSDT"
    assert exposures[0].origin == "UNKNOWN"
    assert exposures[0].account_identity_binding == "NOT_VERIFIED_HERE"
    assert calls == [("/v5/user/query-api", True), ("/v5/position/list", True)]


def test_empty_registry_binding_never_positive(monkeypatch):
    _, outcome, observations, _ = audit(
        monkeypatch, account={**ACCOUNT, "external_account_ref": None}
    )
    assert not outcome.matches
    assert "REGISTERED_BYBIT_UID_MISSING" in outcome.reasons
    assert observations[0].origin == "UNKNOWN"


def test_wrong_uid_never_positive(monkeypatch):
    reader = FakeReader(query={"userID": "999", "isMaster": True, "parentUid": "0", "uta": 1})
    _, outcome, _, _ = audit(monkeypatch, reader=reader)
    assert not outcome.matches and "REGISTERED_BYBIT_UID_MISMATCH" in outcome.reasons


def test_subcredential_under_main_registry_never_positive(monkeypatch):
    reader = FakeReader(query={"userID": UID, "isMaster": False, "parentUid": "8765", "uta": 1})
    _, outcome, _, _ = audit(monkeypatch, reader=reader)
    assert not outcome.matches and "MAIN_ACCOUNT_TYPE_MISMATCH" in outcome.reasons


def test_missing_kind_metadata_never_positive(monkeypatch):
    reader = FakeReader(query={"userID": UID})
    _, outcome, _, _ = audit(monkeypatch, reader=reader)
    assert not outcome.matches and "BYBIT_MAIN_SUB_PROVENANCE_MISSING" in outcome.reasons


def test_unauthorized_credential_ref_never_positive(monkeypatch):
    _, outcome, _, _ = audit(monkeypatch, credential_ref="other")
    assert (
        not outcome.matches
        and "VERIFIED_CONNECTOR_CREDENTIAL_PROVENANCE_MISSING" in outcome.reasons
    )


def test_no_extra_exchange_requests_for_uid_provenance(monkeypatch):
    _, _, _, calls = audit(monkeypatch)
    assert len(calls) == 2 and all(allowed for _, allowed in calls)
