# КРИПТА — АВТОРИТЕТНОСТЬ ДОКУМЕНТОВ

**Документ:** `DOCUMENT_AUTHORITY_RU.md`
**Версия:** 2.0
**Дата:** 2026-09-05
**Статус:** канонический реестр авторитетности

## 1. Порядок авторитетности

При прямом конфликте применяется более высокий уровень:

```text
LEVEL 0 — явное текущее решение владельца, оформленное в каноническом контракте

LEVEL 1 — GLOBAL
CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md
CRIPTA_ARCHITECTURE_RULES_RU_V1.md

LEVEL 2 — BOOTSTRAP / GOVERNANCE
AGENTS.md
docs/PROJECT_GOVERNANCE_RU.md

LEVEL 3 — CURRENT ARCHITECTURE
docs/PROJECT_ARCHITECTURE_RU.md
docs/CURRENT_PROJECT_MAP_RU.md
специализированные архитектурные контракты слоя

LEVEL 4 — implementation contracts
LEVEL 5 — research protocols
LEVEL 6 — historical Pxx / EO / SE / runbooks
LEVEL 7 — старые handoff / resume / task / package metadata
```

Специализированный документ уточняет верхний контракт только там, где не
противоречит ему. Исторический документ не становится текущим поручением из-за
того, что остался в репозитории.

## 2. Разделение видов истины

```text
GitHub PH1119057/cripta:main
    = canonical shared source-code checkpoint

/srv/cripta/source_checkout
    = canonical server source checkout,
      который обязан быть синхронизирован с GitHub main

/srv/cripta/... installed runtime
    = установленная production-версия,
      но не замена source repository

PostgreSQL
    = canonical persisted operational/analytical data truth

Bybit
    = live exchange truth
```

Следующее не является source of truth: `C:\cripta`, старые ZIP, старые
ChatGPT/Codex conversations и local Codex notes.

## 3. Действующая архитектура

К уровню текущей архитектуры относятся, в частности:

- `docs/MAYAK_ARCHITECTURE_PRINCIPLES_RU.md`;
- `docs/STRATEGY_DISPATCHER_ARCHITECTURE_RU.md`;
- `docs/DATA_TIMELINE_CONTRACT_RU.md`;
- `docs/SIGNAL_LIFECYCLE_CONTRACT_RU.md`;
- `docs/ANALYST_ARCHITECTURE_RU.md`;
- `docs/ARCHIVE_V2_ARCHITECTURE_RU.md`.

Implementation- и research-контракт не разрешает автоматически менять live.

## 4. История и provenance

Документы Pxx, EO, SE, старые Entry-пакеты, runbook, handoff, resume, task и ZIP
metadata сохраняют фактическую историю. По умолчанию их статус:

```text
HISTORICAL
NOT CURRENT TASK
NOT CURRENT PRODUCTION CONTRACT
```

Исторические выводы не переписываются задним числом. Их можно использовать как
данные исследования, но не как разрешение на production-влияние.

## 5. Конфликт

При конфликте с уровнем 1:

```text
HARD_STOP=YES
OWNER_DECISION_REQUIRED=YES
```

Исполнитель фиксирует точное противоречие и не меняет архитектурный смысл или
production-код самостоятельно.

## 6. Изменение версии 2.0

Добавлены два глобальных контракта, разделены source/runtime/data/live truth,
понижен статус исторических и локальных материалов, устранён статус `AGENTS.md`
как самостоятельного верхнего архитектурного источника.
