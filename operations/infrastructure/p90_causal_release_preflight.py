#!/usr/bin/env python3
"""Bounded #865 candidate-only P90 release preflight, no network/DB/credentials.

The verified release installer invokes this BEFORE stopping CORE services,
running production SQL migrations or switching runtime/current. The runner
does not replace signed post-deploy or separately owner-authorized LIVE proof.
"""
from __future__ import annotations

import argparse
import re
import sys
import unittest
from pathlib import Path

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_TEST_MODULE = "test_p90_causal_release_preflight_865.py"

_REQUIRED_CASES = frozenset({
    "CandidateGraphTests.test_all_cross_owner_rails_exist_and_are_parseable",
    "CandidateGraphTests.test_exit_consumer_orders_three_owners_and_does_not_post",
    "CandidateGraphTests.test_gateway_and_guard_consume_exit_proof",
    "CandidateGraphTests.test_installer_packages_and_verifies_candidate_tests_before_cutover",
    "CandidateGraphTests.test_offline_candidate_rehearsal_preserves_live_ownership",
    "CandidateGraphTests.test_original_ci_pg_schema_blocks_before_live_cutover",
    "CausalSimulatedTests.test_already_armed_native_trailing_does_not_duplicate",
    "CausalSimulatedTests.test_firstfill_window_can_expire_but_opted_in_recovery_reaches_decision",
    "CausalSimulatedTests.test_gateway_exact_ready_flat_is_not_confused_with_recovery_failures",
    "CausalSimulatedTests.test_signed_private_rotation_is_not_protection_drift",
    "CausalSimulatedTests.test_stale_or_wrong_private_source_and_unsafe_position_fail_closed",
})


def _loaded_case_ids(suite: unittest.TestSuite) -> list[str]:
    result: list[str] = []
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            result.extend(_loaded_case_ids(case))
        else:
            result.append(".".join(case.id().split(".")[-2:]))
    return result


def _stop(code: str) -> int:
    print("P90_CANDIDATE_PREFLIGHT=FAIL reason=" + code, flush=True)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--expected-commit", required=True)
    # Explicit offline-only Data-SSD candidate; never used by production installer.
    parser.add_argument("--staged-candidate", action="store_true")
    args = parser.parse_args(argv)

    if not _SHA40.fullmatch(args.expected_commit):
        return _stop("EXPECTED_COMMIT_INVALID")
    # Never follow runtime/current or an arbitrary borrowed root.
    base = (
        Path("/data/cripta/research/testenvs/p90_causal_candidate_865")
        if args.staged_candidate
        else Path("/srv/cripta/runtime/releases")
    )
    specified = Path(args.candidate_root)
    expected = base / args.expected_commit
    if not specified.is_absolute() or specified != expected:
        return _stop("CANDIDATE_ROOT_SCOPE_INVALID")
    if not expected.is_dir() or expected.is_symlink():
        return _stop("CANDIDATE_DIR_MISSING_OR_SYMLINK")
    if not (expected / "INSTALLED_COMMIT").is_file():
        return _stop("CANDIDATE_IDENTITY_MARKER_MISSING")
    if (expected / "INSTALLED_COMMIT").read_text().strip() != args.expected_commit:
        return _stop("CANDIDATE_IDENTITY_MISMATCH")
    current = Path("/srv/cripta/runtime/current")
    if current.is_symlink() and current.resolve() == expected.resolve():
        return _stop("CANDIDATE_ALREADY_CURRENT")
    if Path(__file__).resolve() != (expected / "operations/infrastructure/p90_causal_release_preflight.py").resolve():
        return _stop("RUNNER_IS_NOT_CANDIDATE_SOURCE")

    test_path = expected / "tests" / _TEST_MODULE
    if not test_path.is_file() or test_path.is_symlink():
        return _stop("CANDIDATE_CAUSAL_TEST_MISSING")
    sys.dont_write_bytecode = True
    # Read only from exact new candidate; never import runtime/current.
    sys.path[:0] = [str(expected), str(expected / "src")]
    print("P90_CANDIDATE_SHA=" + args.expected_commit, flush=True)
    print("C0_CANDIDATE_GRAPH=RUNNING C1_CAUSAL_SIMULATED=RUNNING", flush=True)
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(expected / "tests"),
        pattern=_TEST_MODULE,
    )
    discovered = _loaded_case_ids(suite)
    if len(discovered) != len(_REQUIRED_CASES) or set(discovered) != _REQUIRED_CASES:
        return _stop(
            "C0_C1_TEST_INVENTORY_MISMATCH:discovered=" + str(len(discovered))
        )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if (
        not result.wasSuccessful()
        or result.testsRun != len(_REQUIRED_CASES)
        or result.skipped
    ):
        return _stop(
            f"C0_OR_C1_FAILED:tests={result.testsRun}:failures={len(result.failures)}"
            f":errors={len(result.errors)}:skipped={len(result.skipped)}"
        )
    print(f"C0_CANDIDATE_GRAPH=PASS C1_CAUSAL_SIMULATED=PASS tests={result.testsRun}")
    # #865: actual independent PG16 source/lock/grants tests, NEVER the
    # production PostgreSQL socket. This is a bounded C2 subset: private
    # IPC and real Exchange proof remain separately unexercised.
    from operations.infrastructure.p90_causal_pg16_testenv import run_c2

    if not run_c2(
        expected,
        args.expected_commit,
        candidate_archive=args.staged_candidate,
    ):
        return _stop("C2_PG16_SOURCE_LOCK_ROLE_FAILED")

    # Same physically isolated PG16 cluster, independent original-schema check:
    # actual 66 SQL migrations from the canonical GitHub CI workflow and
    # original private_runtime prestart-recovery query under test role cripta.
    from operations.infrastructure.p90_causal_fullschema_865 import run_full_schema

    if not run_full_schema(
        expected,
        args.expected_commit,
        candidate_archive=args.staged_candidate,
    ):
        return _stop("C2_FULL_SCHEMA_CI_MIGRATION_OR_SQL_FAILED")
    print("C3_SIGNED_REAL_EXCHANGE=NOT_EXERCISED", flush=True)
    print(
        "P90_CANDIDATE_PREFLIGHT="
        + ("PASS_OFFLINE_ARCHIVE_C0_C1_C2_SQL" if args.staged_candidate
           else "PASS_RELEASE_C0_C1_C2_SQL"),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
