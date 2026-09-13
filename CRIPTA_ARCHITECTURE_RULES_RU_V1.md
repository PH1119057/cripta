# CRIPTA — архитектурные правила проекта

Версия: 1.4 · 2026-09-12
Назначение: верхняя модель проекта, владельцы прикладных решений, технический поддерживающий контур, жизненный цикл торговой попытки и обязательные архитектурные границы.

Процесс patch/install/Git вынесен в `CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`.

# 1. Главный принцип

Проект имеет:

1. **прикладной торговый контур** — отвечает на вопрос, что система понимает о рынке, какую Strategy применяет и какое торговое действие требуется;
2. **технический поддерживающий контур** — обеспечивает прикладной контур данными, связью, хранением, исполнением, восстановлением, наблюдаемостью и аудитом.

Эти контуры связаны и взаимозависимы, но не являются двумя конкурирующими торговыми системами.

Технический контур поддерживает прикладной. Он не получает права самостоятельно изобретать торговую логику только потому, что без него прикладной контур не может работать.

# 2. Пять верхнеуровневых архитектурных уровней

Каноническая прикладная цепочка:

```text
1. MAYAK
      ↓
2. DISPATCHER
      ↓
3. STRATEGY
   ├── ENTRY
   └── EXIT
      ↓
4. EXECUTION
      ↓
5. EXCHANGE
```

Это верхняя архитектурная карта проекта.

Не каждый процесс, daemon, таблица, сервис, библиотека или аналитический компонент является отдельным верхнеуровневым слоем.

# 3. MAYAK

MAYAK — независимый наблюдатель внешнего рынка.

Он отвечает:

> Что происходит на рынке?

MAYAK может наблюдать цену, сделки, объём, деньги, открытый интерес, funding, стакан, ликвидации, ширину и синхронность рынка, разные торговые площадки, on-chain и внешний макро/политический контекст, если он утверждён как источник.

MAYAK не выбирает Strategy, не открывает и не закрывает позиции, не задаёт размер позиции, не блокирует Entry, не двигает stop, не меняет торговый счёт и не учится автоматически на PnL конкретной стратегии.

Его результат — причинный, версионированный снимок внешнего рынка.

# 4. DISPATCHER

Dispatcher — прикладной слой общей обстановки между MAYAK и торговыми стратегиями.

Он публикует **показатели**, а не торговые команды.

## 4.1 Объективный рыночный контекст

На основе MAYAK Dispatcher структурирует и публикует причинное состояние рынка, не зная тип Entry, правила Strategy или фактический PnL.

Dispatcher не отвечает на вопрос «подходит ли рынок конкретной Strategy». Это интерпретация Strategy. Один и тот же объективный контекст разные Strategy могут трактовать противоположно.

Минимально различаются:

- общерыночное состояние;
- состояние конкретного инструмента/монеты;
- фактический исполненный денежный поток на spot и derivatives раздельно;
- OI/positioning;
- ликвидность и её изменение;
- ликвидации и фаза/ускорение каскада;
- relative strength / synchronization / divergence;
- freshness, coverage, quality и provenance.

Dispatcher не имеет торговых mutation rights.

## 4.2 Состояние торгового счёта

Dispatcher также публикует фактическое состояние доступной торговой ёмкости аккаунта, полученное из технического контура подключения к текущей торговой площадке.

Минимально должны быть различимы:

- общий торговый баланс / equity, где применимо;
- уже занятые средства;
- зарезервированные средства;
- свободные средства;
- фактически доступная сумма для новой торговли;
- freshness и provenance такого состояния.

Архитектура не привязана к конкретной бирже.

Источник фактической истины — подключённая торговая площадка и её торговый аккаунт. Технический контур получает и нормализует эту истину; Dispatcher публикует её прикладным потребителям как показатель.

Dispatcher не «владеет деньгами», не резервирует их по собственной воле и не создаёт ордер.

## 4.3 Объективный рейтинг монеты

MAYAK может рассчитывать причинные strategy-agnostic признаки состояния конкретной монеты. Dispatcher может собирать их в версионированный `CoinMarketRating` / карточку монеты.

Такой рейтинг описывает сам рынок: приток/отток реально исполненных денег, активность, ликвидность, ликвидации, OI, относительную силу, синхронность, качество данных и событийный риск. Он не использует результаты нашей Strategy/Entry и не является рекомендацией LONG/SHORT.

Историческая совместимость конкретной Strategy с монетой (`StrategyCoinFit`) является отдельным аналитическим/Strategy-specific объектом и не должна менять MAYAK или объективный Dispatcher rating.

# 5. STRATEGY

Strategy — утверждённая владельцем пассивная, неизменяемая и версионированная торговая policy (`StrategyCard`). Она не является ботом, монитором или исполнителем.

Strategy является единственным владельцем торгового смысла конкретного способа торговли. В ней живут:

- условия подходящей среды;
- Entry policy, включая геометрию, касания, последовательности, timers/cooldown/reset и все численные параметры;
- правила использования объективного MAYAK/Dispatcher context;
- размер входа, allocation и leverage policy;
- stop, допустимая просадка, holding и initial protection;
- Exit policy.

Все торговые числа принадлежат Strategy. Универсальный Entry не должен хранить strategy-specific значения как собственные магические константы.

Включение/выключение Strategy хранится отдельно как `StrategyActivation` и не изменяет immutable StrategyCard. Владельцем через управляющий контур может быть одновременно включено любое число утверждённых Strategy, в том числе противоречащих друг другу.

Из StrategyCard конкретной версии материализуются immutable `EntryPlan` и `ExitPlan` с собственными fingerprint. Materializer/compiler является внутренней технической функцией уровня Strategy и не мониторит рынок.

Никто из MAYAK, Dispatcher, Analyst, Supervisor или технического контура не изменяет утверждённую Strategy автоматически.

Изменение торговой policy = новая утверждённая владельцем версия/fingerprint.

## 5.1 Полнота Strategy contract

Strategy не может содержать decision/execution-affecting параметр без полного пути исполнения. Любое
такое поле обязано materialize-иться в точный план, быть причинно наблюдаемым/аудируемым, реально
потребляться Entry или Exit и без потери доходить до Execution там, где оно меняет требуемое торговое
действие. Совместимость проверяется acceptance-test.

`UI/STORAGE ONLY` для торгового параметра запрещён. Unknown/unsupported downstream consumer означает
fail-closed и запрет Activation exact Strategy version. Metadata обязана быть явно non-decision-affecting.

# 6. ENTRY

Entry — специализированная часть Strategy и единый универсальный параметризованный механизм исполнения активных `EntryPlan`.

Entry **не выбирает Strategy**, не сравнивает их, не ранжирует и не выключает. Если включено несколько Strategy, их EntryPlan наблюдаются независимо. Противоречащие LONG/SHORT планы допустимы и не разрешаются Entry скрытым arbitration.

Логически Entry включает:

```text
ACTIVE PLAN REGISTRY
-> ENTRY WATCH
-> STRATEGY SIGNAL
-> ENTRY DECISION
-> optional ExecutionRequest
```

Технический market-data/Monitor/Scanner поставляет причинные рыночные факты/события. Сам рыночный факт не является торговым signal конкретной Strategy.

`StrategySignal` (`signal_id`) создаётся Entry Watch, когда causal market state и обязательный consumed context удовлетворяют декларативному EntryPlan конкретной активной Strategy.

Entry может реализовывать универсальные операции `touch/break/retest/count/sequence/window/reset/AND/OR/NOT`, но их значения и торговый смысл задаются Strategy. Например, `candidate cooldown` является отключаемой Strategy-настройкой; исторические 30 минут не являются свойством универсального Entry.

Для конкретной попытки фиксируются как минимум:

```text
signal_id
strategy_attempt_id
strategy_id
strategy_version
strategy_config_fingerprint
entry_plan_fingerprint
strategy_activation_id
```

Entry использует только те objective contexts, которые EntryPlan разрешает/требует, сохраняет `CONSUMED_CONTEXT`, проверяет применимый account-capacity state и mandatory technical readiness, затем принимает решение конкретной attempt.

Entry не исполняет биржевую заявку. Только `ACCEPTED` создаёт `ExecutionRequest`, который передаётся Execution.

После fill Entry не должен менять Strategy позиции.

# 7. EXIT

Exit — специализированная часть той же Strategy.

После confirmed fill Exit сопровождает позицию по той же `strategy_id/version/fingerprint`, по которой был выполнен Entry.

Разные Strategy могут иметь принципиально разные stop, допустимую глубину отката, break-even, trailing, holding и причины закрытия.

Нельзя молча применить Exit policy одной Strategy к позиции другой Strategy.

# 8. Risk — не верхнеуровневый слой

В проекте не существует самостоятельного верхнеуровневого архитектурного слоя `Risk`.

Слово `Risk` может оставаться в коде, исторических документах, research и специализированных формулах.

Архитектурно соответствующие обязанности распределены:

- MAYAK — наблюдает опасные/нестабильные состояния рынка как факты;
- Dispatcher — показывает рыночный контекст и состояние доступной торговой ёмкости;
- Strategy — задаёт размер, плечо, stop, допустимую просадку и правила удержания;
- Entry — фиксирует решение конкретной попытки;
- Exit — сопровождает позицию по strategy policy;
- Execution / technical safety — не выполняет небезопасную mutation при неизвестном обязательном состоянии;
- Exchange — является фактическим ограничителем по доступным средствам, позициям, правилам инструмента и исполнению.

Нельзя заново создать отдельный top-level `Risk` layer без решения владельца и новой версии архитектурного контракта.

# 9. EXECUTION

Execution реализует принятое прикладное торговое решение на подключённой торговой площадке.

Execution владеет readiness непосредственно перед mutation, order requests, exchange/client IDs, actual fills, qty, actual avg fill, protection mutations, reconciliation и durable handoff.

Execution не выбирает Strategy, не придумывает Entry/Exit и не меняет стратегический размер, stop или holding policy по собственной инициативе.

# 10. EXCHANGE

Exchange — внешняя торговая площадка.

Архитектура CRIPTA не привязана к одной конкретной бирже.

Фактическая торговая площадка является live truth по доступным ей данным, включая торговый баланс/account equity, свободные/занятые средства, positions, orders, fills, instrument rules, leverage/margin/position mode, fees/funding/break-even, если площадка их предоставляет.

# 11. Технический поддерживающий контур

Технический контур не является шестым торговым уровнем.

Он включает компоненты, необходимые для работы пяти верхних уровней, например:

- connectivity / market data adapters;
- private account/exchange sync;
- clock/reconnect/watchdog;
- нормализацию exchange/account state;
- PostgreSQL;
- журналы и audit trail;
- Position Supervisor;
- Analyst;
- UI/read models;
- service management;
- restart/reconciliation;
- архивирование;
- operational safety;
- monitoring/health.

Технический компонент может обслуживать несколько прикладных уровней.

Это не даёт ему права принимать торговое решение вместо Strategy.

# 12. Рыночные факты, Monitor / Scanner и карточка сигнала

Monitor/Scanner относится к техническому/наблюдательному обеспечению и публикует причинные рыночные факты/события. Он не выбирает Strategy и не является отдельным владельцем торговой policy.

Рыночный `MarketEvent`/source fact не равен StrategySignal.

При выполнении EntryPlan Entry Watch создаёт strategy-specific `signal_id` (`StrategySignal`) и постоянную причинную карточку. Карточка существует независимо от дальнейшего отказа, отсутствия средств, operational block, no-fill или execution rejection.

Точная source lineage к trade/candle/zone/context должна сохраняться; не требуется изобретать synthetic `market_event_id` для каждого тика, если уже есть точные source references.

# 13. Несколько Strategy и сигналов

Архитектура должна масштабироваться на множество Strategy, EntryPlan, bot instances и simultaneous attempts.

Один и тот же рыночный момент/набор причинных фактов может породить ноль, один или несколько независимых `StrategySignal` разных Strategy. Каждый signal связан с точной `strategy_id/version/fingerprint` и `entry_plan_fingerprint`.

Разные Strategy могут одновременно породить противоположные LONG/SHORT signals даже по одному symbol. Entry не имеет права выбирать между ними.

Один strategy-specific signal может иметь несколько attempts только если это отдельно требуется явной моделью bot/account execution; скрытого cross-strategy arbitration не существует.

Не устанавливать без отдельного решения владельца максимальное число Strategy, bot instances, одновременно открытых positions, механизм конкуренции Strategy за капитал или алгоритм приоритета/allocator между Strategy.

# 14. Состояние денег и причина отказа

Цепочка:

```text
EXCHANGE ACCOUNT TRUTH
      ↓
TECHNICAL ACCOUNT SYNC
      ↓
DISPATCHER TRADING-CAPACITY CONTEXT
      ↓
STRATEGY / ENTRY
```

Strategy определяет, сколько она хочет использовать.

Entry сравнивает потребность выбранной Strategy с актуально опубликованной доступной торговой ёмкостью и принимает решение.

Если средств недостаточно, это отдельная причина:

```text
INSUFFICIENT_AVAILABLE_FUNDS
```

Она не смешивается с `STRATEGY_CONDITION_REJECTED`, `OPERATIONAL_SAFETY_BLOCKED` или `EXCHANGE_REJECTED`. Исторический token `DISPATCHER_MARKET_INCOMPATIBLE` допускается только как legacy-аудит старого profile-based механизма и не является канонической новой причиной отказа.

# 15. Dispatcher не является торговым gate

Dispatcher показывает context.

Правильная причинная цепочка:

```text
Dispatcher objective global/coin context
      ↓
Strategy interpretation / policy
      ↓
Entry decision
```

Dispatcher не создаёт order block mutation.

# 16. Global Market State

Dispatcher может публиковать общий advisory indicator:

```text
NORMAL
CAUTION
HIGH_RISK
CRITICAL
UNKNOWN
```

Разные Strategy могут трактовать его по-разному.

Универсальная рыночная команда `CLOSE ALL` из Dispatcher запрещена без отдельного нового архитектурного решения.

# 17. Operational Safety

Operational safety относится к техническому поддерживающему контуру.

Он может fail-closed блокировать unsafe mutation при stale/unknown mandatory account state, clock/reconnect failure, reconciliation failure, unknown position/qty/fill/protection, неподтверждённом состоянии доступных средств, невозможности безопасно выполнить exchange mutation или owner emergency kill.

Это не торговое мнение о рынке и не Strategy.

# 18. Жизненный цикл и точные ID

Корневая торговая история начинается со strategy-specific `StrategySignal`; causal market facts находятся upstream и сохраняются как source lineage.

Целевая связь:

```text
causal market source refs
  -> signal_id                         # StrategySignal
  -> strategy_id/version/fingerprint
  -> entry_plan_fingerprint
  -> strategy_activation_id
  -> strategy_attempt_id
  -> entry_decision_id
  -> entry_command_id
  -> exchange/client order IDs
  -> execution IDs
  -> trade_id / position_id
  -> exit_decision_id
```

Нельзя восстанавливать ownership по `symbol + ближайшее время`.

# 19. Analyst / Supervisor

Position Supervisor и Analyst находятся в поддерживающем наблюдательно-аналитическом контуре.

Они не выбирают Strategy, не меняют Strategy автоматически, не открывают/закрывают позиции напрямую и не становятся новыми top-level trading layers.

# 20. Research != Production

Разрешённый путь:

```text
STATISTICS
-> RESEARCH
-> OWNER-APPROVED NEW VERSION
-> SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
```

# 21. Масштабирование

Верхняя архитектура не должна быть зажата текущим числом позиций или одной Strategy.

Будущая система может иметь много Strategy, много bot instances, много одновременных positions и разные торговые площадки.

Точные portfolio limits, allocator, strategy arbitration и capital competition не определяются этим документом.

# 22. Hard stop при конфликте

Если код или более низкий документ делает `Risk` отдельным верхним владельцем, Dispatcher торговым исполнителем/запускателем Strategy, technical service владельцем Strategy, MAYAK источником торговой команды, Entry владельцем выбора/ранжирования Strategy, universal Entry носителем скрытых strategy-specific торговых constants, Exit независимым от Strategy binding конкретной позиции или exchange-specific правило универсальной архитектурой — это архитектурный конфликт.

Порядок:

```text
HARD STOP
-> report mismatch
-> owner decision if needed
-> canonical docs
-> architecture tests
-> code
```

# 23. Текущий production checkpoint

Текущий production может не реализовывать все целевые элементы этого документа.

Новая архитектура не является автоматическим разрешением немедленно менять production-код.

Сначала документация становится каноном. Затем отдельной задачей проводится аудит соответствия текущей реализации.
