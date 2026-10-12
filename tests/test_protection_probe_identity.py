from __future__ import annotations

import asyncio

import pytest

from bybit_workbench.exchange.bybit import protection_probe


def test_authenticated_user_identity_comes_from_existing_key(monkeypatch):
    class FakeReader:
        def __init__(self, **kwargs):
            pass

        async def get(self, endpoint, params, *, authenticated):
            assert endpoint == "/v5/user/query-api"
            assert authenticated
            return {"retCode": 0, "result": {"userID": "existing-account"}}

    monkeypatch.setattr(protection_probe, "LifecycleAccountReadTransport", FakeReader)
    monkeypatch.setattr(
        protection_probe, "_symbol_snapshot",
        lambda *args: asyncio.sleep(
            0, result=("APTUSDT", {"positions": [], "orders": [], "tick": "0.01"})
        ),
    )
    account = asyncio.run(protection_probe.direct_account_snapshot(
        ("APTUSDT",), api_key="fixture", api_secret="fixture",
    ))
    assert account["account_user_id"] == "existing-account"
    assert account["complete"] is True


def test_flat_scope_still_authenticates_account_identity(monkeypatch):
    calls = []

    class FakeReader:
        def __init__(self, **kwargs):
            pass

        async def get(self, endpoint, params, *, authenticated):
            calls.append((endpoint, authenticated))
            return {"retCode": 0, "result": {"userID": "existing-account"}}

    monkeypatch.setattr(protection_probe, "LifecycleAccountReadTransport", FakeReader)
    account = asyncio.run(protection_probe.direct_account_snapshot(
        (), api_key="fixture", api_secret="fixture",
    ))
    assert calls == [("/v5/user/query-api", True)]
    assert account["account_user_id"] == "existing-account"
    assert account["symbols"] == {}


def test_missing_authenticated_identity_fails_closed(monkeypatch):
    class FakeReader:
        def __init__(self, **kwargs):
            pass

        async def get(self, endpoint, params, *, authenticated):
            return {"retCode": 0, "result": {}}

    monkeypatch.setattr(protection_probe, "LifecycleAccountReadTransport", FakeReader)
    with pytest.raises(RuntimeError, match="userID unavailable"):
        asyncio.run(protection_probe.direct_account_snapshot(
            ("APTUSDT",), api_key="fixture", api_secret="fixture",
        ))
