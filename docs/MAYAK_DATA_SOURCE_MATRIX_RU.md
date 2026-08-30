# МАЯК — МАТРИЦА ИСТОЧНИКОВ ДАННЫХ

**Документ:** `MAYAK_DATA_SOURCE_MATRIX_RU.md`  
**Версия:** 1.0  
**Дата:** 2026-08-30  
**Статус:** архитектурная матрица + контрольная точка текущего слепка  
**Связанный документ:** `BYBIT_PUBLIC_DATA_FOR_MAYAK_DISPATCHER_RU.md`

---

## 1. Назначение

Матрица отвечает на цепочку:

```text
Источник доступен?
→ адаптер реализован?
→ данные реально поступают?
→ сохраняются?
→ нормализуются?
→ формируют признак Маяка?
→ входят в dispatcher_handoff?
→ исследованы?
```

Это не список пожеланий, а контрольная карта покрытия.

---

## 2. Контрольная точка

Фактические статусы ниже основаны на диагностическом архиве:

```text
cripta_project_trading_3d_20260830_075125.zip
```

В сохранённом Mayak status:

```text
engine_version = mayak-v2.1
data_quality handoff = MEDIUM
liquidations = WARMUP
```

В сохранённом Dispatcher status:

```text
service_version = strategy-dispatcher-service-0.2.0
profile_count = 0
trading_effect = NONE
adapter_mode = canonical
```

Это **снимок архива**, а не утверждение о текущем live после последующих изменений Codex.

---

## 3. Легенда

```text
YES       — подтверждено в слепке/коде
PARTIAL   — реализована часть
WARMUP    — источник/слой есть, но в снимке не прогрет
NO_DATA   — контракт есть, фактических данных нет
NO        — реализации не найдено
LATER     — запланировано позднее
UNKNOWN   — по слепку доказать нельзя
```

---

## 4. Матрица

| Источник / слой | Bybit public | Адаптер | Live в слепке | Stored | Derived Mayak | Handoff | Research status |
|---|---|---|---|---|---|---|---|
| Spot public trades | YES | YES | PARTIAL, 5/20 coverage | YES | Spot pressure | YES | не подтверждён как gate |
| Linear public trades | YES | YES | YES, 20/20 | YES | Derivatives pressure | YES | не подтверждён как gate |
| Price breadth | derived | YES | YES | YES | Breadth | YES | observation |
| Direction synchronization | derived | YES | YES | YES | Synchronization | YES | observation |
| Multi-timeframe returns | derived | YES | YES | YES | Timeframe alignment | YES | observation |
| Linear ticker | YES | YES | YES | YES | inputs available | PARTIAL | observation |
| Open Interest current | YES | YES | YES | YES | raw/coin | PARTIAL | horizons in warmup at snapshot |
| OI 5/15/30/60m | derived | YES | WARMUP in snapshot | YES | intended OI regime | NO_DATA | needs stable run |
| Funding | YES | YES | YES | YES | raw context | not explicit dedicated feature | needs derivation |
| Mark price | YES | YES | YES | YES | raw context | not dedicated | needs premium stress |
| Index price | YES | YES | YES | YES | raw context | not dedicated | needs premium stress |
| Last/Mark/Index premium stress | YES inputs | NO dedicated layer found | NO | UNKNOWN | NO | NO | planned |
| Long/Short account ratio | YES | YES | YES for sampled symbols | YES/raw status | raw context | not dedicated crowding feature | needs normalization/research |
| Normal orderbook Spot | YES | YES | PARTIAL | YES | liquidity inputs | PARTIAL/NO_DATA trend | needs stable horizons |
| Normal orderbook Linear | YES | YES | YES | YES | liquidity inputs | PARTIAL/NO_DATA trend | needs stable horizons |
| Orderbook 1m change | derived | YES | YES | YES | input | not canonical dedicated | observation |
| Orderbook 5/15m | derived | YES | WARMUP in snapshot | YES | intended liquidity trend | NO_DATA | needs stable run |
| Liquidity resilience | derived | NO dedicated implementation found | NO | NO | NO | NO | planned |
| Absorption | derived | no production Mayak implementation confirmed | NO | NO | NO | vocabulary future | planned |
| All Liquidation | YES | YES in source | WARMUP | likely journal/runtime | WARMUP layer | WARMUP x4 | first priority to validate |
| Liquidation intensity | derived | YES logic indicated | WARMUP | UNKNOWN detail | WARMUP | WARMUP | accumulate |
| Liquidation acceleration | derived | YES logic indicated | WARMUP | UNKNOWN detail | WARMUP | WARMUP | accumulate |
| Liquidation breadth | derived | YES logic indicated | WARMUP | UNKNOWN detail | WARMUP | WARMUP | accumulate |
| Liquidation phase | derived | YES logic indicated | WARMUP | UNKNOWN detail | WARMUP | WARMUP | accumulate |
| RPI orderbook | YES | NO found | NO | NO | NO | NO | planned |
| RPI executions | YES via public trade flags | NO dedicated parsing found | NO | NO | NO | NO | planned |
| Block trade flag | YES | NO dedicated layer found | NO | NO | NO | NO | planned |
| BTC state | derived | YES | YES | YES | BTC state | YES | observation |
| ETH state | derived | YES | YES | YES | ETH state | YES | observation |
| BTC/ETH Options ticker | YES | NO found | NO | NO | NO | NO | later |
| Option IV/skew | derived | NO | NO | NO | NO | NO | later |
| Historical volatility | YES | NO found | NO | NO | NO | NO | later |
| Futures dated basis/curve | YES | NO found | NO | NO | NO | NO | later |
| Insurance pool | YES | NO found | NO | NO | NO | NO | later |
| Bybit system-status WS | YES | NO dedicated adapter found | NO | NO | no | no | planned operational |
| Spot transport health | internal | YES | YES | status | data quality | indirectly | operational |
| Linear transport health | internal | YES | YES | status | data quality | indirectly | operational |
| External exchange flows | external | contract exists | NO_DATA | status field | NO_DATA | not active | later |
| Macro/event context | external | contract exists | NO_DATA | NO/UNKNOWN | NO_DATA | NO_DATA | later |

---

## 5. Что уже хорошо сформировано в handoff

В snapshot `mayak-v2.1` валидно передавались:

```text
market.direction
market.breadth
market.synchronization
market.timeframe_alignment

money.spot_pressure
money.derivatives_pressure
money.pressure
money.spot_derivatives_alignment

btc.state
eth.state
```

Также присутствуют честные placeholders:

```text
positioning.oi_regime = NO_DATA
positioning.price_oi_state = NO_DATA
liquidity.trend = NO_DATA

liquidation.* = WARMUP

event.* = NO_DATA
```

Это правильнее, чем искусственные нули.

---

## 6. Важное замечание по confidence в конкретном слепке

В сохранённом `dispatcher_handoff` многие валидные features имели:

```text
confidence = 1.0
```

при этом Spot coverage в самом Mayak snapshot:

```text
5 / 20
```

Этот слепок появился после предыдущих промежуточных изменений и требует повторной проверки на новом архиве после завершения Codex.

Матрица **не утверждает**, что feature-specific confidence сейчас окончательно корректен в live.

---

## 7. Ближайшее насыщение без новых внешних поставщиков

Уже имеющиеся Bybit inputs позволяют развить:

```text
OI_REGIME
PRICE_OI_STATE

FUNDING_STRESS
DERIVATIVES_PREMIUM_STRESS
POSITIONING_CROWDING

LIQUIDITY_TREND
LIQUIDITY_RESILIENCE
ABSORPTION

LIQUIDATION_* 
```

До подключения Options/RPI/futures curve.

---

## 8. Приоритет проверки следующего архива

После завершения Codex проверить по порядку:

1. transport/freshness semantics;
2. Spot coverage semantics;
3. OI 5/15/30/60;
4. book 1/5/15;
5. allLiquidation реально получает события;
6. liquidation phase выходит из WARMUP;
7. PostgreSQL/JSONL persistence liquidation raw/derived;
8. dedicated OI handoff;
9. dedicated liquidity handoff;
10. Dispatcher assessment persistence;
11. `trading_effect=NONE`;
12. profile count/status;
13. source confidence по каждому feature.

---

## 9. Матрица должна жить как обновляемый документ

После каждого крупного source-layer change обновлять:

```text
Implemented
Live
Stored
Derived
Handoff
Research status
```

Но historical versions сохранять в git.

---

## 10. Итог

Главный практический вопрос для каждого нового источника:

> **Он просто подключён или уже превращён в причинный, сохранённый, версионированный и проверяемый признак Маяка?**

Подключение API само по себе не считается завершённой функцией.
