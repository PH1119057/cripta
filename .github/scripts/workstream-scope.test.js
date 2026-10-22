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
      ".github/workflows/quality.yml"
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
      "archive/new.bin"
    ],
    [
      "owner-approved-cross-contour"
    ],
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
test("unknown path fail closed",()=>assert.equal(classifyPath("outside/new.py"),"unknown"));
