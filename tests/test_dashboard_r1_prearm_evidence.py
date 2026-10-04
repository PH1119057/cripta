from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_r1_micro_live_endpoint_builds_prearm_evidence_before_arm() -> None:
    source = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
    assert "def _u6_prepare_r1_prearm_evidence(" in source
    assert "_u6_prepare_r1_prearm_evidence(connection, now=now)" in source
    prepare_index = source.index("_u6_prepare_r1_prearm_evidence(connection, now=now)")
    arm_index = source.index("result = arm_r1_micro_live(", prepare_index)
    assert prepare_index < arm_index


def test_r1_prearm_requires_exact_release_identity_and_runtime_truth() -> None:
    source = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
    for token in (
        "remote/source/runtime/state release identity mismatch",
        "observer is not exact-release ready",
        "private Bybit runtime is not fresh/ready",
        "lifecycle supervisor is not healthy",
        "position mode cohort incomplete",
        "EntryPlan not loaded",
        "ExitPlan not loaded",
        "R1 PREARM durable evidence incomplete",
    ):
        assert token in source


def test_r1_prearm_writes_all_required_pre_owner_evidence() -> None:
    source = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
    for code in (
        "CANON_CURRENT",
        "REMOTE_COMMIT_VERIFIED",
        "SOURCE_LIVE_IDENTITY",
        "TESTS",
        "LIVE_EQUIVALENCE",
        "EXCHANGE_ACCOUNT_IDENTITY",
        "PHYSICAL_SLOT_CLAIM_CONTRACT",
        "CAPITAL_RESERVATION_CONTRACT",
        "LIFECYCLE_SUPERVISOR_BEHAVIOR",
        "CRITICAL_FAULT_DELIVERY",
        "RECONCILIATION_PATH",
        "ROLLBACK_OR_KILL_PATH",
        "EXACT_STRATEGY_ACTIVATION",
        "ENTRY_PLAN_EXECUTABLE",
        "EXIT_PLAN_EXECUTABLE",
        "INITIAL_PROTECTION_EXECUTABLE",
        "TERMINAL_LOSS_CONTAINMENT_PATH",
        "EMERGENCY_POLICY_SUPPORTED",
        "POSITION_MODE_FRESH",
        "POSITION_IDX_EXPECTED",
        "MICRO_LIVE_LIMITS",
    ):
        assert code in source
    assert "OWNER_WAIVED_FOR_R1_MICRO_LIVE" in source
