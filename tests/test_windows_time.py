import subprocess
import unittest

from bybit_workbench.app.windows_time import resync_windows_time


class WindowsTimeSyncTests(unittest.TestCase):
    def test_non_windows_host_is_skipped_without_launching_process(self) -> None:
        called = False

        def runner(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal called
            called = True
            raise AssertionError("runner should not be called")

        result = resync_windows_time(runner=runner, os_name="posix")

        self.assertFalse(result.attempted)
        self.assertFalse(result.succeeded)
        self.assertFalse(called)

    def test_windows_resync_starts_service_then_executes_w32tm(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(command, **kwargs):  # type: ignore[no-untyped-def]
            calls.append((list(command), dict(kwargs)))
            if command[:2] == ["sc.exe", "start"]:
                return subprocess.CompletedProcess(command, 0, "SERVICE_START_PENDING\n", "")
            return subprocess.CompletedProcess(command, 0, "sync ok\n", "")

        result = resync_windows_time(runner=runner, os_name="nt")

        self.assertTrue(result.attempted)
        self.assertTrue(result.succeeded)
        self.assertEqual(calls[0][0], ["sc.exe", "start", "w32time"])
        self.assertEqual(calls[1][0], ["w32tm", "/resync"])
        self.assertFalse(bool(calls[0][1].get("shell", False)))
        self.assertFalse(bool(calls[1][1].get("shell", False)))
        self.assertIn("sync ok", result.detail)

    def test_already_running_service_is_not_a_resync_failure(self) -> None:
        calls: list[list[str]] = []

        def runner(command, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            calls.append(list(command))
            if command[:2] == ["sc.exe", "start"]:
                return subprocess.CompletedProcess(
                    command,
                    1056,
                    "",
                    "[SC] StartService FAILED 1056: service already running",
                )
            return subprocess.CompletedProcess(command, 0, "sync ok", "")

        result = resync_windows_time(runner=runner, os_name="nt")

        self.assertTrue(result.succeeded)
        self.assertEqual(
            calls,
            [["sc.exe", "start", "w32time"], ["w32tm", "/resync"]],
        )

    def test_service_start_failure_stops_before_resync(self) -> None:
        calls: list[list[str]] = []

        def runner(command, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 5, "", "access denied")

        result = resync_windows_time(runner=runner, os_name="nt")

        self.assertTrue(result.attempted)
        self.assertFalse(result.succeeded)
        self.assertEqual(calls, [["sc.exe", "start", "w32time"]])
        self.assertIn("exit code 5", result.detail)
        self.assertIn("access denied", result.detail)

    def test_failed_windows_resync_is_reported_fail_closed(self) -> None:
        def runner(command, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            if command[:2] == ["sc.exe", "start"]:
                return subprocess.CompletedProcess(command, 0, "started", "")
            return subprocess.CompletedProcess(command, 5, "", "access denied")

        result = resync_windows_time(runner=runner, os_name="nt")

        self.assertTrue(result.attempted)
        self.assertFalse(result.succeeded)
        self.assertIn("exit code 5", result.detail)
        self.assertIn("access denied", result.detail)


if __name__ == "__main__":
    unittest.main()
