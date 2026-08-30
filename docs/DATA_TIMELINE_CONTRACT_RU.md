# КРИПТА — ЕДИНЫЙ КОНТРАКТ ДАННЫХ И ВРЕМЕННОЙ ЛИНИИ

**Документ:** `DATA_TIMELINE_CONTRACT_RU.md`  
**Версия:** 1.0  
**Дата:** 2026-08-30  
**Статус:** глобальный архитектурный контракт  
**Рекомендуемое размещение:** `docs/DATA_TIMELINE_CONTRACT_RU.md`

---

## 1. Назначение

Этот документ задаёт единый способ хранить, связывать и анализировать события всех слоёв проекта:

```text
EXCHANGE
→ MAYAK
→ DISPATCHER
→ STRATEGY
→ ENTRY
→ EXECUTION
→ RISK
→ SUPERVISOR
→ EXIT
→ ANALYST / RESEARCH
```

Главная цель — иметь возможность по любой сделке, отклонённому сигналу, ликвидационному каскаду, сбою или рыночному эпизоду восстановить точную временную историю:

> что произошло во внешнем мире → что система увидела → что поняла → что реально использовала → что сделала → чем закончилось.

---

## 2. Единая координата анализа — UTC

Все слои обязаны иметь UTC-время.

Основная аналитическая ось:

```text
event_time_utc
```

Но одно время не является уникальным идентификатором.

В одну миллисекунду могут существовать:

- сотни public trades;
- десятки liquidation events;
- много обновлений стакана;
- события нескольких символов;
- внутренние события нескольких процессов.

Поэтому каждый объект имеет отдельный:

```text
event_id
```

А время используется для корреляции и построения временной линии.

---

## 3. Минимальный Event Envelope

Каждое событие, сохраняемое как часть доказательной истории, должно позволять восстановить минимум:

```text
event_id
event_time_utc
received_at_utc
processed_at_utc

layer
source
event_type

venue
market_type
symbol

native_source_id
sequence_number

software_version
config_version
schema_version

payload_reference
```

Поля могут быть `null`, если они неприменимы, но отсутствие должно быть явным.

---

## 4. Времена нельзя смешивать

Различать:

### `event_time_utc`

Когда событие произошло у источника.

### `published_at_utc`

Когда источник его опубликовал, если это отдельное время.

### `received_at_utc`

Когда наша система получила сообщение.

### `processed_at_utc`

Когда наша система закончила его обработку.

### `decision_time_utc`

Когда был сформирован торговый вывод.

### `used_at_utc`

Когда конкретный downstream-слой реально использовал контекст.

Это особенно важно для:

- multi-exchange lead/lag;
- WebSocket latency;
- macro events;
- orderbook/trade matching;
- causal replay.

---

## 5. Causality Rule

Для реконструкции решения в момент `T` допустимы только данные:

```text
source_event_time <= T
```

и, если live-процесс не мог знать событие до получения:

```text
received_at_utc <= T
```

Будущие данные разрешены только для постфактум outcome/anatomy.

Запрещено:

```text
T+5m data
→ объяснять как входной признак решения в T
```

---

## 6. Канонические слои

`layer` верхнего уровня:

```text
EXCHANGE
MAYAK
DISPATCHER
STRATEGY
ENTRY
EXECUTION
RISK
SUPERVISOR
EXIT
ANALYST
RESEARCH
SYSTEM
```

Новый слой можно добавить только если существующие значения действительно не описывают его ответственность.

---

## 7. Instrument Identity

Для рыночных событий хранить:

```text
venue
market_type
symbol
```

Например:

```text
venue=BYBIT
market_type=LINEAR
symbol=BTCUSDT
```

или:

```text
venue=BYBIT
market_type=SPOT
symbol=ETHUSDT
```

Нельзя автоматически считать события одного времени относящимися ко всем инструментам.

---

## 8. Scope события

Каждое derived-событие желательно маркировать областью:

```text
SYMBOL
MARKET
VENUE
PANEL
GLOBAL
PORTFOLIO
```

Пример:

```text
BTCUSDT liquidation
scope=SYMBOL
```

```text
18/20 symbols long-liquidation cascade
scope=PANEL
```

Это снижает риск ошибочной корреляции BTC-события с независимым движением ETH.

---

## 9. IDs не заменяют временную корреляцию

Прямые технологические связи должны иметь IDs:

```text
signal_id
decision_id
order_id
fill_id
position_id
exit_id
mayak_snapshot_id
dispatcher_assessment_id
```

Но аналитический слой всё равно строит timeline по времени.

IDs отвечают:

> это тот же объект?

Время отвечает:

> что происходило вокруг него?

---

## 10. Event Links

Рекомендуемый логический объект:

```text
analytics.event_links
```

Минимум:

```text
link_id
source_event_id
target_event_id

relation_type
time_delta_ms

symbol_relation
market_relation
link_quality
link_method

created_at
analyst_version
```

---

## 11. Типы связей

Базовые:

```text
TEMPORALLY_NEAR
SAME_SYMBOL
SAME_MARKET
SAME_POSITION
SAME_ORDER
MARKET_WIDE_CONTEXT

OBSERVED_CONTEXT
CONSUMED_CONTEXT

DERIVED_FROM
CAUSED_ORDER
CAUSED_FILL
OWNS_POSITION
POSITION_EXIT

COUNTERFACTUAL_OF
CORRELATED_WITH

RESEARCH_CONFIRMED
RESEARCH_REJECTED
```

---

## 12. Качество связи

```text
EXACT
STRONG
WEAK
UNKNOWN
```

Примеры:

- одинаковый exchange order ID → `EXACT`;
- один `fill_id` внутри durable handoff → `EXACT`;
- последний causal Mayak snapshot до Entry → `STRONG`, если freshness/coverage достаточны;
- BTC liquidation за 2 секунды до ETH move → только `WEAK` temporal association до дополнительного анализа.

---

## 13. OBSERVED_CONTEXT

`OBSERVED_CONTEXT` отвечает:

> какой объективный контекст существовал к моменту события?

Его может построить отдельный причинный correlator постфактум:

```text
latest_valid_context_time <= event_time
```

Пример:

```text
Entry decision E100
OBSERVED Mayak M88
OBSERVED Dispatcher D91
```

Это ещё не доказывает торговое влияние.

---

## 14. CONSUMED_CONTEXT

`CONSUMED_CONTEXT` отвечает:

> какой snapshot/assessment стратегия реально прочитала и использовала?

Он создаётся только live-кодом/аудитом реального потребителя.

Пример:

```text
M3 decision E100
consumed_dispatcher_assessment_id = D91
```

Нельзя постфактум подменить `OBSERVED_CONTEXT` на `CONSUMED_CONTEXT`.

---

## 15. Raw → Derived → Interpretation

Не смешивать три уровня.

### RAW

Нативные данные источника:

```text
trade
liquidation
ticker
orderbook update
funding
OI
```

### DERIVED / MAYAK

Объективные производные:

```text
breadth
OI regime
liquidation acceleration
liquidity resilience
premium stress
```

### INTERPRETATION / DISPATCHER

Пригодность среды для типа стратегии:

```text
M3_LONG_ENTRY suitability
M3_LONG_HOLD suitability
```

Outcome сделки не должен проникать обратно в RAW/MAYAK.

---

## 16. Counterfactual Timeline

Если сигнал был отклонён, его аналитическая жизнь не заканчивается.

Создаётся:

```text
counterfactual_id
original_signal_id
hypothetical_entry_model
hypothetical_fill_model
```

И позже сохраняются:

```text
MAE
MFE
first +0.5
first +1
first +3
first +5
first -0.5
first -1
holding horizon
```

---

## 17. Временные горизонты анализа

### MICRO
milliseconds → seconds

- orderbook;
- public trades;
- liquidations;
- RPI;
- execution/slippage.

### SHORT
1m → 30m

- Entry;
- early adverse excursion;
- cascade;
- regime transition.

### POSITION
от fill до close

- Supervisor;
- HOLD environment;
- MFE/MAE;
- Exit.

### SESSION
часы → сутки

- clusters;
- correlated exposure;
- daily regime.

### RESEARCH
дни → месяцы

- stability;
- OOS;
- portfolio comparison;
- version comparison.

---

## 18. Временные окна не должны быть одним универсальным join

Аналитик выбирает окно под вопрос.

Пример M3 full stop:

```text
Entry T
Mayak/Dispatcher: T-30m...T
micro market: T-5m...T+5m
position: fill...exit
post outcome: после T только research
```

Пример ликвидационного каскада:

```text
cascade onset ±30m
```

---

## 19. Data Quality входит в историю

Любая derived/interpretation запись должна иметь:

```text
status
confidence
coverage
freshness
source_health
```

Возможные статусы:

```text
VALID
WARMUP
NO_DATA
STALE
UNSUPPORTED
TEMP_UNAVAILABLE
ERROR
```

`NO_DATA` не заменяется нулём.

---

## 20. Версии входят в события

Сохранять версию того, что реально сформировало объект:

```text
mayak_version
feature_version
dispatcher_version
profile_version
strategy_version
entry_version
execution_version
risk_version
supervisor_version
exit_version
settings_version
policy_version
```

---

## 21. Settings History

Изменяемые настройки должны иметь append-only историю:

```text
settings_version
effective_from
effective_until
payload
fingerprint
```

Каждое решение ссылается на действовавшую версию.

---

## 22. Дедупликация

Нельзя дедуплицировать разные exchange events только потому, что у них одинаковые:

```text
timestamp + symbol + price
```

Использовать native IDs / sequence / payload fingerprint.

Derived minute snapshots могут иметь отдельный детерминированный ключ:

```text
layer + feature_version + symbol/scope + interval_start
```

---

## 23. Ordering

При одинаковом `event_time_utc` использовать:

1. native sequence, если есть;
2. received_at;
3. deterministic event_id как последний tie-breaker.

Аналитический отчёт обязан сохранять неопределённость ordering, если источник её не позволяет восстановить.

---

## 24. Retention

Сырые high-frequency данные могут иметь отдельную retention policy.

Но агрегаты, которые понадобятся для исторического causal replay, не должны исчезать без:

- manifest;
- version;
- reproducible aggregation contract.

---

## 25. Правило изменения схемы

Любая новая временная сущность требует одновременно:

- schema/version;
- persistence;
- JSONL/compact export;
- manifest;
- archive inclusion;
- regression test;
- migration/recovery rule.

---

## 26. Главный итог

Проект анализируется по **единой временной оси UTC**, но каждый объект имеет собственную идентичность.

Канонический принцип:

> **IDs отвечают за принадлежность. Время отвечает за контекст. Symbol/venue/scope отвечают за релевантность. Causal links отвечают за доказательство связи.**
