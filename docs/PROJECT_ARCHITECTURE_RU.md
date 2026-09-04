# АРХИТЕКТУРА ПРОЕКТА «КРИПТА»

**Документ:** `PROJECT_ARCHITECTURE_RU.md`
**Версия:** 2.0
**Дата:** 2026-09-05
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

Результат MAYAK — общий причинный `SharedMarketContext`.

# 3. Dispatcher — общая прикладная обстановка

Dispatcher имеет две функции-показателя.

## 3.1 Пригодность рыночной среды

Он сопоставляет MAYAK с профилями Strategy.

Один рынок может дать разные assessments разным Strategy.

## 3.2 Состояние торгового счёта

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

# 5. Strategy — владелец торговой политики

Strategy определяет правила конкретного способа торговли.

В ней живут:

- условия подходящей среды;
- Entry policy;
- капитал, который Strategy хочет использовать;
- размер позиции;
- leverage policy;
- stop;
- допустимая просадка;
- holding rules;
- Exit policy;
- initial protection.

Strategy version immutable после утверждения.

# 6. Entry — вход внутри Strategy

Monitor/Scanner обнаруживает возможность и создаёт signal.

Entry получает возможность на рассмотрение, но не обязан входить.

Entry рассматривает:

- causal signal;
- утверждённые Strategy rules;
- применимый Dispatcher market assessment;
- Dispatcher trading-capacity snapshot;
- technical readiness.

Entry фиксирует точный outcome, например:

```text
ACCEPTED
STRATEGY_CONDITION_REJECTED
DISPATCHER_MARKET_INCOMPATIBLE
INSUFFICIENT_AVAILABLE_FUNDS
OPERATIONAL_SAFETY_BLOCKED
EXCHANGE_REJECTED
NO_FILL
```

Если будущий механизм допускает несколько Strategy, один signal может породить несколько strategy attempts.

Механизм автоматического выбора Strategy этим документом не определяется.

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
| Для Strategy среда плоха | Dispatcher assessment + Strategy interpretation |
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

# 11. Signal / Attempt / Card

`SIGNAL_DETECTED` — начало истории.

Карточка создаётся до реальной сделки.

Целевая модель:

```text
signal_id
  -> 1..N strategy_attempt_id
       -> strategy binding
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

Архитектура разрешает множество Strategy, bot instances и simultaneous positions.

Сегодняшняя реализация одной Strategy — только текущий этап.

Не вводить architecture cap без отдельного решения.

Не проектировать сейчас allocator/arbitration/priority между Strategy.

# 14. Global market indicator

Dispatcher может публиковать общий indicator состояния среды.

Он не является командой.

Strategy решает, что этот indicator означает для её Entry и Exit.

# 15. Supervisor и Analyst

Position Supervisor и Analyst относятся к поддерживающему наблюдательно-аналитическому контуру.

Они не являются новыми top-level trading layers.

# 16. PostgreSQL

PostgreSQL — persisted truth проекта, но не торговый слой.

Он хранит историю и причинные связи, достаточные для восстановления signal, attempt, strategy binding, account/trading-capacity snapshot, Dispatcher context, Entry decision, Execution, position, Exit, economics и post-decision observation.

# 17. Operational safety

Техническая безопасность имеет право fail-closed остановить небезопасную mutation.

Она не должна маскироваться под рыночный фильтр или Strategy.

# 18. Изменения

Любая попытка вернуть top-level Risk, сделать Dispatcher исполнителем, сделать technical service владельцем Strategy, привязать архитектуру к одной бирже или смешать Entry/Exit разных strategy bindings является архитектурно чувствительной.
