# Текущее устройство и архитектурные границы проекта CRIPTA

**Документ:** `CURRENT_PROJECT_MAP_RU.md`
**Версия документа:** 6.3
**Дата:** 2026-09-12
**Статус:** краткая текущая карта; не отдельный архитектурный контракт

## 1. Source checkpoint

Текущий source checkpoint определяется фактически проверенным равенством:

```text
GitHub PH1119057/cripta:main
==
/srv/cripta/source_checkout
```

Последний runtime checkpoint хранится отдельно и не считается автоматически текущим состоянием.

## 2. Верхняя прикладная архитектура

```text
MAYAK
  ↓
DISPATCHER
  ↓
STRATEGY
 ├─ ENTRY
 └─ EXIT
  ↓
EXECUTION
  ↓
EXCHANGE
```

Пять верхних уровней.

`Risk` не является отдельным верхним слоем.

## 3. Два контура

### Прикладной

Определяет смысл: что происходит на рынке, какова общая обстановка, какая Strategy policy применяется, разрешён ли вход, как сопровождать позицию и какое действие требуется.

### Технический поддерживающий

Обеспечивает market/account connectivity, exchange adapters, private account sync, clock/reconnect, PostgreSQL, IDs/audit, Position Supervisor, Analyst, UI/read models, services, restart/reconciliation и operational safety.

Технический контур поддерживает прикладной, но не становится владельцем Strategy.

## 4. MAYAK

Наблюдает внешний рынок. Trading effect: `NONE`.

Текущая source-реализация objective context: `mayak-v2.2` /
`objective-coin-context-v2`. Она добавляет strategy-agnostic
`CoinMarketContext` для каждого наблюдаемого инструмента и сохраняет его append-only
в `mayak_v2.coin_market_contexts`. Внутри MAYAK не вычисляется Strategy-specific
пригодность монеты и не принимается решение LONG/SHORT.

Live и historical replay используют один `LiveMayakEngine`; replay только причинно
подаёт нормализованные события в тот же движок. Исторический replay без точного raw
источника ликвидаций обязан сохранять этот слой как `NO_DATA`, а не выводить ложное
`NONE`.

`CoinMarketRating` остаётся объектом Dispatcher поверх объективных MAYAK-фактов;
формула рейтинга в MAYAK не зашивается. Состояние установленного/загруженного runtime
проверяется отдельно от source checkpoint.

MAYAK V2 historical stage 2026-09-06 завершён отдельным evidence report
`MAYAK_V2_STAGE_RESULTS_RU.md`: exact causal replay по frozen ALL9/1063 включает
derivatives/Spot executed flow, OI, funding/mark/index premium, account ratio и
derivatives depth-200 liquidity. Frozen component research = 251 признак;
`CoinMarketRating` не фитился, live Entry policy не менялась. Следующий research gate —
новый temporal/cross-asset OOS с теми же frozen definitions.

OOS protocol V1 из `MAYAK_COMPONENT_OOS_CONFIRMATION_V1_RU.md` полностью выполнен на NEW15/14024 signals с BTC/ETH только как reference-only. Итог зафиксирован в `MAYAK_COMPONENT_OOS_RESULTS_V1_RU.md`: `CONFIRMED=1`, `MIXED=11`, `REJECTED=0`, `INSUFFICIENT_DATA=0`. Единственный подтверждённый frozen effect — более низкий entry-aligned long/short crowding для исхода `+1.10% раньше -1.00%` (directional AUC 0.53225, ожидаемый знак 12/15 активов, frozen Q1 53.33% против Q4 44.50%). `CoinMarketRating` по-прежнему не фитился; следующий обязательный рубеж — owner review, затем отдельный rating research contract.

## 5. Dispatcher

Публикует три strategy-agnostic класса показателей:

1. объективный global market context;
2. объективный per-coin context / `CoinMarketRating`;
3. состояние торговой ёмкости аккаунта.

Dispatcher не знает тип Entry и не определяет пригодность рынка за конкретную Strategy. Интерпретация принадлежит Strategy.

Account capacity минимум:

```text
total
used
reserved
free
available_for_new_trading
freshness
source_exchange/account
```

Источник фактов — подключённая торговая площадка через technical account-sync.

D0–D7 implementation: `dispatcher-v2.1`; persisted truth — `dispatcher_v2.global_market_contexts`, `dispatcher_v2.coin_market_contexts`, `dispatcher_v2.trading_capacity_snapshots`; runtime — `cripta-dispatcher-v2.service`. Формула `CoinMarketRating` отложена до следующего research-этапа.

## 6. Strategy

Канонический специализированный контракт: `STRATEGY_ENTRY_ARCHITECTURE_RU.md`.

Strategy — owner-approved immutable `StrategyCard`, то есть пассивная карточка всей торговой policy, а не бот/runtime.

В Strategy находятся:

```text
ENTRY POLICY
EXIT POLICY
CAPITAL / LEVERAGE POLICY
PROTECTION / HOLDING POLICY
TOUCH / LIFECYCLE / RESET POLICY
MAYAK-origin / DISPATCHER CONTEXT CONSUMPTION POLICY
```

Все торговые числа и timers принадлежат Strategy.

`StrategyActivation` хранится отдельно и позволяет владельцу независимо включать/выключать любое количество Strategy. Противоречащие Strategy допустимы.

Из StrategyCard materialize-ятся immutable `EntryPlan` и `ExitPlan` с fingerprint.

## 7. Entry

Целевая архитектура Entry — один universal parameterized Entry Engine для любого числа активных EntryPlan.

```text
Active Plan Registry
-> Entry Watch
-> StrategySignal
-> Entry Decision
-> optional ExecutionRequest
```

Entry не выбирает, не ранжирует и не выключает Strategy. Рыночный Monitor/Scanner является источником causal market facts, а не владельцем strategy-specific торгового сигнала.

Канонический `signal_id` — strategy-specific StrategySignal, который Entry Watch создаёт при выполнении конкретного EntryPlan.

Candidate cooldown не является свойством Entry. В том числе исторические `30 минут` V1 являются отключаемой Strategy-настройкой, а не универсальным правилом.

Отказ из-за отсутствия денег:

```text
INSUFFICIENT_AVAILABLE_FUNDS
```

### 7.1 Текущий implementation status

Production Entry по-прежнему реализует историческую V1-specific модель: в коде присутствуют фиксированные/default 30m candidate cooldown, 60m failure embargo, обязательный `pressure_then_reversal` и OI calibration/tail gate.

Это **implementation finding относительно новой целевой архитектуры**, а не разрешение менять production автоматически. Universal Entry consumer cutover ещё не реализован.

Отдельная clean-реализация universal Strategy/Entry уже имеет следующие green stages:

```text
U1/U2
= immutable Strategy/Activation/Plan contracts
+ generic parameterized Entry DSL/engine/registry
+ exact StrategySignal/Attempt/Decision lineage in memory

U3
= PostgreSQL schema strategy_entry
+ immutable StrategyCard / EntryPlan / ExitPlan storage
+ separately mutable StrategyActivation with append-only activation journal
+ exact StrategySignal -> StrategyAttempt -> EntryDecision -> optional ExecutionRequest storage
+ separate OBSERVED_CONTEXT / CONSUMED_CONTEXT and observed/consumed sensor links

U4
= generic parameterized causal market watch inside Entry Watch
+ explicit causal cooldown anchor without hidden defaults
+ generic post-signal Entry lifecycle / optional future-entry embargo
+ forensic V1 compatibility StrategyCard for exactly 10 trading symbols
+ exact frozen OI calibration provenance
+ deterministic causal-sequence parity runner against canonical legacy EntrySymbolEngine
```

`strategy_entry` установлена в PostgreSQL с owner `postgres`; runtime role `cripta` имеет только минимальные SELECT/INSERT и narrow UPDATE для `strategy_activations`, без DELETE и без UPDATE immutable entities.

U4 не переносит ownership raw market-data/feed normalization в Entry: technical sensor contour поставляет нормализованные causal facts, а Entry Watch только интерпретирует их по конкретному EntryPlan. Generic post-signal lifecycle относится только к будущим Entry и не является stop/TP/Exit сопровождением позиции.

V1 compatibility card содержит historical V1 значения только как Strategy/EntryPlan data. Trading scope = ровно 10 legacy `WORKING_SYMBOLS`; дополнительные BTC/ETH/DOGE/1000PEPE строки frozen calibration artifact scope не расширяют. Calibration provenance SHA-256: `b977bd42d76800a3eac63e42f67da7b75ecbf14e93c88761ff674cb084a32571`.

Deterministic U4 parity проверяет candidate/time, direction, geometry, touch, cooldown anchor/state, 5m/15m/60m causal readiness, shock/reset, rolling swing, pressure/reversal, OI result, StrategySignal presence/absence, favorable/adverse post-signal resolution, future-entry embargo и causal refs. Legacy current shadow scanner не используется как parity baseline.

U5 source добавляет отдельный parallel parity-shadow runtime `cripta-universal-entry-shadow.service`. Один public-only technical adapter нормализует causal REST/WS facts один раз и передаёт тот же `MarketFactEnvelope` passive canonical `EntrySymbolEngine` reference и Universal Entry + frozen V1 EntryPlan. Текущий изменённый `entry_shadow_scanner.py` reference не является. Online evidence хранится только в `strategy_entry.shadow_parity_runs/events`; pure `ExecutionRequest` downstream consumer не имеет.

U5 startup/restart fail-honest: каждый новый service instance начинает `WARMUP`; неизвестное pre-start Entry lifecycle influence истекает по horizon, вычисленному из EntryPlan. Для frozen V1 это 420 минут. Незавершённый run при restart не продолжается через неизвестный socket gap и финализируется `NOT_COMPARABLE`; новый run получает новую identity. Exact local fact journal служит evidence/diagnostics, а не способом скрыто восстановить continuity по времени.

U5 transport-continuity repair source-stage добавляет in-process public WebSocket reconnect без изменения Strategy/V1/EntryPlan/comparator semantics и без сброса `parity_run_id/started_at`, но только когда causal continuity доказана exact. `PUBLIC_TRADE` gap восстанавливается только через exact `execId + seq` anchor и public recent-trade window; `CANDLE_CLOSED` — по exact 5m/15m/60m boundaries; repeated `BAR_OPEN` дедуплируется только по exact source identity/boundary. Current `BYBIT_PUBLIC_NORMALIZED_U5_V1_OI30S` не имеет historical 30s replay: 5m OI history не считается эквивалентом. Поэтому continuation допустим только если все required ticker subscriptions восстановлены раньше earliest `last accepted OI30S + 30s`; иначе run fail-closed переходит в `NOT_COMPARABLE`. Disconnect/reconnect/continuity verdict сохраняются как append-only technical evidence и не являются trading facts. Наличие этого source-stage repair в `main` после публикации само по себе не доказывает installed/loaded runtime; deploy checkpoint проверяется отдельно.

Owner decision 2026-09-10 вводит отдельную новую technical source identity `BYBIT_PUBLIC_REST_CURRENT_OI_30S_V1`: один public current-tickers linear REST poll на каждый 30-second source slot, 10/10 frozen symbols, causal availability по фактическому response receive time, exact slot+symbol dedup, без 5m substitution/interpolation/carry-forward. Старый `BYBIT_PUBLIC_NORMALIZED_U5_V1_OI30S` остаётся historical evidence и не переименовывается. До нового U7 run новая source обязана пройти отдельный natural source-only soak >=480 минут с `missed_slots=0`, `incomplete_slots=0`, `silent_gaps=0`. На текущем source-stage soak/deploy/U7 PASS ещё НЕ объявлены.

Фактический source-only soak новой OI identity завершён PASS: 960/960 complete 30s slots, 10/10 symbols, `missed=0`, `incomplete=0`, `silent_gaps=0`, max delivery delay 2.603690s. Последующие fresh parity runs показали следующий independent blocker: полный silent WS market-data stall при локально открытом socket; `PUBLIC_TRADE` и candle cursors могли замереть до выпадения exact recent-trade anchor. Owner-approved 2026-09-11 source-stage repair добавляет application ping/pong watchdog (10s interval / 5s deadline), per-symbol 10s `PUBLIC_TRADE` silence audit через bounded public recent-trade и уточняет exact same-seq replay: уже принятые exact `execId` текущей anchor sequence исключаются до ambiguity check, различимые timestamps задают порядок; одинаковый timestamp с различающимися decision-affecting trade semantics остаётся fail-closed. Дополнительно repair использует independent publicTrade-only mirror WS только как exact recovery evidence: mirror не кормит engines при healthy primary, хранит received order одной непрерывной epoch и позволяет восстановить same-timestamp/same-seq gap без недокументированной REST сортировки; при отсутствии mirror anchor остаётся строгий REST fail-closed fallback. Strategy/V1/EntryPlan/comparator semantics не меняются. U7 остаётся `EVIDENCE_INCOMPLETE` до нового clean natural 420m run после published/deployed repair.

U6 source добавляет PostgreSQL-backed `Strategy` dashboard read-model/control: независимый список exact Strategy versions, read-only StrategyCard с полными `strategy_config_fingerprint` / `entry_plan_fingerprint` / `exit_plan_fingerprint`, реальные policy sections, Activation state/history и отдельный create-new-version flow. Missing persisted Activation/Plan показывается как `NOT SET`, а не как OFF/zero/neutral.

U6 не создаёт draft/approval layer. Новая version создаётся только как новая immutable StrategyCard через canonical `StrategyCard.build`; existing card не UPDATE-ится, StrategyActivation и EntryPlan/ExitPlan автоматически не создаются и новая version автоматически не включается. ON/OFF доступен только для уже существующей exact StrategyActivation через compare-and-set по identity + expected enabled + expected updated_at; stale write отклоняется, no-op не создаёт ложный journal event.

Текущий persisted production read-model на source-stage U6: одна V1 compatibility StrategyCard, один EntryPlan, `StrategyActivation = NOT SET`, `ExitPlan = NOT SET`. U5 parity identity не используется как test Activation и U6 production smoke не должен её переключать. Dashboard использует Universal Entry contracts из отдельно установленного published source tree, а не из mutable checkout.

Trading effect U1-U6: `NONE`. U5/U6 не подключены к `runtime.trade_commands`, Execution mutation или monitoring legacy truth и не являются consumer cutover. `INSTALLED_COMMIT/LOADED_COMMIT` и service state всегда проверяются отдельно после deploy опубликованного checkpoint. MICRO_LIVE/LIVE, mainnet re-arm, allocator и strategy selector отсутствуют.

## 8. Exit

После fill работает по той же Strategy binding.

## 9. Execution

Исполняет готовое решение и владеет exchange mutation mechanics, fill truth, IDs, protection, reconciliation и durable handoff.

## 10. Exchange

Внешняя торговая площадка. Архитектура не привязана к конкретному провайдеру.

## 11. Market facts / StrategySignal / Attempt

Market-data/monitoring создаёт causal facts. Торговая lifecycle конкретной Strategy начинается на `STRATEGY_SIGNAL_DETECTED`.

```text
causal market/context refs
-> signal_id                    # StrategySignal
-> strategy + EntryPlan binding
-> strategy_attempt_id
-> Entry decision
-> optional Execution
-> optional position
-> optional Exit
```

Один рыночный момент может породить несколько независимых StrategySignal разных Strategy. Rejected/no-fill/no-funds attempts сохраняются.

## 12. Аналитика

Supervisor/Analyst/PostgreSQL/UI находятся в поддерживающем наблюдательно-аналитическом контуре.

`StrategyCoinFit` — отдельный Analyst/research показатель исторической совместимости конкретной Strategy с конкретной монетой. Он не смешивается с объективным `CoinMarketRating`.

Они не являются новыми trading layers.

## 12.1 Dispatcher V2.1 runtime

Owner decision 2026-09-06 прекратил profile-based legacy runtime. `cripta-strategy-dispatcher.service` и старый `cripta-causal-context-correlator.service` отключены; исторические `strategy_dispatcher.*` и `research_context.event_links` не переписываются. Первичные signal/Entry/fill/position/MAYAK данные продолжают накапливаться и допускают последующий causal backfill.

Активная целевая реализация D0–D7 — clean `dispatcher_v2`: отдельный package/runtime/schema без Strategy profiles. Она публикует `GlobalMarketContext`, `CoinMarketContext` и `TradingCapacitySnapshot` с `trading_effect=NONE`. `CoinMarketRating` на этом этапе **не реализован**. Strategy/Entry/Exit consumer cutover остаётся следующим отдельным этапом.

D0–D7 production runtime подтверждён evidence report `DISPATCHER_V2_D0_D7_STAGE_RESULTS_RU.md`: `cripta-dispatcher-v2.service` active/enabled, installed/loaded source commit `ff259fdc173841a02cc6bb633af5ed5765614df1`, bootstrap from current MAYAK PASS, 20 coin contexts per source snapshot, restart/idempotency PASS.

D8 завершён и подтверждён `DISPATCHER_V2_D8_STAGE_RESULTS_RU.md`. `cripta-dispatcher-v2-context-correlator.service` active/enabled и причинно пишет append-only `research_context.dispatcher_v2_event_links`. На контрольной production-точке: 23 links, negative context age = 0, duplicates = 0, `NOT_CONSUMED=23/23`, `trading_effect=NONE=23/23`; Global/Coin event-time age доходил примерно до 540 секунд и сохраняется как фактическое качество observed context, а не исправляется задним числом.

Exact-ID discipline остаётся fail-honest: если событие не имеет доказанной exact lineage к `signal_id/position_id/trade_id`, D8 оставляет поля `NULL` и не восстанавливает ownership по `symbol + время`.

Temporal/cross-asset OOS frozen MAYAK components завершён без retuning. По frozen V1 подтверждён 1 из 12 эффектов, 11 получили `MIXED`; Seen frozen ALL9 не используется для post-hoc выбора формулы. Следующий обязательный gate перед `CoinMarketRating` — owner review результата `MAYAK_COMPONENT_OOS_RESULTS_V1_RU.md`, затем отдельный research/implementation contract рейтинга.

## 13. Масштабирование

Архитектура допускает много одновременно включённых Strategy/EntryPlan/bots/positions.

Universal Entry независимо обслуживает все активные планы. Strategy-specific concurrency не требует отдельного процесса на каждый plan; worker/event-loop model является implementation detail.

Не определены и не должны придумыватьcя без отдельной задачи:

- strategy selector;
- capital allocator;
- cross-strategy arbitration;
- strategy priority;
- global position cap.

Dispatcher не выполняет эти функции.

## 14. Что читать

1. `CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`
2. `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`
3. `docs/PROJECT_ARCHITECTURE_RU.md`
4. `docs/PROJECT_GOVERNANCE_RU.md`
5. `docs/MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md` при работе с MAYAK/Dispatcher/coin rating
6. `docs/STRATEGY_ENTRY_ARCHITECTURE_RU.md` при работе со Strategy/Entry/EntryPlan/signal lifecycle
7. затрагиваемые специализированные контракты


## U5 PUBLIC_TRADE mirror recovery repair — source stage

A narrow technical repair is in progress to remove a false REST-vs-mirror race and preserve exact mirror receive order during recovery. Strategy/V1/EntryPlan/comparator semantics remain unchanged; trading effect NONE; U7 remains evidence-incomplete until a fresh natural run reaches PARITY_COMPARABLE.

## U5 fail-honest service restart policy — source stage

`cripta-universal-entry-shadow.service` is being changed from `Restart=always` to `Restart=on-failure` so a clean `NOT_COMPARABLE` evidence stop does not create repeated fresh runs. Unexpected crashes may still restart. Trading effect NONE.

## U5 recovered-fact ordering / clean continuity stop — source stage

Live verification proved Bybit publicTrade seq is monotonic in actual WS receive order; observed seq-regression was caused by local post-recovery re-sorting. A narrow technical repair now preserves mirror receive order through OI merge and converts continuity failures into clean `NOT_COMPARABLE` service stops. Trading effect NONE.

## Universal Entry structural completion decision — 2026-09-12

По прямому решению владельца длительный U7 parity observation больше не блокирует завершение dormant source structure. Shadow evidence продолжает накапливаться независимо; factual semantic mismatch по-прежнему запрещает cutover.

Следующий structural stage завершает hard-disabled путь `ExecutionRequest -> existing Execution command contract`. По умолчанию production остаётся на `ENTRY_COMMAND_SOURCE=LEGACY_V1`, `UNIVERSAL_ENTRY_MAINNET_CONSUMER=DISABLED`; реальное переключение требует отдельного owner-approved cutover и не выполняется этим этапом.

### Structural source checkpoint

По результату owner-approved structural completion source-stage:

```text
Universal Entry -> immutable ExecutionRequest
-> pure exact-lineage execution bridge
-> dormant DB consumer
-> existing Execution command contract
```

реализован в source и прошёл gate: targeted structural tests `86/86 PASS`, полный pytest `1274 passed / 8 skipped`, Ruff для Universal/new source PASS, mypy Universal package `20 source files PASS`, private runtime compile PASS, default dormant consumer smoke `DISABLED / trading_effect=NONE`.

Текущая forensic V1 compatibility StrategyCard намеренно НЕ становится live-ready автоматически: в ней нет явной Strategy-owned allocation/leverage/execution policy, а legacy `runtime.trade_settings` запрещён как скрытый fallback. Для будущего cutover требуется отдельная owner-approved live Strategy version с полными execution/capital параметрами.

Production на этом source checkpoint не переключён: `ENTRY_COMMAND_SOURCE=LEGACY_V1`, `UNIVERSAL_ENTRY_MAINNET_CONSUMER=DISABLED`. Shadow/parity evidence является отдельным длительным наблюдением и не объявлен PASS.

### StrategyCard authoring UI checkpoint — 2026-09-12

По отдельному owner decision рабочая карточка Strategy теперь проектируется и реализована как одна
immutable version всей policy с понятным `name`, одним выбранным направлением `LONG` или `SHORT`
и тремя UI-вкладками: `Вход / Выход / Хедж`. Повтор одинаковой Exit policy в нескольких Strategy
versions разрешён; общий mutable Exit-template на этом этапе не вводится. Hedge хранится внутри
Strategy lifecycle policy и не является новым top-level layer.

Структурированный authoring UI поддерживает:

- signed offset относительно `CALCULATED_ENTRY`;
- macro 5m/15m candle lookbacks и optional local-entry window с 5m/15m/1m настройками;
- touch/Nth-touch, cooldown и reset;
- explicit capital/leverage/execution fields без fallback в legacy `runtime.trade_settings`;
- hard stop, take profit, fee-aware break-even, trailing, local 5m zone и time-exit policy;
- Hedge enabled/trigger depth/size/leverage/SL/TP/trailing;
- 34 фактически привязанные к текущему Dispatcher V2.1 context groups: 19 global + 15 coin groups,
  каждая в режиме `OFF / OBSERVE / CONDITION / RANKING`, причём decision-affecting режимы требуют
  explicit freshness/quality/missing/stale/partial semantics.

Strategy API сохраняет прежнюю границу: existing cards READ ONLY; save создаёт только новую
immutable StrategyCard, не создаёт Activation, не включает consumer и не пишет trading commands.
Frozen V1 Strategy fingerprint остаётся
`9199f1d2a19aa7f3bc54b465e00f14c3acba81886060d4893c23fff11943422e`.

Open/closed trade cards показывают human-readable Strategy `name` только по exact persisted
`strategy_id + strategy_version`; если exact StrategyCard не найдена, UI оставляет ID/version и не
угадывает имя.

Важно: authoring/storage support не равен runtime consumption. Новые signed-entry/local-entry,
feature-level context, extended Exit и Hedge поля на этом checkpoint являются Strategy policy data;
до cutover требуется отдельный wiring stage, который научит Universal Entry/Exit lifecycle
исполнять только явно утверждённые поля. До этого trading effect = `NONE`,
`UNIVERSAL_ENTRY_MAINNET_CONSUMER=DISABLED`, `ENTRY_COMMAND_SOURCE=LEGACY_V1`.

### Structural install checkpoint — 2026-09-12

Published structural source commit: `5e637c79a7328ccc58376d51e16e0bb32dca42f0`.

Фактически установлено без consumer cutover:

- additive append-only `strategy_entry.execution_dispatches`; runtime role `cripta` имеет только `SELECT/INSERT`, `UPDATE/DELETE` запрещены;
- immutable dormant consumer release `/srv/cripta/universal_entry_consumer/releases/5e637c79a7328ccc58376d51e16e0bb32dca42f0`;
- `cripta-universal-entry-consumer.service` установлен, но `disabled/inactive`; default smoke возвращает `ENTRY_COMMAND_SOURCE=LEGACY_V1`, `UNIVERSAL_ENTRY_MAINNET_CONSUMER=DISABLED`, `trading_effect=NONE`;
- existing private Execution runtime не перезапускался ради этого этапа;
- legacy Entry scanner PID/instance и dashboard не заменены;
- `runtime.trade_commands` / `runtime.executions` не изменились установкой structural bridge.

Отдельный shadow evidence runtime обновлён до exact source commit `5e637c79a7328ccc58376d51e16e0bb32dca42f0` и запущен только для длительного read-only накопления parity evidence. Его результат не блокирует structural completion; factual semantic mismatch остаётся hard blocker только для будущего cutover.
