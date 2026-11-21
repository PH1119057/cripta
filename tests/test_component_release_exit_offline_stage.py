"""#849 bounded OFFLINE Exit candidate extraction only (never an installer)."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

from component_release_exit_offline_stage import (  # noqa: E402
    stage_exit_bundle_offline,
)

SHA = "69c64c0dae722d9e655f7cb4ce103ba434e20054"
ENTRY = "operations/connectivity/universal_exit_consumer.py"
LOCK = "operations/infrastructure/exit_component_requirements.lock"
SIBLINGS = (
    "operations/connectivity/p90_real_independent_exit.py",
    "operations/connectivity/p90_real_persistent_trail.py",
    "operations/connectivity/p90_r06_exit_owner.py",
    "operations/connectivity/p90l90_real_postfill_source_client.py",
)


def bundle_bytes(
    *, mismatch: str = "", mode_override: int | None = None,
    missing: str | None = None, extra: str | None = None,
    duplicate: bool = False,
) -> bytes:
    source: dict[str, bytes] = {
        ENTRY: b"EXIT_SCRIPT",
        LOCK: b"LOCKED_WHEELS",
        "src/bybit_workbench/__init__.py": b"",
    }
    source.update({sibling: sibling.encode("ascii") for sibling in SIBLINGS})
    source.pop(missing, None)
    files = {name: hashlib.sha256(raw).hexdigest() for name, raw in source.items()}
    manifest = {
        "schema_version": 1, "component": "EXIT", "target": SHA,
        "entrypoint": ENTRY, "dependency_lock": LOCK,
        "dependency_lock_sha256": files.get(LOCK, "0" * 64),
        "dynamic_import_siblings": list(SIBLINGS),
        "blob_sha256": files, "install_authorized": False,
        "status": "STAGED_ONLY_NOT_DEPLOYABLE",
    }
    if mismatch == "digest":
        manifest["blob_sha256"][ENTRY] = "f" * 64
    if mismatch == "authorization":
        manifest["install_authorized"] = True
    if mismatch == "legacy_lock":
        manifest["dependency_lock"] = "operations/runtime/runtime_requirements.lock"
    if mismatch == "wrong_sha":
        manifest["target"] = "b" * 40
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in source.items():
            item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            item.external_attr = (
                (mode_override if mode_override is not None else 0o100644)
                << 16
            )
            out.writestr(item, data)
        if extra is not None:
            item = zipfile.ZipInfo(extra)
            item.external_attr = 0o100644 << 16
            out.writestr(item, b"unreviewed")
        if duplicate:
            item = zipfile.ZipInfo(ENTRY)
            item.external_attr = 0o100644 << 16
            out.writestr(item, source[ENTRY])
        out.writestr(
            "component_manifest.json",
            json.dumps(manifest, sort_keys=True).encode("utf-8"),
        )
    return bio.getvalue()


class OfflineExitSourceTreeTests(unittest.TestCase):
    def stage(
        self, root: Path, raw: bytes, *, expected_sha: str = SHA,
    ) -> dict[str, object]:
        archive = root / "candidate.zip"
        archive.write_bytes(raw)
        return stage_exit_bundle_offline(
            bundle_zip=archive, sandbox_parent=root, expected_sha=expected_sha,
        )

    def test_verified_bundle_materializes_exact_read_only_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            result = self.stage(parent, bundle_bytes())
            staged = parent / ("exit_" + SHA)
            self.assertEqual(result["status"], "OFFLINE_TREE_NOT_DEPLOYABLE")
            self.assertEqual(result["staged_tree"], str(staged))
            self.assertFalse(result["install_authorized"])
            self.assertFalse(result["immutable_root_attested"])
            self.assertFalse(result["component_loaded"])
            self.assertEqual(
                (staged / "OFFLINE_TREE_SOURCE_COMMIT").read_bytes(),
                (SHA + "\n").encode("ascii"),
            )
            self.assertFalse((staged / "COMPONENT_INSTALLED_COMMIT").exists())
            self.assertFalse((staged / "COMPONENT_LOADED_COMMIT").exists())
            self.assertEqual((staged / LOCK).read_bytes(), b"LOCKED_WHEELS")
            self.assertEqual((staged / ENTRY).read_bytes(), b"EXIT_SCRIPT")
            self.assertEqual(result["file_count"], 7)
            if os.name == "posix":
                self.assertEqual(
                    stat.S_IMODE((staged / ENTRY).stat().st_mode), 0o444,
                )

    def test_no_overwrite_of_other_staged_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            preserved = parent / ("exit_" + SHA)
            preserved.mkdir()
            old = preserved / "important"
            old.write_bytes(b"EXISTING")
            with self.assertRaises(ValueError):
                self.stage(parent, bundle_bytes())
            self.assertEqual(old.read_bytes(), b"EXISTING")
            self.assertFalse(
                any(p.name.startswith(".exit-offline-") for p in parent.iterdir())
            )

    def test_bad_hash_wrong_target_or_financial_flag_denied(self) -> None:
        for mismatch in ("digest", "wrong_sha", "authorization", "legacy_lock"):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                with self.subTest(mismatch=mismatch), self.assertRaises(ValueError):
                    self.stage(parent, bundle_bytes(mismatch=mismatch))
                self.assertFalse((parent / ("exit_" + SHA)).exists())

    def test_zip_traversal_foreign_file_or_duplicates_denied(self) -> None:
        for extra, duplicate in (
            ("../outside.txt", False),
            ("/etc/cripta/fake", False),
            ("operations\\connectivity\\bad.py", False),
            ("operations/connectivity/other_owner.py", False),
            ("operations/runtime/runtime_requirements.lock", False),
            (None, True),
        ):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                with self.subTest(extra=extra, duplicate=duplicate), self.assertRaises(ValueError):
                    self.stage(parent, bundle_bytes(
                        extra=extra, duplicate=duplicate,
                    ))
                self.assertFalse((parent / ("exit_" + SHA)).exists())

    def test_symlink_and_incomplete_dynamic_closure_denied(self) -> None:
        for mode, missing in (
            (0o120777, None),
            (None, SIBLINGS[-1]),
            (None, LOCK),
        ):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                with self.subTest(mode=mode, missing=missing), self.assertRaises(ValueError):
                    self.stage(parent, bundle_bytes(
                        mode_override=mode, missing=missing,
                    ))

    def test_missing_or_symlinked_sandbox_parent_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            archive = parent / "candidate.zip"
            archive.write_bytes(bundle_bytes())
            absent = parent / "missing"
            with self.assertRaises(ValueError):
                stage_exit_bundle_offline(
                    bundle_zip=archive, sandbox_parent=absent,
                    expected_sha=SHA,
                )
            link = parent / "linked"
            try:
                link.symlink_to(parent, target_is_directory=True)
            except (OSError, NotImplementedError):
                return
            with self.assertRaises(ValueError):
                stage_exit_bundle_offline(
                    bundle_zip=archive, sandbox_parent=link,
                    expected_sha=SHA,
                )

    def test_cannot_stage_to_production_software_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            archive = parent / "candidate.zip"
            archive.write_bytes(bundle_bytes())
            for forbidden in (
                "/srv/cripta/components/exit/releases",
                "/srv/cripta/runtime/releases",
                "/var/lib/cripta",
                "/etc/cripta",
            ):
                with self.subTest(path=forbidden), self.assertRaises(ValueError):
                    stage_exit_bundle_offline(
                        bundle_zip=archive,
                        sandbox_parent=Path(forbidden),
                        expected_sha=SHA,
                    )

    def test_wrong_expected_sha_does_not_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            with self.assertRaises(ValueError):
                self.stage(parent, bundle_bytes(), expected_sha="c" * 40)
            self.assertFalse((parent / ("exit_" + SHA)).exists())


if __name__ == "__main__":
    unittest.main()
