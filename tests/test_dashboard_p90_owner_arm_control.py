from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")


def test_dashboard_has_dedicated_p90_owner_arm_control():
    assert 'id="p90RealV3ArmButton"' in INDEX
    assert "P90 REAL v3 OWNER ARM" in INDEX
    assert "/api/p90l90/real-v3/arm" in INDEX
    assert "exactP90RealV3Cohort" in INDEX
    assert "p90RealV3Armed" in INDEX


def test_p90_owner_arm_is_not_routed_through_legacy_generic_gate():
    start = INDEX.index("async function changeP90RealV3Arm")
    end = INDEX.index("tradeGateButton.addEventListener", start)
    body = INDEX[start:end]
    assert "/api/p90l90/real-v3/arm" in body
    assert "/api/live/gate" not in body
    assert "/api/r1/micro-live" not in body
    assert "/api/r1/unified/micro-live" not in body
    assert "rearmReady" not in body


def test_backend_keeps_dedicated_authenticated_p90_arm_endpoint():
    assert 'P90_REAL_V3_ARM_PATH = "/api/p90l90/real-v3/arm"' in APP
    assert "if self.require_login():" in APP
    assert "elif path == P90_REAL_V3_ARM_PATH:" in APP
    assert "_u6_p90_real_v3_arm(handler, request)" in APP


def test_owner_confirmation_text_exposes_exact_scope_and_risk():
    assert "MAIN: LTCUSDT, INJUSDT · cap 20 USDT" in INDEX
    assert "SUB: DOTUSDT, ARBUSDT, APTUSDT · cap 30 USDT" in INDEX
    assert "10 USDT на Entry · isolated 1x" in INDEX
    assert "mainnet gate ПОСЛЕДНИМ шагом" in INDEX
