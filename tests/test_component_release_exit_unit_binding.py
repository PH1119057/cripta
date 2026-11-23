"""#849 source-compatible Exit unit overlay: real filesystem negative gates.

The already sealed b451 binary is immutable and must NOT be rewritten.
All private filesystem tests are synthetic, not production release approval.
"""
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
from component_release_exit_unit_binding import (  # noqa: E402
    _BINDING_NAME,
    _UNIT_FILE,
    _UNIT_SHA256,
    _readback_binding_fixture,
    _stage_binding_fixture,
    prepare_exit_source_compatible_unit_once,
)
from component_release_exit_unit_spec import (  # noqa: E402
    build_exit_unit_candidate,
)

COMPONENT = "b451165846af286e2f31404d9cdb4eb5cf599031"
SOURCE = "8bd45e9874d6ce811d7261ee152fc2c43729e1b9"
LOCK = "eb9a7aea12c9be5836b32c2c18570adf395f0512d3c3f1bff10a1853d3ef6c15"


@unittest.skipUnless(os.name == "posix", "requires Linux-style UID/GID")
class ExitUnitBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="unit-binding-849-")
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name) / "releases"
        self.parent.mkdir(mode=0o750)
        self.uid = os.geteuid()
        self.gid = os.getegid()
        original = (
            ROOT / "operations/systemd/cripta-universal-exit-consumer.service"
        ).read_text(encoding="utf-8")
        proposed = build_exit_unit_candidate(
            source_unit=original, component_commit=COMPONENT,
            component_lock_sha256=LOCK, source_compatibility_commit=SOURCE,
        )
        self.unit = proposed["unit_text"].encode()
        self.assertEqual(proposed["unit_sha256"], _UNIT_SHA256)

    def stage(self, unit: bytes | None = None) -> dict[str, object]:
        return _stage_binding_fixture(
            parent=self.parent, owner_uid=self.uid, service_gid=self.gid,
            binding_name=_BINDING_NAME,
            expected_unit=self.unit if unit is None else unit,
        )

    def readback(self) -> dict[str, object]:
        return _readback_binding_fixture(
            parent=self.parent, owner_uid=self.uid, service_gid=self.gid,
            binding_name=_BINDING_NAME, expected_unit=self.unit,
        )

    def test_actual_fs_stage_exact_sealed_binding_and_readback(self) -> None:
        result = self.stage()
        self.assertEqual(result["state"], "PROTECTED_EXIT_UNIT_BINDING_PREPARED_ONLY")
        self.assertEqual(result, self.readback())
        self.assertTrue(result["file_digest_readback"])
        self.assertFalse(result["component_loaded"])
        self.assertFalse(result["rollback_ready"])
        self.assertFalse(result["cutover_authorized"])
        self.assertFalse(result["systemd_action_performed"])
        bound = self.parent / _BINDING_NAME
        self.assertEqual(
            stat.S_IMODE(bound.lstat().st_mode), 0o750,
        )
        self.assertEqual(
            stat.S_IMODE((bound / _UNIT_FILE).lstat().st_mode), 0o640,
        )
        self.assertIn(b"Environment=CRIPTA_RELEASE_COMMIT=" +
                      SOURCE.encode(), self.unit)
        self.assertIn(b"Environment=CRIPTA_EXIT_COMPONENT_COMMIT=" +
                      COMPONENT.encode(), self.unit)

    def test_existing_or_interrupted_binding_never_overwritten(self) -> None:
        self.stage()
        unit = self.parent / _BINDING_NAME / _UNIT_FILE
        saved = unit.read_bytes()
        with self.assertRaisesRegex(ValueError, "ALREADY_EXISTS"):
            self.stage()
        self.assertEqual(unit.read_bytes(), saved)

    def test_partial_stage_is_not_silently_cleaned(self) -> None:
        pending = self.parent / (
            ".pending-" + _BINDING_NAME.lstrip(".")
        )
        pending.mkdir()
        with self.assertRaisesRegex(ValueError, "ALREADY_EXISTS"):
            self.stage()
        self.assertTrue(pending.is_dir())
        self.assertFalse((self.parent / _BINDING_NAME).exists())

    def test_wrong_unit_or_template_cannot_be_sealed(self) -> None:
        with self.assertRaisesRegex(ValueError, "DRIFT"):
            self.stage(self.unit + b"# bad\n")
        self.assertFalse((self.parent / _BINDING_NAME).exists())

    def test_protected_unit_tamper_detected_on_readback(self) -> None:
        self.stage()
        unit = self.parent / _BINDING_NAME / _UNIT_FILE
        unit.write_bytes(b"replaced")
        with self.assertRaises(ValueError):
            self.readback()

    def test_marker_tamper_detected_on_readback(self) -> None:
        self.stage()
        marker = self.parent / _BINDING_NAME / "UNIT_BINDING_PREPARED.json"
        marker.write_bytes(b"forged")
        with self.assertRaises(ValueError):
            self.readback()

    def test_symlink_or_mutable_parent_blocks(self) -> None:
        self.parent.chmod(0o770)
        with self.assertRaises(ValueError):
            self.stage()
        self.parent.chmod(0o750)
        (self.parent / _BINDING_NAME).symlink_to(self.parent)
        with self.assertRaisesRegex(ValueError, "ALREADY_EXISTS"):
            self.stage()

    def test_noarg_root_owner_rejects_unprivileged_actor_before_writes(self) -> None:
        self.assertEqual(
            prepare_exit_source_compatible_unit_once.__code__.co_argcount, 0,
        )
        with (
            patch("component_release_exit_unit_binding.sys.platform", "linux"),
            patch("component_release_exit_unit_binding.os.geteuid",
                  return_value=107),
            self.assertRaisesRegex(ValueError, "ROOT_LINUX_REQUIRED"),
        ):
            prepare_exit_source_compatible_unit_once()

    def test_noarg_windows_owner_denied_before_any_os_write(self) -> None:
        with (
            patch("component_release_exit_unit_binding.sys.platform", "win32"),
            self.assertRaisesRegex(ValueError, "ROOT_LINUX_REQUIRED"),
        ):
            prepare_exit_source_compatible_unit_once()


if __name__ == "__main__":
    unittest.main()
