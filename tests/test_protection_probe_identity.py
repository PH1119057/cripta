from __future__ import annotations

import asyncio
import pytest

from bybit_workbench.exchange.bybit import protection_probe


def test_identity_mismatch_blocks_account_protection(monkeypatch):
    class FakeReader:
        def __init__(self, **kwargs):
            pass

        async def get(self, endpoint, params, *, authenticated):
            assert endpoint == "/v5/user/query-api"
            assert authenticated
            return {"retCode": 0, "result": {"userID": "other-account"}}

    monkeypatch.setattr(protection_probe, "LifecycleAccountReadTransport", FakeReader)
    with pytest.raises(RuntimeError, match="does not match"):
        asyncio.run(protection_probe.direct_account_snapshot(
            ("APTUSDT",), api_key="fixture", api_secret="fixture",
            expected_user_id="approved-account",
        ))


def test_missing_approved_identity_blocks_even_valid_key(monkeypatch):
    class FakeReader:
        def __init__(self, **kwargs):
            pass

        async def get(self, endpoint, params, *, authenticated):
            return {"retCode": 0, "result": {"userID": "live-identity"}}

    monkeypatch.setattr(protection_probe, "LifecycleAccountReadTransport", FakeReader)
    with pytest.raises(RuntimeError, match="does not match"):
        asyncio.run(protection_probe.direct_account_snapshot(
            ("APTUSDT",), api_key="fixture", api_secret="fixture",
            expected_user_id="",
        ))
