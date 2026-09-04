# НЕПРЕРЫВНЫЙ ЖИЗНЕННЫЙ ЦИКЛ СИГНАЛА

**Документ:** `SIGNAL_LIFECYCLE_CONTRACT_RU.md`
**Версия:** 1.1
**Дата:** 2026-09-05
**Статус:** канонический архитектурный контракт

## 1. Корневая сущность

Корневая сущность — причинный торговый signal.

Карточка и машинная история создаются в момент `SIGNAL_DETECTED`, а не после fill.

`signal_id` не теряется независимо от исхода.

## 2. Strategy attempt

Архитектура допускает:

```text
signal_id
  -> strategy_attempt_id
```

Каждый attempt связан с `strategy_id`, `strategy_version`, `strategy_config_fingerprint` и `bot_instance_id`, где применимо.

Сегодня одна Strategy может давать один attempt. Будущая мультистратегийность не требует менять lifecycle.

## 3. Последовательность

```text
SIGNAL_DETECTED
-> SIGNAL_CARD_CREATED
-> CONTEXT_CAPTURED
-> STRATEGY_ATTEMPT
-> ENTRY_DECISION
     -> REJECTED/BLOCKED -> CONTINUED_OBSERVATION
     -> ACCEPTED -> EXECUTION_REQUEST
          -> NO_FILL/EXPIRED/EXCHANGE_REJECTED -> CONTINUED_OBSERVATION
          -> FILLED -> POSITION
               -> EXIT
               -> ACTUAL_ECONOMICS
               -> CONTINUED_OBSERVATION
```

## 4. Dispatcher context

Для attempt могут быть связаны отдельно:

```text
market_assessment_id
trading_capacity_snapshot_id
```

Различать `OBSERVED_CONTEXT` и `CONSUMED_CONTEXT`.

## 5. Причины Entry decision

Минимально:

```text
ACCEPTED
STRATEGY_CONDITION_REJECTED
DISPATCHER_MARKET_INCOMPATIBLE
INSUFFICIENT_AVAILABLE_FUNDS
OPERATIONAL_SAFETY_BLOCKED
STALE_OR_UNKNOWN_REQUIRED_STATE
EXCHANGE_REJECTED
```

Не объединять их в общий `RISK_REJECTED`.

## 6. Денежный snapshot

Если решение зависит от доступных средств, attempt должен ссылаться на причинный account-capacity snapshot.

Минимально восстанавливать:

```text
source_exchange/account
observed_at
freshness
total/equity
used
reserved
free
available_for_new_trading
strategy_requested_allocation
```

## 7. Карточка attempt

Карточка должна позволять ответить:

- почему возник signal;
- какая Strategy рассматривалась;
- какой market context видел Entry;
- сколько торговой ёмкости было доступно;
- сколько хотела использовать Strategy;
- почему Entry принял/отклонил;
- был ли отправлен order;
- был ли fill;
- что было дальше с рынком;
- если position была — как работал Exit.

## 8. Actual и counterfactual

Rejected/no-fill/no-funds attempt не удаляется.

Он продолжает наблюдаться как `OBSERVATION_PATH`.

Его дальнейший путь не является фактическим PnL.

## 9. Несколько Strategy

Если один signal обрабатывается несколькими Strategy, attempts сохраняются независимо.

## 10. Точные ID

Целевая цепочка:

```text
signal_id
-> strategy_attempt_id
-> entry_decision_id
-> entry_command_id
-> exchange/client order IDs
-> execution IDs
-> trade_id
-> position_id
-> exit_decision_id
```

Связь по `symbol + время` неканонична.

## 11. Аналитика

Analyst должен отдельно считать strategy rejects, Dispatcher-market rejects, insufficient-funds rejects, operational blocks, no-fill, filled outcomes, saved loss, lost profitable path и capital-constrained opportunity.

## 12. UI/read model

UI не хранит собственную историю.

Карточка восстанавливается из PostgreSQL/read-model.

## 13. Запрет автоматического обучения

Lifecycle — доказательная история, а не механизм изменения Strategy.

## 14. Граница реализации

Этот документ утверждает целевую архитектуру lifecycle.

Он не разрешает автоматически менять schema/code/UI без отдельной implementation-задачи.
