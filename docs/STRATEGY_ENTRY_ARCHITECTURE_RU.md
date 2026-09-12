# STRATEGY / ENTRY — УНИВЕРСАЛЬНЫЙ АРХИТЕКТУРНЫЙ КОНТРАКТ

**Документ:** `STRATEGY_ENTRY_ARCHITECTURE_RU.md`
**Версия:** 1.4
**Дата:** 2026-09-12
**Статус:** канонический специализированный архитектурный контракт
**Основание:** явное решение владельца 2026-09-08

Верхние контракты:

- `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
- `PROJECT_ARCHITECTURE_RU.md`
- `PROJECT_GOVERNANCE_RU.md`
- `STRATEGY_DISPATCHER_ARCHITECTURE_RU.md`
- `SIGNAL_LIFECYCLE_CONTRACT_RU.md`

## 1. Решение владельца

Strategy является единственным владельцем торговой политики конкретного способа торговли.

Strategy не является ботом, монитором, сервисом исполнения или активным участником рынка. Канонически Strategy — это утверждённая владельцем неизменяемая версионированная карточка правил (`StrategyCard`).

Entry является единым универсальным параметризованным механизмом исполнения Entry-политики. Entry не выбирает Strategy, не сравнивает Strategy между собой, не ранжирует их и не содержит собственных strategy-specific торговых правил.

Каноническая формула:

```text
StrategyCard
  определяет торговую policy
        ↓
StrategyActivation
  определяет, какие версии Strategy включены владельцем
        ↓
EntryPlan / ExitPlan
  неизменяемые планы конкретной Strategy version
        ↓
Universal Entry Engine
  независимо наблюдает все активные EntryPlan
        ↓
StrategySignal
        ↓
EntryDecision
        ↓
ExecutionRequest
        ↓
Execution
        ↓
Exchange
```

Это не добавляет новый верхнеуровневый слой. `StrategyActivation`, materialization/compilation планов и Entry Watch являются внутренними техническими реализациями уже существующих уровней Strategy и Entry.

## 1.1 Решение владельца 2026-09-12 — рабочая карточка Strategy

Для будущего Universal Entry owner утвердил рабочую модель одной `StrategyCard` как одной
неизменяемой версии всей торговой policy конкретного способа торговли. В пользовательском UI
карточка имеет понятное владельцу `name` и один основной выбор направления: `LONG` или `SHORT`.
Новые live-oriented версии, создаваемые через Strategy UI, не смешивают LONG и SHORT в одной
карточке; одинаковая геометрия не отменяет возможные различия policy между направлениями.

Внутри одной карточки отображаются три самостоятельные секции/вкладки:

```text
ENTRY
EXIT
HEDGE
```

Это не создаёт новые top-level layers. Канонические уровни остаются
`MAYAK -> DISPATCHER -> STRATEGY(ENTRY/EXIT) -> EXECUTION -> EXCHANGE`.
`HEDGE` является Strategy-owned post-fill/lifecycle sub-policy. Будущее фактическое решение о
хеджирующем действии не даёт MAYAK, Dispatcher или UI права мутировать биржу; mutation остаётся
за Execution, а post-fill policy принадлежит Strategy/Exit lifecycle.

На текущем этапе допускается явное повторение одинаковой Exit policy в нескольких immutable
Strategy versions. Отдельная библиотека/общий mutable Exit-template не вводится: это исключает
скрытую связь, при которой изменение одной Exit-настройки молча меняет несколько Strategy.

Карточка обязана позволять owner явно задать или выключить как минимум:

- понятное имя Strategy и одно направление LONG/SHORT;
- Entry: signed offset относительно рассчитанного Entry, базовый lookback по timeframe, optional
  локальный поиск точки входа на коротком окне, touch/Nth-touch/cooldown/reset, signal/execution
  lifetime, capital/leverage/execution parameters;
- отдельное использование objective MAYAK/Dispatcher features в режимах `OFF / OBSERVE /
  CONDITION / RANKING`, с explicit freshness/quality/missing/stale/partial semantics для
  decision-affecting режимов;
- Exit: hard stop, take profit, fee-aware break-even, trailing, optional time/zone/context exit
  rules и используемые objective market/local-position features;
- Hedge: explicit `enabled`, trigger depth/offset относительно основной позиции, size/leverage,
  hedge stop loss, take profit и trailing. Не заданное поле не превращается в hidden default.

Исторические/research числа не становятся live policy только из-за того, что UI умеет их хранить.
В частности, локальное 3-часовое окно, исследовательские trailing/BE варианты и любые найденные
корреляции MAYAK/Dispatcher сохраняются как настраиваемые кандидаты до явного owner-approved
Strategy version.

## 1.2 Решение владельца 2026-09-12 — universe и Entry lifecycle/reset в StrategyCard

Перед переходом к следующей Entry policy owner уточнил, что StrategyCard обязана явно владеть не
только геометрией входа, но и своим universe монет и всеми временными reset/cooldown/embargo
правилами. Никакое историческое число Entry V1 не остаётся скрытым default универсального Entry.

Для каждой Strategy version обязательно хранится непустой список `symbols`. Это позволяет разделять
монеты по характеру движения через разные StrategyCards/versions без введения selector/allocator.
Одна Strategy применима только к явно перечисленным инструментам.

Entry authoring обязан различать как минимум четыре независимых механизма:

1. `candidate_cooldown` — пауза после TOUCH/SIGNAL/ATTEMPT. Исторический V1: после любого TOUCH от
   `candidate_bar_at`, `PER_SYMBOL`, 30 минут. Это не failure embargo.
2. `post_signal_outcome_policy` — причинное наблюдение уже созданного StrategySignal. Исторический
   V1: FIRST_THRESHOLD, `+0.50%` favorable против `-1.00%` adverse, horizon 360 минут; adverse
   resolution запускает `PER_SYMBOL` embargo 60 минут от факта adverse outcome. Все эти числа
   должны быть редактируемыми Strategy data.
3. `shock_reset_policy` — забывание shock-candle и всей более старой zone history с обязательным
   временем созревания новой зоны. Карточка должна поддерживать explicit detection mode. Минимально:
   `ATR_MULTIPLE` (legacy V1: True Range >= 3.0 × mean True Range previous 20 bars) и
   `RANGE_PERCENT` (True Range как процент previous close с явным threshold для каждого timeframe).
   Maturity/wait after shock задаётся Strategy; исторический V1 = 60 минут.
4. `rolling_swing_gate` — отдельный динамический запрет Entry по диапазону рынка за rolling window.
   Исторический V1: 5m candles, 12 bars = 60 минут, high/low range >= 10%; блок снимается, когда
   rolling condition больше не выполняется, а не через фиксированный дополнительный таймер.

Эти механизмы независимы и не должны объединяться в одно поле `reset`. Missing/disabled policy не
может тайно наследовать V1 30/60/10%/3x значения.

Strategy dashboard должен иметь заметный верхний action создания Strategy, отдельно от action
создания новой immutable version существующей Strategy. Создание Strategy/версии не создаёт
StrategyActivation и не включает consumer.

## 1.3 Решение владельца 2026-09-12 — Entry 2 не наследует скрытые V1 gates

Новая Strategy/Entry policy не обязана иметь V1-specific `flow` или `oi` gate только потому, что
legacy Entry V1 их использует. Если эти встроенные gates не выбраны owner, новая Strategy хранит их
явно как `enabled=false`; они не участвуют в решении. Денежные, OI, liquidity и иные market-context
условия Entry 2 подключаются только как явно выбранная Strategy policy из objective MAYAK/Dispatcher
контекста либо как отдельная owner-approved sensor rule.

`history_limit` causal watch является техническим размером in-memory history buffer, а не торговым
числом Strategy. Если legacy card хранит explicit history_limit, он воспроизводится для parity. Для
новой Strategy без explicit value буфер вычисляется детерминированно из её собственных lookback,
ATR/shock и rolling-window требований и не меняет торговой семантики.

Disabled Entry Watch хранится только как `{enabled:false}` и не может прятать геометрию/timers.
Когда Entry Watch включён, StrategyCard обязана явно содержать direction rules, required timeframes,
flow/OI enabled state, candidate lifecycle и derived event contract.

## 1.3 Решение владельца 2026-09-12 — Activation, observer и полнота исполнения policy

StrategyCard должна иметь явный owner-control `АКТИВНА / НЕАКТИВНА`. Одновременно могут
существовать десятки immutable Strategy versions, но Universal Entry observer имеет право
наблюдать только exact versions, для которых существует `StrategyActivation(enabled=true)`.
Переключатель не изменяет StrategyCard: первая активация создаёт отдельную StrategyActivation и
immutable EntryPlan/ExitPlan exact version; последующие переключения меняют только enabled-state с
append-only journal.

Активация запрещена fail-closed, если хотя бы одно включённое поле StrategyCard не имеет доказанного
потребителя в полном runtime-path. Карточка обязана публиковать `runtime_readiness` с точными причинами.
Недопустимо сохранять параметр в UI/JSON, но молча игнорировать его в Entry, Exit или Execution.

Различаются:

- `POLICY_READY` — все включённые поля карточки имеют определённую runtime-семантику;
- `OBSERVER_READY` — production observer умеет загрузить enabled StrategyActivation и подать ей
  причинные market/context/account facts;
- `EXECUTION_READY` — созданный ExecutionRequest переводится в существующий Execution contract без
  потери Strategy-owned параметров;
- `ACTIVE` разрешён только при одновременной готовности обязательных контуров.

Исторический Entry V1 и его parity-shadow после owner decision 2026-09-12 являются archive-only.
Они не считаются production observer для новых StrategyCard и не должны автоматически запускаться.
Immutable V1 source/data/evidence сохраняются для аудита и воспроизводимости.

Особо: неоднозначная торговая семантика не достраивается разработчиком. Пока owner не утвердил
точный алгоритм `local_entry_policy`, включённый local Entry обязан блокировать activation. Аналогично
неподключённые Strategy-specific Exit/Hedge/context rules блокируют activation, а не игнорируются.

## 2. StrategyCard

`StrategyCard` — пассивная неизменяемая карточка торговой политики.

Минимально различать:

```text
strategy_id
strategy_version
strategy_config_fingerprint
name / description

scope / symbols / direction policy
entry_policy
exit_policy
capital_policy
protection_policy
lifecycle_policy
market-sensor policy
MAYAK-origin context consumption policy
Dispatcher context consumption policy
```

После утверждения версия Strategy неизменяема. Любое изменение торгового смысла, численного параметра или правила использования контекста создаёт новую `strategy_version` и новый fingerprint.

StrategyCard сама:

- не читает поток рынка;
- не запускает процесс;
- не создаёт ордер;
- не создаёт StrategySignal;
- не выбирает другие Strategy;
- не включается и не выключается самостоятельно.

## 3. Все торговые параметры принадлежат Strategy

Любые торговые числа и временные ограничения являются данными Strategy, а не магическими константами Entry.

Примеры:

```text
таймфреймы
lookback
ATR period / multiplier
ширина зоны
процентный gap
глубина касания
число касаний
cooldown
failure embargo
shock threshold
shock reset pause
процент входа/инвалидизации/цели
окна времени
минимальная дистанция
size / allocation / leverage
stop / protection / exit thresholds
```

Entry-код не должен содержать значение `30 минут`, `60 минут`, `0.25%`, `0.5 ATR` или иное торговое число как универсальную policy только потому, что оно использовалось одной исторической Strategy.

Если параметр поддерживает отключение, Strategy должна иметь возможность явно задать `enabled=false`.

## 4. Candidate cooldown

`candidate cooldown` не является обязательным свойством Entry.

Историческое значение `30 минут` относится к конкретной реализации Entry V1 и не переносится в универсальный Entry как каноническое правило.

Целевая модель:

```text
candidate_cooldown:
  enabled: true | false
  duration: <value when enabled>
  scope: PER_SYMBOL | PER_STRATEGY | PER_ACCOUNT | другое явно утверждённое значение
```

При `enabled=false` Entry не создаёт скрытой временной задержки.

## 5. Touch Policy

Касание является полноценной настраиваемой частью Strategy, а не одной жёстко зашитой проверкой Entry.

Strategy может определять:

```text
accepted_touch_numbers
first_touch
second_touch
third_touch
fourth_plus / Nth

что считается независимым новым касанием
require_exit_from_zone
minimum_exit_distance: enabled/value
minimum_time_between_touches: enabled/value
maximum_touch_count: enabled/value

reset_touch_counter_on:
  NEW_ZONE
  SHOCK
  CONFIRMED_BREAK
  TIMEOUT
  другие явно определённые события
```

Ни минимальное время, ни минимальная дистанция, ни число касаний не являются глобальной константой Entry. Если Strategy не требует временного ограничения между касаниями, оно выключено.

## 6. StrategyActivation

Включение/выключение Strategy отделено от самой StrategyCard.

Минимально:

```text
activation_id
strategy_id
strategy_version
strategy_config_fingerprint
enabled
enabled_at
disabled_at
optional symbol/account/bot-instance scope
operator/source
```

Изменение `enabled` не создаёт новую версию Strategy и не переписывает StrategyCard.

Управляющий пульт владельца может включать и выключать любое количество утверждённых Strategy.

## 7. Несколько и противоречащих Strategy

Количество одновременно включённых Strategy архитектурно не ограничивается Entry.

Если включены пять Strategy, Entry обязан независимо обслуживать пять соответствующих EntryPlan. Это не требует пяти процессов ОС: модель concurrency является implementation detail.

Допустимо одновременное существование противоположных правил, например:

```text
Strategy A -> LONG XRP
Strategy B -> SHORT XRP
Strategy C -> LONG SOL
```

Entry не имеет права устранять противоречие выбором «лучшей» Strategy.

Запрещены скрытые механизмы:

```text
ENTRY_SELECTS_STRATEGY = YES
ENTRY_STRATEGY_PRIORITY = YES
ENTRY_OTHER_STRATEGY_WON = YES
```

Если в будущем потребуется allocator, strategy priority, capital arbitration или portfolio competition, это отдельный owner-approved архитектурный контракт.

## 8. Материализация EntryPlan / ExitPlan

Из StrategyCard для конкретной версии формируются неизменяемые планы:

```text
EntryPlan
ExitPlan
```

Materializer/compiler является технической функцией реализации уровня Strategy. Он не является новым верхним уровнем, не мониторит рынок и не принимает торговых решений.

`EntryPlan` минимум содержит:

```text
strategy_id
strategy_version
strategy_config_fingerprint
entry_plan_version
entry_plan_fingerprint
scope / symbols / directions
market predicates / sequence rules
lifecycle / touch / reset policy
required context policy
capital request semantics
missing/stale semantics
```

План должен быть достаточен, чтобы универсальный Entry Engine не содержал веток вида:

```text
if strategy_id == "...":
    <special trading logic>
```

## 9. Universal Entry Engine

Entry — единый универсальный механизм.

Его логические обязанности:

```text
ACTIVE PLAN REGISTRY
  хранит активные EntryPlan

ENTRY WATCH
  причинно сравнивает каждый EntryPlan с текущими допустимыми данными

STRATEGY SIGNAL
  фиксирует факт выполнения сигнальных условий конкретного EntryPlan

ENTRY DECISION
  фиксирует итог конкретной попытки и при ACCEPTED формирует ExecutionRequest
```

Entry может технически поддерживать универсальные операторы:

```text
comparison
AND / OR / NOT
count / Nth event
sequence
window
timer
reset
touch
break
retest
reclaim
```

Наличие такого оператора в Entry-коде не означает торговой policy. Значения, комбинации и смысл операторов задаёт Strategy/EntryPlan.

## 10. Рыночный факт и StrategySignal — разные сущности

Технический market-data/monitoring контур публикует причинные рыночные факты и события. Он не решает, является ли факт торговым сигналом конкретной Strategy.

`MarketEvent` здесь означает логический причинный рыночный факт или ссылку на набор фактов. Не требуется создавать отдельный synthetic ID для каждого тика, если уже существует точная source lineage к trade/candle/zone/context/event.

`StrategySignal` возникает только когда Entry Watch установил:

```text
active EntryPlan
+ causal market state
+ обязательный consumed context
= сигнальные условия конкретной Strategy выполнены
```

Канонический торговый `signal_id` является идентификатором StrategySignal.

Один рыночный момент/набор фактов может породить:

- ноль StrategySignal;
- один StrategySignal;
- несколько независимых StrategySignal разных Strategy.

## 11. Кто создаёт StrategySignal

StrategyCard сама не создаёт signal.

Dispatcher не создаёт signal.

MAYAK не создаёт signal.

Отдельный strategy-aware Monitor между Strategy и Entry не создаётся как новый архитектурный слой.

StrategySignal создаёт `Entry Watch` внутри уровня Entry, выполняя декларативный EntryPlan.

## 12. Market monitoring

Существующий Monitor/Scanner в целевой модели должен рассматриваться как техническое наблюдение/сенсорный источник рыночных фактов либо как implementation Entry Watch, но не как самостоятельный владелец торговой policy.

Нельзя иметь два независимых владельца одного и того же решения:

```text
strategy-aware Monitor выбирает торговлю
+
Entry повторно выбирает торговлю
```

Торговый смысл единожды задаётся Strategy и исполняется EntryPlan.

## 13. MAYAK и Dispatcher

Каноническая верхняя цепочка сохраняется:

```text
MAYAK -> DISPATCHER -> STRATEGY(ENTRY/EXIT)
```

MAYAK наблюдает объективный рынок.

Dispatcher универсально структурирует объективный global/coin context и публикует торговую ёмкость.

StrategyCard определяет, какие доступные показатели нужны этой Strategy и какова их роль.

Для настройки Strategy разрешается группировать параметры по происхождению, например `MAYAK-origin facts` и `Dispatcher contexts`, но это не даёт Strategy права менять MAYAK/Dispatcher и не превращает их в торговые gate.

Рекомендуемые режимы использования доступного показателя:

```text
OFF        — Strategy не использует показатель
OBSERVE    — показатель сохраняется для карточки/аналитики, но не влияет на решение
CONDITION  — показатель участвует в условии Strategy
RANKING    — показатель участвует только в явно утверждённой Strategy-local оценке/ранжировании
```

`RANKING` не разрешает Entry сравнивать разные Strategy и не создаёт общий capital allocator.

Отсутствующие/устаревшие данные не превращаются в ноль или нейтральное состояние. Для `CONDITION` Strategy должна явно определить требования к quality/freshness и поведение при `missing/stale/partial`.

## 14. Dispatcher не запускает Strategy

Dispatcher запрещено:

- читать `StrategyActivation` ради управления Strategy;
- включать/выключать Strategy;
- выбирать Strategy;
- компилировать EntryPlan/ExitPlan;
- создавать StrategySignal;
- определять, что условия конкретного EntryPlan выполнены;
- создавать ExecutionRequest.

Название `Dispatcher` не меняет его каноническую роль: это strategy-agnostic слой объективного контекста, а не диспетчер запуска торговых стратегий.

## 15. Entry Decision

После StrategySignal Entry создаёт/ведёт конкретную `strategy_attempt_id` и фиксирует decision.

Минимальные причины различаются:

```text
ACCEPTED
STRATEGY_CONDITION_REJECTED
INSUFFICIENT_AVAILABLE_FUNDS
OPERATIONAL_SAFETY_BLOCKED
STALE_OR_UNKNOWN_REQUIRED_STATE
EXPIRED
CANCELLED
```

`EXCHANGE_REJECTED` и `NO_FILL` являются downstream execution outcomes и не должны маскироваться под рыночный выбор Strategy.

Entry не переоценивает другие Strategy при принятии решения одной attempt.

## 16. Capital competition

Strategy определяет желаемую allocation/size policy.

Dispatcher публикует причинный `TradingCapacitySnapshot`.

Entry может установить `INSUFFICIENT_AVAILABLE_FUNDS` для конкретной attempt по правилам Strategy и актуальному capacity state.

При одновременных независимых попытках Entry не изобретает скрытый приоритет Strategy. Если без отдельного allocator две accepted attempts конкурируют за один и тот же фактический капитал, дальнейшая реальность может привести к execution/exchange rejection одной из них; это фиксируется как фактический outcome.

Механизм reservation/allocator/arbitration требует отдельного owner decision.

## 17. Execution boundary

Entry не является исполнителем биржевой заявки.

```text
EntryDecision ACCEPTED
    ↓
ExecutionRequest
    ↓
EXECUTION
    ↓
EXCHANGE
```

Execution остаётся единственным владельцем биржевой mutation mechanics, readiness непосредственно перед mutation, order/client IDs, fills, reconciliation и durable execution handoff.

## 18. Exit binding

ExitPlan является частью той же Strategy version.

После fill позиция сохраняет:

```text
strategy_id
strategy_version
strategy_config_fingerprint
entry_plan_fingerprint
exit_plan_fingerprint
signal_id
strategy_attempt_id
position_id
```

Exit не может молча перейти на policy другой Strategy или новой версии Strategy.

## 19. Причинная история

Для StrategySignal и attempt необходимо восстанавливать минимум:

```text
signal_id                       # StrategySignal
strategy_attempt_id
strategy_id/version/fingerprint
entry_plan_version/fingerprint
strategy_activation_id
source_market_event_refs / causal source lineage
observed context IDs
consumed context IDs
context quality/freshness at decision time
entry decision and reason
requested allocation
capacity snapshot
optional execution lineage
```

`OBSERVED_CONTEXT` и `CONSUMED_CONTEXT` остаются разными сущностями.

Наличие objective context рядом с событием не доказывает его влияние на Strategy.

## 20. Уведомления администратору

Административные уведомления находятся в техническом поддерживающем контуре и не являются торговой Strategy.

Система должна допускать уведомления минимум о событиях:

```text
INSUFFICIENT_AVAILABLE_FUNDS
OPERATIONAL_SAFETY_BLOCKED
STALE_OR_UNKNOWN_REQUIRED_STATE
EXCHANGE_REJECTED
EXECUTION_FAILURE
```

Уведомление должно содержать точные IDs Strategy/attempt, symbol/direction, время и фактическую причину. Уведомление не изменяет решение и не является способом скрытого retry/arbitration.

## 21. Новые сенсоры

Добавление физически нового типа данных/сенсора может требовать изменения кода.

После появления нового универсального сенсора конкретная Strategy должна подключать и параметризовать его через StrategyCard/EntryPlan, а не через новую strategy-specific ветку внутри Entry.

## 22. Текущий Entry V1

Исторические документы и текущая реализация Entry V1 сохраняются как evidence/implementation truth своего периода.

Известные V1-параметры, включая 30-минутный candidate cooldown, `pressure_then_reversal`, OI calibration/tail gate и другие V1-specific условия, не становятся универсальными правилами нового Entry только потому, что сейчас существуют в коде.

Текущий production-код не переписывается этим документом автоматически.

Перед реализацией universal Entry требуется отдельный аудит соответствия текущего кода новому контракту, архитектурные тесты, implementation contract и обычная цепочка проверок/deploy.

## 23. Hard Stop

Архитектурный конфликт существует, если предлагаемая реализация:

- переносит торговые числа обратно в универсальный Entry как скрытые constants;
- заставляет Entry выбирать/ранжировать/отключать Strategy;
- заставляет Dispatcher запускать Strategy или создавать StrategySignal;
- делает StrategyCard активным торговым ботом;
- создаёт второй strategy-aware торговый Monitor с независимой policy;
- превращает MAYAK/Dispatcher context в общий универсальный trading gate;
- применяет Exit другой Strategy к позиции;
- скрывает отсутствие данных как ноль/neutral;
- восстанавливает ownership по `symbol + время`.

Порядок:

```text
HARD STOP
-> ничего не менять
-> указать точный конфликт
-> решение владельца / новая версия канона при необходимости
```

## 24. Итог

> Strategy хранит весь торговый смысл.

> StrategyActivation говорит только, какие утверждённые Strategy включены владельцем.

> EntryPlan переносит конкретную Entry-policy в универсальный Entry Engine.

> Entry независимо наблюдает каждый активный план, создаёт StrategySignal и принимает решение конкретной attempt.

> Entry не выбирает Strategy.

> Dispatcher не запускает Strategy.

> Execution исполняет уже принятое Entry-решение на Exchange.
