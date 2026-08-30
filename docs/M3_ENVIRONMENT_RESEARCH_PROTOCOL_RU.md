# M3 V1 — ПРОТОКОЛ ИССЛЕДОВАНИЯ РЫНОЧНОЙ СРЕДЫ

**Документ:** `M3_ENVIRONMENT_RESEARCH_PROTOCOL_RU.md`  
**Версия:** 1.0  
**Дата:** 2026-08-30  
**Статус:** research protocol, никаких новых live rules  
**Стратегия:** M3 V1

---

## 1. Что сохраняем неизменным

Базовая геометрия M3 V1:

> согласованный сигнал 5m + 15m.

Этот research не ретюнит 5m/15m Entry автоматически.

Цель — исследовать:

1. в какой внешней среде разумно открывать новый M3;
2. когда среда удержания уже открытой M3-позиции реально ломается;
3. можно ли сократить долю full `-1%` без уничтожения хороших восстановлений и runners.

---

## 2. Четыре независимых профиля

Исследовать отдельно:

```text
M3_V1_LONG_ENTRY
M3_V1_SHORT_ENTRY

M3_V1_LONG_HOLD
M3_V1_SHORT_HOLD
```

ENTRY и HOLD никогда не считать одним контрактом.

---

## 3. Почему ENTRY != HOLD

Новый LONG может быть уже нежелателен в ухудшившейся среде, но существующий LONG:

- может иметь запас прибыли;
- может демонстрировать относительную силу;
- может иметь живую локальную структуру.

Поэтому `ENTRY_ENVIRONMENT` не является командой закрыть уже существующую позицию.

---

## 4. Research only

Первый этап:

```text
SHADOW / COUNTERFACTUAL
```

Никакого влияния на реальные orders, stops, size, leverage, Entry, Exit.

---

## 5. Не задавать пороги заранее

Запрещено без исследования фиксировать:

```text
suitability < 0.4 => reject
INCOMPATIBLE 10 sec => exit
-0.5% => close
```

Сначала накопление статистики.

---

## 6. Какие данные исследовать

Из Mayak/Dispatcher особенно:

```text
market.direction
market.breadth
market.synchronization
market.timeframe_alignment

money.spot_pressure
money.derivatives_pressure
money.spot_derivatives_alignment

positioning.oi_regime
positioning.price_oi_state

funding stress
premium stress
crowding

liquidity.trend
liquidity.resilience
absorption

liquidation.intensity
liquidation.acceleration
liquidation.breadth
liquidation.phase

btc.state
eth.state

option risk        # later
event context      # later
```

---

## 7. Data quality gate для research

Каждая assessment запись должна сохранять:

```text
feature statuses
feature confidence
coverage
overall data_quality
```

Случай с `NO_DATA` нельзя смешивать с валидным наблюдением.

---

## 8. ENTRY cohort

Для каждого исходного M3 signal сохранять:

```text
signal_id
time
symbol
side
M3 version
entry fingerprint

Mayak observed context
Dispatcher ENTRY assessment

actual decision
actual fill if any

counterfactual path
```

---

## 9. Rejected signal обязательно сопровождается

Если M3 SHADOW profile сказал бы не входить:

виртуальный путь всё равно считается.

Минимум:

```text
fill/no-fill according to original M3 model
MAE
MFE
first -1
first +1
first +3
first +5
```

---

## 10. Основные ENTRY категории

```text
SAVED_FULL_STOP
SAVED_PARTIAL_LOSS
LOST_GOOD_TRADE
MISSED_RUNNER
NO_MATERIAL_EFFECT
UNRESOLVED
```

---

## 11. HOLD cohort

После реального fill Entry перестаёт владеть позицией.

Для каждой позиции строить временную линию:

```text
fill
M3 HOLD assessments
Mayak states
Supervisor states
MFE/MAE
actual exit
```

---

## 12. HOLD не должен смотреть на будущий PnL

Assessment в T использует только causal context.

Outcome после T используется только Аналитиком.

---

## 13. Главный вопрос HOLD

Для каждого full hard stop:

> в какой первый causal момент среда M3 HOLD стала устойчиво несовместимой и было ли одновременно локальное ухудшение позиции?

---

## 14. Два независимых канала

Общерыночный:

```text
M3_HOLD assessment
```

Локальный:

```text
Position Supervisor
```

Исследовать их отдельно и вместе.

---

## 15. Не заменять -1 новым тупым стопом

Цель не:

```text
-1 → -0.5
```

Цель:

> hard -1 остаётся последней защитой, а causal environment/local break может позволить осмысленный более ранний выход.

---

## 16. Что измерять при потенциальном early exit

```text
time_from_fill
pnl_at_candidate
MAE_before_candidate
MFE_before_candidate

future_min
future_max

would_hit_hard_stop
would_recover_entry
would_reach_+1
would_reach_+3
would_reach_+5
```

---

## 17. Destroyed Recovery

Критическая категория:

```text
candidate early exit at loss
but original trade later recovered materially
```

Её стоимость обязательно вычитать из экономической пользы.

---

## 18. Runner cost

Если раннее правило убивает tail:

```text
+3
+5
+10
+20
```

это считается отдельно.

---

## 19. Lead Time

Для full-stop cases:

```text
hard_stop_time - first_valid_warning_time
```

Распределение:

- median;
- p25/p75;
- min/max;
- by symbol;
- by regime.

---

## 20. False Warning

Для позиции, которая получила предупреждение, но затем:

- восстановилась;
- вышла в +1;
- стала runner.

Считать отдельно.

---

## 21. Persistence

Исследовать, полезна ли:

- абсолютная assessment;
- скорость ухудшения;
- длительность несовместимости;
- число независимых подтверждающих слоёв.

Но не выбирать параметры на одном seen sample бесконечно.

---

## 22. Regime Transition

Сохранять:

```text
assessment_previous
assessment_current
delta
time_delta
```

Резкое:

```text
GOOD → INCOMPATIBLE
```

может иметь другой смысл, чем постоянный `POOR`.

Это гипотеза для исследования.

---

## 23. Liquidation Phase

Отдельно исследовать:

```text
TENSION_BUILDING
CASCADE
EXHAUSTION
RECOVERY
```

Для LONG и SHORT асимметрично.

---

## 24. Symbol independence

BTC cascade не означает автоматически veto ETH.

Считать:

- panel breadth;
- synchronization;
- конкретный symbol response;
- relative strength;
- local Supervisor state.

---

## 25. Cluster analysis

Full stops M3 группировать:

```text
5m
15m
30m
60m
```

И проверять:

- одинаковые Dispatcher states;
- liquidation breadth;
- BTC/ETH context;
- simultaneous exposure.

---

## 26. Основные ENTRY метрики

```text
signals total
signals/day
accepted/rejected hypothetical
signal retention

bad-entry reduction
full-stop reduction

lost +1 trades
lost +3/+5 runners

net value
```

---

## 27. Основные HOLD метрики

```text
positions
warnings

full stops preceded
median lead time

average loss saved
destroyed recoveries
lost runner value

false warnings
net value
```

---

## 28. Costs

Использовать:

- actual fees;
- modeled/actual slippage;
- funding where applicable.

Теоретический price-level BE не равен net BE.

---

## 29. Net Value ENTRY

```text
avoided losses
- missed winners
- missed runners
- opportunity costs
```

---

## 30. Net Value HOLD

```text
saved loss versus baseline exit
- destroyed recoveries
- lost future runner PnL
- extra fees
- extra slippage
- funding difference
```

---

## 31. Simple Benchmark

Новая средовая логика должна сравниваться с заранее определённым простым benchmark:

```text
M3 V1 unchanged
hard -1 emergency stop
current Exit/Risk policy
```

Сложность оправдана только при материальном улучшении.

---

## 32. Development / OOS

Разделять:

```text
development
temporal OOS
cross-asset OOS
confirmation
```

Не превращать просмотренный holdout в чистый holdout повторно.

---

## 33. High Entry accuracy

Если M3 уже даёт высокое качество входов, цена ложного veto высока.

Поэтому обязательно измерять:

```text
failure reduction
AND
lost good entries
```

Нельзя оптимизировать только число предотвращённых стопов.

---

## 34. Portfolio caveat

Signal-level M3 research не является portfolio backtest.

Отдельно проверять:

- simultaneous positions;
- same-symbol restrictions;
- margin;
- correlated exposure;
- deterministic conflicts.

---

## 35. Порог достаточного evidence

Не фиксировать сейчас число.

Но hard live rule не вводится по:

- десятку случаев;
- одному дню;
- одному активу;
- одной красивой кластерной истории.

---

## 36. Порядок продвижения

```text
SHADOW PROFILE
→ accumulate
→ Analyst
→ research
→ new frozen candidate
→ live feature/signal equivalence
→ MICRO_LIVE
→ separate LIVE decision
```

---

## 37. Provenance

Каждый результат:

```text
project commit
Mayak version
Dispatcher version
profile version
M3 version
Entry fingerprint
Exit/Risk version
period
symbols
sample count
data completeness
research label
```

---

## 38. Итоговый вопрос

Research успешен только если можно доказательно ответить:

> **M3 environment layer уменьшает экономически дорогие неудачи больше, чем уничтожает хорошие M3-сделки и восстановления.**
