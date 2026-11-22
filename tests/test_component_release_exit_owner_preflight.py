"""#849 root-only Exit pre-cutover owner contract.

All fixtures are read-only in memory. This module never invokes systemd,
Exchange, PostgreSQL, or writes a production path during tests.
"""
from __future__ import annotations

import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

from component_release_exit_owner_preflight import (  # noqa: E402
    _EXIT_UNIT,
    _OLD_COMMIT,
    _ORIGINAL_UNIT_SHA256,
    _read_unit_state,
    collect_exit_only_owner_preflight_once,
)

ENTRY = "cripta-universal-entry-consumer.service"
PRIVATE = "cripta-private-runtime.service"
NATIVE = "cripta-native-public-trade-source.service"
SUB = "cripta-private-runtime@ta-" + "a" * 32 + ".service"
BASE = {
    "ENTRY": (4136853, 0),
    "EXIT": (4136855, 0),
    "MAIN_PRIVATE": (4136850, 0),
    "NATIVE": (378530, 3),
    "SUB_PRIVATE": (4136862, 0),
}
OLD_READBACK = {
    "state": "OLD_EXIT_RECOVERY_STAGED_READBACK_PASS",
    "source_commit": _OLD_COMMIT,
    "actual_host_attested": True,
    "pinned_unit_systemd_syntax_verified": True,
    "rollback_ready": False,
    "runtime_behavior_verified": False,
    "signed_main_sub_verified": False,
    "install_authorized": False,
}


class ExitPrecutoverReadbackTests(unittest.TestCase):
    def test_noarg_owner_rejects_non_root_before_any_subprocess(self) -> None:
        self.assertEqual(collect_exit_only_owner_preflight_once.__code__.co_argcount, 0)
        with (
            patch("component_release_exit_owner_preflight.sys.platform", "linux"),
            patch("component_release_exit_owner_preflight.os.geteuid", return_value=107, create=True),
            patch("component_release_exit_owner_preflight._snapshot_units") as scan,
            self.assertRaisesRegex(ValueError, "ROOT_REQUIRED"),
        ):
            collect_exit_only_owner_preflight_once()
        scan.assert_not_called()

    def _fixtures(self, **changes: object) -> dict[str, object]:
        d: dict[str, object] = {
            "root_state": "ROOT_TREE_PREPARED_METADATA_ONLY",
            "old": OLD_READBACK.copy(),
            "unit_hash": _ORIGINAL_UNIT_SHA256,
            "release_names": (),
            "snapshot_before": BASE.copy(),
            "snapshot_after": BASE.copy(),
            "sub_units": (SUB,),
        }
        d.update(changes)
        return d

    def collect(self, data: dict[str, object]) -> dict[str, object]:
        with (
            patch("component_release_exit_owner_preflight.sys.platform", "linux"),
            patch("component_release_exit_owner_preflight.os.geteuid",
                  return_value=0, create=True),
            patch("component_release_exit_owner_preflight._hold_shared_release_lock",
                  return_value=nullcontext()),
            patch("component_release_exit_owner_preflight.inspect_exit_root_tree",
                  return_value={"state": data["root_state"]}),
            patch("component_release_exit_owner_preflight.attest_old_exit_candidate_on_host",
                  return_value=data["old"]),
            patch("component_release_exit_owner_preflight._installed_unit_sha256",
                  return_value=data["unit_hash"]),
            patch("component_release_exit_owner_preflight._candidate_entries",
                  return_value=data["release_names"]),
            patch("component_release_exit_owner_preflight._sub_private_units",
                  return_value=data["sub_units"]),
            patch("component_release_exit_owner_preflight._snapshot_units",
                  side_effect=[data["snapshot_before"], data["snapshot_after"]]),
        ):
            return collect_exit_only_owner_preflight_once()

    def test_real_os_shape_yields_staged_only_never_release_authority(self) -> None:
        report = self.collect(self._fixtures())
        self.assertEqual(report["state"], "EXIT_PRECUTOVER_NO_NEW_COMPONENT_STAGED")
        self.assertEqual(report["component_id"], "EXIT")
        self.assertEqual(report["old_commit"], _OLD_COMMIT)
        self.assertEqual(report["affected_unit"], _EXIT_UNIT)
        self.assertEqual(report["old_exit_pid"], 4136855)
        self.assertEqual(report["untouched_unit_count"], 4)
        self.assertEqual(report["old_unit_sha256"], _ORIGINAL_UNIT_SHA256)
        for key in (
            "install_authorized", "cutover_authorized", "rollback_ready",
            "runtime_behavior_verified", "signed_main_sub_verified",
            "financial_mutation_authorized", "systemd_mutation_performed",
            "new_component_loaded", "new_component_installed",
        ):
            self.assertIs(report[key], False, key)

    def test_bad_root_old_package_or_original_unit_rejected(self) -> None:
        for option, data in (
            ("root_absent", self._fixtures(root_state="ROOT_TREE_PARTIAL_BLOCKED")),
            ("old_flag", self._fixtures(old={**OLD_READBACK, "rollback_ready": True})),
            ("old_not_attested", self._fixtures(old={**OLD_READBACK,
                                                   "actual_host_attested": False})),
            ("old_other", self._fixtures(old={**OLD_READBACK,
                                             "source_commit": "a" * 40})),
            ("unit_modified", self._fixtures(unit_hash="f" * 64)),
        ):
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.collect(data)

    def test_unknown_or_nonempty_new_release_is_not_auto_selected(self) -> None:
        for names in (
            ("a" * 40,),
            (".stage-" + "a" * 40,),
            ("../escape",),
        ):
            with self.subTest(names=names), self.assertRaisesRegex(
                ValueError, "CANDIDATE_NOT_REVIEWED",
            ):
                self.collect(self._fixtures(release_names=names))

    def test_missing_duplicate_sub_or_neighbor_changes_fail_closed(self) -> None:
        for units in ((), (SUB, SUB), (SUB, ENTRY)):
            with self.subTest(units=units), self.assertRaises(ValueError):
                self.collect(self._fixtures(sub_units=units))
        changed = BASE.copy()
        changed["ENTRY"] = (4136854, 1)
        with self.assertRaisesRegex(ValueError, "NEIGHBOR_CHANGED"):
            self.collect(self._fixtures(snapshot_after=changed))
        changed = BASE.copy()
        changed["EXIT"] = (4136890, 1)
        with self.assertRaisesRegex(ValueError, "NEIGHBOR_CHANGED"):
            self.collect(self._fixtures(snapshot_after=changed))

    def test_failed_acquire_lock_must_precede_any_readback(self) -> None:
        with (
            patch("component_release_exit_owner_preflight.sys.platform", "linux"),
            patch("component_release_exit_owner_preflight.os.geteuid",
                  return_value=0, create=True),
            patch("component_release_exit_owner_preflight._hold_shared_release_lock",
                  side_effect=ValueError("EXIT_ROOT_RELEASE_LOCK_BUSY")),
            patch("component_release_exit_owner_preflight.inspect_exit_root_tree") as inspect,
            self.assertRaisesRegex(ValueError, "LOCK_BUSY"),
        ):
            collect_exit_only_owner_preflight_once()
        inspect.assert_not_called()

    def test_bad_systemd_show_or_stale_service_is_rejected(self) -> None:
        values = (
            "Id=" + _EXIT_UNIT,
            "ActiveState=active",
            "SubState=running",
            "MainPID=4136855",
            "NRestarts=0",
            "NeedDaemonReload=no",
            "FragmentPath=/etc/systemd/system/cripta-universal-exit-consumer.service",
        )
        raw = "\n".join(values) + "\n"
        self.assertEqual(_read_unit_state(raw, _EXIT_UNIT), (4136855, 0))
        for invalid in (
            raw.replace("ActiveState=active", "ActiveState=failed"),
            raw.replace("MainPID=4136855", "MainPID=0"),
            raw.replace("NRestarts=0", "NRestarts=-1"),
            raw.replace("NeedDaemonReload=no", "NeedDaemonReload=yes"),
            raw.replace("FragmentPath=/etc", "FragmentPath=/tmp"),
            raw + "MainPID=10\n",
            raw.replace("Id=" + _EXIT_UNIT, "Id=cripta-universal-entry-consumer.service"),
        ):
            with self.subTest(invalid=invalid[:100]), self.assertRaises(ValueError):
                _read_unit_state(invalid, _EXIT_UNIT)

    def test_non_linux_platform_denied_before_unix_actor(self) -> None:
        with (
            patch("component_release_exit_owner_preflight.sys.platform", "win32"),
            self.assertRaisesRegex(ValueError, "LINUX_ONLY"),
        ):
            collect_exit_only_owner_preflight_once()

    def test_source_contains_no_service_mutation_executor(self) -> None:
        path = ROOT / "operations/infrastructure/component_release_exit_owner_preflight.py"
        src = path.read_text(encoding="utf-8")
        for forbidden in (
            '"stop"', '"restart"', '"daemon-reload"',
            '"start"', 'os.replace(', 'os.rename(', 'subprocess.Popen(',
            'psycopg.connect(', 'Bybit', 'trading_api',
        ):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
