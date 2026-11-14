import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - exercised in the dependency-free environment
    QApplication = None

from bybit_workbench.__main__ import gui_smoke, parse_args
from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain.types import AppMode, AppState
from bybit_workbench.entry_bot.models import (
    WORKING_SYMBOLS,
    AssetScanStatus,
    EntryBotAssetSnapshot,
    EntryBotSnapshot,
    ScannerState,
)
from bybit_workbench.exchange.fake import FakeExchange
from bybit_workbench.ui.main_window import create_main_window


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_packaged_gui_smoke_entrypoint_is_fail_closed(self) -> None:
        args = parse_args(["--gui-smoke"])
        self.assertTrue(args.gui_smoke)
        self.assertEqual(gui_smoke(AppSettings(mode=AppMode.REPLAY)), 0)

    def test_replay_window_constructs_renders_and_closes(self) -> None:
        exchange = FakeExchange()
        asyncio.run(exchange.connect())
        machine = AppStateMachine()
        machine.transition(AppState.SYNCING, "GUI smoke")
        machine.transition(AppState.READY, "GUI smoke ready")
        window = create_main_window(
            AppSettings(mode=AppMode.REPLAY),
            machine,
            exchange,
        )

        window.show()
        self.app.processEvents()
        rendered = window.grab()

        self.assertFalse(rendered.isNull())
        self.assertEqual(window.windowTitle(), "Bybit Strategy Workbench")
        self.assertEqual(window.version_label.text(), "v0.8.5 · P48.2")
        self.assertFalse(window.execute_button.isEnabled())
        self.assertEqual(window.execute_button.text(), "ИСПОЛНИТЬ СДЕЛКУ")
        self.assertIs(window.run_button, window.execute_button)
        self.assertFalse(window.credentials_button.isEnabled())
        self.assertEqual(window.execution_badge.text(), "SHADOW · DISARMED")
        window.close()
        self.app.processEvents()
        asyncio.run(exchange.disconnect())

    def test_live_endpoint_selector_applies_supported_endpoint(self) -> None:
        machine = AppStateMachine()
        selected = "https://api.bybit.kz"

        def get_endpoint() -> str:
            return selected

        def set_endpoint(value: str) -> str:
            nonlocal selected
            selected = value.strip().rstrip("/")
            return selected

        settings = AppSettings(mode=AppMode.LIVE)
        window = create_main_window(
            settings,
            machine,
            get_mainnet_endpoint=get_endpoint,
            set_mainnet_endpoint=set_endpoint,
        )

        self.assertEqual(window.endpoint_input.currentText(), "https://api.bybit.kz")
        window.endpoint_input.setCurrentText("https://api.bybit.com")
        window._apply_mainnet_endpoint()
        self.assertEqual(selected, "https://api.bybit.com")
        self.assertEqual(window.endpoint_input.currentText(), "https://api.bybit.com")
        self.assertIn("Endpoint сохранён", window.connection_activity.text())
        window.close()

    def test_live_leverage_selector_calls_explicit_mainnet_configuration(self) -> None:
        machine = AppStateMachine()
        calls: list[tuple[str, str]] = []

        def apply_leverage(symbol: str, leverage: str) -> str:
            calls.append((symbol, leverage))
            return leverage

        window = create_main_window(
            AppSettings(mode=AppMode.LIVE),
            machine,
            set_mainnet_leverage=apply_leverage,
        )
        window.leverage_input.setCurrentText("10")
        self.assertEqual(window.leverage_input.currentText(), "10")
        self.assertTrue(window.apply_leverage_button.isEnabled())
        # P48.1 deliberately delegates safety to the explicit mainnet callback.
        # The callback performs fresh server-side position/order checks before writing.
        window._apply_mainnet_leverage()
        self.assertEqual(calls, [("UNIUSDT", "10")])
        self.assertIn("применено", window.leverage_status_label.text())
        window.close()

    def test_live_window_can_auto_connect_read_only_after_construction(self) -> None:
        machine = AppStateMachine()
        calls: list[tuple[str, ...]] = []

        def connect(symbol: str, interval: str) -> None:
            calls.append(("connect", symbol, interval))

        def disconnect() -> None:
            calls.append(("disconnect",))

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = AppSettings(
            mode=AppMode.LIVE,
            rest_url_override="https://api.bybit.kz",
            database_path=Path(tmp.name) / "workbench.db",
        )
        local_app_data = Path(tmp.name) / "LocalAppData"
        with patch.dict(
            os.environ,
            {"LOCALAPPDATA": str(local_app_data)},
            clear=False,
        ):
            window = create_main_window(
                settings,
                machine,
                connect_read_only=connect,
                disconnect_read_only=disconnect,
                get_mainnet_endpoint=lambda: "https://api.bybit.kz",
                auto_connect_read_only=True,
            )

        window.show()
        self.app.processEvents()
        self.assertEqual(calls[0], ("connect", "BTCUSDT", "60"))
        self.assertEqual(window.endpoint_input.currentText(), "https://api.bybit.kz")
        self.assertIn("Подключение", window.connection_activity.text())
        window.close()
        self.app.processEvents()

    def test_live_connection_buttons_show_immediate_feedback(self) -> None:
        machine = AppStateMachine()
        calls: list[tuple[str, ...]] = []

        def connect(symbol: str, interval: str) -> None:
            calls.append(("connect", symbol, interval))

        def disconnect() -> None:
            calls.append(("disconnect",))

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = AppSettings(
            mode=AppMode.LIVE,
            database_path=Path(tmp.name) / "workbench.db",
        )
        local_app_data = Path(tmp.name) / "LocalAppData"
        with patch.dict(
            os.environ,
            {"LOCALAPPDATA": str(local_app_data)},
            clear=False,
        ):
            window = create_main_window(
                settings,
                machine,
                connect_read_only=connect,
                disconnect_read_only=disconnect,
            )

        window._connect_read_only()
        self.assertEqual(calls[0], ("connect", "BTCUSDT", "60"))
        self.assertIn("Подключение", window.connection_activity.text())
        self.assertFalse(window.connect_button.isEnabled())
        self.assertTrue(window.disconnect_button.isEnabled())
        self.assertEqual(window.disconnect_button.text(), "Остановить подключение")

        machine.transition(AppState.SYNCING, "GUI connection test")
        window.refresh_from_model(force=True)
        self.assertEqual(window.engine_badge.text(), "SYNCING")
        self.assertIn("Подключение", window.connection_activity.text())

        machine.transition(AppState.READY, "GUI connection ready")
        window.refresh_from_model(force=True)
        self.assertIn("Подключено", window.connection_activity.text())
        self.assertFalse(window.connect_button.isEnabled())
        self.assertTrue(window.disconnect_button.isEnabled())
        self.assertTrue(window.symbol_input.isEditable())
        self.assertGreaterEqual(window.symbol_input.findText("BTCUSDT"), 0)
        for symbol in (
            "BTCUSDT",
            "ETHUSDT",
            "XRPUSDT",
            "1000PEPEUSDT",
            "SOLUSDT",
            "DOGEUSDT",
            "ADAUSDT",
            "UNIUSDT",
            "LINKUSDT",
            "BNBUSDT",
            "AVAXUSDT",
            "SUIUSDT",
            "AAVEUSDT",
            "LTCUSDT",
        ):
            self.assertGreaterEqual(window.symbol_input.findText(symbol), 0)
        self.assertEqual(window.timeframe_combo.currentText(), "60")
        self.assertEqual(
            [window.leverage_input.itemText(i) for i in range(window.leverage_input.count())],
            ["1", "2", "3", "5", "7", "10"],
        )
        history_path = (
            local_app_data / "BybitStrategyWorkbench" / "symbol_history.json"
        )
        self.assertTrue(history_path.exists())

        window._disconnect_read_only()
        self.assertEqual(calls[-1], ("disconnect",))
        self.assertIn("Отключение", window.connection_activity.text())
        self.assertFalse(window.disconnect_button.isEnabled())
        window.close()

    def test_live_bot_mode_requires_explicit_scanner_start(self) -> None:
        machine = AppStateMachine()
        calls: list[str] = []

        running = False

        def start_bot() -> None:
            nonlocal running
            calls.append("start")
            running = True

        def stop_bot() -> None:
            nonlocal running
            calls.append("stop")
            running = False

        def snapshot() -> EntryBotSnapshot:
            return EntryBotSnapshot(
                state=ScannerState.RUNNING if running else ScannerState.STOPPED,
                running=running,
                detail="screening" if running else "stopped",
                execution_mode="SHADOW · AUTO ENTRY LOCKED",
                assets=tuple(
                    EntryBotAssetSnapshot(symbol=symbol, status=AssetScanStatus.WAITING)
                    for symbol in WORKING_SYMBOLS
                ),
            )

        window = create_main_window(
            AppSettings(mode=AppMode.LIVE),
            machine,
            start_entry_bot=start_bot,
            stop_entry_bot=stop_bot,
            get_entry_bot_snapshot=snapshot,
        )
        window.bot_mode_checkbox.setChecked(True)
        window.refresh_from_model(force=True)
        self.app.processEvents()

        self.assertEqual(calls, [])
        self.assertTrue(window.bot_start_button.isEnabled())
        window.bot_start_button.click()
        window.refresh_from_model(force=True)
        self.app.processEvents()
        self.assertEqual(calls, ["start"])
        self.assertFalse(window.bot_start_button.isEnabled())
        self.assertEqual(window.signal_bot_stack.currentIndex(), 1)
        self.assertEqual(window.bot_assets_table.rowCount(), 10)
        self.assertEqual(window.bot_assets_table.item(0, 0).text(), "UNIUSDT")
        self.assertIn("RUNNING", window.bot_runtime_label.text())
        self.assertIn("AUTO ENTRY LOCKED", window.bot_runtime_label.text())
        window.close()


if __name__ == "__main__":
    unittest.main()
