# Текущее устройство и архитектурные границы проекта Cripta

**Документ:** `CURRENT_PROJECT_MAP_RU.md`  
**Версия документа:** 2.1
**Дата подготовки:** 2026-08-31  
**Базовый проверенный GitHub commit:** `b9821c3ae427a41efe73254f7f9f238cb8c3293b`  
**Актуально для состояния кода:** `37fcd7773c2e5c4bdf345c7f8051f8c7aa9097a5`  
**Статус:** краткая каноническая карта проекта + обязательные архитектурные инварианты.

## Изменение карты 2.1: advisory-контекст не является торговым gate

Маяк и Диспетчер только сохраняют причинный контекст с торговым эффектом
`NONE/CONTEXT_ONLY` и не могут разрешать, запрещать, создавать или закрывать
сделки. Решение Entry принадлежит утверждённой стратегии. Технические fail-closed
проверки биржи образуют отдельный operational-safety контур.

## Изменение карты: Shared Market Context V1.2

- `mayak_v2.shared_market_contexts` — неизменяемые объективные снимки общего
  рынка без торговых команд.
- `monitoring.entry_geometry_handoffs` — неизменяемая геометрия Entry в момент
  сигнала.
- `runtime.entry_geometry_bindings` — точная связь сигнала и команды входа.
- `runtime.position_ownership` — владелец позиции, стабильные `trade_id` и
  `position_id`, стратегия, bot instance и exchange execution IDs.
- `analytics.shared_market_context_consumption` и
  `analytics.position_lifecycle_identity` — единая семантика для интерфейса,
  Аналитика и компактного экспорта.

Общий рыночный контекст не закрывает позиции. Каждая стратегия интерпретирует
его собственным профилем. Структурный ранний выход остаётся выключенным, пока
для текущей production-версии не утверждено отдельное причинное правило; старые
P45.1/P52 сами по себе таким разрешением не являются.

> При установке этой версии в рабочий репозиторий поле «Актуально для commit»
> должно быть заменено на фактический commit, в котором файл установлен.
> Если код ушёл вперёд относительно базового commit, фактическое состояние
> production имеет приоритет и карта должна быть обновлена вместе с изменением.

Этот документ нужен для быстрого ответа на пять вопросов:

1. что является действующей системой;
2. где находится канонический код и живая истина;
3. кто за что отвечает;
4. какие связи между слоями разрешены;
5. какие правила обязаны соблюдать любые будущие стратегии и любое количество одновременно запущенных торговых ботов.

Подробные специализированные контракты имеют приоритет согласно
`docs/DOCUMENT_AUTHORITY_RU.md`, `AGENTS.md` и
`docs/PROJECT_ARCHITECTURE_RU.md`.

---

# 1. Главная идея проекта

Cripta — не «один большой торговый бот».

Это торговая платформа, в которой независимые общие сервисы обслуживают
множество торговых стратегий и множество одновременно работающих экземпляров
торговых ботов.

Нужно строго различать:

```text
СТРАТЕГИЯ
= алгоритм / правила торговли

БОТ / BOT INSTANCE
= конкретный живой экземпляр исполнения стратегии

ОБЩИЕ СЕРВИСЫ ПЛАТФОРМЫ
= Маяк, Диспетчер, Global Safety, Execution, PostgreSQL,
  Supervisor-инфраструктура, Analyst, Dashboard и operational tooling
```

Одна стратегия может быть запущена:

- на одном символе;
- на нескольких символах;
- в нескольких отдельных bot instances;
- на разных аккаунтах/субаккаунтах, если это разрешено конфигурацией.

Одновременно в системе могут работать:

```text
2 стратегии и 100 ботов
или
10 стратегий и 10 ботов
или
1 стратегия и 1 бот.
```

Общие сервисы не должны дублироваться внутри каждой стратегии.

---

# 2. Каноническая архитектурная цепочка

Основной поток внешнего рыночного контекста:

```text
ВНЕШНИЙ РЫНОК
      ↓
    МАЯК
      ↓
dispatcher_handoff
      ↓
  ДИСПЕТЧЕР
      ↓
оценка среды конкретной стратегии
      ↓
ТОРГОВАЯ СТРАТЕГИЯ / BOT INSTANCE
      ↓
     ENTRY
      ↓
  EXECUTION
      ↓
   BYBIT KZ
```

После подтверждённого fill:

```text
BYBIT POSITION
      ↓
POSITION SUPERVISOR
      +
DISPATCHER HOLD
      ↓
STRATEGY EXIT / RISK
      ↓
EXECUTION
      ↓
BYBIT KZ
```

Вокруг всей системы:

```text
             PostgreSQL
       canonical data truth
               ↑
               │
Маяк ─ Диспетчер ─ Strategy ─ Entry ─ Execution ─ Supervisor ─ Exit/Risk
               │
               ↓
             Analyst
        read-only analysis
```

Над любой конкретной торговой стратегией существует отдельный
общесистемный контур безопасности:

```text
Маяк + состояние платформы + свежая биржевая истина
                    ↓
             GLOBAL SAFETY
                    ↓
        fleet-wide safety state
                    ↓
      все живые bot instances
```

`GLOBAL SAFETY` — архитектурное понятие общей защиты платформы.
Оно не должно маскироваться под Entry или под логику одной стратегии.

---

# 3. Иерархия права решения

При конфликте решений действует следующий приоритет:

```text
1. Биржевая серверная защита и emergency execution
2. Global Safety / общесистемная аварийная политика
3. Account / portfolio Risk
4. Position Supervisor + Exit/Risk конкретной позиции
5. Торговая стратегия
6. Entry
```

Нижний уровень может быть более консервативным, но не имеет права отменить
жёсткое решение более высокого уровня.

Например:

```text
Global Safety = BLOCK_NEW_ENTRIES
```

означает, что никакая стратегия не может открыть новую позицию, даже если её
собственный Entry идеален.

Если:

```text
Global Safety = EMERGENCY_CLOSE
```

то стратегия не имеет права сказать «я предпочитаю ещё потерпеть».

---

# 4. Маяк

## 4.1 Роль

Маяк — независимый наблюдатель внешнего рынка.

Он отвечает:

> **Что объективно происходит на рынке?**

Он может наблюдать:

- цену;
- исполненные сделки;
- спот;
- срочный рынок;
- OI;
- funding;
- стакан;
- ликвидации;
- ширину рынка;
- синхронность;
- BTC/ETH;
- несколько централизованных бирж;
- DEX;
- blockchain/on-chain контекст;
- события и реакцию рынка;
- качество и свежесть источников.

## 4.2 Маяк не торгует

Маяк не знает и не должен знать:

- наши позиции;
- наш PnL;
- результат конкретного Entry;
- текущий stop конкретной позиции;
- предпочтения конкретного bot instance.

Маяк не имеет права:

- открыть позицию;
- закрыть позицию;
- изменить stop;
- изменить размер;
- изменить плечо;
- изменить торговую стратегию.

Он публикует причинное состояние внешнего рынка.

## 4.3 «Пожар» и «потоп»

Маяк может зафиксировать объективное состояние, похожее на:

- массовый directional breakdown;
- широкую синхронную распродажу;
- ликвидационный каскад;
- исчезновение ликвидности;
- резкий системный разрыв;
- одновременно ухудшающиеся деньги, OI, стакан и breadth;
- иные подтверждённые market-wide emergency признаки.

Но сам Маяк не превращает это в ордер.

Его задача:

```text
увидеть пожар
```

а не:

```text
самому нажать CLOSE ALL.
```

---

# 5. Диспетчер стратегий

Диспетчер получает снимок Маяка и отвечает:

> **Насколько текущая рыночная среда подходит конкретному типу торговли?**

Один и тот же рынок может быть одновременно:

- плохим для long mean-reversion;
- хорошим для short breakdown;
- плохим для спокойной trend-following стратегии;
- приемлемым для другой высоковолатильной стратегии.

Поэтому Диспетчер оценивает не «хороший рынок вообще», а:

```text
Mayak snapshot
+
strategy environment profile
=
strategy suitability assessment
```

Каждая стратегия имеет собственные версии:

```text
ENTRY_ENVIRONMENT
HOLD_ENVIRONMENT
```

Оценка ENTRY не должна автоматически заменять HOLD.

Диспетчер:

- не отправляет ордера;
- не меняет stop;
- не закрывает позицию;
- не меняет Mayak;
- не использует PnL нашей стратегии как рыночный признак.

---

# 6. Торговая стратегия

Стратегия — это алгоритм.

Она отвечает:

> **Что мне делать в этой конкретной рыночной ситуации?**

Стратегия содержит:

- собственную геометрию Entry;
- causal признаки;
- собственный профиль среды;
- собственные правила сопровождения;
- собственный Exit;
- собственные ограничения Risk в пределах общесистемных лимитов.

Стратегия может предпочитать собственный выход, но не имеет права игнорировать
общесистемную аварийную команду.

---

# 7. Bot instance — конкретный живой торговый процесс

Нужно отдельно фиксировать понятие `bot_instance`.

Bot instance — это конкретный runtime-потребитель торговой стратегии.

Минимальная идентичность:

```text
bot_instance_id
strategy_id
strategy_version
policy_version
account/subaccount scope
symbol scope
runtime settings version
started_at
loaded_commit
```

Каждый bot instance:

- использует общие сервисы платформы;
- не создаёт собственный скрытый Маяк;
- не создаёт собственную скрытую версию Диспетчера;
- не ведёт отдельную «истину» о бирже;
- подчиняется Global Safety;
- после fill передаёт позицию общему контракту Execution/Supervisor/Exit/Risk;
- оставляет причинный и экономический след в PostgreSQL.

Количество bot instances архитектурно не ограничивается конкретным числом.

---

# 8. Entry

Entry отвечает только за:

- обнаружение торговой возможности;
- проверку причинных условий входа;
- применение разрешённого strategy context;
- создание запроса на вход.

После подтверждённого fill:

```text
Entry ownership = END
```

Entry не должен:

- сопровождать реальную позицию;
- повторно придумывать Exit;
- перемещать stop по собственной памяти;
- считать локальное состояние важнее свежего exchange state.

---

# 9. Execution

Execution отвечает за фактическое исполнение решения.

Биржа является живым источником истины по:

- actual avg fill;
- actual qty;
- exchange order ID;
- client order ID;
- position;
- active orders;
- stops/protection;
- leverage;
- balance;
- margin mode;
- position mode;
- fills.

После fill создаётся durable handoff:

```text
position_id
bot_instance_id
strategy_id/version
symbol
side
actual_avg_fill
actual_qty
fill_time
initial_stop
exchange_order_ids
client_order_ids
protection_ids
```

Execution не должен зависеть от scanner/research для:

- server-side stop;
- reduce-only close;
- cancel pending;
- reconciliation;
- emergency close.

---

# 10. Position Supervisor

Position Supervisor работает только после появления фактической позиции.

Он отвечает:

> **Что происходит именно с этой реальной позицией?**

Он может использовать:

- actual fill;
- локальную структуру;
- цену;
- поток;
- OI;
- стакан;
- внешний рыночный контекст;
- Dispatcher HOLD.

Supervisor не является Entry.

После restart/reconnect Supervisor обязан восстановить позицию по Bybit,
даже если локальная Entry-история отсутствует.

---

# 11. Exit: два разных уровня

Нельзя смешивать:

```text
A. strategy-specific Exit
B. global emergency Exit
```

## 11.1 Strategy-specific Exit

Это обычное сопровождение позиции:

- достижение цели;
- giveback;
- structural break конкретной позиции;
- ухудшение HOLD-среды;
- trailing;
- economic break-even;
- частичная фиксация;
- иные правила конкретной стратегии.

Они могут отличаться между стратегиями.

## 11.2 Global emergency Exit

Это общесистемный механизм для событий типа:

> **«Начался пожар/потоп — общая предпосылка торговли сломана».**

Он должен быть понятен любой стратегии и любому bot instance.

Примеры класса события:

- подтверждённый market-wide crash;
- массовый liquidity withdrawal;
- системная ликвидационная цепочка;
- одновременный breakdown большинства наблюдаемого рынка;
- авария биржи/приватного состояния, при которой оставаться в позиции опаснее,
  чем исполнить заранее разрешённое emergency действие;
- другое заранее версионированное общесистемное состояние.

Важно:

```text
одна красная свеча != GLOBAL EMERGENCY
одна ликвидация != GLOBAL EMERGENCY
один плохой PnL != GLOBAL EMERGENCY
```

Нужен отдельный причинный контракт с версией и доказуемым состоянием.

---

# 12. Global Safety / Fleet Safety

Это общий слой, которого не должно быть внутри M3 или другой отдельной стратегии.

Он отвечает:

> **Разрешено ли платформе как целому продолжать нормальную торговлю?**

Его scope:

```text
все bot instances
все стратегии
все символы/аккаунты в применимом scope
```

Рекомендуемый абстрактный словарь состояния:

```text
NORMAL
CAUTION
BLOCK_NEW_ENTRIES
EMERGENCY_REDUCE_ONLY
EMERGENCY_CLOSE
ERROR / UNKNOWN
```

Точное live-поведение каждой команды должно быть отдельным версионированным
контрактом и не должно определяться этой картой «по догадке».

Минимальные правила:

### `NORMAL`
Стратегии работают по собственным правилам.

### `CAUTION`
Это контекст, но не обязательный ордер.

### `BLOCK_NEW_ENTRIES`
Новые позиции запрещены глобально.
Уже существующие позиции продолжают сопровождаться по действующим контрактам.

### `EMERGENCY_REDUCE_ONLY`
Нельзя увеличивать риск.
Разрешены только действия, уменьшающие экспозицию.

### `EMERGENCY_CLOSE`
Все позиции применимого scope закрываются через общий reduce-only emergency path.

### `ERROR / UNKNOWN`
Не открывать новый риск.
Не трактовать отсутствие данных как безопасный рынок.

---

# 13. Откуда Global Safety получает данные

Global Safety не должен становиться вторым Маяком.

Для рыночного emergency он использует только версионированные результаты
общего наблюдения, прежде всего causal Mayak context.

Для operational emergency он может использовать:

- свежесть Bybit private state;
- clock drift;
- reconciliation;
- состояние server-side protection;
- connectivity;
- trading gate;
- exchange/system status.

Нельзя смешивать рыночный аварийный режим и техническую поломку в одно
неразличимое поле.

Минимально различать:

```text
MARKET_SAFETY_STATE
OPERATIONAL_SAFETY_STATE
```

И только затем формировать итоговое:

```text
FLEET_ACTION
```

---

# 14. Почему Global Safety не должен жить в Analyst

Analyst — read-only слой.

Он отвечает:

> **Что произошло, почему система приняла решение и было ли оно экономически полезно?**

Analyst не должен быть скрытым live controller.

Если Аналитик обнаружил закономерность постфактум:

```text
statistics
→ research
→ new version
→ shadow/equivalence/live decision
```

а не:

```text
Analyst сам ночью переписал поведение бота.
```

Если в будущем нужен новый online слой, способный влиять на торговлю, он должен
иметь отдельное имя, контракт, версию, ownership и разрешение на live influence.

---

# 15. Analyst

Analyst связывает причинную историю системы:

```text
Mayak snapshot
Dispatcher assessment
Strategy decision
Entry
order request
order response
fill
position
Supervisor states
HOLD assessments
Exit decision
actual exit
fees
funding
slippage
net PnL
counterfactual outcome
```

Он должен различать:

```text
OBSERVED_CONTEXT
CONSUMED_CONTEXT
```

`OBSERVED_CONTEXT`:
контекст существовал в тот момент.

`CONSUMED_CONTEXT`:
конкретный bot/strategy действительно его прочитал и использовал в решении.

Analyst не имеет права изменять live trading settings автоматически.

---

# 16. PostgreSQL — каноническая operational/analytical truth

Для данных системы действует принцип:

> **PostgreSQL — единственная каноническая operational и analytical truth.**

Не являются равноправной истиной:

- UI;
- systemd journal;
- runtime JSON;
- SQLite;
- CSV;
- ZIP archive;
- ChatGPT export.

Они могут быть:

- транспортом;
- диагностикой;
- backup/recovery;
- представлением;
- временным кэшем.

Но причинная история реальной торговли должна адресно восстанавливаться
из PostgreSQL.

Целевой вопрос:

> Можно ли по одному `position_id` / `trade_id` восстановить всю сделку
> от рыночного контекста до net PnL без обращения к SQLite, journalctl и
> непубличным runtime-файлам?

Для production-ready статистики ответ должен быть:

```text
YES
```

---

# 17. Основные PostgreSQL схемы

По проверенному состоянию проекта используются:

| Схема | Назначение |
|---|---|
| `mayak_v2` | снимки, события, минутные данные, наблюдения, ликвидации |
| `strategy_dispatcher` | запуски и assessments профилей |
| `monitoring` | сигналы и независимое наблюдение |
| `runtime` | settings, execution gate, решения Entry, команды, fills, позиции, orders, wallet, reconciliation |
| `supervisor` | snapshots/transitions Supervisor и HOLD context |
| `research_context` | причинные связи Analyst |
| `control`, `safety` | управление разрешениями и safety state |

При развитии проекта рекомендуется иметь отдельный устойчивый read model:

```text
analytics.*
```

например:

```text
analytics.current_system_state
analytics.entry_funnel
analytics.trade_lifecycle
analytics.trade_economics
analytics.trade_entry_context
analytics.trade_hold_timeline
analytics.trade_supervisor_timeline
analytics.closed_trade_card
analytics.data_completeness
```

Это могут быть SQL views/materialized views над production-таблицами.
Они не создают вторую истину.

---

# 18. Live transport и PostgreSQL — не одно и то же

PostgreSQL является канонической сохранённой истиной, но это не означает,
что каждый ultra-low-latency live decision обязан ждать медленный polling БД.

Допустимо:

```text
in-memory causal state
API
IPC
event channel
```

если одновременно выполняется контракт:

```text
то, что реально было использовано для решения,
имеет идентификатор/версию/время
и надёжно попадает в PostgreSQL.
```

Нельзя иметь скрытое live-состояние, которое невозможно потом восстановить.

---

# 19. Компактный экспорт для ChatGPT и внешнего анализа

Для регулярного анализа не нужен полный production dump каждые 10–15 минут.

Предпочтительный поток:

```text
PostgreSQL
   ↓
consistent read-only analytics export
   ↓
compact files / connected storage
   ↓
ChatGPT
```

Минимальный manifest такого экспорта:

```text
cutoff_time_utc
server_head
loaded_versions
schema_version
row_counts
latest_event_time_by_table
data_completeness
export_status
```

Экспорт — представление PostgreSQL, а не новая база.

Полный PostgreSQL dump остаётся recovery/Archive V2 артефактом.

---

# 20. Fail-closed

Общее правило:

```text
UNKNOWN != ZERO
NO_DATA != NEUTRAL
STALE != SAFE
```

Для нового риска при неизвестном обязательном состоянии:

```text
BLOCKED / WAITING / WARMUP / ERROR
```

После restart/reconnect нельзя считать восстановленными:

- Mayak context;
- Dispatcher context;
- private account state;
- positions;
- stops;
- orders;
- OI/orderbook causal windows;
- Supervisor state;

пока соответствующий слой реально не восстановлен или не прогрет.

---

# 21. Биржа — живая истина

Свежий Bybit state имеет приоритет по:

- positions;
- avg fill;
- qty;
- orders;
- stops/protection;
- fills;
- leverage;
- wallet/balance;
- margin mode;
- position mode.

UI selection — только намерение до подтверждения биржей.

Нельзя открывать вторую позицию потому, что локальная память ошибочно считает
состояние flat.

---

# 22. Risk

Risk отвечает:

> **Сколько системе разрешено потерять?**

Не смешивать:

- движение цены %;
- R;
- equity %;
- notional;
- margin;
- leverage.

Базовый денежный риск:

```text
position size × stop distance
+ fees
+ slippage
+ funding/other applicable costs
```

Плечо выбирается после допустимого риска и notional.
Оно не имеет права само увеличивать разрешённую потерю.

---

# 23. Общая защита должна быть strategy-agnostic

Каждая стратегия может иметь:

- свой Entry;
- свой normal Exit;
- свой HOLD;
- свою геометрию;
- свои цели.

Но следующие вещи должны иметь единый платформенный контракт:

- clock/connectivity safety;
- exchange reconciliation;
- server-side protection;
- cancel-pending;
- reduce-only close;
- emergency kill;
- global block new entries;
- global emergency close;
- account/portfolio exposure limits;
- audit trail;
- PostgreSQL persistence;
- restart recovery.

Новая стратегия обязана встраиваться в эти контракты,
а не создавать свои несовместимые версии аварийного исполнения.

---

# 24. Portfolio / fleet state

При множестве одновременно работающих bot instances нужно видеть не только
одиночную позицию, но и общую экспозицию.

Минимально:

```text
active bots
active strategies
active symbols
open positions
long/short concentration
same-direction correlated exposure
margin usage
account exposure
portfolio risk
global safety state
```

Signal replay отдельной сделки не является portfolio backtest.

---

# 25. Состояние текущей M3 политики

В проверенном GitHub состоянии карта указывает:

```text
policy = m3_full_live_v1
profile_version = 1.0.0-owner-live
```

Исполняемые профили:

```text
M3_V1_LONG_ENTRY
M3_V1_SHORT_ENTRY
M3_V1_LONG_HOLD
M3_V1_SHORT_HOLD
```

Для M3 новый Entry должен использовать причинную Dispatcher assessment,
существовавшую не позже сигнала.

После fill Entry перестаёт владеть позицией.

Точный действующий контракт раннего выхода должен находиться в
специализированном live contract и не должен угадываться по этой карте.

Если текущая рабочая реализация после commit, указанного в заголовке,
изменила эти правила — этот раздел должен быть обновлён до фактического
loaded production state.

---

# 26. Текущий статус компонентов: «есть» не равно «архитектурно требуется»

Нельзя выдавать желаемую архитектуру за уже загруженный production.

Поэтому карта должна различать:

```text
IMPLEMENTED
LOADED/RUNNING
ARCHITECTURAL_REQUIREMENT
NOT_PROVEN
RESEARCH_ONLY
DEPRECATED
```

На базовом проверенном commit:

| Слой | Статус карты |
|---|---|
| Mayak V2 | реализован как отдельный monitoring layer |
| Strategy Dispatcher | реализован отдельным слоем |
| M3 Entry consumer/private runtime | реализован |
| Position Supervisor | реализован |
| Analyst causal correlator | реализован как read-only causal/statistical foundation; полнота trade-level diagnosis должна подтверждаться фактической версией |
| PostgreSQL | основной production persistence; полнота «единственной истины» должна проверяться аудитом readers/writers |
| Global Safety / Fleet Safety | обязательный архитектурный общий слой; наличие полноценной отдельной live реализации нельзя заявлять без field proof |
| Legacy SQLite | не должен быть production truth; допустим только как явно маркированный historical/read-only источник до decommission |

---

# 27. Основные каталоги проекта

По проверенному состоянию:

| Путь | Назначение |
|---|---|
| `src/bybit_workbench/` | основная библиотека |
| `production/src/bybit_workbench/strategy_dispatcher/` | канонический Dispatcher |
| `operations/monitoring/` | Mayak, Entry scanner, Supervisor, causal correlator |
| `operations/connectivity/` | private Bybit runtime, reconciliation, execution |
| `operations/dashboard/` | Dashboard, API, Archive V2 |
| `operations/devtools/` | state/gate/diff/patch/rollback/soak/field-proof |
| `operations/systemd/` | канонические systemd units |
| `config/strategy_dispatcher/` | профили Dispatcher и schema |
| `tests/` | unit/contract/architecture/runtime tests |
| `research/`, `research_tools/`, `scripts/` | исследования и operational scripts |
| `docs/` | активные архитектурные контракты и research protocols |

Старые `Pxx`, `EO*`, `SE*`, `ENTRY_*` пакеты являются provenance/history,
а не автоматически действующим production runtime.

---

# 28. Где находится фактическая система

Разделять:

```text
SOURCE
RUNTIME
DATA
REPORTING
```

Канонический server source:

```text
/srv/cripta/source_checkout
```

Installed production runtime:

```text
/srv/cripta/production
/srv/cripta/monitoring
/srv/cripta/connectivity
/srv/cripta/dashboard
```

Большие datasets:

```text
/data/cripta
```

Operational artifacts:

```text
/srv/cripta-share/operations
/srv/cripta-share/reports
```

GitHub:

- код;
- тесты;
- конфигурация;
- docs;
- migrations/schema;
- systemd units;
- безопасные fixtures;
- versioned architecture.

GitHub не является live database.

---

# 29. Что не должно попадать в публичный GitHub

Не хранить:

- API keys;
- passwords;
- private keys;
- tokens;
- `.env` с secrets;
- полный production PostgreSQL dump;
- реальные приватные execution identifiers в открытой аналитике;
- системные журналы с чувствительными данными;
- большие рыночные datasets;
- временные runtime files.

Полный dump может находиться в закрытом backup/Archive V2 контуре владельца.

---

# 30. Обязательная причинная идентификация

Любое решение должно быть связуемо через стабильные ID.

Минимальная цепочка:

```text
mayak_snapshot_id
    ↓
dispatcher_assessment_id
    ↓
signal_id
    ↓
strategy_decision_id
    ↓
order_request_id
    ↓
exchange_order_id / exec_id
    ↓
position_id
    ↓
supervisor_state_id / hold_assessment_id
    ↓
exit_decision_id
    ↓
close_exec_id
    ↓
trade_result_id
    ↓
analyst_diagnosis_id
```

Для global safety дополнительно:

```text
global_safety_event_id
fleet_action_id
```

Любой bot instance, на который это повлияло, должен ссылаться на тот же
`global_safety_event_id`.

---

# 31. Аудит общесистемного аварийного события

Для каждого `GLOBAL SAFETY` события хранить минимум:

```text
event_id
created_at_utc
market_state_time_utc
scope
source_snapshot_ids
safety_policy_version
market_safety_state
operational_safety_state
fleet_action
reason_codes
data_quality
freshness
affected_bot_instances
affected_positions
orders_generated
fills
fees/slippage
recovery_time
```

После события Analyst должен уметь посчитать:

```text
saved losses
lost good trades
destroyed recoveries
extra fees
slippage
net economic effect
```

Нельзя объявлять общую защиту полезной только потому, что она закрыла несколько
сделок до hard stop.

---

# 32. Recoverability после restart

После restart:

```text
local memory != truth
```

Система обязана:

1. получить fresh Bybit state;
2. восстановить позиции;
3. восстановить orders/stops/protection IDs;
4. восстановить bot-instance ownership;
5. восстановить strategy/policy versions;
6. восстановить или заново прогреть causal market context;
7. восстановить Supervisor;
8. восстановить Global Safety state;
9. только после этого разрешать новый риск.

Реальная позиция должна быть защищена даже без старой Entry history.

---

# 33. Research и Production

Новая логика проходит:

```text
RESEARCH
→ SHADOW
→ LIVE EQUIVALENCE
→ MICRO_LIVE
→ LIVE
```

Исключения возможны только по отдельному явному решению владельца,
но причинный/audit след и fail-closed границы остаются обязательными.

Запрещено переносить в production:

- future bars;
- look-ahead;
- future-derived artifacts;
- partially inspected holdout logic;
- research datasets как hidden runtime dependency.

---

# 34. Что должна уметь новая стратегия перед подключением

Новая стратегия должна определить:

```text
strategy_id/version
ENTRY logic
ENTRY_ENVIRONMENT profile
HOLD_ENVIRONMENT profile
normal Exit
Risk contract
required data
bot-instance config
```

И обязана использовать общие платформенные интерфейсы:

```text
Mayak context
Dispatcher assessment
Global Safety
Execution
Bybit reconciliation
PostgreSQL audit
Supervisor
common emergency close
Analyst trace
```

Если стратегия требует новый рыночный признак, это не означает, что она может
незаметно встроить новый общий Маяк внутрь себя.

---

# 35. Чего не должен делать торговый бот

Bot instance не должен:

- считать собственный PnL состоянием рынка;
- подменять Mayak;
- подменять Dispatcher;
- самовольно отменять Global Safety;
- хранить единственную копию позиции локально;
- открывать позицию при неизвестном private exchange state;
- использовать будущий контекст;
- повторно выводить actual fill из theoretical Entry;
- создавать собственный несовместимый emergency execution path.

---

# 36. Главный смысл разделения «бот» и «стратегия»

Стратегия — это правила.

Бот — это работающий исполнитель этих правил в общей инфраструктуре.

Поэтому правильная модель:

```text
                  ОБЩИЕ СЕРВИСЫ
     Mayak / Dispatcher / Global Safety / PostgreSQL
            Execution / Analyst / Operations
                         │
          ┌──────────────┼──────────────┐
          ↓              ↓              ↓
      BOT #1          BOT #2         BOT #100
      M3 LONG         M3 SHORT       Strategy X
          │              │              │
          └───── общая биржевая и safety инфраструктура ─────┘
```

Это позволяет добавлять 3, 5 или 10 стратегий без размножения скрытой системной
логики по каждому боту.

---

# 37. Короткая формула проекта

```text
Маяк
= что происходит на рынке

Диспетчер
= подходит ли этот рынок конкретной стратегии

Стратегия
= есть ли торговая возможность и что предпочитает делать стратегия

Bot instance
= конкретно исполняет эту стратегию

Supervisor
= что происходит с конкретной реальной позицией

Global Safety
= разрешено ли всей системе продолжать нормальную торговлю

Execution
= что реально отправлено и исполнено на бирже

Bybit
= живая истина по фактической позиции и ордерам

PostgreSQL
= каноническая сохранённая operational/analytical truth

Analyst
= почему всё произошло и была ли логика экономически полезна
```

---

# 38. Контроль изменений карты

`CURRENT_PROJECT_MAP_RU.md` должен меняться, если меняется хотя бы одно из:

- границы ownership;
- production entrypoint;
- source/runtime paths;
- активная trading policy;
- PostgreSQL schema ownership;
- Global Safety contract;
- Dispatcher/Mayak interfaces;
- Analyst role;
- restart/reconciliation contract;
- список общих обязательных платформенных сервисов.

При изменении:

1. обновить версию документа;
2. обновить фактический `Актуально для commit`;
3. commit/push;
4. кратко перечислить изменившиеся разделы;
5. сообщить владельцу, что карта изменилась.

Если ChatGPT имеет прямой доступ к GitHub, достаточно сообщить:

> **«Есть новая версия `CURRENT_PROJECT_MAP_RU.md`; читай текущую из GitHub, не из памяти».**

Если используется отдельная статическая Project Source копия, её нужно обновить
отдельно.

---

# 39. Неподменяемые архитектурные принципы

1. **Mayak видит рынок, но не торгует.**
2. **Dispatcher оценивает пригодность среды, но не отправляет ордера.**
3. **Strategy определяет собственную торговую логику.**
4. **Bot instance — исполнитель стратегии, а не отдельная вселенная.**
5. **После fill Entry перестаёт владеть позицией.**
6. **Bybit — живая истина по реальным позициям и исполнениям.**
7. **PostgreSQL — каноническая сохранённая operational/analytical truth.**
8. **Analyst read-only и не является скрытым live controller.**
9. **Global Safety обязателен как strategy-agnostic общий аварийный контур.**
10. **Ни одна стратегия не может отменить fleet-wide hard safety action.**
11. **Common emergency close не зависит от scanner/research.**
12. **UNKNOWN/STALE/NO_DATA не превращаются в SAFE.**
13. **Любое live-влияние имеет version/provenance/audit trail.**
14. **Нельзя улучшать систему только по saved stops; считать надо net economics.**
15. **Добавление новой стратегии не должно требовать переписывания Маяка, Execution или общего emergency path.**
