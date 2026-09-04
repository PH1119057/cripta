# УПРАВЛЕНИЕ ИЗМЕНЕНИЯМИ ПРОЕКТА CRIPTA

**Документ:** `PROJECT_GOVERNANCE_RU.md`
**Версия:** 1.1
**Дата:** 2026-09-05
**Статус:** канонический нормативный контракт

## 1. Четыре вида истины

- Документация определяет, что система обязана делать.
- Исходный код определяет, как утверждённый контракт реализован.
- Bybit вместе с PostgreSQL фиксируют, что фактически произошло.
- Статистика и исследования оценивают, насколько хорошо это сработало.

Статистика не имеет права напрямую менять live-поведение. Разрешённый путь:

```text
СТАТИСТИКА -> ИССЛЕДОВАНИЕ -> ДОКУМЕНТИРОВАННАЯ НОВАЯ ВЕРСИЯ
-> РЕАЛИЗАЦИЯ -> ТЕСТ -> SHADOW -> ЭКВИВАЛЕНТНОСТЬ СИГНАЛОВ
-> MICRO_LIVE -> РЕШЕНИЕ ВЛАДЕЛЬЦА -> LIVE
```

Самонастройка рабочего runtime запрещена.

## 2. Общая точка разработки

GitHub `main` — общая точка синхронизации владельца, Codex, входящего ревью и
будущих разработчиков. Перед изменением каждый исполнитель обязан:

1. определить текущий GitHub HEAD;
2. прочитать `CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`;
3. прочитать `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`;
4. прочитать `AGENTS.md`, `docs/DOCUMENT_AUTHORITY_RU.md` и
   `docs/CURRENT_PROJECT_MAP_RU.md`;
5. прочитать контракты затрагиваемых слоёв;
6. объявить архитектурное влияние;
7. только после этого менять реализацию.

Решение, оставшееся только в чате, не является долговременной спецификацией.
После утверждения владельцем оно переносится в каноническую документацию.

## 3. Обязательный порядок архитектурного изменения

```text
РЕШЕНИЕ ВЛАДЕЛЬЦА -> ИЗМЕНЕНИЕ КАНОНИЧЕСКОГО ДОКУМЕНТА
-> ВЕРСИЯ КОНТРАКТА -> АРХИТЕКТУРНЫЙ ТЕСТ -> РЕАЛИЗАЦИЯ
-> ПРОВЕРКИ -> GITHUB CHECKPOINT -> DEPLOY -> RUNTIME EVIDENCE
```

Если действующий контракт запрещает запрос, файлы не изменяются. Исполнитель
сообщает `ARCHITECTURE_CONFLICT=YES`, точное требование, точный запрет и
`OWNER_DECISION_REQUIRED=YES`.

## 4. Источник, установленная и загруженная версии

Нормальное production-состояние:

```text
REMOTE_HEAD = SOURCE_HEAD = INSTALLED_COMMIT = LOADED_COMMIT
```

Любое различие означает `DESYNC=YES`. Один файл `.installed_commit` не доказывает,
что процесс загрузил этот код. Git-источник, установленный файл и фактически
работающий процесс проверяются отдельно. Большой Git-синхронизатор не входит в
этот контракт.

## 5. Версии слоёв и происхождение данных

Зрелая платформа независимо различает как минимум:

```text
PLATFORM_ARCHITECTURE_VERSION
MAYAK_VERSION
DISPATCHER_VERSION
STRATEGY_ID / STRATEGY_VERSION
ENTRY_VERSION / EXIT_VERSION / RISK_VERSION / EXECUTION_VERSION
SUPERVISOR_VERSION
SIGNAL_LIFECYCLE_SCHEMA_VERSION
ANALYTICS_SCHEMA_VERSION
DATABASE_SCHEMA_VERSION
```

Новая lifecycle-запись обязана хранить достаточно provenance, чтобы установить
создавшие её код, стратегию, настройки и версии контрактов. Это требование не
разрешает строить единый тяжёлый framework версий без отдельной задачи.

## 6. Владение сквозным жизненным циклом

До fill торговая стратегия владеет решением `ALLOW/BLOCK`. Маяк только наблюдает,
Диспетчер только даёт рекомендацию; их торговое влияние равно `NONE`.

Execution владеет командой, запросом бирже, exchange/client ID, фактическими
исполнениями, средней ценой и количеством. После подтверждённого fill владение
Entry заканчивается. Durable handoff обязан сохранять точные `signal_id`,
`strategy_decision_id`, `entry_command_id`, exchange order/execution IDs,
`trade_id`, `position_id`, actual fill/qty/time, начальную защиту, protection IDs,
версию стратегии и неизменяемую геометрию Entry.

После confirmed fill Entry больше не владеет позицией. Execution владеет
фактической биржевой мутацией, fill, exchange/client IDs, reconciliation,
initial server-side protection и durable handoff. Exit владеет protection
transitions, economic break-even, trailing, close и restart recovery в пределах
утверждённого Exit contract. Risk владеет допустимым денежным риском и risk
limits. Position Supervisor только наблюдает, формирует контекст и рекомендации
и не владеет close, stop, trailing, Risk или Entry.

Закрытие проходит через Execution и фактические исполнения Bybit, точную связь с
позицией, состояние `CLOSED`, экономику и read-only Аналитик. Параллельные
противоречащие друг другу state machine запрещены.

### Нерешённый вопрос рыночной общесистемной безопасности

`GLOBAL_SAFETY_ARCHITECTURE_STATUS=OWNER_DECISION_REQUIRED`.

Operational safety остаётся fail-closed при неизвестном exchange state, сбое
часов или reconciliation, неизвестных qty/fill/protection, stale mandatory
private state, owner emergency kill либо невозможности безопасной биржевой
мутации. Право отдельного рыночного/fleet-слоя выполнять `BLOCK_NEW_ENTRIES` или
`EMERGENCY_CLOSE` по рыночному контексту этим документом не предоставляется.
Mayak и Dispatcher торговых прав не получают.

## 7. Точные связи и честная неопределённость

Каноническая связь по `symbol + ближайшее время` запрещена. Используются точные
ID сигнала, решения, команды, ордеров, исполнений, позиции, сделки и защит.
Если доказать связь нельзя, сохраняется `UNRESOLVED_EXACT_LINK`; догадка не
становится истиной PostgreSQL.

## 8. Ручные действия владельца

Ручное изменение стопа или trailing, ручное закрытие и отмена заявки являются
отдельными событиями `OWNER_MANUAL_INTERVENTION`. Они содержат точные exchange
и command IDs и не приписываются алгоритму.

## 9. Неизменяемые регрессионные пути

Обязательные golden paths:

1. сигнал -> заявка -> TTL -> `NO_FILL/CANCELLED`;
2. сигнал -> fill -> ownership -> server protection -> прибыльный exit -> `CLOSED`;
3. сигнал -> fill -> initial hard stop -> `CLOSED`;
4. валидный сигнал + Dispatcher `INCOMPATIBLE` -> наблюдение записано, Entry не подавлен;
5. позиция -> действие владельца -> exchange execution -> точное `CLOSED`.

## 10. Изменение контракта

Изменение этого документа требует решения владельца, новой версии, обновления
зависимых контрактов и архитектурных тестов до реализации.
