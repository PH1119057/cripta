"use strict";

// Canonical owner: docs/DEVELOPMENT_RELEASE_RULES_RU.md §50.
// pull_request_target runs this ONLY from trusted main checkout; never checkout PR HEAD.
const LEGACY_ARCHIVE_ROOTS = new Set(["patches","patch_backups","research_tools"]);
const LEGACY_ARCHIVE_FILES = new Set([
  "APPLY_FULL_PANEL_PATCH_FIXED (1).ps1",
  "P46_1_README.txt","P46_README.txt",
  "P47A_README.txt","P47B_README.txt","P47C_README.txt",
  "P47E_README.txt","P47F_README.txt","P47H_README.txt",
  "PATCH_P17_ENDPOINT_SELECTOR.txt",
  "PATCH_P48_1_CONNECTION_LEVERAGE_FIX.txt",
  "PATCH_P48_3_AUTOMATIC_TIME_PREFLIGHT.txt",
  "PATCH_P48_4_CLOCK_WATCHDOG_AUTORECONNECT.txt",
  "PATCH_UI_LEVERAGE_SYMBOLS_1H.txt",
  "START_MICRO_LIVE.cmd","START_WORKBENCH.cmd",
  "dashboard_server_index.html"
]);
const LEGACY_ARCHIVE_PREFIXES = [
  "ENTRY_BOT_","ENTRY_P31_","ENTRY_RUNTIME_","ENTRY_V1_",
  "EO1","EO2","EO3","GUI_CONNECTION_ACTIVITY_RACE_FIX_V1",
  "P44_","P45_","P46_","P47I_","P47J_","P47K_","P47L_","P47M_","P47N_",
  "P49_","P50_","P51_","P52_","P53_","SE1","SE2","ZS1_"
];

function isLegacyArchivePath(path) {
  const root=path.split("/")[0];
  return LEGACY_ARCHIVE_ROOTS.has(root) ||
    LEGACY_ARCHIVE_FILES.has(root) ||
    LEGACY_ARCHIVE_PREFIXES.some(prefix=>root.startsWith(prefix));
}

function classifyPath(path) {
  if (typeof path !== "string" || !path || path.startsWith("/") || path.includes("\\") || path.includes("\0") || path.split("/").some(p=>p===""||p==="."||p==="..")) return "unknown";
  if (path === "operations/dashboard/instrument_research_catalog.json") return "ledger";
  if (path.startsWith("archive/") || isLegacyArchivePath(path)) return "archive";
  if (path.startsWith("docs/") || path==="README.md" || path==="AGENTS.md" || path==="operations/bootstrap/CHATGPT_PROJECT_INSTRUCTIONS_RU.txt") return "docs";
  if (path.startsWith("research/") || path.startsWith("tests/research/") || path.startsWith("scripts/research/")) return "research";
  if (path.startsWith("src/") || path.startsWith("operations/") || path.startsWith("tests/") || path.startsWith("scripts/") || path.startsWith(".github/workflows/") || path.startsWith(".github/scripts/") || ["pyproject.toml","uv.lock",".python-version",".gitignore",".gitattributes","bybit_workbench.spec"].includes(path)) return "core";
  return "unknown";
}

function validateScope(headRef, paths, labels=[]) {
  const errors=[];
  const match=typeof headRef==="string" ? /^(research|docs|core|cross)\/[a-z0-9][a-z0-9._/-]*$/.exec(headRef) : null;
  const stream=match ? match[1] : null;
  const ownerLabels=new Set(Array.isArray(labels)?labels:[]);
  if (!stream || headRef.split("/").some((x,i)=>i>0&&(x===""||x==="."||x===".."))) {
    errors.push("SCOPE_BRANCH_INVALID: use research/*, docs/*, core/*, or approved cross/*");
  }
  if (!Array.isArray(paths) || paths.length===0 || paths.length>6000) errors.push("SCOPE_CHANGED_PATHS_INCOMPLETE");
  if (stream==="cross" && !ownerLabels.has("owner-approved-cross-contour")) {
    errors.push("OWNER_CROSS_APPROVAL_REQUIRED: owner-approved-cross-contour label");
  }
  const kinds=new Set();
  for (const path of (Array.isArray(paths)?paths:[])) {
    const kind=classifyPath(path);
    kinds.add(kind);
    if (kind==="unknown") {
      errors.push(`UNKNOWN_PATH: ${String(path)}`);
      continue;
    }
    if(kind==="ledger" && !ownerLabels.has("owner-approved-ledger-registration")) {
      errors.push(`LEDGER_REGISTRATION_APPROVAL_REQUIRED: ${path}`);
      continue;
    }
    const allowed=stream==="cross" ||
       (stream==="research" && (kind==="research"||kind==="ledger")) ||
       (stream==="docs" && kind==="docs") ||
       (stream==="core" && kind==="core");
    if(!allowed) errors.push(`CROSS_STREAM_CHANGE_DENIED: ${String(path)} is ${kind}, branch is ${stream||"invalid"}`);
  }
  return {ok:errors.length===0,stream,kinds:[...kinds].sort(),errors};
}

async function checkPullRequest({github,context,core}) {
  const eventPR=context.payload.pull_request;
  if (!eventPR) {core.setFailed("No pull_request payload");return;}
  const {owner,repo}=context.repo,pull_number=eventPR.number;
  const pr=(await github.rest.pulls.get({owner,repo,pull_number})).data;
  if(pr.base.ref!=="main") {core.setFailed("SCOPE_BASE_INVALID: expected main");return;}
  if(pr.head.sha!==eventPR.head.sha) {core.setFailed("SCOPE_HEAD_MOVED: rerun on current PR head");return;}
  if(pr.head.repo?.full_name!==`${owner}/${repo}`) {core.setFailed("SCOPE_HEAD_REPO_INVALID: scoped worktrees must publish inside canonical repository");return;}
  if(!Number.isSafeInteger(pr.changed_files) || pr.changed_files<1 || pr.changed_files>3000) {core.setFailed("SCOPE_FILE_COUNT_UNSUPPORTED: incomplete GitHub file listing risk");return;}
  const files=await github.paginate(github.rest.pulls.listFiles,{owner,repo,pull_number,per_page:100});
  if(files.length!==pr.changed_files) {core.setFailed(`SCOPE_FILE_LIST_INCOMPLETE: expected ${pr.changed_files}, got ${files.length}`);return;}
  const paths=[];
  for(const f of files) {
    paths.push(f.filename);
    if(f.status==="renamed") {
      if(!f.previous_filename) {core.setFailed(`SCOPE_RENAME_OLD_PATH_MISSING: ${f.filename}`);return;}
      paths.push(f.previous_filename);
    }
  }
  const result=validateScope(pr.head.ref,paths,(pr.labels||[]).map(x=>x.name));
  core.info(JSON.stringify({head:pr.head.sha,branch:pr.head.ref,changed_files:files.length,scope:result.stream,kinds:result.kinds,ok:result.ok}));
  if(!result.ok) core.setFailed(result.errors.join("\n"));
  else core.setOutput("workstream",result.stream);
}

module.exports = {classifyPath,validateScope,checkPullRequest};
