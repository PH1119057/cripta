# НЕПРЕРЫВНЫЙ ЖИЗНЕННЫЙ ЦИКЛ СТРАТЕГИЧЕСКОГО СИГНАЛА

**Документ:** `SIGNAL_LIFECYCLE_CONTRACT_RU.md`
**Версия:** 1.3
**Дата:** 2026-09-08
**Статус:** канонический архитектурный контракт
**Основание V1.3:** owner decision 2026-09-08 — разделить объективный рыночный факт и strategy-specific signal; universal Entry не выбирает Strategy.

Верхние контракты:

- `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
- `PROJECT_ARCHITECTURE_RU.md`
- `STRATEGY_ENTRY_ARCHITECTURE_RU.md`

## 1. Два типа событий

Нужно различать:

```text
MARKET FACT / MARKET EVENT
= объективный причинный факт рынка или ссылка на набор таких фактов

STRATEGY SIGNAL
= факт того, что конкретный активный EntryPlan конкретной Strategy выполнил свои сигнальные условия
```

Market fact не является торговой командой и не обязан иметь отдельный synthetic `market_event_id`, если точная source lineage уже существует через trade/candle/zone/context/event IDs.

Канонический торговый `signal_id` обозначает `StrategySignal`.

## 2. Где рождается StrategySignal

StrategyCard пассивна и сама signal не создаёт.

MAYAK signal не создаёт.

Dispatcher signal не создаёт.

Entry Watch внутри universal Entry создаёт `StrategySignal`, когда одновременно выполнены:

```text
StrategyActivation enabled
+ immutable EntryPlan
+ causal market state
+ mandatory consumed context according to EntryPlan
```

Один рыночный момент/набор фактов может породить ноль, один или несколько независимых StrategySignal разных Strategy.

## 3. Корневая торговая сущность

Корневая сущность торговой lifecycle — strategy-specific `signal_id`.

Карточка и машинная история создаются в момент `STRATEGY_SIGNAL_DETECTED`, а не после fill.

`signal_id` не теряется независимо от исхода.

Upstream causal source refs сохраняются отдельно и не подменяются связью по `symbol + время`.

## 4. Strategy binding

Каждый `signal_id` обязан быть связан минимум с:

```text
strategy_id
strategy_version
strategy_config_fingerprint
entry_plan_version
entry_plan_fingerprint
strategy_activation_id
```

Signal одной Strategy не является signal другой Strategy, даже если они возникли на одном symbol и в одну миллисекунду.

## 5. Strategy attempt

После signal создаётся `strategy_attempt_id` для конкретной попытки исполнения этой Strategy.

Базовая модель:

```text
signal_id
  -> strategy_attempt_id
```

Несколько attempts от одного signal допустимы только при явно утверждённой модели нескольких bot/account execution targets. Это не механизм выбора между Strategy.

## 6. Последовательность

```text
CAUSAL MARKET/CONTEXT FACTS
-> ENTRY PLAN MATCH
-> STRATEGY_SIGNAL_DETECTED
-> SIGNAL_CARD_CREATED
-> CONTEXT_CAPTURED
-> STRATEGY_ATTEMPT
-> ENTRY_DECISION
     -> REJECTED/BLOCKED/EXPIRED/CANCELLED -> CONTINUED_OBSERVATION
     -> ACCEPTED -> EXECUTION_REQUEST
          -> EXCHANGE_REJECTED/NO_FILL/EXPIRED -> CONTINUED_OBSERVATION
          -> FILLED -> POSITION
               -> EXIT under same Strategy binding
               -> ACTUAL_ECONOMICS
               -> CONTINUED_OBSERVATION
```

## 7. Рыночная causal lineage

Карточка signal должна позволять восстановить, какие данные реально привели Entry Watch к выполнению EntryPlan.

Минимально хранить точные доступные source refs, например:

```text
source trade/event IDs
candle/bar IDs or exact timestamps/fingerprints
zone/geometry handoff IDs
MAYAK source/context IDs
Dispatcher context IDs
sensor state/version
observed_at / event_at / received_at where applicable
```

Для сложной Strategy один signal может опираться на несколько source facts в последовательности/окне.

Нельзя восстанавливать causal lineage постфактум через «ближайшее событие по symbol/time», если точная связь не доказана.

## 8. Observed и Consumed context

Для signal/attempt отдельно различать:

```text
OBSERVED_CONTEXT
= какой объективный MAYAK/Dispatcher context существовал причинно рядом с событием

CONSUMED_CONTEXT
= какой конкретный context EntryPlan действительно прочитал и использовал
```

Наличие `OBSERVED_CONTEXT` не доказывает торговое влияние.

Для consumed context хранить ID, version/fingerprint, observed_at, event-time age, quality/freshness/coverage и роль в EntryPlan (`OBSERVE`, `CONDITION`, `RANKING`, где применимо).

Missing/stale/partial не превращаются в zero/neutral.

## 9. Dispatcher context

Для signal/attempt могут быть связаны отдельно:

```text
global_market_context_id
coin_market_context_id / coin_market_rating_id
trading_capacity_snapshot_id
legacy_market_assessment_id   # только historical compatibility
```

Dispatcher не создаёт StrategySignal и не включает Strategy.

## 10. Причины Entry decision

Минимально различать:

```text
ACCEPTED
STRATEGY_CONDITION_REJECTED
INSUFFICIENT_AVAILABLE_FUNDS
OPERATIONAL_SAFETY_BLOCKED
STALE_OR_UNKNOWN_REQUIRED_STATE
EXPIRED
CANCELLED
```

Downstream Execution отдельно фиксирует:

```text
EXCHANGE_REJECTED
NO_FILL
EXECUTION_FAILURE
```

Не объединять причины в общий `RISK_REJECTED` и не использовать `OTHER_STRATEGY_WON` без отдельного будущего arbitration contract.

## 11. Денежный snapshot

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

Недостаток капитала является фактом конкретной attempt, а не правом Entry выбрать другую Strategy.

## 12. Карточка signal/attempt

Карточка должна позволять ответить:

- какая Strategy version была включена;
- какой EntryPlan выполнялся;
- какие causal market facts выполнили его условия;
- какой objective context существовал;
- какой context реально был consumed;
- сколько торговой ёмкости было доступно;
- сколько хотела использовать Strategy;
- почему Entry принял/отклонил attempt;
- был ли создан ExecutionRequest;
- был ли отправлен order;
- был ли fill;
- что было дальше с рынком;
- если position была — как работал Exit той же Strategy.

## 13. Несколько Strategy

Все включённые Strategy исполняются независимо.

Один market event может породить разные `signal_id`:

```text
Market facts M
  -> Strategy A / EntryPlan A -> signal S-A
  -> Strategy B / EntryPlan B -> no signal
  -> Strategy C / EntryPlan C -> signal S-C
```

`S-A` и `S-C` имеют независимые cards/attempts/outcomes.

Противоположные LONG/SHORT signals допустимы. Entry не разрешает их конфликт выбором Strategy.

## 14. Точные ID

Целевая цепочка:

```text
causal source refs
-> signal_id
-> strategy_id/version/fingerprint
-> entry_plan_fingerprint
-> strategy_activation_id
-> strategy_attempt_id
-> entry_decision_id
-> entry_command_id / execution_request_id
-> exchange/client order IDs
-> execution IDs
-> trade_id
-> position_id
-> exit_decision_id
```

Связь по `symbol + время` неканонична.

## 15. Actual и counterfactual

Rejected/no-fill/no-funds attempt не удаляется.

Он продолжает наблюдаться как `OBSERVATION_PATH`.

Его дальнейший путь не является фактическим PnL.

## 16. Аналитика

Analyst должен отдельно считать strategy-condition rejects, insufficient-funds rejects, operational blocks, stale-required-state rejects, no-fill, execution rejects, filled outcomes, saved loss, lost profitable path и capital-constrained opportunity.

Исторические `DISPATCHER_MARKET_INCOMPATIBLE` сохраняются как legacy-аудит и не становятся канонической новой причиной отказа.

## 17. Уведомления

Технический notification layer может уведомлять администратора о:

```text
INSUFFICIENT_AVAILABLE_FUNDS
OPERATIONAL_SAFETY_BLOCKED
STALE_OR_UNKNOWN_REQUIRED_STATE
EXCHANGE_REJECTED
EXECUTION_FAILURE
```

Уведомление обязано ссылаться на точные Strategy/signal/attempt IDs и не менять торговое решение.

## 18. UI/read model

UI не хранит собственную историю.

Карточка восстанавливается из PostgreSQL/read-model.

Управляющий пульт изменяет `StrategyActivation`, но не переписывает immutable StrategyCard.

## 19. Запрет автоматического обучения

Lifecycle — доказательная история, а не механизм изменения Strategy.

## 20. Граница реализации

Этот документ утверждает целевую архитектуру lifecycle.

Он не разрешает автоматически менять schema/code/UI без отдельной implementation-задачи.

Текущие исторические `signal_id` не переписываются задним числом. Миграционная/совместимая модель для существующих записей определяется отдельным implementation contract.
