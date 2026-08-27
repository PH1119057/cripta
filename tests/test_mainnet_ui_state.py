import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain.models import Position
from bybit_workbench.domain.types import AppMode, ExecutionMode, PositionSide
from bybit_workbench.exchange.bybit.models import (
    AccountSnapshot,
    ApiKeyInfo,
    ApiKeyPermissionAudit,
    BybitPositionSnapshot,
    MainnetConnectionTestReport,
)
from bybit_workbench.ui.view_model import WorkbenchViewModel


class MainnetUiStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
        self.model = WorkbenchViewModel(AppMode.LIVE)

    def test_execution_phase_and_memory_ticket_expiry_are_visible(self) -> None:
        expires = self.now + timedelta(minutes=2)
        self.model.set_execution_status(
            ExecutionMode.MICRO_LIVE,
            "ARMED",
            "ticket is memory-only",
            expires,
        )
        self.assertEqual(self.model.state.execution_phase, "ARMED")
        self.assertEqual(self.model.state.arming_ticket_expires_at, expires)

    def test_derivatives_trade_permission_is_not_an_arming_blocker(self) -> None:
        permissions = ApiKeyPermissionAudit(
            ("Order", "Position"),
            (),
            (),
            (),
            (("Derivatives", ("DerivativesTrade",)),),
            (),
            (),
        )
        account = AccountSnapshot(
            "UNIFIED",
            Decimal("20"),
            Decimal("20"),
            Decimal("20"),
            Decimal("0"),
            self.now,
        )
        position = BybitPositionSnapshot(
            Position("UNIUSDT", PositionSide.FLAT, Decimal("0"), None),
            0,
            Decimal("1"),
            Decimal("3"),
            None,
            None,
            None,
            None,
            Decimal("0"),
            1,
            self.now,
        )
        report = MainnetConnectionTestReport(
            "https://api.bybit.kz",
            self.now,
            0,
            ApiKeyInfo(
                "BotW-Mainnet",
                False,
                ("redacted",),
                80,
                self.now + timedelta(days=80),
                self.now - timedelta(days=1),
                True,
                None,
                True,
                1,
                permissions,
            ),
            account,
            position,
            0,
            ("server_time", "api_key_info", "wallet_balance", "positions", "open_orders"),
        )

        self.model.apply_connection_test(report)

        self.assertNotIn("Derivatives", " | ".join(self.model.state.arming_blockers))


    def test_missing_api_key_metadata_keeps_ui_read_only_and_arming_blocked(self) -> None:
        account = AccountSnapshot(
            "UNIFIED",
            Decimal("20"),
            Decimal("20"),
            Decimal("20"),
            Decimal("0"),
            self.now,
        )
        position = BybitPositionSnapshot(
            Position("UNIUSDT", PositionSide.FLAT, Decimal("0"), None),
            0,
            Decimal("3"),
            Decimal("3"),
            None,
            None,
            None,
            None,
            Decimal("0"),
            1,
            self.now,
        )
        report = MainnetConnectionTestReport(
            "https://api.bybit.kz",
            self.now,
            0,
            None,
            account,
            position,
            0,
            (
                "server_time",
                "wallet_balance",
                "positions",
                "open_orders",
                "api_key_info_unavailable",
            ),
        )

        self.model.apply_connection_test(report)

        self.assertEqual(self.model.state.api_access, "metadata unavailable")
        self.assertIn(
            "execution arming is blocked",
            " | ".join(self.model.state.arming_blockers),
        )

    def test_excess_permissions_and_account_scope_are_arming_blockers(self) -> None:
        permissions = ApiKeyPermissionAudit(
            ("Order", "Position"),
            ("SpotTrade",),
            (),
            ("OptionsTrade",),
            (("UnknownSurface", ("Write",)),),
            (),
            (),
        )
        account = AccountSnapshot(
            "UNIFIED",
            Decimal("20"),
            Decimal("20"),
            Decimal("20"),
            Decimal("0"),
            self.now,
        )
        position = BybitPositionSnapshot(
            Position("UNIUSDT", PositionSide.FLAT, Decimal("0"), None),
            0,
            Decimal("1"),
            Decimal("3"),
            None,
            None,
            None,
            None,
            Decimal("0"),
            1,
            self.now,
        )
        report = MainnetConnectionTestReport(
            "https://api.bybit.com",
            self.now,
            0,
            ApiKeyInfo(
                "BotW-Mainnet",
                False,
                (),
                80,
                self.now + timedelta(days=80),
                self.now - timedelta(days=1),
                False,
                "parent",
                True,
                1,
                permissions,
            ),
            account,
            position,
            0,
            ("server_time", "api_key_info", "wallet_balance", "positions", "open_orders"),
        )

        self.model.apply_connection_test(report)

        blockers = " | ".join(self.model.state.arming_blockers)
        self.assertIn("subaccount", blockers)
        self.assertIn("Spot", blockers)
        self.assertIn("Options/USDC", blockers)
        self.assertIn("UnknownSurface", blockers)


if __name__ == "__main__":
    unittest.main()
