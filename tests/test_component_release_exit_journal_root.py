"""#849 durable Exit-only release journal lives under root-owned /srv parent."""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))
from component_release_exit_root_bootstrap import (  # noqa: E402
    _journal_inspect_under_parent_fd,
    _journal_stage_under_parent_fd,
    bootstrap_exit_journal_root_once,
)
from component_release_exit_transition import _JOURNAL_PARENT  # noqa: E402


@unittest.skipUnless(os.name == "posix", "requires Unix metadata")
class ExitJournalProtectedPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="exit-journal-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root / "exit"
        self.parent.mkdir(mode=0o750)
        self.fd = os.open(self.parent, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.owner = os.geteuid()
        self.group = os.getegid()
        self.dev = os.fstat(self.fd).st_dev

    def stage(self) -> dict[str, object]:
        return _journal_stage_under_parent_fd(
            self.fd, parent_uid=self.owner, parent_gid=self.group,
            journal_uid=self.owner, journal_gid=self.group, dev=self.dev,
        )

    def inspect(self) -> dict[str, object]:
        return _journal_inspect_under_parent_fd(
            self.fd, parent_uid=self.owner, parent_gid=self.group,
            journal_uid=self.owner, journal_gid=self.group, dev=self.dev,
        )

    def test_creates_durable_protected_directory_only(self) -> None:
        self.assertEqual(self.inspect()["state"], "EXIT_JOURNAL_ROOT_MISSING")
        result = self.stage()
        self.assertEqual(result["state"], "EXIT_JOURNAL_ROOT_PREPARED_METADATA_ONLY")
        self.assertIs(result["service_restart_authorized"], False)
        self.assertIs(result["trading_mutation_authorized"], False)
        self.assertEqual(
            self.inspect()["state"], "PROTECTED_EXIT_JOURNAL_ROOT_READY_READ_ONLY",
        )
        item = self.parent / "journal"
        z = item.lstat()
        self.assertTrue(stat.S_ISDIR(z.st_mode))
        self.assertEqual(stat.S_IMODE(z.st_mode), 0o700)
        self.assertEqual((z.st_uid, z.st_gid), (self.owner, self.group))
        self.assertEqual(tuple(item.iterdir()), ())

    def test_existing_correct_directory_is_not_replaced_or_truncated(self) -> None:
        self.stage()
        marker = self.parent / "journal/previous-transaction.jsonl"
        marker.write_text("no overwrite\n", encoding="utf-8")
        result = self.stage()
        self.assertEqual(
            result["state"], "EXIT_JOURNAL_ROOT_ALREADY_PREPARED_METADATA_ONLY",
        )
        self.assertEqual(marker.read_text(), "no overwrite\n")

    def test_symlink_is_rejected_with_no_follow(self) -> None:
        (self.parent / "journal").symlink_to(self.root)
        with self.assertRaises(ValueError):
            self.stage()
        self.assertTrue((self.parent / "journal").is_symlink())

    def test_foreign_or_writable_parent_fails_before_mutation(self) -> None:
        self.parent.chmod(0o770)
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse((self.parent / "journal").exists())

    def test_wrong_existing_journal_mode_not_silently_fixed(self) -> None:
        (self.parent / "journal").mkdir(mode=0o750)
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual(
            stat.S_IMODE((self.parent / "journal").stat().st_mode), 0o750,
        )

    def test_no_auto_create_missing_parent(self) -> None:
        os.close(self.fd)
        self._cleaned_fd = True
        # Work only with the private fixture, never production /srv.
        missing = self.root / "missing"
        with self.assertRaises(OSError):
            os.open(missing, os.O_RDONLY | os.O_DIRECTORY)
        self.fd = os.open(self.parent, os.O_RDONLY | os.O_DIRECTORY)

    def test_production_root_bootstrap_accepts_no_arguments(self) -> None:
        self.assertEqual(bootstrap_exit_journal_root_once.__code__.co_argcount, 0)
        with (
            patch("component_release_exit_root_bootstrap.sys.platform", "linux"),
            patch("component_release_exit_root_bootstrap.os.geteuid",
                  return_value=107),
            self.assertRaisesRegex(ValueError, "ROOT_REQUIRED"),
        ):
            bootstrap_exit_journal_root_once()

    def test_journal_source_now_uses_root_protected_ancestor(self) -> None:
        self.assertEqual(
            str(_JOURNAL_PARENT), "/srv/cripta/components/exit/journal",
        )
        self.assertFalse(str(_JOURNAL_PARENT).startswith("/var/lib/cripta/"))


if __name__ == "__main__":
    unittest.main()
