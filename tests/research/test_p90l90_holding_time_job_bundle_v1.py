"""Offline tests for the independent #646 job-intake ZIP contract."""
from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import unittest
import zipfile
from pathlib import Path

from research.server.jobs import intake
from scripts.research import p90l90_holding_time_job_bundle_v1 as pack

REPO = Path(__file__).resolve().parents[2]
TEST_HEAD = "2d0c1fd2203da8308ed5f49a43705c3e94c79764"


class IndependentJobBundleTests(unittest.TestCase):
    def test_all_six_cases_match_existing_intake_contract(self):
        for period in ("29d", "90d", "183d"):
            for phase in ("ALL", "NEG"):
                with self.subTest(period=period, phase=phase):
                    ident = {"29d": "H29", "90d": "H90", "183d": "H25"}[period]
                    job_id = f"C2-R646-{ident}-{phase}-V1-20261114"
                    body, manifest = pack.build_bundle(
                        REPO, period, phase, job_id, TEST_HEAD,
                    )
                    self.assertEqual(
                        intake.validate_manifest(manifest), manifest,
                    )
                    self.assertLess(len(body), intake.MAX_UNPACKED)
                    with zipfile.ZipFile(io.BytesIO(body)) as archive:
                        intake.inspect_archive(archive)
                        self.assertEqual(sorted(archive.namelist()), [
                            "job.json", "job_launcher.py",
                            "source_snapshot/"
                            + pack.SOURCE_BY_PERIOD[period],
                        ])
                        receipt = json.loads(archive.read("job.json"))
                        self.assertEqual(receipt, manifest)
                        frozen = archive.read(
                            "source_snapshot/" + pack.SOURCE_BY_PERIOD[period])
                        self.assertEqual(
                            hashlib.sha256(frozen).hexdigest(),
                            manifest["parameters"]["source_sha256"],
                        )
                        self.assertEqual(
                            frozen, (REPO / "scripts" / "research"
                                     / pack.SOURCE_BY_PERIOD[period]).read_bytes(),
                        )
                        launcher = archive.read("job_launcher.py").decode()
                        self.assertIsInstance(ast.parse(launcher), ast.Module)
                        self.assertIn("runpy.run_path", launcher)
                        self.assertIn("source_git_head", str(manifest))
                        self.assertIn("CRIPTA_JOB_ID", launcher)
                        self.assertNotIn("subprocess.", launcher)
                        self.assertNotIn("systemd-run", launcher)
                    self.assertEqual(body, pack.build_bundle(
                        REPO, period, phase, job_id, TEST_HEAD)[0])

    def test_period_mismatch_and_invalid_identity_rejected(self):
        for period, ident in (("29d", "H90"),
                              ("90d", "H25"), ("183d", "H29")):
            with self.subTest(period=period):
                with self.assertRaisesRegex(ValueError, "another period"):
                    pack.validate_identity(
                        period, "ALL", f"C2-R646-{ident}-ALL-V1",
                        TEST_HEAD,
                    )
        for wrong in ("C2-R635-H29-ALL", "C2-R646-H29-",
                      "C2-R646-H29-ALL;rm"):
            with self.subTest(job_id=wrong):
                with self.assertRaises(ValueError):
                    pack.validate_identity("29d", "ALL", wrong, TEST_HEAD)
        for head in ("main", "0" * 39, "G" * 40):
            with self.subTest(head=head):
                with self.assertRaises(ValueError):
                    pack.validate_identity("29d", "ALL",
                                           "C2-R646-H29-ALL-V1", head)

    def test_no_runtime_or_service_mutation_from_builder(self):
        source = (REPO / "scripts" / "research"
                  / "p90l90_holding_time_job_bundle_v1.py").read_text()
        tree = ast.parse(source)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("Popen(", source)
        self.assertNotIn("sudo", source)
        self.assertNotIn('ROOT / "queued"', source)
        self.assertNotIn("start_service", source)
        self.assertIn("PACKAGE_PREPARED_NOT_SUBMITTED_NOT_RUNNING", source)
        self.assertIn("os.geteuid()", source)
        self.assertIn("os.statvfs(", source)
        self.assertTrue(any(isinstance(node, ast.FunctionDef)
                            and node.name == "prepare" for node in tree))

    def test_manifest_covers_exact_no_cap_and_time_modes(self):
        for phase in pack.PHASES:
            _, manifest = pack.build_bundle(
                REPO, "29d", phase,
                f"C2-R646-H29-{phase}-V1-20261114", TEST_HEAD,
            )
            params = manifest["parameters"]
            self.assertEqual(
                params["time_limits_hours"],
                [None, 3, 6, 12, 18, 24, 36, 48, 72],
            )
            self.assertTrue(params["no_cap_original_ledger_parity_required"])
            self.assertEqual(params["native_exit"],
                             "original RAW L5-3 structural TP")
            self.assertEqual(manifest["timeout_seconds"], 172800)


if __name__ == "__main__":
    unittest.main()
