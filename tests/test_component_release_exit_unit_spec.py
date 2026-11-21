"""#849 Exit-only pinned systemd unit proposal: pure, not an installer."""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

from component_release_exit_unit_spec import build_exit_unit_candidate  # noqa: E402

SOURCE_UNIT = ROOT / "operations/systemd/cripta-universal-exit-consumer.service"
SOURCE_COMMIT = "69c64c0dae722d9e655f7cb4ce103ba434e20054"
LOCK_SHA = "a" * 64


class ExitPinnedUnitContractTests(unittest.TestCase):
    def template(self) -> str:
        return SOURCE_UNIT.read_text(encoding="utf-8")

    def candidate(self) -> dict[str, object]:
        return build_exit_unit_candidate(
            source_unit=self.template(),
            component_commit=SOURCE_COMMIT,
            component_lock_sha256=LOCK_SHA,
        )

    def test_exit_unit_has_only_component_pinned_executable_identity(self) -> None:
        candidate = self.candidate()
        unit = candidate["unit_text"]
        root = (
            "/srv/cripta/runtime/components/exit/releases/"
            + SOURCE_COMMIT
        )
        self.assertEqual(candidate["component_id"], "EXIT")
        self.assertEqual(candidate["source_commit"], SOURCE_COMMIT)
        self.assertEqual(candidate["component_release_root"], root)
        self.assertEqual(candidate["component_lock_sha256"], LOCK_SHA)
        self.assertIn("WorkingDirectory=" + root, unit)
        self.assertIn(
            "ExecStart=" + root + "/.venv/bin/python "
            + root + "/operations/connectivity/universal_exit_consumer.py", unit,
        )
        self.assertIn(
            "Environment=PYTHONPATH=" + root + "/src:"
            + root + "/operations/connectivity", unit,
        )
        self.assertIn("Environment=CRIPTA_RELEASE_COMMIT=" + SOURCE_COMMIT, unit)
        self.assertIn("Environment=CRIPTA_RUNTIME_ROOT=" + root, unit)
        self.assertNotIn("/srv/cripta/runtime/current", unit)
        self.assertNotIn("EnvironmentFile=-/etc/cripta/release.env", unit)

    def test_sandbox_is_preserved_but_unit_cannot_be_installed(self) -> None:
        candidate = self.candidate()
        unit = candidate["unit_text"]
        for must in (
            "User=cripta", "Group=cripta",
            "NoNewPrivileges=true", "PrivateTmp=true",
            "ProtectHome=true", "ProtectSystem=strict",
            "StateDirectory=cripta-p90-r06-exit",
            "EnvironmentFile=-/etc/cripta/universal_exit_consumer.env",
        ):
            self.assertIn(must, unit)
        self.assertEqual(candidate["status"], "STAGED_ONLY_NOT_DEPLOYABLE")
        self.assertFalse(candidate["install_authorized"])
        self.assertFalse(candidate["unit_installed"])
        self.assertFalse(candidate["compatibility_attested"])
        self.assertFalse(candidate["external_environment_attested"])
        self.assertEqual(
            candidate["unit_sha256"],
            hashlib.sha256(unit.encode("utf-8")).hexdigest(),
        )

    def test_generated_unit_is_deterministic_and_source_unchanged(self) -> None:
        original = SOURCE_UNIT.read_bytes()
        self.assertEqual(self.candidate(), self.candidate())
        self.assertEqual(SOURCE_UNIT.read_bytes(), original)

    def test_unexpected_unit_template_and_effectful_extensions_block(self) -> None:
        template = self.template()
        for changed in (
            template.replace("User=cripta", "User=root", 1),
            template.replace("ProtectSystem=strict", "ProtectSystem=false", 1),
            template.replace(
                "ExecStart=/srv/cripta/runtime/current/.venv/bin/python",
                "ExecStart=/usr/bin/python3", 1,
            ),
            template.replace(
                "EnvironmentFile=-/etc/cripta/release.env",
                "EnvironmentFile=-/etc/cripta/other.env", 1,
            ),
            template.replace(
                "EnvironmentFile=-/etc/cripta/universal_exit_consumer.env",
                "EnvironmentFile=-/etc/cripta/unknown.env", 1,
            ),
            template + "ExecStartPre=/usr/bin/touch /tmp/unapproved\n",
            template.replace("StateDirectory=cripta-p90-r06-exit", "", 1),
        ):
            with self.subTest(changed=changed[:50]), self.assertRaises(ValueError):
                build_exit_unit_candidate(
                    source_unit=changed,
                    component_commit=SOURCE_COMMIT,
                    component_lock_sha256=LOCK_SHA,
                )

    def test_unknown_sha_or_hash_never_prepares_a_unit(self) -> None:
        for sha, digest in (
            ("../x", LOCK_SHA),
            ("g" * 40, LOCK_SHA),
            (SOURCE_COMMIT, "bad-lock"),
            (SOURCE_COMMIT, "f" * 63),
        ):
            with self.subTest(sha=sha, digest=digest), self.assertRaises(ValueError):
                build_exit_unit_candidate(
                    source_unit=self.template(),
                    component_commit=sha,
                    component_lock_sha256=digest,
                )


if __name__ == "__main__":
    unittest.main()
