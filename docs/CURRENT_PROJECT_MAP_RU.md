# Текущее устройство и архитектурные границы проекта CRIPTA

**Документ:** `CURRENT_PROJECT_MAP_RU.md`
**Версия документа:** 4.6
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

D0–D7 implementation: `dispatcher-v2.1`; persisted truth — `dispatcher_v2.global_market_contexts`, `dispatcher_v2.coin_market_contexts`, `dispatcher_v2.trading_capacity_snapshots`; runtime — `cripta-dispatcher-v2.service`. Формула `CoinMarketRating` отложена до следующего research-этапа.

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

## 12.1 Dispatcher V2.1 runtime

Owner decision 2026-09-06 прекратил profile-based legacy runtime. `cripta-strategy-dispatcher.service` и старый `cripta-causal-context-correlator.service` отключены; исторические `strategy_dispatcher.*` и `research_context.event_links` не переписываются. Первичные signal/Entry/fill/position/MAYAK данные продолжают накапливаться и допускают последующий causal backfill.

Активная целевая реализация D0–D7 — clean `dispatcher_v2`: отдельный package/runtime/schema без Strategy profiles. Она публикует `GlobalMarketContext`, `CoinMarketContext` и `TradingCapacitySnapshot` с `trading_effect=NONE`. `CoinMarketRating` на этом этапе **не реализован**. Strategy/Entry/Exit consumer cutover остаётся следующим отдельным этапом.

D0–D7 production runtime подтверждён evidence report `DISPATCHER_V2_D0_D7_STAGE_RESULTS_RU.md`: `cripta-dispatcher-v2.service` active/enabled, installed/loaded source commit `ff259fdc173841a02cc6bb633af5ed5765614df1`, bootstrap from current MAYAK PASS, 20 coin contexts per source snapshot, restart/idempotency PASS. Следующий этап — D8 passive `OBSERVED_CONTEXT` correlation; это ещё не Strategy consumption.

D8 owner-approved implementation scope зафиксирован в `DISPATCHER_V2_D8_OBSERVED_CONTEXT_RU.md`: отдельный append-only V2 event-context link и пассивный causal correlator. На D8 `CONSUMED_CONTEXT=NO`, Entry/Strategy policy не меняется.

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
