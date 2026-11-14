# CRIPTA — core work rules for ChatGPT / Codex / developer

**Версия:** 3.8 · 2026-11-14
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
forensic/deploy. Project Source is the working copy of current canonical docs if its version
matches GitHub main: read and apply it. Memory and past conversations
preserve owner decisions and task continuity, but do not replace current
host/runtime/ACL evidence. Historical ZIP/archive/Git history are evidence
rather than a competing current policy.

## 3. Mandatory pre-read and routing

New chat: FULL META / WORK / SECURITY and INDEX §1–9 routing. ARCH, GLOSSARY, MAP and all routed contracts are mandatory by the exact task route, not read in full for every casual turn. Recheck GitHub main and relevant changed sections before each substantive task; see META §17 and INDEX §5.

Then routed pre-read:
- Strategy / Entry / Exit / Execution -> docs/TRADING_CONTOUR_RU*.md
- MAYAK / Dispatcher / Monitoring / Lifecycle Supervisor / Position Supervisor /
  Analyst -> docs/OBSERVATION_ANALYTICS_RU*.md
- patch / Git / PostgreSQL / package / release / deploy / rollback ->
  docs/DEVELOPMENT_RELEASE_RULES_RU*.md
- research / replay / OOS / holdout / large data / long compute / data forensic ->
  docs/RESEARCH_COMPUTE_RULES_RU*.md
- any server-side script/service/job that writes filesystem state or changes
  effective actor -> additionally DEVELOPMENT_RELEASE §5.1 + §19.1–19.4 and
  CURRENT_PROJECT_MAP §1.4 regardless of the primary route.

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

## 4.3 Capability migration Hard Stop

Архитектурный cleanup не считается завершённым, если он только удалил
неправильного owner capability.

Для любого переноса/удаления/ослабления обязанности до implementation требуется
BEFORE / AFTER CAPABILITY MATRIX минимум с полями:

~~~text
CAPABILITY
BEFORE_OWNER
BEFORE_STATUS
AFTER_OWNER
AFTER_STATUS
REPLACEMENT_IMPLEMENTED
MIGRATION_REQUIRED
TEST_EVIDENCE
~~~

Если ранее требуемая capability после changeset имеет NO OWNER,
NOT IMPLEMENTED или неизвестный consumer path:

~~~text
ARCHITECTURE_CAPABILITY_GAP=YES
HARD_STOP=YES
PREPARED=NO
OWNER_DECISION_REQUIRED=YES
~~~

Нельзя продолжать только потому, что новая локальная boundary архитектурно
чище. Сначала owner decision + канон задают нового владельца/отказ от
capability, затем implementation.

Перед architecture-sensitive change исполнитель отдельно отвечает:

1. что перестаёт работать;
2. какие existing capabilities затронуты;
3. кто владел ими до изменения;
4. кто владеет ими после;
5. реализован ли replacement;
6. не теряется ли causal/history/replay comparability;
7. какие tests/runtime evidence доказывают завершённость migration.

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

Содержательная документация текущего проекта хранится только в `docs/`.
Корневые `README.md` и `AGENTS.md` являются только техническими entrypoints и
не создают отдельный authority. Изменение active document set, путей,
mandatory pre-read или routed reading требует в том же documentation changeset
обновить `docs/DOCUMENTATION_INDEX_RU.md`, корневые `README.md` и `AGENTS.md`.

## 10.1 Effective-actor permission preflight обязателен

Перед написанием или запуском server-side script, который создаёт/изменяет/
переименовывает/удаляет filesystem object либо переключает actor через
`sudo`, `runuser`, systemd или PostgreSQL tooling, исполнитель обязан сначала
прочитать permission contract в `docs/DEVELOPMENT_RELEASE_RULES_RU*.md §19.1–19.4`
и доказать права exact effective actor на exact paths.

Script не считается `PREPARED` и не запускается, пока permission preflight не
PASS. `ReadWritePaths=` systemd, root-shell, наличие sudo или успешный доступ
другого Unix-user не являются доказательством прав фактического writer.

Для стандартной read-only проверки используется
`operations/infrastructure/cripta-permission-preflight` либо доказанно
эквивалентная проверка. Permission failure после запуска — preparation defect и
требует class-wide audit, а не `chmod 777`, recursive chown или перехода на root.

## 10.2 Current server profile читается перед server-side authoring

Если задача пишет/запускает server-side script/service/job, дополнительно к
DEVELOPMENT_RELEASE §19.1–19.4 прочитать current operational snapshot
`docs/CURRENT_PROJECT_MAP_RU*.md §1.4`.

MAP §1.4 даёт current non-secret actor/path profile; DEVELOPMENT_RELEASE
определяет постоянные правила. Если фактический сервер расходится с MAP,
использовать фактический CHECKED HERE state как FINDING, обновить MAP в том же
changeset и только потом продолжать mutation.

## 10.3 ChatGPT UI capacity — hard operational constraint

OWNER CHECKED HERE 2026-09-28:
- Project Instructions: максимум 8000 символов;
- Project Source: максимум 12 файлов;
- current canonical Project Source bundle: 11 файлов.

Нельзя решать рост документации простым split current docs. Новый 12-й current
file требует owner decision; до появления 13-го current file обязательна
консолидация существующих документов. Project Instructions при росте
сокращаются до bootstrap и ссылаются на канон, а не копируют его.

## 10.4 Filesystem contours разделены физически

OWNER DECISION 2026-09-29:

```text
SOURCE_ROOT          = /srv/cripta/source_checkout
RUNTIME_CODE_ROOT    = /srv/cripta/runtime
RESEARCH_ROOT        = /data/cripta/research
HISTORICAL_ARCHIVE   = /data/cripta/script_archive
```

Это target filesystem contract. До завершения migration фактические legacy
runtime/research paths перечисляются в `CURRENT_PROJECT_MAP_RU*.md` и не
считаются новым разрешением создавать там данные.

Инварианты:
- source mirror хранит опубликованный Git source/docs и repository metadata, а
  не runtime outputs или research results;
- runtime code/release artifacts не импортируют и не исполняют код напрямую из
  `RESEARCH_ROOT`;
- все новые server-side research worktrees, run outputs, temporary files,
  source snapshots, logs, manifests и caches создаются на `/data`, внутри
  approved research contour;
- research -> production проходит только через GitHub/canon/tests/release, а не
  прямым копированием исследовательского файла в live runtime;
- legacy research path после начала migration = transition-only: новые runs
  туда не направляются, а существующие объекты переносятся/архивируются только
  после exact active-reference check.

Detailed ownership: `DEVELOPMENT_RELEASE §42`,
`RESEARCH_COMPUTE §17`, current migration status: `CURRENT_PROJECT_MAP §1.5`.


## 11. Security

docs/SECURITY.md applies to all work.

Credentials, secret values, private key paths/material and auth headers are not
canonical documentation content. Public-repo/history secret scanning and
visibility decisions follow docs/DEVELOPMENT_RELEASE_RULES_RU*.md.

## 12. Language and reporting

Owner communication is primarily Russian. English is retained for exact code/API/
DB identifiers where translation hurts precision.

Trading reports use «после комиссий».

## 12.1 Обязательный расчётный контракт в аналитических отчётах

Для любого анализа торговых данных, replay/backtest/OOS, сравнительной
статистики Strategy/Entry/Exit или расчёта economics исполнитель обязан **до
первой таблицы, метрики или вывода** явно указать exact contract расчёта.
Минимальный блок:

```text
ENTRY
EXIT
EXECUTION / COST MODEL
FILTERS / FLAGS
DATASET / PERIOD / UNIVERSE
POSITION / RE-ENTRY / TERMINAL HANDLING
```

Требования:
- `ENTRY` описывается физически и полностью: geometry/horizon, direction,
  thresholds, stability/confirmation conditions и момент, в который они
  проверяются;
- `EXIT` перечисляет все активные причины закрытия и их приоритет/first-event
  semantics; если сравниваются несколько Exit, у каждой таблицы должен быть
  однозначно указан конкретный Exit;
- `EXECUTION / COST MODEL` указывает maker/taker assumption, комиссии,
  slippage, nominal/capital model и fill-price semantics, если они влияют на
  результат. Если владелец явно не задал иной sensitivity-case, базовый
  research/backtest economics считается консервативно как `TAKER / TAKER` для
  Entry и Exit; потенциальная maker-экономия не имеет права улучшать baseline;
- `FILTERS / FLAGS` перечисляет все дополнительные включённые/выключенные
  фильтры и research flags (`STAY`, stabilization, cooldown, regime filter и
  т.п.), а не только новый исследуемый признак;
- `DATASET / PERIOD / UNIVERSE` фиксирует источник данных, exact period и набор
  symbol/direction;
- `POSITION / RE-ENTRY / TERMINAL HANDLING` фиксирует правила same-side signal,
  opposite signal/flip, повторного Entry, unresolved position в конце периода и
  иных lifecycle деталей, влияющих на число/результат сделок.

Запрещено молча наследовать Entry/Exit/flags из предыдущего сообщения, старого
чата, памяти или соседнего research run. Если exact contract результата нельзя
однозначно восстановить по executable/source snapshot/result manifest, такой
результат нельзя публиковать как сопоставимый: сначала восстановить contract
или явно поставить `BLOCKED / NOT CHECKED HERE`.

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

## 14. Continuity across ChatGPT chats — OWNER DECISION 2026-10-22

Before starting a supposedly new Git branch/worktree or rerunning a job on a previously discussed task, apply `DEVELOPMENT_RELEASE §50.4`: discover matching GitHub Issue/PR/branch, registered server worktree (including dirty/untracked bytes), active writer and actual job state; reuse the one exact verified unfinished task. Never infer that GitHub contains local uncommitted changes. Ambiguous or inaccessible pending work is a mutation HARD STOP, not permission to start over. Persist task ID/HEAD/worktree/job/evidence/next-step checkpoint for future chats; keep the one GitHub main authority unchanged.

## 15. DOCS / CORE / RESEARCH and refusal-preflight

DEV §50 defines three **workstream** scopes, not a replacement of ARCH §1 five technical layers. DOCS owns canon/index/bootstrap; CORE owns runtime/trading/observability/DB/Git/release; RESEARCH owns datasets/replay/OOS/evidence and jobs. SECURITY is one common canon. Context reuse requires exact SHA/task equivalence, not old-chat confidence.

Before any denial or technical objection to an owner-approved task, classify exact operation and correct Unix/DB actor **first** per DEV §13.1. Do not ask `cripta` to run migration/DDL, do not confuse a test-only `SELECT` grant with production ACL, and do not suggest privilege escalation to fix transaction races. META §17.3 requires the current DEV §19.5–19.6 / MAP §1.4 / specialized route and an exact tool-capability/actor/path/DB/authorization proof. Never mislabel a tool policy block as Unix permission or a wrong actor as no server rights. WORK §12.1 exact ENTRY/EXIT/COST/FILTERS/DATASET/POSITION report requirements stay in force until any future approved consolidation with RESEARCH_COMPUTE.

## 16. Owner one-off exception and persistent research execution — 2026-11-14

A scoped, explicit owner override of an INTERNAL project workflow is a
one-time operational decision when security/tool/Unix/DB/LIVE and
architecture-capability gates still pass; execute without re-approval,
record outcome and ask once whether to make the exception permanent canon
(META §4.1). Architectural/Strategy/intercomponent contract mutations
still require CANON UPDATE before dangerous implementation.

Long-running research/replay jobs are independent supervised server jobs
(RESEARCH_COMPUTE §8.1), **never** synchronous multi-hour SentinelX
tool invocations. SentinelX is a bounded control/status rail, not the
lifetime owner of the calculation. No newly assumed tool/actor grants.
