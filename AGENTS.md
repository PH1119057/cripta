# CRIPTA — bootstrap для исполнителя

Вся текущая содержательная документация CRIPTA хранится в `docs/`.
Этот `AGENTS.md` — только корневой bootstrap и не является отдельным каноном.

При любом tool denial: `docs/DEVELOPMENT_RELEASE_RULES_RU.md §19.5–19.6`, `docs/CHATGPT_INTERACTION_RULES_RU.md §11.1`; для research также `docs/RESEARCH_COMPUTE_RULES_RU.md`. Distinguish OpenAI upstream block, SentinelX policy, Unix EACCES, job timeout; no repeated blocked calls or privilege changes to bypass safety.

## Mandatory pre-read

Для ChatGPT первым читать:

1. `docs/CHATGPT_INTERACTION_RULES_RU*.md`
2. `docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`
3. `docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md`
4. `docs/DOCUMENTATION_INDEX_RU*.md`
5. `docs/CRIPTA_GLOSSARY_RU*.md`
6. `docs/CURRENT_PROJECT_MAP_RU*.md`
7. `docs/SECURITY.md`

Все 11 current docs должны быть доступны в Project Source. Наличие файла там
не отменяет routed reading и не делает routed doc mandatory every-chat.

Current ChatGPT UI hard limits: Project Instructions <= 8000 chars,
Project Source <= 12 files; current bundle = 11. Не дробить current docs ради
удобства. 12-й current file требует owner decision; 13-й — consolidation first.

При работе с конкретным слоем дополнительно читать его active routed document
из `docs/DOCUMENTATION_INDEX_RU*.md`.

Затем по task route:

- Strategy / Entry / Exit / Execution -> `docs/TRADING_CONTOUR_RU*.md`
- MAYAK / Dispatcher / Monitoring / Lifecycle Supervisor / Position Supervisor / Analyst -> `docs/OBSERVATION_ANALYTICS_RU*.md`
- patch / Git / PostgreSQL / package / release / deploy / rollback -> `docs/DEVELOPMENT_RELEASE_RULES_RU*.md`
- research / replay / OOS / holdout / large-data / long-compute / data forensic -> `docs/RESEARCH_COMPUTE_RULES_RU*.md`
- server-side writer/effective-actor change -> дополнительно
  `docs/DEVELOPMENT_RELEASE_RULES_RU*.md §5.1 + §19.1–19.4` и
  `docs/CURRENT_PROJECT_MAP_RU*.md §1.4`


## Source of truth

```text
AUTHORITATIVE: GitHub PH1119057/cripta:main
OPERATIONAL MIRROR: /srv/cripta/source_checkout
```

Owner-controlled real execution smoke-test — explicit operator-only diagnostic,
not StrategySignal/EntryDecision semantics. Exact current contract:
TRADING_CONTOUR §4.8; architecture boundary: ARCH §7.1.

Current R1 Entry-time exchange protection contract: catastrophic SL `-10%` +
price-based TP at the causal opposite current L5-3 inner boundary; subsequent TP
replacement belongs to Universal Exit. Exact owner document:
`docs/TRADING_CONTOUR_RU.md` Protection and activation boundary.

PAPER/REAL mode must not fork Strategy/Entry/Exit semantics. One attempt selects
exactly one execution environment; read current contract in ARCH §7.2,
TRADING_CONTOUR §4.9 and GLOSSARY §11. Do not infer behavior from historical
`PaperTradeRuntime`; real arm requires `PAPER_REAL_DECISION_PARITY=PASS`.

Dashboard presentation/read-model deploy отделён от trading runtime:
он не требует disarm/Execution OFF/restart торговых сервисов. Static UI и
строго read-only projections/aggregations могут обновляться независимо; backend
read-model change может restart только Dashboard. Auth/control/mutation или
trading-decision code остаётся full runtime change. См. DEVELOPMENT_RELEASE §8.1.

Project Source, память, старые чаты/ZIP, локальные копии, Git history,
`archive/**`, `patch_backups/**` и historical payload docs не являются
current authority.

## Hard Stop

При конфликте с активным каноном:

```text
CANON_CONFLICT=YES
HARD_STOP=YES
CANON_UPDATE_REQUIRED=YES
OWNER_DECISION_REQUIRED=YES
```

Если термин отсутствует в `docs/CRIPTA_GLOSSARY_RU*.md` или физически
неоднозначен — не додумывать, запросить owner decision.

## Architecture capability Hard Stop

Если patch/решение удаляет, ослабляет или переносит ранее требуемую capability,
до implementation обязательна BEFORE/AFTER capability matrix. Capability без
доказанного нового owner/contract/consumer path означает:

~~~text
ARCHITECTURE_CAPABILITY_GAP=YES
HARD_STOP=YES
OWNER_DECISION_REQUIRED=YES
PREPARED=NO
~~~

См. META §15, WORK §4.3, ARCH §13.

## Server-side write scripts

Перед написанием/запуском script, который пишет на сервере или переключает
effective actor, обязательно выполнить permission contract из
`docs/DEVELOPMENT_RELEASE_RULES_RU*.md §19.1–19.4`: exact actor/path matrix +
read-only preflight до mutation. Перед authoring также читать current server
profile в `docs/CURRENT_PROJECT_MAP_RU*.md §1.4`. Не лечить DENIED через
`chmod 777`, recursive chown или запуск всего workflow от root.


## Filesystem contour invariant

Target roots: source `/srv/cripta/source_checkout`, runtime code
`/srv/cripta/runtime`, research `/data/cripta/research`, historical archive
`/data/cripta/script_archive`. Новые research writes/worktrees/runs должны жить
на data-disk; runtime не может импортировать/исполнять research artifacts
напрямую. См. `DEVELOPMENT_RELEASE §42`, `RESEARCH_COMPUTE §17`,
`CURRENT_PROJECT_MAP §1.5`.


Для server/DB forensic строго применять `docs/DEVELOPMENT_RELEASE_RULES_RU.md §19.5`: tool policy, actual effective actor и mutation authorization независимы. Датированные safe read-only команды — `docs/CURRENT_PROJECT_MAP_RU.md §1.4`. Успех SELECT не доказывает WRITE или LIVE authorization.

## Bootstrap synchronization invariant

Точный current document set и routing определяет
`docs/DOCUMENTATION_INDEX_RU*.md`. Если меняются состав документов, пути,
mandatory pre-read или routed reading, корневые `README.md` и `AGENTS.md`
обязаны изменяться в том же documentation changeset. Устаревшая ссылка в этом
bootstrap является documentation defect.

Exact ChatGPT UI Project Instructions template:
`operations/bootstrap/CHATGPT_PROJECT_INSTRUCTIONS_RU.txt`.
Это derived bootstrap artifact, не Project Source и не отдельный authority.

## Recovery implementation route

Recovery/reconnect tasks require ARCH + TRADING_CONTOUR + GLOSSARY + DEVELOPMENT_RELEASE pre-read. Must verify idempotent Exchange-to-DB state projection, paginated snapshots, exact historical execution IDs, 60-position restart/recovery tests and no replay of uncertain Exchange mutations. Do not confuse canonical approval with deployed implementation.
## Parallel Git workstream guard

Каждая задача — отдельный актуальный-base worktree и scoped branch:
`research/*` для `research/**` / `tests/research/**` /
`scripts/research/**`; `docs/*` только для canonical docs и bootstrap;
`core/*` для executable code / tests / CI. Cross-contour `cross/*`
требует явного OWNER DECISION и review marker. Никогда не добавлять
посторонние files, не обходить gate сменой branch name; не выполнять
source-sync или deploy из isolated research worktree. Owner правил:
`docs/DEVELOPMENT_RELEASE_RULES_RU.md §50`; research publication:
`docs/RESEARCH_COMPUTE_RULES_RU.md §24`.

## Continuity: новый чат

НЕ создавать новую ветку по ранее начатой задаче без discovery: Issue/PR,
GitHub branch, registered server worktree и его dirty/untracked state,
running jobs, effective writer и previous checkpoint. Exact safe match ->
resume existing branch/worktree, не reset/overwrite; ambiguity -> HARD_STOP
для mutation и owner decision. Полный контракт: DEVELOPMENT_RELEASE §50.4.
