# DISPATCHER V2 — IMPLEMENTATION CONTRACT D0–D7

**Документ:** `DISPATCHER_V2_IMPLEMENTATION_RU.md`
**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** LEVEL 4 · активный implementation contract
**Основание:** owner decision 2026-09-06 + `STRATEGY_DISPATCHER_ARCHITECTURE_RU.md` v2.1.

## 1. Цель

Создать новый чистый Dispatcher V2 как универсальный пассивный read-model между MAYAK и Strategy.

```text
MAYAK shared/coin contexts
          +
normalized trading account truth
          ↓
DISPATCHER V2
  ├─ GlobalMarketContext
  ├─ CoinMarketContext × N
  └─ TradingCapacitySnapshot
          ↓
future Strategy consumers
```

`trading_effect=NONE` на всём D0–D7.

## 2. Clean implementation boundary

Новые пути:

```text
production/src/bybit_workbench/dispatcher_v2/
operations/monitoring/dispatcher_v2.py
operations/dispatcher_v2/cripta-dispatcher-v2.service
operations/sql/20260906_dispatcher_v2_contexts.sql
dispatcher_v2.* PostgreSQL
/var/lib/cripta/dispatcher_v2/status.json
```

Новый код не импортирует `bybit_workbench.strategy_dispatcher` и не читает `config/strategy_dispatcher/profiles`. Запрещены Strategy profile/suitability semantics.

Legacy `strategy_dispatcher.*` PostgreSQL остаётся историческим audit, но не является input V2.

## 3. D0 — архитектура и decommission boundary

- owner decision оформлен в каноне;
- legacy service перестаёт создавать новые assessments;
- legacy passive correlator не имеет права продолжать приклеивать stale last assessment к новым событиям;
- первичные signal/Entry/fill/position/MAYAK данные продолжают жить независимо и могут быть причинно backfill после появления V2 correlator.

## 4. D1 — immutable contracts

Три frozen read-model объекта. Каждый содержит `observed_at`, `created_at`, `dispatcher_version`, `schema_version`, `config_fingerprint`, quality/freshness/coverage, provenance/source IDs и `trading_effect=NONE`.

Никакой объект не содержит `ENTER_LONG`, `ENTER_SHORT`, `ALLOW`, `BLOCK`, suitability или Strategy profile.

## 5. D2 — direct MAYAK handoff

Runtime читает PostgreSQL напрямую:

```text
mayak_v2.shared_market_contexts
mayak_v2.coin_market_contexts
```

Нельзя читать legacy Dispatcher output как источник V2. Для одного market snapshot coin contexts связываются по точному `mayak_snapshot_id`. Future mixing запрещён.

## 6. D3 — GlobalMarketContext

Источник — immutable `mayak_v2.shared_market_contexts`. V2 сохраняет объективные global facts, source quality, statuses/coverage, MAYAK IDs и provenance. Он не делает Strategy interpretation.

## 7. D4 — CoinMarketContext

Источник — `mayak_v2.coin_market_contexts` того же `mayak_snapshot_id`. Сохраняются физически раздельно money/price/liquidity/positioning/liquidations/relative-strength/event/data-quality.

На D0–D7:

```text
COIN_MARKET_RATING_IMPLEMENTED = NO
```

Ни score, ни band не придумываются.

## 8. D5 — TradingCapacitySnapshot

Источник — normalized technical account state:

```text
runtime.wallet_latest
runtime.hot_positions
runtime.hot_orders
```

Минимум:

```text
total_equity
wallet_balance
used_position_margin
reserved_order_margin
free_balance
available_for_new_trading
open_positions_count
active_orders_count
source_adapter/account_ref
observed_at/freshness/quality
```

Unknown margin field остаётся `null`, не превращается в zero. Snapshot не является разрешением на вход.

## 9. D6 — persisted truth

Новый PostgreSQL namespace `dispatcher_v2` append-only:

```text
dispatcher_v2.global_market_contexts
dispatcher_v2.coin_market_contexts
dispatcher_v2.trading_capacity_snapshots
```

Owner таблиц — migration owner; runtime `cripta` получает только минимальные `SELECT/INSERT`. UPDATE/DELETE запрещены immutable trigger.

Новая schema одновременно добавляется в archive/manifest/export contract проекта.

## 10. D7 — production passive runtime

`cripta-dispatcher-v2.service`:

- user/group `cripta`;
- читает только утверждённые MAYAK/account источники;
- пишет только `dispatcher_v2.*` и `/var/lib/cripta/dispatcher_v2`;
- не имеет пути к Execution/private mutation;
- не импортирует Strategy/Entry/Exit/Supervisor;
- публикует status/health с source freshness и counts;
- restart/idempotency не создаёт дубликаты.

## 11. Версии D0–D7

```text
dispatcher_version = dispatcher-v2.1
global_schema      = global-market-context-v2.1
coin_schema        = dispatcher-coin-context-v2.1
capacity_schema    = trading-capacity-v2.1
trading_effect     = NONE
```

## 12. Freshness implementation defaults

Это техническое описание свежести, не trading gate:

```text
market_context_fresh_seconds = 90
coin_context_fresh_seconds   = 90
account_fresh_seconds        = 30
```

Consumer обязан сам сравнивать `observed_at` с временем своего решения. Исторический stored freshness отражает возраст на момент построения V2 context и не превращает Dispatcher в safety layer.

## 13. Не входит в D0–D7

- CoinMarketRating formula/research;
- StrategyCoinFit;
- Strategy interpretation;
- Entry/Exit consumer cutover;
- allocator/arbitration;
- live/shadow trading influence;
- удаление исторических audit rows.
