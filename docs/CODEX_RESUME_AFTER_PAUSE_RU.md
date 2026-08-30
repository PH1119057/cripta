# CODEX — ПРОДОЛЖЕНИЕ ПОСЛЕ ПАУЗЫ ЛИМИТА

**Дата:** 2026-08-30  
**Назначение:** короткий handoff, не новое большое ТЗ.

## Текущая ситуация

По сообщению владельца проекта:

- предыдущая большая задача Codex не отменена;
- шесть её шагов завершены;
- шаг 7 уже начат и был остановлен только лимитом токенов;
- продолжать нужно с текущей точки, а не начинать задачу заново.

## Что происходило во время паузы

Во время паузы Codex **runtime проекта этими документами не изменялся**.

Подготовлены новые архитектурные документы:

1. `docs/DATA_TIMELINE_CONTRACT_RU.md`
2. `docs/ARCHIVE_V2_ARCHITECTURE_RU.md`
3. `docs/ANALYST_V1_IMPLEMENTATION_CONTRACT_RU.md`
4. `docs/M3_ENVIRONMENT_RESEARCH_PROTOCOL_RU.md`
5. `docs/MAYAK_DATA_SOURCE_MATRIX_RU.md`
6. `docs/ANALYST_ARCHITECTURE_RU.md` — если ещё не установлен
7. `docs/BYBIT_PUBLIC_DATA_FOR_MAYAK_DISPATCHER_RU.md` — если ещё не установлен

## Перед продолжением шага 7

1. Прочитать текущий `AGENTS.md`.
2. Прочитать `PROJECT_ARCHITECTURE_RU.md`.
3. Прочитать `MAYAK_ARCHITECTURE_PRINCIPLES_RU.md`.
4. Прочитать `STRATEGY_DISPATCHER_ARCHITECTURE_RU.md`.
5. Прочитать перечисленные новые документы.
6. Сверить их с уже сделанными шагами.
7. **Не откатывать и не переделывать завершённые шаги без реального конфликта.**
8. Продолжить именно начатый шаг 7.

## Новые обязательные ограничения

- Анализ и корреляция идут по единой UTC timeline, но каждый event имеет отдельный ID.
- `OBSERVED_CONTEXT` и `CONSUMED_CONTEXT` различаются.
- Archive V2 логически разделяет CODE / LIVE_EVIDENCE / PostgreSQL backup / RESEARCH / LOGS.
- Analyst — read-only post-factum layer; не имеет live trading rights.
- M3 environment research: четыре профиля ENTRY/HOLD × LONG/SHORT, сначала SHADOW, без придуманных thresholds.
- Новые Bybit source layers сначала принадлежат Mayak; стратегия не читает raw Bybit data как обход Dispatcher.
- Результаты сделок не ретюнят Mayak/Dispatcher автоматически.

## Важно

Если текущая реализация шага 7 уже отличается от документов, сначала определить:

```text
REAL CONFLICT
или
DOCUMENT DESCRIBES FUTURE WORK
```

Не создавать новый hotfix-chain только ради косметического совпадения документа.

## Отчёт шага 7

После завершения дать коротко:

- что было состоянием до паузы;
- что добавлено после возобновления;
- какие новые документы учтены;
- exact checks;
- что осталось шагом 8 или далее;
- commit/push;
- trading effect.
