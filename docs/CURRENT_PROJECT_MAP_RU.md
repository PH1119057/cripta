# CRIPTA — текущая карта проекта

**Версия:** 8.3  
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
Position Supervisor + Analyst/Research.

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

# 6. Strategy / Universal Entry

В source существует Universal Entry contour:
- immutable StrategyCard;
- StrategyActivation;
- EntryPlan/ExitPlan materialization;
- ActivePlanRegistry;
- `UniversalEntryEngine`;
- `ParameterizedCausalMarketWatch`;
- StrategySignal/Attempt/Decision/ExecutionRequest;
- PostgreSQL evidence/read-model;
- execution bridge.

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

Точный универсальный контракт будущих Exit-мутаций в Execution пока не
утверждён; это открытый архитектурный вопрос.

# 12. Analytics

Analyst/Research — доказательный контур без trading rights.

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
- не превращает обсуждаемый Exit Engine contract в канон до отдельного решения.

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
