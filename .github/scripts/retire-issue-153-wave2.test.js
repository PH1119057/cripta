"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const {validate,parseLedger,sameRefs,CONFIRM}=require("./retire-issue-153-wave2.js");
const m=JSON.parse(fs.readFileSync(path.join(__dirname,"retire-issue-153-wave2-manifest.json"),"utf8"));
test("56 chosen; 23 preserved",()=>assert.deepEqual(validate(m),{ok:true,errors:[]}));
test("no overlaps",()=>assert.equal(new Set([...m.deleteRefs,...m.retainUniqueHistoryRefs].map(x=>x.name)).size,79));
test("no deleted protected branch",()=>assert(!m.deleteRefs.some(x=>m.exclusions.includes(x.name))));
test("manual owner confirmation phrase",()=>assert.equal(CONFIRM,"DELETE-56-ISSUE-153-WAVE2-20261022"));
test("wrong target count rejected",()=>assert.equal(validate({...m,deleteRefs:m.deleteRefs.slice(1)}).ok,false));
test("changed ref sha rejected",()=>assert.equal(validate({...m,deleteRefs:m.deleteRefs.map((x,i)=>i?x:{...x,sha:"invalid"})}).ok,false));
test("ref overlap rejected",()=>assert.equal(validate({...m,retainUniqueHistoryRefs:m.retainUniqueHistoryRefs.map((x,i)=>i?x:{...x,name:m.deleteRefs[0].name})}).ok,false));
test("retain default branch",()=>assert.equal(validate({...m,deleteRefs:m.deleteRefs.map((x,i)=>i?x:{...x,name:"main"})}).ok,false));
test("path traversal rejected",()=>assert.equal(validate({...m,deleteRefs:m.deleteRefs.map((x,i)=>i?x:{...x,name:"core/../main"})}).ok,false));
test("ledger parser distinguishes attached and references",()=>{
 const q=String.fromCharCode(96),a=m.deleteRefs[0],b=m.retainUniqueHistoryRefs[0];
 const content="| "+q+a.name+q+" | "+q+a.sha+q+" | core_only | NO | — | — | 1 |\n"+
 "| "+q+b.name+q+" | "+q+b.sha+q+" | docs_only | YES | DIRTY | — | 3 |";
 assert.deepEqual(parseLedger(content).map(x=>({name:x.name,attached:x.attached,pointer:x.pointer})),
 [{name:a.name,attached:false,pointer:false},{name:b.name,attached:true,pointer:false}]);
});
test("different ledger sha rejected",()=>assert(!sameRefs(m.deleteRefs,m.deleteRefs.map((x,i)=>i?x:{...x,sha:"0".repeat(40)}))));
