# DISPATCHER V2 — РЕЗУЛЬТАТ ЭТАПА D0–D7

**Документ:** `DISPATCHER_V2_D0_D7_STAGE_RESULTS_RU.md`
**Дата:** 2026-09-06
**Статус:** LEVEL 5 · runtime/evidence report

## 1. Итог

```text
DISPATCHER_V2_D0_D7 = PASS
DISPATCHER_VERSION = dispatcher-v2.1
TRADING_EFFECT = NONE
COIN_MARKET_RATING = NOT_IMPLEMENTED
LEGACY_PROFILE_RUNTIME = DISABLED
```

Новый Dispatcher V2 построен как clean implementation и не использует старый profile-based engine.

## 2. Source / installed / loaded

Опубликованный и установленный checkpoint:

```text
ff259fdc173841a02cc6bb633af5ed5765614df1
dispatcher: persist V2 deploy provenance
```

`/var/lib/cripta/dispatcher_v2/runtime.env` хранит тот же `DISPATCHER_V2_SOURCE_COMMIT`; `status.json` подтверждает его в работающем процессе. Source/live SHA проверены для package, runtime, systemd unit и dashboard archive registry.

## 3. Runtime

Активен и enabled только новый сервис:

```text
cripta-dispatcher-v2.service = active/enabled
```

Legacy:

```text
cripta-strategy-dispatcher.service = inactive/disabled
cripta-causal-context-correlator.service = inactive/disabled
```

Остановка legacy не перезапускала `private_runtime` или MAYAK.

## 4. Bootstrap и причинность

Первый старт пустого `dispatcher_v2` взял только последний доступный MAYAK snapshot, а не всю накопленную историю. После следующего минутного snapshot сервис без restart перешёл с source snapshot `8717` на `8718`.

На контрольной точке:

```text
global contexts = 2
coin contexts = 40
distinct symbols per source snapshot = 20
latest source_mayak_snapshot_id = 8718
latest market freshness = FRESH
trading_effect = NONE
```

Restart-idempotency проверена отдельно: для `source_mayak_snapshot_id=8718` до/после restart осталось ровно `1` Global и `20` Coin contexts.

## 5. TradingCapacitySnapshot

Новый Dispatcher публикует причинные account-capacity snapshots из technical account-sync. На runtime smoke присутствовали фактические `total_equity`, `wallet_balance`, `used_position_margin`, `reserved_order_margin`, `free_balance`, `available_for_new_trading`, counts positions/orders, freshness и source provenance.

Снимок является показателем и не разрешает/запрещает Entry.

## 6. PostgreSQL

Persisted truth:

```text
dispatcher_v2.global_market_contexts
dispatcher_v2.coin_market_contexts
dispatcher_v2.trading_capacity_snapshots
```

Runtime role `cripta`:

```text
SELECT = YES
INSERT = YES
UPDATE = NO
DELETE = NO
```

Immutable trigger отдельно подтвердил отказ UPDATE. Таблицы добавлены в archive/export registry.

## 7. Границы

На D0–D7 не реализованы и не разрешены:

- формула `CoinMarketRating`;
- Strategy interpretation;
- Entry/Exit consumer cutover;
- торговый gate/command;
- Strategy profiles/suitability;
- автоматическое изменение Strategy по статистике.

## 8. Следующий этап

Следующий roadmap stage — D8: причинная привязка объективных V2 `GlobalMarketContext`, `CoinMarketContext` и `TradingCapacitySnapshot` к signal/lifecycle событиям как `OBSERVED_CONTEXT`. До фактического подключения Strategy `CONSUMED_CONTEXT` для Dispatcher V2 остаётся отсутствующим.
