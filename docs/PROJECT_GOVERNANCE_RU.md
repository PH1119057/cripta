# УПРАВЛЕНИЕ ИЗМЕНЕНИЯМИ ПРОЕКТА CRIPTA

**Документ:** `PROJECT_GOVERNANCE_RU.md`
**Версия:** 1.2
**Дата:** 2026-09-05
**Статус:** канонический нормативный контракт

## 1. Виды истины

- Документация определяет, что система обязана делать.
- Исходный код определяет, как утверждённый контракт реализован.
- Подключённая торговая площадка вместе с PostgreSQL фиксируют, что фактически произошло.
- Статистика и исследования оценивают, насколько хорошо это сработало.

Статистика не имеет права напрямую менять live-поведение.

## 2. Обязательный pre-read

Перед архитектурно чувствительным изменением исполнитель читает:

1. `CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md`;
2. `CRIPTA_ARCHITECTURE_RULES_RU_V1.md`;
3. `AGENTS.md`;
4. `docs/DOCUMENT_AUTHORITY_RU.md`;
5. `docs/CURRENT_PROJECT_MAP_RU.md`;
6. `docs/PROJECT_ARCHITECTURE_RU.md`;
7. специализированные контракты затрагиваемых компонентов.

## 3. Каноническая верхняя архитектура

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

Это пять верхних уровней.

Технический поддерживающий контур не является дополнительным торговым уровнем.

## 4. Права разработчика

Разработчик не имеет права самостоятельно:

- создавать новый top-level trading layer;
- превращать `Risk` в самостоятельного архитектурного владельца;
- давать Dispatcher торговые mutation rights;
- давать MAYAK торговые mutation rights;
- менять Strategy на основании статистики без новой owner-approved version;
- смешивать Entry одной Strategy и Exit другой;
- привязывать универсальную архитектуру к одной конкретной бирже;
- вводить скрытый лимит количества Strategy/bots/positions как архитектурный факт;
- проектировать allocator/strategy arbitration без отдельного задания.

Если код уже делает что-то из перечисленного, это finding и hard stop для дальнейшего изменения в этой области.

## 5. Прикладной и технический контуры

Прикладной контур владеет смыслом торгового решения.

Технический контур владеет доставкой данных, exchange/account sync, storage, execution mechanics, safety, observation и recovery.

Технический контур не получает права изменить торговую policy.

## 6. Strategy ownership

Strategy version/fingerprint является owner-approved policy.

Внутри конкретной торговой попытки:

- Entry принимает решение об открытии;
- Exit сопровождает и закрывает по той же strategy binding.

Параметры размера, leverage, stop, допустимой просадки и holding относятся к Strategy.

## 7. Деньги и Dispatcher

Фактическое состояние денег принадлежит внешней торговой площадке как live truth.

Technical account-sync получает/нормализует его.

Dispatcher публикует snapshot торговой ёмкости:

- всего;
- занято;
- зарезервировано;
- свободно;
- доступно для новой торговли;
- freshness/source.

Это показатель, а не mutation.

Entry может отказать attempt по причине `INSUFFICIENT_AVAILABLE_FUNDS`.

## 8. Signal lifecycle

До fill существует полноценная торговая attempt.

Нельзя журналировать только состоявшиеся сделки.

Целевая причинная связь:

```text
signal_id
-> strategy_attempt_id
-> strategy binding
-> Entry decision
-> optional command/fill/position
-> optional Exit
-> continued observation
```

## 9. Точные IDs

Ownership нельзя восстанавливать по `symbol + время`.

Используются точные IDs signal/attempt/strategy/command/order/execution/trade/position/exit.

## 10. Архитектурное изменение

Порядок:

```text
OWNER DECISION
-> CANONICAL DOCUMENT
-> VERSION
-> ARCHITECTURE TEST
-> IMPLEMENTATION
-> CHECKS
-> GITHUB CHECKPOINT
-> DEPLOY
-> RUNTIME EVIDENCE
```

## 11. Исследования и live

```text
STATISTICS
-> RESEARCH
-> NEW OWNER-APPROVED VERSION
-> SHADOW
-> LIVE EQUIVALENCE
-> MICRO_LIVE
-> LIVE
```

## 12. Масштаб

Количество Strategy, bot instances и simultaneous positions архитектурно не ограничивается этим документом.

Текущая реализация может иметь более узкие ограничения. Их нельзя выдавать за вечное правило проекта.

## 13. Source / installed / loaded

Нормальное production-состояние должно различать:

```text
REMOTE_HEAD
SOURCE_HEAD
INSTALLED_COMMIT
LOADED_COMMIT
```

## 14. Exchange neutrality

В нормативной архитектуре использовать `Exchange / Trading Account`, а не конкретный бренд, кроме документации конкретного adapter/runtime.

## 15. Конфликт

При конфликте:

```text
ARCHITECTURE_CONFLICT=YES
HARD_STOP=YES
```

Код не используется как автоматический источник новой архитектуры.
