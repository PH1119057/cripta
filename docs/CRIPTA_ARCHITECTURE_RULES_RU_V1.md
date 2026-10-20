# CRIPTA — верхние архитектурные правила

**Версия:** 3.0
**Дата:** 2026-10-16
**Статус:** верхний канонический архитектурный контракт

Этот документ определяет верхнюю архитектуру и межслойные запреты.
Детали каждого слоя находятся в активных документах из
`docs/DOCUMENTATION_INDEX_RU*.md`.

# 1. Верхняя модель

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

Верхних уровней пять.

Внутри `STRATEGY` существует компонент материализации планов. Он не образует
шестой верхнеуровневый слой.

Технический поддерживающий контур обеспечивает данные, связь, хранение,
наблюдаемость, lifecycle-контроль, восстановление, UI и аудит, но не является
дополнительным торговым уровнем.

`Risk` не является самостоятельным верхнеуровневым слоем.

# 2. MAYAK

MAYAK независимо и причинно наблюдает внешний рынок.

Он не знает торговый смысл конкретной Strategy, не создаёт `StrategySignal`,
не открывает и не закрывает позиции, не запрещает и не разрешает Entry,
не двигает stop/trailing и не меняет Strategy.

Результат MAYAK — объективные факты/снимки рынка с качеством, свежестью и
происхождением данных.

# 3. DISPATCHER

Dispatcher структурирует объективный strategy-agnostic рыночный контекст и
состояние торговой ёмкости аккаунта.

Он публикует факты `total/equity/used/reserved/free/available`, но сам не
решает, какой Strategy дать капитал, не резервирует его и не создаёт торговую
мутацию.

# 4. STRATEGY

Strategy layer — единственный владелец торгового смысла и lifecycle своих
утверждённых правил.

## 4.1 StrategyCard

`StrategyCard` остаётся пассивной, immutable, утверждённой владельцем и
версионированной карточкой торговых правил.

Она не наблюдает рынок, не является daemon и сама не создаёт рыночный сигнал.

Изменение торгового смысла или числа = новая утверждённая Strategy version.

## 4.2 Strategy Materializer

Внутри Strategy layer существует `Strategy Materializer`.

Он:
- берёт exact immutable StrategyCard/version;
- детерминированно создаёт immutable `EntryPlan` и `ExitPlan`;
- сохраняет exact strategy/version/fingerprint и plan fingerprints;
- публикует планы в устойчивый active-plan registry;
- может выполняться при activation/load/restart/recovery;
- не наблюдает рынок;
- не решает, выполнены ли условия Entry/Exit;
- не имеет права добавлять торговые defaults, отсутствующие в StrategyCard.

Форма реализации Materializer — функция, класс или service — является
implementation detail и не создаёт отдельного торгового слоя.

## 4.3 Ownership Strategy

StrategyCard содержит все торговые параметры конкретного способа торговли,
включая:
- universe/symbols;
- direction;
- Entry/Exit geometry;
- timeframe/depth;
- stabilization/touch/retest/sequence/cooldown/reset;
- MAYAK/Dispatcher context consumption;
- capital amount/allocation;
- leverage;
- execution order policy;
- initial protection;
- hard stop/TP/BE/trailing;
- holding;
- Exit rules;
- hedge policy, если включена.

Ни Materializer, ни Entry, ни Exit, ни Execution не имеют права подменять
отсутствующие Strategy-owned значения скрытыми defaults.

Hedge policy также не отменяет физические ограничения Exchange position mode.
Для одного и того же symbol/account в текущем one-way contract противоположная
real экспозиция не считается независимым hedge: она может неттировать/закрывать
существующую позицию. Такой same-symbol hedge unsupported и обязан fail-closed
до отдельного owner-approved hedge-mode/subaccount/internal-netting contract.

## 4.4 Strategy settings и экспериментальные версии

Decision/execution-affecting настройки конкретной Strategy принадлежат только
её immutable StrategyCard и раскладываются по явным owner-owned policy-блокам:

- entry_policy — условия и параметры Entry;
- touch_policy — касания/retest/cooldown/reset;
- capital_policy — капитал/leverage;
- protection_policy.initial_protection — базовая защитная рамка, известная уже
  при открытии;
- exit_policy — динамические правила сопровождения/Exit;
- lifecycle_policy — hedge и другие сквозные правила;
- MAYAK/Dispatcher context policies — только явно разрешённое конкретной
  Strategy потребление контекста.

Не создаётся отдельный скрытый runtime-мешок strategy_settings, который мог бы
иметь торговый смысл независимо от StrategyCard.

initial_protection и динамический Exit — разные сущности. Strategy может
утвердить базовую защитную рамку для Entry, пока H3/касания/break-even/trailing
или другие правила сопровождения остаются исследовательскими.

Для optional decision/execution setting обязательно:
- явное enabled;
- при enabled=false отсутствуют скрытые торговые числа;
- authoring template не содержит числовых trading defaults;
- при enabled=true должен существовать exact consumer contract;
- unsupported/missing consumer означает fail-closed, а не silent-ignore.

Для lifecycle_policy.hedge дополнительно обязателен совместимый Exchange
position-mode contract. В текущем one-way same-symbol hedge не поддержан:
authoring/materialization/readiness real Strategy обязаны fail-closed, а не
интерпретировать встречный order как hedge.

Неустойчивые параметры сначала принадлежат Strategy Candidate / Strategy Draft.
Для воспроизводимого shadow/MICRO_LIVE эксперимента владелец может утвердить
exact snapshot как новую experimental immutable Strategy version.

Research/example/history не становятся global defaults и не получают торговых
прав без отдельного owner-approved Strategy snapshot.

Legacy immutable StrategyCard не изменяется при появлении новых structural
slots. Compatibility может добавить только инертные enabled=false slots и не
имеет права изобретать числовые значения.

# 5. ENTRY

Entry Engine — универсальный активный исполнитель EntryPlan.

Количество Strategy ему не важно. Он получает активные планы, независимо
проверяет каждый план на причинных данных и при выполнении условий создаёт
strategy-specific StrategySignal, strategy_attempt и EntryDecision.

Entry не арбитрирует торговый смысл независимых Strategy: не выбирает, какая
Strategy «лучше», и не отменяет противоположный StrategySignal только из-за
направления LONG/SHORT. Отдельно, перед real Exchange mutation, physical-slot
admission обязан fail-closed блокировать attempt, который не может получить
exclusive ownership требуемого Exchange position slot.

## 5.1 Real Entry admission: required state, slot claim и capital reservation

EntryDecision=ACCEPTED разрешён только после успешного real Entry admission.

Обязательный порядок:

```text
strategy_attempt
-> required account / position-mode state validation
-> atomic physical Exchange slot claim
-> atomic capital reservation
-> EntryDecision
```

Physical slot claim и capital reservation должны завершаться как единый
all-or-nothing durable admission contract. Implementation lock order должен быть
стабилен и воспроизводим:

```text
1. exact account / instrument / position-mode state validation
2. exact exchange_position_key / slot row
3. capital reservation ledger
4. commit admission outcome
```

Успех:

```text
slot claim success + capital reservation success
-> COMMIT
-> EntryDecision=ACCEPTED
-> EntryExecutionRequest
```

Отказ slot claim:

```text
-> EXCHANGE_POSITION_OWNERSHIP_CONFLICT
-> no capital reservation
-> no EntryExecutionRequest
```

Отказ reservation:

```text
-> slot claim rollback/release in the same admission transaction
-> INSUFFICIENT_AVAILABLE_FUNDS
-> no EntryExecutionRequest
```

Недопустимы durable partial states вида «slot claimed, но admission outcome
неизвестен» или «capital reserved без доказанного slot ownership».

Unknown order/fill state после dispatch не освобождает slot claim или
reservation до reconciliation.

Dispatcher capacity snapshot остаётся advisory fact и не является lock/ledger.

### 5.1.1 AccountStateGeneration для real Entry

OWNER DECISION 2026-10-06: real Entry admission больше не определяется
независимыми wall-clock age gates для reconciliation, wallet и trading capacity.

Private account contour материализует один causal AccountStateGeneration:

COLLECTING -> COMPLETE | FAILED

COMPLETE generation содержит одним поколением минимум:
- exact exchange/account identity;
- wallet/account type и доступный капитал;
- current positions;
- active orders;
- exact position-mode state refs для всех active real Strategy symbols;
- started/completed timestamps и provenance.

Для real Entry используется current latest terminal generation. Новый
COLLECTING не инвалидирует предыдущий COMPLETE: сбор следующего поколения сам
по себе не делает подтверждённое состояние счёта плохим. Новый FAILED делает
account state unusable fail-closed до следующего COMPLETE.

Real admission обязан под account-level lock доказать, что используемый
generation всё ещё current, его capital совпадает с reservation input, а
position-mode ref exact symbol принадлежит этому generation. После этого идут
physical slot claim и capital reservation.

В generation-backed real admission запрещены независимые gates вида
wallet_age=N, reconciliation_age=N и capacity_age=N. Historical /
non-generation compatibility path может сохранять age metadata, но не является
production real-admission authority.

Время остаётся trading/lifecycle semantics только там, где оно физически
значимо: StrategySignal / EntryExecutionRequest validity/expiry, exchange
mutation timeout и иные отдельно канонизированные deadlines.

## 5.2 Physical Exchange position slot и position mode

Логическая независимость Strategy не означает право нескольким Strategy
одновременно владеть одним physical position slot.

Physical slot определяется exact exchange/account/product/instrument/
position-mode identity и имеет один active owner lifecycle.

Для текущего Bybit Unified linear one-way contract допустимо:

```text
position_mode = ONE_WAY
positionIdx = 0
```

Но position mode не считается вечным свойством аккаунта из-за одной исторической
проверки. Fresh Position mode state — обязательная account state для real
activation/re-arm и Entry admission.

Она должна включать, где применимо:

```text
exchange
account identity
product/category
symbol/instrument scope
position mode
positionIdx
observed_at
received_at
freshness
provenance
```

Если mode state unknown/stale:

```text
EntryDecision=STALE_OR_UNKNOWN_REQUIRED_STATE
-> no real mutation
```

Если state свежая, но режим/positionIdx несовместим с утверждённым execution
contract:

```text
EntryDecision=OPERATIONAL_SAFETY_BLOCKED
block_reason=EXCHANGE_POSITION_MODE_MISMATCH
-> no real mutation
```

В текущем one-way contract фактический positionIdx != 0 является lifecycle/
operational fault EXCHANGE_POSITION_MODE_MISMATCH и требует fail-closed +
reconciliation. Execution не переключает position mode автоматически.

Fresh mode verification обязательна минимум при:
- real activation/re-arm;
- добавлении нового symbol в real Strategy universe;
- private-state reconnect/recovery, если continuity не доказана;
- обнаруженном Exchange/account configuration change;
- снятии position-mode-related fail-closed state.

Для каждого production real Entry exact position-mode proof берётся из current
COMPLETE AccountStateGeneration по §5.1.1. Отдельный per-entry
freshness expiry / fresh_until clock не является вторым admission gate.

## 5.3 Decision outcome, request state и lifecycle fault

Каноническая таблица «token -> entity» находится в
docs/CRIPTA_GLOSSARY_RU*.md и является единственным терминологическим
определением этих tokens.

EXCHANGE_POSITION_OWNERSHIP_CONFLICT — штатный fail-closed EntryDecision
outcome при admission conflict. Сам по себе он не является аварией системы.

EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN — lifecycle fault: уже
существующая durable/runtime/Exchange истина нарушила правило единственного
physical owner.

EntryDecision.EXPIRED/CANCELLED относятся к attempt до принятого request.
После ACCEPTED request использует собственные request-state tokens и не
переиспользует decision-state names.

Blocked Strategy может продолжить жизнь как Analyst counterfactual только по
явно разрешённым причинам и с сохранением exact block reason.

Переход к hedge-mode, internal netting или subaccount isolation является
отдельным owner decision и требует нового canonical/execution contract.

# 6. EXIT

`Exit Engine` — универсальный активный исполнитель `ExitPlan`.

После confirmed open fill создаётся логическая `StrategyPosition`, привязанная
к exact Strategy version, `EntryPlan`, `ExitPlan` и фактическому
exchange/order/fill lifecycle.

Exit Engine получает/claim-ит эту StrategyPosition и сопровождает её только по
exact `ExitPlan`, который был материализован из той же Strategy version.

Entry после confirmed fill сопровождением позиции не владеет.

Exit Engine может сформировать `ExitDecision` для:
- изменения stop;
- изменения TP;
- break-even;
- trailing;
- reduce;
- full close;
- time/geometry/context exit;
- hedge lifecycle,
только если соответствующее действие определено ExitPlan.

Exit Engine не изобретает торговое правило и не заменяет отсутствующее правило
старым default.

Для Strategy, которой разрешена real Exchange mutation, owner-approved
loss-containment contract обязателен. Exact значение принадлежит Strategy и не
является global default.

`protection_policy.initial_protection` должна быть поддержана Execution и
подтверждена на Exchange при открытии/сразу после fill в соответствии с
возможностями адаптера. Real activation/dispatch fail-closed, если обязательная
защита отсутствует, unsupported или её состояние нельзя доказать.

Dynamic Exit может оставаться экспериментальным/disabled, но real position не
может намеренно оставаться без утверждённого loss-containment. Уже открытая
позиция сохраняет exact ExitPlan/protection policy той Strategy version,
которая её открыла, даже если StrategyActivation позже выключена.

Hedge lifecycle исполним только при совместимом Exchange position-mode
contract. В текущем one-way same-symbol hedge unsupported и обязан fail-closed;
встречный order не может молча трактоваться как hedge.

# 7. EXECUTION

Execution — техническая граница биржевой мутации.

Он исполняет уже сформированное решение Entry или Exit и не переоценивает
рынок.

Логически различаются:
- `EntryExecutionRequest` — исполнение принятого EntryDecision;
- `ExitExecutionRequest` — исполнение принятого ExitDecision.

Оба являются видами `ExecutionRequest` и обязаны нести exact Strategy/Plan/
decision/position lineage, достаточную для idempotency, audit и reconciliation.

Execution отвечает за:
- validation identities/fingerprints;
- exchange adapter;
- order preparation/submission;
- idempotency;
- client/exchange IDs;
- fill truth;
- fees/slippage, где измеримы;
- initial protection и разрешённые protection mutations;
- retries без двойной мутации;
- reconciliation;
- durable handoff/recovery;
- technical fail-closed.

Execution не вычисляет H9/H3 как собственную policy и не придумывает
stop/TP/leverage/TTL.

Техническая возможность `EMERGENCY_CLOSE` сама по себе не даёт права её
применять автоматически. Для real Strategy заранее утверждается exact
`lifecycle_policy.emergency_policy` / protection-failure contract:
разрешённое действие, trigger/fault class, timeout/freshness и required
reconciliation. Допустимые действия могут включать повторное подтверждение
initial protection или reduce-only close, но только если они явно разрешены
Strategy policy/owner command.

Lifecycle Supervisor может обнаружить fault и инициировать предусмотренный
operational-safety workflow, но не выбирает emergency action самостоятельно.
`owner kill` останавливает разрешённые новые mutation по своему contract и
не означает автоматическое закрытие уже открытой позиции без отдельного
разрешённого действия.

## 7.1 Owner-controlled execution diagnostic

A manually confirmed owner smoke-test is a separate control-plane mutation
capability, not a Strategy execution decision.

It may call the same production Execution/private-runtime adapter to prove
Exchange order/fill/protection behavior, but it has no right to synthesize
StrategySignal, EntryDecision or StrategyPosition evidence. It must carry an
explicit diagnostic source marker, remain operator-triggered only and be
excluded from Strategy performance semantics.

Current exact limits are owned by TRADING_CONTOUR §4.8.

## 7.2 PAPER / REAL execution-mode invariant — OWNER DECISION 2026-10-06

`StrategyActivation` включает/выключает саму Strategy и её единый
`Strategy -> Entry -> Exit` lifecycle. `PAPER` и `REAL` не являются двумя
Strategy и не имеют права иметь две независимые реализации торговой policy.

Для каждого `strategy_attempt` выбирается ровно одна execution environment:

```text
StrategyActivation=ON
-> same StrategyCard / EntryPlan / ExitPlan
-> same causal market facts/history
-> same StrategySignal / strategy_attempt
-> same mode-neutral EntryExecutionIntent
-> exactly one selected execution environment:
     PAPER  [real execution permission OFF]
     XOR
     REAL   [real execution permission ON + required real gates]
```

Одновременное создание PAPER-position и REAL mutation для одного и того же
attempt запрещено. Переключение execution permission не меняет Strategy
version, Entry formula, Exit formula, geometry, order policy или lifecycle
policy.

REAL имеет дополнительные operational-safety steps, которые не являются
второй торговой логикой:

```text
AccountStateGeneration
-> physical slot claim
-> capital reservation
-> real EntryDecision
-> EntryExecutionRequest
-> Exchange mutation / reconciliation
```

PAPER не имеет права подделывать `EntryDecision=ACCEPTED`, physical slot,
capital reservation или Exchange acknowledgement. Он исполняет тот же
mode-neutral intent через simulation adapter и сохраняет явный PAPER lineage.
Если REAL выбран, но real admission blocked, запрещён silent fallback в PAPER:
фиксируется real block reason, а псевдосделка для этого attempt не открывается.

Exit policy также едина: PAPER и REAL обязаны исполнять один exact ExitPlan
через один Universal Exit decision contract. PAPER adapter симулирует
order/fill/fees, REAL adapter выполняет Exchange mutation/reconciliation.
Отдельный `PaperTradeRuntime` не имеет права владеть альтернативным trading
rule, которого нет в EntryPlan/ExitPlan.

Перед real arm обязателен `PAPER_REAL_DECISION_PARITY=PASS`: на одинаковых
причинных facts должны совпасть Strategy/Plan lineage, direction, Entry
reference/order policy/validity, lifecycle transition и ExitDecision semantics.
Разрешённые различия ограничены real-only account/admission/exchange facts,
exchange/client IDs, фактическим fill/slippage/fees и reconciliation evidence.

# 8. EXCHANGE

Exchange — внешний источник фактической истины об orders/fills/positions,
балансе и ограничениях площадки.

Архитектура выше адаптера не привязана к Bybit.

# 9. Технический поддерживающий контур

Сюда относятся:
- market-data adapters;
- account/private-state sync;
- PostgreSQL;
- active plan/position durable registries;
- atomic capital reservation;
- Lifecycle Supervisor;
- Position Supervisor;
- Monitoring/UI;
- Analyst/Research;
- service/watchdog/recovery;
- operational safety;
- архивирование и аудит.

## 9.0 Presentation UI boundary

OWNER DECISION 2026-10-05: presentation/read-model слой Dashboard физически
и release-wise отделён от trading runtime.

Dashboard presentation/read-model не владеет Strategy/Entry/Exit/Execution,
не меняет решения и не требует остановки trading services при deploy. Он может
иметь отдельную `DASHBOARD_UI_COMMIT` identity и обновляться атомарно при
работающем MICRO_LIVE/LIVE.

В independent Dashboard scope входят static assets и строго read-only
server-side projections/aggregations already-existing runtime/DB facts.
Read-only backend change может перезапустить только Dashboard service.

Authentication, control/mutation handlers, Exchange mutation, gates,
permissions, Strategy/Entry/Exit/Execution semantics и decision-affecting code
остаются частью application/trading runtime и проходят полный release gate.

## 9.1 Lifecycle Supervisor

Lifecycle Supervisor контролирует сквозную корректность lifecycle, но не
является торговым владельцем.

Единственное каноническое определение обязательной lifecycle-chain хранится
здесь. Другие активные документы обязаны ссылаться на этот раздел, а не
дублировать цепочку.

```text
StrategyActivation
-> EntryPlan + ExitPlan materialized/published
-> Entry Engine consumption acknowledgement
-> StrategySignal
-> strategy_attempt
-> required account / position-mode state validation
-> atomic physical Exchange slot claim
-> atomic capital reservation outcome
-> EntryDecision
-> EntryExecutionRequest                  [only ACCEPTED]
-> Execution acknowledgement / dispatch
-> opening order lifecycle / fill truth / reconciliation
-> StrategyPosition exact binding
-> physical slot ownership binding to StrategyPosition
-> initial protection confirmation / reconciliation
-> ExitPlan exact binding
-> Exit Engine claim / heartbeat
-> ExitDecision(s)
-> ExitExecutionRequest(s)
-> Execution acknowledgement
-> Exchange protection/reduce/close result + reconciliation
-> final flat confirmation
-> physical Exchange slot claim finalization/release
-> capital reservation finalization/release
-> final economics/audit
```

Durable lineage включает, где применимо:
strategy_activation_id, Strategy/EntryPlan/ExitPlan fingerprints, signal_id,
strategy_attempt_id, exchange_position_slot_claim_id, exchange_position_key,
position_mode_state_ref, position_mode_observed_at, positionIdx,
capital_reservation_id, Entry/Exit decision IDs, Entry/Exit request IDs,
client/exchange order IDs, fill/execution IDs, strategy_position_id,
Exit claim/heartbeat и final close/economics refs.

Терминологические определения outcome/request-state/fault tokens находятся
только в docs/CRIPTA_GLOSSARY_RU*.md.

Supervisor обязан выявлять нарушение обязательных handoff/invariants, включая:
- CAPITAL_RESERVATION_STUCK;
- EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN;
- EXCHANGE_POSITION_MODE_MISMATCH;
- POSITION_WITHOUT_EXIT_OWNER;
- POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION;
- потерянный/неподтверждённый required handoff/reconciliation.

EXCHANGE_POSITION_OWNERSHIP_CONFLICT не является lifecycle fault: это штатный
fail-closed EntryDecision outcome до создания real request.

Lifecycle Supervisor:
- не выбирает Strategy;
- не создаёт Entry/Exit decision;
- не двигает stop/TP;
- не закрывает позицию по собственной торговой оценке;
- может зафиксировать lifecycle fault и инициировать только заранее
  предусмотренный operational-safety/fail-closed workflow.

Critical fault обязан иметь durable owner-notification delivery contract:
создание alert/event, повторяемую доставку, acknowledgement владельца либо
явный escalation state. UI alone не считается доставкой. Потеря delivery сама
является operational fault и не разрешает торговую mutation.

## 9.2 Position Supervisor

`Position Supervisor` наблюдает фактическое состояние конкретной открытой
StrategyPosition и Exchange state: qty, price, protection, MFE/MAE, geometry,
context, reconciliation.

Он не является владельцем Exit policy.

## 9.3 Analyst

`Analyst` выполняет постфактум-аналитику, research и counterfactual
псевдосделки. Он не контролирует оперативную доставку и не торгует.

# 10. Разложение исторического Risk

Отдельного торгового слоя Risk нет.

Исторические обязанности распределены так:

```text
внешний рынок и account-capacity facts -> Dispatcher
Entry conditions и Strategy capital request -> Strategy/EntryPlan + Entry Engine
сопровождение открытой позиции -> ExitPlan + Exit Engine
биржевая мутация и unknown exchange state -> Execution operational safety
сквозная потеря lifecycle/handoff -> Lifecycle Supervisor + operational safety
```

# 11. Исследование не является архитектурой

Любое исследование/backtest/replay/OOS/holdout остаётся evidence до отдельного
решения владельца и новой Strategy/document version.

```text
ИССЛЕДОВАНИЕ / ДОКАЗАТЕЛЬСТВА
-> РЕШЕНИЕ ВЛАДЕЛЬЦА
-> НОВАЯ ВЕРСИЯ STRATEGY / ДОКУМЕНТА
-> ТЕСТ / SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
```

# 12. Терминология и изменение архитектуры

Канонические определения находятся в `docs/CRIPTA_GLOSSARY_RU*.md`.

Если термин отсутствует или неоднозначен — Hard Stop.

Изменение архитектуры:

```text
РЕШЕНИЕ ВЛАДЕЛЬЦА
-> ОБНОВЛЕНИЕ КАНОНА
-> АРХИТЕКТУРНАЯ ПРОВЕРКА
-> РЕАЛИЗАЦИЯ
-> ТЕСТЫ
-> GITHUB
-> DEPLOY
-> RUNTIME EVIDENCE
```

Код не является автоматическим источником новой архитектуры.
# 13. Conservation of capability ownership

Разделение ответственности не должно уничтожать системную обязанность.

Если capability удаляется из слоя A из-за правильной ownership boundary, до
decommission обязательно существует одно из двух owner-approved состояний:

~~~text
A. capability больше не нужна проекту -> explicit owner decision + canon removal

B. capability нужна ->
   exact NEW_OWNER
   + canonical contract
   + implemented consumer/provider path
   + migration plan
   + tests/evidence
~~~

Состояние OLD_OWNER=forbidden, NEW_OWNER=conceptually named,
NEW_IMPLEMENTATION=absent не является завершённой архитектурной миграцией.

Для архитектурных ревизий обязательна BEFORE / AFTER CAPABILITY MATRIX.
Потеря owner, consumer path, replay/audit path или operator-visible output для
ранее утверждённой capability означает:

~~~text
ARCHITECTURE_CAPABILITY_GAP=YES
HARD_STOP=YES
CANON_UPDATE_REQUIRED=YES
OWNER_DECISION_REQUIRED=YES
~~~

Особенно это относится к границам MAYAK -> Dispatcher -> Strategy: запрет
Strategy-specific semantics в MAYAK/Dispatcher не отменяет обязанность exact
Strategy владеть своей интерпретацией объективного market context.

# 14. Exchange-time calibration for authenticated Bybit transport

OWNER DECISION 2026-10-07: authenticated Exchange transport must not depend on
raw host wall-clock being numerically close to Bybit at every mutation.

For every mutating Bybit POST:

1. obtain a fresh public Bybit server-time observation;
2. bind that observation to a local monotonic midpoint;
3. derive the signed request timestamp from the Bybit server-time anchor plus
   monotonic elapsed time;
4. keep the existing signed `recvWindow` contract;
5. never retry a mutating POST automatically.

Host NTP remains required operational hygiene and clock-offset telemetry, but a
correctable host-vs-Bybit wall-clock offset is not itself an ambiguous mutation
and must not by itself terminate the private runtime.

The safety boundary applies to uncertainty, not to a correctable offset. If
fresh exchange-time evidence cannot be established, or the probe round-trip is
too wide for the approved uncertainty budget after bounded read-only retries,
the mutation is blocked before send and new Entry is disarmed fail-closed.

A pre-mutation safety block and an ambiguous post-send mutation are physically
different states:

```text
PRE_MUTATION_SAFETY_BLOCK
  mutation_sent = NO
  outcome_ambiguous = NO
  new Entry = DISARMED
  runtime restart = NOT REQUIRED

EXCHANGE_MUTATION_BARRIER
  mutation_sent = POSSIBLE / UNKNOWN
  outcome_ambiguous = YES
  reconciliation = REQUIRED
  new Entry = DISARMED
  private runtime restart/recovery = REQUIRED
```

Authenticated GET may retry only under its existing read-safe contract; an
explicit timestamp rejection may use a fresh Bybit-time calibration for that
read retry. WebSocket authentication uses the same exchange-time basis.

This contract changes transport safety only. It does not change Strategy,
Entry geometry, Entry lifetime, Exit, initial protection, stake, leverage or
any trading threshold.

## 46. Exchange-authoritative recovery — OWNER DECISION 2026-10-08

Server restart, deploy, resize and reconnect are normal operational events. Every configured Exchange account connector must obtain a fresh, complete, authenticated and paginated position/order/protection inventory on recovery. Exchange current state supersedes the stale local *current-state projection*, never the durable execution history.

Execution/Lifecycle uses Exchange orderId, orderLinkId and execId to correlate and reconstruct executions, closures, fees and verified P&L. Persist idempotent recovery epochs and before/after deltas. Absence from a complete inventory proves no current position, not no historical fills; missing exit accounting remains UNKNOWN and cannot be invented. New unowned positions are observed without guessed Strategy ownership. An incomplete or unavailable Exchange snapshot must not be treated as an empty account.

Unknown historical outcomes and ordinary closures during downtime must not create permanent global Entry hard stops. Isolate unresolved sent-mutation ambiguity to affected account, symbol, slot, reservation and request; no duplicate Exchange mutation or guessed release of resource ownership. A successful recovery never automatically opens the global LIVE gate. Repeated recovery must be idempotent; distinct connectors are isolated.

## 47. Closed-trade statistics attribution and operator history — OWNER DECISION 2026-10-08

The operator's closed real trades ledger must include all **exchange-evidenced** completed cycles: Strategy-owned lifecycle trades, exact recovered closed cycles, and separate owner/control-smoke trades. Every row carries its actual provenance (`STRATEGY`, `RECOVERED_UNOWNED`, `OWNER_CONTROL` or a separately proven source), exact Exchange execution/order identifiers, verified fee-inclusive result, and explicit unknowns (including unavailable funding). Do not infer Strategy ownership merely from symbol, time overlap, open-order proximity, or the existence of an active Strategy.

**Strategy performance is attribution-gated:** include a trade in the metrics of exactly its proven owning `strategy_id`, `strategy_version` and exact Strategy activation/config identity. An unowned recovery or owner/control-smoke trade is visible in the operator's all-trades history but excluded from every Strategy numerator, denominator, trade count, win rate and P&L (including R1). Each distinct Strategy calculates its own metrics independently. Unknown attribution stays UNKNOWN, never defaulted to R1. Duplicated representations of one Exchange close must be collapsed by stable, exact execution/cycle keys; replay and restart must not double-count.

Closing notifications read from the same durable, deduplicated operator ledger rather than only `position_exit_attribution`. New evidenced closures emit at most one notification per unique cycle, including control and recovered trades, regardless of selected UI tab. Page refresh, restart and reconnect must not replay old closures as new alerts. Notification delivery is separately auditable from verified closure and must not modify Exchange/Lifecycle ownership. No Strategy rules or trading thresholds are changed by this reporting contract.


## 48. Data storage, recoverability and retention — OWNER DECISION 2026-10-16

Production storage must scale to multi-venue observation without turning
PostgreSQL into a market-data lake. Long-term retention is determined by
**recoverability, audit value and exact provenance**, not merely by whether a
fact originated inside or outside CRIPTA.

Every persistent data family must be assigned to exactly one primary storage
class:

1. **CRIPTA_IRREPLACEABLE_DATA** — project-generated or project-bound evidence
   whose exact future reconstruction is impossible or not sufficiently
   guaranteed for audit/recovery. Examples include Strategy/Entry/Exit decisions,
   exact activation/config identity, ownership/lineage, execution attribution,
   operator actions, durable faults/continuity evidence, notification delivery,
   and exact event-linked context needed to explain a real decision. It must be
   preserved durably while required by the project. Old generations may leave
   hot PostgreSQL for verified cold storage, but only with manifest/hash,
   provenance and tested restore.
2. **EXTERNAL_RECONSTRUCTIBLE_DATA** — external market data whose exact required
   range can be re-acquired from an approved source with known semantics and
   provenance. It is cache/input, not permanent production truth by default:
   download/read/derive/research, then expire or delete according to bounded
   cache policy. Research still preserves input provenance, source identity,
   time range and result reproducibility.
3. **EXTERNAL_NON_RECONSTRUCTIBLE_DATA** — externally observed data for which
   exact later reacquisition is unavailable or not sufficiently guaranteed.
   Preserve only the compact exact events/facts that have justified analytical,
   audit or recovery value; do not retain an unlimited raw stream merely because
   it was observed live.
4. **DERIVED_RECOMPUTABLE_DATA** — MAYAK/Dispatcher/analytics views that can be
   deterministically recomputed from retained canonical facts plus exact
   versioned code/config/provenance. The same market fact must not be retained
   indefinitely in multiple full JSON representations. A derived object needed
   as exact evidence for a concrete signal/trade/fault may be promoted into the
   event-linked CRIPTA irreplaceable audit record.
5. **HOT_OPERATIONAL_DATA** — full current market/runtime telemetry required for
   live observation, recent diagnostics, restart/recovery and immediate operator
   work. The default maximum retention of full high-frequency market telemetry
   in hot production storage is **24 hours**. A longer full-fidelity HOT window
   requires a separate explicit owner decision and a capacity justification.

The classification test is intentionally stricter than “our data / external
data”. Exchange facts may still be irreplaceable for CRIPTA when provider
history is limited or exact internal attribution/provenance cannot be recreated.
Conversely, CRIPTA-generated derived telemetry does not become permanent merely
because CRIPTA produced it.

### 48.1 Canonical analytical representation

Long-horizon analytics must use a compact, causal, strategy-agnostic analytical
representation rather than retained full runtime snapshots. For multi-venue
operation its physical grain must preserve at least:

~~~text
observed_minute
venue/source identity
symbol
objective compact market facts/features
quality/freshness
provenance/version/fingerprint
~~~

A CRIPTA cross-venue aggregate may exist as a separate derived object, but must
not erase the venue-level lineage required to reproduce it.

The compact analytical representation keeps the dimensions actually required
for replay/research (for example price path/returns, executed flow, OI/funding,
mark/index/basis where applicable, compact liquidity features, exact liquidation
aggregates/events where available, quality and provenance). It does **not**
require permanent retention of raw public trades, full order-book tapes or
several equivalent MAYAK/Dispatcher JSON payloads.

Higher horizons, MFE/MAE, H3/H9 geometry and Strategy outcomes are recomputed
from canonical facts and exact versioned algorithms when that is sufficient.
Strategy/event-specific geometry or outcome evidence actually used by a real
decision remains in its durable audit lineage.

Exact long-term retention of the compact analytical dataset is a separate
capacity/research decision; this section does not silently establish an
unbounded data lake. Reacquirable heavy external input may follow:

~~~text
DOWNLOAD / STREAM
-> VERIFY SOURCE + PROVENANCE
-> CAUSAL DERIVATION / RESEARCH
-> PRESERVE RESULT + REPRODUCIBILITY EVIDENCE
-> EXPIRE RAW CACHE
~~~

### 48.2 PostgreSQL boundary and duplicate-storage prohibition

PostgreSQL is the operational database, not the permanent repository for
multi-month high-frequency market archives. It owns current state, recent HOT
telemetry and durable queryable project/audit facts that need database
semantics. Older CRIPTA irreplaceable history may be offloaded to verified cold
storage when hot queryability is no longer required.

A single objective fact must have one canonical durable representation.
MAYAK/Dispatcher/runtime may expose multiple live/read views, but this does not
authorize indefinite physical duplication of the same minute/fact in several
large JSONB tables.

Adding a new venue, symbol, observer or context layer must include a storage
budget and class assignment. Capacity planning must assume the configured
multi-venue target, not only the current one-venue load.

### 48.3 Deletion / migration safety

This owner decision defines the target architecture; it does **not** authorize
immediate deletion of existing history.

Before changing or purging current PostgreSQL history:

1. inventory exact readers, foreign keys, append-only triggers and report/replay
   consumers;
2. build/verify the replacement compact analytical path where required;
3. preserve CRIPTA irreplaceable and selected non-reconstructible evidence;
4. prove source/provenance/replay equivalence for data being represented in a
   new form;
5. create and verify the required backup/rollback checkpoint under
   DEVELOPMENT_RELEASE;
6. apply retention/schema migration with the approved DB/filesystem actors;
7. prove reclaimed disk space and runtime/report/research behavior;
8. observe disk growth and retention behavior for at least the first **7 days**
   after cutover before declaring the storage model operationally stable.

No cleanup, PostgreSQL rewrite, service restart, LIVE arm or Exchange mutation is
authorized by this documentation decision alone.

## 49. Venue / MarketSource / TradingAccount identity — OWNER DECISION 2026-10-16

CRIPTA separates market identity, private execution identity and Strategy
identity. These axes may be linked, but must not be collapsed into one
identifier.

~~~text
MARKET
Venue -> MarketSource -> InstrumentIdentity -> market facts/context

PRIVATE EXECUTION
Venue -> TradingAccount -> AccountConnector -> private account/execution truth

STRATEGY
StrategyCard -> StrategyActivation -> StrategyExecutionBinding
                                     -> ExecutionLeg -> TradingAccount
~~~

### 49.1 Venue and market-source boundary

Venue is the physical trading venue/protocol on which an instrument exists.
It may be a centralized exchange or a decentralized/on-chain venue.

MarketSource is the exact source of public/objective market facts. One Venue may
have multiple MarketSources (for example direct public feed, approved historical
archive or another provider carrying exact venue-origin data). MarketSource
identity must preserve provider/origin semantics and provenance.

Public market facts are scoped by MarketSource + InstrumentIdentity + event/time
identity, not by CRIPTA TradingAccount. Five Bybit subaccounts therefore consume
the same Bybit public market stream where source semantics are identical; CRIPTA
must not persist five copies merely because five private accounts exist.

Different Venues remain distinct market sources even when prices are highly
correlated. A cross-venue context/aggregate is DERIVED_RECOMPUTABLE_DATA and must
preserve the exact contributing venue/source lineage. This is required for
future divergence, lead/lag, basis and arbitrage research.

### 49.2 TradingAccount and AccountConnector

TradingAccount is CRIPTA's stable opaque identity for one exact private execution
account: CEX main/subaccount or an approved on-chain wallet execution identity.
It is not the venue name, account type, API key, credential name or a composite
such as BYBIT:UNIFIED.

Each TradingAccount carries or resolves, where applicable:

~~~text
trading_account_id
venue_id
external_account_ref
parent_trading_account_id
account_kind
account_type
environment
status
~~~

External/provider account identifiers remain provenance, not CRIPTA's primary
identity. Unknown historical account identity stays UNKNOWN/LEGACY; migration
must not infer a specific subaccount from symbol/time proximity.

AccountConnector is the runtime/config identity that obtains private account
state and/or performs authorized mutations for one TradingAccount. Credential
rotation or connector replacement does not change trading_account_id.
Credentials remain outside durable public/project identity and follow SECURITY.

AccountStateGeneration, wallet/capital state, position mode, reconciliation,
orders, fills, fees, protection, positions and realized account economics are
TradingAccount-scoped facts.

### 49.3 StrategyExecutionBinding and ExecutionLeg

StrategyCard defines trading logic and must not embed the physical account as a
result-affecting Strategy parameter.

A REAL StrategyActivation receives one or more explicit
StrategyExecutionBinding records. A binding maps an ExecutionLeg to an exact
TradingAccount for an effective interval and execution role.

The normal single-venue form is:

~~~text
StrategyActivation -> PRIMARY ExecutionLeg -> one TradingAccount
~~~

A multi-leg Strategy may use multiple TradingAccounts/Venues, for example
arbitrage:

~~~text
StrategyActivation
  -> LEG_A -> TradingAccount on Venue A
  -> LEG_B -> TradingAccount on Venue B
  -> optional LEG_C -> approved DEX/on-chain TradingAccount
~~~

For REAL execution the default isolation contract is DEDICATED_STRATEGY:
one TradingAccount is not concurrently shared by independent REAL
StrategyActivations. Shared-account netting/allocation requires a separate
owner-approved contract. This restriction does not prevent one Strategy from
owning multiple TradingAccounts as distinct ExecutionLegs.

PAPER has no real TradingAccount mutation rights; a simulated account identity
must never be accepted as evidence for REAL admission.

### 49.4 Account-scoped physical keys

Any physical position/order/execution identity that is not proven globally
unique by provider contract must be scoped by Venue and TradingAccount as
required. A physical position slot is conceptually identified by at least:

~~~text
TradingAccount
product/category
InstrumentIdentity
position mode / positionIdx
~~~

exchange_position_slot_claim_id remains the immutable identity of the claim
event/ownership record. A semantic exchange_position_key must not collapse
different TradingAccounts into the same physical slot.

Likewise Exchange orderId/execId uniqueness must not be assumed outside the
exact provider/account scope. Adapter contracts must state the required
uniqueness scope.

### 49.5 Storage consequence

MarketSource data and TradingAccount private data have independent storage and
retention lifecycles.

- public market analytics grain is MarketSource + InstrumentIdentity + time;
- private account state/audit grain includes TradingAccount;
- Strategy trade/audit lineage references both the consumed market evidence and
  the exact TradingAccount/ExecutionLeg used for REAL execution;
- account count must not multiply stored public market telemetry;
- venue count may multiply venue-specific market facts because those differences
  are analytically meaningful.

ARCH §48 storage/recoverability rules remain controlling. This section changes
identity/ownership only and does not authorize DB migration, deletion, deploy,
LIVE arm or Exchange mutation.

### 49.6 Independent Strategy configurations and multi-account fan-out — OWNER DECISION 2026-10-20

Strategy duplication is a **creation-time UI operation** ("create based on"), not an
inheritance relation between running strategies. Saving a copy creates a new
strategy_id and separately versioned StrategyCard/configuration snapshot even if
all logic parameters match. Origin/version lineage is audit metadata only;
changes to the source Strategy never silently mutate its independently saved
descendants. Strategy display name is operator-friendly, not a primary identity.

One StrategyActivation MAY select many authorized StrategyExecutionBindings,
each identifying an exact TradingAccount and AccountConnector. Its approved
EntryPlan may fan out to eligible accounts, producing separately identifiable
per-account execution requests, orders, protection, position lifecycle and Exit.
It does not imply atomic multi-Venue fills or synchronized hedge/arbitrage.
An AccountConnector is technical transport/adapter identity, NOT a pooled
balance; financial isolation is keyed by TradingAccount. The same TradingAccount
MUST NOT concurrently serve two independent REAL StrategyActivations. One
Strategy may have multiple accounts, including several subaccounts at one Venue.

A Strategy configuration owns permitted instruments, stake sizing, leverage,
capacity/concurrency, direction and separately approved one-way/hedge rules.
There is NO universal "one instrument per Strategy" or "one position per
connector" limit. Effective admission enforces the exact account + instrument +
exchange position-mode/side physical slot and the Strategy-owned policy; existing
physical slot ownership is not discarded. One-way must not create a second
independent overlapping position for the same slot. Opposite LONG/SHORT
execution is allowed only with explicit Strategy hedge policy AND proven
account/venue support. Execution checks margin, leverage, capacity, available
capital and exchange-supported protective orders per account, without altering
Strategy entry decisions. Failure at one permitted account never fabricates
success at another.

Every physical position/order retains exact StrategyActivation, configuration
fingerprint, ExecutionLeg, TradingAccount, AccountConnector, EntryPlan and
ExitPlan lineage. Reports attribute net P&L, fees, funding, losses and capital to
both the independent Strategy ID and exact account. Changing only TP/SL,
leverage, stake, permitted coins or connectors in a newly saved Strategy
does not change other Strategy identities or the shared Universal Entry/Exit
Engines. Opening is not reported protected until exchange protection evidence
is confirmed; Exchange stop orders reduce, but cannot eliminate, liquidation,
gap, outage or execution risk.

No creation, copying, binding or release process automatically enables REAL.
New REAL authorization is exact-release, exact-Strategy and explicit OWNER ARM.
This section defines target semantics, not evidence that existing consumers,
schema, adapters or dashboard implement multi-account fan-out.

## 50. One-time PostgreSQL physical offload — OWNER DECISION 2026-10-18

Approved bounded infrastructure recovery: after safely quiescing all CRIPTA writers and PostgreSQL, preserve the complete current production cluster as a verified cold rollback on the Data SSD, then establish a separately verified minimal operational PostgreSQL on the System SSD. This may precede unfinished R10b/R11/R12 ONLY as physical storage recovery; it does not constitute R11 historical purge, logical retention, historical account reassignment or R12 retirement. ARCH §48–49 remain binding.

Never discard irreplaceable trading, execution, fee, ownership, operator, safety, strategy or event-linked audit evidence. No trading/analytics/statistics source code, Strategy policy or LIVE state changes are approved by this decision. Independence of exchange position/order checks before stopping Exit owner, complete physical rollback, full writer/service/timer inventory, exact actor/path preflight, enough disk headroom, complete minimal-DB consumer contract, permissions and tested recovery are mandatory. Unknowns are HARD STOP. Historical original PGDATA may leave System SSD only AFTER an independently tested complete physical copy is secured; isolated validation copy may be deleted after positive identification and backup verification. Restart only verified services in safe sequence; no LIVE arm. R10–R13 remain open independently.
