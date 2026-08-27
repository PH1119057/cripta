import unittest
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from bybit_workbench.exchange.bybit.errors import BybitClockSkewError, BybitModeMismatch
from bybit_workbench.exchange.bybit.mainnet_connection_test import MainnetConnectionTester
from bybit_workbench.exchange.bybit.rest import BybitReadOnlyAdapter

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


class GetOnlyTransport:
    def __init__(self, mismatch: bool = False, metadata_http_400: bool = False) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.mismatch = mismatch
        self.metadata_http_400 = metadata_http_400

    async def get(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        authenticated: bool,
    ) -> Mapping[str, Any]:
        del params
        self.calls.append((endpoint, authenticated))
        if endpoint == "/v5/market/time":
            return ok({"timeSecond": str(int(NOW.timestamp())), "timeNano": "0"})
        if endpoint == "/v5/user/query-api":
            if self.metadata_http_400:
                raise RuntimeError(
                    "Bad request. retries exceeded maximum. (ErrCode: 400)"
                )
            if self.mismatch:
                return {"retCode": 10003, "retMsg": "API key is invalid", "result": {}}
            return ok(
                {
                    "note": "BotW-Mainnet",
                    "readOnly": 0,
                    "permissions": {
                        "ContractTrade": ["Order", "Position"],
                        "Spot": ["SpotTrade"],
                        "Wallet": [],
                        "Options": [],
                    },
                    "ips": [],
                    "deadlineDay": 80,
                    "expiredAt": "2026-11-01T00:00:00Z",
                    "createdAt": "2026-08-13T00:00:00Z",
                    "type": 1,
                    "uta": 1,
                    "isMaster": True,
                    "parentUid": "0",
                }
            )
        if endpoint == "/v5/account/wallet-balance":
            return ok(
                {
                    "list": [
                        {
                            "accountType": "UNIFIED",
                            "totalEquity": "100",
                            "totalAvailableBalance": "90",
                            "totalWalletBalance": "100",
                            "totalPerpUPL": "0",
                        }
                    ]
                }
            )
        if endpoint == "/v5/position/list":
            return ok(
                {
                    "list": [
                        {
                            "positionIdx": 0,
                            "symbol": "BTCUSDT",
                            "side": "",
                            "size": "0",
                            "avgPrice": "",
                            "leverage": "1",
                            "markPrice": "50000",
                            "liqPrice": "",
                            "stopLoss": "0",
                            "takeProfit": "0",
                            "trailingStop": "0",
                            "unrealisedPnl": "0",
                            "seq": 1,
                            "updatedTime": str(NOW_MS),
                        }
                    ]
                }
            )
        if endpoint == "/v5/order/realtime":
            return ok({"list": [], "nextPageCursor": ""})
        raise AssertionError(endpoint)


def ok(result: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"retCode": 0, "retMsg": "OK", "time": NOW_MS, "result": result}


class MainnetConnectionTestTests(unittest.IsolatedAsyncioTestCase):
    async def test_clock_skew_over_750ms_blocks_before_authenticated_calls(self) -> None:
        transport = GetOnlyTransport()
        tester = MainnetConnectionTester(
            BybitReadOnlyAdapter(transport),
            "https://api.bybit.kz",
        )
        local_ahead = NOW.replace(microsecond=800_000)

        with self.assertRaises(BybitClockSkewError):
            await tester.run("BTCUSDT", local_time=local_ahead)

        self.assertEqual(transport.calls, [("/v5/market/time", False)])

    async def test_ordered_connection_test_is_get_only(self) -> None:
        transport = GetOnlyTransport()
        tester = MainnetConnectionTester(
            BybitReadOnlyAdapter(transport),
            "https://api.bybit.kz",
        )
        report = await tester.run("BTCUSDT", local_time=NOW)
        self.assertEqual(
            [endpoint for endpoint, _ in transport.calls],
            [
                "/v5/market/time",
                "/v5/account/wallet-balance",
                "/v5/position/list",
                "/v5/order/realtime",
                "/v5/user/query-api",
            ],
        )
        self.assertEqual(report.endpoint, "https://api.bybit.kz")
        self.assertIsNotNone(report.api_key)
        assert report.api_key is not None
        self.assertEqual(report.api_key.note, "BotW-Mainnet")
        self.assertEqual(report.api_key.permissions.warnings, ())
        self.assertIn(
            "Spot permissions are forbidden: SpotTrade",
            report.api_key.permissions.blocking_reasons,
        )
        self.assertTrue(report.arming_blocked)
        self.assertIsNone(report.api_key.parent_uid)

    async def test_query_api_http_400_keeps_core_read_only_report_but_blocks_arming(self) -> None:
        transport = GetOnlyTransport(metadata_http_400=True)
        tester = MainnetConnectionTester(
            BybitReadOnlyAdapter(transport),
            "https://api.bybit.kz",
        )
        report = await tester.run("BTCUSDT", local_time=NOW)
        self.assertIsNone(report.api_key)
        self.assertTrue(report.arming_blocked)
        self.assertIn("api_key_info_unavailable", report.completed_steps)
        self.assertEqual(report.wallet.equity, 100)
        self.assertEqual(report.open_order_count, 0)

    async def test_wrong_endpoint_still_rejects_authenticated_key_metadata(self) -> None:
        transport = GetOnlyTransport(mismatch=True)
        tester = MainnetConnectionTester(
            BybitReadOnlyAdapter(transport),
            "https://api.bybit.com",
        )
        with self.assertRaisesRegex(BybitModeMismatch, "no fallback"):
            await tester.run("BTCUSDT", local_time=NOW)
        self.assertEqual(transport.calls[-1], ("/v5/user/query-api", True))


if __name__ == "__main__":
    unittest.main()
