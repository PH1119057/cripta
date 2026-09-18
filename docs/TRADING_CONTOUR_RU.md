# CRIPTA — торговый контур: STRATEGY / ENTRY / EXIT / EXECUTION

**Версия:** 1.1  
**Дата:** 2026-09-18  
**Статус:** активный канонический контракт торгового контура

Этот документ объединяет правила четырёх связанных частей торгового контура:
Strategy, Entry, Exit и Execution. Верхняя архитектура определяется
`CRIPTA_ARCHITECTURE_RULES_RU_V1.md`, терминология — `CRIPTA_GLOSSARY_RU.md`.

# 1. STRATEGY — владелец торгового смысла и планов

Strategy layer владеет торговым смыслом и lifecycle утверждённой Strategy.

Пассивным immutable справочником является `StrategyCard`. Сам торговый смысл
не живёт внутри Entry/Exit Engines.

Внутри Strategy layer существует `Strategy Materializer`, который
детерминированно создаёт `EntryPlan` и `ExitPlan` exact Strategy version и
публикует их в устойчивый active-plan registry. Materializer не наблюдает рынок
и не создаёт `StrategySignal`/ExitDecision.

## 1.1 StrategyCard

`StrategyCard`:
- утверждается владельцем;
- неизменяема после утверждения;
- имеет version/fingerprint;
- не меняется автоматически от статистики;
- содержит все параметры, влияющие на решение и исполнение конкретной Strategy.

Изменение любого торгового параметра означает новую версию Strategy.

## 1.2 Что принадлежит Strategy

Strategy может определять минимум:
- symbols/universe;
- direction;
- Entry geometry;
- timeframe;
- временную глубину геометрии;
- ширину/формулу зон;
- правила совмещения нескольких геометрий;
- stabilization minutes;
- touch/retest/count/sequence;
- cooldown/reset/embargo;
- MAYAK/Dispatcher context consumption;
- capital allocation;
- amount/size;
- leverage;
- execution order policy;
- protection;
- hard stop/TP/BE/trailing;
- holding;
- Exit geometry/context rules;
- hedge, если он включён.

Если параметр отсутствует, Entry/Exit/Execution не имеют права подставить
историческое торговое значение по умолчанию.

## 1.3 H9/H3 как параметры Strategy

H9 и H3 — названия геометрий по временной глубине, а не глобальные константы
системы.

Текущая исследуемая первая Strategy использует H9:
- 5m на глубине 9 часов;
- 15m на глубине 9 часов;
- их совмещение по правилам Strategy.

Это не означает, что любая будущая Strategy обязана иметь H9.

H3:
- 5m на глубине 3 часа;
- 15m на глубине 3 часа;
- их совмещение.

H3 сейчас не является Entry condition текущей первой Strategy.

## 1.4 Стабилизация

Время стабилизации задаётся в Strategy в минутах отдельно там, где оно нужно.

Никакое найденное исследованием значение не становится значением по умолчанию
универсального Entry Engine.

## 1.5 Несколько Strategy

Одновременно могут быть активны несколько Strategy, включая противоположные:
- Strategy A может дать LONG;
- Strategy B в тот же момент может дать SHORT.

Они независимы. Entry Engine не выбирает между ними.

## 1.6 Материализация

Из exact StrategyCard/version `Strategy Materializer` создаёт immutable
`EntryPlan` и `ExitPlan`.

Материализация выполняется при activation/load/restart/recovery в тех местах,
где это требуется реализации. Materializer может быть функцией, классом или
service; это implementation detail внутри Strategy layer, а не новый
верхнеуровневый слой.

Каждый план обязан нести exact:
- strategy_id;
- strategy_version;
- strategy_fingerprint;
- собственный plan fingerprint.

Любое поле, влияющее на решение или исполнение, должно иметь доказанный
сквозной consumer path. Неподдержанный параметр означает fail-closed для
активации, а не silent ignore.

## 1.7 Исследование

Исследование никогда не меняет Strategy автоматически.

Результат становится торговой policy только после отдельного решения владельца
и создания новой Strategy version.

# 2. ENTRY — универсальный Entry Engine

## 2.1 Назначение

Entry Engine — универсальный активный исполнитель EntryPlan.

Каноническая схема:

```text
НОРМАЛИЗОВАННЫЕ ПРИЧИННЫЕ ФАКТЫ РЫНКА
        +
АКТИВНЫЕ ENTRY PLAN
        ↓
ENTRY ENGINE / CAUSAL MARKET WATCH
        ↓
СОВПАДЕНИЕ УСЛОВИЙ КОНКРЕТНОГО ПЛАНА
        ↓
STRATEGY SIGNAL
        ↓
ATTEMPT / ENTRY DECISION
        ↓
optional EXECUTION REQUEST
```

## 2.2 Strategy не создаёт signal как процесс

StrategyCard является пассивной policy.

Entry Engine сам фиксирует `StrategySignal`, когда причинные рыночные факты и
разрешённый/обязательный context удовлетворяют конкретному активному EntryPlan.

## 2.3 Независимость планов

Для каждого market fact Entry Engine рассматривает все активные EntryPlan,
относящиеся к symbol.

Разные Strategy имеют отдельные состояния, могут иметь разные Entry и
противоположные направления и создают разные `signal_id`.

Entry не вводит winner/priority/arbitration между Strategy.

## 2.4 Entry не владеет торговыми числами

В коде Entry допустимы только универсальные операции и техническая механика.

Торговые значения приходят через EntryPlan:
- lookback/depth;
- geometry formula;
- stabilization;
- touch;
- gap/confluence;
- cooldown/reset;
- sensors/context;
- amount/leverage/execution policy;
- lifetime/TTL;
- другие Strategy-owned поля.

Запрещены скрытые глобальные H9/H3/130 bars/30m/60m или иные исторические
торговые значения по умолчанию.

## 2.5 Entry zone / Entry point

`Entry zone` и `Entry point` — общие понятия, а не одна универсальная формула.

Текущая первая исследуемая Strategy может формировать вход через совмещение
H9 5m + H9 15m. Другая Strategy может использовать иной способ.

Поэтому Entry Engine не должен предполагать:
`Entry == H9`.

Если Strategy использует зональную геометрию, физические объекты называются
нейтрально:
- нижняя граничная зона;
- верхняя граничная зона;
- рабочий диапазон;
- внутренняя граница;
- внешняя граница.

LONG/SHORT задаёт роль этих объектов только на уровне Strategy.

## 2.6 Entry decision

StrategySignal сам по себе ещё не равен биржевому fill.

После signal создаётся exact attempt и EntryDecision.

Минимально различаются:
- ACCEPTED;
- STRATEGY_CONDITION_REJECTED;
- INSUFFICIENT_AVAILABLE_FUNDS;
- OPERATIONAL_SAFETY_BLOCKED;
- STALE_OR_UNKNOWN_REQUIRED_STATE;
- EXPIRED;
- CANCELLED.

Только ACCEPTED после успешной обязательной technical/capital reservation
создаёт `EntryExecutionRequest`.

## 2.7 Капитал и atomic reservation

Размер/лимит капитала задаёт Strategy/EntryPlan.

Dispatcher публикует account-capacity facts, но не распределяет капитал между
Strategy.

Перед real Entry требуется атомарная reservation разрешённой Strategy суммы.

Правило V1:
- кто первым успешно зарезервировал доступную сумму, тот её использует;
- Entry Engine не ранжирует Strategy и не выбирает «лучшую»;
- если средств недостаточно, `EntryDecision = INSUFFICIENT_AVAILABLE_FUNDS`;
- real ExecutionRequest не создаётся;
- Analyst может продолжить событие как counterfactual/псевдосделку.

Unknown order/fill state не освобождает reservation до reconciliation истины.

## 2.8 После fill

После confirmed open fill создаётся logical `StrategyPosition` с exact
Strategy/EntryPlan/ExitPlan lineage и фактическими exchange/order/fill refs.

Entry больше не сопровождает позицию.

Фактическая Entry price и causal snapshot сохраняются в истории.

# 3. EXIT — универсальный Exit Engine

## 3.1 Назначение и ownership

`Exit Engine` — универсальный активный исполнитель `ExitPlan`.

Количество Strategy ему не важно. Он получает StrategyPosition + exact
ExitPlan, наблюдает только разрешённые этим планом причинные facts/context и
создаёт `ExitDecision` при выполнении конкретного правила.

После confirmed fill сопровождение принадлежит ExitPlan той же exact Strategy
version, которая открыла позицию.

Exit Engine обязан claim/acknowledge StrategyPosition. Entry больше не владеет
позицией.

## 3.2 Неизменяемая точка входа

Фактическая `Entry price` — исторический факт состоявшегося входа.
Она не двигается вслед за рынком.

Снимок геометрии/фактов момента Entry хранится для аудита и причинности.

## 3.3 Текущая геометрия после Entry

Текущая геометрия Strategy продолжает причинно пересчитываться после входа.

Для H9 это означает, что она может:
- двигаться вверх;
- двигаться вниз;
- оставаться стабильной;
- менять граничные зоны/рабочий диапазон;
- изменяться многократно в течение одной позиции.

Движение может возникать как от 5m-, так и от 15m-компоненты.

Сопровождение анализирует актуальную геометрию относительно:
- фиксированной Entry price;
- предыдущего состояния;
- текущей цены;
- состояния позиции.

Исторический Entry snapshot и текущая геометрия — разные объекты.

Не требуется искусственно считать актуальную геометрию «той же неизменной
исходной зоной».

## 3.4 H3

H3 сейчас является геометрией сопровождения/research:
- 5m на 3 часах;
- 15m на 3 часах;
- совмещение двух компонентов.

H3 не является Entry condition текущей первой Strategy.

Наблюдение H3 не создаёт торгового эффекта само по себе. Чтобы H3 двигала stop,
trailing или закрывала позицию, правило должно быть явно утверждено в новой
Strategy version.

## 3.5 Initial protection и возможные Exit actions

Initial protection принадлежит Strategy и передаётся в Execution при открытии,
чтобы позиция не оставалась без базовой биржевой защиты, даже если динамическое
Exit-сопровождение временно недоступно.

ExitPlan может разрешать:
- SET/REPLACE stop;
- SET/REPLACE TP;
- fee-aware break-even;
- trailing;
- REDUCE;
- CLOSE;
- time exit;
- zone/geometry exit;
- MAYAK/Dispatcher context-based exit;
- protection/holding;
- hedge lifecycle.

Exit Engine не может выполнить action, отсутствующий в ExitPlan.

Никакое старое исследовательское число не является default.

## 3.6 ExitDecision и Execution

Когда условие ExitPlan выполнено, Exit Engine создаёт exact `ExitDecision`.

Принятое ExitDecision создаёт `ExitExecutionRequest` с exact
StrategyPosition/Strategy/ExitPlan lineage.

Exit Engine не мутирует Exchange напрямую.

## 3.7 Position observation

Карточка позиции должна позволять сохранять:
- fixed Entry price;
- exact Strategy binding;
- current geometry snapshots;
- geometry changes over time;
- MFE/MAE;
- protection changes;
- exact Exit decisions/execution;
- context links.

Наблюдение не равно торговому решению.

# 4. EXECUTION — техническое исполнение

## 4.1 Назначение

Execution — техническая граница между уже принятым торговым решением и внешней
торговой площадкой.

## 4.2 Вход Execution

Execution получает typed `ExecutionRequest`.

Логически различаются:
- `EntryExecutionRequest` — от принятого EntryDecision;
- `ExitExecutionRequest` — от принятого ExitDecision.

Entry request несёт signal/attempt/EntryDecision/EntryPlan lineage.

Exit request несёт StrategyPosition/ExitDecision/ExitPlan lineage.

Оба несут exact strategy_id/version/fingerprint, symbol/direction и необходимые
Strategy-owned execution/protection параметры.

## 4.3 Запрет собственной торговой логики

Execution:
- не выбирает Strategy;
- не меняет Entry formula;
- не вычисляет H9/H3 как собственную policy;
- не подставляет 130 bars/9h/3h/stop/leverage/TTL как глобальный trading default;
- не переоценивает MAYAK/Dispatcher;
- не решает, что LONG лучше SHORT.

Если нужная Strategy-owned policy отсутствует или unsupported — fail-closed.

## 4.4 Обязанности Execution

Execution отвечает за:
- validation exact identities/fingerprints;
- exchange adapter;
- order preparation/submission;
- idempotency;
- client/exchange IDs;
- fill truth;
- fees/slippage, где они измеримы;
- initial protection;
- retries без двойной мутации;
- reconciliation;
- durable handoff;
- recovery after restart;
- technical fail-closed.

## 4.5 Exchange-agnostic

Bybit является текущим подключённым провайдером.

Архитектура Execution должна позволять другие биржевые адаптеры без изменения
Strategy/Entry semantics.

## 4.6 Operational safety

Неизвестная позиция, stale private state, потеря reconciliation, неизвестный
fill/qty/protection или owner kill имеют право технически остановить mutation.

Это safety, а не новая оценка рынка.


# 5. Сквозной handoff

Торговый contour обязан оставлять доказуемый lifecycle:

```text
StrategyActivation
-> EntryPlan/ExitPlan materialized + published
-> Entry Engine consumed
-> StrategySignal
-> EntryDecision
-> EntryExecutionRequest
-> Execution ack
-> order/fill
-> StrategyPosition
-> ExitPlan binding
-> Exit Engine claim
-> ExitDecision
-> ExitExecutionRequest
-> Execution ack
-> final exchange result
```

Контроль того, что каждый обязательный handoff состоялся, принадлежит
`Lifecycle Supervisor` из technical support contour.

Entry/Exit/Execution не должны молча подменять потерянный handoff новой
торговой логикой.
