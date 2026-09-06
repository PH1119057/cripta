# DISPATCHER V2 — D8 OBSERVED CONTEXT CONTRACT

**Документ:** `DISPATCHER_V2_D8_OBSERVED_CONTEXT_RU.md`
**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** LEVEL 4 · активный implementation contract
**Основание:** утверждённый roadmap владельца после завершения D0–D7.

## 1. Цель

D8 причинно связывает объективные Dispatcher V2 contexts с фактическими signal/lifecycle событиями для Analyst, audit и UI/read-model.

```text
event at T
  ↓
latest GlobalMarketContext observed_at <= T
+ exact-symbol CoinMarketContext того же global source snapshot
+ latest TradingCapacitySnapshot observed_at <= T
  ↓
OBSERVED_CONTEXT link
```

Это пассивная аналитическая связь.

## 2. OBSERVED не равно CONSUMED

До отдельного owner-approved Strategy consumer cutover:

```text
DISPATCHER_V2_OBSERVED_CONTEXT = YES
DISPATCHER_V2_CONSUMED_CONTEXT = NO
TRADING_EFFECT = NONE
```

Наличие V2 context рядом с Entry decision не означает, что Strategy его читала или использовала.

## 3. Новый clean read-model

Persisted truth D8:

```text
research_context.dispatcher_v2_event_links
```

Старый `research_context.event_links` и `strategy_dispatcher.*` не являются input D8 и не обновляются. Они остаются историческим audit.

## 4. События

D8 минимум связывает:

- `SIGNAL` — `monitoring.opportunities.signal_id`;
- `ENTRY_DECISION` — `runtime.entry_decisions.signal_id`;
- `TRADE_COMMAND` — `runtime.trade_commands.command_id`;
- `EXECUTION` — `runtime.executions.exec_id`;
- `POSITION_LIFECYCLE` — `runtime.position_lifecycle_events.lifecycle_event_id`;
- `SUPERVISOR_TRANSITION` — `supervisor.transitions.id`;
- `EXIT_DECISION` — `supervisor.exit_decisions.decision_id`.

Root `signal_id`, `position_id`, `trade_id` используются только когда доступны через точные IDs/`runtime.position_ownership`/command lineage. Запрещено восстанавливать ownership по `symbol + ближайшее время`.

## 5. Причинность

Для event time `T`:

```text
global.observed_at <= T
coin.observed_at <= T
capacity.observed_at <= T
```

Coin context выбирается по exact `symbol` и `global_context_id`, чтобы Global/Coin относились к одному source MAYAK snapshot.

Возраст каждого context в момент события хранится отдельно. Stored freshness, рассчитанная при создании Dispatcher context, не подменяет event-time age.

## 6. Link quality

Минимально различать:

```text
GLOBAL_COIN_CAPACITY_CAUSAL_PRIOR
GLOBAL_COIN_CAUSAL_PRIOR_NO_CAPACITY
GLOBAL_CAPACITY_CAUSAL_PRIOR_NO_COIN
GLOBAL_ONLY_CAUSAL_PRIOR
```

Отсутствие Coin/Capacity не превращается в нейтральное значение. Событие до первого V2 Global context не получает фиктивную связь.

## 7. Immutable / append-only

`research_context.dispatcher_v2_event_links` append-only. Runtime role получает `SELECT/INSERT`, но не `UPDATE/DELETE`. Restart не создаёт дубликаты по `(event_type, reference_id)`.

## 8. Initial backfill boundary

При первом запуске correlator разрешено связать persisted события начиная только с первого существующего Dispatcher V2 Global context. Это causal backfill уже состоявшейся объективной истории, а не реконструкция несуществовавшего V2 до его запуска.

## 9. UI / Analyst

D8 добавляет отдельный V2 signal/lifecycle export. Исторический legacy export не используется как источник нового V2 анализа. UI/read-model читает PostgreSQL; собственную историю UI не хранит.

## 10. Что D8 не делает

- не меняет Strategy/Entry/Exit;
- не добавляет `CoinMarketRating`;
- не создаёт ALLOW/BLOCK/LONG/SHORT;
- не читает future outcome при выборе context;
- не изменяет legacy assessments;
- не выдаёт `OBSERVED_CONTEXT` за `CONSUMED_CONTEXT`;
- не вызывает Execution/private mutation API.

## 11. Завершение D8

D8 считается завершённым после: schema + correlator + archive/read-model + full gate + GitHub checkpoint + passive production deploy + runtime evidence на реальных новых signal/lifecycle событиях.
