import shutil
import subprocess
from pathlib import Path

import pytest


def test_single_modal_requires_consent_and_projects_real_checks_without_false_open():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for shipped JavaScript behavior")
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert 'id="p90RealV3PrepareButton"' not in html
    code = "function renderP90ArmModal(" + html.split(
        "function renderP90ArmModal(", 1,
    )[1].split("const p90ArmHistoryKey=", 1)[0]
    setup = r"""
const assert=require('node:assert/strict');
let gateChanging=false,p90ModalState='confirm',p90ModalOperation=null,p90PendingOperation=null;
const keys=['versions','services','market','strategies','accounts','entry_exit','arm'];
let rows=keys.map(check=>({dataset:{check}})),calls=[];
let elements={};
function el(id){return elements[id]||(elements[id]={
 open:false,hidden:false,disabled:false,textContent:'',className:'',handlers:{},
 addEventListener(k,f){this.handlers[k]=f},
 showModal(){this.open=true},close(){this.open=false},querySelectorAll(){return rows}
})}
global.document={getElementById:el};
async function changeP90RealV3Arm(enabled){calls.push(enabled)}
"""
    checks = r"""
(async()=>{
showP90ArmModal();
assert.equal(calls.length,0); // merely opening the window never submits ARM
assert.equal(el('p90ArmDialogConfirm').hidden,false);
await el('p90ArmDialogConfirm').handlers.click();
assert.deepEqual(calls,[true]);
assert.equal(el('p90ArmDialogClose').disabled,true);
p90PendingOperation={requestId:'current',message:'Checking accounts'};
renderP90ArmModal({request_id:'old',state:'READY_FOR_OWNER_ARM'});
assert.equal(p90ModalOperation,null); // previous successful request is not current proof
renderP90ArmModal({request_id:'current',state:'VALIDATING',phase:'accounts',
 progress_json:{completed_checks:['versions','services','market','strategies']}});
assert.equal(rows[0].dataset.state,'done');
assert.equal(rows[4].dataset.state,'busy');
assert.equal(rows[5].dataset.state,'pending');
assert.equal(rows[6].dataset.state,'pending');
renderP90ArmModal({request_id:'current',state:'READY_FOR_OWNER_ARM',phase:'arming_closed_gate'});
assert.equal(el('p90ArmDialogTitle').textContent,'Проверяем систему');
assert.equal(rows[6].dataset.state,'busy'); // technical READY is not OPEN
p90ModalState='error';p90PendingOperation.message='Account proof failed';renderP90ArmModal();
assert.equal(el('p90ArmDialogTitle').textContent,'Шлюз не открыт');
assert.equal(el('p90ArmDialogResult').textContent,'Account proof failed');
assert.equal(el('p90ArmDialogClose').disabled,false);
p90ModalState='success';p90PendingOperation.message='Gate readback OPEN';renderP90ArmModal();
assert.equal(el('p90ArmDialogTitle').textContent,'Шлюз открыт');
assert.ok(rows.every(x=>x.dataset.state==='done'));
showP90ArmModal();assert.equal(calls.length,1); // fresh consent required every time
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run([node, "-e", setup + code + checks], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_operational_notice_does_not_play_trade_alarm():
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "queuedSound('alarm')" not in html
    assert "historical_critical_faults" in html
    assert "История закрытых сделок" in html
    assert "entrySoundChoice.value" in html
