"""#849 source-only noarg Release Controller sandbox for immutable Exit root.

No service installation, filesystem mutation or operator authorization.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "operations/systemd/cripta-release-controller.service"
SOURCE = ROOT / "operations/infrastructure/cripta-release-controller"
COMPONENT_PARENT = "/srv/cripta/components"


def _unit_lines() -> list[str]:
    return UNIT.read_text(encoding="utf-8").splitlines()


def _expected_rw_paths() -> set[str]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    declaration = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "EXPECTED_RW_PATHS"
            for target in node.targets
        )
    )
    values = ast.literal_eval(declaration.value)
    if not isinstance(values, set):
        raise AssertionError("controller set source changed unexpectedly")
    return values


class ExitControllerSandboxTests(unittest.TestCase):
    def test_unit_only_adds_optional_scoped_component_parent(self) -> None:
        lines = _unit_lines()
        self.assertEqual(lines.count(
            "ReadWritePaths=-" + COMPONENT_PARENT,
        ), 1)
        self.assertNotIn("ReadWritePaths=/srv/cripta", lines)
        self.assertNotIn("ReadWritePaths=/srv", lines)
        self.assertNotIn("ReadWritePaths=/", lines)
        self.assertNotIn("ReadWritePaths=/srv/cripta/components/exit", lines)
        self.assertNotIn("ReadWritePaths=/srv/cripta/components/*", lines)
        self.assertNotIn("ReadWritePaths=+/srv/cripta/components", lines)

    def test_controller_declares_exact_sandbox_mount(self) -> None:
        paths = _expected_rw_paths()
        self.assertIn(COMPONENT_PARENT, paths)
        self.assertNotIn("/srv/cripta", paths)
        self.assertNotIn("/srv", paths)
        self.assertNotIn("/", paths)
        self.assertIn("/srv/cripta/runtime", paths)
        self.assertIn("/etc/systemd/system", paths)
        self.assertIn("/run/lock", paths)

    def test_existing_released_root_actor_sandbox_preserved(self) -> None:
        lines = _unit_lines()
        for required in (
            "Type=oneshot",
            "User=root", "Group=root", "UMask=0077",
            "ProtectSystem=strict", "NoNewPrivileges=yes",
            "PrivateTmp=yes", "PrivateDevices=yes",
            "ProtectHome=yes", "RestrictSUIDSGID=yes",
            "ExecStart=/usr/bin/python3 "
            "/usr/local/libexec/cripta-release-controller/cripta-release-controller",
            "ReadWritePaths=/srv/cripta/runtime",
            "ReadWritePaths=/srv/cripta/source_checkout",
            "ReadWritePaths=/var/lib/cripta/release-controller",
        ):
            with self.subTest(required=required):
                self.assertEqual(lines.count(required), 1)

    def test_exact_source_is_not_an_install_permission(self) -> None:
        # The unit is a reviewed source blueprint. A future root installer
        # must create/protect the path and independently attest the runtime.
        lines = _unit_lines()
        self.assertNotIn("ExecStartPre=/usr/bin/mkdir", lines)
        self.assertNotIn("ReadWritePaths=/srv/cripta/components", lines)
        self.assertTrue(
            any(line.startswith("ConditionPathExists=") for line in lines)
        )


if __name__ == "__main__":
    unittest.main()
