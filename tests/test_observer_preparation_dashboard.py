import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_actual_presenter_explains_wait_and_terminal_block_without_error_label():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node required for actual dashboard presenter")
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    fn = next(x for x in html.splitlines() if x.startswith("function observerPreparationText("))
    script = fn + """
    const prep={symbol:'LTCUSDT',timeframe:'5',expected:'20:20',actual:'20:15',attempt:2};
    console.log(JSON.stringify({
      waiting:observerPreparationText({state:'PREPARING',preparation:prep}),
      blocked:observerPreparationText({state:'BLOCKED',preparation:{...prep,reason:'BUDGET'}})
    }));
    """
    result = subprocess.run([node, "-e", script], check=True, capture_output=True, encoding="utf-8")
    data = json.loads(result.stdout)
    assert "ожидайте / NOT READY" in data["waiting"]
    assert "LTCUSDT 5m" in data["waiting"] and "20:15" in data["waiting"]
    assert "ошиб" not in data["waiting"].lower()
    assert "приостановлена" in data["blocked"] and "BUDGET" in data["blocked"]
