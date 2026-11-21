"""#849 legacy Exit rollback INPUT inventory; no protected backup is created.

Synthetic fixtures are disposable under the cripta-owned testenv only.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

from component_release_exit_rollback_inventory import (  # noqa: E402
    _audit_legacy_exit_input,
    inspect_previous_exit_rollback_inputs,
)

SHA = "8" * 40
SOURCE_UNIT = ROOT / "operations/systemd/cripta-universal-exit-consumer.service"
_LINKS = {
    ".venv/bin/python": "python3",
    ".venv/bin/python3": "/usr/bin/python3",
    ".venv/bin/python3.12": "python3",
    ".venv/lib64": "lib",
}


@unittest.skipUnless(os.name == "posix", "Linux-only read-only filesystem proof")
class LegacyExitRollbackInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.release = self.parent / SHA
        self.release.mkdir(mode=0o750)
        os.chmod(self.release, 0o750)
        self.uid = os.geteuid()
        self.unit = self.parent / "legacy-exit.service"
        self.unit.write_bytes(SOURCE_UNIT.read_bytes())
        os.chmod(self.unit, 0o644)
        (self.release / "INSTALLED_COMMIT").write_text(SHA + "\n", encoding="ascii")
        items = (
            "src/bybit_workbench/universal_exit/p90l90_real_v3.py",
            "operations/connectivity/universal_exit_consumer.py",
            ".venv/pyvenv.cfg",
            ".venv/lib/python3.12/site-packages/example.py",
        )
        for rel in items:
            dest = self.release / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(rel.encode("ascii") + b"\n")
        (self.release / ".venv/bin").mkdir(parents=True, exist_ok=True)
        for rel, target in _LINKS.items():
            (self.release / rel).symlink_to(target)

    def audit(self) -> dict[str, object]:
        return _audit_legacy_exit_input(
            release=self.release,
            unit=self.unit,
            expected_sha=SHA,
            release_owner_uid=self.uid,
            unit_owner_uid=self.uid,
        )

    def test_consistent_input_is_explicitly_not_rollback_or_release(self) -> None:
        result = self.audit()
        self.assertEqual(result["state"], "LEGACY_ROLLBACK_INPUT_ONLY_UNPROTECTED")
        self.assertEqual(result["previous_commit"], SHA)
        self.assertEqual(result["unit_sha256"], (
            "c4a21004e637df794670d7d096c6ffa443c23a881c662b65bebfc11459433248"
        ))
        self.assertEqual(result["symlink_count"], 4)
        self.assertGreaterEqual(result["regular_file_count"], 4)
        self.assertEqual(len(result["input_tree_sha256"]), 64)
        self.assertTrue(result["legacy_release_mutable_by_runtime_actor"])
        for flag in (
            "immutable_rollback_ready", "previous_python_isolated",
            "previous_dependency_abi_attested", "previous_unit_pinned",
            "install_authorized", "runtime_behavior_verified",
            "signed_exchange_protection_verified", "financial_mutation_authorized",
        ):
            with self.subTest(flag=flag):
                self.assertIs(result[flag], False)
        self.assertNotIn("unit_text", result)
        self.assertNotIn("environment", result)

    def test_inventory_fingerprint_changes_with_a_file_without_writing_it(self) -> None:
        before = self.audit()
        entry = self.release / "operations/connectivity/universal_exit_consumer.py"
        original = entry.read_bytes()
        entry.write_bytes(original + b"changed\n")
        after = self.audit()
        self.assertNotEqual(before["input_tree_sha256"], after["input_tree_sha256"])
        self.assertEqual(entry.read_bytes(), original + b"changed\n")

    def test_marker_wrong_sha_or_root_name_denied(self) -> None:
        marker = self.release / "INSTALLED_COMMIT"
        marker.write_text("a" * 40 + "\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "INSTALLED_COMMIT"):
            self.audit()
        marker.write_text(SHA + "\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "BASELINE_IDENTITY"):
            _audit_legacy_exit_input(
                release=self.release,
                unit=self.unit,
                expected_sha="a" * 40,
                release_owner_uid=self.uid,
                unit_owner_uid=self.uid,
            )

    def test_source_missing_or_unexpected_symlink_denied(self) -> None:
        entry = self.release / "operations/connectivity/universal_exit_consumer.py"
        entry.unlink()
        with self.assertRaises(ValueError):
            self.audit()
        entry.symlink_to("/etc/passwd")
        with self.assertRaises(ValueError):
            self.audit()

    def test_venv_symlink_mismatch_and_extra_symlink_denied(self) -> None:
        link = self.release / ".venv/bin/python3"
        link.unlink()
        link.symlink_to("/tmp/untrusted-python")
        with self.assertRaisesRegex(ValueError, "SYMLINK"):
            self.audit()
        link.unlink()
        link.symlink_to("/usr/bin/python3")
        (self.release / ".venv/other_link").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "SYMLINK"):
            self.audit()

    def test_missing_unit_or_tamper_fail_without_repair(self) -> None:
        self.unit.write_text("unexpected", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "UNIT"):
            self.audit()
        self.unit.unlink()
        with self.assertRaises(ValueError):
            self.audit()

    def test_untrusted_release_owner_or_mode_denied(self) -> None:
        os.chmod(self.release, 0o777)
        with self.assertRaises(ValueError):
            self.audit()
        os.chmod(self.release, 0o750)
        with self.assertRaises(ValueError):
            _audit_legacy_exit_input(
                release=self.release,
                unit=self.unit,
                expected_sha=SHA,
                release_owner_uid=0 if self.uid != 0 else 107,
                unit_owner_uid=self.uid,
            )

    def test_untrusted_unit_owner_or_mode_denied(self) -> None:
        os.chmod(self.unit, 0o666)
        with self.assertRaises(ValueError):
            self.audit()
        os.chmod(self.unit, 0o644)
        with self.assertRaises(ValueError):
            _audit_legacy_exit_input(
                release=self.release, unit=self.unit,
                expected_sha=SHA,
                release_owner_uid=self.uid, unit_owner_uid=0 if self.uid != 0 else 107,
            )

    def test_symlinked_release_or_unit_is_denied(self) -> None:
        link = self.parent / ("a" * 40)
        link.symlink_to(self.release, target_is_directory=True)
        with self.assertRaises(ValueError):
            _audit_legacy_exit_input(
                release=link, unit=self.unit, expected_sha="a" * 40,
                release_owner_uid=self.uid, unit_owner_uid=self.uid,
            )
        original = self.unit.read_bytes()
        self.unit.unlink()
        source = self.parent / "other.service"
        source.write_bytes(original)
        self.unit.symlink_to(source)
        with self.assertRaises(ValueError):
            self.audit()

    def test_unknown_special_file_denied(self) -> None:
        import subprocess

        fifo = self.release / "src/surprise.pipe"
        subprocess.run(["mkfifo", str(fifo)], check=True)
        try:
            with self.assertRaisesRegex(ValueError, "SPECIAL"):
                self.audit()
        finally:
            fifo.unlink()

    def test_unprivileged_published_wrapper_requires_real_baseline(self) -> None:
        # Real-host function is READ ONLY. Never calls root installer.
        self.assertEqual(inspect_previous_exit_rollback_inputs.__code__.co_argcount, 0)
        self.assertNotIn("immutable_rollback_ready", self.unit.read_text())


if __name__ == "__main__":
    unittest.main()
