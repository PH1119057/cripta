"""#849 immutable Exit-only cutover + rollback OFFLINE PLAN contracts.

No systemd, GitHub auth, DB, Exchange or production filesystem mutation.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

from component_release_exit_cutover import build_exit_cutover_preview  # noqa: E402

NEW = "a" * 40
OLD = "b" * 40
DIGEST = "c" * 64
EXIT = "cripta-universal-exit-consumer.service"


def fixture() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    candidate: dict[str, object] = {
        "component_id": "EXIT",
        "unit_instance": EXIT,
        "source_commit": NEW,
        "github_main_commit": NEW,
        "merged_scoped_pr": True,
        "scope_check": "PASS",
        "quality_check": "PASS",
        "component_root": "/srv/cripta/components/exit/releases/" + NEW,
        "component_lock_sha256": DIGEST,
        "bundle_sha256": DIGEST,
        "runtime_dependency_abi_attested": True,
        "protected_parent_host_attested": True,
        "source_commit_registered": True,
    }
    rollback: dict[str, object] = {
        "previous_commit": OLD,
        "backup_root": "/srv/cripta/components/exit/rollback/" + OLD,
        "backup_bundle_sha256": DIGEST,
        "backup_unit_sha256": DIGEST,
        "backup_root_host_attested": True,
        "previous_unit_bytes_verified": True,
        "previous_runtime_recoverable": True,
    }
    observed: dict[str, object] = {
        "old_exit_pid": 101,
        "old_exit_start_ticks": 1200,
        "release_lock_available": True,
        "prior_mutation_outcome": "KNOWN_IDLE",
        "open_position_count": 1,
        "exchange_protection": {"MAIN": "PASS", "SUB": "PASS"},
        "source_compatibility": "PASS",
        "neighbor_before": {
            "ENTRY": (102, 0),
            "PRIVATE_RUNTIME": (103, 0),
            "MARKET_SOURCE": (104, 0),
            "DASHBOARD": (105, 0),
        },
    }
    return candidate, rollback, observed


class ExitOnlyCutoverPreviewTests(unittest.TestCase):
    def test_coherent_open_position_does_not_block_but_cannot_authorize(self) -> None:
        candidate, rollback, observed = fixture()
        plan = build_exit_cutover_preview(
            candidate=candidate, rollback=rollback, observed=observed,
        )
        self.assertEqual(plan["state"], "OFFLINE_EXIT_CUTOVER_CONSISTENCY_ONLY")
        self.assertEqual(plan["component_id"], "EXIT")
        self.assertFalse(plan["install_authorized"])
        self.assertFalse(plan["runtime_behavior_verified"])
        self.assertFalse(plan["exchange_protection_independently_verified"])
        self.assertFalse(plan["actual_actor_authorized"])
        self.assertEqual(plan["affected_units"], [EXIT])
        self.assertEqual(plan["source_commit"], NEW)
        self.assertEqual(plan["rollback_commit"], OLD)
        self.assertEqual(plan["planned_open_positions"], 1)
        self.assertNotIn("ENTRY", plan["affected_units"])
        self.assertNotIn("PRIVATE_RUNTIME", plan["affected_units"])
        self.assertNotIn("MARKET_SOURCE", plan["affected_units"])
        self.assertNotIn("DASHBOARD", plan["affected_units"])
        self.assertIn("ROLLBACK_EXIT_ONLY_ON_FAILED_POSTCHECK", plan["steps"])

    def test_wrong_candidate_identity_scope_or_git_gate_denied(self) -> None:
        mutations = (
            ("component_id", "ENTRY"), ("unit_instance", "other.service"),
            ("source_commit", OLD), ("github_main_commit", OLD),
            ("merged_scoped_pr", False), ("scope_check", "FAIL"),
            ("quality_check", "IN_PROGRESS"),
            ("component_root", "/srv/cripta/runtime/components/exit/releases/" + NEW),
            ("component_root", "/srv/cripta/components/exit/releases/" + OLD),
            ("component_lock_sha256", "unknown"), ("bundle_sha256", "bad"),
            ("runtime_dependency_abi_attested", False),
            ("protected_parent_host_attested", False),
            ("source_commit_registered", False),
        )
        for key, value in mutations:
            candidate, rollback, observed = fixture()
            candidate[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                build_exit_cutover_preview(
                    candidate=candidate, rollback=rollback, observed=observed,
                )

    def test_missing_immutable_rollback_or_wrong_previous_denied(self) -> None:
        mutations = (
            ("previous_commit", NEW),
            ("previous_commit", "../etc"),
            ("backup_root", "/srv/cripta/runtime/releases/" + OLD),
            ("backup_root", "/srv/cripta/components/exit/rollback/" + NEW),
            ("backup_bundle_sha256", "bad"),
            ("backup_unit_sha256", "bad"),
            ("backup_root_host_attested", False),
            ("previous_unit_bytes_verified", False),
            ("previous_runtime_recoverable", False),
        )
        for key, value in mutations:
            candidate, rollback, observed = fixture()
            rollback[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_exit_cutover_preview(
                    candidate=candidate, rollback=rollback, observed=observed,
                )

    def test_missing_signed_protection_does_not_rewrite_financial_state(self) -> None:
        for change in ("MAIN", "SUB", "missing", "unknown"):
            candidate, rollback, observed = fixture()
            if change in ("MAIN", "SUB"):
                protected = observed["exchange_protection"]
                assert isinstance(protected, dict)
                protected[change] = "UNKNOWN"
            elif change == "missing":
                del observed["exchange_protection"]
            else:
                observed["source_compatibility"] = "UNKNOWN"
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_exit_cutover_preview(
                    candidate=candidate, rollback=rollback, observed=observed,
                )

    def test_unknown_prior_mutation_outcome_and_lock_collision_denied(self) -> None:
        for key, value in (
            ("prior_mutation_outcome", "UNKNOWN"),
            ("prior_mutation_outcome", "FAILED"),
            ("release_lock_available", False),
            ("old_exit_pid", 0),
            ("old_exit_start_ticks", 0),
            ("open_position_count", -1),
        ):
            candidate, rollback, observed = fixture()
            observed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_exit_cutover_preview(
                    candidate=candidate, rollback=rollback, observed=observed,
                )

    def test_neighbors_must_be_an_exact_frozen_snapshot(self) -> None:
        for change in ("missing", "pid", "restart", "unknown"):
            candidate, rollback, observed = fixture()
            before = observed["neighbor_before"]
            assert isinstance(before, dict)
            if change == "missing":
                del before["ENTRY"]
            elif change == "pid":
                before["ENTRY"] = (0, 0)
            elif change == "restart":
                before["PRIVATE_RUNTIME"] = (103, -1)
            else:
                before["GATEWAY_OTHER"] = (106, 0)
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_exit_cutover_preview(
                    candidate=candidate, rollback=rollback, observed=observed,
                )

    def test_missing_any_required_evidence_fails_closed(self) -> None:
        candidate, rollback, observed = fixture()
        for which, field in (
            ("candidate", "source_commit"),
            ("candidate", "component_root"),
            ("rollback", "backup_unit_sha256"),
            ("rollback", "previous_runtime_recoverable"),
            ("observed", "prior_mutation_outcome"),
            ("observed", "exchange_protection"),
            ("observed", "neighbor_before"),
        ):
            c, r, o = copy.deepcopy((candidate, rollback, observed))
            target = {"candidate": c, "rollback": r, "observed": o}[which]
            del target[field]
            with self.subTest(which=which, field=field), self.assertRaises(ValueError):
                build_exit_cutover_preview(candidate=c, rollback=r, observed=o)


if __name__ == "__main__":
    unittest.main()
