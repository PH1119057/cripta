# Текущее устройство и архитектурные границы проекта CRIPTA

**Документ:** `CURRENT_PROJECT_MAP_RU.md`
**Версия документа:** 4.3
**Дата:** 2026-09-06
**Статус:** краткая текущая карта; не отдельный архитектурный контракт

## 1. Source checkpoint

Текущий source checkpoint определяется фактически проверенным равенством:

```text
GitHub PH1119057/cripta:main
==
/srv/cripta/source_checkout
```

Последний runtime checkpoint хранится отдельно и не считается автоматически текущим состоянием.

## 2. Верхняя прикладная архитектура

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

Пять верхних уровней.

`Risk` не является отдельным верхним слоем.

## 3. Два контура

### Прикладной

Определяет смысл: что происходит на рынке, какова общая обстановка, какая Strategy policy применяется, разрешён ли вход, как сопровождать позицию и какое действие требуется.

### Технический поддерживающий

Обеспечивает market/account connectivity, exchange adapters, private account sync, clock/reconnect, PostgreSQL, IDs/audit, Position Supervisor, Analyst, UI/read models, services, restart/reconciliation и operational safety.

Технический контур поддерживает прикладной, но не становится владельцем Strategy.

## 4. MAYAK

Наблюдает внешний рынок. Trading effect: `NONE`.

Текущая source-реализация objective context: `mayak-v2.2` /
`objective-coin-context-v2`. Она добавляет strategy-agnostic
`CoinMarketContext` для каждого наблюдаемого инструмента и сохраняет его append-only
в `mayak_v2.coin_market_contexts`. Внутри MAYAK не вычисляется Strategy-specific
пригодность монеты и не принимается решение LONG/SHORT.

Live и historical replay используют один `LiveMayakEngine`; replay только причинно
подаёт нормализованные события в тот же движок. Исторический replay без точного raw
источника ликвидаций обязан сохранять этот слой как `NO_DATA`, а не выводить ложное
`NONE`.

`CoinMarketRating` остаётся объектом Dispatcher поверх объективных MAYAK-фактов;
формула рейтинга в MAYAK не зашивается. Состояние установленного/загруженного runtime
проверяется отдельно от source checkpoint.

MAYAK V2 historical stage 2026-09-06 завершён отдельным evidence report
`MAYAK_V2_STAGE_RESULTS_RU.md`: exact causal replay по frozen ALL9/1063 включает
derivatives/Spot executed flow, OI, funding/mark/index premium, account ratio и
derivatives depth-200 liquidity. Frozen component research = 251 признак;
`CoinMarketRating` не фитился, live Entry policy не менялась. Следующий research gate —
новый temporal/cross-asset OOS с теми же frozen definitions.

## 5. Dispatcher

Публикует три strategy-agnostic класса показателей:

1. объективный global market context;
2. объективный per-coin context / `CoinMarketRating`;
3. состояние торговой ёмкости аккаунта.

Dispatcher не знает тип Entry и не определяет пригодность рынка за конкретную Strategy. Интерпретация принадлежит Strategy.

Account capacity минимум:

```text
total
used
reserved
free
available_for_new_trading
freshness
source_exchange/account
```

Источник фактов — подключённая торговая площадка через technical account-sync.

## 6. Strategy

Owner-approved versioned policy.

Внутри:

```text
ENTRY
EXIT
```

Strategy определяет размер, allocation, leverage, stop, допустимую просадку, holding и exit policy.

## 7. Entry

Monitor/Scanner даёт candidate signal, не приказ на вход.

Entry рассматривает signal в рамках подходящей утверждённой Strategy.

Он может использовать objective Dispatcher global/coin context, Dispatcher account-capacity snapshot и technical readiness в соответствии с policy выбранной Strategy.

Отказ из-за отсутствия денег:

```text
INSUFFICIENT_AVAILABLE_FUNDS
```

## 8. Exit

После fill работает по той же Strategy binding.

## 9. Execution

Исполняет готовое решение и владеет exchange mutation mechanics, fill truth, IDs, protection, reconciliation и durable handoff.

## 10. Exchange

Внешняя торговая площадка. Архитектура не привязана к конкретному провайдеру.

## 11. Signal / Attempt

История начинается на `SIGNAL_DETECTED`.

```text
signal_id
-> strategy_attempt_id
-> strategy binding
-> Entry decision
-> optional Execution
-> optional position
-> optional Exit
```

Rejected/no-fill/no-funds attempts сохраняются.

## 12. Аналитика

Supervisor/Analyst/PostgreSQL/UI находятся в поддерживающем наблюдательно-аналитическом контуре.

`StrategyCoinFit` — отдельный Analyst/research показатель исторической совместимости конкретной Strategy с конкретной монетой. Он не смешивается с объективным `CoinMarketRating`.

Они не являются новыми trading layers.

## 12.1 Переходный runtime Dispatcher

Текущая реализация всё ещё содержит profile-based `strategy_dispatcher.assessments` (`GOOD_MATCH`, `INCOMPATIBLE` и т.п.). После owner decision 2026-09-06 это transitional research/shadow механизм с `trading_effect=NONE`, а не целевой канон Dispatcher. Отдельная implementation-задача должна позже привести runtime к универсальному global/coin context без автоматического изменения trading policy.

## 13. Масштабирование

Архитектура допускает много Strategy/bots/positions.

Не определены и не должны придумыватьcя без отдельной задачи:

- strategy selector;
- capital allocator;
- strategy priority;
- global position cap.

## 14. Что читать

1. `CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`
2. `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
3. `docs/PROJECT_ARCHITECTURE_RU.md`
4. `docs/PROJECT_GOVERNANCE_RU.md`
5. `docs/MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md` при работе с MAYAK/Dispatcher/coin rating
6. затрагиваемые специализированные контракты
