from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = (
    ROOT / "operations/monitoring/lifecycle_supervisor_runtime.py"
).read_text(encoding="utf-8")
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
UI = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
NGINX = (
    ROOT / "research/server/dashboard/cripta-dashboard.nginx"
).read_text(encoding="utf-8")


def test_release_arm_guard_uses_fresh_clock_not_loop_clock() -> None:
    start = LIFECYCLE.index("if len(loaded_commit) == 40:")
    end = LIFECYCLE.index(
        "\n            with connection.transaction():\n                delivery",
        start,
    )
    body = LIFECYCLE[start:end]
    assert "guard_now = datetime.now(UTC)" in body
    assert "now=guard_now" in body
    assert "now=now" not in body
    assert body.index("guard_now = datetime.now(UTC)") < body.index(
        "_entry_source_health("
    ) < body.index("check_release_arm_invariant(")


def test_owner_arm_stage_keeps_bounded_full_runtime_rebuild_budget() -> None:
    helper_start = APP.index("def _u6_wait_p90_real_v3_stage_ready(")
    helper_end = APP.index("\n\nU6_STRATEGY_GET_PATH", helper_start)
    helper = APP[helper_start:helper_end]
    assert "timeout_seconds: float = 120.0" in helper

    handler_start = APP.index("def _u6_p90_real_v3_arm(")
    handler_end = APP.index("\ndef _u6_financial_account_catalog", handler_start)
    handler = APP[handler_start:handler_end]
    assert "timeout_seconds=120.0" in handler
    assert "P90_REAL_OWNER_ARM_STAGE_TIMEOUT:" in handler
    assert "if staged:" in handler
    assert "disarm_p90l90_real_v3(" in handler


def test_owner_arm_timeout_hierarchy_covers_full_request_not_only_stage_wait() -> None:
    start = UI.index("async function changeP90RealV3Arm(")
    end = UI.index("async function changeTradeGate(", start)
    body = UI[start:end]

    # PREARM evidence runs before the staged runtime wait.  Therefore the
    # browser/proxy budgets must not be collapsed to the stage timeout itself.
    assert "300000" in body
    assert "25000" not in body
    assert "proxy_read_timeout 240s;" in NGINX
    assert "proxy_read_timeout 30s;" not in NGINX
