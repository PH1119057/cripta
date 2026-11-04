import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.dashboard import app


def test_unready_one_click_attempt_prepares_but_cannot_stage_when_checks_fail(monkeypatch):
    monkeypatch.setattr(app, "_u6_financial_account_same_origin", lambda _h: True)
    prepared = []

    def prepare(**kwargs):
        prepared.append(kwargs)
        return {"state": "BLOCKED", "blocker_code": "OBSERVER_NOT_READY"}

    def forbidden(*_a, **_kw):
        pytest.fail("failed preparation must not stage or mutate trading state")

    monkeypatch.setattr(app, "_u6_prepare_p90_real_v3_readiness", prepare)
    monkeypatch.setattr(app.psycopg, "connect", forbidden)
    monkeypatch.setattr(app, "stage_p90l90_real_v3", forbidden)
    with pytest.raises(ValueError, match="PREPARATION_BLOCKED"):
        app._u6_p90_real_v3_arm(
            SimpleNamespace(session_user=lambda: "owner"),
            {"enabled": True, "confirmed": True},
        )
    assert len(prepared) == 1


def test_prepare_action_cannot_arm_even_when_preparation_succeeds(monkeypatch):
    result = {"state": "READY_FOR_OWNER_ARM"}
    monkeypatch.setattr(app, "_u6_financial_account_same_origin", lambda _h: True)
    monkeypatch.setattr(app, "_u6_prepare_p90_real_v3_readiness", lambda **_kw: result)
    sent = []
    monkeypatch.setattr(app, "_u6_json", lambda _h, _code, body: sent.append(body))

    def forbidden(*_a, **_kw):
        pytest.fail("technical preparation cannot authorize trading")

    monkeypatch.setattr(app, "stage_p90l90_real_v3", forbidden)
    monkeypatch.setattr(app, "finalize_p90l90_real_v3", forbidden)
    app._u6_p90_real_v3_prepare(
        SimpleNamespace(session_user=lambda: "owner"), {"request_id": "prepare-only-1"},
    )
    assert sent == [result]


def test_p90_control_executes_ready_disabled_disarm_and_single_renderer_states():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for actual Dashboard JavaScript execution")
    # Execute the shipped renderer, rather than a Python copy of its conditions.
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    renderer = html.split("function refreshP90RealV3ArmControl(){", 1)[1].split(
        "let p90PendingOperation=", 1,
    )[0]
    script = "function refreshP90RealV3ArmControl(){" + renderer + "\n" + r"""
const assert=require('node:assert/strict');
let nodes={};
function element(){return {hidden:false,disabled:false,textContent:'',classList:{toggle(){}}}}
global.document={getElementById:id=>nodes[id]||(nodes[id]=element())};
global.window={};
let armed=false,liveGateOpen=false,gateChanging=false,p90PendingOperation=null;
let tradeGateButton=element(),tradeOperationStatus=element(),lastLiveSuccessAt=Date.now();
function exactP90RealV3Cohort(){return true}
function p90RealV3OwnerArmPresent(){return armed}
function p90RealV3Armed(){return armed&&liveGateOpen}
function p90ReadinessOperationText(){return 'server phase'}
function showAuthoritativeP90ArmState(){}
function renderP90ArmModal(){}
function setOperationStatus(node,state,text){node.textContent=text}
function ready(){
 window.p90ArmStatus={observer_ready:true,technical_preparation_ready:true,blockers:[]}
}
ready();refreshP90RealV3ArmControl();assert.equal(tradeGateButton.disabled,false);
window.p90ArmStatus.observer_ready=false;
window.p90ArmStatus.blockers=['P90_REAL_OBSERVER_NOT_READY'];
refreshP90RealV3ArmControl();assert.equal(tradeGateButton.disabled,false);
ready();window.p90ArmStatus.technical_preparation_ready=false;
refreshP90RealV3ArmControl();assert.equal(tradeGateButton.disabled,false);
ready();lastLiveSuccessAt=Date.now()-11000;
refreshP90RealV3ArmControl();assert.equal(tradeGateButton.disabled,true);
lastLiveSuccessAt=Date.now();
// DISARM is available even when Observer/evidence are not ready.
armed=true;refreshP90RealV3ArmControl();assert.equal(tradeGateButton.disabled,false);
armed=false;ready();gateChanging=true;
p90PendingOperation={state:'warn',message:'exact current phase'};
window.p90ArmStatus.readiness_operation={state:'BLOCKED',retry_eligible:true};
refreshP90RealV3ArmControl();
assert.equal(tradeOperationStatus.textContent,'exact current phase');
assert.equal(tradeGateButton.disabled,true);
"""
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_p90_progress_has_no_competing_timer_and_ignores_foreign_requests():
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    block = html.split("async function changeP90RealV3Arm(", 1)[1].split(
        "async function changeTradeGate(", 1,
    )[0]
    assert "beginTimedStatus" not in block
    assert "setInterval" not in block
    assert "op.request_id===requestId" in block
    assert "setOperationStatus(status" not in block
