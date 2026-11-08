"""Regression: P90 owner ARM may wait for the next *fresh* generation.

The strict signed/private 15s proof TTL remains unchanged. Mocked calls only,
no production DB, Exchange requests, or financial gate operations.
"""
from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.dashboard import app


def test_owner_arm_recheck_waits_for_next_generation_without_widening_ttl(
    monkeypatch,
):
    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    clock = SimpleNamespace(value=0.0)
    calls = []

    monkeypatch.setattr(app.psycopg, "connect", lambda *_a, **_kw: DB())
    monkeypatch.setattr(app.time, "monotonic", lambda: clock.value)
    monkeypatch.setattr(
        app.time, "sleep", lambda duration: setattr(
            clock, "value", clock.value + duration,
        ),
    )

    def evidence(*_args, **_kwargs):
        calls.append(clock.value)
        # The previous COMPLETE/PROVEN generation has aged past the 15s TTL.
        # The independent account runtime publishes the next one at t=17s.
        if clock.value < 17.0:
            raise app.P90PrearmEvidenceBlocked(
                "p90l90_legacy_main_ltc_inj account generation "
                "not fresh/flat/capable of one 10 USDT Entry"
            )
        return {"status": "PREARM_TECHNICAL_READY"}

    monkeypatch.setattr(app, "prepare_p90l90_real_v3_prearm_evidence", evidence)
    result = app._wait_p90_real_v3_technical_evidence(timeout_seconds=30.0)
    assert result["status"] == "PREARM_TECHNICAL_READY"
    assert clock.value == 17
    assert len(calls) == 18
    assert "0 <= (generation_checked_at - completed).total_seconds() <= 15" in (
        Path("src/bybit_workbench/p90l90_real_prearm_evidence.py")
        .read_text(encoding="utf-8")
    )


def _arm_handler_with_failed_second_proof(exc):
    code = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    tree = ast.parse(code)
    node = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_u6_p90_real_v3_arm"
    )
    calls = []

    def recheck(*, timeout_seconds):
        calls.append(("recheck", timeout_seconds))
        raise exc

    def blocked(**kwargs):
        calls.append(("blocked", kwargs))

    def no_stage(*_a, **_kw):
        pytest.fail("A failed recheck must never reach financial staging")

    namespace = {
        "datetime": datetime,
        "UTC": UTC,
        "LOADED_RELEASE_COMMIT": "test-release",
        "P90PrearmEvidenceBlocked": app.P90PrearmEvidenceBlocked,
        "P90PrivateIpcUnverified": app.P90PrivateIpcUnverified,
        "P90_REAL_READINESS_TRANSIENT_PRIVATE_IPC": (
            app.P90_REAL_READINESS_TRANSIENT_PRIVATE_IPC
        ),
        "_u6_financial_account_same_origin": lambda _: True,
        "_u6_prepare_p90_real_v3_readiness": lambda **_: {
            "state": "READY_FOR_OWNER_ARM",
        },
        "_wait_p90_real_v3_technical_evidence": recheck,
        "_mark_p90_readiness_blocked": blocked,
        "stage_p90l90_real_v3": no_stage,
        "finalize_p90l90_real_v3": no_stage,
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), "arm", "exec"),
        namespace,
    )
    return namespace["_u6_p90_real_v3_arm"], calls


@pytest.mark.parametrize(
    ("failure", "retry_allowed"),
    [
        (app.P90PrearmEvidenceBlocked("stale generation"), True),
        (app.P90PrivateIpcUnverified("P90_PRIVATE_IPC_INCOMPLETE_FRAME"), True),
        (app.P90PrivateIpcUnverified("P90_PRIVATE_IPC_PEER_NOT_VERIFIED"), False),
    ],
)
def test_second_proof_failure_is_durable_and_cannot_stage(failure, retry_allowed):
    arm, calls = _arm_handler_with_failed_second_proof(failure)
    owner = SimpleNamespace(session_user=lambda: "owner")
    with pytest.raises(type(failure)):
        arm(owner, {"enabled": True, "confirmed": True, "request_id": "once"})
    assert calls[0] == ("recheck", 30.0)
    assert len(calls) == 2
    detail = calls[1][1]
    assert detail["request_id"] == "once"
    assert detail["code"] == "OWNER_ARM_FINAL_TECHNICAL_RECHECK_BLOCKED"
    assert detail["retry_eligible"] is retry_allowed
    assert detail["detail"] == type(failure).__name__ + ":" + str(failure)


def test_recheck_precedes_all_financial_controls_and_retains_exact_ttl():
    code = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    arm = code.split("def _u6_p90_real_v3_arm(", 1)[1].split(
        "\ndef _u6_financial_account_catalog", 1,
    )[0]
    assert arm.index("_wait_p90_real_v3_technical_evidence(") < arm.index(
        "stage_p90l90_real_v3("
    ) < arm.index("finalize_p90l90_real_v3(")
    assert arm.index("_mark_p90_readiness_blocked(") < arm.index(
        "stage_p90l90_real_v3("
    )
    assert "UPDATE control.execution_gates" not in arm
    assert "read_flat_account()" in Path(
        "src/bybit_workbench/p90l90_real_prearm_evidence.py"
    ).read_text(encoding="utf-8")
