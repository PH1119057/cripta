"""#849 reusable protected OLD Exit rollback read-only attestation tests.

All fixture writes are strictly within disposable test directories.
No root/systemd/DB/Bybit/ARM mutation occurs in test or attestation.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
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
    _prepare_legacy_fixture,
    build_pinned_old_exit_unit,
)
from component_release_exit_rollback_attestation import (  # noqa: E402
    _attest_old_exit_candidate_fixture,
    attest_old_exit_candidate_on_host,
)

OLD_UNIT = ROOT / "operations/systemd/cripta-universal-exit-consumer.service"
_LINKS = {
    "bin/python": "python3",
    "bin/python3": "/usr/bin/python3",
    "bin/python3.12": "python3",
    "lib64": "lib",
}


@unittest.skipUnless(os.name == "posix", "Linux protected metadata contract")
class ExitRollbackAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.root = self.base / "rollback"
        self.root.mkdir(mode=0o750)
        os.chmod(self.root, 0o750)
        self.input = self.base / "protected_input"
        self.input.mkdir(mode=0o750)
        os.chmod(self.input, 0o750)
        self.archive = self.input / "legacy_runtime.tar"
        with tarfile.open(self.archive, "w") as tar:
            for name, data in {
                "INSTALLED_COMMIT": (OLD_EXIT_COMMIT + "\n").encode(),
                "src/bybit_workbench/__init__.py": b"VALUE = 1\n",
                "operations/connectivity/universal_exit_consumer.py":
                    b"print('not launched')\n",
            }.items():
                row = tarfile.TarInfo(name)
                row.size = len(data)
                row.mode = 0o644
                tar.addfile(row, io.BytesIO(data))
        os.chmod(self.archive, 0o640)
        self.unit = self.input / "legacy_unit.service"
        self.unit.write_bytes(OLD_UNIT.read_bytes())
        os.chmod(self.unit, 0o640)
        self.venv = self.base / "venv"
        for directory in ("bin", "lib/python3.12/site-packages"):
            (self.venv / directory).mkdir(parents=True, exist_ok=True)
        (self.venv / "pyvenv.cfg").write_text(
            "home = /usr/bin\ninclude-system-site-packages = false\n",
            encoding="ascii",
        )
        (self.venv / "lib/python3.12/site-packages/module.py").write_bytes(
            b"VALUE = 1\n",
        )
        for rel, target in _LINKS.items():
            (self.venv / rel).symlink_to(target)
        self.lock = self.base / "release.lock"
        self.lock.write_bytes(b"")
        os.chmod(self.lock, 0o644)
        self.archive_sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.venv_sha = _fingerprint_venv(self.venv)["fingerprint"]
        self.unit_sha = hashlib.sha256(self.unit.read_bytes()).hexdigest()
        marker = {
            "state": "PROTECTED_LEGACY_EXIT_INPUT_ONLY_NOT_RECOVERABLE",
            "source_commit": OLD_EXIT_COMMIT,
            "archive_sha256": self.archive_sha,
            "systemd_unit_sha256": self.unit_sha,
            "rollback_ready": False,
        }
        self.marker = self.input / "INPUT_SNAPSHOT.json"
        self.marker.write_text(json.dumps(marker), encoding="utf-8")
        os.chmod(self.marker, 0o640)
        report = _prepare_legacy_fixture(
            archive=self.archive, source_unit=self.unit,
            isolated_venv=self.venv, rollback_parent=self.root,
            lock_path=self.lock,
            expected_archive_sha256=self.archive_sha,
            expected_venv_fingerprint=self.venv_sha,
            owner_uid=self.uid, service_gid=self.gid, lock_gid=self.gid,
        )
        self.candidate = self.root / OLD_EXIT_COMMIT
        self.payload_sha = str(report["candidate_payload_sha256"])
        self.pinned_sha = hashlib.sha256(
            build_pinned_old_exit_unit(OLD_UNIT.read_bytes()).encode()
        ).hexdigest()

    def verify(self) -> dict[str, object]:
        return _attest_old_exit_candidate_fixture(
            candidate_root=self.candidate,
            rollback_parent=self.root,
            protected_input_dir=self.input,
            expected_payload_sha256=self.payload_sha,
            expected_archive_sha256=self.archive_sha,
            expected_venv_fingerprint=self.venv_sha,
            expected_pinned_unit_sha256=self.pinned_sha,
            owner_uid=self.uid,
            service_gid=self.gid,
        )

    def test_protected_candidate_attestation_is_staged_only(self) -> None:
        before = self.candidate.stat().st_mtime_ns
        report = self.verify()
        self.assertEqual(report["state"], "OLD_EXIT_RECOVERY_STAGED_READBACK_PASS")
        self.assertEqual(report["component_id"], "EXIT")
        self.assertEqual(report["source_commit"], OLD_EXIT_COMMIT)
        self.assertEqual(report["payload_sha256"], self.payload_sha)
        self.assertEqual(report["pinned_unit_sha256"], self.pinned_sha)
        self.assertFalse(report["rollback_ready"])
        self.assertFalse(report["install_authorized"])
        self.assertFalse(report["systemd_unit_installed"])
        self.assertFalse(report["runtime_behavior_verified"])
        self.assertFalse(report["signed_main_sub_verified"])
        self.assertFalse(report["actual_host_attested"])
        self.assertEqual(before, self.candidate.stat().st_mtime_ns)

    def test_missing_or_wrong_manifest_fails_closed(self) -> None:
        marker = self.candidate / "OLD_EXIT_ROLLBACK_CANDIDATE.json"
        manifest = json.loads(marker.read_text())
        manifest["rollback_ready"] = True
        marker.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            self.verify()
        marker.unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_tampered_payload_and_pinned_unit_fail_closed(self) -> None:
        path = self.candidate / "src/bybit_workbench/__init__.py"
        path.write_bytes(b"CHANGED\n")
        with self.assertRaises(ValueError):
            self.verify()
        path.write_bytes(b"VALUE = 1\n")
        pinned = self.candidate / "legacy_exit_pinned.service"
        pinned.write_bytes(pinned.read_bytes() + b"\nExecStartPre=unknown\n")
        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_owner_mode_and_untrusted_symlink_denied(self) -> None:
        os.chmod(self.candidate, 0o770)
        with self.assertRaises(ValueError):
            self.verify()
        os.chmod(self.candidate, 0o750)
        rogue = self.candidate / "src/other"
        rogue.symlink_to("/etc/passwd")
        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_input_archive_or_source_unit_fails_closed(self) -> None:
        self.archive.write_bytes(self.archive.read_bytes() + b"x")
        with self.assertRaises(ValueError):
            self.verify()
        self.archive.write_bytes(self.archive.read_bytes()[:-1])
        self.unit.write_bytes(b"wrong unit")
        with self.assertRaises(ValueError):
            self.verify()

    def test_input_readiness_claim_and_missing_venv_is_denied(self) -> None:
        claim = json.loads(self.marker.read_text())
        claim["rollback_ready"] = True
        self.marker.write_text(json.dumps(claim))
        with self.assertRaises(ValueError):
            self.verify()
        claim["rollback_ready"] = False
        self.marker.write_text(json.dumps(claim))
        (self.candidate / ".venv/pyvenv.cfg").unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_mismatched_expected_payload_or_pinned_unit_denied(self) -> None:
        kwargs = {
            "candidate_root": self.candidate,
            "rollback_parent": self.root,
            "protected_input_dir": self.input,
            "expected_payload_sha256": "f" * 64,
            "expected_archive_sha256": self.archive_sha,
            "expected_venv_fingerprint": self.venv_sha,
            "expected_pinned_unit_sha256": self.pinned_sha,
            "owner_uid": self.uid,
            "service_gid": self.gid,
        }
        with self.assertRaises(ValueError):
            _attest_old_exit_candidate_fixture(**kwargs)
        kwargs["expected_payload_sha256"] = self.payload_sha
        kwargs["expected_pinned_unit_sha256"] = "a" * 64
        with self.assertRaises(ValueError):
            _attest_old_exit_candidate_fixture(**kwargs)

    def test_host_noarg_entrypoint_rejects_unprivileged_actor(self) -> None:
        self.assertEqual(attest_old_exit_candidate_on_host.__code__.co_argcount, 0)
        with (
            patch("component_release_exit_rollback_attestation.os.geteuid",
                  return_value=107),
            self.assertRaisesRegex(ValueError, "ROOT_REQUIRED"),
        ):
            attest_old_exit_candidate_on_host()


if __name__ == "__main__":
    unittest.main()
