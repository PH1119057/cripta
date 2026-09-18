# CRIPTA — P10 controlled legacy Exit migration readiness

Дата: 2026-09-19
Статус: OPERATIONAL PLAN / НЕ КАНОН / НЕ РАЗРЕШЕНИЕ LIVE

Этот документ описывает только техническую последовательность cutover после
отдельного OWNER DECISION. Он не создаёт Exit rules, Strategy policy или
LIVE-права.

## 1. Ownership invariant

- bot_instance_id=universal-entry принадлежит только Universal lifecycle.
- legacy automated Exit не имеет права создавать break_even / trailing_stop
  для такой StrategyPosition.
- Universal Exit loader/Execution принимает только universal-entry.
- уже открытая legacy position не переводится в Universal Exit посередине
  lifecycle.
- уже открытая Universal StrategyPosition не передаётся legacy Exit при fault.
  Сохраняется Strategy-owned initial protection, поднимается lifecycle fault,
  Universal owner восстанавливается/reconciles.
- ручное действие владельца не является автоматическим legacy ownership.

## 2. Preconditions before any cutover

1. GitHub main == source_checkout == tested release.
2. mainnet execution gate closed during preparation.
3. exact StrategyCard/StrategyActivation/EntryPlan/ExitPlan lineage.
4. у целевой Strategy есть explicit executable ExitPlan rules; отсутствие rules
   не заменяется legacy defaults.
5. Universal Exit shadow evidence получено на exact StrategyPosition + exact
   ExitPlan; no hidden H3/H9/percent defaults.
6. Lifecycle Supervisor: no open critical lifecycle faults.
7. exact Exit Engine claim coverage = 100% для Universal StrategyPosition.
8. initial protection подтверждена до динамического Exit.
9. Universal Exit consumer остаётся disabled до отдельного OWNER DECISION.
10. real execution permission не выдаётся автоматически.
11. legacy/private runtime mutual exclusion tests green.
12. rollback checkpoint и current exchange state проверены непосредственно перед
    mutation.

## 3. Controlled cutover after OWNER DECISION only

1. повторить canonical pre-read;
2. повторить Git/DB/service/readiness matrix;
3. убедиться, что нет ownership conflict и stale reconciliation;
4. сохранить backup/rollback checkpoint;
5. оставить legacy Exit владельцем только legacy positions;
6. разрешить Universal Exit execution только для exact approved Strategy
   activation/version;
7. включить consumer arm и execution permission отдельными явными действиями;
8. открыть только предусмотренный owner-approved MICRO_LIVE gate;
9. проверить через 5–10 секунд exact process/release identity, claim,
   ExitDecision lineage, ExitExecutionRequest lineage, runtime command lineage,
   exchange acknowledgement, lifecycle faults и отсутствие legacy auto command
   на Universal position;
10. не расширять на другие Strategy автоматически.

## 4. Rollback

При structural/ownership/reconciliation fault:

1. запретить новые Universal execution mutations;
2. закрыть consumer/gate в техническом порядке;
3. не переводить Universal StrategyPosition в legacy auto Exit;
4. сохранить существующую Strategy-owned initial protection;
5. оставить/поднять lifecycle fault;
6. выполнить reconciliation exact position/order/protection state;
7. восстановить Universal Exit owner только после exact binding;
8. повторное включение — только после проверки причины и нового допуска.

## 5. Current P10 readiness interpretation

exit_migration_readiness.py является read-only auditor. Он никогда не
разрешает LIVE сам.

live_cutover_authorized=false является постоянным свойством этого отчёта:
OWNER DECISION находится вне автоматического readiness-аудитора.
