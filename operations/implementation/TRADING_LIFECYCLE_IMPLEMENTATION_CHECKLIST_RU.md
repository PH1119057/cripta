# CRIPTA — чек-лист реализации торгового lifecycle

Дата: 2026-09-18
Основание: ТЗ редакции 1.1 + активный канон
Рабочая ветка: impl/trade-lifecycle-20260918
Baseline main: d74086374e6d007730c5b06676f066d560e85142

Статусы: DONE / IN PROGRESS / TODO / BLOCKED.
Файл operational-only: не канон и не источник trading policy.

## Общий gate каждого прохода
- [ ] канон перечитан для затрагиваемых слоёв
- [ ] scope не расширен скрыто
- [ ] git status --untracked-files=all полностью объяснён
- [ ] новые decision/execution поля имеют consumer path
- [ ] missing/unsupported Strategy-owned field => fail-closed
- [ ] py_compile / Ruff / mypy
- [ ] новые tests + affected regression tests
- [ ] DB migration проверена на disposable schema-only DB, если затронута БД
- [ ] LIVE / MICRO_LIVE не включены
- [ ] отдельный commit прохода
- [ ] после commit проверен worktree

## P0 — baseline / forensic — DONE
- [x] GitHub main и source_checkout сверены
- [x] runtime / DB / gates / toolchain проверены
- [x] baseline affected tests: 95 passed
- [x] mainnet gate закрыт; execution permissions = 0

## P1 — immutable contracts / exact plan ownership — DONE
Commit: 6b185a647140645aeb03216a47b21a1f2002d91e
- [x] exact EntryPlan/ExitPlan lookup
- [x] ExitPlan остаётся доступен после deactivation
- [x] StrategyPosition contract
- [x] ExitDecision / ExitExecutionRequest contracts
- [x] cross-lineage fail-closed

## P2 — durable lifecycle schema — DONE
Commit: 73e8a55d056368150945e6344cd3aa134908592f
- [x] plan consumption storage
- [x] capital reservation storage
- [x] StrategyPosition lineage columns
- [x] separate strategy_exit storage
- [x] lifecycle events / faults
- [x] migration schema-only + second apply

## P3 — atomic capital reservation — DONE
- [x] CapitalReservationState / Request / Port
- [x] Postgres implementation
- [x] account-scoped advisory transaction lock
- [x] no Strategy ranking
- [x] reservation before real ACCEPTED
- [x] reservation_id propagated into EntryDecision / EntryExecutionRequest
- [x] exact exit_plan_fingerprint propagated
- [x] persistence wired
- [x] live bridge requires reservation
- [x] live consumer requires RESERVED
- [x] dispatch + RESERVED->DISPATCHED in one DB transaction
- [x] deferred FK transaction contract
- [x] static checks green after final wiring
- [x] bridge tests updated without weakening fail-closed
- [x] reservation identity/idempotency/state tests
- [x] two-connection PostgreSQL concurrency test
- [x] exactly one succeeds when capital is enough for one
- [x] loser => INSUFFICIENT_AVAILABLE_FUNDS
- [x] ambiguous/unknown path does not release capital
- [x] duplicate same attempt idempotent
- [x] migration retested with P3 additions
- [x] affected regression suite green
- [x] P3 commit
- [x] worktree clean after commit

## P4 — StrategyPosition binding — DONE
- [x] confirmed fill -> exact StrategyPosition
- [x] exact Strategy/activation/EntryPlan/ExitPlan lineage
- [x] actual fill/qty/order identities
- [x] physical exchange slot conflict => fail-closed
- [x] historical rows never guessed
- [x] recovery/idempotency test
- [x] separate P4 commit

## P5 — Universal Exit Engine shadow — TODO
- [ ] exact StrategyPosition + exact ExitPlan input
- [ ] no hidden H3/H9/percent defaults
- [ ] typed rules and conflict semantics from ExitPlan
- [ ] SET_STOP / SET_TP / SET_PROTECTION / SET_TRAILING / REDUCE / CLOSE
- [ ] unsupported action fail-closed
- [ ] shadow persistence only; no exchange mutation
- [ ] separate P5 commit

## P6 — typed Execution bridge — TODO
- [ ] EntryExecutionRequest finalized
- [ ] ExitExecutionRequest persistence
- [ ] exact StrategyPosition/ExitPlan validation
- [ ] map only to technical Execution mechanisms
- [ ] Execution does not invent trading policy
- [ ] duplicate request idempotency
- [ ] ambiguous result => reconciliation, no blind retry
- [ ] separate P6 commit

## P7 — Lifecycle Supervisor — TODO
- [ ] activation -> plans -> Entry -> fill -> position -> Exit -> close -> economics
- [ ] V1 lifecycle fault codes
- [ ] POSITION_WITHOUT_EXIT_OWNER critical path
- [ ] no trading authority
- [ ] restart/recovery tests
- [ ] separate P7 commit

## P8 — Counterfactual / Analyst — TODO
- [ ] insufficient-capital candidate
- [ ] exact Strategy/EntryPlan/ExitPlan lineage
- [ ] no reservation / no ExecutionRequest / no exchange path
- [ ] actual and counterfactual economics isolated
- [ ] separate P8 commit

## P9 — shadow deployment / recovery — TODO
- [ ] verified overlay before install
- [ ] DB precheck / migration / grants
- [ ] services wired disarmed
- [ ] repeated runtime checks
- [ ] restart/recovery
- [ ] no duplicate mutation / lost reservation / lost Exit owner
- [ ] mainnet remains closed

## P10 — controlled legacy Exit migration readiness — TODO
- [ ] legacy/Universal ownership boundary explicit
- [ ] mutual exclusion proven
- [ ] shadow comparison against exact Strategy/ExitPlan
- [ ] cutover plan prepared
- [ ] no live cutover without owner decision

## Final completion gate
- [ ] StrategyCard passive immutable
- [ ] Strategy Materializer deterministic
- [ ] Entry Engine universal
- [ ] Exit Engine universal
- [ ] Execution technical only
- [ ] Lifecycle Supervisor non-trading
- [ ] Position Supervisor read-only
- [ ] Analyst non-trading
- [ ] atomic allocation proven
- [ ] exact StrategyPosition ownership proven
- [ ] restart/reconciliation proven
- [ ] no hidden Strategy defaults / M3 / Risk resurrection
- [ ] no automatic account position-mode switch
- [ ] LIVE not enabled without owner decision

## Known baseline diagnostics — не P3 regression

На baseline main и на P3 branch одинаково остаются 18 stale documentation tests,
которые ссылаются на документы, удалённые/архивированные документационной
ревизией 2026-09-18, либо на старые точные формулировки канона.

Проверено полным pytest:
- baseline main: 1300 passed / 8 skipped / 18 failed;
- P3 branch: 1313 passed / 14 skipped / 18 failed;
- NEW_DIAGNOSTICS = 0.

Возвращать исторические документы в активный канон ради этих тестов запрещено.
Их исправление — отдельный maintenance scope.

## P4 evidence

- exact fill binding проверен на disposable PostgreSQL;
- command payload сверяется с durable
  execution_dispatches -> execution_requests -> strategy_attempts -> capital_reservations;
- reservation связывается с StrategyPosition и становится CONSUMED;
- confirmed close переводит связанную reservation в RELEASED;
- повторный identical fill binding idempotent;
- второй OPEN StrategyPosition того же exchange slot отвергается DB;
- RECONCILIATION_REQUIRED также удерживает exchange slot;
- Universal Entry consumer блокирует occupied/pending slot до создания trade command;
- pre-exchange ownership conflict освобождает RESERVED capital;
- P3+P4 PostgreSQL integration: 9 passed;
- targeted P4: 87 passed;
- full branch: 1316 passed / 19 skipped / 18 baseline stale-doc failed;
- NEW_DIAGNOSTICS = 0.
