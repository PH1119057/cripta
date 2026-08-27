import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Pass7ContractTests(unittest.TestCase):
    def test_acceptance_runner_has_no_write_transport_dependency(self) -> None:
        source = (ROOT / "src/bybit_workbench/exchange/bybit/acceptance.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("write_transport", source)
        self.assertNotIn("MainnetMutationGateway", source)
        self.assertNotIn("place_order", source)

    def test_windows_acceptance_forces_live_switch_off(self) -> None:
        script = (ROOT / "scripts/accept_mainnet_windows.ps1").read_text(encoding="utf-8")
        self.assertIn('$env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = "0"', script)
        self.assertIn('$env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = "0"', script)
        self.assertIn("--mainnet-acceptance", script)
        self.assertNotIn("--allow-live", script)

    def test_acceptance_report_version_tracks_current_workbench(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        init = (ROOT / "src/bybit_workbench/__init__.py").read_text(encoding="utf-8")
        self.assertIn('version = "0.8.5"', pyproject)
        self.assertIn('__version__ = "0.8.5"', init)



if __name__ == "__main__":
    unittest.main()
