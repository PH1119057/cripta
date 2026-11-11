# P90L90 SOURCE_ORDER_LEDGER_AUDIT_V1 — Issue #243

Status: **RESEARCH PILOT / SOURCE-LEDGER AUDIT, NOT FULL ENTRY/EXIT REPLAY**.
Research-only, separate from DRAFT core PR #251; no trading activation.

## Exact actor/path preflight contract

| Operation | Exact actor | Exact path/permissions | Status |
|---|---|---|---|
| READ | Unix `cripta`, UID 107 | `/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/inputs/**` original 500 gzip/manifests, and `results/p90l90_strategy_second48_*_20261028_v1/{result.json,trade_ledger.jsonl}` | read-only actor smoke verified Oct 28 |
| EXECUTE | Unix `cripta`, UID 107 | **exact reviewed research GitHub SHA/worktree**, Python ≥3.12 with canonical `bybit_workbench.universal_entry.p90l90_causal` and its pinned deps | **NOT PREPARED**: source branch not staged in approved isolated worktree; `/usr/bin/python3` lacks pydantic |
| CREATE_CHILD | Unix `cripta`, UID 107 | one new `/data/cripta/research/runs/p90l90_source_order_audit_<ID>` child of `runs` (owner=107 group=109, parent mode=2770) | parent DAC/owner/actor checked, no actual child created |
| WRITE/RENAME | Unix `cripta`, UID 107 | `result.json.tmp → result.json` **only** within that new child | NOT CHECKED HERE, no persistent write smoke |
| DELETE | none | no deletion allowed; an occupied output path is a hard stop | design verified |
| DB | none | no PostgreSQL use | none |
| SYSTEMD | none | bounded manual research job, no service sandbox | n/a |
| Source authority | GitHub `PH1119057/cripta:main` | pin exact reviewed, CI-passing commit and worktree source bytes | current branch is DRAFT, not deployed |

PREPARED=NO until an authorized scoped research worktree is created from the verified GitHub SHA under `/data/cripta/research/worktrees/`, matching interpreter/package imports verified under UID 107, and the exact output CREATE+RENAME actor/path preflight is PASS. SentinelX `sudo -u cripta python3 -c` is currently approved for **read-only** forensic only; do NOT hide script execution, write, Git fetch/worktree operations or effective actor changes inside that diagnostic prefix or the management actor. Tool authorization and project actor/path authorization are separate.

## What has actually been checked on original archives

- Read-only source checks under actual UID 107: original 100 daily gzip files each for LTC/INJ/DOT/ARB/APT, 500 source files, size matched manifests, frozen 10-day warmup plus 90-day evaluation period.
- Five exact first `SECOND_48H` ENTRY `trdMatchID` / timestamp / price / 1-based source orders matched original May-18 gzip lines, not invented native `seq`.
- Five corresponding EXIT source ordinal/timestamp and actual marketable price or resting-maker target-crossing condition matched.
- A separate stdlib Decimal read-only verification recomputed fees for **all 1440 realized ledger rows** (636 MAIN, 804 SUB) and reconciled four scenario historical totals. This verifies the frozen ledger's arithmetic, NOT re-simulation of decisions, fills or original WS native sequence.
- Synthetic GitHub CI tests exercise source-order offset, tampered trade identity, timestamp regression and newly modeled fee disagreement.

## Runner after independent exact authorization

`--pilot --symbols LTCUSDT --days 11 --source-commit <exact_reviewed_sha>` is no-write, and reads May 8–18 inclusive. It checks 10-day warmup offset and selected MAIN ledger endpoints. Five-symbol pilot may use `--symbols LTCUSDT INJUSDT DOTUSDT ARBUSDT APTUSDT --days 11` after one-symbol smoke and measured time/RSS.

Full 100-day run is **not** the historical Entry/Exit code replay. It is a 500-file source-order and fee-code audit; it requires an immutable `--output-name p90l90_source_order_audit_<ID>` under the data disk. Output is `result.json` with source SHA, per-symbol checksums, counts, validated ledger endpoints and explicit `full_new_entry_exit_replay=false`, `live_websocket_exchange_seq_parity=false`. Retain per-run stdout, stderr, exact PID/job ID and QC status through the approved job rail, with bounded status checks. The actual full run is NOT STARTED.

## Additional unfinished proof

Only a **separate new-code Entry→PAPER→Exit executor** comparing original causal candle/ATR/PREV7D geometry, candidate acceptance, Strategy-scoped SECOND48 occupancy, original native trade order and each Exit's net financial ledger can establish 90-day decision/financial parity. The reference CSV does not contain Bybit native WS `seq`. CSV ordinal is a separate research key, not a fake `TradeCursor.seq`; live transport equivalence remains HARD_STOP pending authentic data or an explicit owner-approved canonical criterion. No research result authorizes PAPER StrategyActivation, production PostgreSQL migrations, REAL capital/order semantics or LIVE.
