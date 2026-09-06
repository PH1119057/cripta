# КРИПТА — АВТОРИТЕТНОСТЬ ДОКУМЕНТОВ

**Документ:** `DOCUMENT_AUTHORITY_RU.md`
**Версия:** 2.8
**Дата:** 2026-09-06
**Статус:** канонический реестр авторитетности

## 1. Порядок авторитетности

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
docs/STRATEGY_DISPATCHER_ARCHITECTURE_RU.md
docs/MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md
docs/SIGNAL_LIFECYCLE_CONTRACT_RU.md
docs/ANALYST_ARCHITECTURE_RU.md
и другие специализированные контракты

LEVEL 4 — implementation contracts
LEVEL 5 — research protocols
LEVEL 6 — historical Pxx / EO / SE / runbooks
LEVEL 7 — old handoff / resume / task / package metadata
```

## 2. Каноническая верхняя модель

Любой документ уровня 2–7 обязан быть совместим с:

```text
MAYAK -> DISPATCHER -> STRATEGY(ENTRY/EXIT) -> EXECUTION -> EXCHANGE
```

`Risk` не является отдельным top-level layer.

Технический поддерживающий контур не является дополнительным торговым этажом.

## 3. Разделение видов истины

```text
GitHub main
= shared source checkpoint

/srv/cripta/source_checkout
= synchronized server source checkout

installed runtime
= installed implementation

PostgreSQL
= persisted operational/analytical truth

connected Exchange / Trading Account
= live external truth for account/orders/fills/positions/funds
```

Универсальная архитектура не привязана к бренду торговой площадки.

## 4. Специализированные контракты

- MAYAK: `MAYAK_ARCHITECTURE_PRINCIPLES_RU.md`
- MAYAK V2 objective context implementation/research: `MAYAK_OBJECTIVE_CONTEXT_V2_IMPLEMENTATION_RU.md`
- Dispatcher: `STRATEGY_DISPATCHER_ARCHITECTURE_RU.md`
- Dispatcher V2 implementation D0–D7: `DISPATCHER_V2_IMPLEMENTATION_RU.md` (LEVEL 4, active implementation contract)
- Dispatcher V2 D0–D7 runtime evidence: `DISPATCHER_V2_D0_D7_STAGE_RESULTS_RU.md` (LEVEL 5)
- Dispatcher V2 D8 observed-context implementation: `DISPATCHER_V2_D8_OBSERVED_CONTEXT_RU.md` (LEVEL 4)
- Dispatcher V2 D8 runtime evidence: `DISPATCHER_V2_D8_STAGE_RESULTS_RU.md` (LEVEL 5)
- universal market/coin context and rating: `MARKET_CONTEXT_AND_COIN_RATING_ARCHITECTURE_RU.md`
- lifecycle: `SIGNAL_LIFECYCLE_CONTRACT_RU.md`
- Analyst: `ANALYST_ARCHITECTURE_RU.md`
- MAYAK V2 completed-stage evidence (LEVEL 5 / research evidence, not architecture): `MAYAK_V2_STAGE_RESULTS_RU.md`

Специализированный контракт уточняет верхний, но не создаёт нового top-level ownership.

## 5. История

Исторические документы сохраняют фактическую историю и не переписываются задним числом.

Следующие документы старого profile-based Dispatcher после owner decision 2026-09-06 имеют статус **LEVEL 6 / HISTORICAL LEGACY** и не являются заданием для V2:

- `STRATEGY_DISPATCHER_IMPLEMENTATION_D0_D6_RU.md`;
- `STRATEGY_DISPATCHER_MARKET_VOCABULARY_RU.md`;
- `STRATEGY_DISPATCHER_PROFILE_GUIDE_RU.md`;
- `STRATEGY_DISPATCHER_RUNBOOK_RU.md`.

Их старое слово `Risk`, конкретная биржа или старая схема ownership не отменяют текущий канон, если документ явно historical.

## 6. Конфликт

При конфликте:

```text
HARD_STOP=YES
OWNER_DECISION_REQUIRED=YES
```

Код не используется как автоматический источник новой архитектуры.
