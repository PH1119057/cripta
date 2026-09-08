# АРХИТЕКТУРА ПРОЕКТА «КРИПТА»

**Документ:** `PROJECT_ARCHITECTURE_RU.md`
**Версия:** 2.2
**Дата:** 2026-09-08
**Статус:** глобальный архитектурный контракт

Верхний контракт: `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`.

Этот документ раскрывает верхний контракт и не может менять его смысл.

# 1. Два контура проекта

Проект разделён на два концептуальных контура.

## 1.1 Прикладной торговый контур

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

Верхних уровней пять.

`Entry` и `Exit` — специализированные части `Strategy`.

`Risk` — не отдельный верхний архитектурный слой.

## 1.2 Технический поддерживающий контур

Он делает возможной работу прикладного:

- получение market/account data;
- connectivity;
- private account sync;
- clock/reconnect/watchdog;
- storage/PostgreSQL;
- IDs/provenance;
- Position Supervisor;
- Analyst;
- UI/read models;
- service management;
- restart/reconciliation;
- operational safety;
- archival/diagnostics.

Технический контур может быть сложным программно, но архитектурно его сервисы не превращаются в новые торговые уровни.

# 2. MAYAK — наблюдение рынка

MAYAK наблюдает внешний мир независимо от торговли.

Он отвечает только:

> Что происходит на рынке?

Результат MAYAK — причинный `SharedMarketContext` и strategy-agnostic instrument/coin facts, достаточные для объективного описания как рынка в целом, так и конкретных инструментов.

# 3. Dispatcher — универсальная прикладная обстановка

Dispatcher не знает тип Entry и не интерпретирует рынок за конкретную Strategy. Он преобразует MAYAK в единый причинный read-model для прикладных потребителей.

Dispatcher имеет три класса показателей.

## 3.1 Общерыночный контекст

Публикует objective global market state: направление/ширину/синхронность, деньги, ликвидность, ликвидации, позиционирование, качество и свежесть.

## 3.2 Контекст и рейтинг конкретной монеты

Публикует strategy-agnostic карточку/`CoinMarketRating` конкретного инструмента. Рейтинг может учитывать фактический spot/derivatives money flow, его скорость/ускорение, OI, крупные сделки, ликвидность, ликвидации, relative strength, divergence, event risk и data quality.

Рейтинг не использует PnL или успешность наших Entry и не является командой LONG/SHORT.

## 3.3 Состояние торгового счёта

Из technical account-sync Dispatcher получает нормализованный фактический снимок торгового аккаунта подключённой площадки и публикует:

```text
total/equity
used
reserved
free
available_for_new_trading
freshness
source
```

Конкретные поля адаптируются к возможностям площадки.

Это только состояние/показатель.

Dispatcher:

- не резервирует средства сам;
- не выбирает размер;
- не создаёт order;
- не блокирует mutation напрямую.

# 4. Exchange-agnostic архитектура

Ни один верхний слой не должен быть привязан к конкретной бирже.

Технические adapters могут быть специфичными для конкретной площадки, но верхний контракт использует понятия:

```text
EXCHANGE
TRADING ACCOUNT
ACCOUNT STATE
ORDER
FILL
POSITION
AVAILABLE FUNDS
```

Current provider является implementation detail.

# 5. Strategy — единственный владелец торговой политики

Strategy канонически представлена пассивной immutable `StrategyCard`, а не работающим ботом.

В ней живут:

- условия подходящей среды;
- Entry policy со всеми числами, таймерами, touch/reset/lifecycle правилами;
- правила использования MAYAK-origin/Dispatcher objective context;
- капитал, allocation, размер позиции и leverage policy;
- stop, допустимая просадка, holding и initial protection;
- Exit policy.

Strategy version immutable после утверждения. Изменение любого торгового смысла или параметра = новая version/fingerprint.

`StrategyActivation` хранится отдельно от StrategyCard и определяет только, какие утверждённые Strategy включены владельцем. Одновременно могут быть включены несколько противоречащих Strategy.

Из конкретной Strategy version материализуются immutable `EntryPlan` и `ExitPlan`. Materialization является технической функцией уровня Strategy, но не мониторит рынок и не принимает торговых решений.

# 6. Entry — универсальное исполнение EntryPlan

Entry не выбирает Strategy. Он независимо обслуживает все активные `EntryPlan`, которые поступили от включённых Strategy.

Логическая модель:

```text
Active EntryPlan Registry
-> Entry Watch
-> StrategySignal (signal_id)
-> Entry Decision
-> optional ExecutionRequest
```

Технический Monitor/Scanner/market-data контур поставляет causal market facts. Сам рыночный факт не является торговым signal конкретной Strategy.

`StrategySignal` создаётся Entry Watch, когда причинное состояние рынка и обязательный consumed context удовлетворяют конкретному EntryPlan.

Entry может поддерживать универсальные операторы (`touch`, `break`, `retest`, `count`, `sequence`, `window`, `reset`, `AND/OR/NOT`), но вся торговая параметризация принадлежит Strategy. Candidate cooldown, включая исторические 30 минут V1, является отключаемой настройкой Strategy, а не свойством Entry.

Entry для конкретной attempt рассматривает:

- strategy-specific `signal_id`;
- точную Strategy binding и `entry_plan_fingerprint`;
- только разрешённый/обязательный объективный context;
- Dispatcher trading-capacity snapshot согласно Strategy policy;
- technical readiness.

Entry фиксирует точный outcome, например:

```text
ACCEPTED
STRATEGY_CONDITION_REJECTED
INSUFFICIENT_AVAILABLE_FUNDS
OPERATIONAL_SAFETY_BLOCKED
STALE_OR_UNKNOWN_REQUIRED_STATE
EXPIRED
CANCELLED
```

`EXCHANGE_REJECTED` / `NO_FILL` являются downstream execution outcomes.

Entry не сравнивает Strategy A и Strategy B и не создаёт `OTHER_STRATEGY_WON`. При нехватке общего капитала без отдельного allocator фактический недостаток средств/exchange rejection фиксируется честно для конкретной attempt.

# 7. Exit — выход внутри той же Strategy

После fill Entry ownership конкретной попытки заканчивается.

Exit работает по policy **той же Strategy**, которая открыла позицию.

Binding:

```text
bot_instance_id
strategy_id
strategy_version
strategy_config_fingerprint
signal_id
strategy_attempt_id
trade_id
position_id
```

Нельзя подменять Exit другой strategy policy.

# 8. Почему Risk не отдельный слой

`Risk` описывает разные свойства в разных владельцах и поэтому не должен быть отдельным верхним этажом.

| Смысл | Архитектурный владелец |
|---|---|
| Рынок нестабилен / каскад / ликвидность плохая | MAYAK как наблюдаемый факт |
| Для Strategy среда плоха | Strategy interpretation объективного Dispatcher context |
| Сколько денег свободно | Exchange truth -> technical account sync -> Dispatcher indicator |
| Сколько использовать | Strategy |
| Размер позиции / плечо / stop | Strategy |
| Допустимая просадка / удержание | Strategy |
| Войти или отказать | Entry в рамках Strategy |
| Сопровождать / закрыть | Exit в рамках Strategy |
| Нельзя безопасно отправить mutation | technical operational safety / Execution |
| Площадка реально не позволяет действие | Exchange |

Исторический класс/модуль `RiskEngine` может существовать программно. Его имя не определяет архитектурного ownership.

# 9. Execution

Execution — граница между прикладным решением и внешней площадкой.

Он получает уже сформированное решение и обеспечивает:

- fresh readiness;
- mutation;
- order IDs;
- fill truth;
- protection;
- reconciliation;
- durable handoff;
- safe retry/idempotency;
- operational fail-closed.

Execution не переоценивает Strategy.

# 10. Exchange

Exchange — внешняя торговая площадка.

Adapters технического контура нормализуют различия площадок без изменения верхней архитектуры.

# 11. Market facts / StrategySignal / Attempt / Card

Рыночные факты находятся upstream. Торговая история конкретной Strategy начинается при `STRATEGY_SIGNAL_DETECTED`.

Канонический `signal_id` — это strategy-specific `StrategySignal`, созданный Entry Watch из конкретного EntryPlan и причинных source market/context refs.

Один рыночный момент может породить несколько независимых `signal_id` разных Strategy.

Карточка создаётся до реальной сделки.

Целевая модель:

```text
causal market/context source refs
  -> signal_id                 # StrategySignal
       -> strategy + EntryPlan binding
       -> strategy_attempt_id
       -> Entry decision
       -> optional Execution
       -> optional position
       -> optional Exit
       -> continued observation
```

Rejected/no-fill/insufficient-funds attempts остаются аналитическими объектами.

# 12. Деньги как причинный контекст

На момент Entry необходимо сохранить snapshot, достаточный для ответа:

> Сделка не состоялась из-за рынка/Strategy или просто потому, что свободного капитала не было?

Минимально различать:

```text
total_account_value
used_capital
reserved_capital
available_capital
strategy_requested_allocation
snapshot_time
source_exchange/account
```

# 13. Несколько стратегий и ботов

Архитектура разрешает множество одновременно включённых Strategy, EntryPlan, bot instances и simultaneous positions.

Противоречащие Strategy допустимы. Entry не выбирает между ними и не вводит скрытый priority.

Текущая реализация одной Strategy — только текущий implementation stage.

Не вводить architecture cap без отдельного решения.

Не проектировать сейчас allocator/arbitration/priority между Strategy. Если такой механизм понадобится, он оформляется отдельно и не маскируется под Entry или Dispatcher.

# 14. Global market indicator

Dispatcher может публиковать общий objective indicator состояния среды.

Он не является командой и не является оценкой пригодности для конкретной Strategy.

Strategy решает, что этот indicator означает для её Entry и Exit.

# 15. CoinMarketRating и StrategyCoinFit

`CoinMarketRating` — объективная причинная характеристика текущего состояния монеты, формируемая MAYAK/Dispatcher без знания нашей торговой статистики.

`StrategyCoinFit` — статистика того, насколько конкретная `strategy_id/version` исторически работает на конкретном инструменте. Она принадлежит Analyst/research и может стать входом новой owner-approved Strategy version только через обычный research/shadow/live процесс.

Эти сущности запрещено смешивать. Плохой результат Strategy на монете не делает монету «плохой» внутри MAYAK.

Механизм выбора между несколькими одновременно доступными Entry attempts/монетами этим документом не задаётся; если он будет внедряться, это отдельный Strategy/portfolio contract.

# 16. Supervisor и Analyst

Position Supervisor и Analyst относятся к поддерживающему наблюдательно-аналитическому контуру.

Они не являются новыми top-level trading layers.

# 17. PostgreSQL

PostgreSQL — persisted truth проекта, но не торговый слой.

Он хранит историю и причинные связи, достаточные для восстановления signal, attempt, strategy binding, account/trading-capacity snapshot, Dispatcher context, Entry decision, Execution, position, Exit, economics и post-decision observation.

# 18. Operational safety

Техническая безопасность имеет право fail-closed остановить небезопасную mutation.

Она не должна маскироваться под рыночный фильтр или Strategy.

# 19. Изменения

Любая попытка вернуть top-level Risk, сделать Dispatcher исполнителем/запускателем Strategy, сделать technical service владельцем Strategy, дать Entry право выбирать/ранжировать Strategy, вернуть strategy-specific constants внутрь universal Entry, привязать архитектуру к одной бирже или смешать Entry/Exit разных strategy bindings является архитектурно чувствительной.

Специализированный контракт Strategy/Entry: `STRATEGY_ENTRY_ARCHITECTURE_RU.md`.
