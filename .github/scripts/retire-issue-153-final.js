"use strict";
const fs = require("node:fs");
const path = require("node:path");
const FILE = path.join(__dirname, "retire-issue-153-final-manifest.json");
const CONFIRM = "DELETE-38-ISSUE-153-20261024";
function parseLedger(body) {
  const rows = [];
  const re = /^\| \x60([^\x60]+)\x60 \| \x60([0-9a-f]{40})\x60 \| (ARCHIVED_UNIQUE|MERGED_ANCESTOR) \|$/gm;
  let x;
  while ((x = re.exec(String(body || ""))) !== null) rows.push({name:x[1], sha:x[2], class:x[3]});
  return rows;
}
function sameRefs(a,b) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
  const map = new Map(a.map(x => [x.name, x.sha + ":" + x.class]));
  return map.size === a.length && b.every(x => map.get(x.name) === x.sha + ":" + x.class);
}
function validate(m) {
  const errors = [];
  if (m?.schema !== 3 || m?.repo !== "PH1119057/cripta" ||
      m?.issue !== 153 || m?.ledgerCommentId !== 6372760593) errors.push("identity");
  if (m?.confirm !== CONFIRM || !/^[0-9a-f]{40}$/.test(m?.frozenMain || "")) errors.push("confirm/baseline");
  if (!Array.isArray(m?.deleteRefs) || m.deleteRefs.length !== 38) errors.push("count");
  if ((m?.deleteRefs || []).filter(x => x.class === "ARCHIVED_UNIQUE").length !== 23 ||
      (m?.deleteRefs || []).filter(x => x.class === "MERGED_ANCESTOR").length !== 15) errors.push("classes");
  const a = m?.archive;
  if (a?.location !== "/data/cripta/github_cleanup_archive/20261022" ||
      a?.verifiedCommitObjects !== 38 || a?.offsite !== false ||
      !/^[0-9a-f]{64}$/.test(a?.historicHeadsSha256 || "") ||
      !/^[0-9a-f]{64}$/.test(a?.wipSha256 || "")) errors.push("archive-evidence");
  const seen = new Set();
  for (const x of m?.deleteRefs || []) {
    if (!x || typeof x.name !== "string" || !/^[a-z0-9][a-z0-9._/-]*$/.test(x.name) ||
        x.name.split("/").some(part => !part || part === "." || part === "..") ||
        x.name === "main" || x.name.startsWith("research/") ||
        x.name.includes("20261023") || !/^[a-f0-9]{40}$/.test(x.sha || "")) errors.push("unsafe-ref");
    if (seen.has(x.name)) errors.push("duplicate");
    seen.add(x.name);
  }
  if (seen.size !== 38) errors.push("duplicate/count");
  return {ok:errors.length === 0, errors};
}
async function github(resource, method="GET") {
  if (!process.env.GITHUB_TOKEN) throw new Error("No GitHub Actions token");
  const response = await fetch("https://api.github.com/repos/PH1119057/cripta/" + resource, {
    method,
    headers: {
      "Authorization": "Bearer " + process.env.GITHUB_TOKEN,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "cripta-issue-153-final"
    }
  });
  const text = response.status === 204 ? "" : await response.text();
  if (!response.ok) throw new Error(method + " " + resource + " status=" + response.status + " " + text.slice(0,180));
  return text ? JSON.parse(text) : null;
}
function apiRef(name) {
  return "heads/" + name.split("/").map(encodeURIComponent).join("/");
}
async function run() {
  const m = JSON.parse(fs.readFileSync(FILE,"utf8"));
  const v = validate(m);
  if (!v.ok) throw new Error("invalid frozen manifest: " + v.errors.join(","));
  if (process.env.GITHUB_REPOSITORY !== "PH1119057/cripta" ||
      process.env.GITHUB_ACTOR !== "PH1119057" ||
      process.env.GITHUB_EVENT_NAME !== "workflow_dispatch" ||
      process.env.GITHUB_REF !== "refs/heads/main")
    throw new Error("Only repository owner manual action on main permitted");
  const mode = process.env.FINAL_MODE;
  if (mode !== "preview" && mode !== "delete") throw new Error("unknown mode");
  if (mode === "delete" && process.env.FINAL_CONFIRM !== CONFIRM)
    throw new Error("missing exact owner confirmation");
  const issue = await github("issues/153");
  if (issue.state !== "open") throw new Error("Issue 153 unexpectedly closed");
  const ledger = await github("issues/comments/" + m.ledgerCommentId);
  const rows = parseLedger(ledger.body);
  if (rows.length !== 38 || !sameRefs(rows,m.deleteRefs)) throw new Error("frozen Issue ledger drift");
  const rules = await github("rulesets?per_page=100");
  if (!Array.isArray(rules) || !rules.some(x => x.name === "protect-main-workstreams" && x.enforcement === "active"))
    throw new Error("protected main ruleset absent");
  const main = (await github("git/ref/heads/main")).object.sha;
  if (main !== m.frozenMain) {
    const comparison = await github("compare/" + m.frozenMain + "..." + main);
    if (comparison.behind_by !== 0) throw new Error("main no longer descends from approved baseline");
  }
  // Active Research PRs are permitted, but no open PR may rely on a target branch.
  const prs = await github("pulls?state=open&per_page=100");
  if (!Array.isArray(prs) || prs.length === 100) throw new Error("open PR list incomplete");
  const target = new Set(m.deleteRefs.map(x => x.name));
  if (prs.some(x => target.has(x.head?.ref))) throw new Error("target branch has an open PR");
  for (const x of m.deleteRefs) {
    const ref = await github("git/ref/" + apiRef(x.name));
    if (ref.object?.sha !== x.sha) throw new Error("target SHA drift " + x.name);
  }
  console.log("FINAL_PREFLIGHT_PASS=38_SHA_MATCHED; UNIQUE_ARCHIVED=23; MERGED=15; open_unrelated_prs=" + prs.length);
  if (mode === "preview") {
    console.log("FINAL_PREVIEW_PASS: zero refs deleted");
    return;
  }
  let deleted = 0;
  for (const x of m.deleteRefs) {
    const ref = await github("git/ref/" + apiRef(x.name));
    if (ref.object?.sha !== x.sha) throw new Error("SHA moved mid-batch " + x.name + "; deleted=" + deleted);
    await github("git/refs/" + apiRef(x.name),"DELETE");
    deleted++;
    console.log("FINAL_DELETED " + deleted + "/38 " + x.name + " " + x.sha);
  }
  const still = await github("git/matching-refs/heads/");
  const remaining = new Set(still.map(x => x.ref.slice("refs/heads/".length)));
  const failures = m.deleteRefs.filter(x => remaining.has(x.name));
  if (failures.length) throw new Error("postcheck refs still present: " + failures.map(x => x.name).join(","));
  if (!remaining.has("main")) throw new Error("main missing in postcheck");
  console.log("FINAL_DELETED_ALL_38; POSTCHECK_PASS=38_ABSENT; research_heads_preserved_by_scope");
}
if (require.main === module) run().catch(e => {
  console.error("FINAL_FAIL_CLOSED " + e.message); process.exitCode=1;
});
module.exports = {validate,parseLedger,sameRefs,CONFIRM};
