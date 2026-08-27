from dataclasses import dataclass
from datetime import datetime, timedelta

from .health import HealthMonitor, ReconnectBackoff


@dataclass(frozen=True, slots=True)
class ReconnectDirective:
    channel: str
    delay_seconds: float
    subscriptions: tuple[str, ...]
    require_rest_reconciliation: bool


class StreamRecoveryCoordinator:
    HEARTBEAT_INTERVAL = timedelta(seconds=20)

    def __init__(
        self,
        symbol: str,
        interval: str,
        health: HealthMonitor,
        *,
        seed: int = 0,
    ) -> None:
        self.health = health
        self.subscriptions = {
            "public": (f"kline.{interval}.{symbol}", f"tickers.{symbol}"),
            "private": ("order", "execution", "position", "wallet"),
        }
        self._backoff = {
            "public": ReconnectBackoff(seed=seed),
            "private": ReconnectBackoff(seed=seed + 10_000),
        }
        self._last_ping: dict[str, datetime | None] = {"public": None, "private": None}

    def disconnected(self, channel: str, error: str) -> ReconnectDirective:
        self._require_stream_channel(channel)
        self.health.mark_error(channel, error)
        return ReconnectDirective(
            channel,
            self._backoff[channel].next_delay(),
            self.subscriptions[channel],
            channel == "private",
        )

    def connected(self, channel: str) -> tuple[str, ...]:
        self._require_stream_channel(channel)
        self._backoff[channel].reset()
        self.health.mark_connected(channel)
        return self.subscriptions[channel]

    def heartbeat_due(self, channel: str, now: datetime) -> bool:
        self._require_stream_channel(channel)
        if now.tzinfo is None:
            raise ValueError("heartbeat timestamp must be timezone-aware")
        previous = self._last_ping[channel]
        return previous is None or now - previous >= self.HEARTBEAT_INTERVAL

    def heartbeat_payload(self, channel: str, now: datetime) -> dict[str, str]:
        if not self.heartbeat_due(channel, now):
            raise RuntimeError("heartbeat is not due yet")
        self._last_ping[channel] = now
        return {"req_id": f"ping-{int(now.timestamp())}", "op": "ping"}

    @staticmethod
    def _require_stream_channel(channel: str) -> None:
        if channel not in {"public", "private"}:
            raise ValueError(f"unknown stream channel: {channel}")
