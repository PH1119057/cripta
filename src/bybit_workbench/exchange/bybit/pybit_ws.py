from datetime import UTC, datetime
from typing import Any

from .health import HealthMonitor
from .streams import BybitStreamProcessor


class PybitWebSocketBridge:
    """Registers read-only callbacks on injected official pybit WebSocket sessions."""

    def __init__(
        self,
        public_session: Any,
        private_session: Any,
        processor: BybitStreamProcessor,
        health: HealthMonitor,
    ) -> None:
        self.public_session = public_session
        self.private_session = private_session
        self.processor = processor
        self.health = health

    def subscribe(self, symbol: str, interval: str) -> None:
        self.public_session.kline_stream(
            interval=interval,
            symbol=symbol,
            callback=self._on_public,
        )
        self.public_session.ticker_stream(symbol=symbol, callback=self._on_public)
        self.private_session.order_stream(callback=self._on_private)
        self.private_session.execution_stream(callback=self._on_private)
        self.private_session.position_stream(callback=self._on_private)
        self.private_session.wallet_stream(callback=self._on_private)
        self.health.mark_connected("public")
        self.health.mark_connected("private")

    def close(self) -> None:
        for channel, session in (
            ("public", self.public_session),
            ("private", self.private_session),
        ):
            try:
                session.exit()
            except Exception as exc:
                self.health.mark_error(channel, str(exc))

    def transport_status(self, now: datetime) -> tuple[bool, bool]:
        """Refresh transport-level health without inventing public market data.

        Private streams can legitimately stay silent for a long time when there are
        no orders, executions, position changes, or wallet updates.  In that case a
        connected socket is sufficient evidence that the private transport is alive.
        Public freshness still comes only from actual ticker/kline callbacks so stale
        market data remains fail-closed.
        """

        public_connected = self._session_connected(self.public_session)
        private_connected = self._session_connected(self.private_session)
        if not public_connected:
            self.health.mark_error("public", "public WebSocket transport disconnected")
        if private_connected:
            self.health.mark_message("private", now)
        else:
            self.health.mark_error("private", "private WebSocket transport disconnected")
        return public_connected, private_connected

    @staticmethod
    def _session_connected(session: Any) -> bool:
        try:
            checker = getattr(session, "is_connected", None)
            return bool(checker()) if callable(checker) else False
        except Exception:
            return False

    def _on_public(self, message: dict[str, Any]) -> None:
        self.processor.on_public(message, received_at=datetime.now(UTC))

    def _on_private(self, message: dict[str, Any]) -> None:
        self.processor.on_private(message, received_at=datetime.now(UTC))
