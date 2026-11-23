"""#849: Exit-only OS transaction and failure-recovery tests.

Each fake service mutation has the same fixed unit identity as the real
host adapter. No actual systemd calls or production file writes are made.
"""
from __future__ import annotations

import json
import sys
import unittest
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

import component_release_exit_transition as transition  # noqa: E402

_EXIT_UNIT = transition._EXIT_UNIT
_HostExitUnitIO = transition._HostExitUnitIO
_SOURCE = transition._SOURCE
_collect_credential_local_exit_snapshot = (
    transition._collect_credential_local_exit_snapshot
)
_run_exit_only_transition = transition._run_exit_only_transition
_validate_flat_exit_status = transition._validate_flat_exit_status
exit_only_cutover_once = transition.exit_only_cutover_once

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

    def close(self) -> None:
        self.events.append("CLOSED")

    def record_baseline(self, snapshot: object) -> None:
        if self.fail == "BASELINE":
            raise OSError("journal baseline failed")
        self.events.append("BASELINE")


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
        self.current = "OLD_STOPPED" if replacement == OLD else "NEW_STOPPED"

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

    def neighbors_unchanged(self, snapshot: object) -> None:
        self._hit("NEIGHBORS")
        if isinstance(snapshot, dict):
            if (
                set(snapshot) != {
                    "ENTRY", "MAIN_PRIVATE", "NATIVE", "SUB_PRIVATE"
                }
                or any(v != (self.neighbor_pid, 0)
                       for v in snapshot.values())
            ):
                raise RuntimeError("neighbor changed")
        elif snapshot != (self.neighbor_pid, 0):
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

    def test_public_noarg_first_flat_canary_physically_drills_rollback(self) -> None:
        a, j = FakeExit(), FakeJournal()
        with (
            patch("component_release_exit_transition.sys.platform", "linux"),
            patch("component_release_exit_transition.os.geteuid",
                  return_value=0, create=True),
            patch("component_release_exit_transition._check_sealed_controller_root"),
            patch("component_release_exit_transition._JOURNAL_PARENT", ROOT),
            patch("component_release_exit_transition._hold_shared_release_lock",
                  return_value=nullcontext()),
            patch("component_release_exit_transition._protected_real_admission",
                  return_value=(OLD, NEW)),
            patch("component_release_exit_transition._RootAppendFsyncJournal",
                  return_value=j),
            patch("component_release_exit_transition._HostExitUnitIO",
                  return_value=a),
        ):
            result = exit_only_cutover_once()
        self.assertEqual(result["state"], "EXIT_TRANSITION_ACCEPTANCE_PENDING")
        self.assertEqual(a.current, "NEW_ACTIVE")
        self.assertEqual(a.calls.count("START"), 3)
        self.assertEqual(j.events[-2:], ["ACCEPTANCE_PENDING", "CLOSED"])
        self.assertFalse(result["live_deployment_verified"])

    def test_interrupted_new_active_reconciles_exact_old_not_forward_retry(self) -> None:
        a = FakeExit()
        a.current, a.unit = "NEW_ACTIVE", NEW
        baseline = {
            "phase": "BASELINE",
            "neighbors": {
                "ENTRY": [a.neighbor_pid, 0],
                "MAIN_PRIVATE": [a.neighbor_pid, 0],
                "NATIVE": [a.neighbor_pid, 0],
                "SUB_PRIVATE": [a.neighbor_pid, 0],
            },
        }
        history = SimpleNamespace(
            existing_events=lambda: (
                baseline,
                {"phase": "STOP_INTENT"},
                {"phase": "START_INTENT"},
            ),
            record=lambda phase: a.calls.append("LOG_" + phase),
        )
        with (
            patch("component_release_exit_transition._load_exact_exit_units",
                  return_value=(OLD, NEW)),
            patch("component_release_exit_transition._HostExitUnitIO",
                  return_value=a),
            patch("component_release_exit_transition._collect_credential_local_exit_snapshot",
                  return_value={
                      "account_position_counts": {"MAIN": 0, "SUB": 0},
                  }),
        ):
            result = transition._reconcile_interrupted_exit(history)
        self.assertEqual(result["state"], "EXIT_INTERRUPTED_OLD_RECOVERED")
        self.assertTrue(result["rollback_verified"])
        self.assertEqual(a.unit, OLD)
        self.assertEqual(a.current, "OLD_ACTIVE")
        self.assertEqual(a.calls.count("START"), 1)
        self.assertNotIn("VERIFY_NEW", a.calls)

    def test_interrupted_unknown_stops_without_blind_retry(self) -> None:
        a = FakeExit(unknown=True)
        baseline = {
            "phase": "BASELINE",
            "neighbors": {k: [4136853, 0] for k in (
                "ENTRY", "MAIN_PRIVATE", "NATIVE", "SUB_PRIVATE",
            )},
        }
        history = SimpleNamespace(
            existing_events=lambda: (baseline, {"phase": "STOP_INTENT"}),
        )
        with (
            patch("component_release_exit_transition._load_exact_exit_units",
                  return_value=(OLD, NEW)),
            patch("component_release_exit_transition._HostExitUnitIO",
                  return_value=a),
        ):
            result = transition._reconcile_interrupted_exit(history)
        self.assertEqual(result["state"], "EXIT_INTERRUPTED_OUTCOME_UNKNOWN")
        self.assertEqual(a.calls, ["RECONCILE"])

    def test_existing_success_journal_never_issues_second_cutover(self) -> None:
        a = FakeExit()
        a.current, a.unit = "NEW_ACTIVE", NEW
        history = SimpleNamespace(
            existing_events=lambda: (
                {
                    "phase": "BASELINE",
                    "neighbors": {k: [4136853, 0] for k in (
                        "ENTRY", "MAIN_PRIVATE", "NATIVE", "SUB_PRIVATE",
                    )},
                },
                {"phase": "STOP_INTENT"},
                {"phase": "CANARY_OLD_RECOVERED"},
                {"phase": "ACCEPTANCE_PENDING"},
            ),
        )
        with (
            patch("component_release_exit_transition._load_exact_exit_units",
                  return_value=(OLD, NEW)),
            patch("component_release_exit_transition._HostExitUnitIO",
                  return_value=a),
        ):
            result = transition._reconcile_interrupted_exit(history)
        self.assertEqual(
            result["state"], "EXIT_PREVIOUS_CANDIDATE_ACCEPTANCE_PENDING",
        )
        self.assertEqual(a.calls, ["RECONCILE", "VERIFY_NEW", "NEIGHBORS"])
        self.assertEqual(a.unit, NEW)

    def test_real_canary_exercises_old_restart_then_new_second_start(self) -> None:
        a, j = FakeExit(), FakeJournal()
        result = _run_exit_only_transition(
            a, j, old_unit=OLD, new_unit=NEW, exercise_old_rollback=True,
        )
        self.assertEqual(result["state"], "EXIT_TRANSITION_ACCEPTANCE_PENDING")
        self.assertEqual(a.current, "NEW_ACTIVE")
        self.assertEqual(a.unit, NEW)
        self.assertEqual(a.calls.count("STOP"), 3)
        self.assertEqual(a.calls.count("START"), 3)
        self.assertEqual(a.calls.count("UNIT_WRITE"), 3)
        self.assertEqual(a.calls.count("SIGNED_RECOVERY"), 3)
        self.assertLess(
            j.events.index("CANARY_OLD_RECOVERED"),
            j.events.index("CANARY_SECOND_NEW_INTENT"),
        )
        self.assertEqual(j.events[0], "BASELINE")
        self.assertEqual(j.events[-1], "ACCEPTANCE_PENDING")
        for phase in (
            "CANARY_SECOND_NEW_PROCESS_VERIFIED",
            "CANARY_SECOND_NEW_SIGNED_RECOVERY_VERIFIED",
            "CANARY_SECOND_NEW_NEIGHBORS_VERIFIED",
        ):
            self.assertIn(phase, j.events)
        self.assertLess(
            j.events.index("CANARY_SECOND_NEW_PROCESS_VERIFIED"),
            j.events.index("CANARY_SECOND_NEW_SIGNED_RECOVERY_VERIFIED"),
        )
        self.assertFalse(result["runtime_behavior_verified"])
        self.assertFalse(result["financial_mutation_authorized"])

    def test_real_canary_second_new_failure_rolls_back_old(self) -> None:
        a, j = FakeExit(), FakeJournal()
        original = a.verify_new
        attempts = [0]
        def fail_second_candidate() -> None:
            attempts[0] += 1
            if attempts[0] == 2:
                raise RuntimeError("candidate second start fails")
            original()
        a.verify_new = fail_second_candidate
        result = _run_exit_only_transition(
            a, j, old_unit=OLD, new_unit=NEW, exercise_old_rollback=True,
        )
        self.assertEqual(result["state"], "EXIT_OLD_RECOVERED_AFTER_FAILURE")
        self.assertTrue(result["rollback_verified"])
        self.assertEqual(a.current, "OLD_ACTIVE")
        self.assertEqual(a.unit, OLD)
        self.assertEqual(j.events[-1], "ROLLBACK_RECOVERED")
        self.assertNotIn("CANARY_SECOND_NEW_PROCESS_VERIFIED", j.events)

    def test_second_new_signed_failure_records_process_pass_before_rollback(self) -> None:
        a, j = FakeExit(), FakeJournal()
        original = a.verify_signed_and_recovery
        calls = [0]
        def fail_second_signed() -> None:
            calls[0] += 1
            if calls[0] == 3:
                raise RuntimeError("signed postcheck rejected")
            original()
        a.verify_signed_and_recovery = fail_second_signed
        result = _run_exit_only_transition(
            a, j, old_unit=OLD, new_unit=NEW, exercise_old_rollback=True,
        )
        self.assertEqual(result["state"], "EXIT_OLD_RECOVERED_AFTER_FAILURE")
        self.assertTrue(result["rollback_verified"])
        self.assertIn("CANARY_SECOND_NEW_PROCESS_VERIFIED", j.events)
        self.assertNotIn("CANARY_SECOND_NEW_SIGNED_RECOVERY_VERIFIED", j.events)
        self.assertEqual(j.events[-1], "ROLLBACK_RECOVERED")

    def test_baseline_journal_failure_prevents_every_systemd_action(self) -> None:
        a, j = FakeExit(), FakeJournal(fail="BASELINE")
        result = _run_exit_only_transition(
            a, j, old_unit=OLD, new_unit=NEW, exercise_old_rollback=True,
        )
        self.assertEqual(result["state"], "EXIT_PRESTOP_BLOCKED")
        self.assertEqual(a.calls, ["BEFORE"])
        self.assertNotIn("STOP_INTENT", j.events)

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
            with (
                self.subTest(old=len(old), new=len(new)),
                self.assertRaisesRegex(ValueError, "UNIT_INPUT_INVALID"),
            ):
                _run_exit_only_transition(a, j, old_unit=old, new_unit=new)
        self.assertEqual(a.calls, [])

    def test_public_noarg_cutover_fails_closed_no_candidate_before_os_writes(self) -> None:
        self.assertEqual(exit_only_cutover_once.__code__.co_argcount, 0)
        with (
            patch("component_release_exit_transition.sys.platform", "linux"),
            patch("component_release_exit_transition.os.geteuid",
                  return_value=0, create=True),
            patch("component_release_exit_transition._check_sealed_controller_root"),
            patch("component_release_exit_transition._JOURNAL_PARENT", ROOT),
            patch("component_release_exit_transition._hold_shared_release_lock",
                  return_value=nullcontext()),
            patch("component_release_exit_transition._protected_real_admission",
                  side_effect=ValueError("EXIT_TRANSITION_RELEASE_NOT_MERGED")),
            patch("component_release_exit_transition._HostExitUnitIO") as adapter,
            self.assertRaisesRegex(ValueError, "RELEASE_NOT_MERGED"),
        ):
            exit_only_cutover_once()
        adapter.assert_not_called()

    def test_non_linux_public_entrypoint_denies_before_any_actor(self) -> None:
        with (
            patch("component_release_exit_transition.sys.platform", "win32"),
            patch("component_release_exit_transition._HostExitUnitIO") as adapter,
            self.assertRaisesRegex(ValueError, "ROOT_LINUX_REQUIRED"),
        ):
            exit_only_cutover_once()
        adapter.assert_not_called()

    def test_real_signed_child_transport_has_no_shell_and_denies_untrusted(self) -> None:
        program_state = {
            "state": "SIGNED_MAIN_SUB_GET_CONSISTENCY_PASS",
            "position_exit_plan_alignment":
                "SIGNED_POSITION_EXIT_PLAN_DB_ALIGNMENT_PASS",
            "release_authorized": False,
            "financial_mutation_authorized": False,
            "runtime_behavior_verified": False,
            "existing_position_recovery_snapshot": {
                "state": "SIGNED_EXIT_POSITION_SNAPSHOT_READ_ONLY",
            },
        }
        reader = SimpleNamespace(
            returncode=0, stderr="", stdout=json.dumps(program_state),
        )
        with (
            patch("component_release_exit_transition.sys.platform", "linux"),
            patch("component_release_exit_transition.os.geteuid", return_value=0, create=True),
            patch("component_release_exit_transition.subprocess.run",
                  return_value=reader) as run,
        ):
            report = _collect_credential_local_exit_snapshot()
        self.assertEqual(
            report["state"], "SIGNED_EXIT_POSITION_SNAPSHOT_READ_ONLY",
        )
        args, kwargs = run.call_args
        self.assertEqual(args[0][:5], [
            "/usr/sbin/runuser", "-u", "cripta", "--",
            "/srv/cripta/runtime/current/.venv/bin/python",
        ])
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["cwd"], "/srv/cripta/source_checkout")
        self.assertNotIn("sudo", args[0])
        reader.returncode = 1
        with (
            patch("component_release_exit_transition.sys.platform", "linux"),
            patch("component_release_exit_transition.os.geteuid", return_value=0, create=True),
            patch("component_release_exit_transition.subprocess.run",
                  return_value=reader),
            self.assertRaisesRegex(RuntimeError, "SIGNED_READ_BLOCKED"),
        ):
            _collect_credential_local_exit_snapshot()
        with (
            patch("component_release_exit_transition.sys.platform", "linux"),
            patch("component_release_exit_transition.os.geteuid", return_value=1000, create=True),
            self.assertRaisesRegex(ValueError, "ROOT_LINUX_REQUIRED"),
        ):
            _collect_credential_local_exit_snapshot()

    def test_flat_consumer_heartbeat_requires_new_start_and_safe_source(self) -> None:
        now = datetime(2026, 11, 23, 14, 0, 10, tzinfo=UTC)
        status = {
            "service": "p90_r06_exit_owner",
            "state": "P90_R06_EXIT_OWNER_READY_FLAT",
            "result": "P90_R06_EXIT_OWNER_READY_FLAT",
            "release_commit": _SOURCE,
            "p90_r06_exit_owner": "ENABLED",
            "generic_consumer": "DISABLED",
            "observed_at": (now - timedelta(seconds=2)).isoformat(),
            "error": None,
        }
        _validate_flat_exit_status(
            status, observed_at=now, not_before=now - timedelta(seconds=5),
        )
        with self.assertRaisesRegex(RuntimeError, "CLOCK_UNTRUSTED"):
            _validate_flat_exit_status(
                status, observed_at=None, not_before=None,
            )
        for name, change in (
            ("stale", {"observed_at":
                       (now - timedelta(seconds=25)).isoformat()}),
            ("old-process", {"observed_at":
                             (now - timedelta(seconds=4)).isoformat()}),
            ("bad-commit", {"release_commit": "f" * 40}),
            ("false-ready", {"state": "P90_R06_EXIT_OWNER_RUNNING"}),
            ("error", {"error": "P90_R06_EXIT_SOURCE_DRIFT"}),
            ("legacy-enable", {"generic_consumer": "ENABLED"}),
        ):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                _validate_flat_exit_status(
                    {**status, **change},
                    observed_at=now, not_before=now - timedelta(seconds=3),
                )

    def test_host_post_restart_requires_same_signed_main_sub_and_new_ready(self) -> None:
        now = datetime.now(UTC)
        before = {
            "state": "SIGNED_EXIT_POSITION_SNAPSHOT_READ_ONLY",
            "observed_at": (now - timedelta(seconds=4)).isoformat(),
            "account_position_counts": {"MAIN": 0, "SUB": 0},
            "account_position_identity_and_protection_sha256": {
                "MAIN": "a" * 64, "SUB": "b" * 64,
            },
            "immutable_exit_plan_lineage_checked": True,
            "release_authorized": False, "rollback_ready": False,
            "runtime_behavior_verified": False,
            "financial_mutation_authorized": False,
        }
        after = dict(
            before, observed_at=(now - timedelta(seconds=1)).isoformat(),
        )
        status = {
            "service": "p90_r06_exit_owner",
            "state": "P90_R06_EXIT_OWNER_READY_FLAT",
            "result": "P90_R06_EXIT_OWNER_READY_FLAT",
            "release_commit": _SOURCE,
            "p90_r06_exit_owner": "ENABLED",
            "generic_consumer": "DISABLED",
            "observed_at": now.isoformat(),
            "error": None,
        }
        adapter = object.__new__(_HostExitUnitIO)
        adapter._pre_signed_snapshot = before
        adapter._restart_started_at = now - timedelta(seconds=3)
        with (
            patch("component_release_exit_transition._collect_credential_local_exit_snapshot",
                  return_value=after),
            patch("component_release_exit_transition._read_protected_exit_status",
                  return_value=status),
            patch("component_release_exit_transition._check_flat_new_entry_inhibited"),
        ):
            adapter.verify_signed_and_recovery()
        drift = dict(
            after,
            account_position_identity_and_protection_sha256={
                "MAIN": "c" * 64, "SUB": "b" * 64,
            },
        )
        with (
            patch("component_release_exit_transition._collect_credential_local_exit_snapshot",
                  return_value=drift),
            patch("component_release_exit_transition._check_flat_new_entry_inhibited"),
            self.assertRaisesRegex(ValueError, "PROTECTION_DRIFT"),
        ):
            adapter.verify_signed_and_recovery()
        adapter._pre_signed_snapshot = None
        with self.assertRaisesRegex(RuntimeError, "BASELINE_MISSING"):
            adapter.verify_signed_and_recovery()

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
