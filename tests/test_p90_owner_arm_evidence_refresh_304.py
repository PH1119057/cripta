"""Issue #304/#308: explicit P90 OWNER ARM refreshes technical evidence.

No real Exchange or production control is exercised by these source-level tests.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
STAGED = (
    ROOT / "src/bybit_workbench/p90l90_real_staged_arm.py"
).read_text(encoding="utf-8")
EVIDENCE = (
    ROOT / "src/bybit_workbench/p90l90_real_prearm_evidence.py"
).read_text(encoding="utf-8")


def _arm_handler() -> str:
    return APP.split("def _u6_p90_real_v3_arm(", 1)[1].split(
        "\ndef _u6_financial_account_catalog", 1
    )[0]


def test_owner_confirmation_precedes_fresh_evidence_and_stage() -> None:
    src = _arm_handler()
    confirmation = src.index('request.get("confirmed") is not True')
    refresh = src.index("prepare_p90l90_real_v3_prearm_evidence(")
    stage = src.index("stage_p90l90_real_v3(")
    wait = src.index("_u6_wait_p90_real_v3_stage_ready(")
    finalize = src.index("finalize_p90l90_real_v3(")
    assert confirmation < refresh < stage < wait < finalize
    assert 'if enabled:\n        # A previous PREARM_TECHNICAL_READY' in src
    assert "release_commit=LOADED_RELEASE_COMMIT" in src
    assert "now=datetime.now(UTC)" in src


def test_gate_open_is_after_stage_ready_and_recheck() -> None:
    src = _arm_handler()
    assert "stage_p90l90_real_v3(" in src
    assert "_u6_wait_p90_real_v3_stage_ready(" in src
    assert "final_stage_ready, final_stage_detail" in src
    assert "finalize_p90l90_real_v3(" in src
    assert src.index("stage_p90l90_real_v3(") < src.index(
        "_u6_wait_p90_real_v3_stage_ready("
    ) < src.index("finalize_p90l90_real_v3(")
    # Dashboard itself never opens the gate; only the final staged controller can.
    assert "UPDATE control.execution_gates" not in src


def test_failed_stage_is_disarmed_before_error_escapes() -> None:
    src = _arm_handler()
    assert "staged = False" in src
    assert "if staged:" in src
    assert "disarm_p90l90_real_v3(" in src
    assert "P90_REAL_OWNER_ARM_STAGE_CLEANUP_FAILED" in src
    assert src.index("stage_p90l90_real_v3(") < src.index(
        "disarm_p90l90_real_v3("
    )


def test_no_weakening_of_technical_evidence_or_final_arm_gates() -> None:
    assert "timedelta(seconds=90)" in EVIDENCE
    assert "require_owner_approval=False" in STAGED
    assert "require_owner_approval=True" in STAGED
    assert STAGED.index("require_owner_approval=False") < STAGED.index(
        "require_owner_approval=True"
    )
    assert "P90_REAL_PRE_OWNER_EVIDENCE_MISSING:" in STAGED
    assert "P90_REAL_OWNER_ARM_FINAL_EVIDENCE_BLOCKED:" in STAGED
    assert "P90_REAL_OPEN_RUNTIME_INCIDENT" in STAGED
    assert "OWNER_ARM_STAGE_GATE_REASON" in STAGED
    assert "OWNER_ARM_OPEN_GATE_REASON" in STAGED
