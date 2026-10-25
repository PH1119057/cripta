"""Read-only integration: real probe + synthetic authenticated GET + exposure classifier."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from test_exposure_audit import claim, owner

from bybit_workbench.exchange.bybit import protection_probe
from bybit_workbench.exchange.bybit.exposure_audit import audit_account_exposure


def page(rows, cursor=""):
    return {"retCode": 0, "result": {"list": rows, "nextPageCursor": cursor}}


class AccountReader:
    def __init__(self):
        self.calls = []
        self.pages = [
            page(
                [
                    {"symbol": "TRXUSDT", "positionIdx": 0, "size": "2", "side": "Buy"},
                    {"symbol": "UNIUSDT", "positionIdx": 0, "size": "3", "side": "Sell"},
                ],
                "second",
            ),
            page([]),
        ]

    async def get(self, endpoint, params, *, authenticated):
        self.calls.append((endpoint, dict(params), authenticated))
        if endpoint == "/v5/user/query-api":
            return {"retCode": 0, "result": {"userID": "authenticated-but-not-registry-bound"}}
        if endpoint == "/v5/position/list" and params.get("settleCoin") == "USDT":
            return self.pages.pop(0)
        raise AssertionError(f"Unexpected Exchange endpoint: {endpoint}")


def test_probe_discovers_both_unattributed_and_locally_claimed_positions(monkeypatch):
    r = AccountReader()
    monkeypatch.setattr(protection_probe, "LifecycleAccountReadTransport", lambda **_: r)
    result = asyncio.run(
        protection_probe.direct_account_snapshot((), api_key="synthetic", api_secret="synthetic")
    )
    evidence = audit_account_exposure(
        account_snapshot=result,
        trading_account_id="A",
        active_slot_claims=[claim(symbol="TRXUSDT")],
        open_owner_rows=[owner(symbol="TRXUSDT")],
        now=datetime.now(UTC),
    )
    assert {x.symbol for x in evidence} == {"TRXUSDT", "UNIUSDT"}
    trx = next(x for x in evidence if x.symbol == "TRXUSDT")
    uni = next(x for x in evidence if x.symbol == "UNIUSDT")
    assert trx.matching_open_owner_ids == ("position-1",)
    assert uni.matching_open_owner_ids == ()
    assert uni.active_strategy_claim_ids == ()
    assert trx.origin == uni.origin == "UNKNOWN"
    assert trx.account_identity_binding == "NOT_VERIFIED_HERE"
    assert all(
        endpoint in ("/v5/user/query-api", "/v5/position/list") and authenticated
        for endpoint, _, authenticated in r.calls
    )


def test_probe_page_failure_prevents_any_audit_result(monkeypatch):
    r = AccountReader()
    r.pages[-1] = {"retCode": 10001, "result": {"list": []}}
    monkeypatch.setattr(protection_probe, "LifecycleAccountReadTransport", lambda **_: r)
    with pytest.raises(RuntimeError):
        asyncio.run(
            protection_probe.direct_account_snapshot(
                (), api_key="synthetic", api_secret="synthetic"
            )
        )
