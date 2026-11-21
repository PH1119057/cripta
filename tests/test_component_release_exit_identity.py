"""Fail-first tests for #849 component-specific Exit identity (NO installer).

All snapshots are synthetic. A passing synthetic snapshot is never runtime proof.
"""
from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

from component_release_exit_identity import (  # noqa: E402
    check_exit_identity_snapshot,
    collect_exit_installed_file_evidence,
)
from component_release_exit_unit_spec import build_exit_unit_candidate  # noqa: E402

SHA = "a" * 40
PREVIOUS = "b" * 40
LOCK_SHA = "c" * 64
CONTENT_SHA = "d" * 64
ROOT_PATH = "/srv/cripta/runtime/components/exit/releases/" + SHA
ENTRY_SCRIPT = "operations/connectivity/universal_exit_consumer.py"
LOCK_PATH = "operations/infrastructure/exit_component_requirements.lock"
UNIT = "cripta-universal-exit-consumer.service"


def coherent() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """A coherent but *untrusted* bundle/host/process evidence fixture."""
    artifact: dict[str, object] = {
        "component": "EXIT",
        "target": SHA,
        "entrypoint": ENTRY_SCRIPT,
        "dependency_lock": LOCK_PATH,
        "dependency_lock_sha256": LOCK_SHA,
        "blob_sha256": {
            ENTRY_SCRIPT: CONTENT_SHA,
            LOCK_PATH: LOCK_SHA,
        },
        "status": "STAGED_ONLY_NOT_DEPLOYABLE",
        "install_authorized": False,
    }
    proposed_unit = build_exit_unit_candidate(
        source_unit=(
            ROOT / "operations/systemd/cripta-universal-exit-consumer.service"
        ).read_text(encoding="utf-8"),
        component_commit=SHA,
        component_lock_sha256=LOCK_SHA,
    )
    observed: dict[str, object] = {
        "installed_root": ROOT_PATH,
        "installed_marker": SHA,
        "installed_file_sha256": copy.deepcopy(artifact["blob_sha256"]),
        "installed_lock_sha256": LOCK_SHA,
        "installed_files_regular": True,
        "unit_name": UNIT,
        "unit_sha256": proposed_unit["unit_sha256"],
        "systemd_active_state": "active",
        "systemd_sub_state": "running",
        "pid": 2468,
        "pid_start_ticks": 123456,
        "process_unit": UNIT,
        "process_executable": ROOT_PATH + "/.venv/bin/python",
        "process_entrypoint": ROOT_PATH + "/" + ENTRY_SCRIPT,
        "process_working_directory": ROOT_PATH,
        "process_pythonpath": ROOT_PATH + "/src:" + ROOT_PATH + "/operations/connectivity",
        "loaded_release_commit": SHA,
        "loaded_runtime_root": ROOT_PATH,
        "observed_executable_under_installed_root": True,
        "rollback_commit": PREVIOUS,
        "rollback_immutable_ready": True,
        "neighbor_before": {"ENTRY": (127, 0), "MARKET_SOURCE": (128, 0)},
        "neighbor_after": {"ENTRY": (127, 0), "MARKET_SOURCE": (128, 0)},
    }
    return artifact, proposed_unit, observed


class ExitIdentityContractTests(unittest.TestCase):
    def verify(
        self, artifact: dict[str, object], unit: dict[str, object],
        observed: dict[str, object],
    ) -> dict[str, object]:
        return check_exit_identity_snapshot(
            artifact_manifest=artifact,
            proposed_unit=unit,
            observed=observed,
        )

    def test_coherent_snapshot_is_not_deployment_authorization(self) -> None:
        artifact, unit, observed = coherent()
        result = self.verify(artifact, unit, observed)
        self.assertEqual(result["state"], "OFFLINE_METADATA_CONSISTENCY_ONLY")
        self.assertEqual(result["component_id"], "EXIT")
        self.assertEqual(result["component_commit"], SHA)
        self.assertFalse(result["install_authorized"])
        self.assertFalse(result["runtime_behavior_verified"])
        self.assertFalse(result["live_financial_gate_verified"])
        self.assertFalse(result["exchange_protection_verified"])

    def test_moving_source_or_installed_sha_denied(self) -> None:
        for actor in ("artifact", "unit", "installed", "loaded"):
            artifact, unit, observed = coherent()
            if actor == "artifact":
                artifact["target"] = PREVIOUS
            elif actor == "unit":
                unit["source_commit"] = PREVIOUS
            elif actor == "installed":
                observed["installed_marker"] = PREVIOUS
            else:
                observed["loaded_release_commit"] = PREVIOUS
            with self.subTest(actor=actor), self.assertRaises(ValueError):
                self.verify(artifact, unit, observed)

    def test_missing_or_mutated_archive_blob_denied(self) -> None:
        for which in ("missing", "mutated", "additional", "lock"):
            artifact, unit, observed = coherent()
            files = observed["installed_file_sha256"]
            assert isinstance(files, dict)
            if which == "missing":
                del files[ENTRY_SCRIPT]
            elif which == "mutated":
                files[ENTRY_SCRIPT] = "f" * 64
            elif which == "additional":
                files["operations/connectivity/foreign_actor.py"] = "f" * 64
            else:
                observed["installed_lock_sha256"] = "f" * 64
            with self.subTest(which=which), self.assertRaises(ValueError):
                self.verify(artifact, unit, observed)

    def test_unapproved_actor_and_file_symlink_denied(self) -> None:
        for mutation in (
            ("installed_files_regular", False),
            ("unit_name", "cripta-universal-entry-consumer.service"),
            ("unit_sha256", "0" * 64),
            ("process_unit", "cripta-dashboard.service"),
            ("rollback_immutable_ready", False),
            ("rollback_commit", SHA),
        ):
            artifact, unit, observed = coherent()
            observed[mutation[0]] = mutation[1]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.verify(artifact, unit, observed)

    def test_stale_pid_wrong_executable_and_global_root_denied(self) -> None:
        for key, value in (
            ("systemd_active_state", "failed"),
            ("systemd_sub_state", "dead"),
            ("pid", 0),
            ("pid_start_ticks", 0),
            ("process_executable", "/srv/cripta/runtime/current/.venv/bin/python"),
            ("process_entrypoint",
             "/srv/cripta/runtime/current/operations/connectivity/universal_exit_consumer.py"),
            ("process_working_directory", "/srv/cripta/runtime/current"),
            ("process_pythonpath", "/srv/cripta/runtime/current/src"),
            ("loaded_runtime_root", "/srv/cripta/runtime/current"),
            ("observed_executable_under_installed_root", False),
        ):
            artifact, unit, observed = coherent()
            observed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(artifact, unit, observed)

    def test_untouched_neighbor_pid_or_restart_drift_denied(self) -> None:
        for name, entry in (
            ("ENTRY", (999, 0)),
            ("MARKET_SOURCE", (128, 1)),
        ):
            artifact, unit, observed = coherent()
            after = observed["neighbor_after"]
            assert isinstance(after, dict)
            after[name] = entry
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verify(artifact, unit, observed)

    def test_no_implicit_authorization_or_legacy_fallback(self) -> None:
        for where, key, value in (
            ("artifact", "install_authorized", True),
            ("unit", "install_authorized", True),
            ("artifact", "component", "ENTRY"),
            ("unit", "component_id", "ENTRY"),
            ("unit", "component_release_root", "/srv/cripta/runtime/current"),
            ("unit", "unit_text", "a different unreviewed unit"),
            ("artifact", "dependency_lock", "operations/runtime/runtime_requirements.lock"),
            ("artifact", "status", "DEPLOYED"),
        ):
            artifact, unit, observed = coherent()
            obj = artifact if where == "artifact" else unit
            obj[key] = value
            with self.subTest(where=where, key=key), self.assertRaises(ValueError):
                self.verify(artifact, unit, observed)

    def test_missing_required_observation_denied(self) -> None:
        _, unit, _ = coherent()
        for key in ("pid", "installed_marker", "installed_file_sha256",
                    "loaded_release_commit", "rollback_commit", "neighbor_before"):
            artifact, unit, observed = coherent()
            del observed[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(artifact, unit, observed)



class ExitInstalledByteProofTests(unittest.TestCase):
    @staticmethod
    def create_fake_installed(
        releases_parent: Path,
    ) -> tuple[dict[str, object], Path]:
        artifact, _proposal, _observed = coherent()
        root = releases_parent / SHA
        root.mkdir(parents=True)
        contents = {
            ENTRY_SCRIPT: b"immutable exit script\n",
            LOCK_PATH: b"one exact component lock\n",
        }
        artifact["blob_sha256"] = {
            name: hashlib.sha256(raw).hexdigest()
            for name, raw in contents.items()
        }
        artifact["dependency_lock_sha256"] = artifact["blob_sha256"][LOCK_PATH]
        for name, raw in contents.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (root / "COMPONENT_INSTALLED_COMMIT").write_bytes(
            (SHA + "\n").encode("ascii"),
        )
        return artifact, root

    def test_actual_bytes_and_independent_marker_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp) / "releases"
            parent.mkdir()
            artifact, root = self.create_fake_installed(parent)
            actual = collect_exit_installed_file_evidence(
                artifact_manifest=artifact, releases_parent=parent,
            )
            self.assertEqual(actual["installed_marker"], SHA)
            self.assertEqual(actual["installed_root"], str(root))
            self.assertEqual(
                actual["installed_file_sha256"], artifact["blob_sha256"],
            )
            self.assertEqual(
                actual["installed_lock_sha256"],
                artifact["dependency_lock_sha256"],
            )
            self.assertTrue(actual["installed_files_regular"])
            self.assertFalse(actual["runtime_behavior_verified"])
            self.assertFalse(actual["install_authorized"])

    def test_missing_or_modified_installed_file_is_rejected(self) -> None:
        for action in ("missing", "mutated", "missing_marker", "bad_marker"):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp) / "releases"
                parent.mkdir()
                artifact, root = self.create_fake_installed(parent)
                target = root / ENTRY_SCRIPT
                marker = root / "COMPONENT_INSTALLED_COMMIT"
                if action == "missing":
                    target.unlink()
                elif action == "mutated":
                    target.write_bytes(b"unreviewed bytes")
                elif action == "missing_marker":
                    marker.unlink()
                else:
                    marker.write_bytes((PREVIOUS + "\n").encode("ascii"))
                with self.subTest(action=action), self.assertRaises(ValueError):
                    collect_exit_installed_file_evidence(
                        artifact_manifest=artifact, releases_parent=parent,
                    )

    def test_symlinked_file_or_marker_is_rejected(self) -> None:
        for action in ("file", "marker"):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp) / "releases"
                parent.mkdir()
                artifact, root = self.create_fake_installed(parent)
                target = (
                    root / ENTRY_SCRIPT if action == "file"
                    else root / "COMPONENT_INSTALLED_COMMIT"
                )
                outside = Path(temp) / "untrusted"
                outside.write_bytes(target.read_bytes())
                target.unlink()
                try:
                    target.symlink_to(outside)
                except (OSError, NotImplementedError):
                    self.skipTest("symlink creation not permitted")
                with self.subTest(action=action), self.assertRaises(ValueError):
                    collect_exit_installed_file_evidence(
                        artifact_manifest=artifact, releases_parent=parent,
                    )

    def test_symlinked_component_release_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp) / "releases"
            parent.mkdir()
            artifact, root = self.create_fake_installed(parent)
            untrusted = parent / "untrusted-components"
            root.rename(untrusted)
            try:
                root.symlink_to(untrusted, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation not permitted")
            with self.assertRaises(ValueError):
                collect_exit_installed_file_evidence(
                    artifact_manifest=artifact, releases_parent=parent,
                )

    def test_untrusted_artifact_path_and_absent_release_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp) / "releases"
            parent.mkdir()
            artifact, _root = self.create_fake_installed(parent)
            files = artifact["blob_sha256"]
            assert isinstance(files, dict)
            files["../elsewhere"] = "f" * 64
            with self.assertRaises(ValueError):
                collect_exit_installed_file_evidence(
                    artifact_manifest=artifact, releases_parent=parent,
                )
            files.pop("../elsewhere")
            other_parent = Path(temp) / "not-installed"
            other_parent.mkdir()
            with self.assertRaises(ValueError):
                collect_exit_installed_file_evidence(
                    artifact_manifest=artifact, releases_parent=other_parent,
                )


if __name__ == "__main__":
    unittest.main()
