"""Regression for exact P90 SUB WS-latch recovery: no Exchange/live mutations."""
from __future__ import annotations

import ast
import importlib.util

import pytest
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


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("p90_ws_recovery_cli_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("runtime_sha,state_sha,accepted", (
    ("a" * 40, "a" * 40, True),
    ("A" * 40, "a" * 40, False),
    ("a" * 40, "b" * 40, False),
    ("a" * 39, "a" * 39, False),
    ("a" * 40 + "\\Z", "a" * 40 + "\\Z", False),
))
def test_installed_commit_exact_identity_runtime_validation(
    monkeypatch, tmp_path, runtime_sha, state_sha, accepted
):
    module = _load_cli_module()
    runtime = tmp_path / "runtime"
    state = tmp_path / "state"
    runtime.write_text(runtime_sha + "\\n", encoding="utf-8")
    state.write_text(state_sha + "\\n", encoding="utf-8")
    real_path = Path
    paths = {
        "/srv/cripta/runtime/current/INSTALLED_COMMIT": runtime,
        "/var/lib/cripta/release/INSTALLED_COMMIT": state,
    }
    monkeypatch.setattr(module, "Path", lambda value: paths.get(str(value), real_path(value)))
    if accepted:
        assert module._installed_commit() == runtime_sha
    else:
        with pytest.raises(RuntimeError, match="P90_ACCOUNT_WS_RECOVERY_RELEASE_IDENTITY_UNVERIFIED"):
            module._installed_commit()


def test_recovery_cli_maps_known_and_unknown_failures_without_leaking(
    monkeypatch, capsys
):
    module = _load_cli_module()
    private_detail = "signedUid=SECRET-VALUE"
    for error, expected in (
        (RuntimeError("P90_ACCOUNT_WS_RECOVERY_RELEASE_IDENTITY_UNVERIFIED"),
         "P90_ACCOUNT_WS_RECOVERY_RELEASE_IDENTITY_UNVERIFIED"),
        (RuntimeError(private_detail), "P90_ACCOUNT_WS_RECOVERY_PROOF_OR_RUNTIME_ERROR"),
    ):
        def fail(reason=error):
            raise reason
        monkeypatch.setattr(module, "main", fail)
        with pytest.raises(SystemExit) as failure:
            module._main_with_safe_error()
        assert failure.value.code == 20
        stderr = capsys.readouterr().err
        assert "P90_ACCOUNT_WS_RECOVERY_ERROR=" + expected in stderr
        assert private_detail not in stderr
        assert "Traceback" not in stderr
