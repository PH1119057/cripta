"""New post-stop maker probe: pure execution arithmetic and tick direction tests."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SMOKE = """
from decimal import Decimal as D
from scripts.research.p90l90_protected_stop_maker_probe_v1 import matches, financial

assert matches('LONG', D('101'), D('100'))
assert not matches('LONG', D('99'), D('100'))
assert matches('SHORT', D('99'), D('100'))
assert not matches('SHORT', D('101'), D('100'))
assert (financial('LONG', D('100'), D('101'), True, D('0.0055'))
        > financial('LONG', D('100'), D('101'), False, D('0.0055')))
assert (financial('SHORT', D('100'), D('99'), True, D('0.0055'))
        > financial('SHORT', D('100'), D('99'), False, D('0.0055')))
print('PROTECTED_STOP_MAKER_PURE_PASS')
"""


def test_isolated_maker_probe_arithmetic():
    result = subprocess.run(
        [sys.executable, "-c", SMOKE], cwd=ROOT,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROTECTED_STOP_MAKER_PURE_PASS" in result.stdout
