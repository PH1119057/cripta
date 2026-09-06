# MAYAK V2 — ОБЪЕКТИВНЫЙ КОНТЕКСТ РЫНКА И МОНЕТЫ

**Документ:** `MAYAK_OBJECTIVE_CONTEXT_V2_IMPLEMENTATION_RU.md`
**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** owner-approved implementation/research contract
**Основание:** явное решение владельца 2026-09-06 привести MAYAK к новой strategy-agnostic архитектуре и выполнить необходимые этапы live/replay/history research.

Верхние контракты:

- `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
- `PROJECT_ARCHITECTURE_RU.md`
- `MAYAK_ARCHITECTURE_PRINCIPLES_RU.md`
- `MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md`

## 1. Scope

Этот этап изменяет только MAYAK и его технический/аналитический контур данных.

Разрешено:

- исправить фактическое получение публичных рыночных данных;
- исправить quality/coverage semantics;
- построить причинный `CoinMarketContext`;
- сохранить новый контекст append-only в PostgreSQL;
- использовать один причинный feature engine для live и historical replay;
- построить исторический compact dataset и исследовать объективные компоненты;
- внешне сопоставить эти компоненты с frozen Entry outcomes через Analyst/research.

Не разрешено этим этапом:

- менять Strategy/Entry/Exit/Execution;
- превращать MAYAK в trading gate;
- выдавать LONG/SHORT command;
- использовать Entry/PnL как вход MAYAK;
- утверждать финальную формулу `CoinMarketRating` внутри MAYAK;
- смешивать `StrategyCoinFit` с объективным состоянием монеты.

До отдельного owner decision:

```text
MAYAK_TRADING_EFFECT = NONE
DISPATCHER_TRADING_EFFECT = NONE
```

## 2. Версии

Целевая реализация:

```text
engine_version  = mayak-v2.2
feature_version = objective-coin-context-v2
coin_context_schema_version = coin-market-context-v1
```

Существующий `shared-market-context-v1` сохраняется для backward-compatible legacy/shadow Dispatcher. Его source-quality semantics исправляются, но набор полей не ломается этим этапом.

## 3. Один движок live/replay

Каноническая математика признаков находится в `LiveMayakEngine`/его pure helpers и используется как live collector, так и historical replay.

Запрещены две расходящиеся реализации формул вида:

```text
live_feature_formula != research_feature_formula
```

Historical replay подаёт события в тот же движок в хронологическом порядке. В момент T движок видит только события с `event_time <= T` и, где применимо, с причинным `available_at <= T`.

Feature math не использует системное `datetime.now()` для расчёта исторического состояния. Текущее время передаётся в snapshot/event explicitly.

## 4. Source quality

Transport health и наличие рыночной активности — разные факты.

```text
CONNECTED != DATA_PRESENT
SUPPORTED != FRESH
NO_TRADE_IN_WINDOW != ZERO_FLOW_WITH_CONFIDENCE
```

Для source quality сохраняются отдельно:

- instrument availability;
- transport quality;
- activity/event quality;
- observed_at;
- age;
- coverage.

Живой WebSocket не имеет права повышать отсутствующий/непрогретый event stream до `FRESH`.

## 5. WebSocket subscription contract

Для Bybit adapter каждая подписка должна соответствовать фактическому лимиту endpoint.

Spot subscription отправляется пакетами не более 10 args. Linear использует отдельный явно заданный batch limit.

Каждая subscribe request имеет `req_id`. ACK/error response отслеживается. Reject подписки должен ухудшать source/transport diagnostic state, а не выглядеть как нормальный тихий рынок.

## 6. Executed money flow

Spot и derivatives считаются раздельно.

Для каждого symbol и окна:

```text
1m / 5m / 15m / 30m / 60m
```

минимум сохраняются:

```text
buy_usd
sell_usd
net_usd
turnover_usd
net_share
speed_usd_per_min
acceleration_usd_per_min2
large_buy_usd
large_sell_usd
large_trade_share
prior_turnover_usd
turnover_ratio_to_prior
```

`acceleration` сравнивает текущую скорость потока с непосредственно предшествующим окном той же длины. Все окна causal.

`net_usd` — signed aggressive executed flow на конкретной площадке/market type. Он не называется банковским/ончейн депозитом капитала на биржу.

## 7. Positioning / OI / premium

Для каждой монеты минимум:

```text
open_interest
open_interest_value
OI change 5/15/30/60m
OI speed
OI acceleration
funding_rate
funding change where causally available
last_price
mark_price
index_price
mark_index_premium_pct
last_index_premium_pct
last_mark_premium_pct
long_ratio
short_ratio
```

Отсутствующее поле остаётся `null/NO_DATA`.

## 8. Liquidity/orderbook

Spot и derivatives orderbook разделяются.

Минимум хранить:

```text
best/mid reference
bid/ask notional available in subscribed book
imbalance
bid/ask depth within 5/10/25/50 bps, если текущая depth позволяет
bid/ask change 1/5/15m
imbalance change 1/5/15m
```

`resilience` и `absorption` не превращаются в неаудируемый score. Их будущая формула требует отдельной feature version; до этого сохраняются первичные причинные наблюдения, из которых их можно исследовать.

## 9. Liquidations

Сырые liquidation events сохраняются отдельно от voluntary executed flow.

Для каждой монеты и market-wide контекста минимум:

```text
LONG/SHORT count
LONG/SHORT notional
1m / 5m / 15m / 30m
speed
acceleration
intensity
phase
baseline coverage
normalization vs causal prior history
```

Сторона события проверяется контрактными тестами и хранится вместе с provenance.

Exact liquidation history существует только там, где есть raw events. Для периода до начала доказанного raw capture запрещено задним числом записывать proxy как `EXACT_LIQUIDATION`.

Если research использует proxy по OI/price/flow/orderbook, он маркируется отдельным типом `LIQUIDATION_PROXY` и не смешивается с exact cohort.

## 10. Relative strength / divergence

Для каждого горизонта `1/5/15/60m` сохраняются:

```text
coin_return_pct
panel_median_return_pct
relative_to_panel_pct
relative_to_btc_pct
relative_to_eth_pct
```

Это объективные числовые расхождения, а не Strategy score.

## 11. CoinMarketContext

Каждый регулярный MAYAK snapshot формирует для каждого наблюдаемого symbol отдельный неизменяемый объект:

```text
coin_context_id
mayak_snapshot_id
symbol
observed_at
schema_version
engine_version
feature_version
config_fingerprint
data_quality
payload
provenance
content_hash
```

`payload` содержит минимум:

```text
price
money.spot
money.derivatives
positioning
liquidity.spot
liquidity.derivatives
liquidations
relative_strength
event_context
data_quality
```

Объект не содержит signals, positions, PnL, Strategy outcome или trading command.

## 12. PostgreSQL contract

Новая append-only таблица:

```text
mayak_v2.coin_market_contexts
```

создаётся только versioned migration, не скрытым live-startup DDL.

Для неё обязателен immutable UPDATE/DELETE trigger и только минимальные runtime privileges `SELECT, INSERT` для роли `cripta`.

Исторические `coin_minutes` не переписываются задним числом под новую семантику.

## 13. Replay provenance

Каждый historical run фиксирует минимум:

```text
source commit
engine_version
feature_version
schema_version
config_fingerprint
symbols
period
raw dataset fingerprint
source types present/missing
exact liquidation coverage interval
replay code hash
row counts
causality assertions
```

## 14. Historical data policy

За период `2026-05-18..2026-08-16` существующие derivatives public trades и depth-200 orderbook используются повторно, без дублирования raw dataset.

Spot trades, OI, funding, mark/index/premium могут быть backfill через проверенные публичные источники/адаптеры. Backfill должен быть потоковым/compact, чтобы не дублировать десятки гигабайт без необходимости.

Exact liquidations до доказанного начала raw capture не подделываются.

## 15. Component research before rating

До `CoinMarketRating` исследуются отдельно:

- Spot executed flow;
- derivatives executed flow;
- их согласование/расхождение;
- OI;
- funding/premium;
- liquidity;
- exact liquidation state;
- relative strength/divergence;
- data quality.

Сначала измеряется устойчивость каждого объективного компонента по времени/активам/режимам. Затем Dispatcher может получить отдельный owner-approved contract формулы `CoinMarketRating`.

## 16. Entry outcome correlation is external

Frozen Entry/other Strategy outcomes подключаются только после формирования неизменяемого objective context через Analyst/research join.

Правильная связь:

```text
MAYAK objective context at T
        +
external strategy signal/outcome
        ↓
ANALYST / RESEARCH
```

Outcome не возвращается в MAYAK feature computation.

## 17. Required tests

Минимум автоматическими тестами доказать:

1. Spot subscriptions batch <=10 и ACK/error tracked.
2. Connected transport без событий не становится FRESH activity.
3. Missing flow не становится zero/neutral.
4. 1/5/15/30/60m flow windows causal.
5. Replay одного event stream даёт тот же snapshot, что live engine.
6. Snapshot at T не меняется от события T+1.
7. OI horizons/acceleration causal.
8. Relative strength использует только current causal panel.
9. Per-coin liquidation context разделяет LONG/SHORT и не выдумывает phase без baseline.
10. CoinMarketContext не содержит торговых сущностей/команд.
11. PostgreSQL table immutable.
12. Existing legacy shared-market-context schema остаётся совместимым.
13. MAYAK не имеет order mutation surface.

## 18. Deployment / observation

После green overlay:

```text
migration
-> source checkpoint
-> live file install
-> restart ONLY cripta-mayak-v2.service
-> runtime smoke
```

Не перезапускать Strategy/Entry/Exit/Execution ради этого patch.

Runtime smoke должен доказать:

- service active;
- 20-symbol derivatives coverage;
- Spot coverage по реально поддерживаемым/торгуемым Spot symbols без искусственного `FRESH`;
- no subscription rejects;
- new coin context rows arrive;
- `trading_command=false`;
- no future timestamps;
- old shared context still persists.

## 19. Completion of MAYAK stage

MAYAK stage считается завершённым, когда:

```text
P0 data-quality fix = PASS
CoinMarketContext live = PASS
same causal replay engine = PASS
historical compact backfill = PASS for available exact sources
component research = COMPLETE
frozen Entry external correlation = COMPLETE
MAYAK trading_effect = NONE
```

Формула `CoinMarketRating`, Dispatcher V2 и Strategy usage остаются следующими отдельными этапами.
