# CRIPTA — core work rules for ChatGPT / Codex / developer

**Версия:** 2.5 · 2026-09-19
**Статус:** обязательный core process contract
**Source of truth:** GitHub `PH1119057/cripta:main`; `/srv/cripta/source_checkout`
— synchronized operational mirror, not a second authority.

Этот файл намеренно сокращён. Detailed release/PostgreSQL/toolchain rules и
research/compute rules вынесены в routed canonical docs, чтобы не читать
installer/research детали в каждой задаче.

## 1. Scope discipline

Сначала определить scope:

```text
STABILIZATION
INFRASTRUCTURE
TRADING LOGIC
RESEARCH
DOCUMENTATION
RELEASE / DEPLOY
```

Finding не является разрешением автоматически менять architecture/policy.

## 2. Source of truth

AUTHORITATIVE:

```text
GitHub PH1119057/cripta:main
```

OPERATIONAL MIRROR:

```text
/srv/cripta/source_checkout
```

Mirror обязан быть синхронизирован с verified GitHub ref перед source-based
forensic/deploy. Project Source, memory, old chats, ZIP, transport manifests,
local C:\cripta and history are auxiliary only.

## 3. Mandatory pre-read and routing

New chat:
1. docs/CHATGPT_INTERACTION_RULES_RU*.md
2. this WORK file
3. CRIPTA_ARCHITECTURE_RULES_RU_*.md
4. docs/DOCUMENTATION_INDEX_RU*.md
5. docs/CRIPTA_GLOSSARY_RU*.md
6. docs/CURRENT_PROJECT_MAP_RU*.md

Then routed pre-read:
- Strategy / Entry / Exit / Execution -> docs/TRADING_CONTOUR_RU*.md
- MAYAK / Dispatcher / Monitoring / Lifecycle Supervisor / Position Supervisor /
  Analyst -> docs/OBSERVATION_ANALYTICS_RU*.md
- patch / Git / PostgreSQL / package / release / deploy / rollback ->
  docs/DEVELOPMENT_RELEASE_RULES_RU*.md
- research / replay / OOS / holdout / large data / long compute ->
  docs/RESEARCH_COMPUTE_RULES_RU*.md

If a task crosses routes, read all relevant routed contracts.

## 4. Hard Stop

If requested work conflicts with active canon:

```text
CANON_CONFLICT=YES
HARD_STOP=YES
CANON_UPDATE_REQUIRED=YES
OWNER_DECISION_REQUIRED=YES
```

STOP -> do not change implementation -> identify exact conflict -> obtain owner
decision -> update canon -> only then implementation.

If code differs from canon, that is FINDING, not permission to rewrite either
side silently.

## 4.1 Terminology Hard Stop

docs/CRIPTA_GLOSSARY_RU*.md is the single token/term authority.

If a term is missing or physically ambiguous:

```text
TERM_AMBIGUOUS=YES
HARD_STOP=YES
OWNER_DECISION_REQUIRED=YES
```

Do not infer geometry, H3/H9, Entry/Exit, slot/fault semantics or voice artifacts.

## 4.2 Research never becomes canon automatically

Research of any age is evidence only.

```text
RESEARCH / EVIDENCE
-> OWNER DECISION
-> CANON / NEW STRATEGY VERSION
-> TEST / SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
```

Dataset reuse does not imply logic/policy reuse.

## 5. Upper architecture

```text
MAYAK
  ↓
DISPATCHER
  ↓
STRATEGY
 ├─ ENTRY
 └─ EXIT
  ↓
EXECUTION
  ↓
EXCHANGE
```

Exactly five top-level layers. Risk is not another top layer.

Strategy is the only owner of trading meaning/settings. Materializer creates
exact immutable EntryPlan/ExitPlan. Entry/Exit universally execute plans.
Execution performs already-approved mutation and invents no trading policy.

Missing/unsupported mandatory Strategy-owned state = fail-closed.

## 5.1 End-to-end contract for decision/execution fields

Any field affecting decision or execution must have explicit ownership and a
proved end-to-end path:

```text
OWNER
-> AUTHORING / CANON
-> MATERIALIZATION
-> DURABLE STORAGE
-> ACTIVE REGISTRY / READ MODEL
-> CONSUMER
-> DECISION / REQUEST
-> EXECUTION OR SHADOW EVIDENCE
-> TEST
```

No consumer path -> field cannot be enabled for real execution.

## 6. Status vocabulary

Always distinguish:

```text
CHECKED HERE
NOT CHECKED HERE
FINDING
RESEARCH RESULT
OWNER DECISION
CANON
IMPLEMENTED
DEPLOYED
RUNTIME VERIFIED
```

For runtime verification, use the dimensions defined in GLOSSARY:
- RUNTIME LIVENESS VERIFIED
- RUNTIME BEHAVIOR VERIFIED

Bare service-active status is not behavior verification.

Commit != deploy. Deploy != loaded runtime. Zero faults != tested fault behavior.

## 7. Git-first release invariant

Detailed rules: docs/DEVELOPMENT_RELEASE_RULES_RU*.md.

Core invariant:

```text
BASELINE / FORENSIC
-> ISOLATED WORKTREE / OVERLAY
-> FULL CHECKS
-> EXACT COMMIT
-> PUSH
-> INDEPENDENT REMOTE SHA VERIFICATION
-> BACKUP
-> DEPLOY EXACT VERIFIED COMMIT
-> POST-DEPLOY / RUNTIME VERIFICATION
```

Normal checkpoint distinguishes:

```text
REMOTE_HEAD
SOURCE_HEAD
INSTALLED_COMMIT
LOADED_COMMIT
```

A ZIP is transport, never source authority. DEPLOY EXACT VERIFIED COMMIT is the
release identity rule.

## 8. Re-arm / LIVE is never a patch side effect

Patch/deploy must not silently arm real trading.

LIVE rights require the current owner-approved LIVE-arm checklist in
docs/TRADING_CONTOUR_RU*.md and explicit owner approval.

## 9. Tests follow canon, not implementation convenience

Do not change expectation merely to get green and do not restore old architecture
for a stale test.

When canon changes, governance/architecture tests must be consciously updated to
the new contract in the later implementation/test changeset.

## 10. File-space isolation

ChatGPT runtime, server filesystem, GitHub, Project Source and local user machine
are distinct spaces unless an explicit verified transfer exists.

Never invent sandbox/download paths from a file name or connector reference.

## 11. Security

SECURITY.md applies to all work.

Credentials, secret values, private key paths/material and auth headers are not
canonical documentation content. Public-repo/history secret scanning and
visibility decisions follow docs/DEVELOPMENT_RELEASE_RULES_RU*.md.

## 12. Language and reporting

Owner communication is primarily Russian. English is retained for exact code/API/
DB identifiers where translation hurts precision.

Trading reports use «после комиссий».

## 13. Main process principle

```text
UNDERSTAND THE ENVIRONMENT ONCE
-> BUILD ONCE
-> RUN THE STRONGEST PRACTICAL GATE
-> PUBLISH EXACTLY
-> DEPLOY EXACTLY
-> VERIFY EXACTLY
-> STOP AT STABLE CHECKPOINT
```

Long chains of repair/build versions indicate a process defect and require
forensic/class-wide correction, not more blind iterations.
