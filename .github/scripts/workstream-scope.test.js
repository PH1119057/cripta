"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {validateScope,classifyPath} = require("./workstream-scope.js");
const cases=[
  [
    "research own files",
    "research/r1",
    [
      "research/jobs/a.py",
      "tests/research/test_a.py",
      "scripts/research/calc.py"
    ],
    [],
    true
  ],
  [
    "research blocks trading",
    "research/r1",
    [
      "research/jobs/a.py",
      "src/bybit_workbench/x.py"
    ],
    [],
    false
  ],
  [
    "research blocks canon",
    "research/r1",
    [
      "docs/RESEARCH_COMPUTE_RULES_RU.md"
    ],
    [],
    false
  ],
  [
    "docs own files",
    "docs/rules",
    [
      "docs/DEVELOPMENT_RELEASE_RULES_RU.md",
      "README.md",
      "AGENTS.md",
      "operations/bootstrap/CHATGPT_PROJECT_INSTRUCTIONS_RU.txt"
    ],
    [],
    true
  ],
  [
    "docs blocks runtime",
    "docs/rules",
    [
      "operations/dashboard/app.py"
    ],
    [],
    false
  ],
  [
    "core own code",
    "core/exit",
    [
      "src/bybit_workbench/universal_exit/engine.py",
      "operations/connectivity/private_runtime.py",
      "tests/test_universal_exit_engine_p5.py",
      ".github/workflows/quality.yml",
      "bybit_workbench.spec"
    ],
    [],
    true
  ],
  [
    "core blocks catalogue",
    "core/exit",
    [
      "operations/dashboard/instrument_research_catalog.json"
    ],
    [],
    false
  ],
  [
    "research catalogue requires registration",
    "research/catalog",
    [
      "research/exporter.py",
      "operations/dashboard/instrument_research_catalog.json"
    ],
    [],
    false
  ],
  [
    "research catalogue approved",
    "research/catalog",
    [
      "research/exporter.py",
      "operations/dashboard/instrument_research_catalog.json"
    ],
    [
      "owner-approved-ledger-registration"
    ],
    true
  ],
  [
    "cross requires owner approval",
    "cross/workstream",
    [
      "docs/DOCUMENTATION_INDEX_RU.md",
      ".github/workflows/workstream-boundary.yml"
    ],
    [],
    false
  ],
  [
    "cross approved",
    "cross/workstream",
    [
      "docs/DOCUMENTATION_INDEX_RU.md",
      ".github/workflows/workstream-boundary.yml"
    ],
    [
      "owner-approved-cross-contour"
    ],
    true
  ],
  [
    "unknown branch name denied",
    "feature/test",
    [
      "research/job.py"
    ],
    [],
    false
  ],
  [
    "unknown path denied",
    "cross/workstream",
    [
      "outside/new.bin"
    ],
    [
      "owner-approved-cross-contour"
    ],
    false
  ],
  [
    "cross approved archive housekeeping",
    "cross/archive-cleanup",
    [
      "archive/legacy_script_folders_pre_2026-09-15/P53_1M_ENTRY_DISPLACEMENT_V1/file.py",
      "P53_1M_ENTRY_DISPLACEMENT_V1/file.py",
      "archive/patch_backups/old.txt",
      "patch_backups/old.txt",
      "archive/research_tools/exact_touch.py",
      "research_tools/exact_touch.py"
    ],
    [
      "owner-approved-cross-contour"
    ],
    true
  ],
  [
    "core blocks archive housekeeping",
    "core/archive-cleanup",
    [
      "archive/patches/old.txt",
      "patches/old.txt"
    ],
    [],
    false
  ],
  [
    "rename across scope denied old and new",
    "research/rename",
    [
      "research/new.py",
      "src/old.py"
    ],
    [],
    false
  ],
  [
    "core blocks research",
    "core/exit",
    [
      "scripts/research/foo.py"
    ],
    [],
    false
  ],
  [
    "invalid traversal denied",
    "research/a",
    [
      "research/../src/evil.py"
    ],
    [],
    false
  ],
  [
    "empty change list denied",
    "research/a",
    [],
    [],
    false
  ]
];
for(const [name,ref,paths,labels,expected] of cases){
  test(name,()=>assert.equal(validateScope(ref,paths,labels).ok,expected));
}
test("catalogue is special ledger",()=>assert.equal(classifyPath("operations/dashboard/instrument_research_catalog.json"),"ledger"));
test("archive path is canonical archive scope",()=>assert.equal(classifyPath("archive/legacy/file.py"),"archive"));
test("legacy Pxx root is archive scope",()=>assert.equal(classifyPath("P53_1M_ENTRY_DISPLACEMENT_V1/file.py"),"archive"));
test("legacy patch root is archive scope",()=>assert.equal(classifyPath("patch_backups/old/file.py"),"archive"));
test("legacy root file is archive scope",()=>assert.equal(classifyPath("P47A_README.txt"),"archive"));
test("legacy launcher is archive scope",()=>assert.equal(classifyPath("START_WORKBENCH.cmd"),"archive"));
test("active build spec is core scope",()=>assert.equal(classifyPath("bybit_workbench.spec"),"core"));
test("retired production root is unknown",()=>assert.equal(classifyPath("production/src/x.py"),"unknown"));
test("unknown path fail closed",()=>assert.equal(classifyPath("outside/new.py"),"unknown"));
