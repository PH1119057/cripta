import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bybit_workbench.__main__ import _apply_mainnet_startup_defaults
from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.endpoint_preferences import (
    MainnetEndpointPreference,
    normalize_mainnet_endpoint,
    persistent_mainnet_endpoint,
)
from bybit_workbench.domain.types import AppMode


class MainnetEndpointPreferenceTests(unittest.TestCase):
    def test_save_load_and_normalize_supported_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MainnetEndpointPreference(Path(tmp) / "mainnet_endpoint.json")
            saved = store.save(" https://api.bybit.com/ ")
            self.assertEqual(saved, "https://api.bybit.com")
            self.assertEqual(store.load(), "https://api.bybit.com")
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "mainnet-endpoint-v1")

    def test_rejects_paths_insecure_and_unknown_hosts(self) -> None:
        for value in (
            "http://api.bybit.com",
            "https://api.bybit.com/v5/market/time",
            "https://example.com",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_mainnet_endpoint(value)

    def test_live_startup_resets_persisted_global_endpoint_to_kazakhstan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MainnetEndpointPreference(Path(tmp) / "mainnet_endpoint.json")
            store.save("https://api.bybit.com")

            settings = _apply_mainnet_startup_defaults(
                AppSettings(mode=AppMode.LIVE),
                store,
            )

            self.assertEqual(settings.endpoint_profile.rest_url, "https://api.bybit.kz")
            self.assertEqual(
                settings.endpoint_profile.public_ws_url,
                "wss://stream.bybit.kz/v5/public/linear",
            )
            self.assertEqual(
                settings.endpoint_profile.private_ws_url,
                "wss://stream.bybit.kz/v5/private",
            )
            self.assertEqual(store.load(), "https://api.bybit.kz")

    def test_explicit_live_rest_override_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MainnetEndpointPreference(Path(tmp) / "mainnet_endpoint.json")
            settings = _apply_mainnet_startup_defaults(
                AppSettings(
                    mode=AppMode.LIVE,
                    rest_url_override="https://api.bybit.com",
                ),
                store,
            )
            self.assertEqual(settings.endpoint_profile.rest_url, "https://api.bybit.com")
            self.assertIsNone(store.load())

    def test_windows_local_app_data_is_outside_project_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "LocalAppData"
            project = root / "project" / "var" / "mainnet_endpoint.json"
            project.parent.mkdir(parents=True)
            project.write_text(
                '{"schema":"mainnet-endpoint-v1","rest_url":"https://api.bybit.kz"}\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}, clear=False):
                store = persistent_mainnet_endpoint(project)
            self.assertEqual(
                store.path,
                local / "BybitStrategyWorkbench" / "mainnet_endpoint.json",
            )
            self.assertEqual(store.load(), "https://api.bybit.kz")


if __name__ == "__main__":
    unittest.main()
