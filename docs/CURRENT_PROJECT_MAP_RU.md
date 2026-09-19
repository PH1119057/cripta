# CRIPTA — текущая карта проекта

**Версия:** 8.6
**Дата:** 2026-09-19
**Статус:** текущая карта реализации; не заменяет архитектурный контракт

# 1. Source of truth

```text
GitHub PH1119057/cripta:main
==
синхронизированный /srv/cripta/source_checkout
```

Installed runtime и PostgreSQL проверяются отдельно от source.

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

Активный Project Source состоит из восьми семейств из
`docs/DOCUMENTATION_INDEX_RU*.md`.

`CHATGPT_INTERACTION_RULES_RU*.md` — META-контракт: читается первым, но не
задаёт торговую архитектуру.

`TRADING_CONTOUR_RU*.md` объединяет Strategy + Entry + Exit + Execution.

`OBSERVATION_ANALYTICS_RU*.md` объединяет MAYAK + Dispatcher + Monitoring +
Lifecycle Supervisor + Position Supervisor + Analyst/Research.

Старые самостоятельные корневые концептуальные/PASS/Workbench документы
перенесены в `archive/documentation_pre_2026-09-18/root/`.

Исторические документы внутри старых patch/research payload остаются на месте
для воспроизводимости, но исключаются из обычного pre-read/поиска канона.

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
Lifecycle Supervisor, Analyst/counterfactual и shadow recovery. P9 прошёл
runtime verification в SHADOW.

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

Execution исполняет уже принятое торговое решение.
Bybit — текущий provider, но не архитектурная константа.

Typed `EntryExecutionRequest` / `ExitExecutionRequest`, exact lineage,
physical-slot ownership checks и Universal Exit execution bridge реализованы в
current source. Real Universal consumers/gates на текущем checkpoint disarmed.

Read-only проверка Bybit 2026-09-19 по всем 10 symbols активного Strategy
universe показала только `positionIdx=0`: фактический режим текущего Unified
linear account — one-way. Universal Entry consumer source блокирует второй real
Entry в занятый/pending physical slot через
`EXCHANGE_POSITION_OWNERSHIP_CONFLICT`.

Это `IMPLEMENTED` и покрыто tests/disposable PostgreSQL. Simultaneous
same-symbol multi-Strategy execution не объявляется `RUNTIME VERIFIED LIVE`,
поскольку real Universal consumer disabled и такая биржевая мутация не
выполнялась.

# 12. Lifecycle / Position / Analytics

Канонически:
- Lifecycle Supervisor контролирует handoff от Strategy activation/materialized
  plans до final close/economics;
- Position Supervisor наблюдает фактическое состояние StrategyPosition;
- Analyst/Research занимается постфактум-аналитикой и counterfactual;
- Monitoring/UI показывает состояния, но не владеет trading policy.

Status matrix на checkpoint 2026-09-19:

| Компонент / contract | CANON | IMPLEMENTED | DEPLOYED | RUNTIME VERIFIED | Evidence / режим |
| --- | --- | --- | --- | --- | --- |
| StrategyCard settings authoring/materializer | YES | YES | YES | NO | tests + active legacy-card immutability; new slots без live execution |
| Universal Entry observer / plan ACK | YES | YES | YES | YES | SHADOW service active/enabled; ACK пишет runtime |
| Atomic capital reservation / pre-dispatch TTL | YES | YES | YES | NO | PostgreSQL/tests; real Universal consumer disabled |
| StrategyPosition exact binding / physical slot conflict | YES | YES | YES | NO | PostgreSQL/tests; one-way checked, open Universal positions=0 |
| Universal Exit Engine decision-only | YES | YES | YES | YES | SHADOW service/restart verified; open position sample=0 |
| Typed Exit execution bridge/consumer | YES | YES | YES | NO | source/live staged; consumer arm disabled |
| Lifecycle Supervisor | YES | YES | YES | YES | non-trading service active/enabled, faults=0 |
| Analyst counterfactual path | YES | YES | YES | NO | source/DB/tests; no Exchange rights |
| Legacy Exit ownership exclusion | YES | YES | YES | NO | source/live exact; legacy service inactive |
| Current private runtime source | YES | YES | YES | NO | source/live exact + import/unit verified; service inactive/disabled |

`RUNTIME VERIFIED=NO` не означает «не протестировано»: tests/disposable DB/source-live
checks приводятся в Evidence, но не подменяют проверку реально загруженного
runtime path.

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
- вводит новые canonical требования one-way ownership, обязательной real
  protection и emergency policy, но не выдаёт их будущую runtime enforcement за
  уже RUNTIME VERIFIED там, где это отдельно не доказано.

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

Reservation является частью EntryDecision: `ACCEPTED` появляется только после
успешной atomic reservation. Availability опирается на verified account capacity
+ durable commitments/reservations; stale/unknown required state блокирует Entry.

```text
Strategy задаёт требуемую сумму
-> Entry condition fulfilled
-> atomic capital reservation
-> первый успешный reservation получает доступный капитал
```

Entry не ранжирует Strategy.

Если средств недостаточно:
- real Entry получает INSUFFICIENT_AVAILABLE_FUNDS;
- Execution не создаётся;
- Analyst может вести counterfactual/псевдосделку.

Это owner-approved архитектурное правило. Source/tests/DB contract уже
реализованы; real Universal execution path остаётся disarmed и не объявляется
RUNTIME VERIFIED LIVE.

# 17. One-way physical ownership / real protection readiness

CANON:
- логические Strategy могут одновременно давать независимые/opposite signals;
- текущий Bybit one-way physical slot имеет одного active owner lifecycle;
- второй Strategy Entry в тот же slot блокируется до Exchange mutation;
- real Strategy обязана иметь owner-approved initial loss-containment;
- открытая StrategyPosition сохраняет exact ExitPlan/protection/emergency policy
  своей opening Strategy version после деактивации Strategy;
- automatic emergency action разрешён только exact emergency_policy/owner
  command, а не самим фактом наличия `EMERGENCY_CLOSE` capability.

IMPLEMENTATION STATUS:
- physical-slot block реализован и PostgreSQL-tested;
- обязательность real protection/emergency policy в этой ревизии является
  CANON; полная activation/runtime enforcement должна проверяться отдельной
  implementation-задачей до re-arm;
- никакой stop/TP/H3/trailing value этой ревизией не утверждается.
