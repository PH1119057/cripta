"""#849 real read-only Exit process identity evidence, test-first.

All positive samples are synthetic and do NOT prove actual production behavior.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/infrastructure"))

from component_release_exit_process_readback import (  # noqa: E402
    collect_exit_process_readback,
)

SHA = "a" * 40
SOURCE_COMPAT_COMMIT = "8bd45e9874d6ce811d7261ee152fc2c43729e1b9"
ROOT_PATH = "/srv/cripta/components/exit/releases/" + SHA
PID = 2468
UNIT = "cripta-universal-exit-consumer.service"
ENTRY = "operations/connectivity/universal_exit_consumer.py"


def show(*, pid: int = PID, workdir: str = ROOT_PATH) -> str:
    return "\n".join((
        "Id=" + UNIT,
        "ActiveState=active",
        "SubState=running",
        "MainPID=" + str(pid),
        "NRestarts=0",
        "NeedDaemonReload=no",
        "WorkingDirectory=" + workdir,
        "",
    ))


@unittest.skipUnless(os.name == "posix", "Linux proc symlinks required")
class ExitProcessReadbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name)
        self.process = self.proc / str(PID)
        self.process.mkdir()
        (self.process / "cmdline").write_bytes((
            ROOT_PATH + "/.venv/bin/python\0"
            + ROOT_PATH + "/" + ENTRY + "\0"
        ).encode("utf-8"))
        environment = (
            "CRIPTA_RELEASE_COMMIT=" + SOURCE_COMPAT_COMMIT + "\0"
            + "CRIPTA_EXIT_COMPONENT_COMMIT=" + SHA + "\0"
            + "CRIPTA_RUNTIME_ROOT=" + ROOT_PATH + "\0"
            + "PYTHONPATH=" + ROOT_PATH + "/src:"
            + ROOT_PATH + "/operations/connectivity\0"
            + "UNRELATED_SECRET=must-not-leak\0"
        )
        (self.process / "environ").write_bytes(environment.encode("utf-8"))
        # Linux proc stat field 22 = process start time ticks. After the
        # parenthesized comm, field3 starts at token zero, so ticks at 19.
        fields = ["S"] + ["0"] * 19
        fields[19] = "123456"
        (self.process / "stat").write_text(
            str(PID) + " (python) " + " ".join(fields) + "\n",
            encoding="utf-8",
        )
        (self.process / "cwd").symlink_to(ROOT_PATH)
        (self.process / "exe").symlink_to("/usr/bin/python3.12")

    def read(self, systemd: str | None = None) -> dict[str, object]:
        return collect_exit_process_readback(
            systemd_show=show() if systemd is None else systemd,
            expected_commit=SHA,
            expected_source_commit=SOURCE_COMPAT_COMMIT,
            proc_parent=self.proc,
        )

    def test_positive_snapshot_is_only_readonly_not_live_authorized(self) -> None:
        result = self.read()
        self.assertEqual(result["state"], "READ_ONLY_PROCESS_CONSISTENCY_ONLY")
        self.assertEqual(result["pid"], PID)
        self.assertEqual(result["pid_start_ticks"], 123456)
        self.assertEqual(result["loaded_release_commit"], SOURCE_COMPAT_COMMIT)
        self.assertEqual(result["loaded_exit_component_commit"], SHA)
        self.assertNotEqual(result["loaded_release_commit"],
                            result["loaded_exit_component_commit"])
        self.assertEqual(result["loaded_runtime_root"], ROOT_PATH)
        self.assertEqual(result["process_unit"], UNIT)
        self.assertFalse(result["install_authorized"])
        self.assertFalse(result["runtime_behavior_verified"])
        self.assertFalse(result["signed_exchange_protection_verified"])
        self.assertNotIn("must-not-leak", str(result))
        self.assertNotIn("UNRELATED_SECRET", str(result))

    def test_wrong_unit_stale_pid_active_state_or_reload_denied(self) -> None:
        cases = (
            show().replace("Id=" + UNIT, "Id=cripta-universal-entry-consumer.service"),
            show(pid=999),
            show().replace("ActiveState=active", "ActiveState=failed"),
            show().replace("SubState=running", "SubState=dead"),
            show().replace("NeedDaemonReload=no", "NeedDaemonReload=yes"),
            show().replace("NRestarts=0", "NRestarts=-1"),
            show(workdir="/srv/cripta/runtime/current"),
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read(value)

    def test_legacy_shared_executable_cannot_claim_independent_exit(self) -> None:
        cmd = self.process / "cmdline"
        cmd.write_bytes(
            b"/srv/cripta/runtime/current/.venv/bin/python\0"
            b"/srv/cripta/runtime/current/operations/connectivity/"
            b"universal_exit_consumer.py\0"
        )
        with self.assertRaisesRegex(ValueError, "LEGACY_SHARED_RUNTIME"):
            self.read()

    def test_missing_environment_and_mismatched_commit_fail_closed(self) -> None:
        env = self.process / "environ"
        original = env.read_bytes()
        for changed in (
            original.replace(SHA.encode(), ("b" * 40).encode()),
            original.replace(b"CRIPTA_RELEASE_COMMIT", b"OTHER_RELEASE_COMMIT"),
            original.replace(b"CRIPTA_EXIT_COMPONENT_COMMIT",
                             b"OTHER_EXIT_COMPONENT_COMMIT"),
            original.replace(SOURCE_COMPAT_COMMIT.encode(),
                             ("c" * 40).encode()),
            original.replace(
                b"CRIPTA_RUNTIME_ROOT", b"NOT_CRIPTA_RUNTIME_ROOT",
            ),
            original.replace(b"/src:", b"/runtime/current:"),
        ):
            env.write_bytes(changed)
            with self.assertRaises(ValueError):
                self.read()
        env.write_bytes(original)

    def test_missing_or_malformed_proc_files_fail_closed(self) -> None:
        stat = self.process / "stat"
        saved = stat.read_bytes()
        stat.write_bytes(b"NOT A PROC STAT")
        with self.assertRaises(ValueError):
            self.read()
        stat.write_bytes(saved)
        env = self.process / "environ"
        env.unlink()
        with self.assertRaises(ValueError):
            self.read()

    def test_wrong_cwd_or_exe_denied(self) -> None:
        cwd = self.process / "cwd"
        cwd.unlink()
        cwd.symlink_to("/srv/cripta/runtime/current")
        with self.assertRaises(ValueError):
            self.read()
        cwd.unlink()
        cwd.symlink_to(ROOT_PATH)
        exe = self.process / "exe"
        exe.unlink()
        exe.symlink_to("/untrusted/python")
        with self.assertRaises(ValueError):
            self.read()

    def test_unknown_expected_commit_and_duplicate_show_denied(self) -> None:
        with self.assertRaises(ValueError):
            collect_exit_process_readback(
                systemd_show=show(), expected_commit="../x",
                expected_source_commit=SOURCE_COMPAT_COMMIT,
                proc_parent=self.proc,
            )
        with self.assertRaises(ValueError):
            self.read(show() + "MainPID=" + str(PID) + "\n")


    def test_mismatched_source_identity_fails_even_with_correct_executable(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "SOURCE"):
            collect_exit_process_readback(
                systemd_show=show(), expected_commit=SHA,
                expected_source_commit="g" * 40,
                proc_parent=self.proc,
            )
        with self.assertRaises(ValueError):
            collect_exit_process_readback(
                systemd_show=show(), expected_commit=SHA,
                expected_source_commit="b" * 40,
                proc_parent=self.proc,
            )

    def test_pid_does_not_disappear_or_change_on_readback(self) -> None:
        original = (self.process / "stat").read_text()
        (self.process / "stat").write_text(original.replace("123456", "0"))
        with self.assertRaises(ValueError):
            self.read()


if __name__ == "__main__":
    unittest.main()
