from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from bybit_workbench import display_version
from bybit_workbench.app.config import (
    MAINNET_GLOBAL_REST_URL,
    MAINNET_KZ_REST_URL,
    AppSettings,
)
from bybit_workbench.app.credentials import BybitCredentials, WindowsCredentialStore
from bybit_workbench.app.state_machine import AppStateMachine, InvalidStateTransition
from bybit_workbench.domain.types import AppMode, AppState, OrderType, PositionSide
from bybit_workbench.entry_bot.models import EntryBotSnapshot
from bybit_workbench.exchange.fake import FakeExchange
from bybit_workbench.risk import RiskProfileSettings
from bybit_workbench.strategies import default_strategy_registry
from bybit_workbench.ui.manual_workflow import (
    ManualTradeDraft,
    ManualTradeWorkflow,
    PreparedManualTrade,
)
from bybit_workbench.ui.market_recommendation import recommend_market_plan
from bybit_workbench.ui.symbol_history import persistent_symbol_history
from bybit_workbench.ui.view_model import UserFacingError, WorkbenchViewModel

_MICRO_LIVE_INTERNAL_CONFIRMATION = "ARM MICRO_LIVE"
_WORKBENCH_SYMBOLS = (
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
)
_LEVERAGE_CHOICES = ("1", "2", "3", "5", "7", "10")


def create_main_window(
    settings: AppSettings,
    state_machine: AppStateMachine,
    exchange: FakeExchange | None = None,
    *,
    view_model: WorkbenchViewModel | None = None,
    credential_store: WindowsCredentialStore | None = None,
    connect_read_only: Callable[[str, str], None] | None = None,
    disconnect_read_only: Callable[[], None] | None = None,
    get_mainnet_endpoint: Callable[[], str] | None = None,
    set_mainnet_endpoint: Callable[[str], str] | None = None,
    set_mainnet_leverage: Callable[[str, str], str] | None = None,
    switch_read_only_market: Callable[[str, str], None] | None = None,
    pump_read_only: Callable[[], None] | None = None,
    start_access_diagnostics: Callable[[dict[str, str]], None] | None = None,
    load_access_diagnostics: Callable[[], tuple[str, ...]] | None = None,
    manual_workflow: ManualTradeWorkflow | None = None,
    prepare_execution: Callable[[PreparedManualTrade], None] | None = None,
    arm_execution: Callable[[str], None] | None = None,
    invalidate_execution: Callable[[str], None] | None = None,
    submit_manual_trade: Callable[[PreparedManualTrade], None] | None = None,
    stop_strategy: Callable[[], None] | None = None,
    cancel_entries: Callable[[str], None] | None = None,
    cancel_non_protective: Callable[[str], None] | None = None,
    flatten_position: Callable[[str], None] | None = None,
    emergency_strategy: Callable[[], None] | None = None,
    load_execution_commands: Callable[[], tuple[str, ...]] | None = None,
    save_risk_profile: Callable[[dict[str, object]], None] | None = None,
    start_backtest: Callable[[dict[str, str]], None] | None = None,
    load_backtest_results: Callable[[], tuple[str, ...]] | None = None,
    start_entry_bot: Callable[[], None] | None = None,
    stop_entry_bot: Callable[[], None] | None = None,
    get_entry_bot_snapshot: Callable[[], EntryBotSnapshot] | None = None,
    auto_connect_read_only: bool = False,
) -> Any:
    """Create the desktop workbench without importing Qt in headless processes."""

    try:
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QApplication,
            QCheckBox,
            QComboBox,
            QCompleter,
            QDialog,
            QDialogButtonBox,
            QFileDialog,
            QFormLayout,
            QFrame,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QHeaderView,
            QLabel,
            QLineEdit,
            QListWidget,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QScrollArea,
            QSplitter,
            QStackedWidget,
            QStatusBar,
            QTableWidget,
            QTableWidgetItem,
            QTabWidget,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        raise RuntimeError(
            "PySide6 is not installed. Install project dependencies or run --headless."
        ) from exc

    model = view_model or WorkbenchViewModel(
        settings.mode,
        symbol=exchange.symbol if exchange else "BTCUSDT",
    )
    store = credential_store or WindowsCredentialStore()
    workflow = manual_workflow or ManualTradeWorkflow(state_machine, model)
    symbol_history = persistent_symbol_history(
        settings.database_path.parent / "symbol_history.json"
    )

    class TextComboBox(QComboBox):
        def text(self) -> str:
            return self.currentText()

    class SymbolComboBox(TextComboBox):
        pass

    class LeverageComboBox(TextComboBox):
        pass

    class CredentialDialog(QDialog):
        def __init__(self, parent: QWidget) -> None:
            super().__init__(parent)
            self.profile_name = (
                settings.credential_profile_name
                if settings.mode is AppMode.LIVE
                else settings.mode.value
            )
            self.setWindowTitle(f"Профиль ключей: {self.profile_name}")
            self.setMinimumWidth(460)
            layout = QVBoxLayout(self)
            note = QLabel(
                "Ключ и секрет сохраняются только в системном хранилище учётных "
                "данных. В SQLite и журнал приложения они не записываются."
            )
            note.setWordWrap(True)
            layout.addWidget(note)
            form = QFormLayout()
            self.key = QLineEdit()
            self.key.setPlaceholderText("API key")
            self.secret = QLineEdit()
            self.secret.setEchoMode(QLineEdit.EchoMode.Password)
            self.secret.setPlaceholderText("API secret")
            form.addRow("API key", self.key)
            form.addRow("API secret", self.secret)
            layout.addLayout(form)
            self.current = QLabel("Профиль ещё не сохранён")
            layout.addWidget(self.current)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save
                | QDialogButtonBox.StandardButton.Cancel
                | QDialogButtonBox.StandardButton.Reset
            )
            buttons.accepted.connect(self._save)
            buttons.rejected.connect(self.reject)
            buttons.button(QDialogButtonBox.StandardButton.Reset).setText("Удалить")
            buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self._delete)
            layout.addWidget(buttons)
            self._load_masked()

        def _load_masked(self) -> None:
            try:
                saved = store.load(
                    settings.mode,
                    name=self.profile_name if settings.mode is AppMode.LIVE else None,
                )
            except Exception as exc:
                self.current.setText(f"Не удалось прочитать профиль: {_safe_error(exc)}")
                return
            self.current.setText(
                "Профиль ещё не сохранён"
                if saved is None
                else f"Сохранён ключ {saved.masked_key}; секрет скрыт"
            )

        def _save(self) -> None:
            try:
                credentials = BybitCredentials(
                    settings.mode,
                    self.key.text(),
                    self.secret.text(),
                    self.profile_name if settings.mode is AppMode.LIVE else None,
                )
                store.save(credentials)
            except Exception as exc:
                QMessageBox.critical(self, "Ключи не сохранены", _safe_error(exc))
                return
            self.secret.clear()
            self.accept()

        def _delete(self) -> None:
            answer = QMessageBox.question(
                self,
                "Удалить профиль?",
                f"Удалить ключи профиля {settings.mode.value} из системного хранилища?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                store.delete(
                    settings.mode,
                    name=self.profile_name if settings.mode is AppMode.LIVE else None,
                )
            except Exception as exc:
                QMessageBox.critical(self, "Профиль не удалён", _safe_error(exc))
                return
            self.key.clear()
            self.secret.clear()
            self._load_masked()

    class MainWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setObjectName("workbenchMainWindow")
            self.setWindowTitle("Bybit Strategy Workbench")
            self.resize(1480, 920)
            self.setMinimumSize(1120, 720)
            self._last_state: Any = None
            self._connection_action = "idle"
            self._access_diagnostics_active = False
            self._pending_auto_recommendation = True
            self._last_recommendation_market: tuple[str, str] | None = None
            self._recommended_direction: PositionSide | None = None
            self._syncing_linked_fields = False
            self._syncing_size_fields = False
            self._size_link_mode = "auto"
            self._trade_pipeline_active = False
            self._trade_pipeline_stage = "idle"
            self._trade_pipeline_prepared: PreparedManualTrade | None = None
            self._chart: Any = None
            self._chart_note: Any = None
            self._candle_item: Any = None
            self._chart_levels: dict[str, Any] = {}
            self._chart_market_signature: tuple[str, str] | None = None
            self._risk_region: Any = None
            self._build_ui()
            self._apply_theme()
            self.setStatusBar(QStatusBar())
            self.version_label = QLabel(display_version())
            self.version_label.setObjectName("versionLabel")
            self.version_label.setToolTip("Версия исходного кода и последний установленный патч")
            self.statusBar().addPermanentWidget(self.version_label)
            if settings.mode is AppMode.LIVE:
                message = (
                    "Mainnet runtime подключён; ручная сделка выполняется одной кнопкой"
                    if settings.allow_live_trading and prepare_execution is not None
                    else "Mainnet SHADOW; внешний live-switch или historical gate закрыт"
                )
            elif settings.testnet_execution_allowed:
                message = "Testnet write включён; ручная сделка выполняется одной кнопкой"
            else:
                message = "Read-only foundation; биржевые торговые команды заблокированы"
            self.statusBar().showMessage(message)

            self._refresh_timer = QTimer(self)
            self._refresh_timer.timeout.connect(self.refresh_from_model)
            self._refresh_timer.start(250)
            self._fake_timer = QTimer(self)
            if settings.mode is AppMode.REPLAY and exchange is not None:
                self._fake_timer.timeout.connect(self._next_fake_candle)
                self._fake_timer.start(1000)
            self.refresh_from_model(force=True)
            if (
                auto_connect_read_only
                and settings.mode is AppMode.LIVE
                and connect_read_only is not None
            ):
                QTimer.singleShot(0, self._connect_read_only)

        @property
        def view_model(self) -> WorkbenchViewModel:
            return model

        def _build_ui(self) -> None:
            root = QWidget()
            outer = QVBoxLayout(root)
            outer.setContentsMargins(10, 10, 10, 10)
            outer.setSpacing(8)
            outer.addWidget(self._build_header())

            self.error_banner = QLabel()
            self.error_banner.setObjectName("errorBanner")
            self.error_banner.setWordWrap(True)
            self.error_banner.hide()
            outer.addWidget(self.error_banner)

            workspace = QSplitter(Qt.Orientation.Horizontal)
            signal_panel = self._build_right_panel()
            trade_panel = self._build_left_panel()
            center_stack = QSplitter(Qt.Orientation.Vertical)
            center_stack.addWidget(self._build_center_panel())
            center_stack.addWidget(self._build_bottom_tabs())
            center_stack.setStretchFactor(0, 3)
            center_stack.setStretchFactor(1, 2)
            center_stack.setSizes([590, 280])

            # Trading-terminal layout: diagnostics on the left, chart/order workspace
            # in the centre, trade controls on the right. Side panels stay full-height
            # while the lower positions/orders tabs are constrained to the centre.
            workspace.addWidget(signal_panel)
            workspace.addWidget(center_stack)
            workspace.addWidget(trade_panel)
            workspace.setStretchFactor(0, 0)
            workspace.setStretchFactor(1, 1)
            workspace.setStretchFactor(2, 0)
            workspace.setSizes([360, 760, 430])
            outer.addWidget(workspace, 1)
            self.setCentralWidget(root)

        def _build_header(self) -> QWidget:
            frame = QFrame()
            frame.setObjectName("header")
            layout = QHBoxLayout(frame)
            self.mode_badge = QLabel(settings.mode.value.upper())
            self.mode_badge.setObjectName("modeBadge")
            self.engine_badge = QLabel(state_machine.state.value)
            self.engine_badge.setObjectName("engineBadge")
            self.execution_badge = QLabel("SHADOW · DISARMED")
            self.execution_badge.setObjectName(
                "executionEnabledBadge"
                if settings.testnet_execution_allowed
                or (settings.mode is AppMode.LIVE and settings.allow_live_trading)
                else "engineBadge"
            )
            layout.addWidget(self.mode_badge)
            layout.addWidget(self.engine_badge)
            layout.addWidget(self.execution_badge)
            self.health_labels: dict[str, QLabel] = {}
            for channel, title in (
                ("public", "Public WS"),
                ("private", "Private WS"),
                ("rest", "REST"),
            ):
                label = QLabel(f"● {title}")
                label.setObjectName("healthIndicator")
                self.health_labels[channel] = label
                layout.addWidget(label)
            self.clock_label = QLabel("Clock —")
            self.clock_label.setObjectName("healthIndicator")
            self.clock_label.setToolTip("Разница между локальными часами и временем Bybit")
            layout.addWidget(self.clock_label)
            layout.addStretch(1)
            self.equity_label = _metric("Equity", "—")
            self.available_label = _metric("Available", "—")
            self.pnl_label = _metric("Daily PnL", "—")
            layout.addWidget(self.equity_label)
            layout.addWidget(self.available_label)
            layout.addWidget(self.pnl_label)
            self.pause_button = QPushButton("Пауза входов")
            self.pause_button.clicked.connect(self._pause)
            self.cancel_entries_button = QPushButton("Отменить входы")
            self.cancel_entries_button.clicked.connect(self._cancel_entries)
            self.cancel_orders_button = QPushButton("Отменить non-protective")
            self.cancel_orders_button.clicked.connect(self._cancel_non_protective)
            self.flatten_button = QPushButton("Закрыть позицию")
            self.flatten_button.clicked.connect(self._flatten_position)
            self.emergency_button = QPushButton("EMERGENCY STOP")
            self.emergency_button.setObjectName("dangerButton")
            self.emergency_button.clicked.connect(self._emergency)
            layout.addWidget(self.pause_button)
            layout.addWidget(self.cancel_entries_button)
            layout.addWidget(self.cancel_orders_button)
            layout.addWidget(self.flatten_button)
            layout.addWidget(self.emergency_button)
            return frame

        def _build_left_panel(self) -> QWidget:
            panel = QWidget()
            panel.setMinimumWidth(420)
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(7)

            self.left_tabs = QTabWidget()
            self.left_tabs.setDocumentMode(True)

            market = QWidget()
            market_layout = QVBoxLayout(market)
            market_layout.setContentsMargins(8, 8, 8, 8)
            market_layout.setSpacing(7)

            side_row = QWidget()
            side_layout = QHBoxLayout(side_row)
            side_layout.setContentsMargins(0, 0, 0, 0)
            self.buy_button = QPushButton("КУПИТЬ · LONG")
            self.buy_button.setObjectName("buyButton")
            self.sell_button = QPushButton("ПРОДАТЬ · SHORT")
            self.sell_button.setObjectName("sellButton")
            self.buy_button.clicked.connect(lambda: self._select_direction(PositionSide.LONG))
            self.sell_button.clicked.connect(lambda: self._select_direction(PositionSide.SHORT))
            side_layout.addWidget(self.buy_button)
            side_layout.addWidget(self.sell_button)
            market_layout.addWidget(side_row)

            market_form = QFormLayout()
            self.symbol_input = SymbolComboBox()
            self.symbol_input.setEditable(True)
            self.symbol_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            recent_symbols = list(symbol_history.load())
            initial_symbol = recent_symbols[0] if recent_symbols else model.state.symbol
            symbol_choices = list(_WORKBENCH_SYMBOLS)
            for remembered in recent_symbols:
                if remembered not in symbol_choices:
                    symbol_choices.append(remembered)
            if initial_symbol not in symbol_choices:
                symbol_choices.insert(0, initial_symbol)
            self.symbol_input.addItems(symbol_choices)
            self.symbol_input.setCurrentText(initial_symbol)
            if initial_symbol != model.state.symbol:
                model.set_market(initial_symbol, model.state.timeframe)
            self.symbol_input.setToolTip(
                "Введите символ или выберите ранее использованный. "
                "Поиск работает по любой части названия."
            )
            completer = self.symbol_input.completer()
            if completer is not None:
                completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                completer.setFilterMode(Qt.MatchFlag.MatchContains)
                completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            self.timeframe_combo = QComboBox()
            self.timeframe_combo.addItems(["1", "3", "5", "15", "30", "60", "240", "D"])
            self.timeframe_combo.setCurrentText(model.state.timeframe)
            self.timeframe_combo.setToolTip(
                "По умолчанию открывается 1H (60 минут), чтобы сначала видеть картину дня."
            )
            self.strategy_combo = QComboBox()
            for registration in default_strategy_registry().registrations():
                self.strategy_combo.addItem(
                    registration.metadata.display_name,
                    registration.metadata.strategy_id,
                )
            self.entry_order_type_combo = QComboBox()
            self.entry_order_type_combo.addItem("Лимитный", OrderType.LIMIT.value)
            self.entry_order_type_combo.addItem("Рыночный", OrderType.MARKET.value)
            self.entry_order_type_combo.setCurrentIndex(0)
            self.entry_order_type_combo.setToolTip(
                "Limit отправляет указанную Entry как цену заявки. Market не отправляет price; "
                "Entry остаётся расчётной ценой для Risk Gate, Stop и Take profit."
            )
            self.direction_combo = QComboBox()
            self.direction_combo.addItems([PositionSide.LONG.value, PositionSide.SHORT.value])
            market_form.addRow("Инструмент", self.symbol_input)
            market_form.addRow("Таймфрейм", self.timeframe_combo)
            market_form.addRow("Стратегия", self.strategy_combo)
            market_form.addRow("Тип входа", self.entry_order_type_combo)
            market_layout.addLayout(market_form)

            price_box = QGroupBox("План сделки")
            price_grid = QGridLayout(price_box)
            price_grid.setColumnStretch(1, 2)
            price_grid.setColumnStretch(2, 1)
            price_grid.addWidget(QLabel(""), 0, 0)
            price_grid.addWidget(QLabel("Цена, USDT"), 0, 1)
            price_grid.addWidget(QLabel("%"), 0, 2)
            self.entry_input = QLineEdit()
            self.entry_input.setPlaceholderText("авто / вручную")
            self.entry_percent_input = QLineEdit()
            self.entry_percent_input.setPlaceholderText("от Mark")
            self.stop_input = QLineEdit()
            self.stop_input.setPlaceholderText("hard stop")
            self.stop_percent_input = QLineEdit()
            self.stop_percent_input.setPlaceholderText("от Entry")
            self.take_profit_input = QLineEdit()
            self.take_profit_input.setPlaceholderText("необязательно")
            self.take_profit_percent_input = QLineEdit()
            self.take_profit_percent_input.setPlaceholderText("от Entry")
            for row, (title, value_widget, percent_widget) in enumerate(
                (
                    ("Entry", self.entry_input, self.entry_percent_input),
                    ("Stop", self.stop_input, self.stop_percent_input),
                    ("Take profit", self.take_profit_input, self.take_profit_percent_input),
                ),
                start=1,
            ):
                price_grid.addWidget(QLabel(title), row, 0)
                price_grid.addWidget(value_widget, row, 1)
                price_grid.addWidget(percent_widget, row, 2)
            self.entry_percent_input.setToolTip(
                "Entry в процентах относительно текущего Mark. Цена и процент связаны."
            )
            self.stop_percent_input.setToolTip(
                "Stop в процентах относительно Entry. Для Long обычно отрицательный, "
                "для Short положительный."
            )
            self.take_profit_percent_input.setToolTip(
                "Take profit в процентах относительно Entry. Для Long обычно положительный, "
                "для Short отрицательный."
            )
            market_layout.addWidget(price_box)
            self.market_execution_hint = QLabel(
                "Limit: Entry отправляется как цена GTC-заявки."
            )
            self.market_execution_hint.setWordWrap(True)
            self.market_execution_hint.setObjectName("muted")
            market_layout.addWidget(self.market_execution_hint)

            size_box = QGroupBox("Размер входа")
            size_grid = QGridLayout(size_box)
            size_grid.addWidget(QLabel("USDT"), 0, 0)
            size_grid.addWidget(QLabel("% доступного депозита"), 0, 1)
            self.position_notional_input = QLineEdit()
            self.position_notional_input.setPlaceholderText("авто по риску")
            self.position_percent_input = QLineEdit()
            self.position_percent_input.setPlaceholderText("авто")
            self.position_notional_input.setToolTip(
                "Планируемый размер позиции в USDT. Пусто = размер рассчитывается "
                "автоматически по Risk / trade и расстоянию до Stop."
            )
            self.position_percent_input.setToolTip(
                "Тот же размер позиции как процент от Available balance. "
                "Поля USDT и % связаны между собой."
            )
            size_grid.addWidget(self.position_notional_input, 1, 0)
            size_grid.addWidget(self.position_percent_input, 1, 1)
            market_layout.addWidget(size_box)

            self.recommend_button = QPushButton("Обновить рекомендации по рынку")
            self.recommend_button.setToolTip(
                "Trend+ATR: использует свежий Mark и последние закрытые свечи; "
                "Arm автоматически не выполняется."
            )
            self.recommend_button.clicked.connect(self._recommend_market_plan)
            market_layout.addWidget(self.recommend_button)
            self.recommendation_label = QLabel(
                "Автоплан появится после read-only синхронизации выбранной монеты."
            )
            self.recommendation_label.setWordWrap(True)
            self.recommendation_label.setObjectName("muted")
            market_layout.addWidget(self.recommendation_label)
            market_layout.addStretch(1)

            parameters = QWidget()
            parameter_form = QFormLayout(parameters)
            self.leverage_input = LeverageComboBox()
            self.leverage_input.addItems(list(_LEVERAGE_CHOICES))
            self.leverage_input.setCurrentText("1")
            self.leverage_input.setToolTip(
                "Плечо 1x/2x/3x/5x/7x/10x. Плечо не меняет размер позиции само по себе: "
                "денежный риск задаётся risk-budget и stop-distance."
            )
            leverage_row = QWidget()
            leverage_layout = QHBoxLayout(leverage_row)
            leverage_layout.setContentsMargins(0, 0, 0, 0)
            leverage_layout.setSpacing(6)
            leverage_layout.addWidget(self.leverage_input)
            self.apply_leverage_button = QPushButton("Применить к Bybit")
            self.apply_leverage_button.setEnabled(
                settings.mode is AppMode.LIVE and set_mainnet_leverage is not None
            )
            self.apply_leverage_button.clicked.connect(self._apply_mainnet_leverage)
            leverage_layout.addWidget(self.apply_leverage_button)
            self.leverage_status_label = QLabel("Bybit: —")
            self.leverage_status_label.setObjectName("muted")
            leverage_layout.addWidget(self.leverage_status_label)
            self.risk_amount_input = QLineEdit("0")
            self.risk_percent_input = QLineEdit("1.00")
            self.max_notional_input = QLineEdit(
                "6"
                if settings.mode is AppMode.LIVE and settings.allow_live_trading
                else "1000"
            )
            self.max_slippage_input = QLineEdit("0.1")
            self.max_slippage_input.setToolTip(
                "Только внутренний резерв Risk Engine для расчёта возможного убытка. "
                "Это значение не передаётся Bybit как slippageTolerance."
            )
            self.daily_loss_percent_input = QLineEdit("3.00")
            self.daily_loss_percent_input.setToolTip(
                "Дневной лимит потерь как процент текущего Equity. "
                "По умолчанию 3%: после достижения лимита новые входы блокируются."
            )
            self.daily_loss_input = QLineEdit("0")
            self.daily_loss_input.setToolTip(
                "Дополнительный абсолютный дневной лимит в USDT. 0 отключает этот cap; "
                "процентный лимит остаётся активным."
            )
            self.fee_rate_input = QLineEdit("0.0006")
            self.risk_amount_input.setToolTip(
                "Optional absolute emergency cap. 0 disables this cap; "
                "percentage risk remains active."
            )
            self.risk_percent_input.setToolTip(
                "Editable percentage of current equity used to size the trade. Default: 1.00%."
            )
            parameter_form.addRow("Absolute risk cap, USDT (0=off)", self.risk_amount_input)
            parameter_form.addRow("Risk / trade, %", self.risk_percent_input)
            parameter_form.addRow("Max notional", self.max_notional_input)
            parameter_form.addRow("Risk slippage reserve, %", self.max_slippage_input)
            parameter_form.addRow("Daily loss, % equity", self.daily_loss_percent_input)
            parameter_form.addRow("Daily loss cap, USDT (0=off)", self.daily_loss_input)
            parameter_form.addRow("Estimated fee rate", self.fee_rate_input)
            parameter_form.addRow("Leverage", leverage_row)
            self.risk_profile_combo = QComboBox()
            self.risk_profile_combo.addItems(["Default", "Conservative"])
            parameter_form.addRow("Risk profile", self.risk_profile_combo)
            self.save_risk_button = QPushButton("Сохранить версию risk-профиля")
            self.save_risk_button.setEnabled(save_risk_profile is not None)
            self.save_risk_button.clicked.connect(self._save_risk_profile)
            parameter_form.addRow(self.save_risk_button)

            connection = QWidget()
            connection_layout = QVBoxLayout(connection)
            self.endpoint_input = QComboBox()
            self.endpoint_input.setEditable(True)
            self.endpoint_input.addItems(
                [MAINNET_KZ_REST_URL, MAINNET_GLOBAL_REST_URL]
            )
            initial_endpoint = (
                get_mainnet_endpoint()
                if get_mainnet_endpoint is not None
                else settings.endpoint_profile.rest_url
            )
            if initial_endpoint:
                self.endpoint_input.setCurrentText(initial_endpoint)
            self.endpoint_input.setToolTip(
                "REST endpoint для Mainnet. Для api.bybit.kz и api.bybit.com "
                "соответствующий WebSocket выбирается автоматически."
            )
            self.apply_endpoint_button = QPushButton("Применить endpoint")
            self.apply_endpoint_button.setEnabled(
                settings.mode is AppMode.LIVE and set_mainnet_endpoint is not None
            )
            self.apply_endpoint_button.clicked.connect(self._apply_mainnet_endpoint)
            connection_layout.addWidget(QLabel("Mainnet REST endpoint"))
            connection_layout.addWidget(self.endpoint_input)
            connection_layout.addWidget(self.apply_endpoint_button)
            endpoint_hint = QLabel(
                "LIVE запускается на .kz и автоматически подключает read-only. "
                ".com можно выбрать вручную после отключения read-only; "
                "при следующем запуске снова будет выбран .kz."
            )
            endpoint_hint.setWordWrap(True)
            endpoint_hint.setObjectName("muted")
            connection_layout.addWidget(endpoint_hint)
            self.connection_activity = QLabel("Отключено. Нажмите «Подключить read-only».")
            self.connection_activity.setWordWrap(True)
            self.connection_activity.setObjectName("connectionActivity")
            self.connection_details = QLabel(
                f"Endpoint: {initial_endpoint or 'offline'}\n"
                "Права: не проверены\nArming: SHADOW / disarmed"
            )
            self.connection_details.setWordWrap(True)
            self.connection_details.setObjectName("muted")
            self.credentials_button = QPushButton("Профиль API-ключей")
            self.credentials_button.setEnabled(settings.mode is not AppMode.REPLAY)
            self.credentials_button.clicked.connect(self._open_credentials)
            self.connect_button = QPushButton("Подключить read-only")
            self.connect_button.setEnabled(
                settings.mode is not AppMode.REPLAY and connect_read_only is not None
            )
            self.connect_button.clicked.connect(self._connect_read_only)
            self.disconnect_button = QPushButton("Отключить")
            self.disconnect_button.setEnabled(
                settings.mode is not AppMode.REPLAY and disconnect_read_only is not None
            )
            self.disconnect_button.clicked.connect(self._disconnect_read_only)
            connection_layout.addWidget(self.connection_activity)
            connection_layout.addWidget(self.connection_details)
            connection_layout.addWidget(self.credentials_button)
            connection_layout.addWidget(self.connect_button)
            connection_layout.addWidget(self.disconnect_button)
            self.access_diagnostics_button = QPushButton("Диагностика доступа")
            self.access_diagnostics_button.setEnabled(
                settings.mode is AppMode.LIVE and start_access_diagnostics is not None
            )
            self.access_diagnostics_button.clicked.connect(self._run_access_diagnostics)
            self.access_diagnostics_output = QTextEdit()
            self.access_diagnostics_output.setReadOnly(True)
            self.access_diagnostics_output.setMinimumHeight(180)
            self.access_diagnostics_output.setPlaceholderText(
                "KYC, доступность инструмента для аккаунта и /v5/order/pre-check. "
                "Диагностика не создаёт ордеров."
            )
            connection_layout.addWidget(self.access_diagnostics_button)
            connection_layout.addWidget(self.access_diagnostics_output)
            connection_layout.addStretch(1)

            self.left_tabs.addTab(market, "Рынок")
            self.left_tabs.addTab(parameters, "Параметры")
            self.left_tabs.addTab(connection, "Подключение")
            layout.addWidget(self.left_tabs, 1)

            controls = QGroupBox("Управление сделкой")
            controls_layout = QGridLayout(controls)
            self.execute_button = QPushButton("ИСПОЛНИТЬ СДЕЛКУ")
            self.execute_button.setObjectName("executeTradeButton")
            self.execute_button.clicked.connect(self._execute_manual_trade)
            self.stop_button = QPushButton("Остановить / отменить активное")
            self.stop_button.clicked.connect(self._stop_strategy)
            self.trade_activity = QLabel(
                "Готово. После подтверждения все проверки и отправка выполняются автоматически."
            )
            self.trade_activity.setWordWrap(True)
            self.trade_activity.setObjectName("tradeActivity")
            controls_layout.addWidget(self.execute_button, 0, 0, 1, 2)
            controls_layout.addWidget(self.stop_button, 1, 0, 1, 2)
            controls_layout.addWidget(self.trade_activity, 2, 0, 1, 2)
            layout.addWidget(controls)

            # Compatibility aliases used by packaged smoke checks. There are no separate
            # Check / Arm / Run buttons in the visible UI anymore.
            self.check_button = self.execute_button
            self.arm_button = self.execute_button
            self.run_button = self.execute_button

            symbol_line_edit = self.symbol_input.lineEdit()
            if symbol_line_edit is not None:
                symbol_line_edit.textEdited.connect(self._invalidate_manual_plan)
                symbol_line_edit.editingFinished.connect(self._market_selection_changed)
            self.symbol_input.activated.connect(self._market_selection_changed)
            for key, field in (
                ("entry", self.entry_input),
                ("stop", self.stop_input),
                ("take_profit", self.take_profit_input),
            ):
                field.textEdited.connect(
                    lambda _text, price_key=key: self._linked_price_edited(price_key)
                )
            for key, field in (
                ("entry", self.entry_percent_input),
                ("stop", self.stop_percent_input),
                ("take_profit", self.take_profit_percent_input),
            ):
                field.textEdited.connect(
                    lambda _text, price_key=key: self._linked_percent_edited(price_key)
                )
            self.position_notional_input.textEdited.connect(self._position_notional_edited)
            self.position_percent_input.textEdited.connect(self._position_percent_edited)
            self.leverage_input.currentTextChanged.connect(self._leverage_selection_changed)
            for field in (
                self.risk_amount_input,
                self.risk_percent_input,
                self.max_notional_input,
                self.max_slippage_input,
                self.daily_loss_input,
                self.fee_rate_input,
            ):
                field.textEdited.connect(self._invalidate_manual_plan)
            self.direction_combo.currentTextChanged.connect(self._invalidate_manual_plan)
            self.entry_order_type_combo.currentIndexChanged.connect(
                self._entry_order_type_changed
            )
            self.timeframe_combo.currentTextChanged.connect(self._invalidate_manual_plan)
            self.timeframe_combo.currentTextChanged.connect(self._market_selection_changed)
            self.strategy_combo.currentIndexChanged.connect(self._invalidate_manual_plan)

            if settings.mode is AppMode.LIVE:
                safe_text = (
                    "Mainnet: одна кнопка выполняет Risk Gate → GET-only preflight → "
                    "in-memory arming → отправку. Перед реальными деньгами остаётся одно "
                    "короткое подтверждение."
                    if settings.allow_live_trading
                    else "Mainnet остаётся SHADOW: внешний live-switch выключен. "
                    "Интерфейс не может снять эту блокировку."
                )
            elif settings.testnet_execution_allowed:
                safe_text = (
                    "Testnet write-switch включён. Проверки и отправка объединены "
                    "в одну команду."
                )
            else:
                safe_text = (
                    "Торговые кнопки заблокированы внешним write-switch. "
                    "Интерфейс не может снять эту блокировку."
                )
            safe_note = QLabel(safe_text)
            safe_note.setWordWrap(True)
            safe_note.setObjectName("muted")
            layout.addWidget(safe_note)
            return panel

        def _build_center_panel(self) -> QWidget:
            frame = QWidget()
            layout = QVBoxLayout(frame)
            quote = QFrame()
            quote_layout = QHBoxLayout(quote)
            self.symbol_label = QLabel(model.state.symbol)
            self.symbol_label.setObjectName("sectionTitle")
            self.direction_badge = QLabel("")
            self.direction_badge.setObjectName("directionBadge")
            self.direction_badge.setVisible(False)
            self.last_price_label = QLabel("—")
            self.last_price_label.setObjectName("price")
            self.mark_price_label = QLabel("Mark —")
            quote_layout.addWidget(self.symbol_label)
            quote_layout.addStretch(1)
            quote_layout.addWidget(self.direction_badge)
            quote_layout.addWidget(self.last_price_label)
            quote_layout.addWidget(self.mark_price_label)
            layout.addWidget(quote)
            try:
                import pyqtgraph as pg  # type: ignore[import-untyped]

                pg.setConfigOption("background", "#0f141c")
                pg.setConfigOption("foreground", "#7f8b9d")
                date_axis = pg.DateAxisItem(orientation="bottom")
                self._chart = pg.PlotWidget(axisItems={"bottom": date_axis})
                self._chart.showGrid(x=True, y=True, alpha=0.11)
                plot_item = self._chart.getPlotItem()
                plot_item.showAxis("right")
                plot_item.getAxis("left").setStyle(showValues=False)
                plot_item.getAxis("left").setWidth(10)
                plot_item.getAxis("right").setLabel("Price")
                plot_item.getAxis("right").setStyle(tickTextOffset=8)
                self._chart.setLabel("bottom", "Time")
                self._chart.setMouseEnabled(x=True, y=True)
                plot_item.setClipToView(True)
                from bybit_workbench.ui.candlestick import CandlestickItem

                self._candle_item = CandlestickItem()
                self._chart.addItem(self._candle_item)
                for key, color, label, width in (
                    ("mark", "#d8e3f0", "Mark {value:0.6g}", 1.0),
                    ("entry", "#f0b90b", "Entry {value:0.6g}", 1.4),
                    ("average", "#60a5fa", "Average {value:0.6g}", 1.0),
                    ("stop", "#f6465d", "Stop {value:0.6g}", 1.4),
                    ("take_profit", "#2ebd85", "TP {value:0.6g}", 1.4),
                    ("liquidation", "#f97316", "Liq {value:0.6g}", 1.0),
                ):
                    line = pg.InfiniteLine(
                        angle=0,
                        movable=key == "stop",
                        pen=pg.mkPen(color, width=width),
                        label=label,
                        labelOpts={"color": color, "position": 0.985},
                    )
                    line.setVisible(False)
                    self._chart.addItem(line)
                    self._chart_levels[key] = line
                self._chart_levels["stop"].sigPositionChangeFinished.connect(
                    self._on_planned_stop_dragged
                )
                self._risk_region = pg.LinearRegionItem(
                    values=(0, 0),
                    orientation=pg.LinearRegionItem.Horizontal,
                    movable=False,
                    brush=pg.mkBrush(246, 70, 93, 18),
                    pen=pg.mkPen(None),
                )
                self._risk_region.setVisible(False)
                self._chart.addItem(self._risk_region)
                layout.addWidget(self._chart, 1)
            except ImportError:
                self._chart_note = QLabel(
                    "График появится после установки pyqtgraph.\n"
                    "Модель и поток данных продолжают работать без него."
                )
                self._chart_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._chart_note.setObjectName("chartFallback")
                layout.addWidget(self._chart_note, 1)

            position = QGroupBox("Текущая позиция")
            grid = QGridLayout(position)
            self.position_values: dict[str, QLabel] = {}
            for index, (key, title) in enumerate(
                (
                    ("side", "Side"),
                    ("quantity", "Quantity"),
                    ("average", "Average"),
                    ("mark", "Mark"),
                    ("liquidation", "Liquidation"),
                    ("upnl", "Unrealized PnL"),
                )
            ):
                title_label = QLabel(title)
                title_label.setObjectName("muted")
                value_label = QLabel("—")
                value_label.setObjectName("metricValue")
                self.position_values[key] = value_label
                grid.addWidget(title_label, 0, index)
                grid.addWidget(value_label, 1, index)
            layout.addWidget(position)
            return frame

        def _build_right_panel(self) -> QWidget:
            panel = QWidget()
            panel.setMinimumWidth(350)
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)

            mode_box = QGroupBox("Левая панель")
            mode_layout = QHBoxLayout(mode_box)
            self.bot_mode_checkbox = QCheckBox("BOT MODE · 10 монет")
            self.bot_mode_checkbox.setToolTip(
                "Показывает независимый live-скринер Entry V1. BTC/ETH остаются "
                "рыночными reference-активами и не входят в торговую десятку."
            )
            self.bot_mode_checkbox.setEnabled(get_entry_bot_snapshot is not None)
            self.bot_mode_checkbox.toggled.connect(self._set_bot_mode)
            self.bot_mode_badge = QLabel("MANUAL")
            self.bot_mode_badge.setObjectName("engineBadge")
            mode_layout.addWidget(self.bot_mode_checkbox)
            mode_layout.addStretch(1)
            mode_layout.addWidget(self.bot_mode_badge)
            layout.addWidget(mode_box)

            self.signal_bot_stack = QStackedWidget()
            layout.addWidget(self.signal_bot_stack, 1)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            contents = QWidget()
            manual_layout = QVBoxLayout(contents)
            signal = QGroupBox("Сигнал и план")
            form = QFormLayout(signal)
            self.signal_label = QLabel("Нет сигнала")
            self.reason_label = QLabel("Ожидание закрытой свечи")
            self.reason_label.setWordWrap(True)
            self.plan_values: dict[str, QLabel] = {}
            form.addRow("Сигнал", self.signal_label)
            form.addRow("Причина", self.reason_label)
            for key, title in (
                ("entry", "Entry"),
                ("stop", "Stop"),
                ("distance", "Distance"),
                ("quantity", "Quantity"),
                ("notional", "Position size, USDT"),
                ("budget", "Risk budget"),
                ("risk", "Est. loss at stop"),
                ("fees", "Fees"),
                ("slippage", "Slippage"),
                ("funding", "Funding (1 interval)"),
                ("min_qty", "Min exchange qty"),
                ("min_risk", "Min feasible risk"),
            ):
                value = QLabel("—")
                self.plan_values[key] = value
                form.addRow(title, value)
            manual_layout.addWidget(signal)

            protection = QGroupBox("Защита позиции")
            protection_grid = QGridLayout(protection)
            protection_grid.addWidget(QLabel(""), 0, 0)
            protection_grid.addWidget(QLabel("Stop"), 0, 1)
            protection_grid.addWidget(QLabel("Take profit"), 0, 2)
            self.protection_values: dict[str, QLabel] = {}
            for row, (key, title) in enumerate(
                (("planned", "Planned"), ("requested", "Requested"), ("confirmed", "Confirmed")),
                start=1,
            ):
                protection_grid.addWidget(QLabel(title), row, 0)
                for column, suffix in ((1, "stop"), (2, "tp")):
                    value = QLabel("—")
                    self.protection_values[f"{key}_{suffix}"] = value
                    protection_grid.addWidget(value, row, column)
            manual_layout.addWidget(protection)

            checks = QGroupBox("Risk checks")
            checks_layout = QVBoxLayout(checks)
            self.risk_table = _table(["", "Проверка", "Детали"])
            self.risk_table.setWordWrap(True)
            self.risk_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.ResizeToContents
            )
            self.risk_table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.ResizeToContents
            )
            self.risk_table.horizontalHeader().setSectionResizeMode(
                2, QHeaderView.ResizeMode.Stretch
            )
            checks_layout.addWidget(self.risk_table)
            manual_layout.addWidget(checks, 1)
            scroll.setWidget(contents)
            self.signal_bot_stack.addWidget(scroll)

            bot = QWidget()
            bot_layout = QVBoxLayout(bot)
            bot_layout.setContentsMargins(4, 4, 4, 4)
            bot_layout.setSpacing(6)
            runtime_box = QGroupBox("Entry Bot · live screening")
            runtime_layout = QVBoxLayout(runtime_box)
            self.bot_runtime_label = QLabel(
                "STOPPED · SHADOW · AUTO ENTRY LOCKED"
            )
            self.bot_runtime_label.setWordWrap(True)
            runtime_layout.addWidget(self.bot_runtime_label)
            button_row = QWidget()
            button_layout = QHBoxLayout(button_row)
            button_layout.setContentsMargins(0, 0, 0, 0)
            self.bot_start_button = QPushButton("Запустить скрининг")
            self.bot_stop_button = QPushButton("Остановить")
            self.bot_start_button.setEnabled(start_entry_bot is not None)
            self.bot_stop_button.setEnabled(False)
            self.bot_start_button.clicked.connect(self._start_entry_bot)
            self.bot_stop_button.clicked.connect(self._stop_entry_bot)
            button_layout.addWidget(self.bot_start_button)
            button_layout.addWidget(self.bot_stop_button)
            runtime_layout.addWidget(button_row)
            bot_layout.addWidget(runtime_box)

            self.bot_assets_table = _table(
                ["Монета", "Статус", "Side", "Distance", "Flow", "OI"]
            )
            self.bot_assets_table.setSelectionBehavior(
                QAbstractItemView.SelectionBehavior.SelectRows
            )
            self.bot_assets_table.setSelectionMode(
                QAbstractItemView.SelectionMode.SingleSelection
            )
            self.bot_assets_table.verticalHeader().setVisible(False)
            self.bot_assets_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.ResizeToContents
            )
            self.bot_assets_table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.ResizeToContents
            )
            self.bot_assets_table.horizontalHeader().setSectionResizeMode(
                2, QHeaderView.ResizeMode.ResizeToContents
            )
            self.bot_assets_table.horizontalHeader().setSectionResizeMode(
                3, QHeaderView.ResizeMode.ResizeToContents
            )
            self.bot_assets_table.horizontalHeader().setSectionResizeMode(
                4, QHeaderView.ResizeMode.Stretch
            )
            self.bot_assets_table.horizontalHeader().setSectionResizeMode(
                5, QHeaderView.ResizeMode.ResizeToContents
            )
            self.bot_assets_table.cellDoubleClicked.connect(self._bot_asset_activated)
            bot_layout.addWidget(self.bot_assets_table, 1)
            note = QLabel(
                "Distance: красный >0.60%, жёлтый 0.25–0.60%, зелёный <=0.25%; "
                "прочерк означает, что armed candidate сейчас отсутствует. "
                "Двойной щелчок открывает монету на основном графике. "
                "Зелёная зона пишет shadow pre-limit в историю, но реальная Mainnet-заявка "
                "не отправляется до отдельного production-equivalence gate."
            )
            note.setWordWrap(True)
            note.setObjectName("muted")
            bot_layout.addWidget(note)
            self.signal_bot_stack.addWidget(bot)
            self.signal_bot_stack.setCurrentIndex(0)
            return panel

        def _set_bot_mode(self, enabled: bool) -> None:
            self.signal_bot_stack.setCurrentIndex(1 if enabled else 0)
            self.bot_mode_badge.setText("BOT" if enabled else "MANUAL")
            if enabled:
                self._refresh_entry_bot()

        def _start_entry_bot(self) -> None:
            if start_entry_bot is None:
                return
            try:
                start_entry_bot()
                self.bot_start_button.setEnabled(False)
                self.bot_stop_button.setEnabled(True)
                self.bot_runtime_label.setText(
                    "STARTING · public-only scanner · AUTO ENTRY LOCKED"
                )
            except Exception as exc:
                self.bot_runtime_label.setText("ERROR · " + _safe_error(exc))

        def _stop_entry_bot(self) -> None:
            if stop_entry_bot is None:
                return
            try:
                stop_entry_bot()
            except Exception as exc:
                self.bot_runtime_label.setText("ERROR · " + _safe_error(exc))
                return
            self.bot_start_button.setEnabled(False)
            self.bot_stop_button.setEnabled(False)
            self.bot_runtime_label.setText("STOPPING · SHADOW · AUTO ENTRY LOCKED")

        def _refresh_entry_bot(self) -> None:
            if get_entry_bot_snapshot is None:
                return
            try:
                snapshot = get_entry_bot_snapshot()
            except Exception as exc:
                self.bot_runtime_label.setText("ERROR · " + _safe_error(exc))
                return
            total_assets = len(snapshot.assets)
            warmup_assets = sum(asset.status.value == "WARMUP" for asset in snapshot.assets)
            error_assets = sum(asset.status.value == "ERROR" for asset in snapshot.assets)
            no_calibration_assets = sum(
                asset.status.value == "NO CALIBRATION" for asset in snapshot.assets
            )
            ready_assets = total_assets - warmup_assets - error_assets - no_calibration_assets
            progress = (
                f"Ready {ready_assets}/{total_assets} · Warm-up {warmup_assets} · "
                f"Errors {error_assets} · Audit {snapshot.audit_event_count}"
            )
            if no_calibration_assets:
                progress += f" · No calibration {no_calibration_assets}"
            guidance = ""
            if snapshot.running and warmup_assets:
                guidance = (
                    "\nЖдём 5 ПОЛНЫХ минут live tape по каждой монете; "
                    "в колонке Flow видно TAPE n/5. После reconnect отсчёт начинается заново."
                )
            elif snapshot.running and ready_assets == total_assets:
                guidance = "\nВсе монеты прогреты; бот ждёт WAITING / WATCH / APPROACH / SIGNAL."
            self.bot_runtime_label.setText(
                f"{snapshot.state.value} · {snapshot.execution_mode}\n"
                f"{progress}\n{snapshot.detail}{guidance}"
            )
            self.bot_start_button.setEnabled(
                start_entry_bot is not None and not snapshot.running
            )
            self.bot_stop_button.setEnabled(stop_entry_bot is not None and snapshot.running)
            self.bot_assets_table.setRowCount(len(snapshot.assets))
            for row, asset in enumerate(snapshot.assets):
                distance = "—" if asset.distance_pct is None else f"{asset.distance_pct:.3f}%"
                values = (
                    asset.symbol,
                    asset.status.value,
                    asset.side or "—",
                    distance,
                    asset.flow_state,
                    asset.oi_state,
                )
                band_background: QColor | None = None
                band_foreground: QColor | None = None
                if asset.distance_pct is not None:
                    if asset.status.value == "APPROACH":
                        band_background = QColor("#123524")
                        band_foreground = QColor("#7fffb2")
                    elif asset.status.value == "WATCH":
                        band_background = QColor("#3b3211")
                        band_foreground = QColor("#ffe781")
                    else:
                        band_background = QColor("#3a1720")
                        band_foreground = QColor("#ff9aa8")
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    tooltip = asset.detail
                    if asset.distance_pct is not None:
                        tooltip += (
                            "\nЦвет дистанции: красный >0.60%, жёлтый 0.25–0.60%, "
                            "зелёный <=0.25%. Реальная заявка НЕ отправляется."
                        )
                    item.setToolTip(tooltip)
                    if band_background is not None and band_foreground is not None:
                        item.setBackground(band_background)
                        item.setForeground(band_foreground)
                    self.bot_assets_table.setItem(row, column, item)

        def _bot_asset_activated(self, row: int, _column: int) -> None:
            item = self.bot_assets_table.item(row, 0)
            if item is None:
                return
            symbol = item.text().strip().upper()
            if not symbol:
                return
            self.symbol_input.setCurrentText(symbol)
            self._market_selection_changed()

        def _build_bottom_tabs(self) -> QTabWidget:
            tabs = QTabWidget()
            self.positions_table = _table(
                ["Symbol", "Side", "Qty", "Average", "Mark", "Liq.", "uPnL", "Stop", "TP"]
            )
            self.orders_table = _table(
                [
                    "Order ID",
                    "Client ID",
                    "Side",
                    "Type",
                    "Qty",
                    "Filled",
                    "Price",
                    "Status",
                    "Role",
                ]
            )
            self.executions_table = _table(
                ["Execution ID", "Order ID", "Side", "Qty", "Price", "Time"]
            )
            self.closed_trades_table = _table(
                [
                    "Closed at",
                    "Order ID",
                    "Side",
                    "Qty",
                    "Entry",
                    "Exit",
                    "Closed PnL",
                    "Open fee",
                    "Close fee",
                    "Type",
                    "Lev.",
                ]
            )
            self.decisions_list = QListWidget()
            self.risk_events_list = QListWidget()
            self.execution_commands_list = QListWidget()
            self.system_log = QTextEdit()
            self.system_log.setReadOnly(True)
            self.backtest_panel = QWidget()
            backtest_layout = QFormLayout(self.backtest_panel)
            self.backtest_trade_path = QLineEdit()
            self.backtest_trade_path.setPlaceholderText("Trade OHLCV CSV")
            self.backtest_mark_path = QLineEdit()
            self.backtest_mark_path.setPlaceholderText("Mark Price CSV (рекомендуется)")
            self.backtest_funding_path = QLineEdit()
            self.backtest_funding_path.setPlaceholderText("Funding CSV (рекомендуется)")
            self.backtest_parameters = QLineEdit("{}")
            self.backtest_parameters.setPlaceholderText("Параметры стратегии, JSON")
            self.backtest_report_path = QLineEdit()
            self.backtest_report_path.setPlaceholderText("Куда сохранить JSON-отчёт")
            self.backtest_eligibility = QCheckBox(
                "Проверить и сохранить eligibility для Micro-Live"
            )
            self.backtest_eligibility.setToolTip(
                "Требует Mark Price, funding и свежие реальные правила инструмента с Bybit."
            )
            choose_trade = QPushButton("Выбрать Trade CSV")
            choose_trade.clicked.connect(
                lambda: self._choose_backtest_file(self.backtest_trade_path, False)
            )
            choose_mark = QPushButton("Выбрать Mark CSV")
            choose_mark.clicked.connect(
                lambda: self._choose_backtest_file(self.backtest_mark_path, False)
            )
            choose_funding = QPushButton("Выбрать Funding CSV")
            choose_funding.clicked.connect(
                lambda: self._choose_backtest_file(self.backtest_funding_path, False)
            )
            choose_report = QPushButton("Выбрать отчёт JSON")
            choose_report.clicked.connect(
                lambda: self._choose_backtest_file(self.backtest_report_path, True)
            )
            self.backtest_run_button = QPushButton("Запустить BackTest")
            self.backtest_run_button.setEnabled(start_backtest is not None)
            self.backtest_run_button.clicked.connect(self._run_backtest)
            self.backtest_output = QTextEdit()
            self.backtest_output.setReadOnly(True)
            self.backtest_output.setPlaceholderText(
                "Research only: результат теста не является обещанием доходности."
            )
            backtest_layout.addRow(choose_trade, self.backtest_trade_path)
            backtest_layout.addRow(choose_mark, self.backtest_mark_path)
            backtest_layout.addRow(choose_funding, self.backtest_funding_path)
            backtest_layout.addRow("Параметры", self.backtest_parameters)
            backtest_layout.addRow(choose_report, self.backtest_report_path)
            backtest_layout.addRow(self.backtest_eligibility)
            backtest_layout.addRow(self.backtest_run_button)
            backtest_layout.addRow(self.backtest_output)
            tabs.addTab(self.positions_table, "Позиции")
            tabs.addTab(self.orders_table, "Ордера")
            tabs.addTab(self.executions_table, "Исполнения")
            tabs.addTab(self.closed_trades_table, "Закрытые сделки")
            tabs.addTab(self.decisions_list, "Решения стратегии")
            tabs.addTab(self.risk_events_list, "Risk events")
            tabs.addTab(self.execution_commands_list, "Execution commands")
            tabs.addTab(self.backtest_panel, "BackTest")
            tabs.addTab(self.system_log, "Системный журнал")
            return tabs

        def _choose_backtest_file(self, target: QLineEdit, save: bool) -> None:
            if save:
                selected, _ = QFileDialog.getSaveFileName(
                    self, "Сохранить BackTest-отчёт", "backtest-report.json", "JSON (*.json)"
                )
            else:
                selected, _ = QFileDialog.getOpenFileName(
                    self, "Выбрать исторические данные", "", "CSV (*.csv)"
                )
            if selected:
                target.setText(selected)

        def _run_backtest(self) -> None:
            if start_backtest is None:
                return
            if not self.backtest_trade_path.text().strip():
                QMessageBox.warning(self, "BackTest", "Выберите Trade OHLCV CSV")
                return
            if not self.backtest_report_path.text().strip():
                QMessageBox.warning(self, "BackTest", "Укажите путь JSON-отчёта")
                return
            if self.backtest_eligibility.isChecked() and (
                not self.backtest_mark_path.text().strip()
                or not self.backtest_funding_path.text().strip()
            ):
                QMessageBox.warning(
                    self,
                    "BackTest eligibility",
                    "Для Micro-Live eligibility обязательны Mark Price CSV и Funding CSV.",
                )
                return
            request = {
                "trade_path": self.backtest_trade_path.text().strip(),
                "mark_path": self.backtest_mark_path.text().strip(),
                "funding_path": self.backtest_funding_path.text().strip(),
                "report_path": self.backtest_report_path.text().strip(),
                "strategy_id": str(self.strategy_combo.currentData()),
                "symbol": self.symbol_input.text().strip().upper(),
                "timeframe": self.timeframe_combo.currentText(),
                "parameters": self.backtest_parameters.text().strip() or "{}",
                "eligibility": "true" if self.backtest_eligibility.isChecked() else "false",
            }
            self.backtest_output.append("Запуск исторического теста…")
            self.backtest_run_button.setEnabled(False)
            start_backtest(request)

        def refresh_from_model(self, force: bool = False) -> None:
            if pump_read_only is not None:
                pump_read_only()
            self._refresh_entry_bot()
            if load_backtest_results is not None:
                lines = load_backtest_results()
                for line in lines:
                    self.backtest_output.append(line)
                if lines:
                    self.backtest_run_button.setEnabled(start_backtest is not None)
            if load_access_diagnostics is not None:
                reports = load_access_diagnostics()
                if reports:
                    self.access_diagnostics_output.setPlainText("\n\n".join(reports))
                    self._access_diagnostics_active = False
                    self.access_diagnostics_button.setText("Диагностика доступа")
                    self.access_diagnostics_button.setEnabled(
                        settings.mode is AppMode.LIVE
                        and start_access_diagnostics is not None
                    )
            state = model.state
            model.set_engine_state(state_machine.state)
            state = model.state
            self._refresh_connection_controls(state)
            actual_leverage = state.account_leverage
            selected_leverage = self.leverage_input.currentText().strip()
            if actual_leverage is not None:
                actual_text = _number(actual_leverage)
                match = actual_leverage == Decimal(selected_leverage)
                self.leverage_status_label.setText(
                    f"Выбрано: {selected_leverage}x · Bybit: {actual_text}x"
                    + (" · ✓" if match else " · не применено")
                )
            selected_market = (
                self.symbol_input.text().strip().upper(),
                self.timeframe_combo.currentText().strip(),
            )
            if (
                self._pending_auto_recommendation
                and state.engine_state in {AppState.READY, AppState.PAUSED}
                and (state.symbol, state.timeframe) == selected_market
                and self._last_recommendation_market != selected_market
            ):
                self._recommend_market_plan()
                state = model.state
            self._advance_trade_pipeline(state)
            state = model.state
            if not force and state == self._last_state:
                return
            self._last_state = state
            self.engine_badge.setText(state.engine_state.value)
            self.execution_badge.setText(
                f"{state.execution_mode.value} · {state.execution_phase}"
            )
            expiry = "—" if state.api_expiry is None else state.api_expiry.isoformat()
            deadline = "—" if state.api_deadline_day is None else str(state.api_deadline_day)
            ticket_expiry = (
                "—"
                if state.arming_ticket_expires_at is None
                else state.arming_ticket_expires_at.isoformat()
            )
            permissions = ", ".join(state.api_permissions) or "не проверены"
            blockers = "; ".join(state.arming_blockers) or "нет"
            self.connection_details.setText(
                f"Endpoint: {self._selected_endpoint() or state.endpoint or 'offline'}\n"
                f"Аккаунт: {state.api_account_scope or '—'} · {state.api_access or '—'}\n"
                f"IP: {state.api_ip_binding or '—'}\n"
                f"Истекает: {expiry} · осталось дней: {deadline}\n"
                f"Права: {permissions}\nArming blockers: {blockers}\n"
                f"UNIFIED: equity={_number(state.equity)} · "
                f"available={_number(state.available_balance)} · "
                f"wallet={_number(state.wallet_balance)}\n"
                f"Execution: {state.execution_detail}\nTicket expires: {ticket_expiry}"
            )
            self._set_health("public", state.public)
            self._set_health("private", state.private)
            self._set_health("rest", state.rest)
            self.equity_label.setText(f"Equity\n{_number(state.equity)}")
            self.available_label.setText(f"Available\n{_number(state.available_balance)}")
            self.pnl_label.setText(f"Daily PnL\n{_number(state.daily_realized_pnl)}")
            self.symbol_label.setText(f"{state.symbol} · {state.timeframe}")
            self.last_price_label.setText(_number(state.last_price))
            self.mark_price_label.setText(f"Mark {_number(state.mark_price)}")
            self._render_direction_badge()
            self._render_clock_health(state.clock_offset_ms)
            self._refresh_linked_percentages()
            self._refresh_position_size_link()
            position_values = (
                ("side", state.position_side),
                ("quantity", _number(state.position_quantity)),
                ("average", _number(state.position_average_price)),
                ("mark", _number(state.mark_price)),
                ("liquidation", _number(state.liquidation_price)),
                ("upnl", _number(state.unrealized_pnl)),
            )
            for key, value in position_values:
                self.position_values[key].setText(value)
            self.signal_label.setText(state.signal)
            self.reason_label.setText(state.signal_reason)
            for key, plan_value in (
                ("entry", state.entry_price),
                ("stop", state.stop_price),
                ("distance", state.stop_distance),
                ("quantity", state.proposed_quantity),
                (
                    "notional",
                    None
                    if state.proposed_quantity is None or state.entry_price is None
                    else state.proposed_quantity * state.entry_price,
                ),
                ("budget", state.risk_budget),
                ("risk", state.risk_amount),
                ("fees", state.estimated_fees),
                ("slippage", state.estimated_slippage),
                ("funding", state.estimated_funding),
                ("min_qty", state.minimum_viable_quantity),
                (
                    "min_risk",
                    None
                    if state.minimum_viable_risk_percent is None
                    else f"{_number(state.minimum_viable_loss_at_stop)} USDT / "
                    f"{_number(state.minimum_viable_risk_percent)}%",
                ),
            ):
                self.plan_values[key].setText(
                    plan_value if isinstance(plan_value, str) else _number(plan_value)
                )
            protection = state.protection
            for key, protection_value in (
                ("planned_stop", protection.planned_stop),
                ("requested_stop", protection.requested_stop),
                ("confirmed_stop", protection.confirmed_stop),
                ("planned_tp", protection.planned_take_profit),
                ("requested_tp", protection.requested_take_profit),
                ("confirmed_tp", protection.confirmed_take_profit),
            ):
                self.protection_values[key].setText(_number(protection_value))
            self._render_chart(state)
            self._render_risk_checks(state.risk_checks)
            self._render_positions(state)
            self._render_orders(state.orders)
            self._render_executions(state.executions)
            self._render_closed_trades(state.closed_trades)
            _set_list(self.decisions_list, state.strategy_decisions)
            _set_list(self.risk_events_list, state.risk_events)
            if load_execution_commands is not None:
                _set_list(self.execution_commands_list, load_execution_commands())
            self.system_log.setPlainText("\n".join(state.system_log))
            if state.error is None:
                self.error_banner.hide()
            else:
                self.error_banner.setText(state.error.text)
                self.error_banner.show()
            self.pause_button.setEnabled(state.engine_state in {AppState.ARMED, AppState.RUNNING})
            testnet_maintenance = (
                settings.mode is AppMode.TESTNET and settings.testnet_execution_allowed
            )
            mainnet_maintenance = settings.mode is AppMode.LIVE and state.execution_phase in {
                "ARMED",
                "RUNNING",
                "PAUSED",
                "EXPIRED",
                "KILL_SWITCH",
            }
            maintenance_enabled = (
                (testnet_maintenance or mainnet_maintenance)
                and state.engine_state is not AppState.DISCONNECTED
            )
            self.cancel_entries_button.setEnabled(
                maintenance_enabled and cancel_entries is not None
            )
            self.cancel_orders_button.setEnabled(
                maintenance_enabled and cancel_non_protective is not None
            )
            self.flatten_button.setEnabled(maintenance_enabled and flatten_position is not None)
            self.emergency_button.setEnabled(
                state.engine_state not in {AppState.DISCONNECTED, AppState.EMERGENCY_STOP}
            )
            trade_ready = (
                state.engine_state in {AppState.READY, AppState.PAUSED}
                and state.instrument is not None
                and state.equity is not None
                and state.available_balance is not None
            )
            self.recommend_button.setEnabled(trade_ready and not self._trade_pipeline_active)
            if settings.mode is AppMode.LIVE:
                execution_ready = (
                    trade_ready
                    and settings.allow_live_trading
                    and prepare_execution is not None
                    and arm_execution is not None
                    and submit_manual_trade is not None
                    and state.execution_phase not in {"CHECKING", "ARMED", "RUNNING", "BLOCKED"}
                )
            elif settings.mode is AppMode.TESTNET:
                execution_ready = (
                    trade_ready
                    and settings.testnet_execution_allowed
                    and submit_manual_trade is not None
                )
            else:
                execution_ready = False
            self.execute_button.setEnabled(
                execution_ready and not self._trade_pipeline_active
            )
            self.stop_button.setEnabled(
                self._trade_pipeline_active
                or state.engine_state in {AppState.ARMED, AppState.RUNNING, AppState.PAUSED}
            )

        def _set_health(self, channel: str, health: Any) -> None:
            label = self.health_labels[channel]
            title = {"public": "Public WS", "private": "Private WS", "rest": "REST"}[channel]
            label.setText(f"● {title}")
            color = "#22c55e" if health.fresh else "#f59e0b" if health.connected else "#64748b"
            label.setStyleSheet(f"color:{color};font-weight:600")
            when = "—" if health.last_message_at is None else health.last_message_at.isoformat()
            label.setToolTip(f"{health.detail}\nПоследнее сообщение: {when}")

        def _render_chart(self, state: Any) -> None:
            candles = state.candles
            if self._chart is not None:
                if self._candle_item is not None:
                    self._candle_item.set_candles(candles)
                signature = (state.symbol, state.timeframe)
                if candles and self._chart_market_signature != signature:
                    visible = candles[-90:]
                    from bybit_workbench.ui.candlestick import candle_time_bounds

                    bounds = candle_time_bounds(visible)
                    if bounds is not None:
                        self._chart.setXRange(bounds[0], bounds[1], padding=0.01)
                    low = min(float(item.low) for item in visible)
                    high = max(float(item.high) for item in visible)
                    span = max(high - low, max(abs(high), 1.0) * 0.001)
                    self._chart.setYRange(
                        low - span * 0.08,
                        high + span * 0.08,
                        padding=0,
                    )
                    self._chart_market_signature = signature
                values = {
                    "mark": state.mark_price,
                    "entry": state.entry_price,
                    "average": state.position_average_price,
                    "stop": (
                        state.protection.confirmed_stop
                        or state.protection.requested_stop
                        or state.protection.planned_stop
                    ),
                    "take_profit": (
                        state.protection.confirmed_take_profit
                        or state.protection.requested_take_profit
                        or state.protection.planned_take_profit
                    ),
                    "liquidation": state.liquidation_price,
                }
                for key, value in values.items():
                    line = self._chart_levels.get(key)
                    if line is not None:
                        line.setVisible(value is not None)
                        if value is not None:
                            line.setValue(float(value))
                stop_line = self._chart_levels.get("stop")
                if stop_line is not None:
                    stop_line.setMovable(
                        state.position_quantity == 0
                        and state.protection.planned_stop is not None
                        and state.engine_state in {AppState.READY, AppState.PAUSED}
                    )
                if self._risk_region is not None:
                    planned_entry = state.entry_price
                    planned_stop = state.protection.planned_stop
                    visible = planned_entry is not None and planned_stop is not None
                    self._risk_region.setVisible(visible)
                    if visible:
                        self._risk_region.setRegion((float(planned_stop), float(planned_entry)))
            elif self._chart_note is not None and candles:
                self._chart_note.setText(
                    f"Закрытых свечей: {len(candles)}\nПоследняя цена: {_number(candles[-1].close)}"
                )

        def _render_direction_badge(self) -> None:
            direction = self._recommended_direction
            self.direction_badge.setVisible(direction is not None)
            if direction is None:
                self.direction_badge.clear()
                return
            if direction is PositionSide.LONG:
                self.direction_badge.setText("↑ LONG")
                self.direction_badge.setStyleSheet(
                    "color:#d1fae5;background:#064e3b;border:1px solid #10b981;"
                    "border-radius:4px;padding:4px 8px;font-weight:800"
                )
            else:
                self.direction_badge.setText("↓ SHORT")
                self.direction_badge.setStyleSheet(
                    "color:#fee2e2;background:#7f1d1d;border:1px solid #ef4444;"
                    "border-radius:4px;padding:4px 8px;font-weight:800"
                )

        def _render_clock_health(self, offset_ms: int | None) -> None:
            if offset_ms is None:
                self.clock_label.setText("Clock —")
                self.clock_label.setStyleSheet("color:#64748b;font-weight:600")
                return
            absolute = abs(offset_ms)
            if absolute < 500:
                color = "#22c55e"
            elif absolute < 750:
                color = "#f59e0b"
            else:
                color = "#ef4444"
            sign = "+" if offset_ms >= 0 else ""
            self.clock_label.setText(f"Clock {sign}{offset_ms} ms")
            self.clock_label.setStyleSheet(f"color:{color};font-weight:700")
            self.clock_label.setToolTip(
                "Bybit server time − local Windows time. "
                "Проверка каждые 15 секунд; при |offset| >= 500 ms Workbench "
                "автоматически запускает W32Time и повторяет w32tm /resync; "
                "при |offset| > 750 ms торговля блокируется, а read-only сессия "
                "сама синхронизирует часы и переподключается."
            )

        def _on_planned_stop_dragged(self) -> None:
            line = self._chart_levels.get("stop")
            if line is None or model.state.position_quantity != 0:
                return
            self.stop_input.setText(format(Decimal(str(line.value())), "f"))
            self._linked_price_edited("stop")
            self.statusBar().showMessage(
                "Планируемый stop изменён; перед отправкой будет выполнена новая проверка",
                5000,
            )

        def _render_risk_checks(self, checks: tuple[Any, ...]) -> None:
            self.risk_table.setRowCount(len(checks))
            for row, check in enumerate(checks):
                item = QTableWidgetItem("✓" if check.passed else "✕")
                item.setForeground(QColor("#22c55e" if check.passed else "#ef4444"))
                self.risk_table.setItem(row, 0, item)
                self.risk_table.setItem(row, 1, QTableWidgetItem(check.code))
                self.risk_table.setItem(row, 2, QTableWidgetItem(check.detail))

        def _render_positions(self, state: Any) -> None:
            has_position = state.position_quantity != 0
            self.positions_table.setRowCount(1 if has_position else 0)
            if not has_position:
                return
            values = (
                state.symbol,
                state.position_side,
                _number(state.position_quantity),
                _number(state.position_average_price),
                _number(state.mark_price),
                _number(state.liquidation_price),
                _number(state.unrealized_pnl),
                _number(state.protection.confirmed_stop),
                _number(state.protection.confirmed_take_profit),
            )
            _set_table_row(self.positions_table, 0, values)

        def _render_orders(self, orders: tuple[Any, ...]) -> None:
            self.orders_table.setRowCount(len(orders))
            for row, order in enumerate(orders):
                request = order.request
                _set_table_row(
                    self.orders_table,
                    row,
                    (
                        order.order_id,
                        request.client_order_id,
                        request.side.value,
                        request.order_type.value,
                        _number(request.quantity),
                        _number(order.filled_quantity),
                        _number(request.price),
                        order.status.value,
                        request.role.value,
                    ),
                )

        def _render_executions(self, executions: tuple[Any, ...]) -> None:
            self.executions_table.setRowCount(len(executions))
            for row, execution in enumerate(executions):
                _set_table_row(
                    self.executions_table,
                    row,
                    (
                        execution.execution_id,
                        execution.order_id,
                        execution.side.value,
                        _number(execution.quantity),
                        _number(execution.price),
                        execution.executed_at.isoformat(),
                    ),
                )

        def _render_closed_trades(self, records: tuple[Any, ...]) -> None:
            self.closed_trades_table.setRowCount(len(records))
            for row, record in enumerate(records):
                _set_table_row(
                    self.closed_trades_table,
                    row,
                    (
                        record.updated_at.isoformat(),
                        record.order_id,
                        record.side,
                        _number(record.closed_size),
                        _number(record.average_entry_price),
                        _number(record.average_exit_price),
                        _number(record.closed_pnl),
                        _number(record.open_fee),
                        _number(record.close_fee),
                        record.order_type,
                        _number(record.leverage),
                    ),
                )

        def _next_fake_candle(self) -> None:
            try:
                candle = exchange.next_candle() if exchange is not None else None
            except Exception as exc:
                model.set_error(
                    UserFacingError(
                        _safe_error(exc),
                        "Replay-лента остановлена; торговые действия не выполнялись.",
                        "Перезапустите приложение или проверьте системный журнал.",
                    )
                )
                self._fake_timer.stop()
                return
            if candle is not None:
                model.apply_candle(candle)
                model.append_system_log(
                    f"{candle.closed_at.isoformat()} candle.closed "
                    f"O={candle.open} H={candle.high} L={candle.low} C={candle.close}"
                )

        def _selected_entry_order_type(self) -> OrderType:
            value = str(self.entry_order_type_combo.currentData() or OrderType.LIMIT.value)
            return OrderType(value)

        def _entry_order_type_changed(self, *_args: object) -> None:
            order_type = self._selected_entry_order_type()
            if order_type is OrderType.MARKET:
                self.market_execution_hint.setText(
                    "Market: price в /v5/order/create не отправляется. "
                    "slippageTolerance не задаётся — используется Auto-защита Bybit. "
                    "Для Risk Gate / Stop / TP берётся свежий Mark как Entry reference."
                )
                mark = model.state.mark_price
                if mark is not None and mark > 0:
                    self.entry_input.blockSignals(True)
                    try:
                        self.entry_input.setText(format(mark, "f"))
                    finally:
                        self.entry_input.blockSignals(False)
                    self._refresh_linked_percentages()
            else:
                self.market_execution_hint.setText(
                    "Limit: Entry отправляется как цена GTC-заявки."
                )
            self._invalidate_manual_plan("entry order type changed")

        def _entry_reference(self, order_type: OrderType) -> Decimal:
            if order_type is OrderType.MARKET:
                mark = model.state.mark_price
                if mark is None or mark <= 0:
                    raise RuntimeError("для Market-входа нужен свежий положительный Mark")
                return mark
            return _decimal_input(self.entry_input.text(), "Entry reference")

        def _manual_trade_draft(self) -> ManualTradeDraft:
            if self.strategy_combo.currentData() != "manual_protected_trade":
                raise PermissionError(
                    "Алгоритм зарезервирован, но его формальные правила ещё не согласованы"
                )
            order_type = self._selected_entry_order_type()
            return ManualTradeDraft(
                symbol=self.symbol_input.text(),
                direction=PositionSide(self.direction_combo.currentText()),
                entry_price=self._entry_reference(order_type),
                stop_price=_decimal_input(self.stop_input.text(), "Hard stop"),
                take_profit=_optional_decimal_input(self.take_profit_input.text()),
                leverage=_decimal_input(self.leverage_input.text(), "Leverage"),
                requested_notional=self._requested_position_notional(),
                order_type=order_type,
            )

        def _execute_manual_trade(self) -> None:
            if self._trade_pipeline_active:
                return
            self._set_trade_activity("Проверяем риск и параметры сделки…", "busy")
            self.execute_button.setText("ПРОВЕРЯЕМ…")
            QApplication.processEvents()
            try:
                draft = self._manual_trade_draft()
                prepared = workflow.check(
                    draft,
                    profile=self._risk_profile_settings().to_domain(
                        draft.symbol.strip().upper()
                    ),
                )
                if draft.requested_notional is None:
                    self._set_position_size_from_decision(prepared)
                if not prepared.decision.approved:
                    self._show_trade_rejection(prepared)
                    self.execute_button.setText("ИСПОЛНИТЬ СДЕЛКУ")
                    return
                order = prepared.decision.normalized_order
                if order is None:
                    raise RuntimeError("одобренный план не содержит нормализованной заявки")
                reference_price = prepared.decision.normalized_entry
                if reference_price is None:
                    raise RuntimeError("одобренная Micro-Live заявка не содержит Entry reference")
                if order.order_type is OrderType.LIMIT and order.price is None:
                    raise RuntimeError("одобренная Limit-заявка не содержит limit-price")
                notional = order.quantity * (order.price or reference_price)
            except Exception as exc:
                self.execute_button.setText("ИСПОЛНИТЬ СДЕЛКУ")
                self._set_trade_activity("Сделка не готова: " + _safe_error(exc), "error")
                model.set_error(
                    UserFacingError(
                        _safe_error(exc),
                        "Заявка не создана; торговое состояние не изменено.",
                        "Исправьте параметры и снова нажмите «Исполнить сделку».",
                    )
                )
                return

            answer = QMessageBox.warning(
                self,
                "Подтвердить сделку",
                f"{order.side.value} {order.quantity} {order.symbol} · "
                + (
                    f"Limit @ {_number(order.price)}\n"
                    if order.order_type is OrderType.LIMIT
                    else "Market · slippage Auto (Bybit), price не отправляется\n"
                )
                + f"Расчётный объём: {_number(notional)} USDT\n"
                f"Stop: {_number(prepared.decision.normalized_stop)}\n"
                f"Take profit: {_number(prepared.intent.take_profit)}\n\n"
                "После Yes система сама выполнит свежую проверку аккаунта, "
                "arming и отправку. Дополнительных Check / Arm / Run не будет.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                workflow.invalidate("operator cancelled one-click execution")
                if invalidate_execution is not None:
                    invalidate_execution("operator cancelled one-click execution")
                self.execute_button.setText("ИСПОЛНИТЬ СДЕЛКУ")
                self._set_trade_activity("Сделка отменена до отправки.", "idle")
                return

            model.clear_error()
            self._trade_pipeline_active = True
            self._trade_pipeline_prepared = prepared
            self._set_trade_inputs_locked(True)
            if settings.mode is AppMode.LIVE:
                if prepare_execution is None:
                    self._fail_trade_pipeline("Mainnet execution runtime is not connected")
                    return
                self._trade_pipeline_stage = "preflight"
                self.execute_button.setText("ПРОВЕРКА АККАУНТА…")
                self._set_trade_activity(
                    "Проверяем свежесть рынка, аккаунт, позиции и открытые ордера…",
                    "busy",
                )
                QApplication.processEvents()
                try:
                    prepare_execution(prepared)
                except Exception as exc:
                    self._fail_trade_pipeline(_safe_error(exc))
                return

            try:
                workflow.arm()
                workflow.run()
                if submit_manual_trade is None:
                    raise RuntimeError("execution runtime is not connected")
                submit_manual_trade(prepared)
                self._trade_pipeline_stage = "submitted"
                self.execute_button.setText("ОТПРАВЛЕНО")
                self._set_trade_activity(
                    "Заявка передана исполнителю; ждём подтверждение биржи…",
                    "busy",
                )
            except Exception as exc:
                self._fail_trade_pipeline(_safe_error(exc))

        def _show_trade_rejection(self, prepared: PreparedManualTrade) -> None:
            rejection_codes = prepared.decision.rejection_codes
            if "minimum_notional" in rejection_codes:
                minimum_loss = prepared.decision.minimum_viable_loss_at_stop
                minimum_percent = prepared.decision.minimum_viable_risk_percent
                detail = (
                    "Биржевая минимальная заявка для этих Entry/Stop больше "
                    "рассчитанной позиции. "
                    f"Минимальный объём потребует примерно {_number(minimum_loss)} USDT "
                    f"риска ({_number(minimum_percent)}% капитала)."
                )
                action = (
                    "Приблизьте stop к entry, выберите другой инструмент или "
                    "осознанно измените Risk / trade, %."
                )
            else:
                detail = "Risk Gate отклонил план: " + ", ".join(rejection_codes)
                action = "Исправьте отмеченные risk checks и снова исполните сделку."
            self._set_trade_activity(detail, "error")
            model.set_error(
                UserFacingError(
                    detail,
                    "Заявка не отправлена.",
                    action,
                )
            )

        def _risk_profile_settings(self) -> RiskProfileSettings:
            return RiskProfileSettings(
                profile_name=self.risk_profile_combo.currentText(),
                version=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
                max_risk_amount=_nonnegative_decimal_input(
                    self.risk_amount_input.text(), "Risk amount"
                ),
                max_risk_percent=_nonnegative_decimal_input(
                    self.risk_percent_input.text(), "Risk percent"
                ),
                max_position_notional=_decimal_input(
                    self.max_notional_input.text(), "Max notional"
                ),
                max_leverage=_decimal_input(self.leverage_input.text(), "Leverage"),
                max_daily_loss=_nonnegative_decimal_input(
                    self.daily_loss_input.text(), "Daily loss cap"
                ),
                max_daily_loss_percent=_nonnegative_decimal_input(
                    self.daily_loss_percent_input.text(), "Daily loss percent"
                ),
                max_slippage_percent=_nonnegative_decimal_input(
                    self.max_slippage_input.text(), "Max slippage"
                ),
                estimated_fee_rate=_nonnegative_decimal_input(
                    self.fee_rate_input.text(), "Estimated fee rate"
                ),
            )

        def _save_risk_profile(self) -> None:
            if save_risk_profile is None:
                return
            try:
                profile = self._risk_profile_settings()
                save_risk_profile(profile.model_dump(mode="json"))
                model.set_risk_profile(f"{profile.profile_name} · {profile.version}")
                self.statusBar().showMessage("Risk-профиль сохранён в журнале", 5000)
            except Exception as exc:
                model.set_error(
                    UserFacingError(
                        _safe_error(exc),
                        "Некорректный risk-профиль не сохранён и не применён.",
                        "Исправьте значения лимитов и повторите сохранение.",
                    )
                )

        def _advance_trade_pipeline(self, state: Any) -> None:
            if not self._trade_pipeline_active or settings.mode is not AppMode.LIVE:
                return
            prepared = self._trade_pipeline_prepared
            if prepared is None:
                self._fail_trade_pipeline("внутренний план исполнения потерян")
                return
            phase = state.execution_phase
            if self._trade_pipeline_stage == "preflight":
                if phase == "CHECKING":
                    self.execute_button.setText("ПРОВЕРКА АККАУНТА…")
                    return
                if phase == "CHECKED":
                    try:
                        if arm_execution is None or submit_manual_trade is None:
                            raise RuntimeError("Mainnet execution runtime is not connected")
                        self._trade_pipeline_stage = "submitting"
                        self.execute_button.setText("ОТПРАВЛЯЕМ…")
                        self._set_trade_activity(
                            "Проверки пройдены. Вооружаем короткоживущий ticket и "
                            "немедленно отправляем заявку…",
                            "busy",
                        )
                        QApplication.processEvents()
                        arm_execution(_MICRO_LIVE_INTERNAL_CONFIRMATION)
                        workflow.arm()
                        workflow.run()
                        submit_manual_trade(prepared)
                        self._trade_pipeline_stage = "submitted"
                        self._set_trade_activity(
                            "Команда передана Mainnet; ждём acknowledgement и "
                            "reconciliation…",
                            "busy",
                        )
                    except Exception as exc:
                        self._fail_trade_pipeline(_safe_error(exc))
                    return
                if phase in {"BLOCKED", "EXPIRED", "KILL_SWITCH"}:
                    self._fail_trade_pipeline(state.execution_detail)
                    return

            if self._trade_pipeline_stage in {"submitting", "submitted"}:
                if phase == "RUNNING":
                    self.execute_button.setText("ЖДЁМ БИРЖУ…")
                    self._set_trade_activity(
                        state.execution_detail or "Ждём подтверждение биржи…",
                        "busy",
                    )
                    return
                if phase == "PAUSED":
                    self._finish_trade_pipeline(
                        "Цикл исполнения завершён: " + state.execution_detail,
                        "ok",
                    )
                    return
                if phase in {"BLOCKED", "EXPIRED", "KILL_SWITCH"}:
                    self._fail_trade_pipeline(state.execution_detail)

        def _set_trade_activity(self, text: str, kind: str) -> None:
            color = {
                "idle": "#94a3b8",
                "busy": "#f59e0b",
                "ok": "#22c55e",
                "error": "#ef4444",
            }.get(kind, "#94a3b8")
            self.trade_activity.setText(text)
            self.trade_activity.setStyleSheet(f"color:{color};font-weight:600")

        def _set_trade_inputs_locked(self, locked: bool) -> None:
            enabled = not locked
            for widget in (
                self.symbol_input,
                self.timeframe_combo,
                self.strategy_combo,
                self.direction_combo,
                self.entry_order_type_combo,
                self.entry_input,
                self.entry_percent_input,
                self.stop_input,
                self.stop_percent_input,
                self.take_profit_input,
                self.take_profit_percent_input,
                self.position_notional_input,
                self.position_percent_input,
                self.risk_amount_input,
                self.risk_percent_input,
                self.max_notional_input,
                self.max_slippage_input,
                self.daily_loss_percent_input,
                self.daily_loss_input,
                self.fee_rate_input,
                self.leverage_input,
                self.risk_profile_combo,
            ):
                widget.setEnabled(enabled)
            self.recommend_button.setEnabled(enabled and model.state.instrument is not None)
            if enabled:
                self._apply_direction_gate(self._recommended_direction)
            else:
                self.buy_button.setEnabled(False)
                self.sell_button.setEnabled(False)

        def _finish_trade_pipeline(self, message: str, kind: str) -> None:
            self._trade_pipeline_active = False
            self._trade_pipeline_stage = "idle"
            self._trade_pipeline_prepared = None
            self.execute_button.setText("ИСПОЛНИТЬ СДЕЛКУ")
            self._set_trade_inputs_locked(False)
            self._set_trade_activity(message, kind)

        def _fail_trade_pipeline(self, message: str) -> None:
            if state_machine.state is AppState.RUNNING:
                with suppress(InvalidStateTransition):
                    state_machine.transition(AppState.PAUSED, "one-click submission failed")
            self._finish_trade_pipeline("Сделка остановлена: " + message, "error")

        def _stop_strategy(self) -> None:
            try:
                if stop_strategy is not None:
                    stop_strategy()
                workflow.stop()
            except Exception as exc:
                self.statusBar().showMessage(_safe_error(exc), 5000)

        def _select_direction(self, direction: PositionSide) -> None:
            if (
                self._recommended_direction is not None
                and direction is not self._recommended_direction
            ):
                self.statusBar().showMessage(
                    "Направление против текущей рекомендации заблокировано. "
                    "Обновите рекомендации по рынку.",
                    5000,
                )
                return
            self.direction_combo.setCurrentText(direction.value)
            self._invalidate_manual_plan()

        def _apply_direction_gate(self, direction: PositionSide | None) -> None:
            self._recommended_direction = direction
            allow_long = direction in {None, PositionSide.LONG}
            allow_short = direction in {None, PositionSide.SHORT}
            self.buy_button.setEnabled(allow_long)
            self.sell_button.setEnabled(allow_short)
            self.buy_button.setText(
                "✓ КУПИТЬ · LONG" if direction is PositionSide.LONG else "КУПИТЬ · LONG"
            )
            self.sell_button.setText(
                "✓ ПРОДАТЬ · SHORT"
                if direction is PositionSide.SHORT
                else "ПРОДАТЬ · SHORT"
            )
            if direction is not None:
                self.direction_combo.setCurrentText(direction.value)

        def _linked_price_edited(self, key: str) -> None:
            if self._syncing_linked_fields:
                return
            self._syncing_linked_fields = True
            try:
                self._update_percent_from_price(key)
                if key == "entry":
                    self._update_percent_from_price("stop")
                    self._update_percent_from_price("take_profit")
            finally:
                self._syncing_linked_fields = False
            self._invalidate_manual_plan()

        def _linked_percent_edited(self, key: str) -> None:
            if self._syncing_linked_fields:
                return
            self._syncing_linked_fields = True
            try:
                percent_widget = self._percent_widget(key)
                raw_percent = percent_widget.text().strip()
                percent = _optional_signed_decimal(raw_percent)
                reference = self._price_reference(key)
                target = self._price_widget(key)
                if not raw_percent:
                    target.clear()
                elif percent is None:
                    return
                elif reference is not None and reference > 0:
                    tick = (
                        model.state.instrument.tick_size
                        if model.state.instrument is not None
                        else None
                    )
                    price = _price_from_percent(reference, percent, tick)
                    target.setText(format(price, "f"))
                    if key == "entry":
                        self._update_percent_from_price("stop")
                        self._update_percent_from_price("take_profit")
            finally:
                self._syncing_linked_fields = False
            self._invalidate_manual_plan()

        def _refresh_linked_percentages(self) -> None:
            if self._syncing_linked_fields:
                return
            self._syncing_linked_fields = True
            try:
                for key in ("entry", "stop", "take_profit"):
                    if self._percent_widget(key).hasFocus():
                        continue
                    self._update_percent_from_price(key)
            finally:
                self._syncing_linked_fields = False

        def _update_percent_from_price(self, key: str) -> None:
            price = _optional_positive_decimal(self._price_widget(key).text())
            reference = self._price_reference(key)
            percent_widget = self._percent_widget(key)
            percent_widget.blockSignals(True)
            try:
                if price is None or reference is None or reference <= 0:
                    percent_widget.clear()
                    return
                percent = _percent_from_reference(price, reference)
                percent_widget.setText(_format_percent(percent))
            finally:
                percent_widget.blockSignals(False)

        def _price_reference(self, key: str) -> Decimal | None:
            if key == "entry":
                return model.state.mark_price or model.state.last_price
            return _optional_positive_decimal(self.entry_input.text())

        def _price_widget(self, key: str) -> QLineEdit:
            return {
                "entry": self.entry_input,
                "stop": self.stop_input,
                "take_profit": self.take_profit_input,
            }[key]

        def _percent_widget(self, key: str) -> QLineEdit:
            return {
                "entry": self.entry_percent_input,
                "stop": self.stop_percent_input,
                "take_profit": self.take_profit_percent_input,
            }[key]

        def _position_notional_edited(self, _text: str) -> None:
            if self._syncing_size_fields:
                return
            self._size_link_mode = "usdt"
            self._syncing_size_fields = True
            try:
                self._update_position_percent_from_usdt()
            finally:
                self._syncing_size_fields = False
            self._invalidate_manual_plan()

        def _position_percent_edited(self, _text: str) -> None:
            if self._syncing_size_fields:
                return
            self._size_link_mode = "percent"
            self._syncing_size_fields = True
            try:
                raw = self.position_percent_input.text().strip()
                percent = _optional_signed_decimal(raw)
                available = model.state.available_balance
                self.position_notional_input.blockSignals(True)
                try:
                    if not raw:
                        self.position_notional_input.clear()
                    elif percent is not None and percent >= 0 and available is not None:
                        notional = available * percent / Decimal("100")
                        self.position_notional_input.setText(_format_money(notional))
                    else:
                        self.position_notional_input.clear()
                finally:
                    self.position_notional_input.blockSignals(False)
            finally:
                self._syncing_size_fields = False
            self._invalidate_manual_plan()

        def _refresh_position_size_link(self) -> None:
            if self._syncing_size_fields:
                return
            self._syncing_size_fields = True
            try:
                if self._size_link_mode == "percent":
                    raw = self.position_percent_input.text().strip()
                    percent = _optional_signed_decimal(raw)
                    available = model.state.available_balance
                    if percent is not None and percent >= 0 and available is not None:
                        self.position_notional_input.blockSignals(True)
                        try:
                            self.position_notional_input.setText(
                                _format_money(available * percent / Decimal("100"))
                            )
                        finally:
                            self.position_notional_input.blockSignals(False)
                else:
                    self._update_position_percent_from_usdt()
            finally:
                self._syncing_size_fields = False

        def _update_position_percent_from_usdt(self) -> None:
            notional = _optional_positive_decimal(self.position_notional_input.text())
            available = model.state.available_balance
            self.position_percent_input.blockSignals(True)
            try:
                if notional is None or available is None or available <= 0:
                    self.position_percent_input.clear()
                    return
                percent = notional / available * Decimal("100")
                self.position_percent_input.setText(_format_percent(percent))
            finally:
                self.position_percent_input.blockSignals(False)

        def _requested_position_notional(self) -> Decimal | None:
            raw = self.position_notional_input.text().strip()
            if not raw:
                return None
            return _decimal_input(raw, "Position size")

        def _set_position_size_from_decision(self, prepared: PreparedManualTrade) -> None:
            decision = prepared.decision
            if decision.candidate_quantity is None or decision.normalized_entry is None:
                return
            notional = decision.candidate_quantity * decision.normalized_entry
            if notional <= 0:
                return
            self._syncing_size_fields = True
            try:
                self.position_notional_input.blockSignals(True)
                self.position_percent_input.blockSignals(True)
                try:
                    self.position_notional_input.setText(_format_money(notional))
                    available = model.state.available_balance
                    if available is not None and available > 0:
                        percent = notional / available * Decimal("100")
                        self.position_percent_input.setText(_format_percent(percent))
                    else:
                        self.position_percent_input.clear()
                    self._size_link_mode = "auto"
                finally:
                    self.position_notional_input.blockSignals(False)
                    self.position_percent_input.blockSignals(False)
            finally:
                self._syncing_size_fields = False

        def _invalidate_manual_plan(self, *_: Any) -> None:
            if self._trade_pipeline_active:
                return
            if workflow.prepared is None:
                return
            state = state_machine.state
            if state not in {AppState.READY, AppState.PAUSED, AppState.ARMED}:
                return
            if state is AppState.ARMED:
                state_machine.transition(
                    AppState.READY,
                    "operator changed an armed manual plan",
                )
                self.statusBar().showMessage(
                    "План изменён после подготовки; предыдущий ticket снят.",
                    7000,
                )
            workflow.invalidate("operator changed manual parameters")
            if invalidate_execution is not None:
                invalidate_execution("operator changed checked parameters")

        def _pause(self) -> None:
            try:
                state_machine.transition(AppState.PAUSED, "operator paused new entries")
            except InvalidStateTransition as exc:
                self.statusBar().showMessage(str(exc), 5000)

        def _cancel_entries(self) -> None:
            self._confirm_maintenance(
                "Отменить входные заявки",
                "Отменить только активные незаполненные входные заявки?\n"
                "Подтверждённые защитные заявки и позиция сохранятся.",
                cancel_entries,
            )

        def _cancel_non_protective(self) -> None:
            self._confirm_maintenance(
                "Отменить non-protective заявки",
                "Отменить все активные входные и выходные заявки, кроме распознанных "
                "защитных stop/TP?\nПозиция останется открытой.",
                cancel_non_protective,
            )

        def _flatten_position(self) -> None:
            self._confirm_maintenance(
                "Закрыть позицию",
                f"Закрыть текущую {settings.mode.value.upper()}-позицию market reduce-only?\n"
                "Новые входы будут поставлены на паузу; обратная позиция не создаётся.",
                flatten_position,
                pause=True,
            )

        def _confirm_maintenance(
            self,
            title: str,
            message: str,
            action: Callable[[str], None] | None,
            *,
            pause: bool = False,
        ) -> None:
            answer = QMessageBox.warning(
                self,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
            try:
                if action is None:
                    raise RuntimeError("операция недоступна в текущем профиле")
                if pause and state_machine.state in {AppState.ARMED, AppState.RUNNING}:
                    state_machine.transition(AppState.PAUSED, f"operator requested {title}")
                action(model.state.symbol)
                model.append_risk_event(
                    f"{datetime.now(UTC).isoformat()} operator requested {title}"
                )
            except Exception as exc:
                model.set_error(
                    UserFacingError(
                        _safe_error(exc),
                        "Новые торговые заявки автоматически не отправлялись.",
                        "Проверьте Orders/Position в Bybit и выполните reconciliation.",
                    )
                )

        def _emergency(self) -> None:
            answer = QMessageBox.warning(
                self,
                "Emergency stop",
                "Запретить новые входы, отменить активный вход и аварийно закрыть "
                f"{settings.mode.value.upper()}-позицию reduce-only?\n"
                "Для Mainnet после kill switch разрешены только cancel и reduce-only.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                state_machine.transition(AppState.EMERGENCY_STOP, "operator emergency stop")
                if emergency_strategy is not None:
                    emergency_strategy()
                model.append_risk_event(f"{datetime.now(UTC).isoformat()} operator emergency stop")
            except InvalidStateTransition as exc:
                self.statusBar().showMessage(str(exc), 5000)
            except Exception as exc:
                model.set_error(
                    UserFacingError(
                        _safe_error(exc),
                        "Новые входы заблокированы состоянием EMERGENCY_STOP.",
                        "Проверьте позицию в Bybit и выполните reconciliation.",
                    )
                )

        def _remember_symbol(self, value: str) -> None:
            try:
                symbols = symbol_history.remember(value)
            except (OSError, ValueError):
                return
            current = self.symbol_input.currentText()
            choices = list(_WORKBENCH_SYMBOLS)
            for remembered in symbols:
                if remembered not in choices:
                    choices.append(remembered)
            if current and current not in choices:
                choices.append(current)
            self.symbol_input.blockSignals(True)
            try:
                self.symbol_input.clear()
                self.symbol_input.addItems(choices)
                self.symbol_input.setCurrentText(current)
            finally:
                self.symbol_input.blockSignals(False)

        def _market_selection_changed(self, *_args: object) -> None:
            symbol = self.symbol_input.text().strip().upper()
            interval = self.timeframe_combo.currentText().strip()
            if not symbol or not interval:
                return
            self._pending_auto_recommendation = True
            self._last_recommendation_market = None
            self._apply_direction_gate(None)
            self.recommendation_label.setText(
                "Загружаем свежий рынок и готовим новый автоплан…"
            )
            self._invalidate_manual_plan()
            if state_machine.state in {
                AppState.READY,
                AppState.PAUSED,
                AppState.DEGRADED,
            } and switch_read_only_market is not None:
                try:
                    self._connection_action = "reconnecting"
                    self._set_connection_activity(
                        f"Переключение рынка… {symbol} · {interval}", "busy"
                    )
                    switch_read_only_market(symbol, interval)
                except Exception as exc:
                    model.set_error(
                        UserFacingError(
                            _safe_error(exc),
                            "Старая read-only сессия сохранена или остановлена безопасно.",
                            "Повторите выбор рынка или переподключитесь read-only.",
                        )
                    )

        def _recommend_market_plan(self) -> None:
            state = model.state
            try:
                if state.engine_state not in {AppState.READY, AppState.PAUSED}:
                    raise RuntimeError("read-only рынок ещё не синхронизирован")
                if state.instrument is None:
                    raise RuntimeError("InstrumentRules ещё не получены")
                symbol = self.symbol_input.text().strip().upper()
                interval = self.timeframe_combo.currentText().strip()
                if state.symbol != symbol or state.timeframe != interval:
                    raise RuntimeError(
                        "выбранный рынок ещё синхронизируется; дождитесь READY"
                    )
                recommendation = recommend_market_plan(
                    symbol=symbol,
                    timeframe=interval,
                    candles=state.candles,
                    instrument=state.instrument,
                    mark_price=state.mark_price,
                    last_price=state.last_price,
                )
                order_type = self._selected_entry_order_type()
                entry_reference = (
                    state.mark_price
                    if order_type is OrderType.MARKET and state.mark_price is not None
                    else recommendation.entry_price
                )
                widgets = (
                    self.direction_combo,
                    self.entry_input,
                    self.stop_input,
                    self.take_profit_input,
                )
                for widget in widgets:
                    widget.blockSignals(True)
                try:
                    self.direction_combo.setCurrentText(recommendation.direction.value)
                    self.entry_input.setText(format(entry_reference, "f"))
                    self.stop_input.setText(format(recommendation.stop_price, "f"))
                    self.take_profit_input.setText(format(recommendation.take_profit, "f"))
                finally:
                    for widget in widgets:
                        widget.blockSignals(False)
                self._apply_direction_gate(recommendation.direction)
                self._refresh_linked_percentages()
                workflow.invalidate("market recommendation refreshed")
                if invalidate_execution is not None:
                    invalidate_execution("market recommendation refreshed")
                prepared = workflow.check(
                    ManualTradeDraft(
                        symbol=symbol,
                        direction=recommendation.direction,
                        entry_price=entry_reference,
                        stop_price=recommendation.stop_price,
                        take_profit=recommendation.take_profit,
                        leverage=_decimal_input(self.leverage_input.text(), "Leverage"),
                        order_type=order_type,
                        reason="automatic Trend+ATR recommendation",
                    ),
                    profile=self._risk_profile_settings().to_domain(symbol),
                )
                self._set_position_size_from_decision(prepared)
                risk = prepared.decision
                status = "проходит локальный Risk Gate" if risk.approved else (
                    "локальный Risk Gate: " + ", ".join(risk.rejection_codes)
                )
                # Recommendation is preview-only. The one-click execution pipeline will
                # always repeat the risk and Mainnet preflight immediately before POST.
                workflow.invalidate("automatic recommendation preview")
                entry_title = "Market ref" if order_type is OrderType.MARKET else "Entry"
                self.recommendation_label.setText(
                    f"{recommendation.direction.value}: {entry_title} "
                    f"{_number(entry_reference)}, Stop "
                    f"{_number(recommendation.stop_price)}, TP "
                    f"{_number(recommendation.take_profit)}. "
                    f"Размер {_number(risk.candidate_quantity)} ед. / "
                    f"{self.position_notional_input.text() or 'авто'} USDT. {status}.\n"
                    f"{recommendation.reason}"
                )
                self._pending_auto_recommendation = False
                self._last_recommendation_market = (symbol, interval)
                model.clear_error()
                self.statusBar().showMessage(
                    "Автоплан обновлён. Можно нажимать «Исполнить сделку».",
                    5000,
                )
            except Exception as exc:
                self.recommendation_label.setText(
                    "Автоплан пока недоступен: " + _safe_error(exc)
                )

        def _open_credentials(self) -> None:
            if settings.mode is AppMode.REPLAY:
                return
            CredentialDialog(self).exec()

        def _run_access_diagnostics(self) -> None:
            if settings.mode is not AppMode.LIVE or start_access_diagnostics is None:
                return
            if self._access_diagnostics_active:
                return
            try:
                state = model.state
                if state.instrument is None:
                    raise RuntimeError("InstrumentRules ещё не получены")
                symbol = self.symbol_input.text().strip().upper()
                if state.instrument.symbol != symbol:
                    raise RuntimeError("выбранный инструмент ещё не синхронизирован")
                entry = self._entry_reference(self._selected_entry_order_type())
                notional = self._requested_position_notional()
                if notional is None:
                    raise RuntimeError(
                        "сначала обновите рекомендацию или задайте размер входа в USDT"
                    )
                raw_quantity = notional / entry
                step = state.instrument.qty_step
                quantity = (raw_quantity // step) * step
                if quantity < state.instrument.min_order_qty:
                    quantity = state.instrument.min_order_qty
                direction = PositionSide(self.direction_combo.currentText())
                side = "Buy" if direction is PositionSide.LONG else "Sell"
                request = {
                    "symbol": symbol,
                    "side": side,
                    "quantity": format(quantity, "f"),
                    "price": format(entry, "f"),
                }
                self._access_diagnostics_active = True
                self.access_diagnostics_button.setEnabled(False)
                self.access_diagnostics_button.setText("ДИАГНОСТИКА…")
                self.access_diagnostics_output.setPlainText(
                    "Проверяем API-key/KYC, public vs account instrument и "
                    "Limit/Market pre-check. Реальный ордер не создаётся…"
                )
                start_access_diagnostics(request)
            except Exception as exc:
                self._access_diagnostics_active = False
                self.access_diagnostics_button.setText("Диагностика доступа")
                self.access_diagnostics_button.setEnabled(
                    settings.mode is AppMode.LIVE
                    and start_access_diagnostics is not None
                )
                self.access_diagnostics_output.setPlainText(
                    "Диагностика не запущена: " + _safe_error(exc)
                )

        def _leverage_selection_changed(self, _value: str) -> None:
            self._invalidate_manual_plan()
            state = model.state
            actual = state.account_leverage
            selected = self.leverage_input.currentText().strip()
            if actual is None:
                self.leverage_status_label.setText(f"Выбрано: {selected}x · Bybit: —")
                return
            actual_text = _number(actual)
            marker = "✓" if actual == Decimal(selected) else "не применено"
            self.leverage_status_label.setText(
                f"Выбрано: {selected}x · Bybit: {actual_text}x · {marker}"
            )

        def _apply_mainnet_leverage(self) -> None:
            if settings.mode is not AppMode.LIVE or set_mainnet_leverage is None:
                return
            try:
                symbol = self.symbol_input.text().strip().upper()
                leverage = self.leverage_input.currentText().strip()
                self.apply_leverage_button.setEnabled(False)
                self.leverage_status_label.setText(
                    f"Применяем {leverage}x к {symbol}…"
                )
                applied = set_mainnet_leverage(symbol, leverage)
                self.leverage_input.setCurrentText(applied)
                self.leverage_status_label.setText(
                    f"Bybit: {applied}x · применено к {symbol}"
                )
                model.append_system_log(
                    f"Operator changed {symbol} leverage to {applied}x"
                )
                self.statusBar().showMessage(
                    f"{symbol}: плечо {applied}x применено на Bybit", 5000
                )
            except Exception as exc:
                self.leverage_status_label.setText(
                    "Плечо не изменено: " + _safe_error(exc)
                )
                self.statusBar().showMessage("Не удалось изменить плечо", 5000)
            finally:
                self.apply_leverage_button.setEnabled(
                    settings.mode is AppMode.LIVE and set_mainnet_leverage is not None
                )

        def _apply_mainnet_endpoint(self) -> None:
            if settings.mode is not AppMode.LIVE or set_mainnet_endpoint is None:
                return
            try:
                requested = self.endpoint_input.currentText().strip()
                self.apply_endpoint_button.setEnabled(False)
                self.statusBar().showMessage("Применяем Mainnet endpoint…")
                applied = set_mainnet_endpoint(requested)
                self.endpoint_input.setCurrentText(applied)
                model.clear_error()
                self._connection_action = "endpoint_saved"
                self.statusBar().showMessage(
                    f"Mainnet endpoint сохранён: {applied}",
                    5000,
                )
                self.refresh_from_model(force=True)
            except Exception as exc:
                self._set_connection_activity(
                    f"Endpoint не изменён: {_safe_error(exc)}",
                    "error",
                )
                self.statusBar().showMessage("Не удалось изменить Mainnet endpoint", 5000)
            finally:
                self.apply_endpoint_button.setEnabled(
                    settings.mode is AppMode.LIVE and set_mainnet_endpoint is not None
                )

        def _connect_read_only(self) -> None:
            if connect_read_only is None:
                self._set_connection_activity(
                    "Ошибка: read-only runtime недоступен в этой сборке.", "error"
                )
                return
            try:
                symbol = self.symbol_input.text().strip().upper()
                interval = self.timeframe_combo.currentText().strip()
                model.clear_error()
                model.set_market(symbol, interval)
                self._connection_action = "connecting"
                self._set_connection_activity(
                    "Подключение… Проверяем REST, синхронизируем аккаунт и запускаем WebSocket.",
                    "busy",
                )
                self.connect_button.setText("Подключение…")
                self.connect_button.setEnabled(False)
                self.disconnect_button.setText("Остановить подключение")
                self.disconnect_button.setEnabled(True)
                self.statusBar().showMessage("Read-only: устанавливается соединение с Bybit…")
                connect_read_only(symbol, interval)
            except Exception as exc:
                self._connection_action = "error"
                self._set_connection_activity(
                    f"Ошибка подключения: {_safe_error(exc)}", "error"
                )
                self.connect_button.setText("Подключить read-only")
                self.connect_button.setEnabled(True)
                self.disconnect_button.setText("Отключить")
                self.disconnect_button.setEnabled(False)
                model.set_error(
                    UserFacingError(
                        _safe_error(exc),
                        "Подключение остановлено; новые входы запрещены.",
                        "Проверьте профиль ключей, режим и доступность сети, затем повторите.",
                    )
                )

        def _disconnect_read_only(self) -> None:
            if disconnect_read_only is None:
                self._set_connection_activity(
                    "Ошибка: команда отключения недоступна в этой сборке.", "error"
                )
                return
            try:
                self._connection_action = "disconnecting"
                self._set_connection_activity(
                    "Отключение… Останавливаем фоновые REST/WebSocket-сессии.", "busy"
                )
                self.connect_button.setEnabled(False)
                self.disconnect_button.setText("Отключение…")
                self.disconnect_button.setEnabled(False)
                self.statusBar().showMessage("Read-only: отключение…")
                disconnect_read_only()
            except Exception as exc:
                self._connection_action = "error"
                self._set_connection_activity(
                    f"Ошибка отключения: {_safe_error(exc)}", "error"
                )
                model.set_error(
                    UserFacingError(
                        _safe_error(exc),
                        "Новые входы остаются запрещены.",
                        "Закройте приложение, если фоновая сессия не остановилась.",
                    )
                )

        def _set_connection_activity(self, text: str, kind: str) -> None:
            self.connection_activity.setText(text)
            color = {
                "idle": "#94a3b8",
                "busy": "#f59e0b",
                "ok": "#22c55e",
                "error": "#fb7185",
            }.get(kind, "#94a3b8")
            self.connection_activity.setStyleSheet(
                f"color:{color};font-weight:700;padding:6px;border:1px solid #334155;"
                "border-radius:4px"
            )

        def _selected_endpoint(self) -> str | None:
            if get_mainnet_endpoint is not None:
                with suppress(Exception):
                    return get_mainnet_endpoint()
            return settings.endpoint_profile.rest_url

        def _set_endpoint_controls_enabled(self, enabled: bool) -> None:
            if settings.mode is not AppMode.LIVE:
                enabled = False
            self.endpoint_input.setEnabled(enabled)
            self.apply_endpoint_button.setEnabled(
                enabled and set_mainnet_endpoint is not None
            )

        def _refresh_connection_controls(self, state: Any) -> None:
            if settings.mode is AppMode.REPLAY:
                return
            engine_state = state.engine_state
            endpoint_editable = engine_state in {AppState.DISCONNECTED, AppState.ERROR}
            self._set_endpoint_controls_enabled(endpoint_editable)
            if state.error is not None and engine_state in {AppState.DISCONNECTED, AppState.ERROR}:
                self._connection_action = "error"
                self._set_connection_activity(
                    f"Ошибка подключения: {state.error.what_happened}", "error"
                )
                self.connect_button.setText("Подключить read-only")
                self.connect_button.setEnabled(connect_read_only is not None)
                self.disconnect_button.setText("Отключить")
                self.disconnect_button.setEnabled(False)
                return
            if (
                engine_state in {AppState.DISCONNECTED, AppState.ERROR}
                and self._connection_action == "endpoint_saved"
            ):
                endpoint = self._selected_endpoint() or "—"
                self._set_connection_activity(
                    f"Endpoint сохранён: {endpoint}. Теперь подключите read-only.",
                    "idle",
                )
                self.connect_button.setText("Подключить read-only")
                self.connect_button.setEnabled(connect_read_only is not None)
                self.disconnect_button.setText("Отключить")
                self.disconnect_button.setEnabled(False)
                return
            if engine_state is AppState.SYNCING:
                reconnecting = self._connection_action in {"connected", "reconnecting"}
                self._connection_action = "reconnecting" if reconnecting else "connecting"
                self._set_connection_activity(
                    "Восстановление связи… Выполняется повторная синхронизация."
                    if reconnecting
                    else (
                        "Подключение… Проверяем REST, синхронизируем аккаунт "
                        "и запускаем WebSocket."
                    ),
                    "busy",
                )
                self.connect_button.setText("Подключение…")
                self.connect_button.setEnabled(False)
                self.disconnect_button.setText("Остановить подключение")
                self.disconnect_button.setEnabled(disconnect_read_only is not None)
                return
            if engine_state is AppState.DEGRADED:
                self._connection_action = "reconnecting"
                self._set_connection_activity(
                    "Связь нестабильна. Workbench автоматически восстанавливает "
                    "read-only соединение…",
                    "busy",
                )
                self.connect_button.setText("Переподключение…")
                self.connect_button.setEnabled(False)
                self.disconnect_button.setText("Остановить подключение")
                self.disconnect_button.setEnabled(disconnect_read_only is not None)
                return
            if engine_state in {AppState.READY, AppState.ARMED, AppState.RUNNING, AppState.PAUSED}:
                self._connection_action = "connected"
                self._remember_symbol(state.symbol)
                if state.equity == 0 and state.available_balance == 0:
                    self._set_connection_activity(
                        "Подключено, но торговый баланс UNIFIED равен 0. "
                        "Если средства находятся в Funding, переведите их в Unified Trading; "
                        "если они уже в Unified, откройте системный журнал и диагностику.",
                        "busy",
                    )
                else:
                    self._set_connection_activity(
                        "Подключено. Read-only данные синхронизированы; "
                        "торговые мутации определяются отдельным arming.",
                        "ok",
                    )
                self.statusBar().showMessage(
                    "Read-only подключено; торговые мутации заблокированы arming-gate"
                )
                self.connect_button.setText("Read-only подключено")
                self.connect_button.setEnabled(False)
                self.disconnect_button.setText("Отключить")
                self.disconnect_button.setEnabled(disconnect_read_only is not None)
                return
            if self._connection_action in {"connecting", "reconnecting"}:
                # connect_read_only may hand work to an asynchronous runtime and return
                # before the state machine reaches SYNCING. A transient DISCONNECTED
                # snapshot therefore must not erase the visible in-progress state.
                reconnecting = self._connection_action == "reconnecting"
                self._set_connection_activity(
                    "Восстановление связи… Ожидаем начало повторной синхронизации."
                    if reconnecting
                    else "Подключение… Ожидаем начало синхронизации runtime.",
                    "busy",
                )
                self.connect_button.setText(
                    "Переподключение…" if reconnecting else "Подключение…"
                )
                self.connect_button.setEnabled(False)
                self.disconnect_button.setText("Остановить подключение")
                self.disconnect_button.setEnabled(disconnect_read_only is not None)
                return
            if self._connection_action == "disconnecting":
                self._connection_action = "idle"
                self.statusBar().showMessage("Read-only отключено", 5000)
            self._set_connection_activity(
                "Отключено. Нажмите «Подключить read-only».", "idle"
            )
            self.connect_button.setText("Подключить read-only")
            self.connect_button.setEnabled(connect_read_only is not None)
            self.disconnect_button.setText("Отключить")
            self.disconnect_button.setEnabled(False)

        def closeEvent(self, event: Any) -> None:
            self._refresh_timer.stop()
            self._fake_timer.stop()
            if disconnect_read_only is not None:
                with suppress(Exception):
                    disconnect_read_only()
            super().closeEvent(event)

        def _apply_theme(self) -> None:
            mode_color = {
                AppMode.REPLAY: "#475569",
                AppMode.TESTNET: "#2563eb",
                AppMode.DEMO: "#7c3aed",
                AppMode.LIVE: "#b91c1c",
            }[settings.mode]
            self.setStyleSheet(
                "QWidget{background:#0b1220;color:#e2e8f0;font-size:12px}"
                "QFrame#header{background:#111827;border:1px solid #263244;border-radius:7px}"
                f"QLabel#modeBadge{{background:{mode_color};color:white;font-size:16px;"
                "font-weight:800;padding:8px 14px;border-radius:5px}"
                "QLabel#engineBadge{background:#1e293b;color:#cbd5e1;font-weight:700;"
                "padding:8px 12px;border-radius:5px}"
                "QLabel#executionEnabledBadge{background:#78350f;color:#fde68a;font-weight:800;"
                "padding:8px 12px;border-radius:5px}"
                "QLabel#sectionTitle{font-size:17px;font-weight:700}"
                "QLabel#price{font-size:25px;font-weight:800;color:#f8fafc}"
                "QLabel#metricValue{font-size:14px;font-weight:700}"
                "QLabel#muted{color:#94a3b8}"
                "QLabel#chartFallback{background:#111827;border:1px dashed #334155;"
                "color:#94a3b8;font-size:16px}"
                "QLabel#errorBanner{background:#431407;color:#fed7aa;border:1px solid #c2410c;"
                "padding:9px;border-radius:5px}"
                "QGroupBox{border:1px solid #263244;border-radius:6px;margin-top:9px;"
                "padding:10px 7px 7px 7px;font-weight:600}"
                "QGroupBox::title{subcontrol-origin:margin;left:9px;padding:0 4px;color:#cbd5e1}"
                "QLineEdit,QComboBox,QTextEdit,QListWidget,QTableWidget{background:#111827;"
                "border:1px solid #334155;border-radius:4px;padding:5px;"
                "selection-background-color:#1d4ed8}"
                "QPushButton{background:#1e3a5f;border:1px solid #315b85;border-radius:4px;"
                "padding:7px 10px;font-weight:600}"
                "QPushButton:hover{background:#244c78}QPushButton:disabled{color:#64748b;"
                "background:#172033;border-color:#263244}"
                "QPushButton#buyButton{background:#064e3b;border-color:#10b981;color:#d1fae5}"
                "QPushButton#buyButton:hover{background:#065f46}"
                "QPushButton#sellButton{background:#7f1d1d;border-color:#ef4444;color:#fee2e2}"
                "QPushButton#sellButton:hover{background:#991b1b}"
                "QPushButton#buyButton:disabled,QPushButton#sellButton:disabled{"
                "background:#172033;border-color:#263244;color:#64748b}"
                "QPushButton#dangerButton{background:#7f1d1d;border-color:#dc2626}"
                "QHeaderView::section{background:#172033;color:#cbd5e1;padding:5px;border:0}"
                "QTabBar::tab{background:#172033;padding:7px 12px;margin-right:2px}"
                "QTabBar::tab:selected{background:#1e3a5f}"
            )

    def _metric(title: str, value: str) -> Any:
        label = QLabel(f"{title}\n{value}")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumWidth(95)
        return label

    def _table(headers: list[str]) -> Any:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _set_table_row(table: Any, row: int, values: tuple[Any, ...]) -> None:
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(str(value)))

    def _set_list(widget: Any, values: tuple[str, ...]) -> None:
        widget.clear()
        widget.addItems(list(reversed(values)))

    return MainWindow()


def _number(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return format(value, "f")


def _optional_positive_decimal(value: str) -> Decimal | None:
    cleaned = value.strip().replace(",", ".")
    if not cleaned:
        return None
    try:
        result = Decimal(cleaned)
    except Exception:
        return None
    return result if result > 0 else None


def _optional_signed_decimal(value: str) -> Decimal | None:
    cleaned = value.strip().replace(",", ".")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _percent_from_reference(price: Decimal, reference: Decimal) -> Decimal:
    if price <= 0 or reference <= 0:
        raise ValueError("price and reference must be positive")
    return (price / reference - Decimal("1")) * Decimal("100")


def _price_from_percent(
    reference: Decimal,
    percent: Decimal,
    tick_size: Decimal | None = None,
) -> Decimal:
    if reference <= 0:
        raise ValueError("reference must be positive")
    price = reference * (Decimal("1") + percent / Decimal("100"))
    if price <= 0:
        raise ValueError("linked percentage produced a non-positive price")
    if tick_size is None or tick_size <= 0:
        return price
    units = (price / tick_size).to_integral_value(rounding=ROUND_HALF_UP)
    return units * tick_size


def _format_percent(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, "f")


def _format_money(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, "f")


def _safe_error(error: Exception) -> str:
    """Return a log-safe error string without serializing exception attributes."""

    text = str(error).strip()
    return text or error.__class__.__name__


def _decimal_input(value: str, name: str) -> Decimal:
    cleaned = value.strip().replace(",", ".")
    if not cleaned:
        raise ValueError(f"{name} is required")
    try:
        result = Decimal(cleaned)
    except Exception as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _optional_decimal_input(value: str) -> Decimal | None:
    return None if not value.strip() else _decimal_input(value, "Take profit")


def _nonnegative_decimal_input(value: str, name: str) -> Decimal:
    cleaned = value.strip().replace(",", ".")
    if not cleaned:
        raise ValueError(f"{name} is required")
    try:
        result = Decimal(cleaned)
    except Exception as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if result < 0:
        raise ValueError(f"{name} cannot be negative")
    return result
