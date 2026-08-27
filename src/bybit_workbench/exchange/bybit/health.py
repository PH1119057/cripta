import random
from dataclasses import dataclass
from datetime import datetime
from threading import RLock


@dataclass(frozen=True, slots=True)
class ChannelHealth:
    connected: bool
    fresh: bool
    last_message_at: datetime | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class BybitHealthSnapshot:
    public: ChannelHealth
    private: ChannelHealth
    rest: ChannelHealth

    @property
    def can_create_entry(self) -> bool:
        return self.public.fresh and self.private.fresh and self.rest.fresh


class HealthMonitor:
    def __init__(
        self,
        *,
        max_public_age_seconds: float = 10.0,
        max_private_age_seconds: float = 30.0,
        max_rest_age_seconds: float = 60.0,
    ) -> None:
        self._max_ages = {
            "public": max_public_age_seconds,
            "private": max_private_age_seconds,
            "rest": max_rest_age_seconds,
        }
        if any(value <= 0 for value in self._max_ages.values()):
            raise ValueError("health age limits must be positive")
        self._connected = {"public": False, "private": False, "rest": False}
        self._last_message: dict[str, datetime | None] = {
            "public": None,
            "private": None,
            "rest": None,
        }
        self._last_error: dict[str, str | None] = {
            "public": None,
            "private": None,
            "rest": None,
        }
        self._lock = RLock()

    def mark_connected(self, channel: str) -> None:
        with self._lock:
            self._require_channel(channel)
            self._connected[channel] = True
            self._last_error[channel] = None

    def mark_message(self, channel: str, occurred_at: datetime) -> None:
        with self._lock:
            self._require_channel(channel)
            if occurred_at.tzinfo is None:
                raise ValueError("health timestamps must be timezone-aware")
            self._connected[channel] = True
            current = self._last_message[channel]
            if current is None or occurred_at > current:
                self._last_message[channel] = occurred_at
            self._last_error[channel] = None

    def mark_error(self, channel: str, error: str) -> None:
        with self._lock:
            self._require_channel(channel)
            self._connected[channel] = False
            self._last_error[channel] = error

    def mark_transient_error(self, channel: str, error: str) -> None:
        """Record a recoverable error without discarding a still-fresh snapshot.

        A single REST read timeout does not prove that the channel is unusable.
        Keeping the previous successful observation alive until its normal freshness
        deadline prevents UI flapping while the background runtime retries quickly.
        """

        with self._lock:
            self._require_channel(channel)
            self._last_error[channel] = error

    def snapshot(self, now: datetime) -> BybitHealthSnapshot:
        with self._lock:
            if now.tzinfo is None:
                raise ValueError("health evaluation timestamp must be timezone-aware")
            return BybitHealthSnapshot(
                self._channel_snapshot("public", now),
                self._channel_snapshot("private", now),
                self._channel_snapshot("rest", now),
            )

    def _channel_snapshot(self, channel: str, now: datetime) -> ChannelHealth:
        observed = self._last_message[channel]
        fresh = False
        if self._connected[channel] and observed is not None and observed <= now:
            fresh = (now - observed).total_seconds() <= self._max_ages[channel]
        return ChannelHealth(
            self._connected[channel],
            fresh,
            observed,
            self._last_error[channel],
        )

    @staticmethod
    def _require_channel(channel: str) -> None:
        if channel not in {"public", "private", "rest"}:
            raise ValueError(f"unknown health channel: {channel}")


class ReconnectBackoff:
    def __init__(
        self,
        *,
        base_seconds: float = 1.0,
        maximum_seconds: float = 30.0,
        jitter_ratio: float = 0.2,
        seed: int = 0,
    ) -> None:
        if base_seconds <= 0 or maximum_seconds < base_seconds:
            raise ValueError("invalid reconnect delay bounds")
        if jitter_ratio < 0 or jitter_ratio > 1:
            raise ValueError("jitter_ratio must be between 0 and 1")
        self.base_seconds = base_seconds
        self.maximum_seconds = maximum_seconds
        self.jitter_ratio = jitter_ratio
        self.seed = seed
        self.attempt = 0

    def next_delay(self) -> float:
        nominal = min(self.maximum_seconds, self.base_seconds * (2**self.attempt))
        rng = random.Random(self.seed + self.attempt)
        jitter = nominal * self.jitter_ratio * rng.uniform(-1, 1)
        self.attempt += 1
        return float(max(0.0, min(self.maximum_seconds, nominal + jitter)))

    def reset(self) -> None:
        self.attempt = 0
