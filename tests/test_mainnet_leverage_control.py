from __future__ import annotations

import unittest
from typing import Any

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.credentials import BybitCredentials
from bybit_workbench.domain.types import AppMode
from bybit_workbench.exchange.bybit.connection import (
    PybitConstructors,
    set_mainnet_symbol_leverage,
)


class FakeHttpSession:
    def __init__(
        self,
        *,
        position_size: str = "0",
        open_orders: int = 0,
        set_ret_code: int = 0,
        **kwargs: Any,
    ) -> None:
        self.kwargs = kwargs
        self.endpoint = None
        self.position_size = position_size
        self.open_orders = open_orders
        self.set_ret_code = set_ret_code
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_positions(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("positions", dict(kwargs)))
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {
                        "symbol": kwargs["symbol"],
                        "size": self.position_size,
                        "leverage": "1",
                    }
                ]
            },
        }

    def get_open_orders(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("orders", dict(kwargs)))
        rows = [
            {"symbol": kwargs["symbol"], "orderId": str(index)}
            for index in range(self.open_orders)
        ]
        return {"retCode": 0, "retMsg": "OK", "result": {"list": rows}}

    def set_leverage(self, **kwargs: str) -> dict[str, object]:
        self.calls.append(("set", dict(kwargs)))
        msg = "Set leverage has not been modified" if self.set_ret_code == 110043 else "OK"
        return {"retCode": self.set_ret_code, "retMsg": msg, "result": {}, "time": 1}


class MainnetLeverageControlTests(unittest.TestCase):
    def _settings_and_credentials(self) -> tuple[AppSettings, BybitCredentials]:
        return (
            AppSettings(mode=AppMode.LIVE, rest_url_override="https://api.bybit.kz"),
            BybitCredentials(AppMode.LIVE, "key", "secret", "BotW-Mainnet"),
        )

    def test_sets_same_buy_and_sell_leverage_after_fresh_server_prechecks(self) -> None:
        sessions: list[FakeHttpSession] = []

        def http(**kwargs: Any) -> FakeHttpSession:
            session = FakeHttpSession(**kwargs)
            sessions.append(session)
            return session

        constructors = PybitConstructors(http=http, websocket=lambda **_kwargs: object())
        settings, credentials = self._settings_and_credentials()

        applied = set_mainnet_symbol_leverage(
            settings,
            credentials,
            "uniusdt",
            "10",
            constructors=constructors,
        )

        self.assertEqual(applied, "10")
        self.assertEqual(
            sessions[0].calls,
            [
                ("positions", {"category": "linear", "symbol": "UNIUSDT"}),
                (
                    "orders",
                    {"category": "linear", "symbol": "UNIUSDT", "openOnly": 0},
                ),
                (
                    "set",
                    {
                        "category": "linear",
                        "symbol": "UNIUSDT",
                        "buyLeverage": "10",
                        "sellLeverage": "10",
                    },
                ),
            ],
        )

    def test_rejects_leverage_change_when_fresh_position_exists(self) -> None:
        session = FakeHttpSession(position_size="1")
        constructors = PybitConstructors(
            http=lambda **_kwargs: session,
            websocket=lambda **_kwargs: object(),
        )
        settings, credentials = self._settings_and_credentials()
        with self.assertRaisesRegex(RuntimeError, "открытая позиция"):
            set_mainnet_symbol_leverage(
                settings,
                credentials,
                "UNIUSDT",
                "3",
                constructors=constructors,
            )
        self.assertEqual([name for name, _ in session.calls], ["positions"])

    def test_rejects_leverage_change_when_fresh_open_order_exists(self) -> None:
        session = FakeHttpSession(open_orders=1)
        constructors = PybitConstructors(
            http=lambda **_kwargs: session,
            websocket=lambda **_kwargs: object(),
        )
        settings, credentials = self._settings_and_credentials()
        with self.assertRaisesRegex(RuntimeError, "открытые ордера"):
            set_mainnet_symbol_leverage(
                settings,
                credentials,
                "UNIUSDT",
                "3",
                constructors=constructors,
            )
        self.assertEqual([name for name, _ in session.calls], ["positions", "orders"])

    def test_already_selected_leverage_is_idempotent_success(self) -> None:
        session = FakeHttpSession(set_ret_code=110043)
        constructors = PybitConstructors(
            http=lambda **_kwargs: session,
            websocket=lambda **_kwargs: object(),
        )
        settings, credentials = self._settings_and_credentials()
        self.assertEqual(
            set_mainnet_symbol_leverage(
                settings,
                credentials,
                "UNIUSDT",
                "3",
                constructors=constructors,
            ),
            "3",
        )

    def test_rejects_leverage_outside_operator_allowlist(self) -> None:
        constructors = PybitConstructors(
            http=lambda **_kwargs: FakeHttpSession(),
            websocket=lambda **_kwargs: object(),
        )
        settings, credentials = self._settings_and_credentials()
        with self.assertRaisesRegex(ValueError, "supported leverage"):
            set_mainnet_symbol_leverage(
                settings,
                credentials,
                "BTCUSDT",
                "25",
                constructors=constructors,
            )


if __name__ == "__main__":
    unittest.main()
