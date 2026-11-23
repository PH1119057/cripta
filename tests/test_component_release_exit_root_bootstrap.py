"""#849 exact root-owned Exit component tree bootstrap: test FIRST.

All materialization fixtures use a disposable user-owned sandbox; none
test root/sandbox authorization or install a release on production.
"""
from __future__ import annotations

import errno
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

from component_release_exit_root_bootstrap import (  # noqa: E402
    _bootstrap_under_verified_anchor,
    _hold_shared_release_lock,
    _inspect_under_verified_anchor,
    bootstrap_exit_root_once,
)

_EXPECTED_CHILDREN = (
    "components",
    "components/exit",
    "components/exit/releases",
    "components/exit/rollback",
)


@unittest.skipUnless(os.name == "posix", "Linux descriptor-relative mkdir")
class ExitRootBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.anchor = Path(self.tmp.name) / "cripta"
        self.anchor.mkdir(mode=0o755)
        self.uid = os.geteuid()
        self.gid = os.getegid()
        os.chmod(self.anchor, 0o755)
        # The approved Data SSD testenv inherits unrelated sentinelx ACLs.
        # Remove those on this disposable fixture only. Production must
        # still reject unreviewed access/default ACLs without repairing them.
        for key in ("system.posix_acl_access", "system.posix_acl_default"):
            try:
                os.removexattr(self.anchor, key)
            except OSError as exc:
                if exc.errno != errno.ENODATA:
                    raise

    def _inspect(self) -> dict[str, object]:
        return _inspect_under_verified_anchor(
            self.anchor,
            anchor_uid=self.uid,
            anchor_gid=self.gid,
            components_uid=self.uid,
            components_gid=self.gid,
        )

    def _prepare(self) -> dict[str, object]:
        return _bootstrap_under_verified_anchor(
            self.anchor,
            anchor_uid=self.uid,
            anchor_gid=self.gid,
            components_uid=self.uid,
            components_gid=self.gid,
        )

    def test_missing_chain_denies_install_and_requires_bootstrap(self) -> None:
        result = self._inspect()
        self.assertEqual(result["state"], "ROOT_TREE_NEEDS_FIRST_BOOTSTRAP")
        self.assertFalse(result["install_authorized"])
        self.assertFalse(result["rollback_ready"])
        self.assertFalse(result["live_cutover_authorized"])
        self.assertEqual(result["missing_paths"], list(_EXPECTED_CHILDREN))

    def test_exact_first_bootstrap_and_idempotent_readback(self) -> None:
        first = self._prepare()
        self.assertEqual(first["state"], "ROOT_TREE_PREPARED_METADATA_ONLY")
        self.assertEqual(first["created_paths"], list(_EXPECTED_CHILDREN))
        self.assertFalse(first["install_authorized"])
        self.assertFalse(first["rollback_ready"])
        for rel in _EXPECTED_CHILDREN:
            item = self.anchor / rel
            info = item.lstat()
            self.assertTrue(stat.S_ISDIR(info.st_mode))
            self.assertEqual((info.st_uid, info.st_gid), (self.uid, self.gid))
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o750)
            self.assertFalse(item.is_symlink())
        before = {rel: (self.anchor / rel).stat().st_ino for rel in _EXPECTED_CHILDREN}
        self.assertEqual(self._inspect()["state"], "ROOT_TREE_PREPARED_METADATA_ONLY")
        again = self._prepare()
        self.assertEqual(again["state"], "ROOT_TREE_ALREADY_PREPARED_METADATA_ONLY")
        self.assertEqual(again["created_paths"], [])
        after = {rel: (self.anchor / rel).stat().st_ino for rel in _EXPECTED_CHILDREN}
        self.assertEqual(before, after)

    def test_untrusted_anchor_mode_or_uid_denied_without_creation(self) -> None:
        os.chmod(self.anchor, 0o775)
        with self.assertRaises(ValueError):
            self._prepare()
        self.assertFalse((self.anchor / "components").exists())
        os.chmod(self.anchor, 0o755)
        with self.assertRaises(ValueError):
            _bootstrap_under_verified_anchor(
                self.anchor, anchor_uid=0, anchor_gid=0,
                components_uid=self.uid, components_gid=self.gid,
            )
        self.assertFalse((self.anchor / "components").exists())

    def test_preexisting_symlink_to_unrelated_target_is_denied(self) -> None:
        target = Path(self.tmp.name) / "escape"
        target.mkdir()
        (self.anchor / "components").symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self._prepare()
        self.assertFalse((target / "exit").exists())

    def test_untrusted_existing_component_owner_mode_denied_without_repair(self) -> None:
        target = self.anchor / "components"
        target.mkdir(mode=0o750)
        os.chmod(target, 0o770)
        with self.assertRaises(ValueError):
            self._prepare()
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o770)
        self.assertFalse((target / "exit").exists())

    def test_partial_previous_bootstrap_requires_reconciliation(self) -> None:
        existing = self.anchor / "components"
        existing.mkdir(mode=0o750)
        os.chmod(existing, 0o750)
        snapshot = self._inspect()
        self.assertEqual(snapshot["state"], "ROOT_TREE_PARTIAL_BLOCKED")
        with self.assertRaisesRegex(ValueError, "ROOT_TREE_PARTIAL_BLOCKED"):
            self._prepare()
        self.assertFalse((existing / "exit").exists())

    def test_preexisting_file_and_untrusted_trailing_symlink_denied(self) -> None:
        (self.anchor / "components").write_text("unexpected", encoding="ascii")
        with self.assertRaises(ValueError):
            self._prepare()
        (self.anchor / "components").unlink()
        self._prepare()
        rollback = self.anchor / "components/exit/rollback"
        rollback.rmdir()
        rollback.symlink_to(Path(self.tmp.name), target_is_directory=True)
        with self.assertRaises(ValueError):
            self._prepare()

    def test_relative_anchor_and_production_function_reject_nonroot(self) -> None:
        with self.assertRaises(ValueError):
            _bootstrap_under_verified_anchor(
                Path("relative"), anchor_uid=self.uid, anchor_gid=self.gid,
                components_uid=self.uid, components_gid=self.gid,
            )
        if self.uid != 0:
            with self.assertRaisesRegex(ValueError, "ROOT_REQUIRED"):
                bootstrap_exit_root_once()
        self.assertFalse((self.anchor / "components").exists())

    def test_partial_interruption_requires_manual_reconciliation(self) -> None:
        original_mkdir = os.mkdir

        def interrupted(name: str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
            if name == "releases":
                raise PermissionError("synthetic mid-bootstrap failure")
            original_mkdir(name, mode, dir_fd=dir_fd)

        with (
            patch("component_release_exit_root_bootstrap.os.mkdir", side_effect=interrupted),
            self.assertRaisesRegex(ValueError, "BOOTSTRAP_OUTCOME_UNKNOWN"),
        ):
            self._prepare()
        self.assertEqual(self._inspect()["state"], "ROOT_TREE_PARTIAL_BLOCKED")
        with self.assertRaisesRegex(ValueError, "ROOT_TREE_PARTIAL_BLOCKED"):
            self._prepare()
        self.assertFalse((self.anchor / "components/exit/releases").exists())
        self.assertFalse((self.anchor / "components/exit/rollback").exists())

    def test_unreviewed_default_acl_is_never_auto_removed(self) -> None:
        # Linux POSIX ACL version 2, three base entries (user/group/other).
        acl = struct.pack("<I", 2) + b"".join((
            struct.pack("<HHI", 0x01, 7, 0xFFFFFFFF),
            struct.pack("<HHI", 0x04, 5, 0xFFFFFFFF),
            struct.pack("<HHI", 0x20, 5, 0xFFFFFFFF),
        ))
        try:
            os.setxattr(self.anchor, "system.posix_acl_default", acl)
        except OSError as exc:
            if exc.errno in (errno.EOPNOTSUPP, errno.ENOSYS):
                self.skipTest("Linux filesystem has no POSIX ACL support")
            raise
        with self.assertRaisesRegex(ValueError, "UNREVIEWED_ACL"):
            self._prepare()
        self.assertFalse((self.anchor / "components").exists())

    def test_symlinked_anchor_is_never_followed(self) -> None:
        alias = Path(self.tmp.name) / "alias"
        alias.symlink_to(self.anchor, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "ANCHOR_UNAVAILABLE"):
            _bootstrap_under_verified_anchor(
                alias, anchor_uid=self.uid, anchor_gid=self.gid,
                components_uid=self.uid, components_gid=self.gid,
            )
        self.assertFalse((self.anchor / "components").exists())

    def _test_lock_path(self) -> Path:
        lock = self.anchor / "exact-release.lock"
        with lock.open("xb") as stream:
            stream.flush()
        os.chmod(lock, 0o644)
        return lock

    def test_preexisting_exact_shared_lock_can_be_acquired_readonly(self) -> None:
        lock = self._test_lock_path()
        before = lock.stat()
        with _hold_shared_release_lock(
            path=lock, uid=self.uid, gid=self.gid,
        ):
            self.assertEqual(lock.stat().st_ino, before.st_ino)
            self.assertEqual(lock.read_bytes(), b"")
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o644)

    def test_release_lock_contention_blocks_even_when_root_tree_missing(self) -> None:
        import fcntl

        lock = self._test_lock_path()
        with lock.open("r+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                self.assertRaisesRegex(ValueError, "RELEASE_LOCK_BUSY"),
                _hold_shared_release_lock(path=lock, uid=self.uid, gid=self.gid),
            ):
                self.fail("lock collision unexpectedly passed")
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        self.assertFalse((self.anchor / "components").exists())

    def test_release_lock_missing_wrong_mode_or_symlink_denied(self) -> None:
        lock = self.anchor / "exact-release.lock"
        with (
            self.assertRaisesRegex(ValueError, "RELEASE_LOCK"),
            _hold_shared_release_lock(path=lock, uid=self.uid, gid=self.gid),
        ):
            self.fail("missing lock allowed")
        lock = self._test_lock_path()
        os.chmod(lock, 0o666)
        with (
            self.assertRaisesRegex(ValueError, "RELEASE_LOCK"),
            _hold_shared_release_lock(path=lock, uid=self.uid, gid=self.gid),
        ):
            self.fail("writable lock allowed")
        os.chmod(lock, 0o644)
        lock.unlink()
        lock.symlink_to(Path(self.tmp.name) / "nowhere")
        with (
            self.assertRaisesRegex(ValueError, "RELEASE_LOCK"),
            _hold_shared_release_lock(path=lock, uid=self.uid, gid=self.gid),
        ):
            self.fail("symlink lock allowed")

    def test_only_root_owned_directory_contract_has_no_trading_side_effect(self) -> None:
        result = self._prepare()
        self.assertFalse(result["install_authorized"])
        self.assertFalse(result["rollback_ready"])
        self.assertFalse(result["financial_mutation_authorized"])
        self.assertFalse(result["service_restart_authorized"])
        self.assertFalse(result["runtime_behavior_verified"])


if __name__ == "__main__":
    unittest.main()
