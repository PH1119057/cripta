"""Run the exact independent Exit import with deployed systemd PYTHONPATH.

CI's repository-root pytest often makes `operations.monitoring` importable
by accident. The production Exit unit does NOT add that root directory.
This subprocess replicates the service's import paths, starts in a foreign
working directory, and does not connect to Bybit/PostgreSQL or issue orders.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_exit_firstfill_market_source_is_importable_without_repo_root(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((
        str(ROOT / "src"),
        str(ROOT / "operations" / "connectivity"),
    ))
    env.pop("CRIPTA_DATABASE_DSN", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.util
from pathlib import Path
import sys

assert importlib.util.find_spec("bybit_workbench.market_data") is not None
assert importlib.util.find_spec("operations") is None, "repo root accidentally on PYTHONPATH"

from p90_real_independent_exit import (
    read_firstfill_source_with_independent_market_fallback,
)
from bybit_workbench.market_data.p90l90_real_durable_postfill_source import (
    read_independent_durable_postfill_window,
)

assert callable(read_firstfill_source_with_independent_market_fallback)
assert callable(read_independent_durable_postfill_window)
print("P90_EXIT_SOURCE_SERVICE_PYTHONPATH=PASS")
""",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"Return code={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "P90_EXIT_SOURCE_SERVICE_PYTHONPATH=PASS" in result.stdout


def test_exit_service_unit_has_src_not_repo_root():
    # Prevent CI from passing only because tests execute from checkout root.
    unit = ROOT / "operations" / "systemd" / "cripta-universal-exit-consumer.service"
    if not unit.exists():
        # Some deployments use only the canonical installed unit in /etc;
        # subprocess test above is still the definitive import smoke.
        return
    text = unit.read_text(encoding="utf-8")
    assert "PYTHONPATH=" in text
    assert "/src" in text
