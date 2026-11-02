"""Regression for exact P90 SUB WS-latch recovery: no Exchange/live mutations."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/implementation/recover_p90_account_ws_safety.py"
DASHBOARD = ROOT / "operations/dashboard/app.py"


def _regex_constants() -> dict[str, re.Pattern[str]]:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    patterns = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in {"_SHA", "_REQUEST"}:
            continue
        assert isinstance(node.value, ast.Call)
        assert isinstance(node.value.func, ast.Attribute)
        assert node.value.func.attr == "compile"
        assert len(node.value.args) == 1 and isinstance(node.value.args[0], ast.Constant)
        patterns[target.id] = re.compile(node.value.args[0].value)
    assert set(patterns) == {"_SHA", "_REQUEST"}
    return patterns


def test_release_sha_validator_accepts_exact_40_lowercase_hex_and_denies_others():
    sha = _regex_constants()["_SHA"]
    assert sha.fullmatch("67f2c4a1663659c979ffae4dc7a8d092660a1ad9")
    assert sha.fullmatch("a" * 40)
    for invalid in ("a" * 39, "a" * 41, "A" * 40, "g" * 40, "a" * 40 + "\\Z", ""):
        assert sha.fullmatch(invalid) is None


def test_request_id_validator_accepts_dashboard_owner_request_and_denies_injection():
    request = _regex_constants()["_REQUEST"]
    for valid in (
        "p90-owner-arm-1793641716616-47f8aef53bc3",
        "p90-owner-arm-1793641716616-47f8aef53bc3-ws-sub",
        "abcdefgh",
        "a" * 128,
    ):
        assert request.fullmatch(valid)
    for invalid in ("short", "a" * 129, "p90/owner", "a" * 8 + "\\n", "a" * 8 + " ", ""):
        assert request.fullmatch(invalid) is None


def test_recovery_fails_closed_with_bounded_sanitized_error_vocabulary():
    script = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(script)
    diagnostic = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_main_with_safe_error"
    )
    code = ast.get_source_segment(script, diagnostic)
    assert "except Exception as exc:" in code
    assert "reason in _DIAGNOSTIC_CODES" in code
    assert "isinstance(exc, psycopg.Error)" in code
    assert "P90_ACCOUNT_WS_RECOVERY_ERROR=" in code
    assert "raise SystemExit(20)" in code
    assert "str(exc)" in code  # accepted only after exact allowlist membership
    assert "print(str(exc)" not in code
    assert "traceback" not in code


def test_dashboard_recovery_failure_cannot_advance_to_owner_arm():
    app = DASHBOARD.read_text(encoding="utf-8")
    recovery = app[app.index("def _recover_p90_real_ws_safety_if_needed("):
                   app.index("def _u6_prepare_p90_real_v3_readiness(")]
    prepare = app[app.index("def _u6_prepare_p90_real_v3_readiness("):
                  app.index("def _u6_p90_real_v3_arm(")]
    arm = app[app.index("def _u6_p90_real_v3_arm("):
              app.index("def _u6_financial_account_catalog(")]
    assert 'code = "P90_ACCOUNT_WS_RECOVERY_UNCLASSIFIED"' in recovery
    assert "if candidate in safe_codes:" in recovery
    assert "result.stderr.splitlines()" in recovery
    assert '":code="' in recovery
    assert "raise RuntimeError(" in recovery
    assert prepare.index("ws_recovery = _recover_p90_real_ws_safety_if_needed(") < prepare.index("mark_real_readiness_ready(")
    assert arm.index('if preparation.get("state") != "READY_FOR_OWNER_ARM":') < arm.index("stage_p90l90_real_v3(")
    assert "exchange_mutation" in recovery
    assert "gate_mutation" in recovery
    assert "owner_arm" in recovery
