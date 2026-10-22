"use strict";
const fs=require("node:fs");
const path=require("node:path");
const MANIFEST_FILE=path.join(__dirname,"retire-issue-153-manifest.json");
const CONFIRM="DELETE-91-ISSUE-153-20261022";
function validateManifest(m) {
  const errors=[];
  if(m?.schema!==1 || m?.issue!==153 || m?.repo!=="PH1119057/cripta")errors.push("bad identity");
  if(!/^[a-f0-9]{40}$/.test(m?.approvedBaseline||""))errors.push("bad base");
  if(!Array.isArray(m?.refs)||m.refs.length!==91||m.count!==91)errors.push("bad count");
  if(!Array.isArray(m?.retain)||!m.retain.includes("main")||!m.retain.includes("impl/trade-lifecycle-20260918"))errors.push("bad retained list");
  if(!/^[a-f0-9]{64}$/.test(m?.archive?.refsManifestSha256||"") || !/^[a-f0-9]{64}$/.test(m?.archive?.wipTarSha256||""))errors.push("archive hashes absent");
  const seen=new Set();
  for(const item of Array.isArray(m?.refs)?m.refs:[]) {
    if(!item||typeof item.name!=="string"||!/^[a-z0-9][a-z0-9._/-]*$/.test(item.name)||item.name.split("/").some(x=>!x||x==="."||x==="..")||item.name==="main"||(m.retain||[]).includes(item.name))errors.push("unsafe branch");
    if(seen.has(item.name))errors.push("duplicate branch");
    seen.add(item.name);
    if(!/^[a-f0-9]{40}$/.test(item.sha||""))errors.push("bad branch sha");
  }
  return {ok:!errors.length,errors};
}
function parseIssueTable(body) {
  const section=String(body||"").split("## 2026-10-22 exact first-wave reference manifest")[1];
  if(!section)throw new Error("Issue 153 manifest section missing");
  const text=section.split(/\n## /)[0];
  const out=[];
  const re=/^\| \x60([^\x60]+)\x60 \| \x60([a-f0-9]{40})\x60 \| REVIEW ONLY \|$/gm;
  let m;
  while((m=re.exec(text))!==null)out.push({name:m[1],sha:m[2]});
  return out;
}
function sameRefs(a,b) {
  if(!Array.isArray(a)||!Array.isArray(b)||a.length!==b.length)return false;
  const x=new Map(a.map(v=>[v.name,v.sha]));
  return x.size===a.length&&b.every(v=>x.get(v.name)===v.sha);
}
function encodedRef(name) { return "heads/"+name.split("/").map(encodeURIComponent).join("/"); }
async function request(relative,method="GET") {
  const token=process.env.GITHUB_TOKEN;
  if(!token)throw new Error("GITHUB_TOKEN is missing");
  const r=await fetch("https://api.github.com/repos/PH1119057/cripta/"+relative,{
    method,headers:{"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","User-Agent":"cripta-issue-153-retirement"}
  });
  const body=r.status===204?"":await r.text();
  if(!r.ok)throw new Error(method+" "+relative+" HTTP "+r.status+" "+body.slice(0,200));
  return body?JSON.parse(body):null;
}
async function run() {
  const manifest=JSON.parse(fs.readFileSync(MANIFEST_FILE,"utf8"));
  const result=validateManifest(manifest);
  if(!result.ok)throw new Error("bad manifest: "+result.errors.join(","));
  if(process.env.GITHUB_REPOSITORY!=="PH1119057/cripta"||process.env.GITHUB_REF!=="refs/heads/main"||process.env.GITHUB_EVENT_NAME!=="workflow_dispatch"||process.env.GITHUB_ACTOR!=="PH1119057")throw new Error("manual owner/main gate failed");
  const mode=process.env.RETIRE_MODE;
  if(mode!=="preview"&&mode!=="delete")throw new Error("mode invalid");
  if(mode==="delete"&&process.env.RETIRE_CONFIRM!==CONFIRM)throw new Error("owner confirmation phrase invalid");
  const issue=await request("issues/153");
  if(issue.state!=="open"||!sameRefs(parseIssueTable(issue.body),manifest.refs))throw new Error("Issue 153 branch list changed");
  const prs=await request("pulls?state=open&per_page=100");
  if(!Array.isArray(prs)||prs.length)throw new Error("open PRs present");
  const rules=await request("rulesets?per_page=100");
  if(!Array.isArray(rules)||!rules.some(r=>r.name==="protect-main-workstreams"&&r.enforcement==="active"))throw new Error("main ruleset not active");
  const main=(await request("git/ref/heads/main")).object.sha;
  if(main!==manifest.approvedBaseline) {
    const c=await request("compare/"+manifest.approvedBaseline+"..."+main);
    if(c.behind_by!==0)throw new Error("main no longer descends from approved baseline");
  }
  for(const item of manifest.refs) {
    const ref=await request("git/ref/"+encodedRef(item.name));
    if(ref.object?.sha!==item.sha)throw new Error("REF_DRIFT "+item.name+"; zero deletions authorized");
  }
  console.log("PREFLIGHT_PASS exact_91_SHA=yes open_PR=0 issue=153 ruleset=active");
  if(mode==="preview") {
    console.log("PREVIEW_PASS: no branches deleted");
    return;
  }
  let done=0;
  for(const item of manifest.refs) {
    // Re-read immediately before each DELETE. GitHub does not provide atomic SHA compare-and-delete.
    const live=await request("git/ref/"+encodedRef(item.name));
    if(live.object?.sha!==item.sha)throw new Error("REF_MOVED at "+item.name+"; already deleted "+done);
    await request("git/refs/"+encodedRef(item.name),"DELETE");
    done+=1;
    console.log("DELETED "+done+"/91 "+item.name+" "+item.sha);
  }
  console.log("DELETED_ALL_91; independently verify remaining refs and ruleset before closing Issue 153");
}
if(require.main===module)run().catch(e=>{console.error("FAIL_CLOSED "+e.message);process.exitCode=1;});
module.exports={validateManifest,parseIssueTable,sameRefs,CONFIRM};
