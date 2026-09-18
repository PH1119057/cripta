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

## P4.1 — Entry expiry & capital release safety — DONE

- [x] INSUFFICIENT_AVAILABLE_FUNDS remains terminal; no waiting/retry of same attempt
- [x] reservation carries exact Strategy-owned pre-dispatch expiry
- [x] expired RESERVED is released even while mainnet gate is disarmed
- [x] pre-exchange bridge/structural block releases RESERVED
- [x] dispatch changes reservation to DISPATCHED atomically with trade command
- [x] exchange order acknowledgement changes reservation to PENDING_EXCHANGE_REFLECTION
- [x] deterministic failure before order acknowledgement releases capital
- [x] ambiguous outcome after possible mutation -> RECONCILIATION_REQUIRED
- [x] confirmed expired limit cancellation with zero fill -> RELEASED
- [x] partial/unknown cancellation outcome never releases capital blindly
- [x] confirmed fill still -> CONSUMED
- [x] confirmed position close still -> RELEASED
- [x] disposable PostgreSQL lifecycle tests
- [x] affected/full regression NEW_DIAGNOSTICS=0
- [x] separate P4.1 commit

## P5 — Universal Exit Engine shadow — DONE
- [x] exact StrategyPosition + exact ExitPlan input
- [x] no hidden H3/H9/percent defaults
- [x] typed rules and conflict semantics from ExitPlan
- [x] SET_STOP / SET_TP / SET_PROTECTION / SET_TRAILING / REDUCE / CLOSE
- [x] unsupported action fail-closed
- [x] shadow persistence only; no exchange mutation
- [x] separate P5 commit

## P5 evidence

- UniversalExitEngine принимает только exact StrategyPosition + exact ExitPlan + causal ExitObservation;
- ExitObservation хранит отдельные event_at / observed_at / received_at и exact source_refs;
- compatibility ExitPlan без explicit rules -> NO_EXECUTABLE_EXIT_RULES;
- supported actions: SET_STOP / SET_TP / SET_PROTECTION / SET_TRAILING / REDUCE / CLOSE;
- action mutation передаётся дословно из ExitPlan; Engine не добавляет уровни/проценты/defaults;
- conflict_policy обязателен и явно задаёт PRIORITY + HIGHER_WINS/LOWER_WINS + FAIL_CLOSED tie;
- unsupported action / unsupported stateful operator / missing required fact / unknown context contract -> BLOCKED;
- ONCE_PER_POSITION использует durable prior ExitDecision evidence;
- shadow persistence: exit_observations + shadow_evaluations + optional exit_decisions;
- P5 не создаёт strategy_exit.execution_requests и не пишет runtime.trade_commands;
- P3 + P4 + P4.1 + P5 PostgreSQL integration: 20 passed;
- P5 unit: 19 passed;
- affected regression: 136 passed;
- full branch: 1344 passed / 30 skipped / 18 baseline stale-doc failed;
- migration second apply: PASS;
- NEW_DIAGNOSTICS = 0.

## P6 — typed Execution bridge — DONE
- [x] EntryExecutionRequest finalized
- [x] ExitExecutionRequest persistence
- [x] exact StrategyPosition/ExitPlan validation
- [x] map only to technical Execution mechanisms
- [x] Execution does not invent trading policy
- [x] duplicate request idempotency
- [x] ambiguous result => reconciliation, no blind retry
- [x] separate P6 commit

## P6 evidence

- EntryExecutionRequest имеет отдельный runtime type; старое ExecutionRequest оставлено только alias;
- ExitExecutionRequest хранит exact StrategyPosition/ExitDecision/ExitPlan lineage;
- request lifetime принадлежит exact ExitPlan.execution_policy.max_request_age_seconds;
- accepted ExitDecision материализуется в immutable request; expired request блокируется до Exchange;
- SET_STOP / SET_TP / SET_PROTECTION / SET_TRAILING / REDUCE / CLOSE имеют строгие technical mutation schemas;
- неизвестные поля mutation не игнорируются, а fail-closed;
- private runtime повторно сверяет exact StrategyPosition/account/exchange_position_key/positionIdx/direction/expiry;
- новый strategy_exit path не использует legacy break_even/current_stop/protection_plan/default percentages;
- duplicate request/dispatch idempotent;
- invalid decision получает immutable execution_materialization_block и не starvation-ит очередь;
- ambiguous post-mutation outcome -> StrategyPosition RECONCILIATION_REQUIRED + process mutation barrier;
- P3..P6 combined PostgreSQL integration: 25 passed;
- P6 pure/source tests: 23 passed;
- affected regression: 107 passed;
- full branch: 1364 passed / 35 skipped / 18 baseline stale-doc failed;
- migration second apply: PASS;
- NEW_DIAGNOSTICS = 0.

## P7 — Lifecycle Supervisor — DONE
- [x] activation -> plans -> Entry -> fill -> position -> Exit -> close -> economics
- [x] V1 lifecycle fault codes
- [x] POSITION_WITHOUT_EXIT_OWNER critical path
- [x] no trading authority
- [x] restart/recovery tests
- [x] separate P7 commit

## P7 evidence

- Lifecycle Supervisor проектирует immutable lifecycle events из durable P2–P6 truth;
- сквозной projection проверен от StrategyActivation до POSITION_CLOSED и ECONOMICS_FINALIZED;
- runtime.position_exit_claims добавляет exact position-level Exit Engine claim;
- shadow Exit evidence не создаёт ложный live ownership;
- Universal Entry observer пишет фактический ENTRY_ENGINE plan-consumption acknowledgement;
- второй Exit Engine не может тихо перехватить уже claim-нутую StrategyPosition;
- POSITION_WITHOUT_EXIT_OWNER = CRITICAL и автоматически RESOLVED только после exact claim/завершения условия;
- все V1 lifecycle fault codes управляются Supervisor;
- EXCHANGE_STATE_DIVERGED проверяется только при explicit freshness policy + fresh successful reconciliation;
- stale/unknown exchange evidence не закрывает ранее открытый divergence fault;
- Lifecycle Supervisor не пишет trade_commands, не создаёт StrategySignal/EntryDecision/ExitDecision и не вызывает Exchange API;
- restart/recovery: новая instance повторно сканирует durable truth без дублей и закрывает восстановленный fault;
- P3..P7 combined PostgreSQL integration: 29 passed;
- P7 source/schema tests: 19 passed;
- affected regression: 124 passed + 2 known stale-doc baseline failed;
- full branch: 1372 passed / 39 skipped / 18 baseline stale-doc failed;
- migration second apply: PASS;
- NEW_DIAGNOSTICS = 0.

## P8 — Counterfactual / Analyst — DONE
- [x] insufficient-capital candidate
- [x] exact Strategy/EntryPlan/ExitPlan lineage
- [x] no reservation / no ExecutionRequest / no exchange path
- [x] actual and counterfactual economics isolated
- [x] separate P8 commit

## P8 evidence

- canonical counterfactual создаётся только из EntryDecision=INSUFFICIENT_AVAILABLE_FUNDS;
- capture включён только для StrategyActivation из real-execution set;
- обычная нехватка capacity и проигранная atomic reservation race обе дают Analyst candidate;
- candidate несёт exact StrategyActivation / Strategy / EntryPlan / ExitPlan / signal / attempt / EntryDecision lineage;
- candidate физически не имеет capital_reservation_id, ExecutionRequest, StrategyPosition, command/order/exchange identity;
- illegal counterfactual + reservation или counterfactual + ExecutionRequest fail-closed;
- cross-lineage ExitPlan fail-closed;
- старый PaperTradeRuntime не используется как surrogate, потому что его paper_orders требуют реальный execution_request_id;
- analytics.counterfactual_candidates и analytics.counterfactual_outcomes append-only;
- counterfactual outcome economics отделены от runtime.position_exit_attribution;
- P8 pure/source/schema: 21 passed;
- P3..P8 combined PostgreSQL integration: 33 passed;
- affected regression: 132 passed + 2 known stale-doc baseline failed;
- full branch: 1381 passed / 43 skipped / 18 baseline stale-doc failed;
- migration second apply: PASS;
- NEW_DIAGNOSTICS = 0.

## P9 — shadow deployment / recovery — DONE
- [x] verified overlay before install
- [x] DB precheck / migration / grants
- [x] services wired disarmed
- [x] repeated runtime checks
- [x] restart/recovery
- [x] no duplicate mutation / lost reservation / lost Exit owner
- [x] mainnet remains closed

## P9 evidence

- candidate code commit: 14780008a02fc3948e82467e0983e89913ee2068;
- temp overlay built by git archive from exact candidate commit; changed-file SHA256 = MATCH;
- overlay Ruff/mypy/systemd verify = PASS;
- overlay full suite = 1385 passed / 47 skipped / same 18 stale-doc baseline failed;
- overlay P3..P9 PostgreSQL = 37 passed; migration second apply = PASS;
- Installation Readiness Matrix checked source/remote/worktree, owners, Python, pytest, Ruff, mypy, uv 0.11.33, PostgreSQL owner/roles/grants, systemd/live paths, Git transport, disk and permissions;
- pre-mutation backup: /srv/cripta/backups/trade_lifecycle_p9_20260918T204213Z;
- GitHub main and /srv/cripta/source_checkout fast-forwarded to exact candidate before live deploy;
- live migration = PASS; required runtime/strategy_exit/analytics objects and grants verified;
- Universal Entry observer loaded exact candidate source and wrote 2 exact ENTRY_ENGINE plan consumption acknowledgements;
- initial 2 ENTRY_PLAN_NOT_CONSUMED faults were automatically RESOLVED after real observer acknowledgements;
- Universal Exit shadow runtime = RUNNING, execution_rights=NONE;
- Lifecycle Supervisor runtime = RUNNING, trading_rights=NONE;
- Universal Entry consumer remains disabled/inactive with CRIPTA_UNIVERSAL_ENTRY_MAINNET_CONSUMER=DISABLED;
- live restart changed both P9 service PIDs and recovered cleanly;
- before/after restart: trade_commands=2166 unchanged; ExitExecutionRequest=0; exit dispatches=0; reservations=0; claims=0 because no open Universal StrategyPosition;
- seeded disposable recovery tests prove stale/lost Exit owner fault/reclaim and reservation preservation;
- malformed legacy/test StrategyPosition cannot starve valid ownership heartbeat; blocked per-position fail-closed;
- mainnet gate before migration, before start, after start and after restart = 0;
- P9 runtime verification = PASS in SHADOW; no LIVE/MICRO_LIVE re-arm.

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

## P4.1 evidence

- INSUFFICIENT_AVAILABLE_FUNDS remains terminal: no reservation, no ExecutionRequest, no wait/retry;
- next market opportunity must create a new StrategySignal/StrategyAttempt;
- pre-dispatch reservation expiry is derived from exact Strategy max_request_age_seconds;
- expired RESERVED is swept before mainnet gate evaluation;
- request expiry/pre-exchange structural block/ownership conflict releases capital;
- successful dispatch: RESERVED -> DISPATCHED;
- confirmed exchange order acknowledgement: DISPATCHED -> PENDING_EXCHANGE_REFLECTION;
- deterministic no-order failure from DISPATCHED -> RELEASED;
- post-ack/ambiguous failure -> RECONCILIATION_REQUIRED;
- confirmed zero-fill TTL cancellation -> RELEASED;
- partial/unknown cancellation never releases blindly;
- hidden 30-second limit TTL fallback removed from Execution runtime;
- P3 + P4 + P4.1 PostgreSQL integration: 16 passed;
- affected regression: 125 passed;
- full branch: 1322 passed / 26 skipped / 18 baseline stale-doc failed;
- NEW_DIAGNOSTICS = 0.
