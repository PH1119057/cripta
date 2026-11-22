"""#849: Exit-only OS transaction and failure-recovery tests.

Each fake service mutation has the same fixed unit identity as the real
host adapter. No actual systemd calls or production file writes are made.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

from component_release_exit_transition import (  # noqa: E402
    _EXIT_UNIT,
    _HostExitUnitIO,
    _run_exit_only_transition,
    exit_only_cutover_once,
)

OLD = b"[Unit]\nDescription=old\n[Service]\nExecStart=/old\n"
NEW = b"[Unit]\nDescription=new\n[Service]\nExecStart=/new\n"


class FakeJournal:
    def __init__(self, fail: str = "") -> None:
        self.events: list[str] = []
        self.fail = fail

    def record(self, phase: str) -> None:
        if self.fail == phase:
            raise OSError("journal write failed")
        self.events.append(phase)


class FakeExit:
    """Explicitly distinguish known systemd state from unknown timeout."""

    def __init__(self, fail: str = "", unknown: bool = False) -> None:
        self.fail = fail
        self.unknown = unknown
        self.current = "OLD_ACTIVE"
        self.unit = OLD
        self.calls: list[str] = []
        self.neighbor_pid = 4136853

    def _hit(self, op: str) -> None:
        self.calls.append(op)
        if self.fail == op:
            self.fail = ""  # one failed invocation, recovery is independent
            raise RuntimeError("injected " + op)

    def before(self) -> tuple[int, int]:
        self._hit("BEFORE")
        return (self.neighbor_pid, 0)

    def stop(self) -> None:
        self._hit("STOP")
        self.current = "OLD_STOPPED" if self.unit == OLD else "NEW_STOPPED"

    def prove_stopped(self) -> None:
        self._hit("PROVE_STOP")
        if self.current not in ("OLD_STOPPED", "NEW_STOPPED"):
            raise RuntimeError("not stopped")

    def install_unit(self, expected: bytes, replacement: bytes) -> None:
        self._hit("UNIT_WRITE")
        if self.unit != expected or self.current not in ("OLD_STOPPED", "NEW_STOPPED"):
            raise RuntimeError("wrong current")
        self.unit = replacement

    def reload(self) -> None:
        self._hit("RELOAD")

    def start(self) -> None:
        self._hit("START")
        self.current = "OLD_ACTIVE" if self.unit == OLD else "NEW_ACTIVE"

    def verify_new(self) -> None:
        self._hit("VERIFY_NEW")
        if self.current != "NEW_ACTIVE":
            raise RuntimeError("wrong candidate")

    def verify_old(self) -> None:
        self._hit("VERIFY_OLD")
        if self.current != "OLD_ACTIVE":
            raise RuntimeError("old not recovered")

    def verify_signed_and_recovery(self) -> None:
        self._hit("SIGNED_RECOVERY")

    def neighbors_unchanged(self, snapshot: tuple[int, int]) -> None:
        self._hit("NEIGHBORS")
        if snapshot != (self.neighbor_pid, 0):
            raise RuntimeError("neighbor changed")

    def reconcile(self) -> str:
        self.calls.append("RECONCILE")
        return "UNKNOWN" if self.unknown else self.current


class ExitTransactionTests(unittest.TestCase):
    def test_exact_exit_only_successful_transition_with_durable_phase_order(self) -> None:
        a, j = FakeExit(), FakeJournal()
        result = _run_exit_only_transition(a, j, old_unit=OLD, new_unit=NEW)
        self.assertEqual(result["state"], "EXIT_TRANSITION_ACCEPTANCE_PENDING")
        self.assertEqual(a.current, "NEW_ACTIVE")
        self.assertEqual(result["affected_unit"], _EXIT_UNIT)
        self.assertFalse(result["live_deployment_verified"])
        self.assertFalse(result["financial_mutation_authorized"])
        self.assertEqual(a.calls.count("STOP"), 1)
        self.assertEqual(a.calls.count("START"), 1)
        self.assertEqual(a.calls.count("UNIT_WRITE"), 1)
        self.assertEqual(a.calls.count("RELOAD"), 1)
        for intent, actual in (
            ("STOP_INTENT", "STOP"), ("UNIT_WRITE_INTENT", "UNIT_WRITE"),
            ("RELOAD_INTENT", "RELOAD"), ("START_INTENT", "START"),
        ):
            self.assertIn(intent, j.events)
            self.assertIn(actual, a.calls)
        self.assertEqual(j.events[-1], "ACCEPTANCE_PENDING")
        self.assertNotIn("ROLLBACK_INTENT", j.events)

    def test_faults_after_stop_recover_known_old_without_other_units(self) -> None:
        for failure in (
            "PROVE_STOP", "UNIT_WRITE", "RELOAD", "START", "VERIFY_NEW",
            "SIGNED_RECOVERY", "NEIGHBORS",
        ):
            with self.subTest(failure=failure):
                a, j = FakeExit(fail=failure), FakeJournal()
                result = _run_exit_only_transition(a, j, old_unit=OLD, new_unit=NEW)
                self.assertEqual(result["state"], "EXIT_OLD_RECOVERED_AFTER_FAILURE")
                self.assertEqual(a.current, "OLD_ACTIVE")
                self.assertEqual(a.unit, OLD)
                self.assertIn("ROLLBACK_INTENT", j.events)
                self.assertEqual(j.events[-1], "ROLLBACK_RECOVERED")
                self.assertFalse(result["live_deployment_verified"])
                self.assertFalse(result["financial_mutation_authorized"])
                self.assertFalse(result["release_authorized"])

    def test_before_stop_failure_makes_no_os_mutation(self) -> None:
        a, j = FakeExit(fail="BEFORE"), FakeJournal()
        result = _run_exit_only_transition(a, j, old_unit=OLD, new_unit=NEW)
        self.assertEqual(result["state"], "EXIT_PRESTOP_BLOCKED")
        self.assertEqual(a.current, "OLD_ACTIVE")
        self.assertEqual(a.calls, ["BEFORE"])
        self.assertNotIn("STOP_INTENT", j.events)

    def test_journal_failure_before_stop_prevents_os_mutation(self) -> None:
        a, j = FakeExit(), FakeJournal(fail="STOP_INTENT")
        result = _run_exit_only_transition(a, j, old_unit=OLD, new_unit=NEW)
        self.assertEqual(result["state"], "EXIT_PRESTOP_BLOCKED")
        self.assertEqual(a.current, "OLD_ACTIVE")
        self.assertNotIn("STOP", a.calls)

    def test_lost_stop_outcome_unknown_stops_without_blind_retry(self) -> None:
        a, j = FakeExit(fail="STOP", unknown=True), FakeJournal()
        result = _run_exit_only_transition(a, j, old_unit=OLD, new_unit=NEW)
        self.assertEqual(result["state"], "EXIT_OUTCOME_UNKNOWN_STOP")
        self.assertEqual(a.calls.count("STOP"), 1)
        self.assertEqual(a.calls.count("RECONCILE"), 1)
        self.assertEqual(a.calls.count("UNIT_WRITE"), 0)
        self.assertEqual(a.calls.count("START"), 0)
        self.assertFalse(result["rollback_verified"])

    def test_failed_rollback_is_visible_not_success(self) -> None:
        a, j = FakeExit(fail="VERIFY_NEW"), FakeJournal()
        original = a.verify_old
        def broken_old() -> None:
            original()
            raise RuntimeError("forced rollback behavior failure")
        a.verify_old = broken_old
        result = _run_exit_only_transition(a, j, old_unit=OLD, new_unit=NEW)
        self.assertEqual(result["state"], "EXIT_ROLLBACK_NOT_VERIFIED")
        self.assertFalse(result["rollback_verified"])
        self.assertNotIn("ROLLBACK_RECOVERED", j.events)

    def test_journal_failure_midtransition_requires_reconcile_and_rollback(self) -> None:
        a, j = FakeExit(), FakeJournal(fail="UNIT_WRITE_INTENT")
        result = _run_exit_only_transition(a, j, old_unit=OLD, new_unit=NEW)
        self.assertEqual(result["state"], "EXIT_ROLLBACK_NOT_VERIFIED")
        self.assertNotIn("UNIT_WRITE", a.calls)
        self.assertFalse(result["rollback_verified"])

    def test_bad_duplicate_or_equal_unit_material_forbidden(self) -> None:
        a, j = FakeExit(), FakeJournal()
        for old, new in ((OLD, OLD), (b"", NEW), (OLD, b""), (OLD, b"x" * 16385)):
            with self.subTest(old=len(old), new=len(new)):
                with self.assertRaisesRegex(ValueError, "UNIT_INPUT_INVALID"):
                    _run_exit_only_transition(a, j, old_unit=old, new_unit=new)
        self.assertEqual(a.calls, [])

    def test_public_noarg_cutover_fails_closed_no_candidate_before_os_writes(self) -> None:
        self.assertEqual(exit_only_cutover_once.__code__.co_argcount, 0)
        with (
            patch("component_release_exit_transition._protected_real_admission",
                  side_effect=ValueError("EXIT_TRANSITION_RELEASE_NOT_MERGED")),
            patch("component_release_exit_transition._HostExitUnitIO") as adapter,
            self.assertRaisesRegex(ValueError, "RELEASE_NOT_MERGED"),
        ):
            exit_only_cutover_once()
        adapter.assert_not_called()

    def test_host_io_only_exact_exit_unit_is_written_or_restarted(self) -> None:
        import inspect
        source = inspect.getsource(_HostExitUnitIO)
        for forbidden in (
            "cripta-universal-entry-consumer.service",
            "cripta-private-runtime.service",
            "postgresql.service",
            "OWNER_ARM",
            "Bybit",
            "shell=True",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
