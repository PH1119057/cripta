"use strict";
const fs=require("node:fs");
const path=require("node:path");
const MANIFEST=path.join(__dirname,"retire-issue-153-wave2-manifest.json");
const CONFIRM="DELETE-56-ISSUE-153-WAVE2-20261022";
function validate(m) {
  const errors=[];
  if(m?.schema!==2||m?.issue!==153||m?.issueComment!==6344778195||m?.repo!=="PH1119057/cripta")errors.push("wrong identity");
  if(!/^[a-f0-9]{40}$/.test(m?.approvedBaseline||""))errors.push("wrong baseline");
  if(m?.deleteCount!==56||!Array.isArray(m?.deleteRefs)||m.deleteRefs.length!==56)errors.push("wrong target count");
  if(!Array.isArray(m?.retainUniqueHistoryRefs)||m.retainUniqueHistoryRefs.length!==23)errors.push("wrong preserved count");
  if(!Array.isArray(m?.exclusions)||!m.exclusions.includes("main")||!m.exclusions.includes("impl/trade-lifecycle-20260918"))errors.push("missing exclusions");
  for(const h of [m?.archive?.githubHeadsSnapshotSha256,m?.archive?.wipArchiveSha256])
    if(!/^[a-f0-9]{64}$/.test(h||""))errors.push("missing archive evidence");
  const seen=new Set();
  for(const x of [...(m?.deleteRefs||[]),...(m?.retainUniqueHistoryRefs||[])]) {
    if(!x||typeof x.name!=="string"||!/^[a-z0-9][a-z0-9._/-]*$/.test(x.name)||
      x.name.split("/").some(y=>!y||y==="."||y==="..")||(m.exclusions||[]).includes(x.name))errors.push("invalid or excluded ref");
    if(seen.has(x.name))errors.push("duplicate name");
    seen.add(x.name);
    if(!/^[a-f0-9]{40}$/.test(x.sha||""))errors.push("invalid ref SHA");
  }
  if(seen.size!==79)errors.push("unexpected total unique refs");
  return {ok:errors.length===0,errors};
}
function parseLedger(body) {
  const rows=[];
  const expr=/^\| \x60([^\x60]+)\x60 \| \x60([a-f0-9]{40})\x60 \| (core_only|docs_only|research_only|mixed) \| (YES|NO) \| (DIRTY|no detected WIP|—) \| (MAIN DOC REF|—) \| (\d+) \|$/gm;
  let r;
  while((r=expr.exec(String(body||"")))!==null)
    rows.push({name:r[1],sha:r[2],attached:r[4]==="YES",pointer:r[6]==="MAIN DOC REF"});
  return rows;
}
function sameRefs(a,b) {
  if(!Array.isArray(a)||!Array.isArray(b)||a.length!==b.length)return false;
  const mapped=new Map(a.map(x=>[x.name,x.sha]));
  return mapped.size===a.length&&b.every(x=>mapped.get(x.name)===x.sha);
}
async function github(relative,method="GET") {
  const token=process.env.GITHUB_TOKEN;
  if(!token)throw new Error("No scoped GitHub Actions token");
  const res=await fetch("https://api.github.com/repos/PH1119057/cripta/"+relative,{
    method,
    headers:{"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json",
      "X-GitHub-Api-Version":"2022-11-28","User-Agent":"cripta-issue-153-wave2"}
  });
  const body=res.status===204?"":await res.text();
  if(!res.ok)throw new Error(method+" "+relative+" status="+res.status+" "+body.slice(0,170));
  return body?JSON.parse(body):null;
}
function apiRef(name) {
  return "heads/"+name.split("/").map(encodeURIComponent).join("/");
}
async function run() {
  const m=JSON.parse(fs.readFileSync(MANIFEST,"utf8"));
  const v=validate(m);
  if(!v.ok)throw new Error("Manifest invalid: "+v.errors.join(","));
  if(process.env.GITHUB_REPOSITORY!=="PH1119057/cripta"||
     process.env.GITHUB_ACTOR!=="PH1119057"||
     process.env.GITHUB_EVENT_NAME!=="workflow_dispatch"||
     process.env.GITHUB_REF!=="refs/heads/main")
    throw new Error("Only owner manual main can execute wave2");
  const mode=process.env.WAVE2_MODE;
  if(!["preview","delete"].includes(mode))throw new Error("Unknown mode");
  if(mode==="delete"&&process.env.WAVE2_CONFIRM!==CONFIRM)
    throw new Error("Missing exact owner delete confirmation");
  const issue=await github("issues/153");
  if(issue.state!=="open")throw new Error("Issue #153 not open");
  const comment=await github("issues/comments/6344778195");
  const full=parseLedger(comment.body);
  const expected=[...m.deleteRefs,...m.retainUniqueHistoryRefs];
  if(full.length!==79||!sameRefs(full,expected))throw new Error("Issue #153 ledger 79 refs changed");
  const actualTargets=full.filter(x=>!x.attached&&!x.pointer);
  const actualRetained=full.filter(x=>x.attached||x.pointer);
  if(actualTargets.length!==56||actualRetained.length!==23||
     !sameRefs(actualTargets,m.deleteRefs)||!sameRefs(actualRetained,m.retainUniqueHistoryRefs))
    throw new Error("Issue #153 ledger attached/reference selection changed");
  const prs=await github("pulls?state=open&per_page=100");
  if(!Array.isArray(prs)||prs.length)throw new Error("Open PRs prevent deletion");
  const rules=await github("rulesets?per_page=100");
  if(!Array.isArray(rules)||!rules.some(x=>x.name==="protect-main-workstreams"&&x.enforcement==="active"))
    throw new Error("main ruleset inactive");
  const main=(await github("git/ref/heads/main")).object.sha;
  if(main!==m.approvedBaseline) {
    const comparison=await github("compare/"+m.approvedBaseline+"..."+main);
    if(comparison.behind_by!==0)throw new Error("main does not descend from authorized source");
  }
  // Verify all 79 original unique-history tips, including the 23 explicitly retained.
  for(const x of expected) {
    const remote=await github("git/ref/"+apiRef(x.name));
    if(remote.object?.sha!==x.sha)throw new Error("SHA drift preflight "+x.name);
  }
  console.log("WAVE2_PREFLIGHT_PASS delete_targets=56 preserved_unique_history=23 issue=153 scope=checked");
  if(mode==="preview") {
    console.log("WAVE2_PREVIEW_PASS: zero refs deleted");
    return;
  }
  let count=0;
  for(const x of m.deleteRefs) {
    const remote=await github("git/ref/"+apiRef(x.name));
    if(remote.object?.sha!==x.sha)throw new Error("SHA moved mid-batch at "+x.name+"; deleted="+count);
    await github("git/refs/"+apiRef(x.name),"DELETE");
    count++;
    console.log("WAVE2_DELETED "+count+"/56 "+x.name+" "+x.sha);
  }
  console.log("WAVE2_DELETED_ALL_56; independently verify GitHub live refs");
}
if(require.main===module)run().catch(e=>{console.error("WAVE2_FAIL_CLOSED "+e.message);process.exitCode=1;});
module.exports={validate,parseLedger,sameRefs,CONFIRM};
