import unittest
from dataclasses import replace
from decimal import Decimal

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.live_readiness import LiveReadinessGate, LiveReadinessInput
from bybit_workbench.domain.types import AppMode


def ready_input() -> LiveReadinessInput:
    return LiveReadinessInput(
        "LIVE",
        "BTCUSDT",
        Decimal("100"),
        Decimal("50"),
        Decimal("10"),
        True,
        True,
        True,
        True,
        True,
    )


class LiveReadinessTests(unittest.TestCase):
    def test_every_independent_guard_fails_closed(self) -> None:
        settings = AppSettings(mode=AppMode.LIVE, allow_live_trading=True)
        gate = LiveReadinessGate()
        self.assertTrue(gate.require_ready(settings, ready_input()).ready)
        variants = (
            (AppSettings(mode=AppMode.LIVE), ready_input()),
            (settings, replace(ready_input(), confirmation_word="live")),
            (settings, replace(ready_input(), first_trade_notional=Decimal("101"))),
            (settings, replace(ready_input(), fresh_private=False)),
            (settings, replace(ready_input(), reconciliation_complete=False)),
            (settings, replace(ready_input(), withdrawal_permission_absent=False)),
        )
        for selected_settings, request in variants:
            with self.assertRaises(PermissionError):
                gate.require_ready(selected_settings, request)


if __name__ == "__main__":
    unittest.main()
