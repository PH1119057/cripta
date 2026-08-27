from __future__ import annotations

from typing import Any

from bybit_workbench.exchange.bybit.access_diagnostics import (
    MainnetAccessDiagnosticsRunner,
)


class FakeSession:
    def get_api_key_information(self) -> dict[str, Any]:
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "apiKey": "DO-NOT-PRINT",
                "userID": 12345,
                "kycLevel": "LEVEL_2",
                "kycRegion": "KAZ",
                "isMaster": True,
                "readOnly": 0,
                "uta": 1,
                "type": 1,
                "createdAt": "2026-08-15T10:00:00Z",
                "ips": [],
                "permissions": {
                    "ContractTrade": ["Order", "Position"],
                    "Derivatives": ["DerivativesTrade"],
                },
            },
        }

    def get_account_info(self) -> dict[str, Any]:
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "marginMode": "ISOLATED_MARGIN",
                "unifiedMarginStatus": 5,
            },
        }

    def get_instruments_info(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {
                        "symbol": kwargs["symbol"],
                        "status": "Trading",
                        "contractType": "LinearPerpetual",
                        "symbolType": "innovation",
                        "unifiedMarginTrade": True,
                        "leverageFilter": {"maxLeverage": "25"},
                    }
                ]
            },
        }

    def get_account_instruments_info(self, **kwargs: Any) -> dict[str, Any]:
        return self.get_instruments_info(**kwargs)

    def pre_check_order(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["category"] == "linear"
        assert kwargs["reduceOnly"] is False
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "preImrE4": 12,
                "postImrE4": 15,
                "preMmrE4": 8,
                "postMmrE4": 10,
            },
        }


class CrossMarginSession(FakeSession):
    def get_account_info(self) -> dict[str, Any]:
        response = super().get_account_info()
        response["result"]["marginMode"] = "REGULAR_MARGIN"
        return response


def test_access_diagnostics_reports_account_scope_without_secrets() -> None:
    report = MainnetAccessDiagnosticsRunner(
        FakeSession(), "https://api.bybit.com"
    ).run(
        symbol="UNIUSDT",
        side="Sell",
        quantity="1.7",
        price="3.20",
    )

    text = "\n".join(report.lines)
    assert "Endpoint: https://api.bybit.com" in text
    assert "UID: 12345" in text
    assert "KYC: level=LEVEL_2 region=KAZ" in text
    assert "Account availability: FOUND" in text
    assert "Key metadata: type=1 createdAt=2026-08-15T10:00:00Z ipBindings=NONE" in text
    assert "Permissions: ContractTrade=Order,Position Derivatives=DerivativesTrade" in text
    assert "Limit pre-check (NO ORDER): SKIPPED — ISOLATED_MARGIN" in text
    assert "Market pre-check (NO ORDER): SKIPPED — ISOLATED_MARGIN" in text
    assert "/v5/order/pre-check does not support Isolated Margin" in text
    assert "DO-NOT-PRINT" not in text


def test_isolated_margin_diagnostics_never_calls_order_precheck() -> None:
    class NoPrecheckSession(FakeSession):
        def pre_check_order(self, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            raise AssertionError("isolated margin must not call /v5/order/pre-check")

    report = MainnetAccessDiagnosticsRunner(
        NoPrecheckSession(), "https://api.bybit.kz"
    ).run(
        symbol="UNIUSDT",
        side="Buy",
        quantity="1.7",
        price="3.20",
    )

    text = "\n".join(report.lines)
    assert "SKIPPED — ISOLATED_MARGIN" in text


def test_access_diagnostics_detects_public_but_account_missing_symbol() -> None:
    class AccountMissingSession(FakeSession):
        def get_account_instruments_info(self, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {"retCode": 0, "retMsg": "OK", "result": {"list": []}}

    report = MainnetAccessDiagnosticsRunner(
        AccountMissingSession(), "https://api.bybit.kz"
    ).run(
        symbol="UNIUSDT",
        side="Buy",
        quantity="1.7",
        price="3.20",
    )

    text = "\n".join(report.lines)
    assert "Public: status=Trading" in text
    assert "Account availability: NOT FOUND" in text


def test_access_diagnostics_surfaces_precheck_business_rejection() -> None:
    InvalidRequestError = type(
        "InvalidRequestError",
        (RuntimeError,),
        {"__module__": "pybit.exceptions"},
    )

    class RejectedSession(CrossMarginSession):
        def pre_check_order(self, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            error = InvalidRequestError("rejected")
            error.status_code = 10024
            error.message = "regulatory restrictions"
            raise error

    report = MainnetAccessDiagnosticsRunner(
        RejectedSession(), "https://api.bybit.com"
    ).run(
        symbol="UNIUSDT",
        side="Sell",
        quantity="1.7",
        price="3.20",
    )

    text = "\n".join(report.lines)
    assert "Limit pre-check (NO ORDER): FAIL retCode=10024" in text
    assert "Market pre-check (NO ORDER): FAIL retCode=10024" in text
    assert "regulatory restrictions" in text
    assert "failure here did not create or retry an order" in text


def test_access_diagnostics_checks_limit_and_market_without_market_price() -> None:
    class RecordingSession(CrossMarginSession):
        def __init__(self) -> None:
            self.prechecks: list[dict[str, Any]] = []

        def pre_check_order(self, **kwargs: Any) -> dict[str, Any]:
            self.prechecks.append(dict(kwargs))
            return super().pre_check_order(**kwargs)

    session = RecordingSession()
    MainnetAccessDiagnosticsRunner(session, "https://api.bybit.com").run(
        symbol="UNIUSDT",
        side="Buy",
        quantity="1.7",
        price="3.20",
    )

    assert [call["orderType"] for call in session.prechecks] == ["Limit", "Market"]
    assert session.prechecks[0]["price"] == "3.20"
    assert session.prechecks[0]["timeInForce"] == "GTC"
    assert "price" not in session.prechecks[1]
    assert "timeInForce" not in session.prechecks[1]

