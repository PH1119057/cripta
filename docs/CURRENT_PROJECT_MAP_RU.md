# CRIPTA — текущая карта проекта

**Версия:** 8.0  
**Дата:** 2026-09-18  
**Статус:** текущая карта реализации; не заменяет архитектурный контракт

# 1. Source of truth

```text
GitHub PH1119057/cripta:main
==
синхронизированный /srv/cripta/source_checkout
```

Installed runtime и PostgreSQL проверяются отдельно от source.

# 2. Верхняя архитектура

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

# 3. Документационный контур

После ревизии 2026-09-18 активным считается только комплект из
`docs/DOCUMENTATION_INDEX_RU.md`.

Прежняя markdown-документация до ревизии удалена из текущего дерева и сохранена
в Git history/отдельном историческом архиве. Она не является текущей инструкцией.

# 4. MAYAK

Текущая архитектура MAYAK — strategy-agnostic объективное наблюдение.
Он не имеет торговых mutation rights.

Точные текущие services/schema/version перед изменением MAYAK проверяются по
runtime/source, а не копируются из старого research report.

# 5. Dispatcher

Текущая архитектура Dispatcher — strategy-agnostic:
- global market context;
- per-coin context;
- trading capacity snapshot;
- объективный rating только после отдельного утверждения формулы.

Старый profile/suitability Dispatcher не является текущей архитектурой.

# 6. Strategy / Universal Entry

В source существует Universal Entry contour:
- immutable StrategyCard;
- StrategyActivation;
- materialization EntryPlan/ExitPlan;
- ActivePlanRegistry;
- `UniversalEntryEngine`;
- `ParameterizedCausalMarketWatch`;
- StrategySignal/Attempt/Decision/ExecutionRequest;
- PostgreSQL strategy_entry evidence/read-model;
- Strategy dashboard/monitoring;
- execution bridge.

Фактический source-код подтверждает принцип:
`ParameterizedCausalMarketWatch` не выбирает Strategy и получает торговые
числа через EntryPlan.

`UniversalEntryEngine` обрабатывает все `plans_for(symbol)` и создаёт
strategy-specific StrategySignal при выполнении правил плана.

Execution bridge валидирует immutable Strategy/Plan identity и не использует
legacy global trading settings как fallback.

# 7. Текущая исследуемая первая Strategy

Текущая геометрическая Strategy, которую владелец продолжает прорабатывать,
использует H9 как одинаковую временную глубину:

```text
H9 = 9 часов = 540 минут

5m component  = 108 закрытых 5m свечей
15m component = 36 закрытых 15m свечей
```

Совмещение 5m и 15m формирует геометрию этой Strategy.

Это не универсальная константа Entry Engine.

Исторический Entry V1 с 130 барами 5m и 130 барами 15m относится к старому
исследовательскому/implementation этапу и не определяет H9.

# 8. H3

```text
H3 = 3 часа = 180 минут
```

H3 является совмещением 5m- и 15m-геометрий на одинаковой глубине 3 часа.

H3 сейчас не является условием Entry текущей первой Strategy.
Она относится к сопровождению/Exit research.

# 9. Стабилизация

Стабилизация используется для подавления вибрации геометрии:
зона/геометрия должна не изменяться заданное Strategy время в минутах.

Конкретные найденные значения являются параметрами конкретной Strategy и не
фиксируются здесь как глобальный канон.

# 10. Post-fill geometry

Фактическая Entry price фиксируется навсегда как факт сделки.

Текущая H9 после Entry продолжает пересчитываться и может многократно двигаться.
Для сопровождения требуется хранить текущие snapshots и сравнивать их с fixed
Entry price и предыдущим состоянием.

Старое правило о необходимости сопровождать только причинную «ту же исходную
Entry-зону» больше не является каноном.

# 11. Execution / Exchange

Execution получает только уже сформированный ExecutionRequest и использует
Strategy-owned параметры.

Bybit — текущий подключённый provider, но не архитектурная константа.

# 12. Analytics

Analyst/Research — поддерживающий контур без торговых прав.

Никакое исследование, включая сегодняшнее, не является архитектурой или Strategy
до отдельного решения владельца.

# 13. Граница этой ревизии

Документационная ревизия:
- меняет документационный канон;
- не меняет production source logic;
- не меняет Strategy records в PostgreSQL;
- не активирует real Execution;
- не меняет runtime services;
- не переименовывает исторические IDs/DB rows задним числом.

Следующий шаг после замены Project Source — ревизия ChatGPT Project instructions
под новый комплект.

# 14. Проверенный runtime checkpoint 2026-09-18

Этот раздел фиксирует только ключевые факты безопасности/активности на момент
документационной ревизии. Он не превращает изменяемое runtime-состояние в
архитектурную константу.

Проверено на сервере:

```text
cripta-mayak-v2.service                         active/running
cripta-dispatcher-v2.service                    active/running
cripta-dispatcher-v2-context-correlator.service active/running
cripta-universal-entry-observer.service         active/running
cripta-universal-entry-consumer.service         disabled/inactive
cripta-private-runtime.service                  active/running
cripta-exit-runtime.service                     active/running
```

Торговые разрешения:

```text
strategy_entry.execution_permissions: enabled = 0, total = 0
control.execution_gates mainnet: enabled = 0
```

Следовательно, работа observer/private-state/Exit runtime сама по себе не
означает разрешение новой real Entry через Universal Entry.

На сервере также существует активный технический service identifier:

```text
cripta-m3-trade-analyst.service
```

Это legacy-имя, возникшее на историческом этапе. Оно не создаёт сущность
`M3` и не отменяет словарь. Переименование systemd/service/code identifiers
требует отдельной migration-задачи с проверкой ссылок, state и operational
совместимости; документационная ревизия этого не делает.
