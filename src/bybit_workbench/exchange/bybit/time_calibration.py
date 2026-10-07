from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BybitTimeCalibration:
    """Fresh Bybit server-time observation anchored to a monotonic midpoint."""

    server_time_ms: float
    local_midpoint_ms: float
    monotonic_midpoint_ns: int
    round_trip_ms: float

    @property
    def offset_ms(self) -> float:
        return self.server_time_ms - self.local_midpoint_ms

    def now_ms(self, *, monotonic_ns: int | None = None) -> int:
        """Return current Bybit-calibrated timestamp without trusting wall-clock drift."""

        current_ns = time.monotonic_ns() if monotonic_ns is None else monotonic_ns
        elapsed_ms = (current_ns - self.monotonic_midpoint_ns) / 1_000_000
        return int(self.server_time_ms + elapsed_ms)


def build_bybit_time_calibration(
    *,
    server_time_ms: float,
    wall_started_ns: int,
    wall_finished_ns: int,
    monotonic_started_ns: int,
    monotonic_finished_ns: int,
) -> BybitTimeCalibration:
    if monotonic_finished_ns < monotonic_started_ns:
        raise ValueError("monotonic clock moved backwards")
    if wall_finished_ns < wall_started_ns:
        raise ValueError("wall clock moved backwards during Bybit time probe")

    wall_midpoint_ms = (wall_started_ns + wall_finished_ns) / 2_000_000
    monotonic_midpoint_ns = (
        monotonic_started_ns + monotonic_finished_ns
    ) // 2
    round_trip_ms = (
        monotonic_finished_ns - monotonic_started_ns
    ) / 1_000_000
    return BybitTimeCalibration(
        server_time_ms=float(server_time_ms),
        local_midpoint_ms=wall_midpoint_ms,
        monotonic_midpoint_ns=monotonic_midpoint_ns,
        round_trip_ms=round_trip_ms,
    )
