# CRIPTA — текущая карта проекта

**Версия:** 8.7
**Дата:** 2026-09-19
**Статус:** текущая карта реализации; не заменяет архитектурный контракт

# 1. Source of truth

Авторитетный source of truth:

```text
GitHub PH1119057/cripta:main
```

/srv/cripta/source_checkout — синхронизированное operational mirror GitHub
main, а не второй независимый authority.

Installed runtime, PostgreSQL и Exchange truth проверяются отдельно от source.

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

ChatGPT Project Source по-прежнему состоит из восьми семейств, перечисленных в
docs/DOCUMENTATION_INDEX_RU*.md.

Для уменьшения обязательного pre-read тяжёлые process rules вынесены в
GitHub-only routed canon:
- docs/DEVELOPMENT_RELEASE_RULES_RU*.md — patch/Git/PostgreSQL/release/deploy;
- docs/RESEARCH_COMPUTE_RULES_RU*.md — research/large jobs/compute/data.

Эти документы читаются только для соответствующей работы и не увеличивают
базовый Project Source bundle.

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

Текущий source частично реализует старую сторону этой модели:
- StrategyActivation;
- EntryPlan/ExitPlan materialization;
- ActivePlanRegistry;
- UniversalEntryEngine;
- ParameterizedCausalMarketWatch;
- StrategySignal/Attempt/Decision;
- PostgreSQL evidence/read-model;
- execution bridge.

Текущий implementation contour P3-P9 уже реализует atomic reservation,
StrategyPosition lineage, Universal Exit Engine, typed Exit execution bridge,
Lifecycle Supervisor, Analyst/counterfactual и shadow recovery. Исторический
P9 checkpoint имел SHADOW runtime evidence; эта формулировка не заменяет
нынешнее раздельное доказательство LIVENESS и BEHAVIOR.

P10 controlled legacy Exit migration остаётся без LIVE-cutover: Universal Entry
consumer disabled, mainnet gate закрыт. Cutover не разрешён без отдельного
owner decision и exact executable ExitPlan evidence.

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

# 9. Стабилизация

Стабилизация задаётся Candidate/Strategy в минутах и не является global default.

# 10. Post-fill geometry

Entry price фиксируется как факт сделки.
Текущая geometry после Entry продолжает причинно пересчитываться.

# 11. Execution / Exchange

Execution исполняет уже принятое торговое решение. Bybit — текущий provider, но
не архитектурная константа.

Историческая read-only проверка 2026-09-19 по 10 symbols показала
positionIdx=0 и one-way для тогдашнего account state. Эта проверка не считается
вечной: новый канон требует fresh Position mode state при real activation/re-arm
и Entry admission.

CANON 2026-09-19:
- one-way same-symbol physical slot имеет одного owner lifecycle;
- до ACCEPTED требуется durable physical slot claim;
- slot claim и capital reservation составляют один all-or-nothing admission;
- expected current contract: ONE_WAY + positionIdx=0;
- mode unknown/stale -> fail-closed;
- fresh incompatible mode/positionIdx -> EXCHANGE_POSITION_MODE_MISMATCH /
  OPERATIONAL_SAFETY_BLOCKED;
- same-symbol hedge в one-way unsupported.

IMPLEMENTATION / DEPLOY STATUS НОВЫХ ТРЕБОВАНИЙ:
NOT CHECKED HERE в этой документационной ревизии. Предыдущая реализация coarse
physical-slot block не считается доказательством нового durable slot-claim /
fresh position-mode contract.

# 12. Lifecycle / Position / Analytics

Каноническая lifecycle-chain определяется только ARCH §9.1. TC/OBS её больше
не дублируют.

Bare RUNTIME VERIFIED=YES больше не используется. Runtime evidence разделяется
на:
- RUNTIME LIVENESS VERIFIED;
- RUNTIME BEHAVIOR VERIFIED.

Status matrix на checkpoint документационной ревизии 2026-09-19:

| Компонент / contract | CANON | IMPLEMENTED | DEPLOYED | LIVENESS | BEHAVIOR | Evidence / режим |
| --- | --- | --- | --- | --- | --- | --- |
| StrategyCard authoring/materializer | YES | YES | YES | N/A | N/A | tests + authoring evidence; runtime behavior dimension not applicable |
| Universal Entry observer / plan ACK | YES | YES | YES | YES | YES | SHADOW service alive; ACK path observed |
| Capital reservation existing contract | YES | YES | YES | N/A | NO | tests/PostgreSQL evidence only; real consumer disabled |
| Durable physical slot claim + fresh mode state | YES | NOT CHECKED HERE | NOT CHECKED HERE | NO | NO | new canon; implementation audit deferred |
| Universal Exit Engine decision-only | YES | YES | YES | YES | NO | service alive; open position sample=0, executable ExitPlans=0 |
| Typed Exit execution bridge/consumer | YES | YES | YES | NO | NO | staged; consumer arm disabled |
| Lifecycle Supervisor current full contract | YES | PARTIAL | PARTIAL | YES | NO | service alive; new slot/mode/fault-delivery behavior NOT CHECKED HERE |
| Critical fault delivery to owner | YES | NOT CHECKED HERE | NOT CHECKED HERE | NO | NO | new safety contract |
| Analyst counterfactual path | YES | YES | YES | N/A | NO | capital case source/tests; slot-conflict runtime behavior deferred |
| Current private runtime source | YES | YES | YES | NO | NO | service inactive/disabled |

LIVENESS=YES не означает behavior correctness. faults=0 без специально
проведённого fault scenario также не является behavior verification.

# 13. ChatGPT Project Instructions

Каноническая схема Project Instructions — тонкий bootstrap по
`CHATGPT_INTERACTION_RULES_RU*.md`, с семействами имён через `*`.

Фактический текст Project Instructions в UI является отдельным ChatGPT-project
state и не подтверждается одним только GitHub.

# 14. Граница текущей документационной ревизии

Эта ревизия:
- не меняет production trading logic;
- не меняет Strategy records в PostgreSQL;
- не активирует real Execution;
- не переименовывает historical IDs/DB rows;
- синхронизирует CANON с уже проверенными implementation/runtime фактами из §12/§15;
- вводит новые canonical требования durable slot claim, fresh position-mode state,
  обязательной real protection, emergency policy и critical fault delivery;
- не выдаёт liveness сервиса за behavior verification и не объявляет новые
  requirements IMPLEMENTED/DEPLOYED без отдельной проверки кода/runtime.

# 15. Проверенный runtime/source checkpoint 2026-09-19

На последнем P10/P10.1 runtime-check:

```text
cripta-universal-entry-observer.service  active/enabled
cripta-universal-exit-shadow.service      active/enabled
cripta-lifecycle-supervisor.service       active/enabled
cripta-universal-entry-consumer.service  inactive/disabled
cripta-private-runtime.service           inactive/disabled
cripta-exit-runtime.service              inactive/enabled
```

```text
mainnet execution gate = 0
shadow gate = 1
open Universal StrategyPosition = 0
open lifecycle faults = 0
ExitExecutionRequest = 0
queued/running trade_commands = 0
ENTRY_ENGINE loaded acknowledgements = 4
active ExitPlans = 2
active ExitPlans with executable rules = 0
```

Private runtime source/live divergence устранён staging-deploy текущего source,
но сервис не запускался. Legacy Exit ownership filter deployed, legacy Exit
service также не запускался.

Legacy identifiers с `M3` — технический долг и не создают термин `M3`.


# 16. Capital allocation V1

CANON:

Capital reservation является частью единого real Entry admission вместе с
physical slot claim:

```text
Strategy attempt
-> required account / position-mode state
-> physical slot claim
-> atomic capital reservation
-> EntryDecision
```

Первый успешно завершивший весь admission получает право на ACCEPTED. Entry не
ранжирует Strategy.

Если capital недостаточно:
- EntryDecision=INSUFFICIENT_AVAILABLE_FUNDS;
- durable slot claim не остаётся;
- ExecutionRequest не создаётся;
- Analyst может вести counterfactual с exact block reason.

Если physical slot недоступен:
- EntryDecision=EXCHANGE_POSITION_OWNERSHIP_CONFLICT;
- capital reservation не создаётся;
- Analyst может вести отдельный slot-conflict counterfactual.

Новый atomic slot+capital contract — CANON. Его current implementation в этой
документационной ревизии NOT CHECKED HERE.

# 17. One-way physical ownership / real protection readiness

CANON:
- независимые Strategy могут одновременно давать opposite signals;
- current approved real contract — ONE_WAY + positionIdx=0;
- position mode является fresh required account state, а не вечным свойством;
- до ACCEPTED нужен exclusive durable physical slot claim;
- claim после fill связывается с StrategyPosition и живёт до final flat;
- same-symbol hedge в one-way unsupported и fail-closed;
- real Strategy обязана иметь owner-approved initial loss-containment;
- открытая StrategyPosition сохраняет exact ExitPlan/protection/emergency policy;
- automatic emergency action разрешён только exact emergency_policy/owner command;
- critical lifecycle fault должен иметь durable owner-notification delivery.

IMPLEMENTATION STATUS:
- предыдущий coarse slot block существует по старым evidence;
- новый durable slot claim, fresh position-mode enforcement,
  EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN,
  EXCHANGE_POSITION_MODE_MISMATCH и critical fault delivery — NOT CHECKED HERE;
- их нельзя считать готовыми к re-arm до отдельного source/DB/runtime audit.

# 18. LIVE-arm readiness

Канонический checklist находится в TRADING_CONTOUR §4.7.

Текущий checkpoint НЕ READY FOR LIVE, пока минимум новые slot/mode/fault-delivery
requirements не будут IMPLEMENTED и runtime-behavior verified.

Этот раздел не изменяет mainnet gate и не активирует real Execution.

# 19. Repository / security findings

CHECKED HERE 2026-09-19:
- GitHub repository PH1119057/cripta имеет visibility=public;
- в корне source_checkout сохраняется большое число historical Pxx/EO/SE/
  ENTRY_BOT/PATCH artifacts вне archive;
- gitleaks и trufflehog на server не установлены.

NOT CHECKED HERE:
- full-history secret scan;
- отсутствие исторически закоммиченных credential paths/names/secrets;
- необходимость сохранять repository public.

До security checkpoint требуется full-history secret scan approved tool'ом и
отдельное owner decision о public/private visibility. Отсутствие scan tool не
считается PASS.

Root historical artifacts должны быть перемещены в archive отдельным exact
repository-cleanup changeset. Эта документационная ревизия их не перемещает.

# 20. Known test debt after documentation-first revision

CHECKED HERE на isolated documentation worktree:

```text
full pytest:
1415 passed
47 skipped
6 failed
```

Шесть failures относятся к stale documentation-contract expectations:
- old WORK wording/location after routed-doc split;
- old literal Entry Engine wording;
- old INDEX route phrase;
- old reservation-only ACCEPTED assertion;
- old requirement to duplicate lifecycle chain in ARCH/TC/OBS;
- old MAP status-matrix wording.

Это FINDING следующего implementation/test changeset. Тесты в этой
documentation-only ревизии намеренно не меняются по owner scope.

Этот результат НЕ является проверкой реализации новых slot claim /
position-mode / critical-fault-delivery требований.
