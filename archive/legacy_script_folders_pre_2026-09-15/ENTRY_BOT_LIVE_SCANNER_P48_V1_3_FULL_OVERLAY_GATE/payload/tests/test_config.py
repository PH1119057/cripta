import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from bybit_workbench import display_version
from bybit_workbench.app.config import PROFILES, AppSettings
from bybit_workbench.domain.types import AppMode
from bybit_workbench.risk import RiskProfileSettings


class AppSettingsTests(unittest.TestCase):
    def test_display_version_includes_patch_marker(self) -> None:
        self.assertEqual(display_version(), "v0.8.5 · P48")

    def test_pydantic_risk_profile_validates_and_builds_domain_profile(self) -> None:
        settings = RiskProfileSettings(
            profile_name="Desk",
            version="1",
            max_risk_amount=Decimal("25"),
            max_risk_percent=Decimal("0.5"),
            max_position_notional=Decimal("1000"),
            max_leverage=Decimal("2"),
            max_daily_loss=Decimal("100"),
            max_slippage_percent=Decimal("0.1"),
            estimated_fee_rate=Decimal("0.0006"),
        )
        profile = settings.to_domain("btcusdt")
        self.assertIn("BTCUSDT", profile.allowed_symbols)
        with self.assertRaises(ValueError):
            RiskProfileSettings(
                **{
                    **settings.model_dump(),
                    "max_risk_percent": Decimal("101"),
                }
            )

    def test_default_is_offline_and_live_is_locked(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = AppSettings.from_environment()
        self.assertEqual(settings.mode, AppMode.REPLAY)
        self.assertFalse(settings.allow_live_trading)
        self.assertFalse(settings.enable_testnet_execution)
        self.assertFalse(PROFILES[settings.mode].allows_network_orders)

    def test_live_profile_allows_read_only_startup_without_execution_permission(self) -> None:
        settings = AppSettings(mode=AppMode.LIVE, allow_live_trading=False)
        settings.validate_startup()
        self.assertFalse(settings.allow_live_trading)
        self.assertTrue(settings.is_mainnet)

    def test_demo_and_testnet_have_distinct_endpoints(self) -> None:
        self.assertNotEqual(PROFILES[AppMode.DEMO].rest_url, PROFILES[AppMode.TESTNET].rest_url)
        self.assertNotEqual(
            PROFILES[AppMode.DEMO].private_ws_url,
            PROFILES[AppMode.TESTNET].private_ws_url,
        )

    def test_regional_endpoint_override_is_explicit_and_validated(self) -> None:
        settings = AppSettings(
            mode=AppMode.TESTNET,
            rest_url_override="https://regional.example",
            public_ws_url_override="wss://public.example/v5/public/linear",
            private_ws_url_override="wss://private.example/v5/private",
        )
        settings.validate_startup()
        self.assertEqual(settings.endpoint_profile.rest_url, "https://regional.example")
        with self.assertRaises(ValueError):
            AppSettings(
                mode=AppMode.TESTNET,
                rest_url_override="http://insecure.example",
            ).validate_startup()

    def test_kazakhstan_mainnet_rest_selects_matching_websocket_hosts(self) -> None:
        settings = AppSettings(
            mode=AppMode.LIVE,
            rest_url_override="https://api.bybit.kz",
        )
        profile = settings.endpoint_profile
        self.assertEqual(profile.rest_url, "https://api.bybit.kz")
        self.assertEqual(profile.public_ws_url, "wss://stream.bybit.kz/v5/public/linear")
        self.assertEqual(profile.private_ws_url, "wss://stream.bybit.kz/v5/private")

    def test_global_mainnet_rest_selects_matching_websocket_hosts(self) -> None:
        settings = AppSettings(
            mode=AppMode.LIVE,
            rest_url_override="https://api.bybit.com",
        )
        profile = settings.endpoint_profile
        self.assertEqual(profile.rest_url, "https://api.bybit.com")
        self.assertEqual(profile.public_ws_url, "wss://stream.bybit.com/v5/public/linear")
        self.assertEqual(profile.private_ws_url, "wss://stream.bybit.com/v5/private")

    def test_testnet_execution_switch_is_profile_bound(self) -> None:
        settings = AppSettings(
            mode=AppMode.TESTNET,
            enable_testnet_execution=True,
        )
        settings.validate_startup()
        self.assertTrue(settings.testnet_execution_allowed)
        with self.assertRaises(PermissionError):
            AppSettings(
                mode=AppMode.DEMO,
                enable_testnet_execution=True,
            ).validate_startup()


if __name__ == "__main__":
    unittest.main()
