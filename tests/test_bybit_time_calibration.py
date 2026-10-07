from __future__ import annotations

from bybit_workbench.exchange.bybit.time_calibration import (
    build_bybit_time_calibration,
)


def test_calibrated_timestamp_tracks_bybit_not_local_wall_clock() -> None:
    calibration = build_bybit_time_calibration(
        server_time_ms=10_000.0,
        wall_started_ns=9_400_000_000,
        wall_finished_ns=9_600_000_000,
        monotonic_started_ns=1_000_000_000,
        monotonic_finished_ns=1_200_000_000,
    )

    assert calibration.offset_ms == 500.0
    assert calibration.round_trip_ms == 200.0
    assert calibration.now_ms(monotonic_ns=1_300_000_000) == 10_200


def test_large_correctable_wall_offset_does_not_corrupt_exchange_timestamp() -> None:
    calibration = build_bybit_time_calibration(
        server_time_ms=50_000.0,
        wall_started_ns=42_000_000_000,
        wall_finished_ns=42_100_000_000,
        monotonic_started_ns=5_000_000_000,
        monotonic_finished_ns=5_100_000_000,
    )

    assert calibration.offset_ms == 7_950.0
    assert calibration.now_ms(monotonic_ns=5_250_000_000) == 50_200


def test_calibration_rejects_backwards_probe_clocks() -> None:
    try:
        build_bybit_time_calibration(
            server_time_ms=1.0,
            wall_started_ns=10,
            wall_finished_ns=9,
            monotonic_started_ns=10,
            monotonic_finished_ns=11,
        )
    except ValueError as exc:
        assert "wall clock moved backwards" in str(exc)
    else:
        raise AssertionError("expected ValueError")
