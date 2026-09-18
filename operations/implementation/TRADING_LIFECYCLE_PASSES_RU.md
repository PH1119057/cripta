# CRIPTA — реализация торгового lifecycle по проходам

**Дата:** 2026-09-18
**Статус:** implementation plan / не канон
**Baseline:** d74086374e6d007730c5b06676f066d560e85142

Каждый проход обязан быть отдельно проверяемым. Mainnet/MICRO_LIVE в рамках
этого плана не включаются.

## P0 — baseline / forensic
- Git/source/runtime/DB/toolchain.
- Проверка gates, permissions и текущих constraints.
- Baseline tests.

## P1 — immutable contracts / plan ownership
- exact EntryPlan/ExitPlan lookup;
- сохранение exact ExitPlan после deactivation для уже открытых positions;
- StrategyPosition contract;
- ExitDecision / ExitExecutionRequest contracts;
- no runtime mutation.

## P2 — durable lifecycle schema
- plan consumption acknowledgements;
- capital reservations;
- StrategyPosition lineage columns;
- Exit decision/request/dispatch storage;
- global lifecycle event/fault ledger;
- migrations сначала проверяются на disposable DB.

## P3 — atomic capital reservation
- Postgres CapitalReservationPort;
- account-scoped atomic serialization;
- live dispatch запрещён без exact reservation;
- unknown exchange result не освобождает reservation.

## P4 — StrategyPosition binding
- confirmed fill -> exact StrategyPosition;
- immutable EntryPlan/ExitPlan lineage;
- physical exchange slot conflict -> fail closed.

## P5 — Universal Exit Engine shadow
- exact StrategyPosition + exact ExitPlan;
- Strategy-owned rules only;
- typed ExitDecision;
- no exchange mutation.

## P6 — typed Execution bridge
- EntryExecutionRequest/ExitExecutionRequest;
- exact lineage validation;
- Exit mutation maps only to existing technical Execution mechanisms;
- Execution не рассчитывает торговую policy.

## P7 — Lifecycle Supervisor
- activation -> plans -> Entry -> fill -> position -> Exit -> close -> economics;
- lifecycle faults;
- no trading authority.

## P8 — Counterfactual / Analyst
- insufficient-capital pseudo trades;
- no reservation/execution path;
- actual/counterfactual economics separated.

## P9 — shadow deployment / recovery
- source/live overlay;
- service wiring with exchange mutation disabled;
- restart/recovery/idempotency;
- dual Exit ownership forbidden.

## P10 — legacy Exit migration readiness
- mutual exclusion proven;
- legacy automated Exit remains authoritative until separate controlled cutover;
- no LIVE activation without owner decision.
