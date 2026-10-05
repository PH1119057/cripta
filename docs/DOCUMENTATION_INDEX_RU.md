# CRIPTA — активный комплект документации

**Версия:** 3.7
**Дата:** 2026-10-02
**Статус:** канонический индекс документации

# 1. Цель

В проекте существует один небольшой активный комплект документов.
Физическое наличие старого файла в repository/history не даёт ему authority.

Вся текущая содержательная документация проекта хранится только в `docs/`.
Корневые `README.md` и `AGENTS.md` являются техническими entrypoints/bootstrap,
а не самостоятельным каноном. Расположение файла не задаёт authority: роли и
приоритет определяет только этот INDEX.

# 2. Роли и приоритет

```text
META — docs/CHATGPT_INTERACTION_RULES_RU*.md

LEVEL 0 — confirmed current owner decision.

LEVEL 1 — docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md
          docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md
          docs/CRIPTA_GLOSSARY_RU*.md

LEVEL 2 — docs/TRADING_CONTOUR_RU*.md
          docs/OBSERVATION_ANALYTICS_RU*.md

ROUTED PROCESS CANON —
          docs/DEVELOPMENT_RELEASE_RULES_RU*.md
          docs/RESEARCH_COMPUTE_RULES_RU*.md

TECHNICAL SECURITY BASELINE — docs/SECURITY.md
          применяется ко всей работе, но не задаёт trading policy.

LEVEL 3 — docs/CURRENT_PROJECT_MAP_RU*.md

LEVEL H — Git history, archive, patch payload docs,
          old research/evidence/runbook/handoff/Pxx/EO/SE/PASS.
```

Все 11 current docs входят в ChatGPT Project Source. Это не означает, что все
11 читаются перед каждым ответом: SECURITY входит в base pre-read, а routed
documents читаются только по соответствующему task route.

If owner decision conflicts with canon:

```text
CANON_CONFLICT=YES
HARD_STOP=YES
CANON_UPDATE_REQUIRED=YES
OWNER_DECISION_REQUIRED=YES
```

# 3. 11 файлов ChatGPT Project Source

Все current документы из `docs/` должны быть доступны в Project Source:

1. `docs/CHATGPT_INTERACTION_RULES_RU*.md`
2. `docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md`
3. `docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md`
4. `docs/DOCUMENTATION_INDEX_RU*.md`
5. `docs/CRIPTA_GLOSSARY_RU*.md`
6. `docs/CURRENT_PROJECT_MAP_RU*.md`
7. `docs/SECURITY.md`
8. `docs/TRADING_CONTOUR_RU*.md`
9. `docs/OBSERVATION_ANALYTICS_RU*.md`
10. `docs/DEVELOPMENT_RELEASE_RULES_RU*.md`
11. `docs/RESEARCH_COMPUTE_RULES_RU*.md`

Project Source availability и mandatory reading — разные понятия.
Используется семейство имени, а не номер версии/UI suffix.
`CHATGPT_INTERACTION_RULES_RU*.md` читается первым.

OWNER CHECKED HERE 2026-09-28: ChatGPT Project Source hard limit = 12 files.
Current bundle = 11 files. Нельзя дробить current canonical document так, чтобы
bundle превысил limit. Добавление 12-го current file требует owner decision;
перед любым потенциальным 13-м file сначала консолидировать existing docs и
обновить этот INDEX.

# 4. Корневые entrypoints

`README.md` и `AGENTS.md` — единственные текущие Markdown-документы, которые
остаются в корне repository.

- `README.md` — короткий человекочитаемый вход в проект и ссылки на current docs;
- `AGENTS.md` — GitHub-only bootstrap для Codex/разработчика/робота.

Они не создают самостоятельный архитектурный или торговый контракт. Если
меняются состав current docs, их пути, mandatory pre-read или routed reading,
`README.md` и `AGENTS.md` обязаны быть обновлены в том же changeset, что и
этот INDEX. Устаревшая ссылка в root bootstrap является documentation defect.

# 5. Mandatory pre-read and routed pre-read

Base new-chat pre-read:
1. docs/CHATGPT_INTERACTION_RULES_RU*.md
2. docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md
3. docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md
4. docs/DOCUMENTATION_INDEX_RU*.md
5. docs/CRIPTA_GLOSSARY_RU*.md
6. docs/CURRENT_PROJECT_MAP_RU*.md
7. docs/SECURITY.md

Then route:
- Strategy / Entry / Exit / Execution -> docs/TRADING_CONTOUR_RU*.md
- MAYAK / Dispatcher / Monitoring / Lifecycle Supervisor / Position Supervisor /
  Analyst -> docs/OBSERVATION_ANALYTICS_RU*.md
- patch / Git / PostgreSQL / packaging / release / deploy / rollback ->
  docs/DEVELOPMENT_RELEASE_RULES_RU*.md
- research / replay / OOS / holdout / large data / long compute / data forensic ->
  docs/RESEARCH_COMPUTE_RULES_RU*.md
- любой server-side script/service/job, который пишет на filesystem или меняет
  effective actor -> дополнительно docs/DEVELOPMENT_RELEASE_RULES_RU*.md
  (§5.1 и §19.1–19.4) + current server profile из
  docs/CURRENT_PROJECT_MAP_RU*.md §1.4, независимо от primary route.

Cross-route task -> read all relevant routed docs.

# 6. Историческая изоляция

В активном каталоге `docs/` находится только текущая содержательная документация и technical baseline; historical docs туда не возвращаются.

Исторические материалы могут физически сохраняться в `archive/**`, Git
history и внутри старых неизменяемых patch/research artifacts.

По умолчанию исполнитель не читает и не использует:
- `archive/**`;
- `patch_backups/**`;
- `*/payload/docs/**`;
- старые Pxx / EO / SE / PASS / handoff / runbook.

Открывать их можно только по явной исторической задаче.

# 7. Исследования и evidence

Исследование любой давности остаётся evidence.

```text
RESEARCH RESULT
-> OWNER DECISION
-> CANON / STRATEGY UPDATE
-> TEST / SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
```

# 8. Project Instructions

Project Instructions должны быть тонким bootstrap и ссылаться на семейства
имён с `*`, а не дублировать полный содержательный канон.

Они обязаны найти source of truth, загрузить правильный pre-read, применить
Hard Stop, не использовать память/history как канон и различать статусы.

# 9. Обновление Project Source

1. обновляется GitHub `main`;
2. существующим механизмом синхронизируется `/srv/cripta/source_checkout`;
   локальное зеркало владельца также получает GitHub `main` своим штатным sync-механизмом;
3. формируется набор всех 11 current Project Source файлов из `docs/`;
4. root `README.md` и `AGENTS.md` сверяются с current paths/pre-read/routing;
5. владелец полностью заменяет старые Project Source;
6. дополнительные материалы не получают authority автоматически.

Все current routed docs находятся в Project Source, но читаются только по
соответствующему route. Наличие в Project Source не расширяет их authority и
не делает их частью mandatory every-chat pre-read.

# 10. Согласованная ревизия Strategy settings — 2026-09-19

Текущее решение владельца о Strategy-specific настройках отражено согласованно
в активном пакете:

- `docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md` — ownership policy-блоков StrategyCard,
  разделение initial protection и dynamic Exit, правила experimental version;
- `docs/CRIPTA_ASSISTANT_WORK_RULES_RU_*.md` — authoring/fail-closed дисциплина и
  запрет research/history как trading default;
- `docs/CRIPTA_GLOSSARY_RU*.md` — термины Strategy settings, initial protection,
  Strategy Candidate/Draft и experimental Strategy version;
- `docs/TRADING_CONTOUR_RU*.md` — точное распределение настроек по policy-
  блокам и различие protective envelope / dynamic Exit;
- `docs/CURRENT_PROJECT_MAP_RU*.md` — текущий implementation status authoring.

В этой ревизии не утверждается конкретный канонический Exit и не закрепляются
исследовательские числа как global defaults. H3/touch/break-even/trailing и
временные protective boundaries становятся торговой policy только внутри exact
owner-approved Strategy version.

В рамках именно ревизии Strategy settings META routing и наблюдательный контур
не менялись. Последующая ревизия §11 обновляет META source-authority wording и
Lifecycle Supervisor contract, не передавая наблюдательному контуру торговые права.

# 11. Ревизия lifecycle / one-way / release contract — 2026-09-19

Owner-approved revision синхронизирует активный пакет по следующим инвариантам:

- GitHub `main` — единственный authority; `source_checkout` — synchronized
  operational mirror;
- release order един: test/overlay -> exact commit -> GitHub/remote verify ->
  backup -> deploy exact commit -> runtime evidence;
- независимые Strategy могут иметь противоположные signals, но текущий Bybit
  one-way physical position slot имеет только одного real lifecycle owner;
- reservation входит в формирование EntryDecision; `ACCEPTED` существует
  только после successful reservation;
- для real Strategy обязателен owner-approved initial loss-containment;
- emergency execution capability не является policy; автоматическое аварийное
  действие требует exact Strategy emergency/protection-failure contract;
- Strategy deactivation не меняет ExitPlan уже открытой StrategyPosition;
- lifecycle chain определяется в ARCH §9.1; TRADING_CONTOUR/OBS ссылаются на неё
  вместо дублирования;
- MAP использует явную status matrix и разносит runtime LIVENESS / BEHAVIOR
  вместо одного неоднозначного RUNTIME VERIFIED;
- GLOSSARY определяет previously ambiguous runtime/lifecycle terms и
  implementation-pass numbering.

Эта ревизия не включает real execution, не меняет Strategy records/Exchange
state и не утверждает конкретные stop/TP/H3/trailing числа.

# 12. Revision: slot admission / runtime evidence / routed process canon — 2026-09-19

Owner decision adds:
- single token->entity authority in GLOSSARY;
- EXCHANGE_POSITION_OWNERSHIP_CONFLICT as EntryDecision outcome, not critical fault;
- EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN as separate lifecycle fault;
- durable physical slot claim before ACCEPTED;
- slot claim + capital reservation as one all-or-nothing admission contract;
- fresh position-mode/positionIdx required state;
- EXCHANGE_POSITION_MODE_MISMATCH fail-closed contract;
- same-symbol hedge unsupported in current one-way contract;
- critical fault durable owner-notification delivery;
- single lifecycle definition in ARCH §9.1; TC/OBS reference it;
- runtime evidence split into LIVENESS and BEHAVIOR;
- explicit LIVE-arm checklist;
- Git-first package MANIFEST bound to release_commit;
- deploy-host GitHub write credential not required by default;
- WORK split into small core + routed DEVELOPMENT_RELEASE / RESEARCH_COMPUTE docs;
- public-repository full-history secret scan becomes security gate.

This revision is DOCUMENTATION/CANON only. New slot/mode/fault-delivery requirements
are not declared IMPLEMENTED/DEPLOYED/RUNTIME BEHAVIOR VERIFIED until code/DB/
runtime are separately audited and changed.

# 13. Implementation / runtime sync — 2026-09-21

После documentation-first канона 2026-09-19 выполнены implementation passes и
runtime verification. Текущий authoritative implementation checkpoint отражён в
`docs/CURRENT_PROJECT_MAP_RU*.md`.

CHECKED HERE:
- durable physical slot claim + atomic capital reservation implemented/deployed;
- fresh position-mode state contract implemented/deployed and fail-closed;
- canonical request-state lifecycle implemented/deployed;
- exact StrategyPosition slot/reservation binding implemented/deployed;
- Lifecycle Supervisor new invariant faults implemented/deployed;
- critical fault durable delivery/retry/ack/escalation implemented/deployed;
- LIVE-arm evidence/session gate implemented/deployed;
- Git-first release identity and exact installed/loaded commit contract implemented;
- controlled PostgreSQL behavior verification completed;
- current mainnet remains disarmed.

This sync does not authorize MICRO_LIVE or LIVE. Current real-arm blockers and
the exact runtime checkpoint are owned by CURRENT_PROJECT_MAP and
TRADING_CONTOUR §4.7.

# 14. Geometry / sync / SentinelX revision — 2026-09-25

Owner-confirmed: H9/H3 baseline формализован; Strategy-owned GeometrySpec может иметь shared causal Geometry timeline; research различает repeated touches и unique H9 episodes. GitHub main остаётся publication authority, server mirror и локальное зеркало владельца получают изменения штатными sync-механизмами. SentinelX является текущим ChatGPT server-management rail; потеря tool connection не равна server/job failure, требуется reconnect + host-state verification. PostgreSQL actor/interpreter/role smoke обязателен до DB-sensitive work. Geometry-timeline implementation этой документационной ревизией не объявляется DEPLOYED.

# 15. Documentation topology / root bootstrap revision — 2026-09-26

OWNER DECISION:
- вся текущая содержательная документация CRIPTA хранится в `docs/`;
- корневые `README.md` и `AGENTS.md` остаются только стабильными entrypoints;
- WORK, ARCH и SECURITY перенесены из root в `docs/` без изменения их authority;
- изменение состава документов, путей, mandatory pre-read или routed reading
  требует синхронного обновления INDEX + root README + root AGENTS;
- routed DEVELOPMENT_RELEASE и RESEARCH_COMPUTE имеют локальную непрерывную
  нумерацию разделов; старая разделённая сквозная нумерация удалена;
- MAP §18 зеркалит все обязательные имена LIVE-arm gates из TRADING_CONTOUR §4.7.

Эта ревизия не меняет trading behavior, Strategy ownership или runtime rights.
Неоднозначности Strategy Candidate/monitoring Strategy и физического owner
Geometry timeline этой ревизией не фиксируются и остаются без изменения.

# 16. Documentation consistency / operational sync — 2026-09-28

OWNER DECISION:
- Project Source содержит все 11 current docs из `docs/`;
- base pre-read содержит семь документов, включая обязательный `docs/SECURITY.md`;
- TRADING_CONTOUR, OBSERVATION_ANALYTICS, DEVELOPMENT_RELEASE и
  RESEARCH_COMPUTE остаются routed reading;
- process-state terms PREPARED/RUNNING/COMPLETE/FAILED/BLOCKED принадлежат
  GLOSSARY, а routed docs используют их без собственного переопределения;
- fault token может использоваться как causal `block_reason` без изменения
  своей token->entity классификации;
- физический owner Geometry timeline этой ревизией не фиксируется.

Operational finding private-runtime / Dispatcher закрывается отдельным
implementation commit и отражается в CURRENT_PROJECT_MAP; это не меняет
Strategy/Entry/Exit policy и не разрешает real execution.

# 17. Documentation maintenance invariant — 2026-09-28

Это постоянное правило для всех следующих документационных ревизий.

## 17.1 Topology / bootstrap

- current substantive docs живут только в `docs/`;
- root Markdown entrypoints — только `README.md` и `AGENTS.md`;
- все 11 current docs доступны в Project Source;
- ChatGPT Project Source hard limit = 12 files; current bundle = 11;
- current docs не дробятся ради удобства, если это расходует/превышает UI capacity;
- base pre-read и routed reading определяются только этим INDEX;
- изменение active document set, пути, base pre-read или routing в том же
  changeset обновляет INDEX + README + AGENTS + Project Instructions/bootstrap,
  если их фактический текст затронут.

## 17.2 Нумерация и ссылки

Перед renumber section обязателен search по current tree на все ссылки на
старые номера. Renumber разрешён только если все найденные references либо
обновляются атомарно, либо доказано, что они исторические/non-authoritative.

После changeset:
- top-level numbering каждого routed document непрерывна;
- internal/cross-doc references разрешаются в существующие sections;
- governance test фиксирует это там, где проверка детерминирована.

## 17.3 Один owner для повторяемых списков

Для token sets, lifecycle chain, LIVE-arm gate list и других duplicated mirrors
обязательно указан canonical owner document. Остальные документы либо
ссылаются на owner, либо зеркалят список только при наличии exact consistency
test. Нельзя независимо редактировать две «канонические» копии.

## 17.4 MAP time semantics

Любой section/table, названный `current`, `текущий`, `runtime checkpoint`
или `readiness`, обязан иметь явную дату `CHECKED HERE`.

Старый runtime evidence не удаляется только ради свежести, но помечается
`historical checkpoint <date>` и не смешивается с current state.

Если current state проверен частично, указываются exact проверенные dimensions,
а непроверенное остаётся `NOT CHECKED HERE`; старые значения не переносятся
молча как текущие.

## 17.5 Release/runtime identity

Каждый current production checkpoint, где обсуждается deploy/runtime, различает:

```text
REMOTE_HEAD
SOURCE_HEAD
INSTALLED_COMMIT
LOADED_COMMIT
OPERATIONAL_DELTA_COMMIT(S) [если есть]
```

Для operational delta обязательно указываются affected paths и evidence.
Наличие delta запрещает `SOURCE_LIVE_IDENTITY=PASS` для real-arm, пока
production state не сведён к одному exact verified release composition.

Сам documentation commit неизбежно меняет `REMOTE_HEAD/SOURCE_HEAD` после
публикации. Поэтому exact SHA внутри MAP разрешён только как явно подписанный
`pre-publication snapshot` / dated CHECKED HERE evidence; его нельзя
представлять как самоссылочный SHA содержащего его commit. После публикации
current GitHub/source identity проверяется внешним runtime/Git evidence.

## 17.6 Open architecture decisions не закрываются редакционной правкой

Известная открытая ownership/architecture boundary (например physical owner
Geometry timeline) остаётся открытой, пока владелец отдельно не принял решение.
Documentation cleanup не имеет права молча превращать её в CANON.

## 17.7 Обязательный documentation gate

Перед публикацией документационной ревизии проверяются минимум:

```text
CURRENT_GITHUB_MAIN_VERIFIED
ROOT_BOOTSTRAP_LINKS
PROJECT_SOURCE_SET
BASE_PRE_READ
ROUTING
SECTION_NUMBERING
CROSS_REFERENCES
CANONICAL_TOKEN/LIST CONSISTENCY
MAP_CURRENT_VS_HISTORICAL_LABELS
RELEASE/RUNTIME IDENTITY WORDING
OPEN_DECISIONS_NOT_SILENTLY_CLOSED
CAPABILITY_OWNERSHIP_CONSERVATION
CHATGPT_UI_CAPACITY
PROJECT_INSTRUCTIONS_LENGTH
PROJECT_SOURCE_FILE_COUNT
PROJECT_INSTRUCTIONS_TEMPLATE_SYNC
SERVER_SIDE_CROSS_ROUTE
FILESYSTEM_CONTOUR_SEPARATION
GOVERNANCE_TESTS
FULL_PYTEST when repository tests are affected/available
```

Documentation-only commit не считается безопасным только потому, что он не
меняет Python/code: неверный routing, stale MAP или ошибочная identity
формулировка являются реальными project defects.

## 17.8 Current server operational profile

Если production host используется для разработки, research, deploy или runtime
forensic, MAP содержит датированный non-secret server execution profile:

```text
ACTORS / RESPONSIBILITIES
SOURCE CHECKOUT OWNERSHIP / GIT READ RULES
CURRENT WRITE ROOTS
SYSTEMD WRITE SEMANTICS
SERVER-SIDE PROHIBITED PATTERNS
```

Profile является operational snapshot, не новой архитектурой. Изменение actor,
write-root, source-sync semantics или обязательного permission path требует:

```text
CHECK CURRENT HOST
-> UPDATE IMPLEMENTATION/OS STATE IF APPROVED
-> VERIFY EFFECTIVE ACTOR
-> UPDATE MAP §1.4 IN SAME CHANGESET
-> GOVERNANCE TEST
```

Sensitive credentials, private keys, SSH aliases/paths и secret-store details в
MAP не публикуются.


## 17.9 ChatGPT UI capacity limits

OWNER CHECKED HERE 2026-09-28:

```text
PROJECT_INSTRUCTIONS_MAX_CHARS = 8000
PROJECT_SOURCE_MAX_FILES       = 12
CURRENT_PROJECT_SOURCE_FILES   = 11
```

Эти значения являются current ChatGPT product/UI constraints, а не вечной
архитектурой. При изменении UI limits владелец повторно подтверждает факт, после
чего META/INDEX/bootstrap обновляются одним documentation changeset.

Правила:
- Project Instructions всегда должны помещаться в 8000 символов;
- Instructions остаются thin bootstrap и не дублируют большие canonical blocks;
- current Project Source bundle не превышает 12 files;
- split current document только ради размера/удобства запрещён, если он
  увеличивает bundle и расходует лимит;
- добавление 12-го current file требует отдельного OWNER DECISION;
- потенциальный 13-й current file = HARD STOP до консолидации existing docs;
- при необходимости расширить тему сначала добавить раздел в существующий
  owner-document либо объединить близкие contracts без потери authority/routing;
- после изменения состава Project Source обновляются INDEX + README + AGENTS +
  Project Instructions/bootstrap в одном documentation cycle.

## 17.10 Version-controlled Project Instructions template

Exact UI text поддерживается в:

`operations/bootstrap/CHATGPT_PROJECT_INSTRUCTIONS_RU.txt`

Статус файла: derived UI bootstrap artifact, не canonical document, не
Project Source и не дополнительный authority. Он не расходует
`PROJECT_SOURCE_MAX_FILES`.

Правило изменения:

```text
UPDATE CANON / INDEX FIRST
-> UPDATE TEMPLATE IN SAME CHANGESET
-> GOVERNANCE TEST <= 8000 CHARS
-> OWNER PASTES EXACT TEMPLATE INTO CHATGPT UI
-> UI COPY = CHECKED HERE only after owner confirms/save succeeds
```

Template не имеет права вводить независимую архитектуру, routing, token или
runtime fact. Каждое его содержательное правило должно ссылаться на current
canon либо быть явно owner-checked UI/product constraint, уже записанным в
META/INDEX.

Автоматический repository test проверяет:
- template существует;
- LF и CRLF representations обе помещаются в 8000 characters;
- current Project Source count остаётся <= 12;
- обязательные pre-read/routing/server-side safety markers присутствуют.

Саму сохранённую UI-копию GitHub test проверить не может. Пока владелец не
подтвердил вставку exact template, состояние UI copy = `NOT CHECKED HERE`.


## 17.11 Filesystem contour separation

OWNER DECISION 2026-09-29:

```text
SOURCE_ROOT        = /srv/cripta/source_checkout
RUNTIME_CODE_ROOT  = /srv/cripta/runtime
RESEARCH_ROOT      = /data/cripta/research
ARCHIVE_ROOT       = /data/cripta/script_archive
```

Canonical ownership:
- terminology -> GLOSSARY §17;
- permanent release/migration boundary -> DEVELOPMENT_RELEASE §42;
- research storage/source-snapshot discipline -> RESEARCH_COMPUTE §17;
- current host/migration state -> CURRENT_PROJECT_MAP §1.5.

Изменение любого из этих roots или dependency direction требует одного
documentation cycle минимум для INDEX + MAP + соответствующего routed contract
+ governance tests. Если изменение затрагивает bootstrap-visible path/rule,
README + AGENTS + Project Instructions template обновляются в том же cycle.

Target rule не делает migration `IMPLEMENTED`: MAP обязан отдельно показывать
legacy/current paths до завершённого deploy/runtime verification.
# 18. Observation contour / capability-conservation revision — 2026-10-02

OWNER DECISION 2026-10-02 is incorporated without adding a new current
canonical file.

Canonical ownership:
- META: architecture-capability Hard Stop and mandatory warning before silent
  loss of an existing capability;
- WORK: BEFORE/AFTER capability matrix and migration-preparedness gate;
- ARCH: conservation of capability ownership across layer-boundary changes;
- OBSERVATION_ANALYTICS: persistent multi-horizon MarketRegime, continuity/
  quality, MarketObservationAlert, useful strategy-agnostic Dispatcher context;
- TRADING_CONTOUR: exact Strategy owns market-context interpretation per
  ENTRY/POSITION/EXIT phase;
- GLOSSARY: MarketRegime/episode/alert/context/policy/capability terms;
- MAP: dated implementation/runtime findings and pending implementation status.

The revision explicitly preserves:

~~~text
MAYAK = strategy-agnostic observation
Dispatcher = strategy-agnostic applied context
Strategy = sole owner of trading interpretation
Research = evidence only
~~~

It does not approve any stress threshold, LONG/SHORT rule, Entry/Exit filter or
LIVE effect. Those require the existing research -> owner decision -> new
Strategy version -> validation path.

Bootstrap-visible Hard Stop behavior is mirrored in README / AGENTS /
operations/bootstrap/CHATGPT_PROJECT_INSTRUCTIONS_RU.txt; document set,
mandatory pre-read, routing and Project Source count remain unchanged.


# 19. Dashboard UI release separation — 2026-10-05

OWNER DECISION 2026-10-05:

Presentation-only Dashboard UI получает отдельный release/deploy lifecycle и
не должен останавливать trading runtime.

Canonical ownership:
- ARCH: presentation UI boundary;
- OBSERVATION_ANALYTICS: UI/read-model semantics;
- DEVELOPMENT_RELEASE: exact UI-only Git/deploy gate;
- GLOSSARY: `DASHBOARD_UI_COMMIT`;
- MAP: current physical/runtime state.

Правило не разрешает обходить full release для API/auth/control/mutation или
decision/execution-affecting JavaScript. Только presentation-only asset может
обновляться при открытом mainnet gate и Execution ON.

Document set, mandatory pre-read, routing и Project Source count не меняются.
README / AGENTS / Project Instructions template синхронизированы в этом cycle.
