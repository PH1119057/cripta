"""#849: regression-first previous Exit protected rollback PREPARATION.

Fixtures are disposable. No production filesystem/systemd/DB/Exchange writes
are exercised by this test module.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

from component_release_exit_legacy_prepare import (  # noqa: E402
    OLD_EXIT_COMMIT,
    _fingerprint_venv,
    _payload_digest,
    _resume_legacy_fixture,
    _prepare_legacy_fixture,
    _verify_legacy_fixture,
    build_pinned_old_exit_unit,
    prepare_legacy_exit_rollback_once,
    finalize_reconciled_partial_legacy_exit_once,
)

ORIGINAL_UNIT = ROOT / "operations/systemd/cripta-universal-exit-consumer.service"
_LINKS = {
    "bin/python": "python3",
    "bin/python3": "/usr/bin/python3",
    "bin/python3.12": "python3",
    "lib64": "lib",
}


@unittest.skipUnless(os.name == "posix", "Linux-only immutable rollback preparation")
class PreviousExitRollbackPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.owner = os.geteuid()
        self.group = os.getegid()
        self.rollback = self.base / "rollback"
        self.rollback.mkdir(mode=0o750)
        os.chmod(self.rollback, 0o750)
        self.input = self.base / "input"
        self.input.mkdir(mode=0o750)
        os.chmod(self.input, 0o750)
        self.archive = self.input / "legacy_runtime.tar"
        self.old_unit = self.input / "legacy_unit.service"
        self.old_unit.write_bytes(ORIGINAL_UNIT.read_bytes())
        os.chmod(self.old_unit, 0o640)
        self.venv = self.base / "testvenv"
        for name in ("bin", "lib/python3.12/site-packages"):
            (self.venv / name).mkdir(parents=True, exist_ok=True)
        (self.venv / "pyvenv.cfg").write_text(
            "home = /usr/bin\ninclude-system-site-packages = false\n"
            "version = 3.12.3\n", encoding="ascii",
        )
        (self.venv / "lib/python3.12/site-packages/example.py").write_text(
            "VALUE = 1\n", encoding="ascii",
        )
        for name, target in _LINKS.items():
            (self.venv / name).symlink_to(target)
        self.lock = self.base / "install.lock"
        self.lock.write_bytes(b"")
        os.chmod(self.lock, 0o644)
        self.write_archive()

    def write_archive(self, *, wrong_path: str | None = None) -> None:
        entries = {
            "INSTALLED_COMMIT": (OLD_EXIT_COMMIT + "\n").encode("ascii"),
            "src/bybit_workbench/__init__.py": b"__version__ = 'old'\n",
            "operations/connectivity/universal_exit_consumer.py": b"print('never executed')\n",
        }
        with tarfile.open(self.archive, mode="w") as tar:
            for name, contents in entries.items():
                if wrong_path and name.startswith("src/"):
                    name = wrong_path
                row = tarfile.TarInfo(name)
                row.mode = 0o644
                row.size = len(contents)
                tar.addfile(row, io.BytesIO(contents))
        os.chmod(self.archive, 0o640)

    def args(self) -> dict[str, object]:
        return {
            "archive": self.archive,
            "source_unit": self.old_unit,
            "isolated_venv": self.venv,
            "rollback_parent": self.rollback,
            "lock_path": self.lock,
            "expected_archive_sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            "expected_venv_fingerprint": _fingerprint_venv(self.venv)["fingerprint"],
            "owner_uid": self.owner,
            "service_gid": self.group,
            "lock_gid": self.group,
        }

    def test_pinned_old_unit_preserves_sandbox_and_changes_only_exit_path(self) -> None:
        unit = build_pinned_old_exit_unit(ORIGINAL_UNIT.read_bytes())
        root = f"/srv/cripta/components/exit/rollback/{OLD_EXIT_COMMIT}"
        self.assertIn("WorkingDirectory=" + root, unit)
        self.assertIn("ExecStart=" + root + "/.venv/bin/python ", unit)
        self.assertIn("Environment=CRIPTA_RUNTIME_ROOT=" + root, unit)
        self.assertIn("Environment=CRIPTA_RELEASE_COMMIT=" + OLD_EXIT_COMMIT, unit)
        self.assertIn("User=cripta", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("EnvironmentFile=-/etc/cripta/universal_exit_consumer.env", unit)
        self.assertNotIn("EnvironmentFile=-/etc/cripta/release.env", unit)
        self.assertNotIn("/srv/cripta/runtime/current", unit)
        self.assertEqual(unit, build_pinned_old_exit_unit(ORIGINAL_UNIT.read_bytes()))

    def test_unreviewed_old_unit_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "UNIT"):
            build_pinned_old_exit_unit(ORIGINAL_UNIT.read_bytes() + b"\nExecStartPre=evil\n")

    def test_isolated_venv_fingerprint_and_site_packages_gate(self) -> None:
        snapshot = _fingerprint_venv(self.venv)
        self.assertEqual(snapshot["symlink_count"], 4)
        self.assertGreater(snapshot["regular_file_count"], 0)
        self.assertEqual(len(snapshot["fingerprint"]), 64)
        (self.venv / "pyvenv.cfg").write_text(
            "include-system-site-packages = true\n", encoding="ascii",
        )
        with self.assertRaisesRegex(ValueError, "SYSTEM_SITE"):
            _fingerprint_venv(self.venv)

    def test_venv_fingerprint_is_invariant_to_directory_creation_order(self) -> None:
        mirror = self.base / "mirrored_venv"
        shutil.copytree(self.venv, mirror, symlinks=True)
        left = self.venv / "lib/python3.12/site-packages"
        right = mirror / "lib/python3.12/site-packages"
        for base, names in ((left, ("alpha", "omega")), (right, ("omega", "alpha"))):
            for name in names:
                folder = base / name
                folder.mkdir(mode=0o755)
                (folder / "__init__.py").write_text("VALUE = 1\n", encoding="ascii")
        original_walk = os.walk

        def reversed_walk(root: Path, *, followlinks: bool = False):
            yield from reversed(list(original_walk(root, followlinks=followlinks)))

        expected = _fingerprint_venv(self.venv)["fingerprint"]
        with patch("component_release_exit_legacy_prepare.os.walk", reversed_walk):
            actual = _fingerprint_venv(mirror)["fingerprint"]
        self.assertEqual(expected, actual)

    def test_payload_digest_is_independent_of_directory_creation_order(self) -> None:
        roots = (self.base / "payload_left", self.base / "payload_right")
        for root, names in zip(
            roots, (("alpha", "omega"), ("omega", "alpha")), strict=True,
        ):
            root.mkdir(mode=0o750)
            for name in names:
                folder = root / name
                folder.mkdir(mode=0o750)
                (folder / "code.py").write_text("VALUE = 1\n", encoding="ascii")
                os.chmod(folder / "code.py", 0o640)
        original_walk = os.walk

        def reversed_walk(root: Path, *, followlinks: bool = False):
            yield from reversed(list(original_walk(root, followlinks=followlinks)))

        expected = _payload_digest(roots[0], self.owner, self.group)
        with patch("component_release_exit_legacy_prepare.os.walk", reversed_walk):
            actual = _payload_digest(roots[1], self.owner, self.group)
        self.assertEqual(expected, actual)

    def test_unexpected_symlink_is_rejected(self) -> None:
        (self.venv / "lib/python3.12/site-packages/unexpected").symlink_to(
            "/etc/passwd",
        )
        with self.assertRaisesRegex(ValueError, "SYMLINK"):
            _fingerprint_venv(self.venv)

    def test_positive_fixture_is_staged_only_and_verifiable(self) -> None:
        args = self.args()
        report = _prepare_legacy_fixture(**args)
        self.assertEqual(report["state"], "PROTECTED_OLD_EXIT_CANDIDATE_STAGED")
        self.assertFalse(report["rollback_ready"])
        self.assertFalse(report["systemd_unit_installed"])
        self.assertFalse(report["runtime_behavior_verified"])
        self.assertFalse(report["financial_mutation_authorized"])
        candidate = self.rollback / OLD_EXIT_COMMIT
        self.assertTrue((candidate / "src/bybit_workbench/__init__.py").is_file())
        self.assertTrue(
            (candidate / "operations/connectivity/universal_exit_consumer.py").is_file()
        )
        self.assertTrue((candidate / ".venv/pyvenv.cfg").is_file())
        self.assertTrue((candidate / "legacy_exit_pinned.service").is_file())
        self.assertFalse((candidate / "COMPONENT_INSTALLED_COMMIT").exists())
        self.assertFalse((candidate / "COMPONENT_LOADED_COMMIT").exists())
        self.assertEqual((candidate / ".venv/bin/python3").readlink(), Path("/usr/bin/python3"))
        verification = _verify_legacy_fixture(
            candidate_root=candidate,
            expected_archive_sha256=args["expected_archive_sha256"],
            expected_venv_fingerprint=args["expected_venv_fingerprint"],
            owner_uid=self.owner,
            service_gid=self.group,
        )
        self.assertEqual(verification["state"], "PROTECTED_OLD_EXIT_CANDIDATE_READBACK_PASS")
        self.assertFalse(verification["rollback_ready"])
        self.assertEqual(
            (self.input / "legacy_unit.service").read_bytes(),
            ORIGINAL_UNIT.read_bytes(),
        )

    def test_duplicate_preparation_fails_without_rewriting_candidate(self) -> None:
        args = self.args()
        _prepare_legacy_fixture(**args)
        before = (self.rollback / OLD_EXIT_COMMIT).stat().st_ino
        with self.assertRaisesRegex(ValueError, "EXISTS"):
            _prepare_legacy_fixture(**args)
        self.assertEqual((self.rollback / OLD_EXIT_COMMIT).stat().st_ino, before)

    def test_archive_mismatch_and_traversal_fail_closed(self) -> None:
        args = self.args()
        args["expected_archive_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "ARCHIVE"):
            _prepare_legacy_fixture(**args)
        self.assertFalse((self.rollback / OLD_EXIT_COMMIT).exists())
        self.write_archive(wrong_path="../escaped.py")
        args = self.args()
        with self.assertRaisesRegex(ValueError, "ARCHIVE"):
            _prepare_legacy_fixture(**args)
        self.assertFalse((self.base / "escaped.py").exists())

    def test_tampered_venv_fingerprint_blocks_without_creating_files(self) -> None:
        args = self.args()
        args["expected_venv_fingerprint"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "VENV"):
            _prepare_legacy_fixture(**args)
        self.assertFalse((self.rollback / OLD_EXIT_COMMIT).exists())

    def test_wrong_unit_hash_blocks_without_creation(self) -> None:
        self.old_unit.write_bytes(b"unreviewed unit")
        with self.assertRaisesRegex(ValueError, "UNIT"):
            _prepare_legacy_fixture(**self.args())
        self.assertFalse((self.rollback / OLD_EXIT_COMMIT).exists())

    def test_lock_contention_blocks_before_disk_write(self) -> None:
        import fcntl

        with self.lock.open("r+b") as locked:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "LOCK"):
                _prepare_legacy_fixture(**self.args())
            fcntl.flock(locked, fcntl.LOCK_UN)
        self.assertFalse((self.rollback / OLD_EXIT_COMMIT).exists())

    def test_lock_gid_is_independent_from_candidate_group(self) -> None:
        args = self.args()
        args["lock_gid"] = self.group + 1
        with self.assertRaisesRegex(ValueError, "LOCK"):
            _prepare_legacy_fixture(**args)
        self.assertFalse((self.rollback / OLD_EXIT_COMMIT).exists())

    def test_tampered_candidate_readback_rejects(self) -> None:
        args = self.args()
        _prepare_legacy_fixture(**args)
        root = self.rollback / OLD_EXIT_COMMIT
        (root / "src/bybit_workbench/__init__.py").write_text("tampered\n")
        with self.assertRaisesRegex(ValueError, "READBACK"):
            _verify_legacy_fixture(
                candidate_root=root,
                expected_archive_sha256=args["expected_archive_sha256"],
                expected_venv_fingerprint=args["expected_venv_fingerprint"],
                owner_uid=self.owner,
                service_gid=self.group,
            )

    def create_reconciled_copy_stage(self) -> Path:
        stage = self.rollback / (".stage-" + OLD_EXIT_COMMIT)
        stage.mkdir(mode=0o700)
        with tarfile.open(self.archive, "r:") as tar:
            for member in tar:
                if member.name.startswith(".venv"):
                    continue
                target = stage / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stream = tar.extractfile(member)
                    assert stream is not None
                    target.write_bytes(stream.read())
        shutil.copytree(self.venv, stage / ".venv", symlinks=True)
        return stage

    def test_verified_partial_stage_is_finalized_without_recopied_inputs(self) -> None:
        stage = self.create_reconciled_copy_stage()
        before_source = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        result = _resume_legacy_fixture(**self.args())
        self.assertEqual(result["state"], "PROTECTED_OLD_EXIT_CANDIDATE_STAGED")
        self.assertFalse(result["rollback_ready"])
        self.assertFalse(stage.exists())
        self.assertEqual(
            hashlib.sha256(self.archive.read_bytes()).hexdigest(), before_source,
        )
        self.assertTrue((self.rollback / OLD_EXIT_COMMIT / "legacy_exit_pinned.service").is_file())

    def test_extra_file_in_partial_stage_blocks_without_delete(self) -> None:
        stage = self.create_reconciled_copy_stage()
        (stage / "src/untrusted.py").write_text("bad\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "PARTIAL_STAGE"):
            _resume_legacy_fixture(**self.args())
        self.assertTrue(stage.exists())
        self.assertFalse((self.rollback / OLD_EXIT_COMMIT).exists())

    def test_partial_stage_with_venv_change_blocks_without_delete(self) -> None:
        stage = self.create_reconciled_copy_stage()
        (stage / ".venv/pyvenv.cfg").write_text(
            "include-system-site-packages = true\n", encoding="ascii",
        )
        with self.assertRaises(ValueError):
            _resume_legacy_fixture(**self.args())
        self.assertTrue(stage.exists())
        self.assertFalse((self.rollback / OLD_EXIT_COMMIT).exists())

    def test_partial_stage_production_wrapper_has_no_arguments(self) -> None:
        self.assertEqual(finalize_reconciled_partial_legacy_exit_once.__code__.co_argcount, 0)
        if os.geteuid() != 0:
            with self.assertRaisesRegex(ValueError, "ROOT_REQUIRED"):
                finalize_reconciled_partial_legacy_exit_once()

    def test_malformed_destination_or_untrusted_existing_partial_stage(self) -> None:
        args = self.args()
        stage = self.rollback / (".stage-" + OLD_EXIT_COMMIT)
        stage.mkdir()
        with self.assertRaisesRegex(ValueError, "EXISTS"):
            _prepare_legacy_fixture(**args)
        self.assertTrue(stage.exists())
        self.assertFalse((self.rollback / OLD_EXIT_COMMIT).exists())

    def test_production_wrapper_cannot_be_called_from_unprivileged_test(self) -> None:
        self.assertEqual(prepare_legacy_exit_rollback_once.__code__.co_argcount, 0)
        if os.geteuid() != 0:
            with self.assertRaisesRegex(ValueError, "ROOT_REQUIRED"):
                prepare_legacy_exit_rollback_once()


if __name__ == "__main__":
    unittest.main()
