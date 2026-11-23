"""#849 new Exit immutable payload: bounded real filesystem stage fixtures."""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))
from component_release_exit_new_install import (  # noqa: E402
    _readback_prepared_candidate,
    _stage_prepared_candidate,
    _verify_candidate_zip,
    prepare_exit_new_component_once,
)
from component_release_exit_legacy_prepare import _fingerprint_venv  # noqa: E402

COMMIT = "a" * 40
ENTRY = "operations/connectivity/universal_exit_consumer.py"
LOCK = "operations/infrastructure/exit_component_requirements.lock"
SIBLINGS = (
    "operations/connectivity/p90_real_independent_exit.py",
    "operations/connectivity/p90_real_persistent_trail.py",
    "operations/connectivity/p90_r06_exit_owner.py",
    "operations/connectivity/p90l90_real_postfill_source_client.py",
)


def _bundle() -> bytes:
    files = {
        "src/bybit_workbench/__init__.py": b"# test\n",
        ENTRY: b"print('test only')\n",
        LOCK: b"psycopg==3.1.17 --hash=sha256:" + b"a" * 64 + b"\n",
        **{x: b"# isolated test\n" for x in SIBLINGS},
    }
    manifest = {
        "schema_version": 1,
        "component": "EXIT",
        "baseline": "b" * 40,
        "target": COMMIT,
        "delta_paths": ["operations/connectivity/p90_real_persistent_trail.py"],
        "candidate_units": ["cripta-universal-exit-consumer.service"],
        "entrypoint": ENTRY,
        "dynamic_import_siblings": list(SIBLINGS),
        "dependency_lock": LOCK,
        "dependency_lock_sha256": hashlib.sha256(files[LOCK]).hexdigest(),
        "blob_sha256": {
            k: hashlib.sha256(v).hexdigest() for k, v in files.items()
        },
        "status": "STAGED_ONLY_NOT_DEPLOYABLE",
        "dependency_environment": "NOT_ATTESTED",
        "postgresql_compatibility": "NOT_ATTESTED",
        "install_authorized": False,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, payload in files.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, payload)
        info = zipfile.ZipInfo("component_manifest.json")
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100444 << 16
        z.writestr(info, json.dumps(
            manifest, sort_keys=True, separators=(",", ":"),
        ).encode())
    return buf.getvalue()


def _fake_venv(root: Path) -> str:
    (root / "bin").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "pyvenv.cfg").write_text(
        "include-system-site-packages = false\n", encoding="utf-8",
    )
    (root / "bin/pip").write_bytes(b"#!/bin/sh\n")
    for name, target in (
        ("python", "python3"),
        ("python3", "/usr/bin/python3"),
        ("python3.12", "python3"),
    ):
        (root / "bin" / name).symlink_to(target)
    (root / "lib64").symlink_to("lib")
    return str(_fingerprint_venv(root)["fingerprint"])


@unittest.skipUnless(os.name == "posix", "Unix filesystem metadata fixture")
class ExitCandidateRootStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="exit-new-849-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root / "releases"
        self.parent.mkdir(mode=0o750)
        self.venv = self.root / "venv"
        self.fingerprint = _fake_venv(self.venv)
        self.blob = _bundle()
        self.candidate = self.parent / COMMIT
        self.unit = (
            "[Unit]\nDescription=Exit test\n[Service]\n"
            "ExecStart=/srv/cripta/components/exit/releases/"
            + COMMIT + "/.venv/bin/python test\n"
        ).encode()

    def stage(self, blob: bytes | None = None) -> dict[str, object]:
        archive = self.blob if blob is None else blob
        contents, manifest_digest = _verify_candidate_zip(
            archive,
            expected_archive_sha256=hashlib.sha256(self.blob).hexdigest(),
            expected_commit=COMMIT,
            expected_lock_sha256=hashlib.sha256(
                b"psycopg==3.1.17 --hash=sha256:" + b"a" * 64 + b"\n"
            ).hexdigest(),
        )
        return _stage_prepared_candidate(
            component_parent=self.parent,
            commit=COMMIT,
            contents=contents,
            manifest_sha256=manifest_digest,
            archive_sha256=hashlib.sha256(self.blob).hexdigest(),
            isolated_venv=self.venv,
            expected_venv_fingerprint=self.fingerprint,
            unit_bytes=self.unit,
            owner_uid=os.geteuid(),
            service_gid=os.getegid(),
        )

    def test_real_private_root_stage_and_byte_readback_no_service_action(self) -> None:
        result = self.stage()
        self.assertEqual(result["state"], "IMMUTABLE_EXIT_CANDIDATE_PREPARED")
        self.assertTrue(result["physical_file_hashes_verified"])
        self.assertIs(result["new_component_loaded"], False)
        self.assertIs(result["cutover_authorized"], False)
        self.assertIs(result["rollback_ready"], False)
        self.assertTrue(self.candidate.is_dir())
        self.assertFalse(self.candidate.is_symlink())
        self.assertEqual(
            stat.S_IMODE(self.candidate.lstat().st_mode), 0o750,
        )
        before = result["payload_sha256"]
        verified = _readback_prepared_candidate(
            candidate_root=self.candidate, owner_uid=os.geteuid(),
            service_gid=os.getegid(),
            expected_commit=COMMIT,
            expected_archive_sha256=hashlib.sha256(self.blob).hexdigest(),
        )
        self.assertEqual(verified["payload_sha256"], before)
        for f in self.candidate.rglob("*"):
            self.assertNotEqual(stat.S_IMODE(f.lstat().st_mode) & 0o002, 0o002)
        self.assertTrue((self.candidate / ".venv/bin/python").is_symlink())

    def test_wrong_blob_and_unknown_commit_never_stage(self) -> None:
        with self.assertRaisesRegex(ValueError, "ARCHIVE_HASH"):
            self.stage(self.blob + b"tamper")
        with self.assertRaisesRegex(ValueError, "COMMIT"):
            _verify_candidate_zip(
                self.blob,
                expected_archive_sha256=hashlib.sha256(self.blob).hexdigest(),
                expected_commit="b" * 40,
                expected_lock_sha256="a" * 64,
            )
        self.assertEqual(tuple(self.parent.iterdir()), ())

    def test_existing_output_cannot_be_replaced(self) -> None:
        self.stage()
        with self.assertRaisesRegex(ValueError, "ALREADY_EXISTS"):
            self.stage()
        self.assertTrue(self.candidate.is_dir())

    def test_partially_staged_candidate_blocks_no_silent_cleanup(self) -> None:
        temp = self.parent / (".stage-" + COMMIT)
        temp.mkdir()
        with self.assertRaisesRegex(ValueError, "ALREADY_EXISTS"):
            self.stage()
        self.assertTrue(temp.is_dir())
        self.assertFalse(self.candidate.exists())

    def test_mutating_fingerprint_after_preflight_stops_before_candidate(self) -> None:
        (self.venv / "bin/pip").write_bytes(b"changed\n")
        with self.assertRaisesRegex(ValueError, "VENV_FINGERPRINT"):
            self.stage()
        self.assertFalse(self.candidate.exists())

    def test_tampered_files_detected_by_readback(self) -> None:
        self.stage()
        f = self.candidate / ENTRY
        f.chmod(0o640)
        f.write_bytes(b"tampered\n")
        with self.assertRaises(ValueError):
            _readback_prepared_candidate(
                candidate_root=self.candidate,
                owner_uid=os.geteuid(),
                service_gid=os.getegid(),
                expected_commit=COMMIT,
                expected_archive_sha256=hashlib.sha256(self.blob).hexdigest(),
            )

    def test_no_arguments_production_gate_non_root(self) -> None:
        self.assertEqual(prepare_exit_new_component_once.__code__.co_argcount, 0)
        with (
            patch("component_release_exit_new_install.sys.platform", "linux"),
            patch("component_release_exit_new_install.os.geteuid",
                  return_value=107),
            self.assertRaisesRegex(ValueError, "ROOT_REQUIRED"),
        ):
            prepare_exit_new_component_once()

    def test_no_arguments_production_gate_windows(self) -> None:
        with (
            patch("component_release_exit_new_install.sys.platform", "win32"),
            self.assertRaisesRegex(ValueError, "LINUX_ONLY"),
        ):
            prepare_exit_new_component_once()


if __name__ == "__main__":
    unittest.main()
