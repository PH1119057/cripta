# CRIPTA — текущая карта проекта

**Версия:** 9.3
**Дата:** 2026-09-28
**Статус:** текущая карта реализации; не заменяет архитектурный контракт

# 1. Source of truth

Авторитетный source of truth:

```text
GitHub PH1119057/cripta:main
```

/srv/cripta/source_checkout — синхронизированное operational mirror GitHub
main, а не второй независимый authority.

Installed runtime, PostgreSQL и Exchange truth проверяются отдельно от source.

## 1.1 Автоматическая синхронизация server source mirror

IMPLEMENTED / DEPLOYED / RUNTIME VERIFIED 2026-09-27:

- `cripta-source-sync.timer` enabled/active;
- периодическая проверка GitHub `main` выполняется примерно каждые 5 минут;
- sync работает от repository owner `cripta`;
- разрешён только clean `main` -> exact remote `main` fast-forward;
- dirty worktree, unexpected branch, divergent/non-fast-forward history,
  fetch/remote mismatch блокируют изменение checkout;
- sync не выполняет deploy, не перезапускает trading services и не меняет
  PostgreSQL/Exchange/mainnet gate;
- clean fast-forward, dirty-worktree block и divergent-history block покрыты
  автоматическими тестами;
- первый production run завершён `SOURCE_SYNC=UP_TO_DATE`, а bootstrap
  installation не перезапустила ни одного уже работающего CRIPTA service.

GitHub `main` остаётся единственным authority. Автоматический mirror sync не
означает deploy: `SOURCE_HEAD`, `INSTALLED_COMMIT` и `LOADED_COMMIT`
по-прежнему проверяются раздельно.

## 1.2 Current operational identity / delta — CHECKED HERE 2026-09-28

Exact identity snapshot immediately before this documentation revision:

```text
REMOTE_HEAD      = 109dd2fd7f33c584d5f2d0ed107d9aede8b1481d
SOURCE_HEAD      = 109dd2fd7f33c584d5f2d0ed107d9aede8b1481d
INSTALLED_COMMIT = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
LOADED_COMMIT    = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
                  [application runtime release]
```

Active operational deltas outside the last full package release:

```text
OPERATIONAL_DELTA_COMMITS =
  6cf5ae08917e3eb99124053a59766f155773a2ef
  109dd2fd7f33c584d5f2d0ed107d9aede8b1481d
```

- `6cf5ae0...` removed `cripta-private-runtime.service` from Dispatcher
  `Wants=`; live affected path:
  `/etc/systemd/system/cripta-dispatcher-v2.service`;
- `109dd2f...` added effective-actor permission preflight and hardened passive
  MAYAK report units; live affected paths:
  `/usr/local/sbin/cripta-permission-preflight`,
  `/etc/systemd/system/cripta-mayak-v2-report.service`,
  `/etc/systemd/system/cripta-mayak-v2-weekly-report.service`;
- live SHA256 этих четырёх files совпадает с current source bytes;
- Universal Entry observer/consumer и trade-lifecycle current symlinks всё ещё
  указывают на release `5a3ea5aba545d5fb97cef108562eac41d35bc47c`.

Следовательно full `INSTALLED_COMMIT` и application `LOADED_COMMIT` остаются
`5a3ea5a...`; точечные unit/helper changes не маскируются под новый full
release. До сведения operational deltas в один exact verified release
`SOURCE_LIVE_IDENTITY` для real-arm не может быть PASS.

Private-runtime finding:
- до `6cf5ae0...` private runtime был disabled, но Dispatcher запускал его
  через `Wants=`; из-за `Restart=always` и schema mismatch накопилось 68973
  restart attempts;
- fail-closed mismatch:
  `expected=runtime-schema-2026-09-20-slot-v1`,
  `actual=runtime-schema-2026-09-02-v1`;
- после fix private runtime остаётся `inactive/disabled`; schema contract
  намеренно не мигрировался.

Current safety evidence:
- Dispatcher, Universal Entry observer, Lifecycle Supervisor,
  Universal Exit shadow и dashboard — active;
- Universal Entry consumer и private runtime — inactive;
- mainnet gate=0;
- real execution permissions=0;
- open/reconciliation positions=0;
- active slot claims=0;
- active capital reservations=0;
- queued/running commands=0;
- pending Exchange orders=0;
- `runtime.position_mode_states=0`;
- `control.live_arm_evidence=0`;
- `control.live_arm_sessions=0`.

## 1.3 Filesystem permission hardening — CHECKED HERE 2026-09-28

FINDING был подтверждён реальными runtime failures, а не только static audit:

- research worker получил `PermissionError` на
  `/data/cripta/research_runs` 2026-09-26;
- MAYAK daily report получил `PermissionError` на
  `/srv/cripta-share/reports` 2026-09-26 и 2026-09-27;
- у report unit уже был `ReadWritePaths=/srv/cripta-share/reports`, но Unix
  owner/group/mode всё равно запрещали write actor'у `cripta`.

DEPLOYED / VERIFIED:
- `/data/cripta/research_runs = cripta:cripta 2770`; existing SentinelX ACL
  `user:sentinelx:rwx` сохранён;
- `/srv/cripta-share/reports = cripta:cripta-share 2750`;
- active research job services имели latent create-path defect:
  `/data/cripta/jobs` и `/data/cripta/jobs/intake` были `root:root 755`,
  хотя service user `cripta` по source contract может создавать в них state
  directories; оба exact parents приведены к `cripta:cripta 750`;
- read-only `cripta-permission-preflight` проверяет exact actor/path до
  mutation;
- create/delete smoke от Unix actor `cripta` PASS на research/report roots
  и на обоих job-state parents;
- daily и weekly MAYAK report service прошли real service-context preflight и
  завершились `Result=success / ExecMainStatus=0`;
- static audit всех current non-disabled CRIPTA services показал: каждый
  существующий `ReadWritePaths` доступен service `User` на write+traverse;
  disabled legacy units с отсутствующими state paths в этот PASS не включались.

Эта правка не меняет trading policy и не выдаёт real-execution rights.

# 2. Верхняя архитектура

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

# 3. Документационный контур

ChatGPT Project Source содержит все 11 current docs, перечисленных в
docs/DOCUMENTATION_INDEX_RU*.md.

Base pre-read состоит из META, WORK, ARCH, INDEX, GLOSSARY, MAP и SECURITY.
TRADING_CONTOUR, OBSERVATION_ANALYTICS, DEVELOPMENT_RELEASE и
RESEARCH_COMPUTE читаются по task route. Наличие routed doc в Project Source
не делает его mandatory every-chat reading.

TRADING_CONTOUR_RU*.md объединяет Strategy + Entry + Exit + Execution.
OBSERVATION_ANALYTICS_RU*.md объединяет MAYAK + Dispatcher + Monitoring +
Lifecycle Supervisor + Position Supervisor + Analyst/Research.

Historical payload/archive docs не являются текущим каноном.

# 4. MAYAK

MAYAK — strategy-agnostic объективное наблюдение внешнего рынка без trading
mutation rights.

# 5. Dispatcher

Текущая архитектура Dispatcher — strategy-agnostic:
- global market context;
- per-coin context;
- trading capacity snapshot;
- объективный rating только после отдельного утверждения формулы.

Dispatcher не создаёт Strategy profile/suitability и не принимает торговое
решение.

CHECKED HERE 2026-09-18:
- `cripta-dispatcher-v2.service` active/enabled;
- legacy `cripta-strategy-dispatcher.service` inactive/disabled;
- legacy source/config всё ещё содержит `M3_V1_*` identifiers и старый
  profile-based contour.

Последний пункт — `FINDING`, а не текущая архитектура. Перед разработкой
старого profile-кода требуется отдельная migration/cleanup задача.

# 6. Strategy / Entry / Exit

CANON после решения владельца 2026-09-18:

- StrategyCard остаётся passive immutable policy;
- Strategy layer включает Strategy Materializer;
- Materializer создаёт exact immutable EntryPlan + ExitPlan;
- Entry Engine универсально исполняет EntryPlan;
- после confirmed fill создаётся StrategyPosition;
- Exit Engine универсально исполняет exact ExitPlan этой StrategyPosition;
- Entry/Exit Engines не зависят от количества Strategy и не содержат скрытой
  Strategy-specific policy;
- Execution исполняет typed Entry/Exit requests.

Текущий source реализует основной универсальный contour этой модели:
- StrategyActivation;
- EntryPlan/ExitPlan materialization;
- ActivePlanRegistry;
- UniversalEntryEngine;
- ParameterizedCausalMarketWatch;
- StrategySignal -> strategy_attempt -> EntryDecision;
- atomic slot + capital admission;
- typed EntryExecutionRequest / request-state lifecycle;
- StrategyPosition exact lineage;
- Universal Exit Engine и typed Exit execution bridge;
- Lifecycle Supervisor;
- Analyst/counterfactual;
- PostgreSQL evidence/read-model;
- shadow recovery/restart contracts.

Real cutover при этом не выполнен: Universal Entry consumer и private runtime
остаются inactive, mainnet gate закрыт. Текущие enabled Strategy являются
monitoring Strategy, а их ExitPlan пока не содержит executable rules. Поэтому
наличие реализованного contour не даёт real execution rights.

# 7. Текущий первый Strategy Candidate

Текущая геометрическая идея остаётся `Strategy Candidate / Draft`, пока
владелец не утвердил immutable StrategyCard/version.

```text
H9 = 9 часов = 540 минут
5m component  = 108 закрытых 5m свечей
15m component = 36 закрытых 15m свечей
```

Это не универсальная константа Entry Engine.

## 7.1 Strategy settings authoring

Owner decision 2026-09-19:

- Strategy-specific settings остаются внутри явных StrategyCard policy-блоков;
- базовая защитная рамка отделяется от динамического Exit;
- новый authoring template содержит explicit disabled slots для hard stop, TP,
  break-even, trailing, geometry Exit, local-zone Exit и time Exit;
- numeric trading defaults в authoring template отсутствуют;
- `geometry_exit` зарезервирован, но его включение fail-closed до появления
  точного executable consumer contract;
- нестабильные H3/touch/trailing параметры остаются Strategy Candidate/Draft
  либо отдельной experimental Strategy version, а не global defaults;
- текущие активные Strategy records в PostgreSQL этой ревизией не меняются.

Классический исследовательский пример хранится только как non-canonical
implementation example и не получает StrategyActivation/execution rights.

# 8. H3

```text
H3 = 3 часа = 180 минут
```

H3 сейчас не является Entry condition текущего Strategy Candidate и относится к
сопровождению/Exit research.

Owner-confirmed baseline 2026-09-25: H9=108x5m+36x15m на 540m; H3=36x5m+12x15m на 180m; одна formula extrema/zones; Wilder ATR200 по каждому timeframe; zone half-width=ATR*0.5; gap=0; legacy shock/reset вне baseline. H3>=1% сейчас RESEARCH condition, не утверждённый Entry rule.

## 8.1 Geometry timeline

OWNER DECISION 2026-09-25: одинаковую Strategy-defined геометрию причинно рассчитывать один раз и сохранять versioned timeline для Entry/Exit/Position Supervisor/Analyst. Strategy сохраняет ownership параметров; observation получает exact GeometrySpec/fingerprint. `CANON=YES; IMPLEMENTED=NOT CHECKED/NOT CLAIMED; DEPLOYED=NO CLAIM`.

# 9. Стабилизация

Стабилизация задаётся Candidate/Strategy в минутах и не является global default.

# 10. Post-fill geometry

Entry price фиксируется как факт сделки.
Текущая geometry после Entry продолжает причинно пересчитываться.

# 11. Execution / Exchange

Execution исполняет уже принятое торговое решение. Bybit — текущий provider, но
не архитектурная константа.

Текущий approved real account contract остаётся:

```text
Bybit Unified linear
position_mode = ONE_WAY
positionIdx = 0
```

Это не считается вечным account state. Перед real arm и далее по freshness
contract требуется новое подтверждённое `position_mode_state`.

IMPLEMENTED / DEPLOYED 2026-09-21:
- durable `runtime.position_mode_states`;
- exclusive `runtime.exchange_position_slot_claims`;
- slot claim до `EntryDecision=ACCEPTED`;
- slot claim + capital reservation как один admission contract;
- `EXCHANGE_POSITION_OWNERSHIP_CONFLICT` как штатный EntryDecision outcome;
- `EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN` как отдельный lifecycle fault;
- `EXCHANGE_POSITION_MODE_MISMATCH` как fail-closed lifecycle/operational fault;
- separate request-state lifecycle;
- StrategyPosition binding/release exact slot + reservation lineage.

Controlled PostgreSQL scenarios проверили:
- два concurrent attempt не могут владеть одним physical slot;
- capital failure не оставляет durable slot claim;
- stale/incompatible position mode не создаёт claim/reservation;
- unknown/post-ack state не приводит к blind release;
- confirmed fill связывает exact StrategyPosition;
- final confirmed close освобождает reservation/slot;
- reconciliation state удерживает ownership до доказанного разрешения.

Production runtime сейчас не содержит fresh `position_mode_state`, потому что
private runtime выключен. Поэтому этот реализованный contract не является
доказательством текущей real account freshness.

# 12. Lifecycle / Position / Analytics

Каноническая lifecycle-chain определяется только ARCH §9.1. TC/OBS её не
дублируют.

Runtime evidence разделяется на:
- RUNTIME LIVENESS VERIFIED;
- RUNTIME BEHAVIOR VERIFIED.

Разделы §12.1, §12.2, §14, §15 и §20 ниже сохраняются как точное historical evidence
checkpoint 2026-09-21 и не должны читаться как current service-state snapshot.
Текущий проверенный operational delta находится в §1.2.

## 12.1 Status matrix — checkpoint 2026-09-21

| Компонент / contract | CANON | IMPLEMENTED | DEPLOYED | LIVENESS | BEHAVIOR | Evidence / режим |
| --- | --- | --- | --- | --- | --- | --- |
| StrategyCard authoring/materializer | YES | YES | YES | N/A | N/A | source/tests; runtime behavior dimension не применяется |
| Universal Entry observer / plan ACK | YES | YES | YES | YES | PARTIAL | service running, exact release loaded, facts advance; текущий observer после restart в WARMUP, post-restart evaluation ещё не наблюдался |
| Capital reservation admission | YES | YES | YES | N/A | YES (CONTROLLED) | concurrent/rollback/reconciliation PostgreSQL scenarios PASS; real consumer inactive |
| Durable physical slot claim + fresh mode contract | YES | YES | YES | N/A | YES (CONTROLLED) | atomic claim/reservation, conflict, stale/mismatch scenarios PASS; production fresh mode state сейчас отсутствует |
| Universal Exit Engine decision-only | YES | YES | YES | YES | YES (CONTROLLED) | service running; controlled ownership/restart/decision scenarios PASS; production open position sample=0 |
| Typed Exit execution bridge/consumer | YES | YES | YES | NO | YES (CONTROLLED) | execution path tested; real execution arm disabled |
| Lifecycle Supervisor full current contract | YES | YES | YES | YES | YES (CONTROLLED) | heartbeat advances; ownership/reconciliation/protection fault scenarios PASS |
| Critical fault durable delivery contract | YES | YES | YES | N/A | YES (CONTROLLED) | retry/ack/escalation and protection-fault delivery PASS; production owner channel currently not configured |
| Analyst counterfactual path | YES | YES | YES | N/A | YES (CONTROLLED) | capital + slot-conflict isolation/tests PASS |
| LIVE-arm evidence/session gate | YES | YES | YES | N/A | YES (CONTROLLED) | missing/stale/wrong-release fail-closed; active exact-release session required |
| Current private runtime source | YES | YES | YES | NO | NO | source/live exact; service inactive/disabled |

`YES (CONTROLLED)` означает специально проведённый воспроизводимый scenario на
disposable PostgreSQL/source exact текущего release. Это не означает, что такой
сценарий уже возникал на real Exchange.

## 12.2 Exact release identity — checkpoint 2026-09-21

Полностью проверенный historical implementation/runtime checkpoint
2026-09-21:

```text
REMOTE_HEAD      = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
SOURCE_HEAD      = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
INSTALLED_COMMIT = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
LOADED_COMMIT    = 5a3ea5aba545d5fb97cef108562eac41d35bc47c
```

Ключевые live/source hashes совпали. После документационной ревизии Git SHA
может измениться только из-за документации; implementation evidence относится
к тем же исполняемым bytes до следующего implementation changeset.

# 13. ChatGPT Project Instructions

Каноническая схема Project Instructions — тонкий bootstrap по
`CHATGPT_INTERACTION_RULES_RU*.md`, с семействами имён через `*`.

Фактический текст Project Instructions в UI является отдельным ChatGPT-project
state и не подтверждается одним только GitHub.

# 14. Граница documentation/runtime revision — checkpoint 2026-09-21

Эта ревизия checkpoint 2026-09-21:
- синхронизирует карту с фактически проверенным code/DB/runtime checkpoint;
- не меняет production trading logic;
- не меняет Strategy records;
- не активирует real Execution;
- не включает mainnet;
- не создаёт fresh position-mode state;
- не создаёт LIVE-arm evidence/session;
- не назначает новые Strategy trading parameters;
- не переводит SHADOW в MICRO_LIVE/LIVE.

Research/исторические H3/H9/TP/SL/trailing результаты не становятся каноном из-за
этого обновления.

# 15. Проверенный runtime checkpoint 2026-09-21

Проверенные service states:

```text
cripta-universal-entry-observer.service = active/running
cripta-lifecycle-supervisor.service      = active/running
cripta-universal-exit-shadow.service     = active/running

cripta-universal-entry-consumer.service = inactive
cripta-private-runtime.service           = inactive
cripta-dashboard.service                 = inactive
```

Safety snapshot:

```text
mainnet execution gate = 0
shadow gate = 1
real Strategy execution permissions = 0
open/reconciliation StrategyPosition = 0
active physical slot claims = 0
active capital reservations = 0
queued/running trade_commands = 0
pending Exchange orders = 0
open lifecycle faults = 0
```

Двухсрезная runtime-проверка показала:
- heartbeat Entry observer / Lifecycle Supervisor / Universal Exit движется;
- PID трёх активных сервисов стабилен;
- Entry observer продолжает принимать market facts;
- за 4 секунды `facts_received` вырос с 105284 до 105649;
- `trading_effect=NONE`;
- faults/claims отсутствуют.

Entry observer после restart находится в штатном `WARMUP`:
- `observer_ready=true`;
- `state=WARMUP`;
- `evaluations=0`;
- `signals=0`;
- причина — ожидание plan-owned pre-start influence/sensor completeness.

Следовательно RUNTIME LIVENESS VERIFIED=YES, но фактический post-restart Entry
evaluation ещё не наблюдался.

# 16. Capital allocation / physical-slot admission

CANON и implementation теперь совпадают:

```text
Strategy attempt
-> required account / position-mode validation
-> physical slot claim
-> atomic capital reservation
-> EntryDecision
-> EntryExecutionRequest only for ACCEPTED
```

Фактически реализованы durable:
- `exchange_position_slot_claim_id`;
- `position_mode_state_ref`;
- `capital_reservation_id`;
- request-state events;
- StrategyPosition binding;
- reconciliation-aware release.

`EXCHANGE_POSITION_OWNERSHIP_CONFLICT` не является lifecycle fault.
Post-admission divergence классифицируется отдельно как
`EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN`.

# 17. Real protection / Lifecycle Supervisor / owner notification

IMPLEMENTED / DEPLOYED:
- StrategyPosition хранит exact ExitPlan/protection/emergency lineage;
- Supervisor выявляет `POSITION_WITHOUT_EXIT_OWNER`;
- Supervisor выявляет `CAPITAL_RESERVATION_STUCK`;
- Supervisor выявляет
  `POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION`;
- Supervisor выявляет `EXCHANGE_POSITION_MODE_MISMATCH`;
- Supervisor выявляет
  `EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN`;
- critical fault создаёт durable delivery;
- delivery поддерживает retry, separate owner acknowledgement и escalation;
- fault/recovery lifecycle restart-idempotent.

Отдельный controlled scenario 2026-09-21 подтвердил:

```text
POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION
-> severity=CRITICAL
-> state=OPEN
-> durable delivery state=PENDING
-> channel=OWNER_WEBHOOK
```

При этом production owner delivery channel сейчас не настроен
(`critical_delivery.configured=false`). Поэтому
`CRITICAL_FAULT_DELIVERY=PASS` для real arm пока ставить нельзя, хотя сам
delivery contract реализован и controlled behavior verified.

# 18. LIVE / MICRO_LIVE readiness — CHECKED HERE 2026-09-28

Канонический checklist находится в TRADING_CONTOUR §4.7.

Current readiness checkpoint:

```text
CANON_CURRENT                         = PASS
REMOTE_COMMIT_VERIFIED                = PASS
SOURCE_LIVE_IDENTITY                  = NOT READY FOR ARM (operational deltas outside full release)
TESTS                                 = PASS
LIVE_EQUIVALENCE                      = NOT YET DECLARED PASS

EXCHANGE_ACCOUNT_IDENTITY             = NOT CURRENTLY PROVED FOR ARM
POSITION_MODE_FRESH                   = NOT READY
POSITION_IDX_EXPECTED                 = NOT READY
PHYSICAL_SLOT_CLAIM_CONTRACT          = PASS (CONTROLLED)
CAPITAL_RESERVATION_CONTRACT          = PASS (CONTROLLED)

EXACT_STRATEGY_ACTIVATION             = NOT READY FOR ARM
ENTRY_PLAN_EXECUTABLE                 = NOT READY FOR ARM
EXIT_PLAN_EXECUTABLE                  = NOT READY
INITIAL_PROTECTION_EXECUTABLE         = NOT READY
TERMINAL_LOSS_CONTAINMENT_PATH        = NOT CHECKED HERE
EMERGENCY_POLICY_SUPPORTED            = NOT CHECKED HERE

LIFECYCLE_SUPERVISOR_BEHAVIOR         = PASS (CONTROLLED)
CRITICAL_FAULT_DELIVERY               = NOT READY
RECONCILIATION_PATH                   = PASS (CONTROLLED)

MAINNET_GATE_EXPLICIT_OWNER_APPROVAL  = NOT GIVEN
MICRO_LIVE_LIMITS                     = NOT APPROVED
ROLLBACK_OR_KILL_PATH                 = NOT CHECKED HERE
```

Current repository gate for this revision:

```text
full pytest = 1448 passed / 64 skipped / 0 failed
```

Current Strategy activation DB check:

```text
enabled strategy_activations = 0
entry_v1_monitor_long  1.0 = disabled since 2026-09-21
entry_v1_monitor_short 1.0 = disabled since 2026-09-21
```

Experimental StrategyCard records существуют для research, но наличие
StrategyCard не является StrategyActivation и не даёт Entry/Execution rights.
Entry observer сейчас `state=IDLE`,
`reason=no enabled StrategyActivation`, `active_strategies=0`.
Поэтому `EXACT_STRATEGY_ACTIVATION` и `ENTRY_PLAN_EXECUTABLE` остаются
`NOT READY FOR ARM`.

В `runtime.position_mode_states` сейчас 0 rows.
В `control.live_arm_evidence` сейчас 0 rows.
В `control.live_arm_sessions` сейчас 0 rows.

Следовательно:

```text
READY_FOR_LIVE = NO
READY_FOR_MICRO_LIVE = NO
MAINNET = DISARMED
```

Это fail-closed состояние является ожидаемым и не считается дефектом.

# 19. Repository / security checkpoint

CHECKED HERE 2026-09-21:
- GitHub repository `PH1119057/cripta` имеет visibility=public;
- full Git-history scan выполнен `gitleaks 8.30.1`;
- найдено 2 `generic-api-key` findings;
- оба вручную классифицированы как старые synthetic test fixtures:
  - `tests/test_dashboard_password_hash.py`;
  - `tests/test_mayak_component_resource_smoke.py`;
- findings принадлежат историческим commits `770a380...` и `7b366ea...`;
- actionable secret findings текущей implementation revision = 0.

Большое количество historical Pxx/EO/SE/ENTRY_BOT/PATCH artifacts в корне
repository остаётся отдельным cleanup finding. Их перенос в `archive/**`
должен быть отдельным exact repository-cleanup changeset после dependency
classification; текущая документационная синхронизация их не перемещает.

# 20. Verification results — checkpoint 2026-09-21

CHECKED HERE 2026-09-21:

```text
full pytest:
1430 passed
62 skipped
0 failed

controlled PostgreSQL lifecycle/admission/fault suite:
54 passed
0 failed

Ruff targeted current-release changes:
PASS
```

Дополнительно доказаны:
- exact remote/source/installed/loaded release identity;
- source/live equality ключевых исполняемых модулей;
- runtime-role ACL новых lifecycle tables;
- Git-first release identity/installer rail;
- exclusive installer lock;
- runtime heartbeat progression;
- no real Exchange mutation during verification;
- mainnet remained disarmed.

Старый documentation-first test debt из checkpoint 2026-09-19 закрыт и больше
не является текущим finding.

# 21. Current unresolved operational items

До real arm остаются именно operational/readiness задачи, а не недоказанная
реализация slot/lifecycle foundation:

1. current Entry observer находится `IDLE` с
   `reason=no enabled StrategyActivation`; до real arm требуется exact
   owner-approved StrategyActivation и post-activation runtime behavior evidence;
2. activated Strategy должна иметь executable EntryPlan + ExitPlan + required
   protection/lifecycle policy;
3. private account state поднимать только отдельным разрешённым readiness step:
   сначала совместить runtime schema contract, затем получить fresh
   `position_mode_state` / `positionIdx=0`; dependency crash-loop уже устранён;
4. настроить реальный durable owner-notification channel для critical faults;
5. сформировать canonical LIVE-arm evidence для exact Strategy/symbol/release;
6. owner-approved MICRO_LIVE limits;
7. отдельное explicit owner approval на real arm;
8. только после этого MICRO_LIVE; full LIVE не следует из MICRO_LIVE автоматически.
