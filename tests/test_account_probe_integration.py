"""Integration of actual GitHub probe source with a simulated authenticated transport."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from bybit_workbench.exchange.bybit import protection_probe


def page(rows, cursor=""):
    return {"retCode": 0, "result": {"list": rows, "nextPageCursor": cursor}}


class SimulatedAccount:
    def __init__(self):
        self.calls = []
        self.pages = [
            page(
                [{"symbol": "TRXUSDT", "positionIdx": 0, "size": "2.00", "side": "Buy"}],
                "part2",
            ),
            page([{"symbol": "BTCUSDT", "positionIdx": 0, "size": "0", "side": ""}]),
        ]

    async def get(self, endpoint: str, params: Mapping[str, object], *, authenticated: bool):
        self.calls.append((endpoint, dict(params), authenticated))
        if endpoint == "/v5/user/query-api":
            return {"retCode": 0, "result": {"userID": "authenticated-uid-1"}}
        if endpoint == "/v5/market/instruments-info":
            assert not authenticated
            return page([{"symbol": "UNIUSDT", "priceFilter": {"tickSize": "0.001"}}])
        if endpoint == "/v5/position/list" and params.get("settleCoin") == "USDT":
            return self.pages.pop(0)
        if endpoint == "/v5/position/list" and params.get("symbol") == "UNIUSDT":
            return page([])
        if endpoint == "/v5/order/realtime" and params.get("symbol") == "UNIUSDT":
            return page([])
        raise AssertionError("Unexpected endpoint in GET-only probe")


def test_flat_local_and_arm_scope_still_discovers_other_symbols(monkeypatch):
    reader = SimulatedAccount()
    monkeypatch.setattr(protection_probe, "LifecycleAccountReadTransport", lambda **kwargs: reader)
    evidence = asyncio.run(
        protection_probe.direct_account_snapshot((), api_key="fake", api_secret="fake")
    )
    assert evidence["account_user_id"] == "authenticated-uid-1"
    assert evidence["symbols"] == {}
    assert evidence["account_wide_pagination_complete"] is True
    assert {r["symbol"] for r in evidence["account_wide_positions"]} == {
        "TRXUSDT",
        "BTCUSDT",
    }
    assert all(endpoint != "/v5/order/create" for endpoint, _, _ in reader.calls)


def test_independent_known_position_verdicts_keep_symbol_accurate(monkeypatch):
    reader = SimulatedAccount()
    monkeypatch.setattr(protection_probe, "LifecycleAccountReadTransport", lambda **kwargs: reader)
    evidence = asyncio.run(
        protection_probe.direct_account_snapshot(("UNIUSDT",), api_key="fake", api_secret="fake")
    )
    assert evidence["symbols"]["UNIUSDT"]["positions"] == []
    assert evidence["symbols"]["UNIUSDT"]["tick"] == "0.001"
    assert evidence["account_wide_positions"][0]["symbol"] == "TRXUSDT"
    assert all(
        authenticated or endpoint == "/v5/market/instruments-info"
        for endpoint, _, authenticated in reader.calls
    )


def test_failed_second_page_does_not_mark_complete(monkeypatch):
    reader = SimulatedAccount()
    reader.pages[-1] = {"retCode": 10001, "result": {"list": []}}
    monkeypatch.setattr(protection_probe, "LifecycleAccountReadTransport", lambda **kwargs: reader)
    with pytest.raises(RuntimeError):
        asyncio.run(protection_probe.direct_account_snapshot((), api_key="fake", api_secret="fake"))
