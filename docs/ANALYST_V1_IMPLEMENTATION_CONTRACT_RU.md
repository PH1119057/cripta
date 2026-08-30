# АНАЛИТИК V1 — КОНТРАКТ ПЕРВОЙ РЕАЛИЗАЦИИ

**Документ:** `ANALYST_V1_IMPLEMENTATION_CONTRACT_RU.md`  
**Версия:** 1.0  
**Дата:** 2026-08-30  
**Статус:** implementation contract, без live trading rights  
**Зависимости:** `ANALYST_ARCHITECTURE_RU.md`, `DATA_TIMELINE_CONTRACT_RU.md`

---

## 1. Цель V1

Первая версия Аналитика должна доказательно соединять уже существующие события и давать воспроизводимый постфактум ответ:

- какой market context существовал;
- что видел Маяк;
- что сформировал Диспетчер;
- что решила стратегия;
- что реально использовалось;
- что произошло с потенциальной/реальной сделкой;
- какой слой требует исследования.

V1 ничего не меняет в live trading.

---

## 2. Режим

Первая реализация:

```text
OFFLINE / BATCH / READ-ONLY
```

Не требуется daemon.

Допустимо запускать:

```text
analyst correlate ...
analyst trade-report ...
analyst day-report ...
analyst counterfactual ...
```

---

## 3. V1 Modules

Минимальные компоненты:

```text
analyst/
  contracts.py
  event_reader.py
  correlator.py
  counterfactual.py
  diagnostics.py
  reports.py
  storage.py
  cli.py
```

Имена ориентировочные; границы ответственности обязательны.

---

## 4. Input adapters

V1 читает:

- PostgreSQL;
- compact JSONL из архива;
- Mayak snapshots/journal;
- Dispatcher assessments;
- strategy/Entry events;
- fills/positions/exits.

Если источник отсутствует — `NO_DATA`.

---

## 5. Unified Event

Внутри Аналитика все входы преобразуются к Event Envelope из `DATA_TIMELINE_CONTRACT_RU.md`.

Не требуется физически переписывать исходные таблицы в одну огромную таблицу.

---

## 6. Первая PostgreSQL schema

Рекомендуется:

```text
analytics.event_links
analytics.analysis_runs
analytics.counterfactuals
analytics.diagnoses
```

---

## 7. analytics.analysis_runs

Минимум:

```text
analysis_run_id
started_at
finished_at

analyst_version
project_commit
source_fingerprint

analysis_type
parameters_json

source_period_start
source_period_end

input_fingerprints
status
error
```

---

## 8. analytics.event_links

Минимум:

```text
link_id
analysis_run_id

source_layer
source_event_id

target_layer
target_event_id

relation_type
time_delta_ms

symbol_relation
scope_relation

link_quality
link_method

created_at
```

---

## 9. Correlator V1

Должен уметь:

1. выбрать target event;
2. определить symbol/market/scope;
3. выбрать causal окно назад;
4. найти latest valid Mayak snapshot;
5. найти latest valid Dispatcher assessment;
6. построить соседние Exchange/market events;
7. отдельно выбрать future outcome;
8. не смешивать future с causal context.

---

## 10. Корреляция не только nearest timestamp

Алгоритм учитывает:

```text
time
symbol
venue
market_type
scope
quality
freshness
```

Пример:

BTC liquidation может стать `MARKET_WIDE_CONTEXT` ETH сделки только если market-wide derived data подтверждают распространение.

---

## 11. OBSERVED_CONTEXT builder

Для каждого:

```text
signal
entry decision
fill
exit
```

может строить постфактум:

```text
observed_mayak_snapshot_id
observed_dispatcher_assessment_id
```

с quality/method.

Это не меняет исходную торговую запись молча.

---

## 12. CONSUMED_CONTEXT reader

Аналитик только читает реальные live references.

Если trading runtime не записал consumed context:

```text
CONSUMED_CONTEXT = NONE / NOT_AVAILABLE
```

Нельзя его восстанавливать догадкой.

---

## 13. Counterfactual V1

Первый объект:

```text
counterfactual_id
signal_id
decision_id

reason_not_executed
hypothetical_fill_model_version

entry_price
side
qty_model

horizon

mae
mfe

first_stop_time
first_target_times
final_classification
```

---

## 14. Контрфактический трекер не торгует

Он никогда не создаёт:

- Bybit order;
- reservation;
- risk exposure.

Это research/statistics only.

---

## 15. Outcome categories

V1:

```text
SAVED_FULL_STOP
SAVED_PARTIAL_LOSS
LOST_PROFIT
MISSED_RUNNER
DESTROYED_RECOVERY
NEUTRAL_OR_LOW_IMPACT
UNRESOLVED
INSUFFICIENT_DATA
```

---

## 16. Диагностика слоя

`analytics.diagnoses` должна уметь классифицировать:

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

Это hypothesis/diagnosis, а не автоматический patch request.

---

## 17. Diagnosis evidence

Любой диагноз хранит:

```text
diagnosis_id
analysis_run_id
subject_id

category
confidence

supporting_event_ids
contradicting_event_ids

human_summary
machine_facts_json
```

---

## 18. Не писать «Маяк был прав» без критерия

Формулировки V1:

```text
Mayak state preceded X in N cases
median lead = ...
coverage = ...
false association = ...
```

а не эмоциональное `correct=true` без определения.

---

## 19. Dispatcher report V1

Для каждого profile/version:

```text
assessment_count
veto/adverse_count
saved_full_stops
lost_positive_trades
missed_runners
unresolved

net_value_model
confidence distribution
data_quality distribution
```

---

## 20. Net Value

Минимум:

```text
saved_losses
- lost_good_trades
- destroyed_recoveries
- additional_fees
- additional_slippage
- additional_funding
```

Не считать nominal avoided stop единственной пользой.

---

## 21. M3 report V1

Отдельно:

```text
M3_LONG_ENTRY
M3_SHORT_ENTRY
M3_LONG_HOLD
M3_SHORT_HOLD
```

Даже если пока profiles SHADOW.

---

## 22. HOLD analysis

Для реальной позиции:

```text
fill_time
hard_stop_time
hold_environment deterioration time
Supervisor warning time
actual exit time
```

Считать:

```text
lead_before_full_stop
pnl_at_warning
pnl_at_environment_break
future_recovery
future_mfe
```

---

## 23. Cluster report

V1 должен иметь:

```text
5m
15m
30m
60m
```

и группировать:

- full stops;
- liquidations;
- Dispatcher adverse assessments;
- regime changes.

---

## 24. Daily report

Минимальный Markdown + machine JSON:

```text
DATA QUALITY
MARKET
MAYAK
DISPATCHER
STRATEGIES
EXECUTION
POSITIONS
EXITS
SAVED LOSSES
LOST OPPORTUNITIES
CLUSTERS
ANOMALIES
OPEN QUESTIONS
```

---

## 25. Machine truth

Каждый отчёт имеет:

```text
*.json
```

Markdown строится из JSON.

---

## 26. Replay deterministic

Одинаковый:

```text
inputs + versions + parameters
```

должен давать одинаковый результат.

---

## 27. Provenance

Каждый run:

```text
analyst_version
project_commit
source_tree_fingerprint
db_schema
mayak_version
dispatcher_version
profile_versions
strategy_versions
period
symbols
row_counts
completeness
```

---

## 28. Missing data

Если отсутствует ликвидационный слой:

```text
liquidation_analysis = NO_DATA
```

Аналитик не должен заменять его price proxy.

---

## 29. V1 Safety

Аналитик не импортирует live private exchange client для мутаций.

Запрещены:

```text
place_order
amend_order
cancel_order
set_stop
set_leverage
```

---

## 30. V1 Acceptance

Готовность означает:

1. causal timeline для одной реальной сделки;
2. timeline для одного rejected signal;
3. exact OBSERVED vs CONSUMED semantics;
4. контрфактический path;
5. machine JSON;
6. reproducible report;
7. no trading mutation;
8. tests;
9. archive export.

---

## 31. Что V1 НЕ делает

Не делает:

- automatic retraining;
- optimizer;
- automatic profile rewrite;
- live veto;
- live exit;
- portfolio allocator;
- causal ML.

---

## 32. V2 позже

После доказанного V1:

- scheduled daily service;
- richer causal graph;
- portfolio attribution;
- multi-exchange lead/lag;
- statistical significance library;
- automated research candidate generation.

Но не автоматическая live-активация.

---

## 33. Итог

Analyst V1 — это **детерминированный read-only correlator + counterfactual evaluator + diagnostic reporter**.

Его первая задача — не быть «умным AI», а сделать историю системы доказательной и воспроизводимой.
