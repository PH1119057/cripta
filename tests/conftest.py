from __future__ import annotations

from pathlib import Path

import bybit_workbench

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PACKAGE = ROOT / "production" / "src" / "bybit_workbench"

if not PRODUCTION_PACKAGE.is_dir():
    raise RuntimeError(f"production package tree is missing: {PRODUCTION_PACKAGE}")

production_package_text = str(PRODUCTION_PACKAGE)
if production_package_text not in bybit_workbench.__path__:
    bybit_workbench.__path__.append(production_package_text)
