from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

from bybit_workbench.app.config import MAINNET_REST_URLS

_SCHEMA = "mainnet-endpoint-v1"


class MainnetEndpointPreference:
    """Persist the selected supported Mainnet REST endpoint without secrets."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> str | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA:
            return None
        value = payload.get("rest_url")
        if not isinstance(value, str):
            return None
        try:
            return normalize_mainnet_endpoint(value)
        except ValueError:
            return None

    def save(self, endpoint: str) -> str:
        normalized = normalize_mainnet_endpoint(endpoint)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"schema": _SCHEMA, "rest_url": normalized},
            ensure_ascii=False,
            indent=2,
        )
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(f"{payload}\n", encoding="utf-8")
        temporary.replace(self.path)
        return normalized


def persistent_mainnet_endpoint(project_var_path: Path) -> MainnetEndpointPreference:
    """Store UI connection preference outside the project tree on Windows when possible."""

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return MainnetEndpointPreference(project_var_path)
    target = Path(local_app_data) / "BybitStrategyWorkbench" / "mainnet_endpoint.json"
    if not target.exists() and project_var_path.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project_var_path, target)
        except OSError:
            return MainnetEndpointPreference(project_var_path)
    return MainnetEndpointPreference(target)


def normalize_mainnet_endpoint(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Mainnet endpoint must use https://")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Mainnet endpoint must be an API host without path/query")
    if normalized not in MAINNET_REST_URLS:
        supported = ", ".join(sorted(MAINNET_REST_URLS))
        raise ValueError(f"Unsupported Mainnet endpoint; supported: {supported}")
    return normalized
