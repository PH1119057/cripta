import json
import tempfile
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bybit_workbench.exchange.bybit.acceptance import (
    MainnetAcceptanceRunner,
    write_acceptance_report,
)
from bybit_workbench.exchange.bybit.rest import BybitReadOnlyAdapter

NOW = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


def ok(result: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"retCode": 0, "retMsg": "OK", "time": NOW_MS, "result": result}


def position_row(position_idx: int = 0, *, size: str = "0") -> dict[str, Any]:
    side = "" if size == "0" else "Buy"
    return {
        "positionIdx": position_idx,
        "symbol": "UNIUSDT",
        "side": side,
        "size": size,
        "avgPrice": "" if size == "0" else "10",
        "leverage": "1",
        "markPrice": "10",
        "liqPrice": "",
        "stopLoss": "0",
        "takeProfit": "0",
        "trailingStop": "0",
        "unrealisedPnl": "0",
        "seq": 1,
        "updatedTime": str(NOW_MS),
    }


class AcceptanceTransport:
    def __init__(
        self,
        *,
        hedge: bool = False,
        extra_spot: bool = False,
        isolated_wallet: bool = False,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any], bool]] = []
        self.hedge = hedge
        self.extra_spot = extra_spot
        self.isolated_wallet = isolated_wallet

    async def get(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        authenticated: bool,
    ) -> Mapping[str, Any]:
        values = dict(params)
        self.calls.append((endpoint, values, authenticated))
        if endpoint == "/v5/market/time":
            return ok({"timeSecond": str(int(NOW.timestamp())), "timeNano": "0"})
        if endpoint == "/v5/user/query-api":
            return ok(
                {
                    "id": "key-id-1",
                    "note": "BotW-Mainnet",
                    "readOnly": 0,
                    "permissions": {
                        "ContractTrade": ["Order", "Position"],
                        "Spot": ["SpotTrade"] if self.extra_spot else [],
                        "Wallet": [],
                        "Options": [],
                        "Derivatives": ["DerivativesTrade"],
                    },
                    "ips": ["203.0.113.10"],
                    "deadlineDay": -2,
                    "expiredAt": "1970-01-01T00:00:00Z",
                    "createdAt": "2026-08-01T00:00:00Z",
                    "type": 1,
                    "uta": 1,
                    "isMaster": True,
                    "parentUid": "0",
                }
            )
        if endpoint == "/v5/market/instruments-info":
            if values.get("symbol") == "UNIUSDT":
                return ok(
                    {
                        "list": [
                            {
                                "symbol": "UNIUSDT",
                                "priceScale": "3",
                                "priceFilter": {"tickSize": "0.001"},
                                "lotSizeFilter": {
                                    "qtyStep": "0.1",
                                    "minOrderQty": "0.1",
                                    "minNotionalValue": "5",
                                    "maxOrderQty": "100000",
                                    "maxMktOrderQty": "10000",
                                },
                            }
                        ],
                        "nextPageCursor": "",
                    }
                )
            return ok(
                {
                    "list": [{"symbol": "UNIUSDT", "settleCoin": "USDT"}],
                    "nextPageCursor": "",
                }
            )
        if endpoint == "/v5/account/wallet-balance":
            wallet = {
                "accountType": "UNIFIED",
                "totalEquity": "20",
                "totalAvailableBalance": "20",
                "totalWalletBalance": "20",
                "totalPerpUPL": "0",
            }
            if self.isolated_wallet:
                wallet.update(
                    {
                        "totalEquity": "",
                        "totalAvailableBalance": "",
                        "totalWalletBalance": "",
                        "totalPerpUPL": "",
                        "coin": [
                            {
                                "coin": "USDT",
                                "equity": "20",
                                "usdValue": "20",
                                "walletBalance": "20",
                                "unrealisedPnl": "0",
                                "totalPositionIM": "0",
                                "totalOrderIM": "0",
                                "locked": "0",
                                "bonus": "0",
                            }
                        ],
                    }
                )
            return ok({"list": [wallet]})
        if endpoint == "/v5/account/info":
            return ok({"marginMode": "ISOLATED_MARGIN", "unifiedMarginStatus": 5})
        if endpoint == "/v5/account/fee-rate":
            return ok(
                {
                    "list": [
                        {
                            "symbol": "UNIUSDT",
                            "makerFeeRate": "0.0001",
                            "takerFeeRate": "0.0006",
                        }
                    ]
                }
            )
        if endpoint == "/v5/position/closed-pnl":
            return ok({"list": [], "nextPageCursor": ""})
        if endpoint == "/v5/position/list":
            if values.get("symbol") == "UNIUSDT":
                rows = (
                    [position_row(1), position_row(2)]
                    if self.hedge
                    else [position_row(0)]
                )
                return ok({"list": rows, "nextPageCursor": ""})
            return ok({"list": [], "nextPageCursor": ""})
        if endpoint == "/v5/order/realtime":
            return ok({"list": [], "nextPageCursor": ""})
        raise AssertionError((endpoint, values))


class MainnetAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_clean_master_uta_report_is_redacted_and_ready(self) -> None:
        transport = AcceptanceTransport()
        runner = MainnetAcceptanceRunner(
            BybitReadOnlyAdapter(transport),
            "https://api.bybit.kz",
            expected_profile_name="BotW-Mainnet",
        )
        report = await runner.run("UNIUSDT", local_time=NOW)
        self.assertTrue(report.micro_live_ready)
        self.assertEqual(report.account_role, "master")
        self.assertEqual(report.position_mode, "ONE_WAY")
        self.assertIsNone(report.expired_at)
        self.assertIsNone(report.deadline_day)
        payload = report.to_redacted_dict()
        self.assertEqual(payload["ip_binding_count"], 1)
        self.assertFalse(payload["ip_addresses_included"])
        encoded = json.dumps(payload)
        self.assertNotIn("203.0.113.10", encoded)
        self.assertNotIn("apiKey", encoded)
        self.assertTrue(all(method == "GET" for method in ["GET"] * len(transport.calls)))
        self.assertTrue(all(endpoint.startswith("/v5/") for endpoint, _, _ in transport.calls))


    async def test_exchange_note_is_independent_from_local_credential_profile(self) -> None:
        transport = AcceptanceTransport()
        original_get = transport.get

        async def get_with_custom_note(
            endpoint: str,
            params: Mapping[str, Any],
            *,
            authenticated: bool,
        ) -> Mapping[str, Any]:
            response = await original_get(endpoint, params, authenticated=authenticated)
            if endpoint != "/v5/user/query-api":
                return response
            payload = dict(response)
            result = dict(payload["result"])
            result["note"] = "Bybit_KZ"
            payload["result"] = result
            return payload

        transport.get = get_with_custom_note  # type: ignore[method-assign]
        report = await MainnetAcceptanceRunner(
            BybitReadOnlyAdapter(transport),
            "https://api.bybit.kz",
            expected_profile_name="BotW-Mainnet",
        ).run("UNIUSDT", local_time=NOW)
        self.assertTrue(report.micro_live_ready)
        note_check = next(check for check in report.checks if check.code == "key.exchange_note")
        self.assertTrue(note_check.passed)
        self.assertFalse(note_check.blocking)
        self.assertIn("Bybit_KZ", note_check.detail)

    async def test_isolated_wallet_empty_account_totals_are_derived_from_coin_rows(self) -> None:
        report = await MainnetAcceptanceRunner(
            BybitReadOnlyAdapter(AcceptanceTransport(isolated_wallet=True)),
            "https://api.bybit.kz",
            expected_profile_name="BotW-Mainnet",
        ).run("UNIUSDT", local_time=NOW)
        self.assertTrue(report.micro_live_ready)
        self.assertEqual(report.equity, 20)
        self.assertEqual(report.available_balance, 20)

    async def test_uta_derivatives_trade_permission_is_allowed(self) -> None:
        report = await MainnetAcceptanceRunner(
            BybitReadOnlyAdapter(AcceptanceTransport()),
            "https://api.bybit.kz",
            expected_profile_name="BotW-Mainnet",
        ).run("UNIUSDT", local_time=NOW)
        self.assertTrue(report.micro_live_ready)
        self.assertIn(("Derivatives", ("DerivativesTrade",)), report.other_permissions)
        failed = {check.code for check in report.checks if not check.passed}
        self.assertNotIn("permissions.other_absent", failed)

    async def test_hedge_mode_is_reported_as_blocker_without_crashing(self) -> None:
        report = await MainnetAcceptanceRunner(
            BybitReadOnlyAdapter(AcceptanceTransport(hedge=True)),
            "https://api.bybit.kz",
            expected_profile_name="BotW-Mainnet",
        ).run("UNIUSDT", local_time=NOW)
        self.assertEqual(report.position_mode, "HEDGE")
        self.assertFalse(report.micro_live_ready)
        failed = {check.code for check in report.checks if not check.passed}
        self.assertIn("position.one_way", failed)

    async def test_spot_permission_is_a_micro_live_blocker(self) -> None:
        report = await MainnetAcceptanceRunner(
            BybitReadOnlyAdapter(AcceptanceTransport(extra_spot=True)),
            "https://api.bybit.kz",
            expected_profile_name="BotW-Mainnet",
        ).run("UNIUSDT", local_time=NOW)
        self.assertFalse(report.micro_live_ready)
        failed = {check.code for check in report.checks if not check.passed}
        self.assertIn("permissions.spot_absent", failed)

    async def test_report_writer_produces_sha_without_secret_material(self) -> None:
        report = await MainnetAcceptanceRunner(
            BybitReadOnlyAdapter(AcceptanceTransport()),
            "https://api.bybit.kz",
            expected_profile_name="BotW-Mainnet",
        ).run("UNIUSDT", local_time=NOW)
        with tempfile.TemporaryDirectory() as tmp:
            target, sha = write_acceptance_report(report, Path(tmp) / "acceptance.json")
            self.assertTrue(target.exists())
            self.assertTrue(sha.exists())
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertFalse(payload["secret_material_included"])
            self.assertFalse(payload["api_key_value_included"])
            self.assertFalse(payload["ip_addresses_included"])
            self.assertIn(target.name, sha.read_text(encoding="ascii"))


if __name__ == "__main__":
    unittest.main()
