# UNIVERSAL ENTRY — АУДИТ ГОТОВНОСТИ STRATEGYCARD → ENTRY → EXECUTION

**Дата:** 2026-09-12
**Статус:** runtime readiness evidence / finding
**Торговый эффект проверки:** NONE

## 1. Итог

```text
NEW STRATEGY CARD AUTHORING      = AVAILABLE
STRATEGY ACTIVATION MODEL        = IMPLEMENTED, FAIL-CLOSED
PRODUCTION MULTI-STRATEGY OBSERVER = NOT INSTALLED
UNIVERSAL ENTRY CUTOVER          = NOT READY
EXISTING ENTRY EXECUTION ADAPTER = READY FOR SUPPORTED ENTRY SUBSET
STRATEGY-SPECIFIC POST-FILL EXIT = NOT READY
HEDGE RUNTIME                    = NOT READY
```

Новая Strategy не может быть объявлена ACTIVE, если хотя бы одно включённое поле карточки не имеет
доказанного runtime consumer. Silent-ignore запрещён.

## 2. Entry policy matrix

| Поле Strategy | Состояние | Фактический consumer / причина |
|---|---|---|
| symbols | SUPPORTED | EntryPlan scope + ActivePlanRegistry |
| LONG / SHORT | SUPPORTED | EntryPlan directions + market watch |
| macro 5m/15m RANGE_ATR_CONFLUENCE | SUPPORTED | ParameterizedCausalMarketWatch |
| lookback / ATR width / confluence gap | SUPPORTED | ParameterizedCausalMarketWatch |
| shock reset ATR_MULTIPLE | SUPPORTED | ParameterizedCausalMarketWatch |
| shock reset RANGE_PERCENT | SUPPORTED | ParameterizedCausalMarketWatch |
| rolling swing gate | SUPPORTED | ParameterizedCausalMarketWatch |
| touch/Nth-touch/reset | SUPPORTED | UniversalEntryEngine + DSL |
| candidate cooldown | SUPPORTED | UniversalEntryEngine |
| post-signal favorable/adverse + embargo | SUPPORTED | PostSignalLifecycleBook |
| account capacity freshness/quality | SUPPORTED | UniversalEntryEngine decision |
| signed `entry_reference_policy` | STORED_ONLY / BLOCK ACTIVATION | точка Entry пока не пересчитывается |
| `local_entry_policy` | NOT SPECIFIED / BLOCK ACTIVATION | owner algorithm не определён достаточно точно |
| 34 `context_feature_policy` rows | STORED_ONLY / BLOCK ACTIVATION | не компилируются в current EntryPlan/context lookup |
| typed ContextRequirement failure actions | PARTIAL / BLOCK when decision-mode | freshness связывается, но разные failure actions не исполняются полностью |
| RANKING | NOT IMPLEMENTED / BLOCK ACTIVATION | scoring/ranking semantics не утверждены |

## 3. Activation / observer

`ActivePlanRegistry` уже фильтрует только `StrategyActivation(enabled=true)`. Dashboard first-enable
материализует exact EntryPlan/ExitPlan вместе с StrategyActivation одной транзакцией, а последующие
ON/OFF являются CAS update mutable activation-state.

Но текущий production observer новых Strategy отсутствует. Старый U5 shadow жёстко загружал frozen
V1 compatibility bundle и не является универсальным observer. При runtime audit также подтверждено,
что Dispatcher публикует account capacity, но текущий technical contour не предоставляет Entry
общий persisted/IPC normalized stream `5m/15m CANDLE + PUBLIC_TRADE`. MAYAK читает public trades
внутри своего процесса, но не является raw-feed API для Entry. Создание ещё одного независимого
strategy observer WebSocket feed без отдельного архитектурного решения не выполнялось.

Поэтому installed dashboard держит `MULTI_STRATEGY_OBSERVER_READY=False`, и любое включение до
появления нового observer отклоняется до DB mutation с `STRATEGY_RUNTIME_NOT_READY`.

## 4. Universal Entry → existing Execution

Поддержанный subset проходит существующий `prepare_runtime_entry_command()` и старый private runtime:

- exact Strategy/Activation/EntryPlan/ExitPlan identity;
- immutable `signal_id -> attempt -> decision -> ExecutionRequest` lineage;
- USDT `requested_amount`;
- leverage;
- `MARKET`;
- `LIMIT_OFFSET` только с explicit execution offset и TTL;
- max age ExecutionRequest;
- causal `fact.*` reference price;
- initial stop loss;
- initial take profit;
- `LastPrice` + `Full` protection mode.

Bridge не читает старые `runtime.trade_settings` как скрытые defaults. Private runtime повторно
проверяет баланс, рассчитывает qty, задаёт leverage и отправляет initial SL/TP вместе с entry order.

## 5. Что existing Execution/Exit НЕ делает из новой карточки

Следующие поля сохраняются в StrategyCard, но activation обязана блокироваться, если они включены:

- fee-aware break-even из конкретного ExitPlan;
- Strategy-specific trailing activation/distance;
- local 5m zone exit;
- time exit;
- Exit MAYAK/Dispatcher context rules;
- Hedge trigger/size/leverage/SL/TP/trailing.

Причина: текущий `exit_runtime.py` читает глобальные `runtime.trade_settings`, а не exact ExitPlan
владельца позиции. Техническая способность private runtime двигать stop/close не является
Strategy-specific Exit implementation.

## 6. Entry V1 retirement

12.09.2026 owner приказал выключить Entry V1, чтобы он не мешал переходу.

Выполнено:

```text
cripta-entry-shadow-scanner.service      = disabled / inactive
cripta-universal-entry-shadow.service    = disabled / inactive
```

Mutable state перенесён без удаления истории:

```text
/var/lib/cripta/archive/entry_v1_20260912T024510Z
size = 5.4G
```

Operational manifest:

```text
/srv/cripta-share/reports/entry_v1_archive_20260912T024510Z
```

PostgreSQL evidence и frozen source/fingerprint сохранены.

## 7. Cutover verdict

```text
CUTOVER_TO_NEW_ENTRY = NO
```

Причина не в старом Execution entry-order adapter. Главные блокеры:

1. production multi-Strategy observer ещё не установлен;
2. signed Entry offset ещё не потребляется;
3. local Entry algorithm ещё не утверждён/реализован;
4. 34 feature controls ещё не скомпилированы в причинный Dispatcher/MAYAK runtime consumption;
5. Strategy-specific post-fill Exit/Hedge не подключены.

До устранения этих блокеров `UNIVERSAL_ENTRY_MAINNET_CONSUMER` остаётся disabled и mainnet gate не
переоткрывается.

## 8. Published / installed checkpoint

Functional source commit `a0c805bfff07b66d1c69c986aa1eafb01354c715` установлен в dashboard
read-model contour по exact SHA. После restart `cripta-dashboard.service` active PID `839222`.
Entry V1 services остаются disabled/inactive; Universal consumer disabled/inactive; private/exit
runtime не запускались; mainnet execution gate остаётся закрыт. Negative activation smoke сохранил
counts без изменений: `activations=0`, `entry_plans=1`, `exit_plans=0`, `trade_commands=2166`,
`executions=978`, `execution_dispatches=0`.
