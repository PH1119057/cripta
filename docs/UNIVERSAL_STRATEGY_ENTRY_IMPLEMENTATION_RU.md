# UNIVERSAL STRATEGY / ENTRY — IMPLEMENTATION CONTRACT

**Документ:** UNIVERSAL_STRATEGY_ENTRY_IMPLEMENTATION_RU.md
**Версия:** 1.4
**Дата:** 2026-09-08
**Статус:** LEVEL 4 / implementation contract
**Торговый эффект этапа:** NONE до отдельного owner-approved cutover
**Source baseline U6:** 78e5e90753a3dffb2b61177174a94dc8ea4eea54
**Основание V1.4:** owner decision 2026-09-09 — narrow U5 public-transport continuity repair for parity evidence only. V1 Strategy semantics, EntryPlan, comparator semantics, trading path, Execution, consumer cutover, re-arm, MICRO_LIVE and LIVE are outside scope. U6 dashboard contract remains unchanged.

## 1. Назначение

Этот контракт реализует утверждённую цепочку:

    StrategyCard
    -> StrategyActivation
    -> immutable EntryPlan / ExitPlan
    -> Universal Entry Engine
    -> Entry Watch
    -> StrategySignal
    -> StrategyAttempt
    -> EntryDecision
    -> optional ExecutionRequest
    -> existing Execution

Он не определяет новую торговую Strategy V2, не ретюнит V1 и не меняет MAYAK/Dispatcher formulas.
До отдельного owner-approved cutover новый runtime работает только в SHADOW / PARITY и не имеет права передавать реальную mutation в Execution.

Верхние документы: CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md, CRIPTA_ARCHITECTURE_RULES_RU_V1.md, PROJECT_GOVERNANCE_RU.md, PROJECT_ARCHITECTURE_RU.md, STRATEGY_ENTRY_ARCHITECTURE_RU.md, SIGNAL_LIFECYCLE_CONTRACT_RU.md, STRATEGY_DISPATCHER_ARCHITECTURE_RU.md, MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md.

## 2. Architecture impact declaration

    ARCHITECTURE_CONFLICT = NO
    TRADING_POLICY_CHANGE = NO
    V1_SEMANTICS_CHANGE = NO
    MAYAK_TRADING_EFFECT = NONE
    DISPATCHER_TRADING_EFFECT = NONE
    MAINNET_REARM = NO
    ALLOCATOR = NO
    STRATEGY_SELECTOR = NO
    HISTORICAL_REWRITE = NO

## 3. Exact current forensic

### 3.1 Current source/runtime boundary

Текущий source checkpoint:

    GitHub PH1119057/cripta:main
    == /srv/cripta/source_checkout
    == 78e5e90753a3dffb2b61177174a94dc8ea4eea54

Текущий Entry V1 находится в src/bybit_workbench/entry_bot. Production scanner service использует отдельный installed source tree и проверяется отдельно от source checkout.

### 3.2 Decision-affecting V1 parameters found in code

Фактически влияют на создание Core signal:

    working_symbols                    current fixed V1 universe
    5m lookback                        130
    15m lookback                       130
    ATR period                         200
    zone half width                    0.50 ATR
    max 5m/15m confluence gap          0.25%
    candidate cooldown                 30 minutes
    failure embargo                    60 minutes
    shock baseline                     20 TR
    shock threshold                    3.0 x mean previous TR
    post-shock maturity                60 minutes
    hourly swing pause                 10.0%
    public-trade flow window           4 pressure minutes + 1 reversal minute
    flow condition                     pressure_then_reversal
    OI calibration required            true
    OI danger rule                     high 60m change OR low 5-vs-60 acceleration
    causal readiness                   latest 5m/15m/60m histories reach required closed boundary
    exact-touch convention             first qualifying touch in current V1 lifecycle
    failure-outcome trigger            -1.00% before +0.50% on accepted Core signal
    failure-outcome horizon            360 minutes

`60m` в этом forensic является readiness requirement текущего V1 candidate lifecycle, а не третьим timeframe геометрии: сама Entry geometry остаётся 5m+15m.

hourly_lookback=130 существует в EntryBotConfig, но production entry_bot/engine.py его не читает; поэтому он не переносится в V1 EntryPlan как доказанный live decision parameter.

### 3.3 Non-trading / observational values found

Следующие значения не влияют на факт создания Core signal и остаются technical/read-model configuration, пока тест не докажет обратное:

    approach display distance          0.25%
    watch display distance             0.60%
    last SIGNAL display lifetime       2 minutes
    diagnostic outcome milestones      post-decision observation

public_trade_flow_warmup_minutes=5 влияет на fail-closed возможность принять signal, поэтому переносится как sensor completeness requirement.

### 3.4 Current hard-coded identity

Current EntrySymbolEngine создаёт strategy_id=entry_v1_core, strategy_version=1.0-live-first-touch и V1-specific signal_id. Это implementation finding. Новый Universal Entry не содержит веток по strategy_id.

### 3.5 Current V1 card is incomplete

config/live_strategies/entry_v1_core.json хранит только identity и initial protection: stop 1.00%, TP 3.00%, LastPrice, Full. Для parity создаётся отдельная полная immutable compatibility StrategyCard со всеми доказанными decision-affecting V1 parameters. Старый файл не удаляется до cutover.

### 3.6 Current shadow is not parity baseline

operations/monitoring/entry_shadow_scanner.py меняет V1 configuration: candidate cooldown 0, OI calibration not required, expanded monitoring universe, outcome horizon 24h. Поэтому текущий shadow output нельзя использовать как эталон production parity.

### 3.7 Current storage truth

Исторические/production entities уже существуют:

    monitoring.opportunities
    monitoring.entry_geometry_handoffs
    runtime.entry_decisions
    runtime.entry_decision_events
    runtime.entry_geometry_bindings
    runtime.position_ownership
    runtime.trade_commands
    runtime.executions
    dispatcher_v2.global_market_contexts
    dispatcher_v2.coin_market_contexts
    dispatcher_v2.trading_capacity_snapshots
    research_context.dispatcher_v2_event_links

Они не переписываются задним числом. monitoring.entry_geometry_handoffs уже immutable через DB trigger и causal check geometry_observed_at <= signal_at. dispatcher_v2.trading_capacity_snapshots имеет DB check trading_effect=NONE.

### 3.8 Current execution boundary

Legacy production path сейчас фактически делает:

    monitoring.opportunities
    -> private_runtime policy checks
    -> runtime.entry_decisions
    -> runtime.trade_commands entry
    -> exchange mutation
    -> runtime.executions
    -> runtime.position_ownership

Новый Universal Entry stage не подключается к runtime.trade_commands. Он только строит immutable ExecutionRequest value object и сохраняет shadow evidence.

## 4. Target package layout

Новая clean implementation создаётся отдельно от legacy V1 engine:

    src/bybit_workbench/universal_entry/
        __init__.py
        contracts.py
        fingerprint.py
        catalog.py
        dsl.py
        materializer.py
        registry.py
        engine.py
        storage.py
        v1_compat.py
        parity.py

Strategy-specific trading logic внутри generic engine запрещена. v1_compat.py является compatibility input/adapter, а не special branch внутри engine.

## 5. Immutable contracts

### 5.1 StrategyCard

Минимальные typed fields: strategy_id, strategy_version, strategy_config_fingerprint, name, description, scope, symbols, direction_policy, entry_policy, exit_policy, capital_policy, protection_policy, lifecycle_policy, touch_policy, market_sensor_policy, mayak_context_policy, dispatcher_context_policy, created_at, approved source.

Fingerprint = SHA-256 canonical JSON всех policy fields по stable sorted-key serialization.

После insert StrategyCard UPDATE/DELETE запрещены DB trigger. Новое торговое значение = новая version + fingerprint.

### 5.2 StrategyActivation

Отдельная mutable control entity: activation_id, exact Strategy identity/fingerprint, enabled, enabled_at, disabled_at, optional scope, operator/source, created_at, updated_at. Activation не изменяет card. Нет hidden exclusivity между разными Strategy. Каждое изменение enabled дополнительно journalled append-only.

### 5.3 EntryPlan / ExitPlan

Materialized plans immutable. EntryPlan минимум содержит exact Strategy identity, activation_id, plan version/fingerprint, symbols/directions, predicate tree, sequence/window/timer/reset rules, touch policy, required sensors, context policy, missing/stale/partial semantics и capital request semantics.

ExitPlan сохраняет same Strategy identity. Universal Entry не исполняет ExitPlan.

## 6. Generic DSL

Generic operators:

    COMPARE
    AND
    OR
    NOT
    TOUCH
    BREAK
    RETEST
    RECLAIM
    COUNT
    NTH_EVENT
    SEQUENCE
    WINDOW
    TIMER
    RESET

Первый stage реально поддерживает boolean composition, comparison, event matching, touch number/Nth event, sequence/window/timer state and reset events. Unsupported operator возвращает explicit UNSUPPORTED_OPERATOR; он не превращается в false/neutral silently.

## 7. TouchPolicy / cooldown anchor

Typed policy:

    accepted_touch_numbers
    accept_touch_from optional N+
    require_exit_from_zone
    minimum_exit_distance enabled/value/unit
    minimum_time_between_touches enabled/value/unit
    maximum_touch_count enabled/value
    reset_on event kinds
    candidate_cooldown enabled/duration/unit/scope/anchor

`candidate_cooldown.anchor` задаётся явно как поддерживаемый causal timestamp field/event reference. Hidden/default anchor запрещён. Generic contract не содержит enum/branch со смыслом `V1 candidate bar`. Для V1 compatibility card anchor = `candidate_bar_at`, duration = 30 minutes. Другая Strategy может явно использовать `touch_at`, `signal_at` или иной поддерживаемый causal timestamp.

При candidate_cooldown enabled=false Universal Entry не создаёт hidden timer.

## 8. Sensor and Context Catalog

Stable classes: MARKET_FACT, MAYAK_CONTEXT, DISPATCHER_GLOBAL_CONTEXT, DISPATCHER_COIN_CONTEXT, TRADING_CAPACITY, TECHNICAL_READINESS.

Context modes: OFF, OBSERVE, CONDITION, RANKING.

CONDITION требует explicit max-age rule, data quality requirement, coverage requirement, on_missing, on_stale и on_partial. Missing/stale/partial никогда не преобразуются в zero, neutral или safe автоматически. RANKING только strategy-local и не сравнивает разные Strategy.

## 9. Active Plan Registry

Registry загружает enabled activations, materializes card to immutable plans, хранит любое число plans, индексирует по symbol/direction, удаляет из active observation только disabled activation, не выбирает winner, не имеет allocator и не mutates StrategyCard.

## 10. Universal Entry Engine

Вход engine — MarketFactEnvelope с exact causal refs and observed/event/received times плюс optional objective contexts.

Для каждого applicable active plan independently:

    update plan-local lifecycle state
    evaluate sensor/context quality
    evaluate predicate tree
    on match create StrategySignal
    create strategy_attempt_id
    evaluate only this Strategy capital/readiness requirements
    create EntryDecision
    only ACCEPTED creates pure ExecutionRequest
    persist shadow evidence

Один input может вернуть 0..N independent results. Engine API не принимает selected_strategy, winner, priority или other_strategy_won.

### 10.1 Boundary: causal market watch

Universal Entry / Entry Watch имеет право вычислять Strategy-specific predicates из уже причинно нормализованных market facts согласно EntryPlan:

    causal candles/facts
    -> geometry specified by EntryPlan
    -> confluence specified by EntryPlan
    -> touch/break/retest specified by EntryPlan
    -> StrategySignal

При этом получение raw market data, exchange-feed normalization и публикация shared causal facts остаются техническим sensor/market-data contour. U4 не создаёт второй strategy-aware Scanner рядом с Entry, не переносит V1 geometry в shared sensor layer и не даёт Universal Entry ownership над exchange feed.

Если для вычисления Strategy predicate требуется новый тип универсального causal derived fact/operator, он реализуется как generic primitive, parameterized EntryPlan. Ветка `if strategy_id == ...`, hidden trading number и V1-specific sensor logic запрещены.

### 10.2 Boundary: post-signal Entry lifecycle

Generic post-signal observation разрешён только как plan-local Entry lifecycle state, влияющий на будущие Entry той же Strategy, если EntryPlan это явно требует:

    StrategySignal
    -> causal post-signal market observation
    -> favorable/adverse resolution
    -> Entry lifecycle state
    -> optional future-entry embargo

Это не position supervision, stop management, take-profit или Exit. После confirmed fill сопровождение позиции принадлежит ExitPlan той же Strategy.

Минимальная explicit policy model:

    post_signal_outcome_policy:
        enabled
        favorable_threshold
        adverse_threshold
        horizon
        resolution_semantics
        resulting_entry_state
        optional_embargo

При `enabled=false` нет hidden post-signal tracking. Threshold/horizon/embargo values принадлежат StrategyCard -> EntryPlan. Исторические +0.50%, -1.00%, 360m и 60m существуют только в V1 compatibility card.

## 11. IDs and lineage

    source_market_event_refs
    -> signal_id
    -> strategy_id/version/fingerprint
    -> entry_plan_fingerprint
    -> strategy_activation_id
    -> strategy_attempt_id
    -> entry_decision_id
    -> optional execution_request_id

IDs строятся из canonical immutable identity and exact causal refs, not nearest symbol/time matching. Historical exact binding absent => NULL/UNKNOWN, no heuristic backfill.

## 12. EntryDecision vocabulary

Canonical decisions: ACCEPTED, STRATEGY_CONDITION_REJECTED, INSUFFICIENT_AVAILABLE_FUNDS, OPERATIONAL_SAFETY_BLOCKED, STALE_OR_UNKNOWN_REQUIRED_STATE, EXPIRED, CANCELLED.

Execution outcomes downstream: EXCHANGE_REJECTED, NO_FILL, EXECUTION_FAILURE. OTHER_STRATEGY_WON запрещён.

## 13. Capital semantics

StrategyCard владеет desired allocation/size/leverage semantics. Universal Entry читает exact TradingCapacitySnapshot только если plan этого требует. Недостаток средств даёт INSUFFICIENT_AVAILABLE_FUNDS конкретной attempt. Reservation/allocator/priority between Strategy отсутствует.

## 14. Notification hook

Typed notification поддерживает INSUFFICIENT_AVAILABLE_FUNDS, OPERATIONAL_SAFETY_BLOCKED, STALE_OR_UNKNOWN_REQUIRED_STATE, EXCHANGE_REJECTED, EXECUTION_FAILURE. Payload включает exact Strategy identity, signal_id, attempt_id, symbol/direction, time, reason, requested allocation and available capital where known. Hook cannot retry or mutate decision.

## 15. New PostgreSQL storage

Создаётся отдельная schema strategy_entry с owner postgres и minimal runtime grants.

Planned tables:

    strategy_entry.strategy_cards
    strategy_entry.strategy_activations
    strategy_entry.strategy_activation_events
    strategy_entry.entry_plans
    strategy_entry.exit_plans
    strategy_entry.strategy_signals
    strategy_entry.strategy_attempts
    strategy_entry.entry_decisions
    strategy_entry.context_links
    strategy_entry.execution_requests
    strategy_entry.notifications
    strategy_entry.shadow_parity_runs
    strategy_entry.shadow_parity_events

Policy/plan/signal/attempt/decision facts append-only. Activations mutable only for enabled state and every change is separately journalled. Migration actor postgres. Runtime actor cripta gets only required SELECT/INSERT and narrowly scoped UPDATE on activations. Historical existing tables untouched.

## 16. V1 compatibility mapping

V1 compatibility StrategyCard records current production values only. Trading scope фиксирован ровно на 10 `WORKING_SYMBOLS` legacy V1; наличие дополнительных строк в calibration artifact не расширяет Strategy scope. BTC/ETH reference rows и DOGE/1000PEPE calibration rows не становятся V1 trading symbols.

Generic calibration consumer не знает список V1: он получает symbol из конкретного EntryPlan и требует exact calibration row для этого symbol. Frozen calibration artifact используется как parity evidence/provenance, а не как hidden Universal Entry constant:

    SHA256 = b977bd42d76800a3eac63e42f67da7b75ecbf14e93c88761ff674cb084a32571
    schema = entry-bot-calibration-v1
    strategy = ENTRY_V1_CORE
    period = 20260518_20260816
    missing_symbols = []

V1 compatibility StrategyCard records current production values only:

    scope/symbols        exactly 10 legacy V1 WORKING_SYMBOLS
    causal readiness     explicit required closed timeframes 5m/15m/60m
    entry geometry       5m/15m lookback, ATR, zone width, confluence
    shock/reset          20 TR, 3.0 multiple, 60m maturity
    hourly policy        10.0% prior-60m swing pause
    candidate lifecycle  30m candidate cooldown
    flow sensor          exact 4+1 completed public-trade minute requirement
    flow condition       pressure_then_reversal
    OI sensor            required frozen per-symbol calibration
    OI condition         current tail danger thresholds from calibration refs
    failure lifecycle    accepted Core; +0.50 clears tracking; -1.00 first creates 60m embargo; 360m horizon
    protection           stop 1.00%, TP 3.00%, LastPrice, Full

OI thresholds remain per-symbol calibration data referenced by exact source fingerprint; they are not universal constants.

## 17. V1 parity gate

Parity replay uses identical deterministic causal events:

    A = legacy EntrySymbolEngine with production V1 settings
    B = Universal Entry Engine + V1 compatibility EntryPlan

Deterministic comparison сравнивает не только итоговое число Core signals, а causal sequence по каждой сравнимой попытке/кандидату минимум:

    candidate identity/time
    direction
    geometry values
    exact touch time
    candidate cooldown decision and anchor
    shock/reset state
    hourly swing state
    pressure/reversal result
    OI result
    final StrategySignal presence/absence
    post-signal favorable/adverse resolution
    entry-lifecycle embargo transitions
    causal source refs

Первый mismatch обязан иметь diagnostic category, exact causal key, legacy value/state и universal value/state. `COUNT_DIFF` без первой диагностируемой семантической причины недостаточен.

New generic signal_id byte identity is not required to equal legacy V1 id because canonical new identity includes exact Strategy binding. Parity maps by deterministic causal key and requires one-to-one semantic equivalence. Any unexplained semantic difference => V1_PARITY=FAIL and cutover forbidden.

Если legacy V1 semantic невозможно выразить generic primitives без `if strategy_id == V1`, hidden default/number или special V1 branch, U4 останавливается с `HARD_STOP=YES` и фиксирует недостающий generic primitive; compatibility workaround запрещён.

## 18. Architecture test matrix

Mandatory tests:

1. one active Strategy;
2. five simultaneous Strategy;
3. LONG and SHORT Strategy on same symbol;
4. no winner/priority output;
5. disable activation affects only its own observation;
6. activation mutation does not mutate card;
7. cooldown disabled creates no hidden timer;
8. cooldown enabled respects scope;
9. first/second/Nth touch generic;
10. reset clears only plan-local lifecycle;
11. MAYAK/Dispatcher OFF has no effect;
12. OBSERVE is recorded but cannot change decision;
13. CONDITION changes only that Strategy decision;
14. missing/stale/partial never becomes zero/neutral;
15. same market fact may create multiple StrategySignal;
16. exact strategy/activation/plan bindings on signal/attempt/decision;
17. historical signals are not rewritten;
18. Universal Entry has no exchange mutation API;
19. Dispatcher package has no StrategyActivation/plan/signal dependency;
20. source scan rejects strategy-id-specific branches in universal package.

Additional tests cover fingerprint stability, explicit unsupported operator, capacity rejection, ExecutionRequest only on ACCEPTED, independent plan state and V1 card materialization without magic trading defaults.

## 19. UI migration

Dashboard adds Strategy list with Strategy, Version, Fingerprint, Description, Direction, Scope and Active ON/OFF.

Card sections: General; Entry; Touch; Lifecycle/cooldown/reset; Geometry/sensors; Capital/leverage; Protection; Exit; MAYAK usage; Dispatcher usage.

Approved card is read-only. Editing means create new version. Saving new version does not activate it. Activation endpoint changes StrategyActivation only. During this implementation stage Strategy API cannot re-arm mainnet.

## 20. U5 parallel shadow runtime contract

Historical U5 source baseline before runtime source modification:

    49670cb0631a8742b2bf8dace9ab33d6b29a107d

### 20.1 Trading effect and isolation

U5 создаёт отдельный technical shadow service `cripta-universal-entry-shadow.service`. Его trading effect строго `NONE`. Service не заменяет и не изменяет действующий legacy Entry service, не читает/не пишет `runtime.trade_commands`, не вызывает Execution transport, не имеет authenticated/private exchange credentials и не меняет `monitoring` legacy truth. Остановка/падение/restart U5 не должны влиять на legacy Entry.

Pure `ExecutionRequest` value object допустим только внутри Universal evaluation/evidence. U5 не имеет downstream consumer для этого объекта.

### 20.2 Один normalized causal fact source

Оба сравниваемых движка получают один и тот же `MarketFactEnvelope` из одного technical public-data adapter внутри U5:

    PUBLIC REST/WS transport (technical sensor contour, read-only)
        -> normalize exactly once
        -> one immutable causal MarketFactEnvelope
        -> fan-out same object/data
             -> passive canonical EntrySymbolEngine reference
             -> Universal Entry + frozen V1 EntryPlan

Legacy reference и Universal Entry сами transport не открывают. Нельзя иметь отдельный WS/REST stream для A и B. Adapter не является strategy-aware Scanner и не принимает торговых решений. Raw feed acquisition/normalization остаются technical sensor contour; Strategy geometry вычисляет только Entry Watch.

U5 использует ровно 10 V1 trading symbols. Исторические BTC/ETH reference и DOGE/1000PEPE calibration rows не расширяют trading scope.

Initial history fetch выполняется один раз на symbol для causal closed 5m/15m/60m candles и OI history; одни и те же нормализованные objects загружаются в обе стороны. Live fact kinds минимум: `PUBLIC_TRADE`, `BAR_OPEN`, `CANDLE_CLOSED`, `OPEN_INTEREST`. Каждый fact сохраняет exact source refs, `event_at`, `observed_at`, `received_at` и closed-bar boundaries.

### 20.3 Passive canonical V1 reference

Reference A = прямой `EntrySymbolEngine` с exact production V1 config и exact frozen calibration, но без `EntryBotRuntime`, `AuditStore`, `PositionHandoffStore`, `monitoring.opportunities` и иных production writes. Текущий изменённый `operations/monitoring/entry_shadow_scanner.py` не является reference и не вызывается comparator-ом.

Reference adapter разрешено только вызвать pure/stateful methods `load_history`, `on_current_five_minute_open`, `on_closed_candle`, `on_open_interest`, `on_trade`, читать snapshot/audit state для сравнения и drain локального audit buffer. Он не публикует legacy signal наружу.

### 20.4 Online parity comparator

После каждого comparable causal input U5 вычисляет тот же semantic comparison set, что U4: candidate identity/time/direction, geometry, touch, cooldown decision+anchor, 5m/15m/60m readiness, shock/reset, rolling 60m swing, pressure/reversal, OI result, StrategySignal presence/absence, post-signal favorable/adverse resolution, future-entry embargo и causal refs.

Счётчики ведутся по каждому comparable input. В PostgreSQL append-only `shadow_parity_events` обязательно пишутся: status transitions, semantic state transitions/checkpoints и любой mismatch. Первый mismatch пишется немедленно и содержит `run_id`, `causal_key`, category, legacy value/state, universal value/state, source refs, observed time, Strategy fingerprint и EntryPlan fingerprint. `COUNT_DIFF` без первой semantic причины запрещён.

### 20.5 Startup and restart comparability

State machine:

    START/RESTART
      -> WARMUP / NOT_COMPARABLE
      -> exact causal seed/replay complete
      -> required live sensor completeness complete
      -> unknown pre-start Entry lifecycle influence expired or exactly replayed
      -> PARITY_COMPARABLE

Отсутствие signal в WARMUP не является mismatch.

Для первого запуска неизвестный pre-start plan-local lifecycle нельзя считать пустым. Максимальный safe warmup horizon вычисляется **из EntryPlan**, а не hidden constant: максимум влияния enabled candidate cooldown и enabled post-signal outcome horizon + resulting embargo; исторические candle-based shock/swing/readiness восстанавливаются exact closed history seed. Для frozen V1 этот derived horizon равен 420 минутам (360m outcome horizon + 60m adverse embargo; candidate cooldown 30m меньше). Это derived compatibility evidence, не universal Entry constant.

U5 ведёт durable local append-only normalized-fact/recovery journal под отдельным shadow state root. Journal не является trading truth и не заменяет PostgreSQL parity evidence. U5 не пытается переносить `PARITY_COMPARABLE` через restart/socket gap: незавершённый предыдущий run финализируется `NOT_COMPARABLE`, а новый service instance создаёт новый run и начинает отдельный `WARMUP`. Journal сохраняет exact уже полученные facts для аудита/детерминированного replay при диагностике; он не является основанием считать gap покрытым. Если в будущем будет утверждено продолжение одного run через restart, оно допустимо только после exact replay и доказательства отсутствия gap. Никакой nearest-time reconstruction.

### 20.6 PostgreSQL parity runtime storage

Existing U3 parity tables расширяются только для online evidence. `shadow_parity_run` identity immutable: Strategy identity/fingerprint, EntryPlan fingerprint, calibration SHA/size, baseline/universal commits, fact-source identity, service instance and started_at не меняются после insert.

Operational fields run-а могут иметь только narrow audited transitions `WARMUP -> PARITY_COMPARABLE -> PASS|FAIL`, `WARMUP -> NOT_COMPARABLE` либо `PARITY_COMPARABLE -> NOT_COMPARABLE` при потере доказанной continuity; `finished_at` NULL до finalization. UPDATE других columns и DELETE запрещены. Runtime role получает UPDATE только разрешённых operational columns. Каждая status transition одновременно имеет append-only event.

`shadow_parity_events` остаётся полностью append-only. В event evidence сохраняются category, causal key, exact source refs/times и Strategy/Plan fingerprints. Storage не содержит trading predicates/threshold logic.

### 20.7 Frozen identity

Каждый run фиксирует:

    V1 trading symbols = exact 10-symbol Strategy scope
    strategy_config_fingerprint
    entry_plan_fingerprint
    calibration_size = 4647
    calibration_sha256 = b977bd42d76800a3eac63e42f67da7b75ecbf14e93c88761ff674cb084a32571
    baseline_source_commit
    universal_source_commit / loaded commit

Calibration SHA является provenance, не generic constant.

### 20.8 Deployment barrier

U5 сначала проходит tests/Git checkpoint/push. Deploy разрешён только published commit. Systemd unit устанавливается отдельно и независимо, без изменения legacy service. Initial smoke обязан доказать service ACTIVE, legacy unaffected, same fact fan-out, parity run persistence, zero shadow writes to `runtime.trade_commands`, zero exchange mutation path и restart -> explicit WARMUP behavior. Реальный candidate не требуется для smoke.


### 20.9 U5 public transport continuity repair

Owner-approved repair scope is technical continuity only. A transient public WebSocket disconnect is not itself a reason to reset a parity run, but reconnect alone is never proof of continuity.

Required state machine:

    WARMUP / PARITY_COMPARABLE
      -> PUBLIC_TRANSPORT_PAUSED
      -> reconnect + exact resubscribe
      -> per-fact-kind continuity verification
           -> PROVEN_COMPLETE: replay exact missing facts, same process/service_instance_id/parity_run_id/started_at, resume
           -> NOT_PROVABLE: current run -> NOT_COMPARABLE; only a subsequent new process/run may restart WARMUP

A recoverable reconnect MUST NOT reset the derived warmup clock. The 420-minute V1 horizon remains derived only from EntryPlan.

Transport continuity is verified independently for every required fact kind:

- `PUBLIC_TRADE`: keep exact trade ID and Bybit cross-sequence as transport cursor evidence. Gap replay may use public recent-trade only when the exact pre-gap trade anchor is present and the missing trade set can be enumerated by exact `execId`; otherwise continuity is `NOT_PROVABLE`. REST backfill rows use their exact execution ID/timestamp; no nearest-time reconstruction.
- `CANDLE_CLOSED`: restore only exact missing closed 5m/15m/60m boundaries from public kline history. A missing expected boundary or conflicting OHLC is `NOT_PROVABLE`.
- `BAR_OPEN`: repeated WS updates for one 5m bar are transport duplicates. Only one semantic `BAR_OPEN` per exact opened-at boundary is admitted. If a boundary was missed during a recoverable gap, it may be reconstructed only from the exact 5m candle boundary/open value; reconnect itself never creates another bar-open event.
- `OPEN_INTEREST`: current frozen fact source remains `BYBIT_PUBLIC_NORMALIZED_U5_V1_OI30S`. Public historical OI is not available at 30-second cadence and 5m OI is NOT semantically substitutable. Therefore OI continuity is provable only if all required ticker subscriptions are confirmed ready before the earliest `last_accepted_oi_sample_at + 30s` deadline for all 10 symbols. If this deadline is crossed, or an exact prior OI30S cursor is absent, the gap is `NOT_PROVABLE`. No OI cadence/source/fact_source_id change is permitted by this repair.

Exact deduplication is by source identity only. Replayed `PUBLIC_TRADE` uses exact trade ID; candles/bar-open use exact timeframe boundary identity; OI uses its exact sampled ticker timestamp/value identity. A duplicate replay MUST NOT enter the comparator a second time.

All disconnect/reconnect attempts append technical parity evidence containing at least disconnect time, reconnect/subscription-ready time, last accepted cursors/sequences, replay counts by fact kind, gap duration and verdict. These records are technical evidence, not market/trading facts, do not create StrategySignal and do not affect either engine state except through the exact replayed normalized facts.

A disconnect after `PARITY_COMPARABLE` follows the identical fail-closed continuity contract. First-mismatch evidence remains immutable and unchanged by transport repair.

Process restart continuation of one run remains forbidden. Systemd restart is only an emergency path after the current run has become `NOT_COMPARABLE` or the process has failed before continuity could be proven.

Acceptance tests for this repair include: artificial close during WARMUP with in-process reconnect; recoverable gap preserving run identity/start time; unrecoverable gap finalizing `NOT_COMPARABLE`; exact replay dedup; disconnect after `PARITY_COMPARABLE`; first-mismatch preservation; zero `runtime.trade_commands`/`runtime.executions` identifiers; legacy Entry isolation; architecture/Ruff/mypy/full pytest gates.

## 21. U6 Strategy dashboard control/read-model contract

### 21.1 UI ownership boundary

U6 расширяет существующий dashboard только как technical UI/read-model/control surface. Dashboard не становится владельцем Strategy policy, Entry, Execution или Exchange и не получает путь к `runtime.trade_commands`, Execution consumer, private exchange mutation, MAYAK/Dispatcher mutation, mainnet arm/re-arm, allocator или Strategy selection/ranking.

UI читает persisted truth только из `strategy_entry`. Он не хранит отдельную историю Strategy и не подставляет собственные торговые defaults. Отсутствующее значение отображается как `UNKNOWN`, `NOT SET` или `DISABLED` по фактической семантике. Ноль/neutral не используется вместо отсутствующих данных.

### 21.2 Strategy list and card read-model

Список строится независимо для каждой exact Strategy version и показывает минимум `name`, `strategy_id`, `strategy_version`, полный `strategy_config_fingerprint`, description, direction policy, scope/symbols, exact Activation identity/state когда она существует, exact EntryPlan fingerprint и exact ExitPlan fingerprint. Никаких `primary`, `winner`, `priority`, `best strategy` или cross-Strategy ranking полей.

Карточка раскрывает сохранённые policy sections: General; Entry; Touch; Lifecycle/cooldown/reset; Geometry/sensors; Capital/leverage; Protection; Exit; MAYAK usage; Dispatcher usage. Approved StrategyCard всегда read-only. Fingerprints Strategy/EntryPlan/ExitPlan визуально различаются и доступны полностью для копирования.

### 21.3 Current production facts at U6 start

Read-only PostgreSQL forensic на baseline U6 показывает: одна immutable V1 compatibility StrategyCard, один EntryPlan, `0` persisted StrategyActivation, `0` ExitPlan и `0` activation journal rows. Поэтому production UI обязан показать для этой card `Activation = NOT SET` и `ExitPlan = NOT SET`; `NOT SET` нельзя превращать в `OFF`. Frozen U5 parity runtime не зависит от `strategy_activations` и U6 smoke не создаёт/не переключает для него Activation.

### 21.4 Create-new-version flow

Текущая schema не имеет draft/approval lifecycle, и U6 его не изобретает. Owner-authenticated dashboard может создать новую immutable StrategyCard version только из полного explicit policy payload через canonical `StrategyCard.build`. Request обязан ссылаться на exact base `strategy_id/version/fingerprint`; existing row не UPDATE-ится. New `strategy_version` должна быть новой; policy payload валидируется typed contracts; canonical fingerprint вычисляется только общим Universal Entry builder.

U6 version-save НЕ создаёт StrategyActivation, НЕ включает новую version и НЕ материализует EntryPlan/ExitPlan автоматически. В результате для новой version activation/plan fields остаются честно `NOT SET` до отдельного owner-approved materialization/activation control. Это сознательная граница U6, исключающая выдуманный hidden approval/activation layer.

### 21.5 StrategyActivation control

ON/OFF endpoint работает только для уже существующей exact `activation_id`. Создание новой Activation через U6 dashboard отсутствует. Mutation меняет только permitted operational Activation fields и проходит существующий PostgreSQL trigger/journal contract; StrategyCard/Plan fingerprints не меняются.

Stale-write protection использует compare-and-set: request обязан нести exact `strategy_id/version/fingerprint`, `activation_id`, expected `enabled` и expected `updated_at`. UPDATE выполняется только если все expected values ещё совпадают. Если row уже изменён — `STALE_ACTIVATION_STATE`/HTTP 409 без overwrite. Повторный request, который просит уже текущее состояние при той же exact identity, возвращает `NO_CHANGE` и не создаёт ложный activation journal event.

### 21.6 U5 isolation

Все U6 write-tests выполняются только в disposable PostgreSQL или rollback-only transaction с test identity. Production smoke read-only; он не создаёт и не меняет V1 parity Activation. `cripta-universal-entry-shadow.service` не перезапускается ради U6 и его PID/state/fact counters фиксируются до/после deploy. Legacy Entry service проверяется так же.

### 21.7 Dashboard runtime source boundary

Dashboard backend использует canonical Universal Entry contracts/builders из отдельного installed source tree, собранного только из опубликованного U6 commit. Он не импортирует package из mutable development worktree. Dashboard deployment различает source commit и loaded dashboard/runtime source commit.

### 21.8 U6 acceptance

Минимальный gate: one/five/contradictory Strategy render; no winner/priority; immutable-card mutation absent; new-version creates new immutable card and stays unactivated; activation mutation changes only Activation and journals append-only; stale CAS rejected; missing values never become zero/neutral; exact three fingerprint classes rendered; no execution/mainnet path in Strategy API; U5/legacy unaffected; headless dashboard smoke; Ruff; mypy; full pytest. Trading effect remains `NONE`.

## 22. Runtime migration stages

    U0 implementation contract + forensic
    U1 immutable contracts/fingerprint/DSL/catalog
    U2 materializer + registry + engine
    U3 PostgreSQL schema/storage
    U4 generic watch/lifecycle primitives + V1 compatibility card/plan + deterministic parity runner
    U5 shadow universal runtime parallel to legacy V1
    U6 dashboard Strategy control/read-model
    U7 parity evidence + full gate + Git checkpoint

No live cutover stage exists in this task.

## 23. Cutover barrier

Even after green checks:

    UNIVERSAL_ENTRY_MAINNET_CONSUMER = DISABLED
    MAINNET_REARM = NO
    LIVE_CUTOVER_OWNER_DECISION_REQUIRED = YES

Future cutover requires separate owner task and normal MICRO_LIVE/LIVE process.

## 24. Rollback

Initial deployment is shadow-only. Rollback stops/disables only universal shadow service if required. Legacy Entry V1 is not replaced. New strategy_entry facts remain audit history. Source rollback uses exact Git checkpoint. No exchange reconciliation is required solely because universal shadow runtime has no mutation path.

## 25. Acceptance

    ARCHITECTURE_TESTS = PASS
    V1_COMPATIBILITY_CARD = COMPLETE
    V1_PARITY = PASS
    MULTI_STRATEGY = PASS
    CONTRADICTORY_STRATEGY = PASS
    ENTRY_SELECTS_STRATEGY = NO
    DISPATCHER_STARTS_STRATEGY = NO
    HISTORICAL_REWRITE = NO
    EXECUTION_MUTATION_FROM_UNIVERSAL_SHADOW = NO
    FULL_GATE = PASS
    SOURCE_HEAD = REMOTE_HEAD
    MAINNET_REARM = NO
