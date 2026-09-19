# CRIPTA — верхние архитектурные правила

**Версия:** 2.4
**Дата:** 2026-09-19
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
- Entry admission после истечения freshness;
- private-state reconnect/recovery, если continuity не доказана;
- обнаруженном Exchange/account configuration change;
- снятии position-mode-related fail-closed state.

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
