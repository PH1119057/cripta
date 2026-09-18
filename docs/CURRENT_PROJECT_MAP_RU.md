# CRIPTA — текущая карта проекта

**Версия:** 8.4  
**Дата:** 2026-09-18  
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

Полная runtime-реализация нового канона (универсальный Exit Engine,
StrategyPosition/lifecycle handoff, typed Exit execution path) ещё не считается
IMPLEMENTED до отдельного ТЗ, разработки и проверки.

# 7. Текущий первый Strategy Candidate

Текущая геометрическая идея остаётся `Strategy Candidate / Draft`, пока
владелец не утвердил immutable StrategyCard/version.

```text
H9 = 9 часов = 540 минут
5m component  = 108 закрытых 5m свечей
15m component = 36 закрытых 15m свечей
```

Это не универсальная константа Entry Engine.

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

Канонически Execution должен принимать решения как Entry, так и Exit через
различимые EntryExecutionRequest/ExitExecutionRequest с exact lineage.

Физическая реализация этого нового interface contract ещё не проверена и не
считается IMPLEMENTED.

# 12. Lifecycle / Position / Analytics

Канонически:
- Lifecycle Supervisor контролирует handoff от Strategy activation/materialized
  plans до final close/economics;
- Position Supervisor наблюдает фактическое состояние StrategyPosition;
- Analyst/Research занимается постфактум-аналитикой и counterfactual
  псевдосделками;
- Monitoring/UI показывает состояния, но не владеет trading policy.

Новый Lifecycle Supervisor contract пока является CANON, но его соответствие
текущему production source/runtime должно быть проверено в отдельной
implementation-задаче.

# 13. ChatGPT Project Instructions

Каноническая схема Project Instructions — тонкий bootstrap по
`CHATGPT_INTERACTION_RULES_RU*.md`, с семействами имён через `*`.

Фактический текст Project Instructions в UI является отдельным ChatGPT-project
state и не подтверждается одним только GitHub.

# 14. Граница этой ревизии

Документационная ревизия:
- не меняет production trading logic;
- не меняет Strategy records в PostgreSQL;
- не активирует real Execution;
- не переименовывает historical IDs/DB rows;
- фиксирует новое owner-approved устройство Strategy Materializer / Entry
  Engine / Exit Engine / Lifecycle Supervisor как CANON;
- не объявляет это IMPLEMENTED/DEPLOYED до отдельного ТЗ, разработки и
  runtime verification.

# 15. Проверенный runtime/source checkpoint 2026-09-18

```text
cripta-mayak-v2.service                         active/enabled
cripta-dispatcher-v2.service                    active/enabled
cripta-dispatcher-v2-context-correlator.service active/enabled
cripta-universal-entry-observer.service         active/enabled
cripta-universal-entry-consumer.service         inactive/disabled
cripta-private-runtime.service                  active/enabled
cripta-exit-runtime.service                     active/enabled
cripta-strategy-dispatcher.service              inactive/disabled
```

```text
strategy_entry.execution_permissions: enabled = 0, total = 0
```

Mainnet gate в этом проходе повторно не подтверждён отдельным успешным запросом,
поэтому прошлое значение не выдаётся как `CHECKED HERE`.

Legacy identifiers с `M3` — технический долг и не создают термин `M3`.


# 16. Capital allocation V1

CANON:

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

Это owner-approved архитектурное правило. Текущая production реализация
reservation/counterfactual path должна проверяться отдельно.
