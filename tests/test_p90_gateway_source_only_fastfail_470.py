"""Regression: an exact P90 source-only Exit block must terminate the wizard.

No database, Bybit, Exchange, gateway operation or service is invoked.
Only the actual production readiness-wait function is executed with inert ports.
"""
import ast
import datetime
import pathlib
import shutil
import subprocess
import types

import pytest


APP = pathlib.Path("operations/dashboard/app.py")
HTML = pathlib.Path("operations/dashboard/index.html")
SOURCE_ONLY = (
    "P90 REAL v3 PREARM: source-only observer not exact/fresh: "
    "SOURCE_REPAIR_ONLY: P90_OPEN_EXIT_BLOCKED_MARKET_SOURCE_INGEST_ONLY"
)


class P90PrearmEvidenceBlocked(ValueError):
    pass


class P90PrivateIpcUnverified(ValueError):
    pass


def _real_wait_function() -> types.ModuleType:
    """Compile source AST without importing the network-enabled Dashboard."""
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    statements = []
    for node in tree.body:
        selected_fn = (
            isinstance(node, ast.FunctionDef)
            and node.name == "_wait_p90_real_v3_technical_evidence"
        )
        selected_prefix = (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "P90_REAL_READINESS_SOURCE_REPAIR_ONLY_PREFIX"
                for target in node.targets
            )
        )
        if selected_fn or selected_prefix:
            statements.append(node)
    assert len(statements) == 2
    module = types.ModuleType("p90_gateway_source_only_unit")
    module.time = types.SimpleNamespace()
    module.psycopg = types.SimpleNamespace()
    module.UTC = datetime.UTC
    module.datetime = datetime.datetime
    module.P90PrearmEvidenceBlocked = P90PrearmEvidenceBlocked
    module.P90PrivateIpcUnverified = P90PrivateIpcUnverified
    module.P90_REAL_READINESS_PRIVATE_ATTEMPT_SECONDS = 10.0
    module.P90_REAL_READINESS_RETRY_SLEEP_SECONDS = 0.25
    module.P90_REAL_READINESS_TRANSIENT_PRIVATE_IPC = frozenset()
    module.LOADED_RELEASE_COMMIT = "a" * 40
    extracted = ast.Module(body=statements, type_ignores=[])
    exec(compile(extracted, str(APP), "exec"), module.__dict__)
    return module


class InertConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_exact_source_only_repair_does_not_spin_for_three_minutes():
    module = _real_wait_function()
    calls, sleeps = [], []

    def blocked(*_args, **_kwargs):
        calls.append(True)
        raise P90PrearmEvidenceBlocked(SOURCE_ONLY)

    module.prepare_p90l90_real_v3_prearm_evidence = blocked
    module.psycopg.connect = lambda *_a, **_kw: InertConnection()
    module.time.monotonic = lambda: 0.0
    module.time.sleep = sleeps.append

    with pytest.raises(P90PrearmEvidenceBlocked, match="SOURCE_REPAIR_ONLY"):
        module._wait_p90_real_v3_technical_evidence(timeout_seconds=180.0)
    assert len(calls) == 1
    assert sleeps == []  # Not 180 seconds, and never a financial mutation.


def test_transient_non_source_only_evidence_still_gets_bounded_retry():
    module = _real_wait_function()
    calls, sleeps = [], []
    ticks = iter((0.0, 0.0, 0.0, 0.0, 2.0))

    def temporarily_unavailable(*_args, **_kwargs):
        calls.append(True)
        raise P90PrearmEvidenceBlocked("P90 REAL v3 PREARM: waiting for candle")

    module.prepare_p90l90_real_v3_prearm_evidence = temporarily_unavailable
    module.psycopg.connect = lambda *_a, **_kw: InertConnection()
    module.time.monotonic = lambda: next(ticks)
    module.time.sleep = sleeps.append

    with pytest.raises(P90PrearmEvidenceBlocked, match="waiting for candle"):
        module._wait_p90_real_v3_technical_evidence(timeout_seconds=1.0)
    assert calls == [True]
    assert sleeps == [1.0]


def test_failure_is_durable_and_financial_arm_still_requires_full_evidence():
    source = APP.read_text(encoding="utf-8")
    assert '"P90_EXIT_SOURCE_ONLY_NOT_READY"' in source
    assert "mark_real_readiness_blocked(" in source
    assert 'code="TECHNICAL_EVIDENCE_BLOCKED"' not in source
    assert "_wait_p90_real_v3_technical_evidence(" in source
    assert 'if preparation.get("state") != "READY_FOR_OWNER_ARM":' in source
    assert 'stage_p90l90_real_v3(' in source
    assert 'finalize_p90l90_real_v3(' in source


def test_modal_explains_source_only_without_claiming_gate_open():
    html = HTML.read_text(encoding="utf-8")
    assert "const current=p90ModalState==='busy'&&(" in html
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node required for executed shipped JavaScript check")
    body = "function p90ReadinessOperationText(" + html.split(
        "function p90ReadinessOperationText(", 1,
    )[1].split("function p90ReadinessOperationTechnicalText(", 1)[0]
    script = (
        "const assert=require('node:assert/strict');\n"
        + body
        + "\nconst message=p90ReadinessOperationText({"
        + "state:'BLOCKED',phase:'blocked',"
        + "blocker_code:'P90_EXIT_SOURCE_ONLY_NOT_READY'});\n"
        + "assert.match(message,/P90 Entry\\/Exit не подтверждены/);\n"
        + "assert.match(message,/шлюз закрыт/);\n"
        + "assert.match(message,/Следующие этапы ещё не проверены/);\n"
        + "const other=p90ReadinessOperationText({state:'BLOCKED',"
        + "blocker_code:'TECHNICAL_EVIDENCE_BLOCKED',"
        + "blocker_detail:'other exact blocker'});\n"
        + "assert.match(other,/other exact blocker/);\n"
    )
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
