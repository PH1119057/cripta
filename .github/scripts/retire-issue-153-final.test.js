"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {validate,parseLedger,sameRefs,CONFIRM} = require("./retire-issue-153-final.js");
const m = JSON.parse(fs.readFileSync(path.join(__dirname,"retire-issue-153-final-manifest.json"),"utf8"));
test("exact manifest and 38 historic refs",()=>assert.deepEqual(validate(m),{ok:true,errors:[]}));
test("only 15 merged and 23 archived unique",()=>{
  assert.equal(m.deleteRefs.filter(x=>x.class==="ARCHIVED_UNIQUE").length,23);
  assert.equal(m.deleteRefs.filter(x=>x.class==="MERGED_ANCESTOR").length,15);
});
test("owner confirmation fixed",()=>assert.equal(CONFIRM,"DELETE-38-ISSUE-153-20261024"));
test("no main, active research or 20261023 branches selected",()=>{
  assert(!m.deleteRefs.some(x=>x.name==="main"||x.name.startsWith("research/")||x.name.includes("20261023")));
});
test("count drift rejected",()=>assert.equal(validate({...m,deleteRefs:m.deleteRefs.slice(1)}).ok,false));
test("duplicate rejected",()=>assert.equal(validate({...m,deleteRefs:[m.deleteRefs[0],...m.deleteRefs.slice(0,-1)]}).ok,false));
test("renamed research target rejected",()=>assert.equal(validate({...m,deleteRefs:m.deleteRefs.map((x,i)=>i?x:{...x,name:"research/r158-active"})}).ok,false));
test("main target rejected",()=>assert.equal(validate({...m,deleteRefs:m.deleteRefs.map((x,i)=>i?x:{...x,name:"main"})}).ok,false));
test("invalid SHA rejected",()=>assert.equal(validate({...m,deleteRefs:m.deleteRefs.map((x,i)=>i?x:{...x,sha:"invalid"})}).ok,false));
test("unapproved ledger identity rejected",()=>assert.equal(validate({...m,ledgerCommentId:1}).ok,false));
test("missing archive evidence rejected",()=>assert.equal(validate({...m,archive:{...m.archive,verifiedCommitObjects:0}}).ok,false));
test("Issue 153 exact ledger parity and drift detection",()=>{
  const ledger=m.deleteRefs.map(x=>"| "+String.fromCharCode(96)+x.name+String.fromCharCode(96)+" | "+
    String.fromCharCode(96)+x.sha+String.fromCharCode(96)+" | "+x.class+" |").join("\n");
  const parsed=parseLedger(ledger);
  assert.equal(parsed.length,38);
  assert(sameRefs(parsed,m.deleteRefs));
  assert(!sameRefs(parsed,m.deleteRefs.map((x,i)=>i?x:{...x,sha:"0".repeat(40)})));
});
