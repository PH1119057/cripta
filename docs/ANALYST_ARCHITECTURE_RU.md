# АНАЛИТИК — АРХИТЕКТУРА СЛОЯ ПОСТФАКТУМ-АНАЛИЗА

**Документ:** `ANALYST_ARCHITECTURE_RU.md`  
**Версия:** 1.0  
**Дата:** 2026-08-30  
**Статус:** глобальный архитектурный контракт  
**Рекомендуемое размещение:** `docs/ANALYST_ARCHITECTURE_RU.md`

---

# 1. Назначение

**Аналитик** — отдельный независимый слой проекта, который после событий объединяет данные разных контуров по общей временной оси, восстанавливает причинную историю и отвечает на вопросы:

- что происходило на рынке;
- что видел Маяк;
- что понял Диспетчер;
- какой сигнал сформировала стратегия;
- какой контекст стратегия реально использовала;
- что произошло при исполнении;
- как развивалась позиция;
- чем закончилась сделка;
- какую сделку мы предотвратили;
- какую хорошую сделку мы потеряли;
- какой слой ошибся;
- какой слой был прав;
- где требуется отдельное исследование или новая версия.

Аналитик **не торгует** и **не управляет live-системой**.

---

# 2. Место Аналитика в общей архитектуре

Рабочий live-контур:

```text
EXTERNAL WORLD / EXCHANGES
          ↓
        MAYAK
          ↓
       DISPATCHER
          ↓
    TRADING STRATEGY
          ↓
 ENTRY / EXECUTION / RISK / EXIT
          ↓
       EXCHANGE
```

Отдельно:

```text
POSITION SUPERVISOR
наблюдает реальную открытую позицию после fill
```

Аналитический контур:

```text
BYBIT / EXTERNAL DATA
       ↓
MAYAK HISTORY
       ↓
DISPATCHER HISTORY
       ↓
STRATEGY / ENTRY HISTORY
       ↓
EXECUTION / POSITION / EXIT HISTORY
       ↓
          ANALYST
       ↓
STATISTICS / DIAGNOSIS / RESEARCH TASKS
       ↓
PROPOSED NEW VERSION
       ↓
SHADOW
       ↓
LIVE EQUIVALENCE
       ↓
MICRO_LIVE
       ↓
LIVE — только отдельным решением
```

Аналитик находится **сбоку и после факта**.

Он не является дополнительным торговым фильтром.

---

# 3. Главный принцип — анализ по временной линии

Для анализа основная координата — единая UTC-временная ось.

Пример:

```text
12:41:17.100  Bybit: liquidation event BTC
12:41:17.130  Bybit: liquidation event ETH
12:41:17.170  Bybit: sell flow acceleration
12:41:17.240  Mayak: liquidation phase = CASCADE
12:41:17.310  Mayak: breadth deteriorates
12:41:17.350  Dispatcher: M3_LONG_ENTRY = POOR
12:41:18.020  M3: LONG signal
12:41:18.025  Strategy: rejected
12:41:18.030  Counterfactual tracker: hypothetical trade starts
12:47:42.000  Hypothetical trade reaches -1.00%
```

Аналитик идёт по времени и подбирает связанные события из разных слоёв.

---

# 4. Время не является уникальным ID

В одну миллисекунду могут произойти:

- десятки или сотни сделок;
- множество ликвидаций;
- изменения стакана;
- события нескольких инструментов;
- несколько внутренних системных событий.

Поэтому каждое событие имеет собственный идентификатор:

```text
event_id
```

А время используется как общая координата анализа.

Минимальный контракт события:

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

payload_reference
```

---

# 5. Слои событий

Канонические значения `layer`:

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
```

При необходимости допускаются дополнительные специализированные слои, но они не должны разрушать этот верхнеуровневый контракт.

---

# 6. Аналитик не смешивает разные инструменты автоматически

Совпадение времени само по себе не доказывает влияние.

Пример:

```text
BTC liquidation cascade
```

не означает автоматически:

```text
ETH LONG был плохой сделкой
```

Аналитик обязан учитывать:

- symbol;
- market;
- venue;
- breadth;
- synchronization;
- BTC/ETH context;
- cross-asset relationship;
- actual price response конкретного инструмента.

Если ETH в момент сильной BTC-ликвидации сохранил структуру и вырос, это должно быть зафиксировано как реальный факт, а не подогнано под объяснение BTC.

---

# 7. TEMPORAL ASSOCIATION != CAUSAL EXPLANATION

Аналитик должен различать:

```text
TEMPORAL_ASSOCIATION
```

и:

```text
CAUSAL_HYPOTHESIS
```

и:

```text
VALIDATED_RELATION
```

## TEMPORAL_ASSOCIATION

События произошли близко по времени.

Этого недостаточно для причинного вывода.

## CAUSAL_HYPOTHESIS

Есть логически согласованная последовательность:

```text
ликвидации
→ поток
→ OI
→ ликвидность
→ breadth
→ price response
```

Но она ещё не подтверждена достаточным числом независимых случаев.

## VALIDATED_RELATION

Связь прошла заранее определённый статистический protocol, OOS/holdout и не является результатом подгонки.

---

# 8. OBSERVED_CONTEXT и CONSUMED_CONTEXT

Аналитик обязан различать два типа связи.

## OBSERVED_CONTEXT

Какой рыночный контекст объективно существовал в момент решения.

Например:

```text
mayak_snapshot_id = M123
dispatcher_assessment_id = D456
```

Это может быть добавлено причинным correlator после события:

```text
context_time <= decision_time
```

## CONSUMED_CONTEXT

Какой конкретный snapshot/assessment стратегия **реально прочитала и использовала** в live-решении.

Пример:

```text
consumed_dispatcher_assessment_id = D456
```

Наличие `OBSERVED_CONTEXT` не означает, что торговый код его видел.

---

# 9. Корреляция по времени

Для каждого торгового события Аналитик может строить временное окно:

```text
T - N ... T ... T + M
```

Например для Entry:

```text
T - 30m
T - 15m
T - 5m
T - 1m
T
T + 1m
T + 5m
T + 15m
T + 30m
```

Но признаки для оценки решения в `T` должны использовать только:

```text
event_time <= T
```

Будущее используется только для оценки outcome, никогда для реконструкции live-состояния.

---

# 10. Контрфактический трекер

Каждый отклонённый или заблокированный сигнал должен по возможности продолжать жить как виртуальная сделка.

Минимум сохранять:

```text
signal_id
hypothetical_entry_price
hypothetical_fill_model
initial_stop_model

MAE
MFE

first +0.5%
first +1%
first +3%
first +5%

first -0.5%
first -1%

72h path
final classification
```

Это позволяет оценивать не только спасённые убытки, но и потерянные хорошие сделки.

---

# 11. Четыре основных исхода защитного решения

Для любого veto / early-exit / hold modification Аналитик должен классифицировать результат минимум так:

```text
SAVED_LOSS
LOST_PROFIT
NEUTRAL_OR_LOW_IMPACT
UNRESOLVED
```

Дополнительно полезны:

```text
SAVED_FULL_STOP
SAVED_PARTIAL_LOSS
DESTROYED_RECOVERY
MISSED_RUNNER
AVOIDED_NOISE
```

---

# 12. Пример: Диспетчер правильно запретил вход

```text
M3 signal = LONG
Dispatcher M3_LONG_ENTRY = INCOMPATIBLE
Strategy consumes assessment
Trade rejected
Hypothetical path reaches -1.00%
```

Вывод Аналитика:

```text
decision_effect = SAVED_FULL_STOP
```

При условии, что:

- assessment действительно был `CONSUMED_CONTEXT`;
- hypothetical fill model соответствует стратегии;
- нет look-ahead;
- outcome посчитан после факта.

---

# 13. Пример: Диспетчер уничтожил хорошую сделку

```text
M3 signal = LONG
Dispatcher = POOR
Strategy consumes assessment
Trade rejected
Hypothetical path:
  +1.0%
  +3.0%
  MFE +4.2%
```

Вывод:

```text
decision_effect = LOST_PROFIT / MISSED_RUNNER
```

Это такой же важный результат, как предотвращённый убыток.

---

# 14. Экономика фильтра

Нельзя оценивать защитный слой только числом спасённых стопов.

Минимальная экономическая формула:

```text
saved_losses
- lost_good_trades
- destroyed_recoveries
- additional_fees
- additional_slippage
- additional_funding
= net_value
```

Для каждой стратегии считать отдельно.

---

# 15. Аналитик должен уметь определить, какой слой ошибся

Пример возможной диагностики:

```text
MAYAK:
market state measured correctly

DISPATCHER:
interpreted state incorrectly for M3

STRATEGY:
correctly obeyed Dispatcher

RESULT:
good trade rejected
```

Тогда предложение:

```text
review Dispatcher profile/version
```

Другой пример:

```text
MAYAK:
liquidation layer under-reported cascade

DISPATCHER:
correctly interpreted bad Mayak input

STRATEGY:
correctly used Dispatcher

RESULT:
full -1 stop
```

Тогда проблема относится к:

```text
Mayak source/feature layer
```

---

# 16. Аналитик не имеет права автоматически менять live-код

Запрещено:

```text
trade lost
→ automatically lower threshold
```

или:

```text
dispatcher wrong once
→ automatically modify profile
```

Правильный цикл:

```text
LIVE
 ↓
STATISTICS
 ↓
ANALYST
 ↓
RESEARCH HYPOTHESIS
 ↓
NEW VERSION
 ↓
SHADOW
 ↓
LIVE EQUIVALENCE
 ↓
MICRO_LIVE
 ↓
LIVE
```

Каждая версия принимается отдельно.

---

# 17. Аналитик не обучает Маяк результатами сделок

Маяк должен оставаться независимым наблюдателем внешней среды.

Запрещено:

```text
M3 lost
→ Mayak changes market state definition
```

Допустимо:

```text
M3 losses correlate with certain Mayak state
→ Analyst creates research task
→ separate research validates or rejects relation
```

---

# 18. Аналитик не обучает Диспетчер автоматически

Результаты сделок могут показать:

```text
Profile M3_LONG_ENTRY V1
too restrictive
```

Но профиль не переписывается автоматически.

Создаётся:

```text
M3_LONG_ENTRY_V2 candidate
```

который проходит новый research/shadow cycle.

---

# 19. Единая таблица связей

Рекомендуется отдельный логический объект:

```text
analytics.event_links
```

Пример полей:

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

# 20. Типы связей

Примеры:

```text
TEMPORALLY_NEAR
SAME_SYMBOL
SAME_MARKET
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

# 21. Link quality

Каждая автоматически построенная связь должна иметь качество:

```text
EXACT
STRONG
WEAK
UNKNOWN
```

Пример:

```text
same exchange order_id
```

может быть `EXACT`.

Пример:

```text
BTC liquidation 2 seconds before ETH move
```

может быть только `WEAK` temporal association до дополнительного анализа.

---

# 22. Временные уровни анализа

Аналитик должен поддерживать разные горизонты.

## MICRO

```text
milliseconds ... seconds
```

Используется для:

- orderbook;
- trade impact;
- liquidations;
- RPI;
- execution/slippage.

## SHORT

```text
1m ... 30m
```

Используется для:

- Entry;
- immediate adverse excursion;
- cascade;
- market regime transition.

## TRADE

```text
position lifetime
```

Используется для:

- Supervisor;
- MFE/MAE;
- Hold environment;
- Exit.

## SESSION

```text
hours / day
```

Используется для:

- cluster losses;
- strategy performance;
- market regime.

## RESEARCH

```text
weeks / months
```

Используется для:

- stability;
- OOS;
- portfolio behaviour;
- version comparison.

---

# 23. Дневной отчёт Аналитика

Аналитик может формировать ежедневный отчёт:

```text
MARKET DAY
MAYAK QUALITY
DISPATCHER QUALITY
STRATEGY QUALITY
EXECUTION QUALITY
RISK/EXIT QUALITY
MISSED OPPORTUNITIES
SAVED LOSSES
CLUSTERS
ANOMALIES
DATA QUALITY
```

---

# 24. Что Аналитик говорит про Маяк

Примеры:

```text
Mayak correctly detected broad cascade 4/4 cases
Mayak was late by median 42 seconds
Mayak liquidation coverage insufficient in 2 cases
Mayak Spot coverage LOW during 17% of session
```

Не использовать слово «прав» без заранее определённого критерия.

---

# 25. Что Аналитик говорит про Диспетчер

Примеры:

```text
M3_LONG_ENTRY vetoes: 14
saved full stops: 4
lost +1 trades: 7
neutral: 3
net_value: negative
```

или:

```text
M3_LONG_HOLD warnings:
median lead before hard stop = 3m 12s
false warning rate = ...
```

---

# 26. Что Аналитик говорит про стратегию

Примеры:

```text
M3 setup quality stable
entry geometry unchanged
loss cluster concentrated in market-wide stress
```

или:

```text
M3 loses quality in high synchronization regimes
```

Такой вывод становится research hypothesis, а не live rule.

---

# 27. Что Аналитик говорит про Execution

Примеры:

```text
requested vs actual fill
slippage
fees
rejections
latency
partial fill
cancel pending
reconciliation
```

Отделять ошибку рыночной идеи от ошибки исполнения.

---

# 28. Что Аналитик говорит про Exit

Примеры:

```text
hard -1 reached after environment was already broken
```

или:

```text
early-exit candidate would have destroyed recovery
```

Exit должен оцениваться после costs.

---

# 29. Кластеры

Аналитик обязан отдельно анализировать:

```text
5m
15m
30m
60m
```

кластеры:

- стопов;
- сигналов;
- ликвидаций;
- market regime transitions;
- simultaneously exposed strategies.

---

# 30. Портфельный уровень

Signal replay не является portfolio backtest.

Аналитик должен отдельно учитывать:

```text
chronology
simultaneous positions
capital
margin
correlated exposure
conflicts
one-position-per-symbol
fees
slippage
funding
```

---

# 31. Data quality — часть вывода

Любой вывод Аналитика должен включать качество данных:

```text
COMPLETE
PARTIAL
LOW_COVERAGE
STALE_SOURCE
MISSING_LAYER
REPLAY_ONLY
```

Нельзя делать сильный вывод, если критический источник отсутствовал.

---

# 32. Версионирование

Каждый отчёт Аналитика должен фиксировать:

```text
analyst_version
project_commit
source_tree_fingerprint

database_schema_version

mayak_version
dispatcher_version

strategy_version
profile_version

entry_version
risk_version
exit_version
execution_version

dataset_fingerprint
period
symbols
sample_size
completeness
```

---

# 33. Машинная истина и представление

Рекомендуется:

```text
PostgreSQL = production truth
JSONL = compact event archive
CSV/Parquet = research extracts
Markdown = human report
```

Markdown не является первичной статистической истиной.

---

# 34. Аналитические выборки должны быть адресными

Не нужно для каждого вопроса загружать весь проект.

Пример:

## Анализ полного -1 M3

Подтягивается:

```text
selected M3 trade
±30m Bybit/Mayak
±30m Dispatcher
full position lifetime
Supervisor
Execution
Exit
counterfactuals
```

## Анализ Маяка за 7 дней

Подтягивается:

```text
Mayak
source quality
market data
Dispatcher optional
```

Без Execution всех сделок.

---

# 35. Архивы должны поддерживать Аналитика

Рекомендуемые уровни:

```text
CODE
LIVE_EVIDENCE
POSTGRESQL_BACKUP
RESEARCH
LOGS
```

А внутри `LIVE_EVIDENCE`:

```text
exchange/
mayak/
dispatcher/
strategies/
entry/
execution/
supervisor/
exit/
analytics/
```

Все слои связываются по UTC + IDs.

---

# 36. Будущий Analyst service

Первая версия Аналитика может быть offline/research tool.

Не требуется сразу делать постоянный daemon.

Позднее возможны:

```text
analyst.daily
analyst.session_report
analyst.event_correlator
analyst.counterfactual_tracker
analyst.version_comparison
```

---

# 37. Fail-closed семантика Аналитика

Если нужный слой отсутствует:

```text
NO_DATA
```

Если связь не доказана:

```text
UNCONFIRMED
```

Если outcome ещё не известен:

```text
UNRESOLVED
```

Если выборка недостаточна:

```text
INSUFFICIENT_SAMPLE
```

Нельзя выдавать предполагаемый вывод как доказанный.

---

# 38. Главная роль Аналитика в рабочем цикле

Полный цикл проекта:

```text
EXCHANGE
   ↓
MAYAK
   ↓
DISPATCHER
   ↓
STRATEGY
   ↓
EXECUTION / POSITION / EXIT
   ↓
STATISTICS
   ↓
ANALYST
   ↓
DIAGNOSIS
   ↓
RESEARCH TASK
   ↓
NEW VERSION
   ↓
SHADOW
   ↓
MICRO_LIVE
   ↓
LIVE
```

---

# 39. Принцип ответственности слоёв

Аналитик должен всегда пытаться ответить:

```text
Где возникла ошибка?
```

Возможные классы:

```text
SOURCE_DATA
MAYAK_MEASUREMENT
DISPATCHER_INTERPRETATION
STRATEGY_LOGIC
ENTRY_LOGIC
EXECUTION
POSITION_SUPERVISION
RISK
EXIT
DATA_QUALITY
UNKNOWN
```

---

# 40. Итог

Аналитик — это не ещё один торговый бот.

Он является **контуром доказательного разбора всей системы**.

Его задача:

> восстановить временную историю, связать события разных слоёв, отделить наблюдаемую корреляцию от доказанной зависимости, измерить фактическую экономическую пользу решений и определить, какой слой требует отдельного исследования или новой версии.

Главный принцип:

> **Маяк наблюдает рынок. Диспетчер интерпретирует среду для типов торговли. Стратегия принимает торговое решение. Аналитик после факта проверяет, насколько каждый слой был полезен и где система ошиблась.**
