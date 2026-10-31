"""Issue #304: explicit P90 OWNER ARM refreshes expiring technical evidence.

No real exchange or production control is exercised by these tests.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
CONTROL = (ROOT / "src/bybit_workbench/p90l90_real_arm_control.py").read_text(
    encoding="utf-8"
)
EVIDENCE = (
    ROOT / "src/bybit_workbench/p90l90_real_prearm_evidence.py"
).read_text(encoding="utf-8")


def _arm_handler() -> str:
    return APP.split("def _u6_p90_real_v3_arm(", 1)[1].split(
        "\ndef _u6_financial_account_catalog", 1
    )[0]


def test_owner_confirmation_precedes_fresh_technical_evidence() -> None:
    src = _arm_handler()
    confirmation = src.index('request.get("confirmed") is not True')
    refresh = src.index("prepare_p90l90_real_v3_prearm_evidence(")
    arm = src.index("result = arm_p90l90_real_v3(")
    assert confirmation < refresh < arm
    assert 'if enabled:\n        # A previous PREARM_TECHNICAL_READY' in src
    assert "release_commit=LOADED_RELEASE_COMMIT" in src
    assert "now=datetime.now(UTC)" in src


def test_evidence_commit_before_separate_atomic_owner_arm() -> None:
    src = _arm_handler()
    assert "as evidence_connection:" in src
    assert "as connection:" in src
    assert src.index("as evidence_connection:") < src.index("as connection:")
    assert src.index("prepare_p90l90_real_v3_prearm_evidence(") < src.index(
        "result = arm_p90l90_real_v3("
    )
    # Only the existing ARM owner can create sessions or open the gate.
    assert "UPDATE control.execution_gates" not in src
    assert "INSERT INTO control.live_arm_sessions" not in src
    assert "INSERT INTO control.account_live_arm_sessions" not in src


def test_disarm_does_not_refresh_or_require_new_evidence() -> None:
    src = _arm_handler()
    assert "if enabled:" in src
    assert "result = disarm_p90l90_real_v3(" in src
    assert src.index("result = disarm_p90l90_real_v3(") > src.index(
        "result = arm_p90l90_real_v3("
    )


def test_no_weakening_of_90_second_freshness_or_exact_arm_gates() -> None:
    assert "timedelta(seconds=90)" in EVIDENCE
    assert "for context in contexts:" in CONTROL
    assert "require_owner_approval=False" in CONTROL
    assert "require_owner_approval=True" in CONTROL
    assert CONTROL.index("require_owner_approval=False") < CONTROL.index(
        "require_owner_approval=True"
    )
    assert "P90_REAL_PRE_OWNER_EVIDENCE_MISSING:" in CONTROL
    assert "P90_REAL_OWNER_ARM_FINAL_EVIDENCE_BLOCKED:" in CONTROL
