# КРИПТА — АВТОРИТЕТНОСТЬ ДОКУМЕНТОВ

**Документ:** `DOCUMENT_AUTHORITY_RU.md`  
**Версия:** 1.2
**Дата:** 2026-08-31  
**Назначение:** не позволять старым research/runbook документам становиться случайным текущим ТЗ.

---

## 1. Порядок авторитетности

Если документы противоречат друг другу:

```text
1. текущий AGENTS.md
2. PROJECT_ARCHITECTURE_RU.md
3. специализированный текущий архитектурный контракт слоя
4. текущий implementation contract
5. текущий research protocol
6. исторические runbooks / Pxx / EO / SE документы
7. старые handoff / resume / package metadata
```

Текущий `/srv/cripta` остаётся source of truth по установленному коду.

---

## 2. CANONICAL ARCHITECTURE

Считать действующими:

```text
PROJECT_ARCHITECTURE_RU.md
PROJECT_GOVERNANCE_RU.md
MAYAK_ARCHITECTURE_PRINCIPLES_RU.md
STRATEGY_DISPATCHER_ARCHITECTURE_RU.md
DATA_TIMELINE_CONTRACT_RU.md
SIGNAL_LIFECYCLE_CONTRACT_RU.md
ANALYST_ARCHITECTURE_RU.md
ARCHIVE_V2_ARCHITECTURE_RU.md
```

---

## 3. ACTIVE SUPPORTING CONTRACTS

```text
ANALYST_V1_IMPLEMENTATION_CONTRACT_RU.md
M3_ENVIRONMENT_RESEARCH_PROTOCOL_RU.md
MAYAK_DATA_SOURCE_MATRIX_RU.md
BYBIT_PUBLIC_DATA_FOR_MAYAK_DISPATCHER_RU.md
STRATEGY_DISPATCHER_MARKET_VOCABULARY_RU.md
STRATEGY_DISPATCHER_PROFILE_GUIDE_RU.md
```

Наличие implementation/research contract не означает разрешение немедленно менять live trading.

---

## 4. HISTORICAL / RESEARCH PROVENANCE

Сохранять, но **не трактовать как текущую задачу без явного указания владельца**:

```text
P44*
P45*
P46*
P47*
P49*
P50*
P51*
P52*
P53*
EO1*
EO2*
EO3*
EO4*
SE1*
SE2*
ENTRY_* research protocols/verdicts
algorithm_2_entry_research_v1..v7
historical Mayak P1 reports
```

Они нужны для provenance и воспроизводимости исследований.

Не удалять их только потому, что работа ушла дальше.

---

## 5. IMPLEMENTATION HISTORY

Документы вроде:

```text
STRATEGY_DISPATCHER_IMPLEMENTATION_D0_D6_RU.md
STRATEGY_DISPATCHER_RUNBOOK_RU.md
POSITION_SUPERVISOR_V1_PLAN_RU.md
```

после фактической реализации являются историей реализации.

Они не должны автоматически становиться новым backlog.

При желании позже перенести в:

```text
docs/history/implementation/
```

но перенос не обязателен для текущей задачи.

---

## 6. УДАЛИТЬ КАК УСТАРЕВШЕЕ

Следующий файл больше не соответствует состоянию проекта:

```text
docs/CODEX_RESUME_AFTER_PAUSE_RU.md
```

Причина:

- описывает незавершённый шаг 7;
- прогон уже завершён;
- commit давно изменился;
- документ может заставить агента «продолжать» уже завершённую задачу.

Удалить из active tree. Git history сохраняет его происхождение.

---

## 7. УДАЛИТЬ TRANSPORT METADATA ИЗ docs

Каталог:

```text
docs/architecture_pack_20260830/
```

с `README_RU.md` и `MANIFEST.json` является метаданными транспортного пакета, а не архитектурой проекта.

После успешной интеграции удалить из active `docs`.

Git history остаётся достаточным provenance.

---

## 8. Не добавлять handoff-файлы в постоянную архитектуру без необходимости

Файлы вида:

```text
CODEX_RESUME_*
CODEX_HANDOFF_*
NEXT_TASK_*
```

должны быть временными рабочими инструкциями.

После выполнения:

- удалить;
- или переместить в `docs/history/tasks/`.

Они не должны конкурировать с архитектурой.

---

## 9. Новая обязательная строка для AGENTS.md

Добавить смысловой контракт:

> Перед использованием документа как задания определить его статус по `docs/DOCUMENT_AUTHORITY_RU.md`. Исторический research/runbook/handoff не является текущим поручением. При конфликте текущий AGENTS.md и канонические архитектурные контракты имеют приоритет.

---

## 10. Главный принцип

> **Историю исследований сохраняем. Старые поручения не сохраняем как активные инструкции.**
