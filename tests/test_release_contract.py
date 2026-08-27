import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractTests(unittest.TestCase):
    def test_release_versions_are_synchronized(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        init = (ROOT / "src" / "bybit_workbench" / "__init__.py").read_text(
            encoding="utf-8"
        )
        lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
        self.assertIn('version = "0.8.5"', pyproject)
        self.assertIn('__version__ = "0.8.5"', init)
        self.assertIn(
            'name = "bybit-strategy-workbench"\nversion = "0.8.5"',
            lock,
        )

    def test_pyinstaller_spec_is_windowed_one_file_without_upx(self) -> None:
        text = (ROOT / "bybit_workbench.spec").read_text(encoding="utf-8")
        self.assertIn('name="BybitStrategyWorkbench"', text)
        self.assertIn("console=False", text)
        self.assertIn("upx=False", text)
        self.assertIn('runtime_hooks=["scripts/release/pyinstaller_runtime_hook.py"]', text)
        self.assertNotIn("COLLECT(", text)

    def test_windows_release_gate_exercises_packaged_headless_and_gui(self) -> None:
        text = (ROOT / "scripts" / "release_windows.ps1").read_text(encoding="utf-8")
        self.assertIn('RUN_SOAK_TESTS = "1"', text)
        self.assertIn('"--headless"', text)
        self.assertIn('"--gui-smoke"', text)
        self.assertIn("PyInstaller", text)
        self.assertIn("package_release.py", text)
        self.assertIn("verify_release.py", text)
        self.assertIn("PASS 6 Windows release completed successfully.", text)

    def test_clean_windows_verifier_requires_only_release_bundle(self) -> None:
        text = (ROOT / "scripts" / "release" / "verify_clean_windows.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("Get-FileHash -Algorithm SHA256", text)
        self.assertIn('"--headless"', text)
        self.assertIn('"--gui-smoke"', text)
        self.assertNotIn("python", text.lower())


if __name__ == "__main__":
    unittest.main()
