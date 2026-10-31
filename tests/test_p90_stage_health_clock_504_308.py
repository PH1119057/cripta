from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = (
    ROOT / "operations/monitoring/lifecycle_supervisor_runtime.py"
).read_text(encoding="utf-8")
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
UI = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")


def test_release_arm_guard_uses_fresh_clock_not_loop_clock() -> None:
    start = LIFECYCLE.index("if len(loaded_commit) == 40:")
    end = LIFECYCLE.index("\n            with connection.transaction():\n                delivery", start)
    body = LIFECYCLE[start:end]
    assert "guard_now = datetime.now(UTC)" in body
    assert "now=guard_now" in body
    assert "now=now" not in body
    assert body.index("guard_now = datetime.now(UTC)") < body.index(
        "_entry_source_health("
    ) < body.index("check_release_arm_invariant(")


def test_owner_arm_stage_timeout_finishes_before_nginx_proxy_timeout() -> None:
    helper_start = APP.index("def _u6_wait_p90_real_v3_stage_ready(")
    helper_end = APP.index("\n\nU6_STRATEGY_GET_PATH", helper_start)
    helper = APP[helper_start:helper_end]
    assert "timeout_seconds: float = 20.0" in helper

    handler_start = APP.index("def _u6_p90_real_v3_arm(")
    handler_end = APP.index("\ndef _u6_financial_account_catalog", handler_start)
    handler = APP[handler_start:handler_end]
    assert "timeout_seconds=20.0" in handler
    assert "P90_REAL_OWNER_ARM_STAGE_TIMEOUT:" in handler
    assert "if staged:" in handler
    assert "disarm_p90l90_real_v3(" in handler


def test_owner_arm_browser_timeout_is_below_nginx_30s_and_above_backend_wait() -> None:
    start = UI.index("async function changeP90RealV3Arm(")
    end = UI.index("async function changeTradeGate(", start)
    body = UI[start:end]
    assert "25000" in body
    assert "75000" not in body
