# DISPATCHER V2 — ИТОГИ ЭТАПА D8 OBSERVED CONTEXT

**Документ:** `DISPATCHER_V2_D8_STAGE_RESULTS_RU.md`
**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** LEVEL 5 · production/runtime evidence; не торговая policy

Верхние и implementation-контракты:

- `../CRIPTA_ARCHITECTURE_RULES_RU_V1.md`;
- `PROJECT_ARCHITECTURE_RU.md`;
- `STRATEGY_DISPATCHER_ARCHITECTURE_RU.md`;
- `DISPATCHER_V2_D8_OBSERVED_CONTEXT_RU.md`;
- `SIGNAL_LIFECYCLE_CONTRACT_RU.md`.

```text
DISPATCHER_V2_OBSERVED_CONTEXT = YES
DISPATCHER_V2_CONSUMED_CONTEXT = NO
DISPATCHER_TRADING_EFFECT      = NONE
ENTRY_POLICY_CHANGED           = NO
EXIT_POLICY_CHANGED            = NO
```

## 1. Source checkpoint

D8 implementation опубликован в GitHub:

```text
6d6bfd035128bac090809127d9c9e56e4cf2ce98
dispatcher: add D8 observed context correlator
```

Перед закрытием этапа подтверждено:

```text
REMOTE_HEAD = 6d6bfd035128bac090809127d9c9e56e4cf2ce98
SOURCE_HEAD = 6d6bfd035128bac090809127d9c9e56e4cf2ce98
WORKTREE    = clean
```

## 2. Production runtime

Новый пассивный сервис:

```text
cripta-dispatcher-v2-context-correlator.service
active
enabled
```

Его installed source provenance:

```text
DISPATCHER_V2_CORRELATOR_SOURCE_COMMIT=
6d6bfd035128bac090809127d9c9e56e4cf2ce98
```

Legacy-сервисы остаются выключены:

```text
cripta-strategy-dispatcher.service         inactive / disabled
cripta-causal-context-correlator.service inactive / disabled
```

## 3. Persisted truth

D8 пишет только append-only связь:

```text
research_context.dispatcher_v2_event_links
```

Ключ уникальности:

```text
(event_type, reference_id)
```

UPDATE/DELETE запрещены storage contract. Legacy `research_context.event_links`
не является input нового correlator.

## 4. Реальный runtime checkpoint

Контрольный снимок 2026-09-06 после production deploy:

```text
TOTAL                  23
SIGNAL                 10
ENTRY_DECISION         10
EXECUTION               1
SUPERVISOR_TRANSITION   2
```

Причинность и режим:

```text
negative global age       0
negative coin age         0
negative capacity age     0
NOT_CONSUMED             23 / 23
trading_effect=NONE      23 / 23
duplicates                0
coin/global mismatch      0
```

Качество связей:

```text
GLOBAL_COIN_CAPACITY_CAUSAL_PRIOR       19
GLOBAL_COIN_CAUSAL_PRIOR_NO_CAPACITY     4
```

Максимальный возраст объективного context в момент события:

```text
GlobalMarketContext       540.059 s
CoinMarketContext         540.059 s
TradingCapacitySnapshot     3.246 s
```

Эти возраста сохраняются как фактическое качество observed-context. D8 не имеет
права подставлять более новый snapshot задним числом.

## 5. Exact-ID дисциплина

Для `SIGNAL`, `ENTRY_DECISION` и текущих `SUPERVISOR_TRANSITION` точный
`signal_id` присутствовал во всех наблюдавшихся D8 links.

Один фактический `EXECUTION` на контрольной точке не имел доказанной exact-ID
цепочки к `signal_id/position_id/trade_id`. D8 оставил эти поля `NULL` и **не**
восстанавливал ownership по `symbol + ближайшее время`. Это правильное
fail-honest поведение по lifecycle contract, а не ошибка, которую разрешено
маскировать эвристикой.

## 6. Gate, выполненный перед production deploy

Для D8 implementation был выполнен:

```text
Ruff                         PASS
mypy --strict                PASS
PostgreSQL disposable E2E    PASS
real production source-union PASS
full pytest                  1080 passed / 8 skipped
```

Synthetic D8 end-to-end отдельно подтвердил causal selection, exact linkage на
доступных fixture IDs, `NOT_CONSUMED`, `NONE` и idempotent повторный запуск.

## 7. Итог D8

```text
D8_SCHEMA                 = COMPLETE
D8_CORRELATOR             = COMPLETE
D8_ARCHIVE_READ_MODEL     = COMPLETE
D8_GITHUB_CHECKPOINT      = COMPLETE
D8_PRODUCTION_DEPLOY      = COMPLETE
D8_RUNTIME_EVIDENCE       = PASS
D8_TRADING_EFFECT         = NONE
D8_CONSUMED_CONTEXT       = NO
```

D8 закрыт. Новый Dispatcher теперь накапливает причинную историю того, **какой
объективный global/coin/capacity context существовал в момент события**, не
выдавая это за доказательство использования контекста Strategy.

## 8. Следующий research gate

`CoinMarketRating` ещё не разрешено фитить по seen frozen ALL9. По завершённому
MAYAK V2 stage сначала требуется новый temporal/cross-asset OOS с теми же frozen
component definitions, без retuning на новых данных, затем owner review.

Только после этого допускается отдельный `CoinMarketRating` research contract.
Live Strategy/Entry/Exit на этом этапе не меняются.
