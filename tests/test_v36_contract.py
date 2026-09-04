from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_contract_loader() -> dict[str, str]:
    code = r'''
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "operations/monitoring"))
from entry_strategy_contract import ENTRY_V1_CORE_INITIAL_PROTECTION
print(json.dumps(ENTRY_V1_CORE_INITIAL_PROTECTION.payload_for(
    "entry_v1_core", "1.0-live-first-touch"
)))
'''
    env = os.environ.copy()
    env["CRIPTA_ENTRY_STRATEGY_CONFIG"] = str(
        ROOT / "config/live_strategies/entry_v1_core.json"
    )
    output = subprocess.check_output(
        [sys.executable, "-c", code, str(ROOT)], env=env, text=True
    )
    value = json.loads(output)
    assert isinstance(value, dict)
    return {str(key): str(item) for key, item in value.items()}


def test_strategy_config_owns_initial_protection() -> None:
    payload = _run_contract_loader()
    assert payload == {
        "strategy_id": "entry_v1_core",
        "strategy_version": "1.0-live-first-touch",
        "stop_loss_pct": "1.00",
        "take_profit_pct": "3.00",
        "trigger_by": "LastPrice",
        "tpsl_mode": "Full",
    }


def test_protection_math_has_no_strategy_defaults() -> None:
    sys.path.insert(0, str(ROOT / "operations/connectivity"))
    try:
        import protection_math  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    signature = inspect.signature(protection_math.calculate_initial_boundaries)
    assert signature.parameters["stop_loss_pct"].default is inspect.Parameter.empty
    assert signature.parameters["take_profit_pct"].default is inspect.Parameter.empty
    stop, target = protection_math.calculate_initial_boundaries(
        entry=Decimal("100"),
        side="Buy",
        tick=Decimal("0.01"),
        stop_loss_pct=Decimal("1.25"),
        take_profit_pct=Decimal("2.50"),
    )
    assert stop == Decimal("98.75")
    assert target == Decimal("102.50")


def test_entry_order_contains_initial_server_protection() -> None:
    text = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert '"stopLoss":str(stop)' in text
    assert '"takeProfit":str(target)' in text
    assert "initial_protection_contract(payload)" in text
    assert '"slOrderType":"Market"' in text
    assert '"tpOrderType":"Market"' in text


def test_executor_no_longer_decides_break_even_or_trailing() -> None:
    text = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "auto-be-" not in text
    assert "auto-trail-" not in text
    assert "BE/trailing/close decisions belong to Exit" in text


def test_supervisor_is_information_only() -> None:
    text = (ROOT / "operations/monitoring/position_supervisor.py").read_text(encoding="utf-8")
    assert "POSITION_SUPERVISOR_INFORMATION_ONLY_V36" in text
    assert "INSERT INTO runtime.trade_commands" not in text
    assert "REDUCE_ONLY_CLOSE" not in text


def test_exit_runtime_owns_post_fill_decisions_but_has_no_unproven_close_path() -> None:
    text = (ROOT / "operations/monitoring/exit_runtime.py").read_text(encoding="utf-8")
    assert "auto-be-" in text
    assert "auto-trail-" in text
    assert "STRUCTURAL_EARLY_EXIT_ENABLED = False" in text
    assert 'STRUCTURAL_BREAK_RULE = "NOT_PROVEN"' in text
    assert '"early_loss": "DISABLED_NOT_PROVEN_NO_CLOSE_IMPLEMENTATION"' in text
    assert "EARLY_LOSS_PREVENTION" not in text
    assert "m3-exit-" not in text


def test_scanner_persists_strategy_protection_in_immutable_handoff() -> None:
    text = (ROOT / "operations/monitoring/entry_shadow_scanner.py").read_text(encoding="utf-8")
    assert '"initial_protection": ENTRY_V1_CORE_INITIAL_PROTECTION.payload_for(' in text


def test_geometry_fail_closed_reason_preserves_existing_contract() -> None:
    text = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "нет причинной неизменяемой геометрии Entry" in text
    assert "immutable Entry handoff" in text


def test_superseded_p0_test_contract_matches_v36_authority() -> None:
    text = (ROOT / "tests/test_advisory_context_boundary.py").read_text(encoding="utf-8")
    assert "test_v36_strategy_owns_initial_protection_without_retuning_other_risk" in text
    assert "test_entry_and_risk_settings_are_not_changed_by_p0" not in text
    assert "stop_loss_pct: Decimal," in text
    assert "take_profit_pct: Decimal," in text
