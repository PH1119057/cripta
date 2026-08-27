from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WindowsTimeSyncResult:
    attempted: bool
    succeeded: bool
    command: tuple[str, ...]
    detail: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(
    runner: Runner,
    command: Sequence[str],
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return runner(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _compact_output(completed: subprocess.CompletedProcess[str]) -> str:
    output = (
        (completed.stdout or completed.stderr or "")
        .strip()
        .replace("\r", " ")
        .replace("\n", " ")
    )
    output = " ".join(output.split())
    return output if len(output) <= 240 else output[:237] + "..."


def _service_already_running(completed: subprocess.CompletedProcess[str]) -> bool:
    # SC.exe reports ERROR_SERVICE_ALREADY_RUNNING as decimal 1056.  The text
    # around the numeric code is localized, so key off the stable Windows code.
    return "1056" in _compact_output(completed)


def resync_windows_time(
    *,
    runner: Runner = subprocess.run,
    os_name: str | None = None,
    timeout_seconds: float = 15.0,
) -> WindowsTimeSyncResult:
    """Ensure W32Time is running, then request an immediate resynchronization.

    The Workbench deployment runs elevated.  We deliberately avoid shell=True:
    service start and resync are launched as explicit argv lists.  Windows may
    stop W32Time when it is configured as a trigger-start service, so every
    recovery attempt first makes sure the service is running.
    """

    selected_os = os.name if os_name is None else os_name
    resync_command: Sequence[str] = ("w32tm", "/resync")
    if selected_os != "nt":
        return WindowsTimeSyncResult(
            attempted=False,
            succeeded=False,
            command=tuple(resync_command),
            detail="Windows time sync skipped: non-Windows host",
        )
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    service_command: Sequence[str] = ("sc.exe", "start", "w32time")
    try:
        service = _run(runner, service_command, timeout_seconds=timeout_seconds)
    except FileNotFoundError:
        return WindowsTimeSyncResult(
            attempted=True,
            succeeded=False,
            command=tuple(service_command),
            detail="sc.exe is not available; cannot start Windows Time service",
        )
    except subprocess.TimeoutExpired:
        return WindowsTimeSyncResult(
            attempted=True,
            succeeded=False,
            command=tuple(service_command),
            detail=f"starting Windows Time service timed out after {timeout_seconds:.1f}s",
        )
    except Exception as exc:
        return WindowsTimeSyncResult(
            attempted=True,
            succeeded=False,
            command=tuple(service_command),
            detail=f"Windows Time service start failed: {type(exc).__name__}: {exc}",
        )

    if service.returncode != 0 and not _service_already_running(service):
        output = _compact_output(service)
        return WindowsTimeSyncResult(
            attempted=True,
            succeeded=False,
            command=tuple(service_command),
            detail=(
                f"sc.exe start w32time returned exit code {service.returncode}"
                + (f": {output}" if output else "")
            ),
        )

    try:
        completed = _run(runner, resync_command, timeout_seconds=timeout_seconds)
    except FileNotFoundError:
        return WindowsTimeSyncResult(
            attempted=True,
            succeeded=False,
            command=tuple(resync_command),
            detail="w32tm is not available",
        )
    except subprocess.TimeoutExpired:
        return WindowsTimeSyncResult(
            attempted=True,
            succeeded=False,
            command=tuple(resync_command),
            detail=f"w32tm /resync timed out after {timeout_seconds:.1f}s",
        )
    except Exception as exc:
        return WindowsTimeSyncResult(
            attempted=True,
            succeeded=False,
            command=tuple(resync_command),
            detail=f"w32tm /resync failed to start: {type(exc).__name__}: {exc}",
        )

    output = _compact_output(completed)
    service_note = "W32Time running"
    if completed.returncode == 0:
        return WindowsTimeSyncResult(
            attempted=True,
            succeeded=True,
            command=tuple(resync_command),
            detail=(
                f"{service_note}; "
                + (output or "w32tm /resync completed successfully")
            ),
        )
    return WindowsTimeSyncResult(
        attempted=True,
        succeeded=False,
        command=tuple(resync_command),
        detail=(
            f"{service_note}; w32tm /resync returned exit code {completed.returncode}"
            + (f": {output}" if output else "")
        ),
    )
