"""#869 UI-only regression: OPEN rows never bounce or flicker on price polls.

Run the actual shipped JavaScript sort/DOM helper with Node (no browser,
backend, network, Exchange, SQL or production mutation).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")


def test_p90_rail_never_falls_back_to_generic_during_partial_poll():
    assert "P90_RAIL_FIXED_SYMBOLS" in HTML
    assert "rail.dataset.p90Rendered==='true'" in HTML
    assert "rail.setAttribute('aria-busy','true')" in HTML
    assert "if(Array.isArray(lt.p90_rail?.items)" in HTML
    assert "renderP90RailPositions(lt.p90_rail?.items||[],positions)" in HTML


def test_p90_open_view_uses_current_trusted_state_only():
    # UI pin affects sorting ONLY; the green OPEN claim, PnL and Bybit controls
    # are entirely derived from the incoming current signed read model.
    assert "if(state!=='OPEN'){" in HTML
    assert "const p=item.position||{}" in HTML
    assert "if((state==='OPEN'&&item?.position)||p90RailKnownOpen.has(key))return 0" in HTML
    assert "p90RailConfirmedGone.set(key,seen)" in HTML
    assert "if(seen>=3)p90RailKnownOpen.delete(key)" in HTML


def test_p90_dom_reuses_nodes_instead_of_recreating_on_quote():
    snippet = HTML.split("function patchP90RailNode(node,next){", 1)[1].split(
        "const p90TpReadbacks=", 1
    )[0]
    assert "node.nodeValue=next.nodeValue" in snippet
    assert "node.innerHTML=next.innerHTML" not in snippet
    assert "node===document.activeElement" in snippet
    assert "if(node!==target)rail.insertBefore(node,target)" in snippet
    assert "node.removeChild(oldChild)" in snippet


def test_js_sticky_open_and_text_only_dom_patch():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable; static scope tests still run")
    start = HTML.index("function p90RailH9Proximity(item){")
    end = HTML.index("function patchP90RailDom(rail,html){")
    actual_js = HTML[start:end]
    driver = r"""
const assert=require('node:assert/strict');
globalThis.document={activeElement:null};
const entry=(symbol,distance)=>({
    symbol,state:'FAR',position:null,account_inventory_verified:true,
    position_attribution:'NO_CURRENT_EXCHANGE_POSITION',
    h9_distances:{long_distance_pct:distance,short_distance_pct:9}
});
const source=[
    entry('APTUSDT',1.0),entry('ARBUSDT',0.8),entry('DOTUSDT',0.4),
    entry('INJUSDT',0.1),
    {...entry('LTCUSDT',2.0),state:'OPEN',position:{size:'2'},
     position_attribution:'EXACT_STRATEGY_OWNED'}
];
const order=xs=>p90RailStableRows(xs).map(x=>x.symbol);
assert.deepEqual(order(source),
  ['LTCUSDT','INJUSDT','DOTUSDT','ARBUSDT','APTUSDT']);
const unknown=source.map(x=>x.symbol==='LTCUSDT'?{
  ...x,state:'ACCOUNT_UNVERIFIED',position:null,
  account_inventory_verified:false,position_attribution:'INVENTORY_UNKNOWN'
}:x);
assert.equal(order(unknown)[0],'LTCUSDT');
const drift=unknown.map(x=>x.symbol==='ARBUSDT'?{
 ...x,h9_distances:{long_distance_pct:0.01,short_distance_pct:9}
}:x);
assert.deepEqual(order(drift),
  ['LTCUSDT','INJUSDT','DOTUSDT','ARBUSDT','APTUSDT']);
const flat=drift.map(x=>x.symbol==='LTCUSDT'?{
 ...x,state:'FAR',position:null,account_inventory_verified:true,
 position_attribution:'NO_CURRENT_EXCHANGE_POSITION'
}:x);
assert.equal(order(flat)[0],'LTCUSDT');
assert.equal(order(flat)[0],'LTCUSDT');
assert.equal(order(flat)[4],'LTCUSDT');
assert.deepEqual(order(source).slice(0,2),['LTCUSDT','INJUSDT']);
assert.deepEqual(order(source).slice(0,2),['LTCUSDT','INJUSDT']);
// Prices change every second. The SAME actual text object must be retained.
const before={nodeType:3,nodeName:'#text',nodeValue:'+0.1200 USDT'};
const after={nodeType:3,nodeName:'#text',nodeValue:'+0.1350 USDT'};
assert.strictEqual(patchP90RailNode(before,after),before);
assert.equal(before.nodeValue,'+0.1350 USDT');
// A stable DOM element also survives attribute and child-text updates.
class Span {
  constructor(label,text){
    this.nodeType=1;this.nodeName='SPAN';this.map=new Map([['class',label]]);
    this.child={nodeType:3,nodeName:'#text',nodeValue:text};
  }
  get attributes(){return [...this.map].map(([name,value])=>({name,value}));}
  hasAttribute(k){return this.map.has(k);}
  getAttribute(k){return this.map.get(k)||null;}
  setAttribute(k,v){this.map.set(k,v);}
  removeAttribute(k){this.map.delete(k);}
  get firstChild(){return this.child;}
}
const oldSpan=new Span('pnl-old','+0.12');
const newSpan=new Span('pnl-new','+0.13');
const oldText=oldSpan.child;
assert.strictEqual(patchP90RailNode(oldSpan,newSpan),oldSpan);
assert.strictEqual(oldSpan.child,oldText);
assert.equal(oldSpan.child.nodeValue,'+0.13');
assert.equal(oldSpan.getAttribute('class'),'pnl-new');
console.log('P90_OPEN_STICKY_AND_KEYED_DOM_PASS');
"""
    result = subprocess.run(
        [node, "-"],
        input=actual_js + "\n" + driver,
        text=True,
        capture_output=True,
        timeout=12,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2500:]
    assert "P90_OPEN_STICKY_AND_KEYED_DOM_PASS" in result.stdout
