from enum import StrEnum


class AppMode(StrEnum):
    REPLAY = "replay"
    TESTNET = "testnet"
    DEMO = "demo"
    LIVE = "live"


class ExecutionMode(StrEnum):
    """Volatile Mainnet execution state; never persisted as an armed capability."""

    SHADOW = "SHADOW"
    MICRO_LIVE = "MICRO_LIVE"
    LIVE = "LIVE"


class AppState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    SYNCING = "SYNCING"
    READY = "READY"
    ARMED = "ARMED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class OrderSide(StrEnum):
    BUY = "Buy"
    SELL = "Sell"


class PositionSide(StrEnum):
    LONG = "Long"
    SHORT = "Short"
    FLAT = "Flat"


class OrderType(StrEnum):
    MARKET = "Market"
    LIMIT = "Limit"


class OrderStatus(StrEnum):
    CREATED = "Created"
    ACCEPTED = "Accepted"
    PARTIALLY_FILLED = "PartiallyFilled"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"


class OrderRole(StrEnum):
    ENTRY = "Entry"
    EXIT = "Exit"
    PROTECTIVE = "Protective"


class FillReason(StrEnum):
    ENTRY = "Entry"
    STOP_LOSS = "StopLoss"
    TAKE_PROFIT = "TakeProfit"
    EMERGENCY_FLATTEN = "EmergencyFlatten"
    STRATEGY_EXIT = "StrategyExit"
