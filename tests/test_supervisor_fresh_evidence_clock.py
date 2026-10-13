"""Guard the authenticated Bybit protection-evidence clock ordering."""
from pathlib import Path

SOURCE = Path("operations/monitoring/lifecycle_supervisor_runtime.py").read_text(encoding="utf-8")


def test_protection_verdict_uses_timestamp_after_authenticated_get() -> None:
    scan = SOURCE.split("def main() -> int:", 1)[1]
    start = scan.index("while running:")
    loop = scan[start:]
    read = loop.index("account = _independent_exchange_snapshot(symbols)")
    updated_clock = loop.index("now = datetime.now(UTC)", read)
    verify = loop.index("protection = account_protection_verdicts(", updated_clock)
    persist = loop.index("result = scan_once(", verify)
    assert read < updated_clock < verify < persist
    assert "now=now" in loop[verify:persist]
    assert "now=now" in loop[persist:persist + 150]


def test_supervisor_preserves_exact_exchange_protection_evidence() -> None:
    bridge = Path(
        "src/bybit_workbench/exchange/bybit/protection_supervisor_bridge.py"
    ).read_text(encoding="utf-8")
    evaluator = Path(
        "src/bybit_workbench/exchange/bybit/protection_evidence.py"
    ).read_text(encoding="utf-8")
    assert "verify_approved_exchange_protection(" in bridge
    assert "expected_tp_order_id" in evaluator
    assert 'return "UNKNOWN", "Exchange account observation is stale"' in evaluator
