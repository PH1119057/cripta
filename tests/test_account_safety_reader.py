"""Offline Bybit GET safety facts; no keys, network or exchange writes."""
from __future__ import annotations

import asyncio

import pytest

from bybit_workbench.exchange.bybit.account_safety_reader import (
    read_account_safety,
    validate_wallet,
)


class _Reader:
    def __init__(self, duplicate: bool = False) -> None:
        self.calls: list[str] = []
        self.duplicate = duplicate

    async def get(
        self, path: str, params: dict[str, str], *, authenticated: bool,
    ) -> dict[str, object]:
        assert authenticated and path.startswith("/v5/")
        assert path in {"/v5/account/wallet-balance", "/v5/order/realtime"}
        self.calls.append(path)
        if path.endswith("wallet-balance"):
            return {
                "retCode": 0, "result": {"list": [{
                    "accountType": "UNIFIED",
                    "totalEquity": "21.5",
                    "totalAvailableBalance": "20.0",
                    "totalWalletBalance": "21.4",
                }]}
            }
        if "cursor" not in params:
            return {"retCode": 0, "result": {
                "list": [{"orderId": "id-1"}], "nextPageCursor": "page2",
            }}
        return {"retCode": 0, "result": {
            "list": [{"orderId": "id-1" if self.duplicate else "id-2"}],
            "nextPageCursor": "",
        }}


def test_verified_wallet_and_paginated_orders_stay_exact() -> None:
    fake = _Reader()
    facts = asyncio.run(read_account_safety(fake))  # type: ignore[arg-type]
    assert str(facts.total_equity) == "21.5"
    assert str(facts.available_balance) == "20.0"
    assert facts.open_orders == 2
    assert fake.calls == [
        "/v5/account/wallet-balance",
        "/v5/order/realtime",
        "/v5/order/realtime",
    ]


def test_duplicate_order_id_does_not_count_as_two_orders() -> None:
    with pytest.raises(RuntimeError, match="DUPLICATE_OPEN_ORDER"):
        asyncio.run(read_account_safety(_Reader(duplicate=True)))  # type: ignore[arg-type]


def test_wrong_wallet_type_is_not_private_account_proof() -> None:
    with pytest.raises(RuntimeError, match="WALLET_TYPE_MISMATCH"):
        validate_wallet({"retCode": 0, "result": {
            "list": [{"accountType": "CONTRACT", "totalEquity": "5"}],
        }})


def test_incomplete_wallet_rejected() -> None:
    with pytest.raises(RuntimeError, match="WALLET_SCOPE_AMBIGUOUS"):
        validate_wallet({"retCode": 0, "result": {"list": []}})
