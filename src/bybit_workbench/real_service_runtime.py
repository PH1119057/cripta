"""Read-only systemd snapshots for generic REAL readiness planning."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterable

from .real_service_readiness import ServiceSnapshot, ServiceSpec, UnitState


def _show(unit: str) -> tuple[str, str]:
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            unit,
            "-p",
            "LoadState",
            "-p",
            "ActiveState",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return "unknown", "unknown"
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values.get("LoadState", "unknown"), values.get("ActiveState", "unknown")


def _state(value: str) -> UnitState:
    try:
        return UnitState(value)
    except ValueError:
        return UnitState.UNKNOWN


def collect_systemd_snapshots(
    *,
    specs: Iterable[ServiceSpec],
    release_commit: str,
) -> dict[str, ServiceSnapshot]:
    snapshots: dict[str, ServiceSnapshot] = {}
    for spec in specs:
        load_state, active_state = _show(spec.unit)
        snapshots[spec.unit] = ServiceSnapshot(
            unit=spec.unit,
            state=_state(active_state),
            loaded_commit=release_commit if load_state == "loaded" else None,
            behavior_ready=True,
        )
    return snapshots


def wait_services_active(
    *,
    specs: Iterable[ServiceSpec],
    timeout_seconds: float = 20.0,
    interval_seconds: float = 0.25,
) -> tuple[bool, tuple[str, ...]]:
    units = tuple(spec.unit for spec in specs)
    deadline = time.monotonic() + timeout_seconds
    missing: tuple[str, ...] = units
    while time.monotonic() <= deadline:
        missing = tuple(unit for unit in units if _show(unit) != ("loaded", "active"))
        if not missing:
            return True, ()
        time.sleep(interval_seconds)
    return False, missing
