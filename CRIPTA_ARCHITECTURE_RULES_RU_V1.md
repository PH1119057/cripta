# CRIPTA — верхние архитектурные правила

**Версия:** 2.1  
**Дата:** 2026-09-18  
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

# 5. ENTRY

`Entry Engine` — универсальный активный исполнитель `EntryPlan`.

Количество Strategy ему не важно. Он получает активные планы, независимо
проверяет каждый план на причинных данных и при выполнении условий создаёт
strategy-specific `StrategySignal`, attempt и `EntryDecision`.

Entry не выбирает winner/priority между Strategy и не устраняет конфликт
LONG/SHORT между независимыми Strategy.

## 5.1 Капитал

Запрошенный размер капитала принадлежит Strategy/EntryPlan.

Перед принятым real Entry выполняется атомарная техническая reservation
доступного капитала.

Правило V1:

```text
первый Entry, успешно получивший atomic reservation,
получает разрешённую Strategy сумму
```

Нет дополнительного ранжирования Strategy.

Если доступного капитала недостаточно:
- real Entry не создаёт биржевую мутацию;
- `EntryDecision = INSUFFICIENT_AVAILABLE_FUNDS`;
- событие может продолжить жизнь как counterfactual/псевдосделка в Analyst,
  без Execution и без reservation.

Reservation не освобождается при неизвестном результате ордера до
reconciliation истины.

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

Initial protection, требуемая Strategy, должна устанавливаться через Execution
при открытии позиции и оставаться защитным каркасом, пока Exit Engine не
потребовал разрешённую ExitPlan мутацию.

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

`Lifecycle Supervisor` контролирует сквозную корректность жизненного цикла,
но не является торговым владельцем.

Его область наблюдения:

```text
StrategyActivation
-> plan materialization/publication
-> Entry Engine consumption
-> StrategySignal / EntryDecision
-> EntryExecutionRequest
-> Execution acknowledgement
-> order / fill
-> StrategyPosition
-> ExitPlan binding
-> Exit Engine claim
-> ExitDecision
-> ExitExecutionRequest
-> close/reduce/protection execution
-> final close
-> final economics/audit
```

Он проверяет exact IDs/fingerprints, обязательные handoff/acknowledgement и
отсутствие потерянных/осиротевших lifecycle-состояний.

Lifecycle Supervisor:
- не доставляет торговый смысл;
- не выбирает Strategy;
- не создаёт Entry/Exit decision;
- не двигает stop/TP;
- не закрывает позицию по собственной торговой оценке;
- может зафиксировать lifecycle fault и инициировать предусмотренный
  operational-safety/fail-closed путь, не изобретая торговую policy.

Надёжная доставка обеспечивается durable storage/registry/queue,
idempotency и acknowledgement. Supervisor проверяет, что этот механизм
сработал.

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
